"""Local setup and MCP smoke client. Never deploys a WhatsApp session to Fly."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

# Supports direct execution from an isolated source checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.whatsapp_muse import configuration, directory, MUSE_JID


async def call_tool(name, arguments):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=sys.executable, args=['-m', 'harness.whatsapp_muse'],
        env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            result = await client.call_tool(name, arguments)
            if result.is_error:
                raise RuntimeError('Muse-only MCP operation failed; inspect the private runtime log. No automatic resend.')
            return result.structured_content or json.loads(next(c.text for c in result.content if c.type == 'text'))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    init = sub.add_parser('init', help='Create private configuration without connecting an account')
    init.add_argument('--binary', required=True, type=Path)
    sub.add_parser('serve', help='Run the separately installed companion MCP on loopback')
    sub.add_parser('pair', help='Save the current companion pairing QR privately')
    sub.add_parser('status', help='Inspect connection state using the narrow stdio MCP facade')
    for action in ('test', 'reply'):
        cmd = sub.add_parser(action)
        cmd.add_argument('--request-id', required=True)
    args = parser.parse_args()
    root = directory()
    if args.action == 'init':
        binary = args.binary.expanduser().resolve()
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ValueError('Supply the reviewed companion MCP executable.')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        value = {'url': f'http://127.0.0.1:{port}/mcp', 'token': secrets.token_urlsafe(32), 'binary': str(binary)}
        with (root/'connection.json').open('x') as f:
            json.dump(value, f)
        (root/'connection.json').chmod(0o600)
        print('Private configuration created. Account not paired; no messages sent.')
        return
    config = configuration()
    if args.action == 'serve':
        from urllib.parse import urlsplit
        port = urlsplit(config['url']).port
        env = {**os.environ, 'WHATSAPP_MCP_TOKEN': config['token'], 'WHATSAPP_MCP_ADDR': f'127.0.0.1:{port}',
               'DATABASE_URL': 'sqlite://'+str(root/'whatsapp.sqlite'), 'WHATSAPP_MCP_LOG_LEVEL': 'error',
               'WHATSAPP_MCP_ALLOW_RECIPIENTS': MUSE_JID, 'WHATSAPP_MCP_ENABLE_GROUP_ADMIN': 'false',
               'WHATSAPP_MCP_ALLOW_STATUS': 'false', 'WHATSAPP_MCP_ALLOW_INVITE_LINKS': 'false',
               'WHATSAPP_MCP_READ_ONLY': 'false', 'WHATSAPP_MCP_MEDIA_DIR': '', 'WHATSAPP_MCP_PUBLIC_URL': '',
               'WHATSAPP_MCP_EXPIRE_DISAPPEARING': 'true'}
        # The terminal QR and any diagnostics remain in the private integration home.
        with (root/'bridge.log').open('a') as log:
            return subprocess.call([config['binary']], env=env, cwd=root, stdout=log, stderr=log)
    if args.action == 'pair':
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *args):
                return None
        req = Request(config['url'].removesuffix('/mcp')+'/pair', headers={'Authorization': 'Bearer '+config['token']})
        with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=10) as response:
            body = response.read(100000)
            if not body.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('No valid pairing image was returned.')
        path = root/('pairing-'+str(time.time_ns())+'.png')
        path.write_bytes(body); path.chmod(0o600)
        print(path)
        return
    name = {'status': 'whatsapp_muse_status', 'test': 'send_muse_connection_test', 'reply': 'read_muse_connection_test'}[args.action]
    result = asyncio.run(call_tool(name, {} if args.action == 'status' else {'request_id': args.request_id}))
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        pass
    except Exception:
        print('Local WhatsApp setup/test failed. Inspect the private integration log; credentials and provider errors are withheld.', file=sys.stderr)
        sys.exit(1)
