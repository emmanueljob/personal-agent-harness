import json
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from harness import command_actions as actions, command_center, correspondence
from test_admin import AUTH, admin_app
from test_command_center import add, company, message

ROOT = '/api/admin/command-center/cards/'


def test_draft_actual_result_and_concurrent_retries(tmp_path, monkeypatch):
    module, store = admin_app(tmp_path, monkeypatch)
    company_id = add(store, company('Fixture Company'))
    ident = add(store, dict(kind='activity', company_id=company_id, title='Fixture opportunity',
                           next_action='Draft a follow-up'))
    with TestClient(module.app, base_url='https://testserver') as client:
        path = ROOT+ident
        assert client.get(path+'/execution').status_code == 401
        assert client.post(path+'/execute', json={}).status_code == 401
        info = client.get(path+'/execution', headers=AUTH).json()
        assert info['kind'] == 'draft_follow_up' and info['supported']
        body = {k:info[k] for k in ('kind','revision','dispositionVersion')}
        body['request_id'] = 'synthetic-draft-request'
        def execute(_):
            return client.post(path+'/execute', headers=AUTH, json=body)
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(execute, range(4)))
        assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
        results = [r.json()['result'] for r in responses]
        assert all(r == results[0] for r in results)
        draft = results[0]
        assert draft['status'] == 'drafted' and 'Fixture Company' in draft['draftText']
        saved = store.query('SELECT body FROM crm_records WHERE id=?', (draft['draftId'],))
        assert len(saved) == 1
        assert json.loads(saved[0]['body'])['stage'] == 'drafted'
        assert json.loads(saved[0]['body'])['notes'] == draft['draftText']
        new_id = client.post(path+'/execute', headers=AUTH, json={**body, 'request_id':'another-synthetic-request'})
        assert new_id.json()['result']['id'] == draft['id']
        assert client.get(path+'/execution', headers=AUTH).json()['result'] == draft
        assert len(store.query('SELECT * FROM command_action_results')) == 1
        assert client.post(path+'/execute', headers=AUTH,
                           json={**body, 'kind':'check_captured_replies'}).status_code == 409
        other = add(store, dict(kind='activity', company_id=company_id, title='Other', next_action='Follow up'))
        assert client.post(ROOT+other+'/execute', headers=AUTH, json=body).status_code == 409
        assert client.post(ROOT+other+'/execute', headers=AUTH,
                           json={**body, 'request_id':'another-synthetic-request'}).status_code == 409


def test_unsupported_stale_hidden_and_rollback(tmp_path, monkeypatch):
    module, store = admin_app(tmp_path, monkeypatch)
    company_id = add(store, company('Example Instruments', domain='example.org'))
    unsupported = add(store, dict(kind='activity', company_id=company_id, title='Research',
                                 next_action='Attend the in-person interview'))
    ident = add(store, dict(kind='activity', company_id=company_id, title='Draft', next_action='Follow up'))
    with TestClient(module.app, base_url='https://testserver') as client:
        assert not client.get(ROOT+unsupported+'/execution', headers=AUTH).json()['supported']
        info = client.get(ROOT+ident+'/execution', headers=AUTH).json()
        body = {k:info[k] for k in ('kind','revision','dispositionVersion')}
        body['request_id'] = 'synthetic-conflict'
        assert client.post(ROOT+ident+'/execute', headers=AUTH,
                           json={**body,'revision':99}).status_code == 409
        def fail(db, card, action_id):
            actions.draft_follow_up_original(db, card, action_id)
            raise ValueError('synthetic failure after draft creation')
        monkeypatch.setattr(actions, 'draft_follow_up_original', actions.draft_follow_up, raising=False)
        monkeypatch.setattr(actions, 'draft_follow_up', fail)
        before = len(store.query('SELECT id FROM crm_records'))
        failed = client.post(ROOT+ident+'/execute', headers=AUTH, json=body)
        assert failed.status_code == 409
        assert len(store.query('SELECT id FROM crm_records')) == before
        assert not store.query('SELECT * FROM command_action_results')
        receipt = correspondence.ingest(store, message())
        command_center.initialize(store)
        with store.connect() as db:
            db.execute('INSERT INTO crm_email_visibility(item_id,hidden) VALUES (?,1)', (receipt['id'],))
        assert client.get(ROOT+receipt['activity_id']+'/execution', headers=AUTH).status_code == 404


def test_check_captured_reply_scope_and_disposition(tmp_path, monkeypatch):
    module, store = admin_app(tmp_path, monkeypatch)
    add(store, company('Example Instruments', domain='example.org'))
    sent = correspondence.ingest(store, message())
    with TestClient(module.app, base_url='https://testserver') as client:
        path = ROOT+sent['activity_id']
        info = client.get(path+'/execution', headers=AUTH).json()
        assert info['kind'] == 'check_captured_replies'
        body = {k:info[k] for k in ('kind','revision','dispositionVersion')}
        body['request_id'] = 'synthetic-reply-check'
        result = client.post(path+'/execute', headers=AUTH, json=body).json()['result']
        assert result['status'] == 'completed' and result['replies'] == []
        assert 'Captured My Career' in result['scope']
        # A different action cannot be executed after the owner dismisses the card.
        response = client.post(path+'/disposition', headers=AUTH,
            json={'revision':info['revision'], 'disposition_version':info['dispositionVersion'],
                  'state':'dismissed', 'request_id':'synthetic-dismiss'})
        assert response.status_code == 200, response.text
        after = client.get(path+'/execution', headers=AUTH).json()
        assert not after['supported'] and after['result'] == result
        assert client.post(path+'/execute', headers=AUTH,
                           json={**body,'request_id':'synthetic-after-dismiss'}).status_code == 409


def test_review_brief_is_persisted_without_claiming_owner_completion(tmp_path, monkeypatch):
    module, store = admin_app(tmp_path, monkeypatch)
    company_id = add(store, company('Fixture Instruments', domain='example.org'))
    correspondence.ingest(store, message())
    receipt = correspondence.ingest(store, message('review-reply', 'inbound'))
    with TestClient(module.app, base_url='https://testserver') as client:
        path = ROOT+receipt['activity_id']
        info = client.get(path+'/execution', headers=AUTH).json()
        assert info['kind'] == 'prepare_review' and info['label'] == 'Prepare review brief'
        body = {k:info[k] for k in ('kind','revision','dispositionVersion')}
        result = client.post(path+'/execute', headers=AUTH,
            json={**body, 'request_id':'synthetic-review-brief'}).json()['result']
        assert result['status'] == 'completed'
        assert 'Synthetic correspondence' in result['briefText']
        assert client.get(path+'/execution', headers=AUTH).json()['result'] == result
        record = json.loads(store.query('SELECT body FROM crm_records WHERE id=?', (receipt['activity_id'],))[0]['body'])
        assert record['stage'] == 'replied'


def test_company_and_person_cards_target_their_own_entities(tmp_path, monkeypatch):
    module, store = admin_app(tmp_path, monkeypatch)
    co = add(store, company('Fixture own company', next_action='Follow up'))
    person = add(store, dict(kind='person', company_id=co, name='Fixture Contact', next_action='Follow up'))
    with TestClient(module.app, base_url='https://testserver') as client:
        for ident in (co, person):
            info = client.get(ROOT+ident+'/execution', headers=AUTH).json()
            assert info['supported'] and info['kind']=='draft_follow_up'
            payload = {k: info[k] for k in ('kind','revision','dispositionVersion')}
            result = client.post(ROOT+ident+'/execute', headers=AUTH,
                                 json={**payload, 'request_id':'fixture-'+ident}).json()['result']
            saved = store.query('SELECT body FROM crm_records WHERE id=?', (result['draftId'],))[0]
            draft = json.loads(saved['body'])
            assert draft['company_id']==co
            assert draft['person_id']==(person if ident==person else '')


def test_captured_reply_alias_uses_canonical_visibility_and_identity(tmp_path):
    from harness.store import Store
    store = Store(tmp_path)
    co = add(store, company('Fixture alias company', domain='example.org'))
    sent = correspondence.ingest(store, message())
    reply = correspondence.ingest(store, message('alias-reply', 'inbound'))
    canonical = add(store, dict(kind='activity', company_id=co, title='Canonical reply', stage='replied'))
    with store.connect() as db:
        db.execute('INSERT INTO crm_redirects(alias_id,record_id) VALUES (?,?)', (reply['activity_id'], canonical))
        db.execute('INSERT INTO crm_email_visibility(item_id,hidden) VALUES (?,1)', (reply['id'],))
    with store.connect() as db:
        hidden = actions.captured_replies(db, {'receiptId': sent['id']})
        assert hidden['replies']==[]
        db.execute('UPDATE crm_email_visibility SET hidden=0 WHERE item_id=?', (reply['id'],))
    with store.connect() as db:
        visible = actions.captured_replies(db, {'receiptId': sent['id']})
    assert visible['replies'][0]['activity_id']==canonical


def test_consecutive_action_wording_and_saved_outreach(tmp_path, monkeypatch):
    module, store = admin_app(tmp_path, monkeypatch)
    co = add(store, company('Synthetic consecutive company'))
    examples = [('Prepare review brief', 'prepare_review'),
                ('Send an introduction to someone', 'draft_outreach'),
                ('Follow-up with the team tomorrow', 'draft_follow_up')]
    ids = [add(store, dict(kind='activity', company_id=co, title='Suggestion '+str(i), next_action=text))
           for i, (text, _) in enumerate(examples)]
    with TestClient(module.app, base_url='https://testserver') as client:
        for ident, (_, kind) in zip(ids, examples):
            info = client.get(ROOT+ident+'/execution', headers=AUTH).json()
            assert info['supported'] and info['kind']==kind
            body = {k:info[k] for k in ('kind','revision','dispositionVersion','intentRevision')}
            response = client.post(ROOT+ident+'/execute', headers=AUTH,
                                   json={**body,'request_id':'consecutive-'+ident})
            assert response.status_code==200, response.text
            result = response.json()
            assert result['resultCurrent']
            assert client.get(ROOT+ident+'/execution', headers=AUTH).json()['result']['id']==result['result']['id']
            if kind=='draft_outreach':
                draft = json.loads(store.query('SELECT body FROM crm_records WHERE id=?',
                                               (result['result']['draftId'],))[0]['body'])
                assert draft['company_id']==co and draft['stage']=='drafted'
                assert result['result']['status']=='drafted'
                assert 'review' in result['result']['scope']
        assert len(store.query('SELECT * FROM command_action_results'))==3


def test_legacy_migration_preserves_results_requests_and_rolls_back(tmp_path, monkeypatch):
    import pytest
    from harness.store import Store
    store = Store(tmp_path)
    command_center.initialize(store)
    original = json.dumps({'id':'legacy-result','summary':'Synthetic preserved brief'})
    with store.connect() as db:
        db.execute('''CREATE TABLE command_action_results (
            id TEXT PRIMARY KEY, card_id TEXT NOT NULL, kind TEXT NOT NULL,
            revision INTEGER NOT NULL, disposition_version INTEGER NOT NULL,
            request_id TEXT NOT NULL UNIQUE, result TEXT NOT NULL,
            UNIQUE(card_id,kind,revision,disposition_version))''')
        db.execute('''CREATE TABLE command_action_requests (
            request_id TEXT PRIMARY KEY, result_id TEXT NOT NULL REFERENCES command_action_results(id))''')
        db.execute('INSERT INTO command_action_results VALUES (?,?,?,?,?,?,?)',
                   ('legacy-result','missing-card','prepare_review',1,0,'legacy-request',original))
        db.execute('INSERT INTO command_action_requests VALUES (?,?)',('legacy-retry','legacy-result'))
    bind = actions._bind_legacy_intents
    def fail(db):
        raise RuntimeError('Synthetic migration interruption')
    monkeypatch.setattr(actions,'_bind_legacy_intents',fail)
    with pytest.raises(RuntimeError):
        actions.initialize(store)
    with store.connect() as db:
        assert 'intent_revision' not in {r['name'] for r in db.execute('PRAGMA table_info(command_action_results)')}
        assert db.execute('SELECT result FROM command_action_results').fetchone()['result']==original
    monkeypatch.setattr(actions,'_bind_legacy_intents',bind)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: actions.initialize(store),range(4)))
    with store.connect() as db:
        row = db.execute('SELECT * FROM command_action_results').fetchone()
        assert (row['id'],row['result'],row['request_id'],row['intent_revision'])==('legacy-result',original,'legacy-request','')
        assert db.execute('SELECT result_id FROM command_action_requests').fetchone()['result_id']=='legacy-result'
        assert not list(db.execute('PRAGMA foreign_key_check'))
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_action_result_requires_same_kind_and_meaningful_snapshot():
    row = {'kind':'prepare_review','card_id':'old-member','revision':2,
           'disposition_version':0,'intent_revision':'snapshot-one'}
    card = {'id':'representative','revision':3,'dispositionVersion':0,'intentRevision':'snapshot-one'}
    assert actions._current_result(row,card,'prepare_review')
    assert not actions._current_result(row,card,'draft_follow_up')
    assert not actions._current_result(row,{**card,'intentRevision':'snapshot-two'},'prepare_review')


def test_legacy_accepted_request_replays_after_schema_upgrade(tmp_path, monkeypatch):
    module, store = admin_app(tmp_path, monkeypatch)
    co = add(store, company('Synthetic legacy company'))
    ident = add(store, dict(kind='activity',company_id=co,title='Review legacy',next_action='Review the plan'))
    command_center.initialize(store)
    with store.connect() as db:
        card = command_center.resolve_card(db,ident)
        original = {'id':'legacy-accepted-result','kind':'prepare_review','revision':card['revision'],
                    'dispositionVersion':card['dispositionVersion'],'status':'completed',
                    'summary':'Original saved synthetic result','scope':'Captured records only',
                    'briefText':'Original immutable brief'}
        db.execute('''CREATE TABLE command_action_results (
            id TEXT PRIMARY KEY,card_id TEXT NOT NULL,kind TEXT NOT NULL,revision INTEGER NOT NULL,
            disposition_version INTEGER NOT NULL,request_id TEXT NOT NULL UNIQUE,result TEXT NOT NULL,
            UNIQUE(card_id,kind,revision,disposition_version))''')
        db.execute('''CREATE TABLE command_action_requests (
            request_id TEXT PRIMARY KEY,result_id TEXT NOT NULL REFERENCES command_action_results(id))''')
        db.execute('INSERT INTO command_action_results VALUES (?,?,?,?,?,?,?)',
                   (original['id'],ident,'prepare_review',card['revision'],card['dispositionVersion'],
                    'legacy-accepted-request',json.dumps(original)))
        db.execute('INSERT INTO command_action_requests VALUES (?,?)',('legacy-accepted-request',original['id']))
    with TestClient(module.app,base_url='https://testserver') as client:
        replay = client.post(ROOT+ident+'/execute',headers=AUTH,json={
            'kind':'prepare_review','revision':card['revision'],'dispositionVersion':card['dispositionVersion'],
            'request_id':'legacy-accepted-request'})
        assert replay.status_code==200, replay.text
        assert {k:replay.json()['result'][k] for k in original}==original
        assert replay.json()['resultCurrent']
        assert len(store.query('SELECT * FROM command_action_results'))==1
        with store.connect() as db:
            assert json.loads(db.execute('SELECT result FROM command_action_results').fetchone()['result'])==original
            assert not list(db.execute('PRAGMA foreign_key_check'))


def test_grouped_result_reuse_and_new_evidence_guard(tmp_path, monkeypatch):
    from test_card_intents import edit
    module, store = admin_app(tmp_path, monkeypatch)
    co = add(store,company('Synthetic grouped company'))
    first = add(store,dict(kind='activity',company_id=co,title='First review',next_action='Review role',
                          notes='Synthetic captured plan.'))
    second = add(store,dict(kind='activity',company_id=co,title='Second review',next_action='Assess this opportunity',
                           notes='Synthetic captured plan.'))
    with TestClient(module.app,base_url='https://testserver') as client:
        info = client.get(ROOT+second+'/execution',headers=AUTH).json()
        body = {k:info[k] for k in ('kind','revision','dispositionVersion','intentRevision')}
        result = client.post(ROOT+second+'/execute',headers=AUTH,
                             json={**body,'request_id':'grouped-first-request'}).json()['result']
        assert client.get(ROOT+first+'/execution',headers=AUTH).json()['result']['id']==result['id']
        edit(store,second,title='Cosmetic renamed title')
        same = client.get(ROOT+first+'/execution',headers=AUTH).json()
        assert same['resultCurrent'] and same['intentRevision']==info['intentRevision']
        edit(store,second,notes='Synthetic captured plan. Budget approved.')
        changed = client.get(ROOT+first+'/execution',headers=AUTH).json()
        assert changed['revision']==info['revision'] and changed['dispositionVersion']==info['dispositionVersion']
        assert changed['intentRevision']!=info['intentRevision'] and not changed['resultCurrent']
        from harness.home_agent import enrich
        from harness.helper_delegation import initialize as initialize_helpers
        initialize_helpers(store)
        with store.connect() as db:
            evidence=enrich(db,{'cards':[{'id':first}]})['actionEvidence']
            assert evidence[0]['id']==result['id'] and not evidence[0]['resultCurrent']
            assert evidence[0]['intentRevision']==info['intentRevision']
        stale = client.post(ROOT+first+'/execute',headers=AUTH,json={**body,'request_id':'grouped-stale-request'})
        assert stale.status_code==409
        payload = {k:changed[k] for k in ('kind','revision','dispositionVersion','intentRevision')}
        fresh = client.post(ROOT+first+'/execute',headers=AUTH,json={**payload,'request_id':'grouped-new-request'})
        assert fresh.status_code==200 and fresh.json()['result']['id']!=result['id']
        # Accepted old retries return their immutable artifact, not a second action or a new claim.
        replay = client.post(ROOT+second+'/execute',headers=AUTH,json={**body,'request_id':'grouped-first-request'})
        assert replay.status_code==200 and replay.json()['result']['id']==result['id']
        assert not replay.json()['resultCurrent']
        assert len(store.query('SELECT * FROM command_action_results'))==2


def test_legacy_member_result_is_found_without_rewriting_provenance(tmp_path, monkeypatch):
    module, store = admin_app(tmp_path,monkeypatch)
    co = add(store,company('Synthetic historical member company'))
    first = add(store,dict(kind='activity',company_id=co,title='First',next_action='Review role',notes='Same synthetic evidence.'))
    member = add(store,dict(kind='activity',company_id=co,title='Member',next_action='Assess this opportunity',notes='Same synthetic evidence.'))
    actions.initialize(store)
    original = {'id':'member-old-result','kind':'prepare_review','revision':1,'dispositionVersion':0,
                'status':'completed','summary':'Saved on the other member','scope':'Synthetic evidence',
                'briefText':'Original member brief'}
    with store.connect() as db:
        db.execute('INSERT INTO command_action_results VALUES (?,?,?,?,?,?,?,?)',
                   (original['id'],member,'prepare_review',1,0,'member-old-request',json.dumps(original),''))
        db.execute('INSERT INTO command_action_requests VALUES (?,?)',('member-old-request',original['id']))
    with TestClient(module.app,base_url='https://testserver') as client:
        info = client.get(ROOT+first+'/execution',headers=AUTH).json()
        assert info['resultCurrent'] and info['result']['id']==original['id']
        assert info['result']['intentRevision']==info['intentRevision']
        from harness.helper_delegation import initialize as initialize_helpers
        initialize_helpers(store)
        with store.connect() as db:
            row = db.execute('SELECT * FROM command_action_results').fetchone()
            assert row['card_id']==member and json.loads(row['result'])==original
            from harness.home_agent import enrich
            evidence=enrich(db,{'cards':[{'id':first}]})['actionEvidence']
            assert evidence[0]['cardId']==member and evidence[0]['resolvedCardId']==first
            assert evidence[0]['resultCurrent'] and evidence[0]['intentRevision']==info['intentRevision']
        replay = client.post(ROOT+member+'/execute',headers=AUTH,json={
            'kind':'prepare_review','revision':1,'dispositionVersion':0,'request_id':'member-old-request'})
        assert replay.status_code==200 and replay.json()['result']['id']==original['id']


def test_research_action_queues_once_projects_real_reports_and_retries_same_task(tmp_path, monkeypatch):
    from test_helper_delegation import ready
    from harness import grok_connection as grok
    module, store = ready(tmp_path,monkeypatch)
    monkeypatch.setenv('OWNER_STUDIO_TOKEN','synthetic-owner-access')
    co = add(store,company('Synthetic research company'))
    ident = add(store,dict(kind='activity',company_id=co,title='Research team',next_action='Research the engineering team'))
    monkeypatch.delenv('GROKBOT_WEBHOOK_KEY')
    monkeypatch.delenv('MUSE_BRIDGE_TOKEN')
    sent=[]
    monkeypatch.setattr(grok,'push_webhook',lambda payload: sent.append(payload) or 200)
    with TestClient(module.app,base_url='https://testserver') as client:
        info = client.get(ROOT+ident+'/execution',headers=AUTH).json()
        assert info['supported'] and info['kind']=='delegate_research'
        body={k:info[k] for k in ('kind','revision','dispositionVersion','intentRevision')}
        body['request_id']='synthetic-research-action'
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses=list(pool.map(lambda _:client.post(ROOT+ident+'/execute',headers=AUTH,json=body),range(4)))
        assert all(r.status_code==200 for r in responses),[r.text for r in responses]
        result=responses[0].json()['result']
        assert len({r.json()['result']['id'] for r in responses})==1
        job=result['delegation']
        assert job['status']=='unavailable'
        assert len(store.query('SELECT * FROM owner_helper_requests'))==1 and not sent
        task=job['tasks'][0]
        monkeypatch.setenv('GROKBOT_WEBHOOK_KEY','synthetic-webhook-key')
        retried=client.post('/api/admin/helper-requests/'+job['id']+'/retry',headers=AUTH,json={})
        assert retried.status_code==200 and retried.json()['status']=='pending'
        assert retried.json()['tasks'][0]['id']==task['id'] and not sent
        request=json.loads(store.query('SELECT body FROM owner_helper_requests')[0]['body'])
        assert request['kind']=='research' and request['source_kind']=='card_action'
        assert request['card_id']==ident and request['intent_revision']==info['intentRevision']
        assert not any(t['deliveryVerified'] for t in job['tasks'])
        # The transport callback updates the same task; the descriptor reads its real status.
        assert task['helper']=='grokbot'
        grok.dispatch_assignment(store,task['id'])
        assert len(sent)==1
        assert grok.acknowledge(store,task['id'],'synthetic-action-claim')['should_run']
        grok.submit(store,task['id'],'synthetic-action-claim',grok.Result(
            submission_id='synthetic-action-report',outcome='completed',
            report='Synthetic research report; no outreach performed.'))
        polled=client.get(ROOT+ident+'/execution',headers=AUTH).json()['result']['delegation']
        assert polled['status']=='completed' and polled['tasks'][0]['report'].startswith('Synthetic research report')
        assert polled['tasks'][0]['verification']=='helper_report_only' and not polled['tasks'][0]['deliveryVerified']
        assert len(store.query('SELECT * FROM command_action_results'))==1
