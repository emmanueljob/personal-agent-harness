"""Run on the paired Mac with python -m harness.muse_companion_worker."""
import asyncio
import json
import os
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import httpx2

from .muse_transport import MuseTransport
from .paths import application_home


async def upstream(name, arguments):
    home = Path(os.getenv('WHATSAPP_MUSE_HOME', str(application_home()/'integrations'/'whatsapp-muse')))
    path = home/'connection.json'
    if path.resolve().is_relative_to(Path(__file__).resolve().parents[1]) or path.is_symlink():
        raise ValueError('Use an external private companion configuration.')
    metadata = path.stat()
    if metadata.st_mode & 0o077 or not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise ValueError('Use an owner-only companion configuration.')
    config = json.loads(path.read_text())
    url = urlsplit(config['url'])
    if (url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port or url.path != '/mcp'
            or url.username or url.password or url.query or url.fragment or len(config['token']) < 32):
        raise ValueError('Use the authenticated loopback companion.')
    async with httpx2.AsyncClient(headers={'Authorization': 'Bearer '+config['token']}, trust_env=False) as http:
        http.follow_redirects = False
        async with streamable_http_client(config['url'], http_client=http, max_sse_event_size=128000) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
                if result.is_error:
                    raise RuntimeError('Muse companion rejected the operation.')
                if result.structured_content is not None:
                    return result.structured_content
                return json.loads(next(c.text for c in result.content if c.type == 'text'))


async def synchronize(transport, post):
    try:
        ready = await asyncio.wait_for(transport.ready(), 10)
    except Exception:
        ready = False
    claim = await post('/claim', {'ready': ready})
    pending = claim['pending']
    turn = claim['send']
    if turn:
        try:
            await asyncio.wait_for(transport.send(turn['id'], turn['prompt']), 25)
            delivered = True
        except Exception:
            delivered = False
        await post('/turns/'+turn['id'], {'delivered': delivered})
        pending.append(turn['id'])
    for ident, answer in (await asyncio.wait_for(transport.replies(pending), 15)).items():
        await post('/turns/'+ident, {'reply': answer})


async def run():
    origin = os.environ['MUSE_APP_ORIGIN'].rstrip('/')
    url = urlsplit(origin)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.path or url.fragment or url.query):
        raise ValueError('Set MUSE_APP_ORIGIN to the private app HTTPS origin.')
    token = os.environ['MUSE_BRIDGE_TOKEN']
    if not token:
        raise ValueError('Configure the separate bridge token.')
    async with httpx.AsyncClient(base_url=origin+'/api/muse-companion/', trust_env=False,
                                 headers={'Authorization': 'Bearer '+token}, follow_redirects=False) as client:
        async def post(path, body):
            response = await client.post(path.lstrip('/'), json=body)
            response.raise_for_status()
            return response.json()
        while True:
            try:
                await synchronize(MuseTransport(upstream), post)
            except Exception:
                print('Muse sync incomplete. Check the app and companion connections.', file=sys.stderr)
            await asyncio.sleep(10)


if __name__ == '__main__':
    asyncio.run(run())
