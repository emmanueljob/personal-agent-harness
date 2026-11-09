import json
from pathlib import Path

import pytest
from harness import crm
from harness.crm_imports import resume,ranges,job_for_turn
from harness.store import Store


def source(store,ref,report='Example is a target. Pat is a contact. Applied to Lead.',day=None):
    return crm.save_source(store,crm.Source(provider='muse',reference=ref,title='Synthetic report',report=report,observed_on=day))


def candidate(key,record,excerpt='Example is a target.'):
    return crm.Candidate(key=key,record=crm.RECORD.validate_python(record),excerpt=excerpt)


def stage(store,sid,*rows):return crm.stage(store,sid,crm.Proposal(candidates=list(rows)))


def test_newer_source_merges_clear_identities_and_does_not_regress_application(tmp_path):
    store=Store(tmp_path)
    older=source(store,'old',day='2026-01-01')
    newer=source(store,'new',day='2026-02-01')
    co={'kind':'company','name':'Example','priority':'high'}
    stage(store,older,candidate('co',co),candidate('app',{'kind':'application','company_id':'$co','title':'Backend GTM Innovation','stage':'considering'},'Applied to Lead.'))
    stage(store,newer,candidate('co',{'kind':'company','name':'example','domain':'example.org','stage':'pursuing'}),candidate('app',{'kind':'application','company_id':'$co','title':'Backend SWE, GTM Innovation','stage':'applied','applied_on':'2026-01-20'},'Applied to Lead.'))
    records=crm.overview(store)['records']
    assert len(records)==2
    company=next(r for r in records if r['kind']=='company')
    app=next(r for r in records if r['kind']=='application')
    assert company['record']['priority']=='high' and company['record']['domain']=='example.org'
    assert app['record']['stage']=='applied' and app['record']['applied_on']=='2026-01-20'
    stage(store,older,candidate('old_again',{'kind':'application','company_id':company['id'],'title':'Backend GTM Innovation','stage':'considering'},'Applied to Lead.'))
    assert crm.overview(store)['records'][0]['record']['kind'] in ('application','company')
    assert next(r for r in crm.overview(store)['records'] if r['kind']=='application')['record']['stage']=='applied'


def test_owner_edits_and_explicit_clears_survive_future_imports(tmp_path):
    store=Store(tmp_path);sid=source(store,'initial')
    stage(store,sid,candidate('co',{'kind':'company','name':'Example'}),candidate('pat',{'kind':'person','company_id':'$co','name':'Pat','email':'pat@example.org'},'Pat is a contact.'))
    person=next(r for r in crm.overview(store)['records'] if r['kind']=='person')
    revised={**person['record'],'email':'','role':'Owner corrected role','notes':'Owner note'}
    with store.connect() as db:crm.write_record(db,revised,person['id'],person['revision'])
    second=source(store,'second')
    stage(store,second,candidate('co',{'kind':'company','name':'Example','domain':'example.org'}),candidate('pat',{'kind':'person','company_id':'$co','name':'Pat','email':'pat@example.org','role':'Stale role','notes':'Imported note','next_action':'Follow up'},'Pat is a contact.'))
    people=[r for r in crm.overview(store)['records'] if r['kind']=='person']
    assert len(people)==1
    value=people[0]['record']
    assert value['email']=='' and value['role']=='Owner corrected role' and value['notes']=='Owner note'
    assert value['next_action']=='Follow up'


def test_bad_record_does_not_hold_up_other_records_and_late_dependency_imports(tmp_path):
    store=Store(tmp_path);sid=source(store,'one')
    result=stage(store,sid,candidate('bad',{'kind':'company','name':'Invented'},'Not present'),candidate('pat',{'kind':'person','name':'Pat','company_id':'$co'},'Pat is a contact.'))
    assert result['errors'] and result['issues'] and not result['requires_owner_review']
    result=stage(store,sid,candidate('co',{'kind':'company','name':'Example'}))
    assert result['imported']==2 and not result['issues']
    assert len(crm.overview(store)['records'])==2


def test_duplicate_new_source_receipts_enqueue_once_and_cover_original(tmp_path,monkeypatch):
    monkeypatch.setattr('harness.models.settings',lambda *a:{'configured':True})
    store=Store(tmp_path);text=('Example is a target.\n'*400)+'Last line.'
    sid=source(store,'long',text)
    assert source(store,'long',text)==sid
    state=crm.overview(store);jobs=state['import_jobs']
    assert len(jobs)>1 and len(store.query('SELECT * FROM turns'))==len(jobs)
    assert ''.join(text[a:b] for a,b in ranges(text))==text
    assert all(j['end_offset']-j['offset']<=2400 for j in jobs)
    assert all(r['mode']=='private' for r in store.query('SELECT mode FROM threads'))
    assert all(store.runtime_for(j['turn_id']) in ('legacy','deepagents') for j in jobs)
    assert resume(store)['extraction_jobs_queued']==0
    assert len(store.query('SELECT * FROM turns'))==len(jobs)


def test_waiting_jobs_resume_when_model_is_configured_and_retries_are_bounded(tmp_path,monkeypatch):
    monkeypatch.setattr('harness.models.settings',lambda *a:{'configured':False})
    store=Store(tmp_path);source(store,'waiting')
    assert crm.overview(store)['import_jobs'][0]['status']=='waiting_for_model'
    monkeypatch.setattr('harness.models.settings',lambda *a:{'configured':True})
    assert resume(store)['extraction_jobs_queued']==1
    tid=crm.overview(store)['import_jobs'][0]['turn_id']
    for n in range(3):
        with store.connect() as db:db.execute("UPDATE turns SET status='failed' WHERE id=?",(tid,))
        assert resume(store)['extraction_jobs_queued']==(1 if n<2 else 0)
    assert store.query('SELECT generation FROM dispatch_attempts WHERE turn_id=?',(tid,))[0]['generation']==2


def test_import_tool_chunks_are_source_bound_and_excerpts_fail_softly(tmp_path,monkeypatch):
    monkeypatch.setattr('harness.models.settings',lambda *a:{'configured':True})
    store=Store(tmp_path);sid=source(store,'bound');other=source(store,'other')
    tid=next(j['turn_id'] for j in crm.overview(store)['import_jobs'] if j['source_id']==sid)
    tools={t.name:t for t in crm.tools({'mode':'private','store':store,'turn_id':tid})}
    assert tools['read_job_import'].invoke({'source_id':other})['error']
    assert tools['request_grok_job_report'].invoke({})['error']
    assert tools['propose_job_import'].invoke({'source_id':other,'candidates':[candidate('co',{'kind':'company','name':'Example'}).model_dump(mode='json')]})['error']
    value=tools['read_job_import'].invoke({'source_id':sid,'offset':0,'limit':12})
    assert value['report']=='Example is a' and value['next_offset']==12
    result=tools['propose_job_import'].invoke({'source_id':sid,'candidates':[candidate('bad',{'kind':'company','name':'Unknown'},'Bad excerpt').model_dump(mode='json'),candidate('co',{'kind':'company','name':'Example'}).model_dump(mode='json')]})
    assert result['imported']==1 and result['errors']


def test_automatic_snapshot_uses_only_import_context_without_brain_retrieval(tmp_path,monkeypatch):
    monkeypatch.setenv('MODEL_PROVIDER','openai');monkeypatch.setenv('OPENAI_API_KEY','synthetic-not-used')
    store=Store(tmp_path);source(store,'snapshot')
    tid=crm.overview(store)['import_jobs'][0]['turn_id']
    def unavailable(*a,**k):raise AssertionError('Importer should not read unrelated brain material')
    for name in ('profile','voice','perspectives','search','public_portfolio'):monkeypatch.setattr(store.brain,name,unavailable)
    from harness.agent import prepare_turn,build_system_prompt
    mode,snapshot=prepare_turn(store,store.query('SELECT * FROM turns WHERE id=?',(tid,))[0])
    assert mode=='private' and snapshot['config']['job_search_import']
    assert not snapshot['config']['web_enabled'] and snapshot['config']['max_output_tokens']==8192
    assert snapshot['knowledge']==[] and snapshot['perspectives']==[]
    assert 'Never ask the owner to accept' in build_system_prompt(snapshot,mode)


def test_invalid_model_fields_return_feedback_instead_of_failing_the_activity(tmp_path):
    store=Store(tmp_path);sid=source(store,'invalid-model-fields')
    tool=next(t for t in crm.tools({'mode':'private','store':store,'turn_id':'test'}) if t.name=='propose_job_import')
    response=tool.invoke({'source_id':sid,'candidates':[{'key':'co','record':{'kind':'company','name':'Example','follow_up':'unknown'},'excerpt':'Example is a target.'}]})
    assert json.loads(response)['fields'] and not crm.overview(store)['records']


def test_terminal_failure_event_retries_only_import_jobs_without_owner_action(tmp_path,monkeypatch):
    monkeypatch.setattr('harness.models.settings',lambda *a:{'configured':True})
    store=Store(tmp_path);source(store,'retry-events')
    ident=crm.overview(store)['import_jobs'][0]['turn_id']
    from harness.crm_imports import retry_failed_job
    ordinary=store.enqueue(store.thread('private')['id'],'Ordinary private question')
    for i in range(3):
        with store.connect() as db:db.execute("UPDATE turns SET status='failed' WHERE id=?",(ident,))
        assert retry_failed_job(store,ident)==(i<2)
    with store.connect() as db:db.execute("UPDATE turns SET status='failed' WHERE id=?",(ordinary,))
    assert not retry_failed_job(store,ordinary)
    assert store.query('SELECT status FROM turns WHERE id=?',(ordinary,))[0]['status']=='failed'


def test_owner_renaming_an_entity_does_not_recreate_its_old_imported_identity(tmp_path):
    store=Store(tmp_path);sid=source(store,'old-name')
    stage(store,sid,candidate('co',{'kind':'company','name':'Example'}))
    row=crm.overview(store)['records'][0]
    with store.connect() as db:crm.write_record(db,{**row['record'],'name':'Owner corrected company'},row['id'],row['revision'])
    next_source=source(store,'new-report-old-name')
    stage(store,next_source,candidate('co',{'kind':'company','name':'Example','next_action':'Research team'}))
    records=crm.overview(store)['records']
    assert len(records)==1 and records[0]['record']['name']=='Owner corrected company'
    assert records[0]['record']['next_action']=='Research team'
