import asyncio
import json

from fastapi.testclient import TestClient

from harness import muse_companion_worker as worker
from harness.muse_transport import MUSE, MuseTransport, marker
from test_agent_conversations import ROOT
from test_grok_connection import setup, OWNER

BRIDGE = '/api/muse-companion'
TOKEN = {'Authorization': 'Bearer synthetic-bridge'}


def test_hosted_owner_and_paired_companion(tmp_path, monkeypatch):
    module, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setenv('MUSE_BRIDGE_TOKEN', 'synthetic-bridge')
    events, sent = [], []

    async def upstream(name, args):
        if name == 'connection_status':
            return {'connected': True, 'logged_in': True}
        if name == 'send_message':
            assert args['recipient'] == MUSE
            sent.append(args['text'])
            if len(sent) == 3:
                raise TimeoutError()
            return {'id': str(len(sent)), 'chat_jid': MUSE}
        return {'messages': events}

    with TestClient(module.app) as client:
        def owner(path, body):
            response = client.post(path, headers=OWNER, json=body)
            assert response.is_success, response.text
            return response.json()

        async def bridge(path, body):
            response = client.post(BRIDGE+path, headers=TOKEN, json=body)
            assert response.is_success, response.text
            return response.json()

        def sync():
            asyncio.run(worker.synchronize(MuseTransport(upstream), bridge))

        assert client.post(BRIDGE+'/claim', headers=OWNER, json={'ready': True}).status_code == 401
        assert client.get(ROOT+'/agents', headers=TOKEN).status_code == 401
        sync()
        path = ROOT+'/'+owner(ROOT, {'agent': 'muse'})['id']
        for idx, text in enumerate(['Where?', 'Why?', 'Uncertain?'], 1):
            body = {'text': text, 'request_id': f'synthetic-message-{idx}'}
            turn = owner(path+'/messages', body)['turns'][-1]
            assert turn['status'] == 'queued'
            assert owner(path+'/messages', body)['turns'][-1] == turn
            sync()
            assert len(sent) == idx
            assert marker(turn['id']) in sent[-1]
            if idx == 2:
                assert 'Synthetic answer 1' in sent[-1]
            events.append(dict(id=str(idx), from_me=False, sender=MUSE, chat_jid=MUSE, text='unrelated'))
            sync()
            latest = client.get(path, headers=OWNER).json()['turns'][-1]
            assert latest['status'] == ('delivery_unknown' if idx == 3 else 'delivered')
            assert latest['reply'] is None
            answer = f'Synthetic answer {idx}'
            event = {**events[-1], 'text': marker(turn['id'])+'\n'+answer}
            events.extend([event, event])
            sync()
            transcript = client.get(path, headers=OWNER).json()
            assert len(transcript['turns']) == idx
            assert transcript['turns'][-1]['reply'] == answer
            assert len(sent) == idx
            assert 'unrelated' not in json.dumps(transcript)
        asyncio.run(bridge('/claim', {'ready': False}))
        assert not client.get(ROOT+'/agents', headers=OWNER).json()['agents'][1]['available']
        assert client.get(path, headers=OWNER).json() == transcript
        assert client.post(path+'/messages', headers=OWNER, json={**body, 'request_id':'unavailable-turn'}).status_code == 409


def test_companion_mcp_client(tmp_path, monkeypatch):
    import httpx2
    from mcp.server import MCPServer

    server = MCPServer('Synthetic companion')
    @server.tool()
    def connection_status() -> dict:
        return {'connected': True, 'logged_in': True}
    app = server.streamable_http_app(stateless_http=True, json_response=True)
    monkeypatch.setenv('WHATSAPP_MUSE_HOME', str(tmp_path))
    config = tmp_path/'connection.json'
    config.write_text(json.dumps({'url': 'http://127.0.0.1:18888/mcp', 'token': 'synthetic-'*4}))
    config.chmod(0o600)
    original = httpx2.AsyncClient
    def local_client(**kwargs):
        assert kwargs['trust_env'] is False
        return original(transport=httpx2.ASGITransport(app=app), **kwargs)
    monkeypatch.setattr(worker.httpx2, 'AsyncClient', local_client)
    async def scenario():
        async with server.session_manager.run():
            assert await MuseTransport(worker.upstream).ready()
    asyncio.run(scenario())
