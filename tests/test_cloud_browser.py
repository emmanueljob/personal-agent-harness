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


def test_realtime_connection_supplies_application_name_and_queues_events(tmp_path):
    import httpx
    event={'com.linkedin.realtimefrontend.DecoratedEvent':{'topic':'urn:li:messagesTopic:urn:li-realtime:fixture','payload':{'event':{'conversationUrn':THREAD}}}}
    def provider(request):
        track=json.loads(request.headers.get('x-li-track','{}'))
        if track.get('mpName')!='voyager-web':
            return httpx.Response(400,text='Application name is required.')
        return httpx.Response(200,headers={'content-type':'text/event-stream'},text='data: '+json.dumps(event)+'\n\n')
    worker=Worker(tmp_path)
    async def capture():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            rejected=await client.get(li.BASE+'/realtime/connect?rc=1')
            assert rejected.status_code==400
            worker.http=client
            await worker.stream()
    asyncio.run(capture())
    assert worker.state.counts('jobs')=={'queued':1}
    assert json.loads(worker.state.take('jobs')['body'])['thread']==THREAD
    assert worker.state.get('last_event')>0
    assert not worker.transport


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


def test_native_browser_has_no_automation_flags_and_gets_no_service_credentials(tmp_path,monkeypatch):
    from worker.desktop import chrome_command,browser_environment,linkedin_cookies
    monkeypatch.setenv('BROWSER_WORKER_TOKEN','synthetic-worker-access')
    monkeypatch.setenv('CRM_CAPTURE_TOKEN','synthetic-capture-access')
    command=chrome_command(tmp_path)
    assert command[0]=='google-chrome-stable'
    assert not any('headless' in part or 'automation' in part or part=='--no-sandbox' for part in command)
    assert '--remote-debugging-address=127.0.0.1' in command
    assert 'BROWSER_WORKER_TOKEN' not in browser_environment(tmp_path)
    assert 'CRM_CAPTURE_TOKEN' not in browser_environment(tmp_path)
    cookies=linkedin_cookies([{'domain':'.linkedin.com','name':'li_at','value':'synthetic-session'},{'domain':'.google.com','name':'unrelated','value':'not-forwarded'},{'domain':'linkedin.com.evil.example','name':'evil','value':'not-forwarded'}])
    assert cookies=={'li_at':'synthetic-session'}


def test_desktop_tickets_are_hashed_single_use_and_expire(tmp_path,monkeypatch):
    store=Store(tmp_path);token=gateway.desktop_ticket(store)
    assert store.query('SELECT digest FROM crm_desktop_tickets')[0]['digest']!=token
    assert gateway.consume_desktop_ticket(store,token)
    assert not gateway.consume_desktop_ticket(store,token)
    token=gateway.desktop_ticket(store)
    with store.connect() as db:db.execute('UPDATE crm_desktop_tickets SET expires=0')
    assert not gateway.consume_desktop_ticket(store,token)


def test_desktop_gateway_rejects_wrong_origin_without_consuming_ticket(tmp_path,monkeypatch):
    from starlette.websockets import WebSocketDisconnect
    module,store=admin_app(tmp_path,monkeypatch)
    monkeypatch.setenv('CLOUD_BROWSER_URL','http://worker.internal:8090')
    monkeypatch.setenv('CLOUD_BROWSER_TOKEN','synthetic-worker-access')
    with TestClient(module.app,base_url='https://testserver') as client:
        assert client.post('/api/admin/cloud-browser/desktop-ticket').status_code==401
        result=client.post('/api/admin/cloud-browser/desktop-ticket',headers=AUTH)
        assert result.status_code==200 and result.headers['cache-control']=='private, no-store'
        token=result.json()['ticket']
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/api/admin/cloud-browser/desktop',headers={'Origin':'https://untrusted.example'},subprotocols=['desktop','ticket.'+token]):pass
        assert gateway.consume_desktop_ticket(store,token)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/api/admin/cloud-browser/desktop',headers={'Origin':'https://testserver'},subprotocols=['desktop','ticket.'+token]):pass


def test_desktop_gateway_relays_authenticated_binary_without_exposing_worker_token(tmp_path,monkeypatch):
    module,store=admin_app(tmp_path,monkeypatch)
    monkeypatch.setenv('CLOUD_BROWSER_URL','http://worker.internal:8090')
    monkeypatch.setenv('CLOUD_BROWSER_TOKEN','synthetic-worker-access')
    import websockets.asyncio.client
    class Upstream:
        def __init__(self):self.ready=asyncio.Event();self.values=[]
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def send(self,value):self.values.append(value);self.ready.set()
        async def __aiter__(self):
            yield b'RFB 003.008\n'
            await self.ready.wait()
            yield b'synthetic-screen-frame'
            await asyncio.Event().wait()
    upstream=None
    def connect(url,**kw):
        nonlocal upstream
        assert url=='ws://worker.internal:8090/desktop'
        assert kw['additional_headers']=={'Authorization':'Bearer synthetic-worker-access'}
        assert kw['proxy'] is None
        upstream=Upstream();return upstream
    monkeypatch.setattr(websockets.asyncio.client,'connect',connect)
    with TestClient(module.app,base_url='https://testserver') as client:
        token=client.post('/api/admin/cloud-browser/desktop-ticket',headers=AUTH).json()['ticket']
        with client.websocket_connect('/api/admin/cloud-browser/desktop',headers={'Origin':'https://testserver'},subprotocols=['desktop','ticket.'+token]) as socket:
            assert socket.accepted_subprotocol=='desktop'
            assert socket.receive_bytes()==b'RFB 003.008\n'
            socket.send_bytes(b'synthetic-keyboard-input')
            assert socket.receive_bytes()==b'synthetic-screen-frame'
        assert upstream.values==[b'synthetic-keyboard-input']
        assert not gateway.consume_desktop_ticket(store,token)


def test_worker_desktop_rejects_unauthenticated_websocket(monkeypatch):
    from worker.service import app
    from starlette.websockets import WebSocketDisconnect
    monkeypatch.setenv('BROWSER_WORKER_TOKEN','synthetic-worker-access')
    client=TestClient(app)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/desktop'):pass


def test_sent_outreach_dossier_uses_current_association_and_original_receipt(tmp_path, monkeypatch):
    from harness import crm

    monkeypatch.setenv('LINKEDIN_CAPTURE_TOKEN', 'synthetic-bridge-access')
    store = Store(tmp_path)
    first = add(store, company('Fixture Studio'))
    second = add(store, company('Fixture Works'))
    profile = 'https://www.linkedin.com/in/fixture-person/'
    contact = add(store, {'kind': 'person', 'company_id': first, 'name': 'Riley Test', 'urls': [profile]})
    corrected = add(store, {'kind': 'person', 'company_id': second, 'name': 'Riley Example'})
    payload = li.messages(response(outbound=True), ACCOUNT, THREAD, [])[0][0]

    def receive():
        return gateway.receive(store, 'Bearer synthetic-bridge-access', payload)

    def outreach():
        return [r for r in crm.overview(store)['records'] if 'outreach_receipt' in r]

    result = receive()
    activity, = outreach()
    receipt = activity['outreach_receipt']
    assert activity['id'] == result['activity_id']
    assert (activity['record']['company_id'], activity['record']['person_id']) == (first, contact)
    assert activity['record']['happened_on'] == '2025-10-09'
    assert receipt == {'id': result['id'], 'occurred_at': payload['occurred_at'], 'recipients': ['Riley Test']}

    mail.link(store, receipt['id'], second, corrected)
    assert receive() == result
    activity, = outreach()
    assert activity['outreach_receipt'] == receipt
    assert (activity['record']['company_id'], activity['record']['person_id']) == (second, corrected)
    assert activity['id'] == result['activity_id']
    assert len(store.query("SELECT id FROM crm_records WHERE kind='activity'")) == 1


def test_account_identity_resolves_only_the_explicit_authenticated_profile_reference():
    reference='urn:li:fs_miniProfile:FixtureOwner'
    decoy={'entityUrn':'urn:li:fs_miniProfile:UnrelatedMember'}
    owner={'entityUrn':reference}
    assert li.me({'data':{'*miniProfile':reference},'included':[decoy,owner]})==ACCOUNT
    assert li.me({'miniProfile':owner})==ACCOUNT
    assert li.me({'data':{'miniProfile':owner}})==ACCOUNT
    with pytest.raises(li.SchemaError):li.me({'data':{'*miniProfile':reference},'included':[decoy]})
    with pytest.raises(li.SchemaError):li.me({'data':{'*miniProfile':reference},'included':[owner,owner]})
    with pytest.raises(li.SchemaError):li.me({'data':{'miniProfile':decoy,'*miniProfile':reference},'included':[owner]})


def test_native_shutdown_keeps_display_available_until_chrome_exits(tmp_path):
    from worker.desktop import Desktop
    events=[]
    class Process:
        def __init__(self,name):self.name=name;self.returncode=None
        def terminate(self):events.append('stop '+self.name);self.returncode=0
        async def wait(self):events.append('wait '+self.name);return self.returncode
        def kill(self):raise AssertionError('No forced kill is needed for a clean shutdown.')
    desktop=Desktop(tmp_path)
    desktop.processes=[Process(name) for name in ('display','window-manager','chrome','vnc')]
    asyncio.run(desktop.close())
    assert events==['stop vnc','wait vnc','stop chrome','wait chrome','stop window-manager','wait window-manager','stop display','wait display']
