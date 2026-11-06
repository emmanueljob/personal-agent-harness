"""Public PoC boundaries: visitor-owned chats and bounded queue admission."""
import hashlib
import os
import re
import secrets
import time

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.responses import RedirectResponse
from urllib.parse import urlsplit

COOKIE = 'portfolio_visitor'


def public_only():
    return os.getenv('PUBLIC_ONLY', '').lower() == 'true'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def consume(store, policies):
    """Atomically check and increment all buckets, across web/worker processes."""
    now = int(time.time())
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for bucket, duration, limit in policies:
            window = now // duration
            row = db.execute('SELECT count FROM public_usage WHERE bucket=? AND window=?', (bucket, window)).fetchone()
            if (row[0] if row else 0) >= limit:
                raise HTTPException(429, 'The demo has reached its usage limit. Please try again later.')
        for bucket, duration, _ in policies:
            db.execute('INSERT INTO public_usage VALUES (?,?,1) ON CONFLICT(bucket,window) DO UPDATE SET count=count+1', (bucket, now//duration))
        db.execute('DELETE FROM public_usage WHERE window < ? AND bucket LIKE ?', (now//86400-2, 'day:%'))
        db.execute('DELETE FROM public_usage WHERE window < ? AND bucket LIKE ?', (now//3600-2, 'hour:%'))


def reserve_model_call(store):
    if public_only():
        consume(store, [('day:model', 86400, int(os.getenv('PUBLIC_DAILY_MODEL_CALLS', '150')))])


class PublicAccess(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        canonical = urlsplit(os.getenv('PUBLIC_ORIGIN', ''))
        # Fly terminates TLS. Only trust its protocol header in a Fly deployment.
        scheme = request.headers.get('fly-forwarded-proto', request.url.scheme) if os.getenv('FLY_APP_NAME') else request.url.scheme
        if canonical.scheme == 'https' and request.url.hostname == canonical.hostname and scheme == 'http':
            return RedirectResponse(str(request.url.replace(scheme='https')), status_code=308)
        if request.url.hostname in os.getenv('REDIRECT_HOSTS','').split(','):
            target = os.getenv('PUBLIC_ORIGIN','').rstrip('/')
            if urlsplit(target).scheme=='https' and urlsplit(target).hostname!=request.url.hostname:
                if request.method not in ('GET','HEAD'):
                    return JSONResponse({'detail':'Use the canonical hostname.'},status_code=421)
                suffix = request.url.path + ('?'+request.url.query if request.url.query else '')
                return RedirectResponse(target+suffix,status_code=308)
        if not public_only():
            return await self.decorate(await call_next(request), request)
        path = request.url.path
        request.state.visitor_hash = None
        token = request.cookies.get(COOKIE, '')
        valid = bool(re.fullmatch(r'[a-f0-9]{64}', token))
        if valid:
            request.state.visitor_hash = digest(token)
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            expected = {os.getenv('PUBLIC_ORIGIN', '').rstrip('/')}
            # The Fly-provided hostname remains usable during domain setup.
            if os.getenv('FLY_APP_NAME'):
                expected.add('https://'+os.environ['FLY_APP_NAME']+'.fly.dev')
            # OAuth token/registration endpoints use their protocol's validation;
            # admin MCP requires a resource-bound bearer token on every request.
            oauth_transport = path in {'/register','/token','/revoke','/admin-mcp'}
            if not oauth_transport and ((origin and origin.rstrip('/') not in expected) or request.headers.get('sec-fetch-site') == 'cross-site'):
                return JSONResponse({'detail':'Cross-site requests are not accepted.'}, status_code=403)
            try:
                limit = 128000 if path.startswith('/api/model-bridge/') else 32000
                if int(request.headers.get('content-length', '0')) > limit:
                    return JSONResponse({'detail':'Request is too large.'}, status_code=413)
            except ValueError:
                return JSONResponse({'detail':'Invalid request length.'}, status_code=400)
        match = re.fullmatch(r'/api/threads/([^/]+)(?:/.*)?', path)
        retry = re.fullmatch(r'/api/turns/([^/]+)/retry', path)
        if match or retry:
            store = request.app.state.public_store
            ident = match[1] if match else None
            if retry:
                rows = store.query('SELECT thread_id FROM turns WHERE id=?', (retry[1],))
                ident = rows[0]['thread_id'] if rows else None
            rows = store.query('SELECT visitor_hash FROM visitor_threads WHERE thread_id=?', (ident,))
            if not valid or not rows or not secrets.compare_digest(rows[0]['visitor_hash'], request.state.visitor_hash):
                return JSONResponse({'detail':'Conversation not found.'}, status_code=404)
        fresh = path == '/api/threads' and request.method == 'POST' and not valid
        if fresh:
            token = secrets.token_hex(32)
            request.state.visitor_hash = digest(token)
        response = await call_next(request)
        if fresh and response.status_code < 400:
            response.set_cookie(COOKIE, token, max_age=60*60*24*30, secure=True, httponly=True, samesite='lax')
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' https: data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        if path.startswith('/api/threads') or retry:
            response.headers['Cache-Control'] = 'no-store'
        response = await self.decorate(response, request)
        return response

    async def decorate(self, response, request):
        if response.headers.get('content-type', '').startswith('text/html') and request.url.path != '/admin' and not request.url.path.startswith('/admin-auth/'):
            body = b''.join([chunk async for chunk in response.body_iterator]).decode('utf-8')
            body = body.replace('</head>', '<link rel="stylesheet" href="/contact.css"><script src="/visit.js" defer></script></head>', 1)
            from starlette.responses import Response
            headers = dict(response.headers)
            headers.pop('content-length', None)
            headers['cache-control'] = 'private, no-store'
            response = Response(body, status_code=response.status_code, headers=headers, background=response.background)
        csp = response.headers.get('Content-Security-Policy', '')
        if csp:
            scripts = []
            connections = []
            from .site_config import settings
            config = settings()
            if config.tracking_enabled and config.linkedin_partner_id:
                scripts += ['https://snap.licdn.com']
                connections += ['https://px.ads.linkedin.com', 'https://px4.ads.linkedin.com']
            if config.tracking_enabled and config.x_pixel_id:
                scripts += ['https://static.ads-twitter.com', 'https://analytics.twitter.com']
                connections += ['https://analytics.twitter.com', 'https://t.co', 'https://ads-api.twitter.com']
            csp = csp.replace("script-src 'self'", "script-src 'self' "+' '.join(scripts)).replace("connect-src 'self'", "connect-src 'self' "+' '.join(connections))
            response.headers['Content-Security-Policy'] = csp
        return response


def admit_turn(store, request):
    if not public_only():
        return
    visitor = request.state.visitor_hash
    peer = request.headers.get('fly-client-ip') if os.getenv('FLY_APP_NAME') else None
    peer = peer or (request.client.host if request.client else 'unknown')
    consume(store, [
        ('hour:visitor:'+visitor, 3600, 12),
        ('day:ip:'+digest(peer), 86400, 20),
        ('day:turns', 86400, int(os.getenv('PUBLIC_DAILY_TURNS', '50'))),
    ])
    if len(store.query("SELECT id FROM turns WHERE status IN ('queued','running')")) >= 8:
        raise HTTPException(429, 'The demo is busy. Please try again shortly.')
