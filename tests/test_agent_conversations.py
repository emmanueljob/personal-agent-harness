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
