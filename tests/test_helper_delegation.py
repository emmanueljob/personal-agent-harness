import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from harness import agent, grok_connection as grok, helper_delegation as helpers, runner
from harness.muse_transport import MUSE, MuseTransport
from harness.muse_companion_worker import synchronize
from test_grok_connection import setup, OWNER, call, content


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def ready(tmp_path, monkeypatch):
    module, store = setup(tmp_path, monkeypatch)
    monkeypatch.setenv('MODEL_PROVIDER', 'ollama')
    monkeypatch.setenv('MUSE_BRIDGE_TOKEN', 'synthetic-bridge')
    helpers.initialize(store)
    task = grok.create_assignment(store, grok.Assignment(request_id='synthetic-helper-proof'))
    monkeypatch.setattr(grok, 'push_webhook', lambda _:200)
    grok.dispatch_assignment(store, task['id'])
    grok.acknowledge(store, task['id'], 'synthetic-proof-claim')
    grok.submit(store, task['id'], 'synthetic-proof-claim', grok.Result(
        submission_id='synthetic-proof-result',outcome='completed',report='SYNTHETIC CONNECTION OK'))
    with store.connect() as db:
        import time
        db.execute('INSERT OR REPLACE INTO muse_bridge_state VALUES (1,?,1)', (time.time(),))
    return module, store


def test_real_owner_graph_both_helpers_and_correlated_owner_answer(tmp_path, monkeypatch):
    module, store = ready(tmp_path, monkeypatch)
    model = ToolModel(responses=[
        AIMessage(content='', tool_calls=[{'id':'delegate-call', 'name':'delegate_work',
            'args': {'instruction':'Research Fixture Instruments and prepare an outreach draft.',
                     'kind':'draft_outreach', 'helpers':['grokbot'], 'include_card':False}}]),
        AIMessage(content='Both helpers are working. No outreach is confirmed.'),
        AIMessage(content='The research and draft reports are ready. External delivery remains unverified.')])
    monkeypatch.setattr(agent, 'local_model', lambda *_: model)
    sent, events = [], []
    monkeypatch.setattr(grok, 'push_webhook', lambda payload: sent.append(payload) or 200)
    with TestClient(module.app) as client:
        thread = client.post('/api/admin/conversations',headers=OWNER,json={'agent':'owner'}).json()['id']
        path = '/api/admin/conversations/'+thread
        body = {'request_id':'synthetic-owner-delegation',
                'text':'Ask Muse and Grokbot to research Fixture Instruments and draft outreach.', 'context':''}
        posted = client.post(path+'/messages',headers=OWNER,json=body)
        assert posted.status_code == 200, posted.text
        owner_turn = posted.json()['turns'][0]['id']
        runner.run_turn(store, owner_turn)
        jobs = client.get(path,headers=OWNER).json()['turns'][0]['delegations']
        assert len(jobs) == 1 and jobs[0]['status'] == 'pending'
        assert {t['helper'] for t in jobs[0]['tasks']} == {'grokbot','muse'}
        job = jobs[0]['id']
        assert client.get('/api/admin/helper-requests/'+job).status_code == 401
        helpers.resume(store)
        assert len(sent) == 1 and sent[0]['action'] == 'career_question'
        assert 'relevant_card": {}' in sent[0]['instruction']
        grok_task = next(t['id'] for t in jobs[0]['tasks'] if t['helper']=='grokbot')
        claim = {'task_id':grok_task,'claim_id':'synthetic-helper-claim'}
        assert content(call(client,'acknowledge_assignment',claim))['should_run']
        result = {'submission_id':'synthetic-helper-result','outcome':'completed',
                  'report':'Synthetic research report. No outreach sent.'}
        assert content(call(client,'submit_result',{**claim,'result':result}))['accepted']
        partial = client.get(path,headers=OWNER).json()['turns'][0]['delegations'][0]
        assert partial['status'] == 'partial'
        async def upstream(name,args):
            if name == 'connection_status':
                return {'connected':True,'logged_in':True}
            if name == 'send_message':
                assert args['recipient'] == MUSE
                events.append({'id':'synthetic-muse-event','sender':MUSE,'chat_jid':MUSE,'from_me':False,
                    'text':args['text'].splitlines()[-1]+'\nSynthetic draft report. Draft only.'})
                return {'id':'synthetic-muse-send','chat_jid':MUSE}
            return {'messages':events}
        async def bridge(path,body):
            response = client.post('/api/muse-companion'+path,json=body,
                                   headers={'Authorization':'Bearer synthetic-bridge'})
            assert response.is_success,response.text
            return response.json()
        asyncio.run(synchronize(MuseTransport(upstream),bridge))
        helpers.resume(store)
        synthesis = store.query('SELECT synthesis_id FROM owner_helper_requests WHERE id=?',(job,))[0]['synthesis_id']
        assert synthesis
        frozen = json.loads(store.query('SELECT body FROM execution_context WHERE turn_id=?',(synthesis,))[0]['body'])
        assert frozen['config']['home_delegation'] is False
        runner.run_turn(store,synthesis)
        reopened = client.get(path,headers=OWNER).json()
        assert len(reopened['turns']) == 1
        assert reopened['turns'][0]['text'] == body['text']
        assert reopened['turns'][0]['reply'].startswith('The research and draft reports')
        assert all(not t['deliveryVerified'] for t in reopened['turns'][0]['delegations'][0]['tasks'])
        assert content(call(client,'submit_result',{**claim,'result':result}))['duplicate']
        helpers.resume(store)
        assert len(store.query('SELECT * FROM turns WHERE thread_id=?',(thread,))) == 2
        assert client.post(path+'/messages',headers=OWNER,json=body).json()['turns'][0]['id']==owner_turn
        # Internal helper threads do not become the owner's direct My Career conversations.
        for helper in ('grokbot','muse'):
            assert client.get('/api/admin/conversations?agent='+helper,headers=OWNER).json()['threads'] == []


def test_card_enqueue_transaction_dedup_and_unavailable_retry(tmp_path, monkeypatch):
    module, store = ready(tmp_path, monkeypatch)
    monkeypatch.delenv('GROKBOT_WEBHOOK_KEY')
    body = helpers.WorkRequest(request_id='synthetic-card-request',source_kind='card_action',
        source_id='synthetic-action',kind='research',instruction='Research Fixture Company',helpers=['grokbot'])
    def request(_):
        return helpers.request_work(store,body)
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs=list(pool.map(request,range(4)))
    assert len({j['id'] for j in jobs}) == 1
    job=jobs[0]
    assert job['status'] == 'unavailable'
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        assert helpers.job_status(store,job['id'],connection=db)['id'] == job['id']
        assert db.in_transaction
    with pytest.raises(Exception):
        helpers.request_work(store,body.model_copy(update={'instruction':'A different task'}))
    monkeypatch.setenv('GROKBOT_WEBHOOK_KEY','synthetic-webhook-key')
    assert helpers.retry_request(store,job['id'])['status'] == 'pending'
    sent=[]
    def uncertain(payload):
        sent.append(payload)
        raise TimeoutError()
    monkeypatch.setattr(grok,'push_webhook',uncertain)
    helpers.resume(store)
    assert helpers.job_status(store,job['id'])['status'] == 'unknown'
    helpers.resume(store)
    assert len(sent)==1
    helpers.retry_request(store,job['id'])
    helpers.resume(store)
    assert len(sent)==2 and sent[0]['task_id']==sent[1]['task_id']


def test_local_crm_uses_local_tools_without_helper_assignment(tmp_path, monkeypatch):
    module,store=ready(tmp_path,monkeypatch)
    model=ToolModel(responses=[
        AIMessage(content='',tool_calls=[{'id':'local-read','name':'search_job_crm','args':{'query':'Fixture'}}]),
        AIMessage(content='I checked the local CRM.')])
    monkeypatch.setattr(agent,'local_model',lambda *_:model)
    with TestClient(module.app) as client:
        ident=client.post('/api/admin/conversations',headers=OWNER,json={'agent':'owner'}).json()['id']
        response=client.post('/api/admin/conversations/'+ident+'/messages',headers=OWNER,
            json={'request_id':'synthetic-local-crm','text':'Show my local CRM records.','context':''})
        runner.run_turn(store,response.json()['turns'][0]['id'])
        assert not store.query('SELECT * FROM owner_helper_requests')


def test_named_helpers_permissions_and_unknown_muse_are_truthful(tmp_path, monkeypatch):
    module, store = ready(tmp_path, monkeypatch)
    for helper in ('Muse','Grokbot'):
        body = helpers.WorkRequest(request_id='synthetic-named-'+helper,source_kind='card_action',
            source_id='synthetic-named-action-'+helper,kind='research',instruction='Research a synthetic topic',
            user_message='Ask '+helper+' to research a synthetic topic.')
        job = helpers.request_work(store,body)
        assert [t['helper'] for t in job['tasks']] == [helper.lower()]
    forbidden = helpers.WorkRequest(request_id='synthetic-no-send',source_kind='card_action',
        source_id='synthetic-no-send-source',kind='send_outreach',instruction='Send a note',
        user_message='Do not message anyone. Draft only.')
    with pytest.raises(Exception):
        helpers.request_work(store,forbidden)
    assert not store.query('SELECT * FROM owner_helper_requests WHERE request_id=?',(forbidden.request_id,))
    allowed = forbidden.model_copy(update={'request_id':'synthetic-explicit-send',
        'user_message':'Send the approved synthetic message to Casey Fixture by email.'})
    job = helpers.request_work(store,allowed)
    assert job['tasks'][0]['helper']=='muse'
    task = job['tasks'][0]['id']
    with store.connect() as db:
        db.execute("UPDATE muse_turns SET status='delivery_unknown' WHERE id=?", (task,))
    retried = helpers.retry_request(store,job['id'])
    assert retried['status']=='unknown'
    assert retried['tasks'][0]['status']=='delivery_unknown'
    assert retried['tasks'][0]['deliveryVerified'] is False
    assert 'not resent' in retried['retryNote']


def test_enqueue_rollback_and_snapshot_guard(tmp_path, monkeypatch):
    from harness import command_center
    from test_command_center import add, company
    module, store = ready(tmp_path, monkeypatch)
    ident = add(store,company('Fixture company',next_action='Research this company'))
    body = helpers.WorkRequest(request_id='synthetic-rollback',source_kind='card_action',
        source_id='synthetic-action-rollback',kind='research',instruction='Research Fixture company',
        include_card=True,card_id=ident,revision=999)
    with pytest.raises(Exception):
        helpers.request_work(store,body)
    assert not store.query('SELECT * FROM owner_helper_requests')
    with pytest.raises(ValueError):
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            helpers.request_work(store,body.model_copy(update={'revision':1}),connection=db)
            raise ValueError('synthetic outer transaction failure')
    assert not store.query('SELECT * FROM owner_helper_requests')
    assert not store.query('SELECT * FROM owner_helper_tasks')


def test_local_crm_write_and_automatic_research_routing(tmp_path, monkeypatch):
    from test_command_center import add, company
    module,store=ready(tmp_path,monkeypatch)
    record=add(store,company('Fixture local CRM'))
    model=ToolModel(responses=[
        AIMessage(content='',tool_calls=[{'id':'local-update','name':'update_job_record',
            'args':{'record_id':record,'revision':1,'changes':{'notes':'Synthetic local correction'}}}]),
        AIMessage(content='Updated the local CRM.'),
        AIMessage(content='',tool_calls=[{'id':'automatic-research','name':'delegate_work',
            'args':{'instruction':'Research Fixture Company','kind':'research'}}]),
        AIMessage(content='Research is pending with the helper.')])
    monkeypatch.setattr(agent,'local_model',lambda *_:model)
    with TestClient(module.app) as client:
        thread=client.post('/api/admin/conversations',headers=OWNER,json={'agent':'owner'}).json()['id']
        path='/api/admin/conversations/'+thread
        for i,text in enumerate(('Change the local CRM notes.','Research Fixture Company.')):
            result=client.post(path+'/messages',headers=OWNER,json={
                'request_id':f'synthetic-routing-{i}','text':text,'context':''})
            assert result.status_code==200,result.text
            runner.run_turn(store,result.json()['turns'][-1]['id'])
            if i==0:
                assert not store.query('SELECT * FROM owner_helper_requests')
                assert json.loads(store.query('SELECT body FROM crm_records WHERE id=?',(record,))[0]['body'])['notes']=='Synthetic local correction'
        jobs=store.query('SELECT id FROM owner_helper_requests')
        assert len(jobs)==1
        assert helpers.job_status(store,jobs[0]['id'])['tasks'][0]['helper']=='grokbot'


def test_lost_tool_result_retry_reuses_persisted_assignment(tmp_path, monkeypatch):
    module,store=ready(tmp_path,monkeypatch)
    model=ToolModel(responses=[
        AIMessage(content='',tool_calls=[{'id':'crash-safe-helper','name':'delegate_work',
            'args':{'instruction':'Research a synthetic company','kind':'research'}}]),
        AIMessage(content='The existing helper request is pending.')])
    monkeypatch.setattr(agent,'local_model',lambda *_:model)
    original=helpers.request_work
    def crash_after_commit(*args,**kwargs):
        original(*args,**kwargs)
        raise RuntimeError('Synthetic process failure after durable enqueue')
    monkeypatch.setattr(helpers,'request_work',crash_after_commit)
    with TestClient(module.app) as client:
        thread=client.post('/api/admin/conversations',headers=OWNER,json={'agent':'owner'}).json()['id']
        path='/api/admin/conversations/'+thread
        result=client.post(path+'/messages',headers=OWNER,json={
            'request_id':'synthetic-lost-result','text':'Research a synthetic company.','context':''})
        turn=result.json()['turns'][0]['id']
        with pytest.raises(RuntimeError):
            runner.run_turn(store,turn)
        original_task=store.query('SELECT task_id FROM owner_helper_tasks')[0]['task_id']
        monkeypatch.setattr(helpers,'request_work',original)
        assert store.retry(turn)
        runner.run_turn(store,turn)
        assert [r['task_id'] for r in store.query('SELECT task_id FROM owner_helper_tasks')]==[original_task]
        assert len(store.query('SELECT * FROM owner_helper_requests'))==1
        assert client.get(path,headers=OWNER).json()['turns'][0]['reply'].startswith('The existing helper request')


def test_deep_catalog_registers_owner_tools():
    from harness import deep_runtime
    deep_runtime.register_tools()
    assert {'delegate_work','read_helper_work'} <= deep_runtime.ROUTED.keys()
