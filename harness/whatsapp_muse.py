"""Opt-in, local Muse-only facade over a separately installed WhatsApp MCP.

The upstream companion session is private runtime tooling, never a web deploy.
Only a fixed synthetic message and its correlated reply are exposed here.
"""
import asyncio
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp_types import ToolAnnotations

from .paths import application_home

MUSE_JID = '1807055946647697@bot'


def directory():
    root = Path(os.getenv('WHATSAPP_MUSE_HOME', str(application_home()/'integrations'/'whatsapp-muse'))).expanduser()
    # A separate integration home is required even when a caller overrides it.
    checkout = Path(__file__).resolve().parents[1]
    if root.resolve().is_relative_to(checkout):
        raise ValueError('WhatsApp session data must live outside the source checkout.')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root


def configuration():
    path = directory()/'connection.json'
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError('The private WhatsApp connection file must have mode 600.')
    value = json.loads(path.read_text())
    url = urlsplit(value['url'])
    if url.scheme != 'http' or url.hostname != '127.0.0.1' or url.path != '/mcp' or not url.port or url.username or url.password or url.query or url.fragment:
        raise ValueError('Use the authenticated loopback WhatsApp MCP endpoint.')
    if not isinstance(value.get('token'), str) or len(value['token']) < 32:
        raise ValueError('A separate private WhatsApp MCP token is required.')
    return value


async def upstream(name, arguments):
    config = configuration()
    # The SDK owns transport compatibility. Never forward the bearer on redirects.
    async with create_mcp_http_client(headers={'Authorization': 'Bearer '+config['token']}) as client:
        client.follow_redirects = False
        async with streamable_http_client(config['url'], http_client=client, max_sse_event_size=128000) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.call_tool(name, arguments)
                if response.is_error:
                    # Keep diagnostics locally; provider messages never enter a model response.
                    detail = '\n'.join(c.text for c in response.content if c.type == 'text')[:2000]
                    detail = detail.replace(config['token'], '[REDACTED]')
                    log_path = directory()/'facade.log'
                    if not log_path.is_symlink():
                        fd = os.open(log_path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
                        with os.fdopen(fd, 'a') as log:
                            log.write(json.dumps({'time': time.time(), 'tool': name, 'error': detail})+'\n')
                    raise RuntimeError('WhatsApp MCP rejected this operation.')
                if response.structured_content is not None:
                    return response.structured_content
                return json.loads(next(c.text for c in response.content if c.type == 'text'))


def database():
    path = directory()/'proof.sqlite'
    if path.is_symlink():
        raise ValueError('Proof database cannot be a symbolic link.')
    # Create privately before SQLite opens it; do not rely on the caller's umask.
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    path.chmod(0o600)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS tests(request_id TEXT PRIMARY KEY, status TEXT NOT NULL, created REAL NOT NULL, message_id TEXT)')
    return db


def validate_id(request_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', request_id):
        raise ValueError('Use a request ID of 8–64 letters, digits, hyphens or underscores.')


async def connection_status():
    value = await upstream('connection_status', {})
    return {k: bool(value.get(k)) for k in ('connected', 'logged_in', 'pairing')}


async def send_test(request_id):
    validate_id(request_id)
    # Check before reserving. An unpaired local instance never consumes an ID.
    status = await connection_status()
    if not status['connected'] or not status['logged_in']:
        raise ValueError('Pair WhatsApp and link Muse on your primary phone before sending.')
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT * FROM tests WHERE request_id=?', (request_id,)).fetchone()
        if old:
            return {'request_id': request_id, 'status': old['status'], 'message_id': old['message_id'], 'duplicate': True}
        db.execute('INSERT INTO tests VALUES (?,\'sending\',?,NULL)', (request_id, time.time()))
    text = ('Connection test from my personal job-search harness. Reply exactly HARNESS_WHATSAPP_OK_'+request_id+
            '. Do not access private sources, submit applications, contact anyone, publish or spend. This is only a messaging test.')
    try:
        response = await upstream('send_message', {'recipient': MUSE_JID, 'text': text})
        message_id = response.get('id')
        if not isinstance(message_id, str) or not message_id or response.get('chat_jid') != MUSE_JID:
            raise RuntimeError('No matching WhatsApp send receipt was returned.')
    except BaseException:
        # Even a cancellation can happen after the send. Never automatically resend.
        with database() as db:
            db.execute("UPDATE tests SET status='unknown' WHERE request_id=?", (request_id,))
        raise
    with database() as db:
        db.execute("UPDATE tests SET status='sent',message_id=? WHERE request_id=?", (message_id, request_id))
    return {'request_id': request_id, 'status': 'sent', 'message_id': message_id, 'duplicate': False}


async def read_test_reply(request_id):
    validate_id(request_id)
    with database() as db:
        row = db.execute('SELECT * FROM tests WHERE request_id=?', (request_id,)).fetchone()
    if not row:
        raise ValueError('No connection test exists with that request ID.')
    # One explicit read of the archive populated by upstream message events.
    # There is no background polling or agent heartbeat in this facade.
    value = await upstream('list_messages', {'chat_jid': MUSE_JID, 'limit': 20})
    expected = 'HARNESS_WHATSAPP_OK_'+request_id
    replies = [{'id': m.get('id'), 'text': m.get('text', '')[:2000], 'timestamp': m.get('timestamp')}
               for m in value.get('messages', [])
               if m.get('chat_jid') == MUSE_JID and m.get('sender') == MUSE_JID
               and m.get('from_me') is False and m.get('text', '').strip() == expected]
    return {'request_id': request_id, 'verified': bool(replies), 'replies': replies}


def mcp_server():
    server = MCPServer('Muse-only WhatsApp connection proof', instructions='Only synthetic Muse connection tests. No general WhatsApp access, outreach, applications, arbitrary recipients or private-history export. Read a test reply only when explicitly requested; no polling loops.')
    read = ToolAnnotations(read_only_hint=True, idempotent_hint=True)

    async def safe(fn, *args):
        try:
            async with asyncio.timeout(40):
                return await fn(*args)
        except ValueError as error:
            raise ToolError(str(error))
        except Exception:
            raise ToolError('The local WhatsApp operation could not complete. Inspect its private runtime log; do not blindly resend.')

    @server.tool(annotations=read)
    async def whatsapp_muse_status() -> dict:
        """Read paired/connected state only. Does not reveal the account identity or history."""
        return await safe(connection_status)

    @server.tool()
    async def send_muse_connection_test(request_id: str) -> dict:
        """Send a fixed synthetic message ONLY to Muse, as requested by the owner. Reuse request_id after uncertainty to avoid sending twice."""
        return await safe(send_test, request_id)

    @server.tool(annotations=read)
    async def read_muse_connection_test(request_id: str) -> dict:
        """Read only the exact nonce reply for a previously sent test. One read, no automatic checks or wider history."""
        return await safe(read_test_reply, request_id)
    return server


def whatsapp_muse_tools(ctx):
    """Expose the connection proof only to explicitly enabled local private turns."""
    if ctx['mode'] != 'private' or os.getenv('WHATSAPP_MUSE_ENABLED', '').lower() != 'true':
        return []
    from langchain.tools import tool

    async def safe(fn, *args):
        try:
            async with asyncio.timeout(40):
                return await fn(*args)
        except Exception:
            return {'error': 'Local Muse WhatsApp operation could not complete. Inspect its private log; do not blindly resend.'}

    @tool
    async def whatsapp_muse_status() -> dict:
        """Check only whether the local WhatsApp companion is paired and connected."""
        return await safe(connection_status)

    @tool
    async def send_muse_connection_test(request_id: str) -> dict:
        """Send one fixed synthetic WhatsApp test to Muse only when the owner asks. Reuse request_id after uncertainty."""
        return await safe(send_test, request_id)

    @tool
    async def read_muse_connection_test(request_id: str) -> dict:
        """Read only the exact Muse nonce reply to a previous test, on request. Do not repeatedly poll."""
        return await safe(read_test_reply, request_id)
    return [whatsapp_muse_status, send_muse_connection_test, read_muse_connection_test]


if __name__ == '__main__':
    import logging
    for name in ('httpx', 'httpx2', 'mcp'):
        logging.getLogger(name).setLevel(logging.WARNING)
    mcp_server().run(transport='stdio')
