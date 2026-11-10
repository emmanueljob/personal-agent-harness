"""Synthetic cloud-worker protocol, durable outbox, and private gateway coverage."""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from harness import cloud_browser as gateway
from harness import correspondence as mail
from harness.store import Store
from test_admin import admin_app,AUTH
from test_crm import add,company

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'cloud_browser'))
from worker import protocol as li
from worker.state import State
from worker.service import Worker

ACCOUNT='urn:li:fsd_profile:FixtureOwner'
THREAD='urn:li:messagingThread:fixture-thread'


def peer(ident,name,url=''):
    return {'entityUrn':'participant:'+ident,'hostIdentityUrn':'urn:li:fsd_profile:'+ident,'participantType':{'member':{'firstName':{'text':name},'lastName':{'text':'Test'},'profileUrl':url}}}


def response(outbound=False,stamp=1760000000000):
    sender=peer('FixtureOwner' if outbound else 'Peer','Owner' if outbound else 'Riley','https://www.linkedin.com/in/fixture-person/' if not outbound else '')
    other=peer('Peer' if outbound else 'FixtureOwner','Riley' if outbound else 'Owner','https://www.linkedin.com/in/fixture-person/' if outbound else '')
    message={'entityUrn':'frontend-message','backendUrn':'urn:li:messagingMessage:fixture-'+str(outbound),'deliveredAt':stamp,'body':{'text':'Synthetic text'},'*sender':sender['entityUrn'],'originToken':'not-an-outbound-proof'}
    return {'data':{'data':{'messengerMessagesByAnchorTimestamp':{'*elements':['frontend-message'],'metadata':{}}}},'included':[message,sender,other]}


def test_parse_sender_not_origin_token_and_canonical_message_id():
    incoming,_,more=li.messages(response(),ACCOUNT,THREAD,[])
    outgoing,_,_=li.messages(response(True),ACCOUNT,THREAD,[])
    assert incoming[0]['direction']=='inbound' and outgoing[0]['direction']=='outbound'
    assert incoming[0]['external_id']=='urn:li:messagingMessage:fixture-False'
    assert outgoing[0]['recipients'][0]['profile_url']=='https://www.linkedin.com/in/fixture-person/'
    assert not more
    assert mail.Message.model_validate(incoming[0]).occurred_at.tzinfo
    with pytest.raises(li.SchemaError):li.messages({'data':{}},ACCOUNT,THREAD,[])
    broken=response();broken['included'][0].pop('backendUrn')
    with pytest.raises(li.SchemaError):li.messages(broken,ACCOUNT,THREAD,[])


def test_protocol_url_and_realtime_event_parser():
    url=li.query_url('messages',ACCOUNT,thread=THREAD,anchor=123)
    assert 'conversationUrn:urn%3Ali%3Amsg_conversation%3A%28' in url and '%2528' not in url
    event={'com.linkedin.realtimefrontend.DecoratedEvent':{'topic':'urn:li:messagesTopic:urn:li-realtime:abc','payload':{'event':{'conversationUrn':THREAD}}}}
    assert li.event_thread(event)==THREAD
    assert list(li.sse_data([': comment','data: '+json.dumps(event),'']))==[event]


def test_restart_preserves_outbox_and_requeues_inflight_jobs(tmp_path):
    state=State(tmp_path);messages,_,_=li.messages(response(),ACCOUNT,THREAD,[])
    state.enqueue('thread',{'thread':THREAD},'event-one');assert state.take('jobs')['id']=='event-one'
    state.save_messages(messages);row=state.take('outbox');assert row
    resumed=State(tmp_path)
    assert resumed.take('jobs')['id']=='event-one'
    delivery=resumed.take('outbox');assert delivery['id']==row['id']
    resumed.retry('outbox',delivery,'Synthetic delivery unavailable')
    assert resumed.counts('outbox')=={'retry':1}
    resumed.save_messages(messages);assert resumed.counts('outbox')=={'retry':1}
    resumed.finish('outbox',delivery['id']);assert State(tmp_path).counts('outbox')=={'done':1}


def test_history_schema_failure_does_not_advance_or_mark_complete(tmp_path):
    worker=Worker(tmp_path);worker.account=ACCOUNT
    worker.read=AsyncMock(return_value={'data':{}})
    body={'thread':THREAD,'anchor':1760000001000,'peers':[],'subject':'','cutoff':0}
    worker.state.enqueue('thread',body,'thread-one');row=worker.state.take('jobs')
    with pytest.raises(li.SchemaError):asyncio.run(worker.process(row))
    assert not worker.state.get('watermark:'+THREAD)
    assert worker.state.counts('jobs')=={'working':1} and not worker.state.counts('outbox')
    worker.read=AsyncMock(return_value=response())
    asyncio.run(worker.process(row))
    assert worker.state.counts('outbox')=={'queued':1} and worker.state.counts('jobs')=={'done':1}


def test_capture_auth_account_pin_and_idempotent_crm_linking(tmp_path,monkeypatch):
    monkeypatch.setenv('LINKEDIN_CAPTURE_TOKEN','synthetic-bridge-access')
    store=Store(tmp_path);co=add(store,company());person=add(store,{'kind':'person','company_id':co,'name':'Riley Test','urls':['https://www.linkedin.com/in/fixture-person/']})
    messages,_,_=li.messages(response(),ACCOUNT,THREAD,[]);message=messages[0]
    with pytest.raises(PermissionError):gateway.receive(store,'Bearer wrong',message)
    result=gateway.receive(store,'Bearer synthetic-bridge-access',message)
    assert result['saved'] and result['linked']
    assert gateway.receive(store,'Bearer synthetic-bridge-access',message)['id']==result['id']
    assert len(store.query('SELECT * FROM crm_messages'))==1
    activity=store.query('SELECT * FROM crm_records WHERE id=?',(result['activity_id'],))[0]
    assert activity['certainty']=='provider_observed' and json.loads(activity['body'])['person_id']==person
    with pytest.raises(ValueError):gateway.receive(store,'Bearer synthetic-bridge-access',{**message,'account':'urn:li:fsd_profile:Other'})
    assert store.query('SELECT account FROM crm_linkedin_capture')[0]['account']==ACCOUNT
    assert store.query('SELECT account FROM crm_messages')[0]['account']==ACCOUNT


def test_owner_gateway_private_and_no_token_forwarded_to_ui(tmp_path,monkeypatch):
    module,store=admin_app(tmp_path,monkeypatch)
    monkeypatch.delenv('CLOUD_BROWSER_URL',raising=False)
    monkeypatch.setenv('LINKEDIN_CAPTURE_TOKEN','synthetic-bridge-access')
    with TestClient(module.app,base_url='https://testserver') as client:
        for path in ('status','frame'):
            assert client.get('/api/admin/cloud-browser/'+path).status_code==401
        assert client.post('/api/admin/cloud-browser/control',json={'action':'start'}).status_code==401
        state=client.get('/api/admin/cloud-browser/status',headers=AUTH)
        assert state.headers['cache-control']=='private, no-store' and state.json()['state']=='not_deployed'
        assert client.post('/api/hooks/linkedin',json={}).status_code==403
        assert 'cloud-browser' not in client.get('/openapi.json').text
        assert 'synthetic-bridge-access' not in state.text
        assert client.post('/api/admin/cloud-browser/control',headers={**AUTH,'Origin':'https://untrusted.example'},json={'action':'start'}).status_code==403


def test_delivery_requires_durable_matching_ack_and_retries_after_restart(tmp_path,monkeypatch):
    import httpx
    monkeypatch.setenv('CRM_CAPTURE_URL','https://crm.example.org/api/hooks/linkedin')
    monkeypatch.setenv('CRM_CAPTURE_TOKEN','synthetic-capture-access')
    worker=Worker(tmp_path);captured,_,_=li.messages(response(),ACCOUNT,THREAD,[])
    worker.state.save_messages(captured);row=worker.state.take('outbox')
    async def deliver(ack):
        def handler(request):
            assert request.headers['authorization']=='Bearer synthetic-capture-access'
            return httpx.Response(200,json=ack)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:await worker.deliver_one(row,client)
    asyncio.run(deliver({'saved':True,'external_id':'wrong-message'}))
    assert worker.state.counts('outbox')=={'retry':1} and not worker.state.get('last_saved')
    resumed=Worker(tmp_path)
    with resumed.state.db() as db:db.execute('UPDATE outbox SET due=0')
    row=resumed.state.take('outbox')
    asyncio.run(deliver({'saved':True,'external_id':captured[0]['external_id']}))
    assert resumed.state.counts('outbox')=={'done':1} and resumed.state.get('last_saved')


def test_browser_guard_blocks_internal_network_and_messaging_writes(tmp_path):
    from types import SimpleNamespace
    worker=Worker(tmp_path)
    async def check(url,method,blocked):
        route=SimpleNamespace(request=SimpleNamespace(url=url,method=method),abort=AsyncMock(),continue_=AsyncMock())
        await worker.guard(route)
        assert route.abort.called==blocked and route.continue_.called!=blocked
    asyncio.run(check('https://www.linkedin.com/login','POST',False))
    asyncio.run(check('https://www.linkedin.com/voyager/api/voyagerMessagingDashMessengerMessages?action=createMessage','POST',True))
    asyncio.run(check('http://127.0.0.1:8080/','GET',True))
    asyncio.run(check('https://www.linkedin.com/voyager/api/me','GET',False))
    asyncio.run(check('https://evil.example.org/','GET',True))
    asyncio.run(check('https://challenges.cloudflare.com/turnstile/v0/api.js','GET',False))
    asyncio.run(check('https://challenges.cloudflare.com.evil.example.org/','GET',True))
