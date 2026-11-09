import json
import uuid
from harness import crm
from harness.crm_imports import consolidate,role,job_urls,canonical_id
from harness.store import Store


def add(store,record):
    crm.initialize(store)
    with store.connect() as db:return crm.write_record(db,record,str(uuid.uuid4()),0,actor='automatic_import',certainty='reported')


def test_role_abbreviations_and_job_specific_urls():
    assert role('VP Eng')==role('VP, Engineering')
    assert role('SE6 Ads Account Foundations JR40610')==role('SWE 6, Ads Account Foundations JR40610')
    assert role('VP Platform Engineering (unnamed Series A employer)')==role('VP Platform Engineering')
    assert not job_urls({'urls':['https://example.org/careers','https://example.org/jobs']})
    assert job_urls({'urls':['https://example.org/positions/12345']})
    assert job_urls({'urls':['https://example.org/jobs?gh_jid=12345']})


def test_existing_short_role_duplicates_merge_without_deleting_originals(tmp_path):
    store=Store(tmp_path);co=add(store,{'kind':'company','name':'Example'})
    first=add(store,{'kind':'application','company_id':co,'title':'MTS, Evals and Post-Training','stage':'applied','applied_on':'2026-01-20'})
    second=add(store,{'kind':'application','company_id':co,'title':'MTS, Evals','stage':'applied','applied_on':'2026-01-20'})
    event=add(store,{'kind':'activity','company_id':co,'application_id':second,'title':'Follow up','stage':'proposed'})
    with store.connect() as db:assert consolidate(db)==1
    records=crm.overview(store)['records']
    assert len([r for r in records if r['kind']=='application'])==1
    assert next(r for r in records if r['id']==event)['record']['application_id']==first
    with store.connect() as db:
        assert canonical_id(db,second)==first
        assert db.execute('SELECT COUNT(*) FROM crm_records WHERE id IN (?,?)',(first,second)).fetchone()[0]==2
        assert consolidate(db)==0


def test_company_label_aliases_rewire_contacts_and_applications(tmp_path):
    store=Store(tmp_path)
    co=add(store,{'kind':'company','name':'Example Recruiting (employer undisclosed)'})
    alias=add(store,{'kind':'company','name':'Example Recruiting'})
    person=add(store,{'kind':'person','company_id':alias,'name':'Pat'})
    app=add(store,{'kind':'application','company_id':alias,'person_id':person,'title':'VP Platform Engineering','stage':'applied','applied_on':'2026-01-20'})
    original=add(store,{'kind':'application','company_id':co,'title':'VP Platform Engineering (unnamed Series A employer)','stage':'applied','applied_on':'2026-01-20'})
    with store.connect() as db:assert consolidate(db)==2
    records=crm.overview(store)['records']
    assert len([r for r in records if r['kind']=='company'])==1
    assert len([r for r in records if r['kind']=='application'])==1
    assert next(r for r in records if r['id']==person)['record']['company_id']==co
    with store.connect() as db:
        assert canonical_id(db,alias)==co
        kept=canonical_id(db,app)
        assert kept==canonical_id(db,original)


def test_distinct_role_submissions_and_different_dates_stay_separate(tmp_path):
    store=Store(tmp_path);co=add(store,{'kind':'company','name':'Example'})
    for title,day in [('Staff SWE GTM AI Engineering','2026-01-20'),('Staff SWE GTM Systems','2026-01-20'),('Staff SWE GTM Systems','2026-02-20')]:
        add(store,{'kind':'application','company_id':co,'title':title,'stage':'applied','applied_on':day,'urls':['https://example.org/careers']})
    with store.connect() as db:assert consolidate(db)==0
    assert len([r for r in crm.overview(store)['records'] if r['kind']=='application'])==3


def test_owner_corrected_duplicate_wins_and_two_owner_edits_are_not_combined(tmp_path):
    store=Store(tmp_path);co=add(store,{'kind':'company','name':'Example'})
    a=add(store,{'kind':'application','company_id':co,'title':'VP Eng','stage':'applied','applied_on':'2026-01-20'})
    b=add(store,{'kind':'application','company_id':co,'title':'VP Engineering','stage':'applied','applied_on':'2026-01-20'})
    current=next(r for r in crm.overview(store)['records'] if r['id']==b)
    with store.connect() as db:crm.write_record(db,{**current['record'],'notes':'Owner correction'},b,current['revision'])
    with store.connect() as db:
        assert consolidate(db)==1
        assert canonical_id(db,a)==b
    assert next(r for r in crm.overview(store)['records'] if r['id']==b)['record']['notes']=='Owner correction'
    c=add(store,{'kind':'company','name':'Example Talent (employer undisclosed)'})
    d=add(store,{'kind':'company','name':'Example Talent'})
    rows={r['id']:r for r in crm.overview(store)['records']}
    with store.connect() as db:
        crm.write_record(db,{**rows[c]['record'],'notes':'First owner correction'},c,rows[c]['revision'])
        crm.write_record(db,{**rows[d]['record'],'notes':'Different owner correction'},d,rows[d]['revision'])
    with store.connect() as db:
        assert consolidate(db)==0
        assert canonical_id(db,c)==c and canonical_id(db,d)==d


def test_stable_job_ids_match_short_and_full_titles_and_imports_still_work_after_merge(tmp_path):
    store=Store(tmp_path);co=add(store,{'kind':'company','name':'Example'})
    a=add(store,{'kind':'application','company_id':co,'title':'Principal SWE, Agentic AI Applications JR123456','stage':'applied','applied_on':'2026-01-20','urls':['https://example.org/jobs/123456']})
    b=add(store,{'kind':'application','company_id':co,'title':'Principal Agentic AI JR123456','stage':'applied','applied_on':'2026-01-20'})
    with store.connect() as db:assert consolidate(db)==1
    sid=crm.save_source(store,crm.Source(provider='muse',reference='after-merge',title='Synthetic report',report='Principal Agentic AI JR123456 was submitted.'))
    result=crm.stage(store,sid,crm.Proposal(candidates=[crm.Candidate(key='role',record=crm.Application(company_id=co,title='Principal Agentic AI JR123456',stage='applied',applied_on='2026-01-20',next_action='Follow up'),excerpt='Principal Agentic AI JR123456 was submitted.')]))
    assert not result['issues']
    records=[r for r in crm.overview(store)['records'] if r['kind']=='application']
    assert len(records)==1 and records[0]['record']['next_action']=='Follow up'


def test_default_import_stage_does_not_reopen_closed_company_or_withdrawn_application(tmp_path):
    store=Store(tmp_path);co=add(store,{'kind':'company','name':'Example','stage':'closed'})
    app=add(store,{'kind':'application','company_id':co,'title':'VP Engineering','stage':'withdrawn','applied_on':'2026-01-20'})
    sid=crm.save_source(store,crm.Source(provider='muse',reference='stage-default',title='Synthetic report',report='Example. VP Engineering.'))
    result=crm.stage(store,sid,crm.Proposal(candidates=[crm.Candidate(key='co',record=crm.Company(name='Example'),excerpt='Example.'),crm.Candidate(key='app',record=crm.Application(company_id='$co',title='VP Engineering',stage='applied',applied_on='2026-01-20'),excerpt='VP Engineering.')]))
    rows={r['id']:r for r in crm.overview(store)['records']}
    assert rows[co]['record']['stage']=='closed' and rows[app]['record']['stage']=='withdrawn'
    assert not result['issues']
