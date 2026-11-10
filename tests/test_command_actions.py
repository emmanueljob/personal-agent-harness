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
                                 next_action='Send an introduction to someone'))
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
