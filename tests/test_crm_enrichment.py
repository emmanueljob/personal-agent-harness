import json
import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from harness import crm,crm_enrichment as enrichment
from harness.crm_workspace import tools
from harness.public_access import reserve_model_call
from harness.store import Store
from test_crm import add,company
from test_admin import admin_app,AUTH


def configured(monkeypatch):monkeypatch.setattr('harness.models.settings',lambda choice='default':{'configured':True,'agent_runtime':'legacy'})


def create(tmp_path,monkeypatch):
    configured(monkeypatch);store=Store(tmp_path);ident=add(store,company(priority='high',notes='Owner notes',next_action='Owner next step',news=[dict(title='Existing news',url='https://example.org/old')]))
    result=enrichment.start(store,enrichment.Start(request_id='enrich-fixture-001',record_ids=[ident]))
    return store,ident,result['jobs'][0]


def catalog(store,job):
    tid=store.query('SELECT thread_id FROM turns WHERE id=?',(job['turn_id'],))[0]['thread_id']
    return {t.name:t for t in tools({'mode':'private','store':store,'turn_id':job['turn_id'],'thread_id':tid})}


def finish(store,turn,status):
    with store.connect() as db:db.execute('UPDATE turns SET status=? WHERE id=?',(status,turn))


def test_private_quota_exemption_keeps_public_cap(tmp_path,monkeypatch):
    monkeypatch.setenv('PUBLIC_ONLY','true');monkeypatch.setenv('PUBLIC_DAILY_MODEL_CALLS','1');store=Store(tmp_path)
    reserve_model_call(store)
    reserve_model_call(store,'private');reserve_model_call(store,'private')
    with pytest.raises(HTTPException):reserve_model_call(store,'public')
    assert store.query("SELECT count FROM public_usage WHERE bucket='day:model'")[0]['count']==1


def test_batches_are_idempotent_deduplicate_active_and_isolate_threads(tmp_path,monkeypatch):
    store,ident,job=create(tmp_path,monkeypatch)
    again=enrichment.start(store,enrichment.Start(request_id='enrich-fixture-001',record_ids=[ident]))
    assert again['jobs'][0]['turn_id']==job['turn_id']
    other=add(store,company('Other',priority='high'))
    body=enrichment.Start(request_id='enrich-fixture-002',record_ids=[ident,other])
    enrichment.start(store,body);enrichment.start(store,body)
    jobs=enrichment.status(store)
    assert len(jobs)==2
    threads=store.query('SELECT DISTINCT thread_id FROM turns')
    assert len(threads)==2
    with pytest.raises(ValueError):enrichment.start(store,enrichment.Start(request_id='enrich-fixture-001',record_ids=[other]))
    assert all(crm.coordinator_prompt(store,t['thread_id']) for t in threads)


def test_completion_requires_actual_save_then_retries_without_manual_action(tmp_path,monkeypatch):
    store,ident,job=create(tmp_path,monkeypatch)
    finish(store,job['turn_id'],'completed')
    first=enrichment.resume(store,100)[0]
    assert first['status']=='retrying' and 'did not save' in first['message']
    assert enrichment.resume(store,129)[0]['attempt']==1
    second=enrichment.resume(store,130)[0]
    assert second['attempt']==2 and second['turn_id']!=job['turn_id']
    t=catalog(store,second)
    assert 'error' in t['update_job_record'].invoke({'record_id':ident,'revision':1,'changes':{}})
    saved=t['update_job_record'].invoke({'record_id':ident,'revision':1,'changes':{'notes':'New dated evidence with a source https://example.org/news','summary':'A concise supported brief','next_action':'Overwrite owner action','news':[{'title':'New source','url':'https://example.org/new'}]}})
    assert saved['revision']==2
    body=saved['record'];assert body['notes'].startswith('Owner notes') and body['next_action']=='Owner next step' and len(body['news'])==2
    finish(store,second['turn_id'],'failed') # worker died after the successful write
    assert enrichment.resume(store,131)[0]['status']=='saved'
    assert enrichment.status(Store(tmp_path))[0]['status']=='saved'


def test_enrichment_cannot_write_other_records_or_replay_stale_attempts(tmp_path,monkeypatch):
    store,ident,job=create(tmp_path,monkeypatch);other=add(store,company('Other'))
    t=catalog(store,job)
    assert 'error' in t['update_job_record'].invoke({'record_id':other,'revision':1,'changes':{'notes':'No'}})
    assert 'error' in t['write_job_record'].invoke({'record_id':ident,'revision':1,'record':company('Replacement')})
    finish(store,job['turn_id'],'failed');enrichment.resume(store,1);enrichment.resume(store,31)
    assert 'error' in t['update_job_record'].invoke({'record_id':ident,'revision':1,'changes':{'notes':'Late stale write'}})
    assert store.query('SELECT revision FROM crm_records WHERE id=?',(ident,))[0]['revision']==1


def test_failed_jobs_back_off_and_stop_after_four_attempts(tmp_path,monkeypatch):
    store,ident,job=create(tmp_path,monkeypatch)
    now=100
    for attempt in range(1,5):
        finish(store,job['turn_id'],'failed')
        job=enrichment.resume(store,now)[0]
        if attempt<4:
            assert job['status']=='retrying'
            now=job['next_retry'];job=enrichment.resume(store,now)[0]
        else:assert job['status']=='needs_attention'
    assert len(store.query('SELECT * FROM crm_enrichment_attempts'))==4
    assert enrichment.resume(Store(tmp_path),99999)[0]['status']=='needs_attention'


def test_only_private_owner_can_start_batch_and_state_has_no_raw_notes(tmp_path,monkeypatch):
    configured(monkeypatch);module,store=admin_app(tmp_path,monkeypatch);configured(monkeypatch)
    ident=add(store,company(priority='high',notes='PRIVATE RESEARCH CANARY'))
    with TestClient(module.app,base_url='https://testserver') as client:
        body={'request_id':'enrichment-route-001','record_ids':[ident]}
        assert client.post('/api/admin/crm/enrichment',json=body).status_code==401
        result=client.post('/api/admin/crm/enrichment',headers=AUTH,json=body)
        assert result.status_code==200
        assert 'PRIVATE RESEARCH CANARY' not in result.text
        assert client.get('/api/admin/crm/enrichment',headers=AUTH).headers['cache-control']=='private, no-store'
        assert '/api/admin/crm/enrichment' not in client.get('/openapi.json').text
        assert client.post('/api/admin/crm/enrichment',headers={**AUTH,'Origin':'https://untrusted.example'},json=body).status_code==403


def test_save_tool_remains_available_after_research_budget():
    from harness.agent import finish_bounded_research
    from langchain.agents.middleware.types import ModelRequest
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.messages import ToolMessage,SystemMessage
    request=ModelRequest(model=FakeListChatModel(responses=['unused']),messages=[ToolMessage(content='Supported finding',tool_call_id=str(i)) for i in range(16)],tools=[{'name':'search_web'},{'name':'update_job_record'}],system_message=SystemMessage(content='You are the job-search coordinator.'))
    result=finish_bounded_research.wrap_model_call(request,lambda v:v)
    assert result.tools==[{'name':'update_job_record'}]
    assert 'Save requested CRM findings' in result.system_message.content


def test_page_reader_exposes_observed_images_without_fetching_them(monkeypatch):
    import httpx
    from harness import web_tools as web
    from test_web_tools import reader_transport
    def handler(request):
        return httpx.Response(200,headers={'content-type':'text/html'},text='<title>Company</title><meta property="og:image" content="/cover.png"><link rel="icon" href="/favicon.svg"><meta name="twitter:image" content="http://localhost/private"><main>Verified article</main>')
    reader_transport(monkeypatch,handler)
    result=web.read_public_page('https://company.example/news')
    assert set(result['media'])=={'https://company.example/cover.png','https://company.example/favicon.svg'}
    assert result['text']=='Verified article'
