import json
import pytest
from fastapi.testclient import TestClient
from harness import crm
from harness import crm_workspace as workspace
from harness.store import Store
from test_admin import admin_app, AUTH
from test_crm import add, source, proposal, candidate, company


def catalog(store):
    return {t.name:t for t in crm.tools({'mode':'private','store':store,'turn_id':'workspace-synthetic','thread_id':'workspace-synthetic'})}


def test_chat_reads_complete_totals_and_all_pages(tmp_path):
    store=Store(tmp_path)
    for n in range(41):add(store,company('Example '+str(n)))
    tools=catalog(store)
    first=tools['read_job_workspace'].invoke({'limit':30})
    second=tools['read_job_workspace'].invoke({'offset':first['next_offset'],'limit':30})
    assert first['totals']=={'company':41} and first['matching']==41
    assert len(first['records'])==30 and len(second['records'])==11 and second['next_offset'] is None
    assert len({r['id'] for r in first['records']+second['records']})==41
    assert not crm.tools({'mode':'public'})


def test_chat_writes_read_revision_preserve_owner_edits_and_retry(tmp_path):
    store=Store(tmp_path);tools=catalog(store)
    record={'kind':'company','name':'Example','domain':'example.org','stats':{'Employees':'100 (synthetic)'},'notes':'Owner research'}
    first=tools['write_job_record'].invoke({'record':record})
    assert first['revision']==1
    assert tools['write_job_record'].invoke({'record':record})['id']==first['id']
    body=first['record'];body['next_action']='Prepare questions'
    updated=tools['write_job_record'].invoke({'record_id':first['id'],'revision':1,'record':body})
    assert updated['revision']==2
    patched=tools['update_job_record'].invoke({'record_id':first['id'],'revision':2,'changes':{'follow_up':'2026-11-12'}})
    assert patched['revision']==3 and patched['record']['notes']=='Owner research'
    assert 'error' in tools['update_job_record'].invoke({'record_id':first['id'],'revision':2,'changes':{'next_action':'stale'}})
    body['next_action']='STALE WRITE'
    assert 'error' in tools['write_job_record'].invoke({'record_id':first['id'],'revision':1,'record':body})
    sid=source(store)
    crm.stage(store,sid,proposal(candidate('company',company(domain='example.org',notes='Imported research'))))
    row=workspace.dossier(store,first['id'])['record']
    assert row['record']['notes']=='Owner research' and row['record']['next_action']=='Prepare questions'
    assert len(workspace.dossier(store,first['id'])['research_evidence'])==1


def test_relations_and_contact_research_are_available_to_chat(tmp_path):
    store=Store(tmp_path);tools=catalog(store)
    co=tools['write_job_record'].invoke({'record':company()})
    person=tools['write_job_record'].invoke({'record':{'kind':'person','company_id':co['id'],'name':'Pat','role':'Engineering lead','notes':'Research: built the platform team','urls':['https://example.org/team']}})
    activity=tools['write_job_record'].invoke({'record':{'kind':'activity','company_id':co['id'],'person_id':person['id'],'title':'Intro draft','stage':'drafted','channel':'email','notes':'Synthetic draft'}})
    dossier=tools['read_job_dossier'].invoke({'record_id':person['id']})
    assert dossier['record']['record']['notes'].startswith('Research:')
    assert dossier['related'][0]['id']==activity['id']
    assert 'error' in tools['write_job_record'].invoke({'record':{'kind':'person','company_id':'missing','name':'Orphan'}})


def dashboard():
    return {'title':'Pipeline','format':'dashboard','description':'Synthetic snapshot as of 2026-01-01','columns':[{'key':'stage','label':'Stage'},{'key':'count','label':'Applications','numeric':True}],'rows':[{'stage':'Applied','count':3},{'stage':'Interview','count':1}]}


def test_artifact_persistence_revision_and_private_routes(tmp_path,monkeypatch):
    module,store=admin_app(tmp_path,monkeypatch)
    tools=catalog(store)
    created=tools['save_job_artifact'].invoke({'artifact':dashboard()})
    assert tools['save_job_artifact'].invoke({'artifact':dashboard()})==created
    updated=dashboard();updated['rows'][0]['count']=4
    assert tools['save_job_artifact'].invoke({'artifact':updated,'artifact_id':created['id'],'revision':1})['revision']==2
    assert 'error' in tools['save_job_artifact'].invoke({'artifact':dashboard(),'artifact_id':created['id'],'revision':1})
    with TestClient(module.app,base_url='https://testserver') as client:
        assert client.get('/api/admin/crm/artifacts').status_code==401
        assert client.get('/api/admin/crm/chat').status_code==401
        result=client.get('/api/admin/crm/chat',headers=AUTH)
        assert result.headers['cache-control']=='private, no-store'
        assert result.json()['artifacts'][0]['artifact']['rows'][0]['count']==4
        response=client.put('/api/admin/crm/artifacts/'+created['id'],headers=AUTH,json={'revision':2,'artifact':dashboard()})
        assert response.status_code==200
        assert client.put('/api/admin/crm/artifacts/'+created['id'],headers=AUTH,json={'revision':2,'artifact':updated}).status_code==409
        assert '/api/admin/crm/artifacts' not in client.get('/openapi.json').text
        assert client.post('/api/admin/crm/artifacts',headers={**AUTH,'Origin':'https://untrusted.example'},json={'revision':0,'artifact':dashboard()}).status_code==403
    assert workspace.artifacts(Store(store.root))[0]['revision']==3
    assert len(store.query('SELECT * FROM crm_artifact_changes'))==3


def test_import_tools_parity_and_scoped_extraction_cannot_mutate_owner_data(tmp_path):
    store=Store(tmp_path);tools=catalog(store)
    s=tools['import_job_report'].invoke({'source':{'provider':'owner','reference':'synthetic','title':'Research','report':'Example is a company.'}})
    assert tools['read_job_workspace'].invoke({})['sources'][0]['id']==s['id']
    assert tools['process_job_reports'].invoke({})['requires_owner_review'] is False
    from harness.crm_imports import queue_source
    tid=store.thread('private')['id'];turn=store.enqueue(tid,'synthetic extraction')
    with store.connect() as db:db.execute('UPDATE crm_import_jobs SET turn_id=?',(turn,))
    scoped={t.name:t for t in crm.tools({'mode':'private','store':store,'turn_id':turn,'thread_id':tid})}
    assert 'error' in scoped['write_job_record'].invoke({'record':company('Forbidden')})
    assert 'error' in scoped['save_job_artifact'].invoke({'artifact':dashboard()})
    assert not workspace.artifacts(store)


def test_news_validation_and_dossier_endpoint(tmp_path,monkeypatch):
    module,store=admin_app(tmp_path,monkeypatch)
    ident=add(store,company(news=[{'title':'Example announces platform','url':'https://example.org/news','published_on':'2026-01-01','summary':'Synthetic news'}],logo_url='https://example.org/logo.png'))
    with TestClient(module.app,base_url='https://testserver') as client:
        assert client.get('/api/admin/crm/records/'+ident).status_code==401
        result=client.get('/api/admin/crm/records/'+ident,headers=AUTH)
        assert result.json()['record']['record']['news'][0]['published_on']=='2026-01-01'
    for field in ('logo_url','image_url'):
        with pytest.raises(ValueError):crm.Company(name='Unsafe',**{field:'javascript:alert(1)'})
    with pytest.raises(ValueError):crm.Company(name='Unsafe',news=[{'title':'Bad','url':'https://secret@example.org'}])
    with pytest.raises(ValueError):workspace.Artifact.model_validate({**dashboard(),'rows':[{'unknown':'payload'}]})
    with pytest.raises(ValueError):workspace.Artifact.model_validate({**dashboard(),'html':'<script>bad()</script>'})


def test_new_optional_defaults_do_not_become_owner_locks(tmp_path):
    store=Store(tmp_path);ident=add(store,company())
    # Simulate pre-news schema history on an existing volume.
    with store.connect() as db:
        old=json.loads(db.execute('SELECT body FROM crm_changes WHERE record_id=?',(ident,)).fetchone()['body'])
        for field in ('news','logo_url','image_url'):old.pop(field,None)
        db.execute('UPDATE crm_changes SET body=? WHERE record_id=?',(json.dumps(old),ident))
        db.execute('UPDATE crm_records SET body=? WHERE id=?',(json.dumps(old),ident))
    tools=catalog(store)
    tools['update_job_record'].invoke({'record_id':ident,'revision':1,'changes':{'next_action':'Research team'}})
    from harness.crm_imports import owner_fields
    with store.connect() as db:
        locks=owner_fields(db,ident)
    assert 'next_action' in locks and not {'news','logo_url','image_url'}&locks
