"""Synthetic local WhatsApp boundary tests. Never pair or send to a provider."""
import asyncio
import json
from pathlib import Path

import pytest
from harness import whatsapp_muse as muse
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('WHATSAPP_MUSE_HOME', str(tmp_path/'wa'))
    return muse.directory()


def test_only_muse_send_and_nonce_reply_are_exposed(home, monkeypatch):
    calls = []
    async def upstream(name, arguments):
        calls.append((name, arguments))
        if name == 'connection_status':
            return {'connected': True, 'logged_in': True, 'jid': 'private-account-identity'}
        if name == 'send_message':
            assert arguments['recipient'] == muse.MUSE_JID
            assert 'HARNESS_WHATSAPP_OK_synthetic-test-01' in arguments['text']
            return {'id': 'synthetic-message', 'chat_jid': muse.MUSE_JID}
        return {'messages': [
            {'chat_jid': muse.MUSE_JID, 'sender': muse.MUSE_JID, 'from_me': False, 'text': 'HARNESS_WHATSAPP_OK_synthetic-test-01', 'id': 'reply'},
            {'chat_jid': muse.MUSE_JID, 'sender': 'stranger@bot', 'from_me': False, 'text': 'HARNESS_WHATSAPP_OK_synthetic-test-01'},
            {'chat_jid': 'unrelated@s.whatsapp.net', 'sender': muse.MUSE_JID, 'from_me': False, 'text': 'HARNESS_WHATSAPP_OK_synthetic-test-01'},
            {'chat_jid': muse.MUSE_JID, 'sender': muse.MUSE_JID, 'from_me': True, 'text': 'HARNESS_WHATSAPP_OK_synthetic-test-01'},
            {'chat_jid': muse.MUSE_JID, 'sender': muse.MUSE_JID, 'from_me': False, 'text': 'unrelated private content'},
        ]}
    monkeypatch.setattr(muse, 'upstream', upstream)
    async def check():
        assert 'jid' not in await muse.connection_status()
        assert (await muse.send_test('synthetic-test-01'))['status'] == 'sent'
        assert (await muse.send_test('synthetic-test-01'))['duplicate']
        result = await muse.read_test_reply('synthetic-test-01')
        assert result['verified'] and len(result['replies']) == 1
        assert 'unrelated private content' not in json.dumps(result)
    asyncio.run(check())
    assert sum(name == 'send_message' for name, _ in calls) == 1
    assert calls[-1] == ('list_messages', {'chat_jid': muse.MUSE_JID, 'limit': 20})
    assert (home/'proof.sqlite').stat().st_mode & 0o777 == 0o600


def test_uncertain_send_is_not_repeated_and_unpaired_does_not_send(home, monkeypatch):
    calls = []
    async def upstream(name, arguments):
        calls.append(name)
        if name == 'connection_status':
            return {'connected': True, 'logged_in': True}
        raise TimeoutError('synthetic upstream failure')
    monkeypatch.setattr(muse, 'upstream', upstream)
    async def check():
        with pytest.raises(TimeoutError): await muse.send_test('uncertain-test-01')
        result = await muse.send_test('uncertain-test-01')
        assert result['status'] == 'unknown' and result['duplicate']
        with pytest.raises(ValueError): await muse.send_test('injected\nmessage')
        with pytest.raises(ValueError): await muse.read_test_reply('does-not-exist')
    asyncio.run(check())
    assert calls.count('send_message') == 1
    async def unpaired(*_): return {'connected': True, 'logged_in': False}
    monkeypatch.setattr(muse, 'upstream', unpaired)
    with pytest.raises(ValueError): asyncio.run(muse.send_test('unpaired-test-01'))


def test_local_config_and_private_permissions(home):
    config = home/'connection.json'
    for url in ['https://remote.example/mcp', 'http://localhost:8000/mcp', 'http://127.0.0.1:8080/other', 'http://user:pass@127.0.0.1:8080/mcp']:
        config.write_text(json.dumps({'url': url, 'token': 'synthetic-token-'*4})); config.chmod(0o600)
        with pytest.raises(ValueError): muse.configuration()
    config.write_text(json.dumps({'url': 'http://127.0.0.1:18888/mcp', 'token': 'synthetic-token-'*4}));config.chmod(0o644)
    with pytest.raises(ValueError): muse.configuration()
    config.chmod(0o600)
    assert muse.configuration()['url'] == 'http://127.0.0.1:18888/mcp'


def test_actual_stdio_tool_catalog_is_narrow(home):
    import os, sys
    async def check():
        params = StdioServerParameters(command=sys.executable, args=['-m', 'harness.whatsapp_muse'], env={**os.environ, 'WHATSAPP_MUSE_HOME': str(home)})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                names = {t.name for t in (await client.list_tools()).tools}
                assert names == {'whatsapp_muse_status', 'send_muse_connection_test', 'read_muse_connection_test'}
                result = await client.call_tool('send_muse_connection_test', {'request_id': 'invalid'})
                assert result.is_error
    asyncio.run(check())


def test_private_opt_in_only(home, monkeypatch):
    monkeypatch.delenv('WHATSAPP_MUSE_ENABLED', raising=False)
    assert muse.whatsapp_muse_tools({'mode': 'private'}) == []
    monkeypatch.setenv('WHATSAPP_MUSE_ENABLED', 'true')
    assert muse.whatsapp_muse_tools({'mode': 'public'}) == []
    assert {t.name for t in muse.whatsapp_muse_tools({'mode': 'private'})} == {'whatsapp_muse_status', 'send_muse_connection_test', 'read_muse_connection_test'}


def test_real_http_mcp_transport(home, monkeypatch):
    import httpx2
    from mcp.server import MCPServer
    config = home/'connection.json'
    config.write_text(json.dumps({'url': 'http://127.0.0.1:18888/mcp', 'token': 'synthetic-token-'*4}));config.chmod(0o600)
    server = MCPServer('Synthetic WhatsApp')
    @server.tool()
    def connection_status() -> dict:
        return {'connected': True, 'logged_in': False, 'pairing': True}
    @server.tool()
    def synthetic_rejection() -> dict:
        from mcp.server.mcpserver.exceptions import ToolError
        raise ToolError('synthetic rejection '+('synthetic-token-'*4))
    app = server.streamable_http_app(stateless_http=True, json_response=True)
    def fake_http(headers):
        assert headers['Authorization'] == 'Bearer '+('synthetic-token-'*4)
        return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), headers=headers)
    monkeypatch.setattr(muse, 'create_mcp_http_client', fake_http)
    async def check():
        async with server.session_manager.run():
            assert await muse.connection_status() == {'connected': True, 'logged_in': False, 'pairing': True}
            with pytest.raises(Exception):
                await muse.upstream('synthetic_rejection', {})
            log = (home/'facade.log').read_text()
            assert '[REDACTED]' in log and ('synthetic-token-'*4) not in log
    asyncio.run(check())
