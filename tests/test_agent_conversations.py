from fastapi.testclient import TestClient

from harness import grok_connection as grok
from test_grok_connection import setup, OWNER, content, call

ROOT = '/api/admin/conversations'


def test_isolated_threads_and_reopen(tmp_path, monkeypatch):
    module, store = setup(tmp_path, monkeypatch)
    sent = []
    monkeypatch.setattr(grok, 'push_webhook', lambda payload: sent.append(payload) or 200)
    with TestClient(module.app) as client:
        def post(path, **body):
            r = client.post(path, headers=OWNER, json=body)
            assert r.is_success, r.text
            return r.json()

        def reply(task, report):
            claim = dict(task_id=task, claim_id='synthetic-claim')
            instruction = content(call(client, 'acknowledge_assignment', claim))['instruction']
            args = {**claim, 'result': dict(submission_id='synthetic-result', outcome='completed', report=report)}
            assert content(call(client, 'submit_result', args))['accepted']
            assert content(call(client, 'submit_result', args))['duplicate']
            return instruction

        assert client.get(ROOT+'/agents').status_code == 401
        assert client.post(ROOT, json={'agent': 'grokbot'}).status_code == 401
        assert not client.get(ROOT+'/agents', headers=OWNER).json()['agents'][0]['available']
        proof = post('/api/admin/grok/assignments', request_id='synthetic-proof')
        post('/api/admin/grok/assignments/'+proof['id']+'/dispatch')
        reply(proof['id'], 'SYNTHETIC CONNECTION OK')
        for msg in ('unrelated-marker', 'fictional company'):
            ident = post(ROOT, agent='grokbot')['id']
            path = ROOT+'/'+ident
            first = post(path+'/messages', text=msg, request_id='synthetic-first')
            reply(first['turns'][0]['id'], 'reply to '+msg)
        follow = dict(text='Why follow up?', context='corrected company', request_id='synthetic-second')
        second = post(path+'/messages', **follow)
        count = len(sent)
        assert post(path+'/messages', **follow) == second
        assert len(sent) == count
        prompt = reply(second['turns'][1]['id'], 'synthetic final')
        assert prompt == sent[-1]['instruction']
        assert 'reply to fictional company' in prompt and 'corrected company' in prompt
        assert 'unrelated-marker' not in prompt
        reopened = client.get(path, headers=OWNER).json()
        assert [t['reply'] for t in reopened['turns']] == ['reply to fictional company', 'synthetic final']
        assert client.get(path).status_code == 401
        assert client.post(path+'/messages', json=follow).status_code == 401
        monkeypatch.delenv('GROKBOT_MCP_TOKEN')
        assert client.get(path, headers=OWNER).json() == reopened
        assert client.post(path+'/messages', headers=OWNER, json={**follow, 'request_id': 'unavailable-send'}).status_code == 409


def test_home_evidence_resolved_at_send_and_frozen_on_retry(tmp_path, monkeypatch):
    from harness import command_center, conversations
    import json
    module, store = setup(tmp_path, monkeypatch)
    conversations.initialize(store)
    monkeypatch.setattr(conversations, 'availability', lambda *_: '')
    sent, resolved = [], []
    monkeypatch.setattr(grok, 'push_webhook', lambda payload: sent.append(payload) or 200)
    state = {'surface': 'command-center', 'cards': [{'id': 'synthetic-card', 'company': 'Original Company'}]}
    def hydrate(db, selected_id='', previous_ids=None):
        assert db.in_transaction
        resolved.append((selected_id, previous_ids))
        return state
    monkeypatch.setattr(command_center, 'chat_evidence', hydrate, raising=False)
    with TestClient(module.app) as client:
        def post(path, body):
            response = client.post(path, headers=OWNER, json=body)
            assert response.is_success, response.text
            return response.json()
        ident = post(ROOT, {'agent':'grokbot'})['id']
        other = post(ROOT, {'agent':'grokbot'})['id']
        path = ROOT+'/'+ident+'/messages'
        marker = json.dumps({'surface':'command-center', 'card':''})
        body = {'text':'What needs attention?', 'request_id':'synthetic-home-first', 'context':marker}
        first = post(path, body)
        assert resolved[-1] == ('', [])
        task = first['turns'][0]['id']
        grok.acknowledge(store, task, 'synthetic-home-claim')
        grok.submit(store, task, 'synthetic-home-claim', grok.Result(
            submission_id='synthetic-home-result', outcome='completed', report='Historical Original Company'))
        state = {'surface': 'command-center', 'cards': [], 'statuses': [{'id':'synthetic-card', 'status':'hidden'}]}
        assert post(path, body)['turns'][0]['id'] == task
        assert len(resolved) == 1 and len(sent) == 1
        follow = post(path, {**body, 'request_id':'synthetic-home-next'})
        prompt = json.loads(sent[-1]['instruction'].split('\n',1)[1])
        assert prompt['current_evidence'] == state
        assert resolved[-1] == ('', ['synthetic-card'])
        assert 'Original Company' not in json.dumps(prompt['current_evidence'])
        assert 'supersedes historical' in sent[-1]['instruction']
        post(ROOT+'/'+other+'/messages', body)
        assert resolved[-1] == ('', [])
        assert store.query('SELECT context FROM agent_turns WHERE id=?', (follow['turns'][-1]['id'],))[0]['context'] == marker


def test_dispatch_uses_real_home_corrections_and_hidden_status(tmp_path, monkeypatch):
    import json
    from harness import command_center, conversations, correspondence
    from test_command_center import add, company, message

    module, store = setup(tmp_path, monkeypatch)
    conversations.initialize(store)
    monkeypatch.setattr(conversations, 'availability', lambda *_: '')
    sent = []
    monkeypatch.setattr(grok, 'push_webhook', lambda payload: sent.append(payload) or 200)
    add(store, company('Fixture Instruments', domain='example.org'))
    target = add(store, company('Corrected Fixture Studio'))
    receipt = correspondence.ingest(store, message('home-reply', 'inbound'))
    command_center.initialize(store)
    with TestClient(module.app) as client:
        ident = client.post(ROOT, headers=OWNER, json={'agent':'grokbot'}).json()['id']
        for index in range(3):
            context = {'surface':'command-center', 'card':receipt['activity_id'] if index == 0 else ''}
            result = client.post(ROOT+'/'+ident+'/messages', headers=OWNER, json={
                'request_id':f'synthetic-live-state-{index}', 'text':'What needs attention?',
                'context':json.dumps(context)})
            assert result.status_code == 200, result.text
            evidence = json.loads(sent[-1]['instruction'].split('\n',1)[1])['current_evidence']
            if index == 0:
                assert evidence['references'][0]['company'] == 'Fixture Instruments'
                correspondence.link(store, receipt['id'], target)
            elif index == 1:
                ref = next(r for r in evidence['references'] if r['id'] == receipt['activity_id'])
                assert ref['company'] == 'Corrected Fixture Studio'
                with store.connect() as db:
                    db.execute('INSERT INTO crm_email_visibility(item_id,hidden) VALUES (?,1)', (receipt['id'],))
            else:
                ref = next(r for r in evidence['references'] if r['id'] == receipt['activity_id'])
                assert ref == {'id':receipt['activity_id'], 'eligibility':'hidden', 'action':''}
                assert receipt['activity_id'] not in [c['id'] for c in evidence['cards']]
                assert 'Synthetic correspondence' not in json.dumps(evidence)
            task = result.json()['turns'][-1]['id']
            grok.acknowledge(store, task, 'synthetic-state-claim')
            grok.submit(store, task, 'synthetic-state-claim', grok.Result(
                submission_id=f'synthetic-state-result-{index}', outcome='completed', report='Synthetic answer'))


def test_selected_card_snapshot_guard_and_accepted_retry(tmp_path, monkeypatch):
    import json
    from harness import command_center, conversations, crm
    from test_command_center import add, company

    module, store = setup(tmp_path, monkeypatch)
    conversations.initialize(store)
    monkeypatch.setattr(conversations, 'availability', lambda *_: '')
    sent = []
    monkeypatch.setattr(grok, 'push_webhook', lambda payload: sent.append(payload) or 200)
    card_id = add(store, company('Synthetic target', next_action='Follow up'))
    command_center.initialize(store)
    with store.connect() as db:
        card = command_center.resolve_card(db, card_id)
    marker = {'surface':'command-center', 'card':card_id, 'revision':card['revision'],
              'dispositionVersion':card['dispositionVersion']}
    with TestClient(module.app) as client:
        def send(scope, request='synthetic-snapshot', thread=None):
            ident = thread or client.post(ROOT, headers=OWNER, json={'agent':'grokbot'}).json()['id']
            return client.post(ROOT+'/'+ident+'/messages', headers=OWNER, json={
                'request_id':request, 'text':'Explain this suggestion', 'context':json.dumps(scope)}), ident
        response, ident = send(marker)
        assert response.status_code == 200
        task = response.json()['turns'][0]['id']
        grok.acknowledge(store, task, 'synthetic-snapshot-claim')
        grok.submit(store, task, 'synthetic-snapshot-claim', grok.Result(
            submission_id='synthetic-snapshot-result', outcome='completed', report='Synthetic explanation'))
        command_center.disposition(store, card_id, command_center.Disposition(
            revision=card['revision'], disposition_version=card['dispositionVersion'],
            state='later', request_id='synthetic-snapshot-later'))
        count = len(sent)
        retried, _ = send(marker, thread=ident)
        assert retried.status_code == 200 and len(sent) == count
        assert retried.json()['turns'][0]['id'] == task
        assert send(marker, request='synthetic-stale-state')[0].status_code == 409
        with store.connect() as db:
            latest = command_center.resolve_card(db, card_id)
        current = {**marker, 'dispositionVersion':latest['dispositionVersion']}
        assert send({**current, 'revision':999})[0].status_code == 409
        assert send({**current, 'revision':True})[0].status_code == 409
        assert send(current)[0].status_code == 200
        assert send({'surface':'command-center', 'card':card_id})[0].status_code == 200
        assert send({'surface':'command-center', 'card':''})[0].status_code == 200
        with store.connect() as db:
            row = db.execute('SELECT body,revision FROM crm_records WHERE id=?', (card_id,)).fetchone()
            record = json.loads(row['body'])
            record['stage'] = 'closed'
            crm.write_record(db, record, card_id, row['revision'])
        assert send(current)[0].status_code == 409


def test_optional_intent_snapshot_guard_preserves_accepted_retry(tmp_path, monkeypatch):
    import json
    from harness import command_center, conversations

    module, store = setup(tmp_path, monkeypatch)
    conversations.initialize(store)
    monkeypatch.setattr(conversations, 'availability', lambda *_: '')
    sent = []
    monkeypatch.setattr(grok, 'push_webhook', lambda payload: sent.append(payload) or 200)
    current = {'id':'synthetic-group', 'revision':1, 'dispositionVersion':0, 'intentRevision':'a'*64}
    monkeypatch.setattr(command_center, 'resolve_card', lambda *_: current)
    monkeypatch.setattr(command_center, 'chat_evidence', lambda *_args, **_kwargs: {'cards':[], 'references':[]})
    with TestClient(module.app) as client:
        thread = client.post(ROOT, headers=OWNER, json={'agent':'grokbot'}).json()['id']
        path = ROOT+'/'+thread+'/messages'
        scope = {'surface':'command-center','card':'synthetic-group','revision':1,'dispositionVersion':0,
                 'intentRevision':current['intentRevision']}
        body = {'request_id':'synthetic-intent-first','text':'Explain the suggestion','context':json.dumps(scope)}
        first = client.post(path, headers=OWNER, json=body)
        assert first.status_code == 200, first.text
        task = first.json()['turns'][0]['id']
        grok.acknowledge(store, task, 'synthetic-intent-claim')
        grok.submit(store, task, 'synthetic-intent-claim', grok.Result(
            submission_id='synthetic-intent-result', outcome='completed', report='Synthetic response'))
        current['intentRevision'] = 'b'*64
        assert client.post(path, headers=OWNER, json=body).status_code == 200
        assert len(sent) == 1
        stale = client.post(path, headers=OWNER, json={**body,'request_id':'synthetic-intent-stale'})
        assert stale.status_code == 409 and len(sent) == 1
        # Older clients remain valid and get freshly resolved evidence.
        scope.pop('intentRevision')
        compatible = client.post(path, headers=OWNER, json={**body,'request_id':'synthetic-intent-old',
                                                           'context':json.dumps(scope)})
        assert compatible.status_code == 200
