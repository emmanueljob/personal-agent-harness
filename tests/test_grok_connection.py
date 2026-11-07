"""Synthetic webhook and real MCP transport tests; no external services."""
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from harness import grok_connection as grok
from test_agent_surfaces import fixture_app

OWNER = {'Authorization': 'Bearer synthetic-owner'}
AGENT = {'Authorization': 'Bearer synthetic-grok-callback', 'Accept': 'application/json, text/event-stream'}


def setup(tmp_path, monkeypatch):
    module, store = fixture_app(tmp_path, monkeypatch)
    monkeypatch.setenv('OWNER_STUDIO_TOKEN', 'synthetic-owner')
    monkeypatch.setenv('GROKBOT_MCP_TOKEN', 'synthetic-grok-callback')
    monkeypatch.setenv('GROKBOT_WEBHOOK_URL', 'https://hooks.example.test/trigger')
    monkeypatch.setenv('GROKBOT_WEBHOOK_KEY', 'synthetic-webhook-key')
    return module, store


def rpc(client, method, params=None, headers=None):
    response = client.post('/grok-mcp', headers=headers or AGENT,
        json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})
    assert response.status_code == 200, response.text
    return response.json()['result']


def call(client, name, arguments):
    return rpc(client, 'tools/call', {'name': name, 'arguments': arguments})


def content(result):
    assert not result.get('isError'), result
    return json.loads(result['content'][0]['text'])


def test_real_mcp_roundtrip_private_boundaries_and_duplicates(tmp_path, monkeypatch):
    module, store = setup(tmp_path, monkeypatch)
    sent = []
    monkeypatch.setattr(grok, 'push_webhook', lambda payload: sent.append(payload) or 200)
    with TestClient(module.app) as client:
        assert client.get('/api/admin/grok').status_code == 401
        assert client.post('/grok-mcp', json={}).status_code == 401
        assert client.post('/grok-mcp', headers=OWNER, json={}).status_code == 401
        info = client.get('/api/admin/grok', headers=OWNER)
        assert info.headers['cache-control'] == 'private, no-store'
        assert 'synthetic-grok-callback' not in info.text
        assert 'synthetic-webhook-key' not in info.text
        assert '/grok-mcp' not in client.get('/api/public/openapi.json').text
        task = client.post('/api/admin/grok/assignments', headers=OWNER,
                           json={'request_id': 'synthetic-request', 'action': 'connection_test'}).json()
        ident = task['id']
        assert client.post('/api/admin/grok/assignments', headers=OWNER,
            json={'request_id': 'synthetic-request'}).json()['id'] == ident
        assert client.post('/api/admin/grok/assignments', headers=OWNER,
            json={'request_id': 'import-before-proof', 'action': 'export_job_search'}).status_code == 409
        initialized = rpc(client, 'initialize', {'protocolVersion': '2025-03-26', 'capabilities': {},
                                               'clientInfo': {'name': 'Synthetic Grok', 'version': '1'}})
        assert initialized['serverInfo']['name'] == 'Grok Bot assignment callbacks'
        names = {t['name'] for t in rpc(client, 'tools/list')['tools']}
        assert names == {'acknowledge_assignment', 'submit_result'}
        assert call(client, 'acknowledge_assignment', {'task_id': ident, 'claim_id': 'synthetic-claim'}).get('isError')
        response = client.post(f'/api/admin/grok/assignments/{ident}/dispatch', headers=OWNER)
        assert response.json()['status'] == 'delivered'
        assert sent[0]['task_id'] == ident and sent[0]['action'] == 'connection_test'
        assert 'synthetic-webhook-key' not in json.dumps(sent)
        assert 'synthetic-grok-callback' not in json.dumps(sent)
        claim = {'task_id': ident, 'claim_id': 'synthetic-claim'}
        assert content(call(client, 'acknowledge_assignment', claim))['should_run']
        assert not content(call(client, 'acknowledge_assignment', claim))['should_run']
        assert not content(call(client, 'acknowledge_assignment', {**claim, 'claim_id': 'another-claim'}))['should_run']
        result = {'submission_id': 'synthetic-result', 'outcome': 'completed', 'report': 'SYNTHETIC CONNECTION OK'}
        assert call(client, 'submit_result', {**claim, 'claim_id': 'wrong-claim', 'result': result}).get('isError')
        assert content(call(client, 'submit_result', {**claim, 'result': result}))['accepted']
        assert content(call(client, 'submit_result', {**claim, 'result': result}))['duplicate']
        assert call(client, 'submit_result', {**claim, 'result': {**result, 'report': 'overwrite'}}).get('isError')
        assert client.post(f'/api/admin/grok/assignments/{ident}/dispatch', headers=OWNER).status_code == 409
        final = client.get('/api/admin/grok', headers=OWNER).json()['tasks'][0]
        assert final['status'] == 'completed' and final['result']['report'] == result['report']
        assert final['result']['complete_export'] is False
        imported = client.post('/api/admin/grok/assignments', headers=OWNER,
            json={'request_id': 'synthetic-import', 'action': 'export_job_search'})
        assert imported.status_code == 201
        import_id = imported.json()['id']
        client.post(f'/api/admin/grok/assignments/{import_id}/dispatch', headers=OWNER)
        claim2 = {'task_id': import_id, 'claim_id': 'synthetic-import-claim'}
        assert content(call(client, 'acknowledge_assignment', claim2))['should_run']
        report = {'submission_id': 'synthetic-import-result', 'outcome': 'completed',
                  'report': 'Example Company: draft introduction only; not sent. Source: synthetic agent notes. '+('界'*18000)}
        assert content(call(client, 'submit_result', {**claim2, 'result': report}))['accepted']
        assert grok.get_task(store, import_id)['result']['report'] == report['report']
        assert 'SYNTHETIC CONNECTION OK' not in client.get('/api/public/profile').text
        monkeypatch.delenv('GROKBOT_MCP_TOKEN')
        assert client.post('/grok-mcp', headers=AGENT, json={}).status_code == 404
    # The report survives a new Store, without changing canonical brain files.
    from harness.store import Store
    assert grok.get_task(Store(store.root), ident)['result']['report'] == result['report']


def test_uncertain_delivery_retry_and_callback_race(tmp_path, monkeypatch):
    module, store = setup(tmp_path, monkeypatch)
    with TestClient(module.app) as client:
        task = client.post('/api/admin/grok/assignments', headers=OWNER, json={'request_id': 'uncertain-request'}).json()
        def fail(payload):
            raise httpx.ReadTimeout('synthetic secret must not leak')
        monkeypatch.setattr(grok, 'push_webhook', fail)
        path = f"/api/admin/grok/assignments/{task['id']}/dispatch"
        response = client.post(path, headers=OWNER)
        assert response.json()['status'] == 'delivery_unknown'
        assert 'must not leak' not in response.text
        def callback_before_response(payload):
            assert payload['task_id'] == task['id']
            assert grok.acknowledge(store, task['id'], 'race-claim')['should_run']
            grok.submit(store, task['id'], 'race-claim', grok.Result(submission_id='race-result', outcome='blocked', report='Synthetic blocker'))
            return 200
        monkeypatch.setattr(grok, 'push_webhook', callback_before_response)
        response = client.post(path, headers=OWNER).json()
        assert response['status'] == 'blocked' and response['attempts'] == 2


def test_atomic_claims_and_validation(tmp_path, monkeypatch):
    module, store = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(grok, 'push_webhook', lambda _: 200)
    with TestClient(module.app) as client:
        task = client.post('/api/admin/grok/assignments', headers=OWNER, json={'request_id': 'concurrent-request'}).json()
        client.post(f"/api/admin/grok/assignments/{task['id']}/dispatch", headers=OWNER)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda n: grok.acknowledge(store, task['id'], f'claim-number-{n}'), range(4)))
        assert sum(r['should_run'] for r in results) == 1
        huge = call(client, 'submit_result', {'task_id': task['id'], 'claim_id': 'claim-number-0',
            'result': {'submission_id': 'oversize-report', 'outcome': 'completed', 'report': 'x'*21000}})
        assert huge.get('isError')
        assert client.post('/grok-mcp', headers=AGENT, content='x'*129000).status_code == 413
        assert client.post('/api/admin/grok/assignments', headers={**OWNER, 'Origin': 'https://evil.example'}, json={'request_id': 'crosssite-request'}).status_code == 403


def test_webhook_security_and_exact_protocol(monkeypatch):
    monkeypatch.setenv('GROKBOT_WEBHOOK_KEY', 'synthetic-webhook-key')
    for url in ['http://example.test/hook', 'https://user:pass@example.test/hook', 'https://example.test:8443/hook']:
        monkeypatch.setenv('GROKBOT_WEBHOOK_URL', url)
        with pytest.raises(ValueError):
            grok.push_webhook({})
    monkeypatch.setenv('GROKBOT_WEBHOOK_URL', 'https://hooks.example.test/trigger')
    monkeypatch.setattr(grok, 'public_address', lambda _: '93.184.216.34')
    original = httpx.Client
    def receiver(request):
        assert request.url.host == '93.184.216.34'
        assert request.headers['host'] == 'hooks.example.test'
        assert request.headers['authorization'] == 'Bearer synthetic-webhook-key'
        assert request.extensions['sni_hostname'] == 'hooks.example.test'
        assert json.loads(request.content) == {'task_id': 'synthetic'}
        return httpx.Response(302, headers={'Location': 'https://evil.example'})
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(receiver), **kwargs))
    assert grok.push_webhook({'task_id': 'synthetic'}) == 302
