import json

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from harness import agent, command_actions, command_center, conversations, runner
from test_admin import admin_app, AUTH
from test_command_center import add, company

ROOT = '/api/admin/conversations'


def test_home_runs_actual_agent_with_scoped_action_evidence(tmp_path, monkeypatch):
    module, store = admin_app(tmp_path, monkeypatch)
    monkeypatch.setenv('MODEL_PROVIDER', 'ollama')
    target = add(store, company('Synthetic target', next_action='Follow up'))
    other = add(store, company('UNRELATED PRIVATE CANARY', next_action='Follow up'))
    command_actions.initialize(store)
    with store.connect() as db:
        card = command_center.resolve_card(db, target)
    result = command_actions.execute(store, target, command_actions.Execute(
        kind='draft_follow_up', revision=card['revision'], dispositionVersion=card['dispositionVersion'],
        request_id='synthetic-draft-action'))['result']
    conversations.initialize(store)
    # No retrieval/profile/plugin path should be needed for this frozen, scoped Home turn.
    monkeypatch.setattr(store, 'profile', lambda *_: (_ for _ in ()).throw(AssertionError('profile read')))
    monkeypatch.setattr(store.brain, 'search', lambda *_: (_ for _ in ()).throw(AssertionError('brain search')))
    model = FakeListChatModel(responses=['A starter draft is saved, and sending remains a separate action.',
                                        'We prepared the draft earlier; it has not established delivery.'])
    monkeypatch.setattr(agent, 'local_model', lambda *_: model)
    with TestClient(module.app, base_url='https://testserver') as client:
        assert client.post(ROOT, json={'agent':'owner'}).status_code == 401
        ident = client.post(ROOT, headers=AUTH, json={'agent':'owner'}).json()['id']
        path = ROOT+'/'+ident
        marker = json.dumps({'surface':'command-center', 'card':target,
                             'revision':card['revision'], 'dispositionVersion':card['dispositionVersion']})
        body = {'request_id':'synthetic-owner-first', 'text':'Already done?', 'context':marker}
        first = client.post(path+'/messages', headers=AUTH, json=body)
        assert first.status_code == 200, first.text
        task = first.json()['turns'][0]['id']
        frozen = json.loads(store.query('SELECT body FROM execution_context WHERE turn_id=?', (task,))[0]['body'])
        assert frozen['config']['home_agent'] is True
        evidence = json.loads(frozen['messages'][0]['content'].split('\n', 1)[-1])['current_evidence']
        assert evidence['actionEvidence'][0]['id'] == result['id']
        assert evidence['actionEvidence'][0]['status'] == 'drafted'
        assert evidence['actionEvidence'][0]['draftCurrentState']['stage'] == 'drafted'
        assert 'UNRELATED PRIVATE CANARY' not in json.dumps(frozen)
        assert evidence['helperEvidence'] == []
        assert 'No live' in evidence['helperConsultation']
        assert agent.plugins.tools({'home_agent':True}) == []
        runner.run_turn(store, task)
        assert client.get(path, headers=AUTH).json()['turns'][0]['reply'].startswith('A starter draft')
        assert client.post(path+'/messages', headers=AUTH, json=body).json()['turns'][0]['id'] == task
        assert len(store.query('SELECT * FROM turns WHERE thread_id=?', (ident,))) == 1
        follow = client.post(path+'/messages', headers=AUTH, json={
            **body, 'request_id':'synthetic-owner-next', 'text':'What remains?', 'context':''})
        assert follow.status_code == 200, follow.text
        task2 = follow.json()['turns'][-1]['id']
        prompt = store.query('SELECT prompt FROM agent_turns WHERE id=?',(task2,))[0]['prompt']
        assert 'A starter draft is saved' in prompt
        runner.run_turn(store, task2)
        assert len(client.get(path, headers=AUTH).json()['turns']) == 2
        assert not store.query('SELECT * FROM grok_assignments WHERE action=\'conversation\'')
        assert client.get(path).status_code == 401


def test_snapshot_guard_and_enqueue_rollback_for_owner(tmp_path, monkeypatch):
    module, store = admin_app(tmp_path, monkeypatch)
    monkeypatch.setenv('MODEL_PROVIDER','ollama')
    target = add(store, company('Fixture target', next_action='Follow up'))
    with TestClient(module.app, base_url='https://testserver') as client:
        ident = client.post(ROOT, headers=AUTH, json={'agent':'owner'}).json()['id']
        path = ROOT+'/'+ident+'/messages'
        body = {'request_id':'synthetic-owner-stale', 'text':'Already done?',
                'context':json.dumps({'surface':'command-center','card':target,'revision':999,'dispositionVersion':0})}
        assert client.post(path, headers=AUTH, json=body).status_code == 409
        assert not store.query('SELECT * FROM agent_turns')
        assert not store.query('SELECT * FROM turns WHERE thread_id=?',(ident,))


def test_action_completion_and_existing_helper_evidence_are_scoped(tmp_path, monkeypatch):
    from harness.home_agent import enrich
    module, store = admin_app(tmp_path, monkeypatch)
    conversations.initialize(store)
    command_actions.initialize(store)
    target = add(store, company('Fixture target', next_action='Follow up'))
    other = add(store, company('Unrelated company', next_action='Follow up'))
    for index, card_id in enumerate((target, other)):
        helper_thread = f'helper-thread-{index}'
        helper_turn = f'helper-turn-{index}'
        with store.connect() as db:
            db.execute('INSERT INTO agent_threads VALUES (?,?,?)', (helper_thread, 'grokbot', index))
            db.execute('INSERT INTO agent_turns VALUES (?,?,?,?,?,?,?)',
                (helper_turn, helper_thread, f'helper-request-{index}', 'Synthetic helper question',
                 json.dumps({'card':card_id}), '{}', index))
            db.execute("""INSERT INTO grok_assignments(id,request_id,action,status,created,updated,result)
                VALUES (?,?,'conversation','completed',0,0,?)""",
                (helper_turn, helper_turn, json.dumps({'report': 'Scoped helper evidence' if index == 0 else 'UNRELATED HELPER CANARY'})))
    command_center.initialize(store)
    with store.connect() as db:
        card = command_center.resolve_card(db, target)
    command_center.disposition(store, target, command_center.Disposition(
        revision=card['revision'], disposition_version=card['dispositionVersion'],
        state='done', request_id='synthetic-done-card'))
    with store.connect() as db:
        current = command_center.chat_evidence(db, selected_id=target)
        enriched = enrich(db, {**current, 'selectedCard':target})
    reference = next(r for r in enriched['references'] if r.get('requestedId') == target)
    assert reference['disposition'] == 'done'
    assert enriched['helperEvidence'][0]['reply'] == 'Scoped helper evidence'
    assert len(enriched['helperEvidence']) == 1
    assert enriched['actionEvidence'] == []  # Owner completion is distinct from an executor result.
    assert 'UNRELATED HELPER CANARY' not in json.dumps(enriched)


def test_failed_owner_turn_retries_frozen_model_context(tmp_path, monkeypatch):
    module, store = admin_app(tmp_path, monkeypatch)
    monkeypatch.setenv('MODEL_PROVIDER','ollama')
    with TestClient(module.app, base_url='https://testserver') as client:
        ident = client.post(ROOT, headers=AUTH, json={'agent':'owner'}).json()['id']
        path = ROOT+'/'+ident
        response = client.post(path+'/messages', headers=AUTH, json={
            'request_id':'synthetic-owner-failure', 'text':'What remains?', 'context':''})
        task = response.json()['turns'][0]['id']
        original = store.query('SELECT body FROM execution_context WHERE turn_id=?', (task,))[0]['body']
        with store.connect() as db:
            db.execute("UPDATE turns SET status='failed',error='Synthetic failure' WHERE id=?", (task,))
        assert client.post(path+'/turns/'+task+'/retry').status_code == 401
        assert client.post(path+'/turns/'+task+'/retry', headers=AUTH).status_code == 200
        assert client.get(path, headers=AUTH).json()['turns'][0]['status'] == 'queued'
        assert store.query('SELECT body FROM execution_context WHERE turn_id=?', (task,))[0]['body'] == original
        assert client.post(path+'/turns/'+task+'/retry', headers=AUTH).status_code == 409
