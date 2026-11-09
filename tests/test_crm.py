import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from harness import crm
from harness.store import Store
from test_admin import admin_app, AUTH


def company(name='Example', **kwargs):
    return {'kind':'company','name':name,**kwargs}


def add(store,record):
    crm.initialize(store)
    import uuid
    with store.connect() as db:
        return crm.write_record(db,record,str(uuid.uuid4()),0)


def source(store,report='Example is a company. Pat is a contact. A role is proposed, not applied.'):
    return crm.save_source(store,crm.Source(provider='muse',reference='fixture-1',title='Synthetic report',report=report))


def proposal(*candidates):
    return crm.Proposal.model_validate({'candidates':list(candidates)})


def candidate(key,record,excerpt='Example is a company.'):
    return {'key':key,'record':record,'excerpt':excerpt}


def test_private_hosted_crm_boundary_and_persistence(tmp_path,monkeypatch):
    module,store=admin_app(tmp_path,monkeypatch)
    with TestClient(module.app,base_url='https://testserver') as client:
        assert client.get('/api/admin/crm').status_code==401
        assert client.post('/api/admin/crm/records',json={}).status_code==401
        assert client.get('/api/admin/crm',headers=AUTH).headers['cache-control']=='private, no-store'
        row=client.post('/api/admin/crm/records',headers=AUTH,json={'revision':0,'record':company(notes='PRIVATE CRM CANARY')})
        assert row.status_code==200
        ident=row.json()['id']
        assert 'PRIVATE CRM CANARY' in client.get('/api/admin/crm',headers=AUTH).text
        assert 'PRIVATE CRM CANARY' not in client.get('/').text
        assert '/api/admin/crm' not in client.get('/api/public/openapi.json').text
        assert '/api/admin/crm' not in client.get('/openapi.json').text
        assert client.put('/api/admin/crm/records/'+ident,headers=AUTH,json={'revision':0,'record':company('stale')}).status_code==409
        assert client.post('/api/admin/crm/records',headers={**AUTH,'Origin':'https://untrusted.example'},json={'revision':0,'record':company('csrf')}).status_code==403
    assert crm.overview(Store(store.root))['records'][0]['record']['notes']=='PRIVATE CRM CANARY'


def test_automatic_dependency_import_is_idempotent_and_keeps_evidence(tmp_path):
    store=Store(tmp_path);sid=source(store)
    p=proposal(candidate('co',company(domain='example.org')),candidate('pat',{'kind':'person','company_id':'$co','name':'Pat'},'Pat is a contact.'),candidate('role',{'kind':'application','company_id':'$co','person_id':'$pat','title':'Lead','stage':'considering'},'A role is proposed, not applied.'))
    first=crm.stage(store,sid,p)
    assert first['imported']==3 and not first['requires_owner_review']
    assert crm.stage(store,sid,p)['imported']==0
    records=crm.overview(store)['records']
    assert len(records)==3 and len(crm.overview(store)['changes'])==3
    app=next(r for r in records if r['kind']=='application')
    assert app['record']['stage']=='considering' and app['record']['applied_on'] is None
    assert app['record']['person_id']==next(r['id'] for r in records if r['kind']=='person')
    assert app['source_id']==sid and app['evidence']=='A role is proposed, not applied.'
    result=crm.stage(store,sid,proposal(candidate('made-up',company('Invented'),'Fabricated evidence')))
    assert result['errors'] and len(crm.overview(store)['records'])==3
    with pytest.raises(ValueError,match='different content'):
        crm.save_source(store,crm.Source(provider='muse',reference='fixture-1',title='Changed',report='different'))


def test_automatic_merge_fills_gaps_and_preserves_owner_corrections(tmp_path):
    store=Store(tmp_path);ident=add(store,company('Owner name',domain='example.org',notes='Owner correction',priority='high'))
    sid=source(store)
    result=crm.stage(store,sid,proposal(candidate('co',company('Imported name',domain='example.org',notes='Other notes',next_action='Research team'))))
    assert result['imported']==1
    row=crm.overview(store)['records'][0]
    assert row['id']==ident and row['record']['name']=='Owner name'
    assert row['record']['notes']=='Owner correction' and row['record']['priority']=='high'
    assert row['record']['next_action']=='Research team' and row['revision']==2
    assert crm.overview(store)['changes'][0]['source_id']==sid


def test_relationship_validation_duplicate_edits_and_bad_urls(tmp_path):
    store=Store(tmp_path);a=add(store,company('A',domain='a.example'));b=add(store,company('B',domain='b.example'))
    p=add(store,{'kind':'person','name':'Pat','company_id':a})
    with pytest.raises(ValueError,match='same company'):
        add(store,{'kind':'application','title':'Role','company_id':b,'person_id':p})
    with pytest.raises(ValueError,match='same company'):
        add(store,{'kind':'person','name':'Orphan','company_id':'missing'})
    with store.connect() as db:
        with pytest.raises(ValueError,match='matching record'):
            crm.write_record(db,company('A',domain='a.example'),b,1)
    with pytest.raises(ValueError):
        crm.RECORD.validate_python(company(urls=['javascript:alert(1)']))
    with pytest.raises(ValueError):
        crm.RECORD.validate_python(company(domain='https://example.org/path'))


def test_concurrent_accept_creates_one_record(tmp_path):
    store=Store(tmp_path);sid=source(store)
    crm.stage(store,sid,proposal(candidate('co',company())))
    with ThreadPoolExecutor(max_workers=2) as executor:
        outputs=list(executor.map(lambda _:crm.accept(store,sid,[crm.Choice(key='co')]),range(2)))
    assert outputs[0]==outputs[1] and len(crm.overview(store)['records'])==1


def test_private_tools_stage_only_and_report_collection(tmp_path,monkeypatch):
    store=Store(tmp_path)
    assert not crm.tools({'mode':'public'})
    catalog={t.name:t for t in crm.tools({'mode':'private','store':store,'turn_id':'synthetic-turn-0001'})}
    assert 'accept_job_import' not in catalog
    sid=source(store)
    assert not catalog['propose_job_import'].invoke({'source_id':sid,'candidates':[candidate('co',company())]})['requires_owner_review']
    assert len(crm.overview(store)['records'])==1
    from harness.grok_connection import initialize
    initialize(store)
    result=json.dumps({'outcome':'completed','report':'Existing synthetic company research','complete_export':False,'submission_id':'fixture-123'})
    with store.connect() as db:
        db.execute("INSERT INTO grok_assignments(id,request_id,action,status,created,updated,result) VALUES ('fixture','fixture','export_job_search','completed',1,1,?)",(result,))
    assert catalog['collect_job_reports'].invoke({})=={'reports_available':1}
    assert catalog['collect_job_reports'].invoke({})=={'reports_available':1}
    assert len(crm.overview(store)['sources'])==2
    assert all(not s['complete'] for s in crm.overview(store)['sources'])
    # Request helper uses one stable assignment per turn; a tool retry cannot re-send.
    import harness.grok_connection as grok
    sent=[]
    monkeypatch.setattr(grok,'create_assignment',lambda *args:{'id':'assignment','attempts':len(sent),'status':'delivered' if sent else 'queued','delivery':None})
    monkeypatch.setattr(grok,'dispatch_assignment',lambda *args:sent.append('sent') or {'id':'assignment','status':'delivered','delivery':'accepted'})
    catalog['request_grok_job_report'].invoke({});catalog['request_grok_job_report'].invoke({})
    assert len(sent)==1


def test_hosted_coordinator_queues_private_durable_turn_without_leaking(tmp_path,monkeypatch):
    module,store=admin_app(tmp_path,monkeypatch)
    monkeypatch.setattr('harness.models.settings',lambda choice='default':{'configured':True})
    with TestClient(module.app,base_url='https://testserver') as client:
        body={'request_id':'crm-chat-fixture-001','prompt':'Which companies need follow-up?'}
        first=client.post('/api/admin/crm/chat',headers=AUTH,json=body)
        assert first.status_code==200
        assert client.post('/api/admin/crm/chat',headers=AUTH,json=body).json()==first.json()
        assert client.post('/api/admin/crm/chat',headers=AUTH,json={**body,'prompt':'different'}).status_code==409
        tid=store.query('SELECT thread_id FROM crm_workspace')[0]['thread_id']
        assert store.query('SELECT mode FROM threads WHERE id=?',(tid,))[0]['mode']=='private'
        assert client.get('/api/threads/'+tid).status_code in (401,404)
        assert len(client.get('/api/admin/crm',headers=AUTH).json()['turns'])==1
        assert store.runtime_for(first.json()['id']) in ('legacy','deepagents')
        assert crm.coordinator_prompt(store,tid)==crm.GUIDANCE
        assert crm.coordinator_prompt(store,store.thread('public')['id'])==''


def test_coordinator_identity_is_frozen_and_retry_is_workspace_scoped(tmp_path,monkeypatch):
    module,store=admin_app(tmp_path,monkeypatch)
    monkeypatch.setenv('MODEL_PROVIDER','openai')
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-test-value-not-used')
    monkeypatch.setenv('WEB_SEARCH_PROVIDER','disabled')
    with TestClient(module.app,base_url='https://testserver') as client:
        body={'request_id':'coordinator-context-fixture','prompt':'Read my accepted CRM records.'}
        ident=client.post('/api/admin/crm/chat',headers=AUTH,json=body).json()['id']
        assert client.post('/api/admin/crm/chat',headers=AUTH,json={**body,'model_choice':'openai'}).status_code==409
        turn=store.query('SELECT * FROM turns WHERE id=?',(ident,))[0]
        from harness.agent import prepare_turn,build_system_prompt
        mode,snapshot=prepare_turn(store,turn)
        assert mode=='private' and snapshot['config']['job_search_workspace']
        assert 'job-search coordinator' in build_system_prompt(snapshot,mode)
        assert crm.GUIDANCE in build_system_prompt(snapshot,mode)
        assert 'job-search coordinator' not in build_system_prompt({**snapshot,'config':{**snapshot['config'],'job_search_workspace':False}},'public')
        unrelated=store.enqueue(store.thread('private')['id'],'Synthetic unrelated turn')
        with store.connect() as db:
            db.execute("UPDATE turns SET status='failed' WHERE id IN (?,?)",(ident,unrelated))
        assert client.post('/api/admin/crm/chat/'+unrelated+'/retry',headers=AUTH).status_code==404
        assert client.post('/api/admin/crm/chat/'+ident+'/retry',headers=AUTH).status_code==200
        assert client.post('/api/admin/crm/chat/'+ident+'/retry',headers=AUTH).status_code==409
        assert store.query('SELECT generation FROM dispatch_attempts WHERE turn_id=?',(ident,))[0]['generation']==1


def test_long_report_intake_keeps_original_and_is_still_bounded(tmp_path,monkeypatch):
    module,_=admin_app(tmp_path,monkeypatch)
    with TestClient(module.app,base_url='https://testserver') as client:
        report='Synthetic research. '*2500
        payload={'provider':'muse','reference':'long-fixture','title':'Long report','report':report}
        assert client.post('/api/admin/crm/sources',headers=AUTH,json=payload).status_code==200
        assert client.get('/api/admin/crm',headers=AUTH).json()['sources'][0]['report']==report
        assert client.post('/api/admin/crm/sources',headers=AUTH,json={**payload,'report':'x'*130000}).status_code==413


def test_grok_callback_atomically_enters_imports_without_accepting_records(tmp_path):
    from harness.grok_connection import submit, Result
    store=Store(tmp_path)
    from harness.grok_connection import initialize
    initialize(store)
    with store.connect() as db:
        db.execute("INSERT INTO grok_assignments(id,request_id,action,status,created,updated,attempts,claim_id) VALUES ('export-fixture','export-fixture','export_job_search','acknowledged',1,1,1,'claim-fixture')")
    result=Result(submission_id='submission-fixture',outcome='completed',report='Synthetic company report, not a complete memory export.',complete_export=False)
    assert submit(store,'export-fixture','claim-fixture',result)['accepted']
    assert submit(store,'export-fixture','claim-fixture',result)['duplicate']
    data=crm.overview(store)
    assert len(data['sources'])==1 and not data['records']
    assert data['sources'][0]['report']==result.report
    assert data['sources'][0]['candidates']==[] and not data['sources'][0]['complete']
    assert crm.collect_grok(store)=={'reports_available':1}
    assert len(crm.overview(store)['sources'])==1
