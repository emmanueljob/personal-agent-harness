"""Revocable browser sessions for the existing owner authentication boundary."""
import hashlib
import hmac
import os
import secrets
import time

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from .public_access import public_only

COOKIE = 'owner_session'
LIFETIME = 12 * 60 * 60


def credential():
    return os.getenv('OWNER_STUDIO_TOKEN' if public_only() else 'OWNER_TOKEN', '')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def initialize(store):
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS owner_sessions (digest TEXT PRIMARY KEY, credential TEXT NOT NULL, expires REAL NOT NULL)')


def session(store, token):
    secret = credential()
    if not secret or not token or len(token) > 200:
        return None
    initialize(store)
    with store.connect() as db:
        row = db.execute('SELECT credential, expires FROM owner_sessions WHERE digest=?', (digest(token),)).fetchone()
    fingerprint = hmac.new(secret.encode(), b'owner-session', hashlib.sha256).hexdigest()
    if row and row['expires'] > time.time() and secrets.compare_digest(row['credential'], fingerprint):
        return row['expires']
    return None


def same_origin(request):
    origin = request.headers.get('origin')
    expected = os.getenv('PUBLIC_ORIGIN', '').rstrip('/') or str(request.base_url).rstrip('/')
    if request.headers.get('sec-fetch-site') == 'cross-site' or (origin and origin.rstrip('/') != expected):
        raise HTTPException(403, 'Cross-site requests are not accepted.')


def response(value):
    return JSONResponse(value, headers={'Cache-Control': 'private, no-store'})


class OwnerSessionMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, get_store):
        super().__init__(app)
        self.get_store = get_store

    async def dispatch(self, request, call_next):
        token = request.cookies.get(COOKIE, '')
        if token and session(self.get_store(), token):
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                try:
                    same_origin(request)
                except HTTPException as error:
                    return JSONResponse({'detail': error.detail}, status_code=error.status_code)
            # All owner dependencies and explicit private-thread checks already
            # validate this header. Keep explicit bearer credentials authoritative.
            if not request.headers.get('authorization'):
                request.scope['headers'] = [
                    (key, value) for key, value in request.scope['headers'] if key.lower() != b'authorization'
                ] + [(b'authorization', ('Bearer ' + credential()).encode())]
        result = await call_next(request)
        if token or request.url.path == '/api/admin/session':
            result.headers['Cache-Control'] = 'private, no-store'
        return result


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=2000)


def mount(app, get_store):
    @app.post('/api/admin/session', include_in_schema=False)
    def login(body: Login, request: Request):
        same_origin(request)
        secret = credential()
        if not secret:
            raise HTTPException(404, 'Owner workspace is unavailable.')
        if not secrets.compare_digest(body.password.encode(), secret.encode()):
            from .public_access import consume
            consume(get_store(), [('hour:admin-auth-failures', 3600, 120)])
            raise HTTPException(401, 'Enter your owner password.')
        store = get_store()
        initialize(store)
        token = secrets.token_urlsafe(32)
        expires = time.time() + LIFETIME
        fingerprint = hmac.new(secret.encode(), b'owner-session', hashlib.sha256).hexdigest()
        with store.connect() as db:
            db.execute('DELETE FROM owner_sessions WHERE expires<=? OR digest=?',
                       (time.time(), digest(request.cookies.get(COOKIE, ''))))
            db.execute('INSERT INTO owner_sessions VALUES (?,?,?)', (digest(token), fingerprint, expires))
        result = response({'authenticated': True, 'expires_at': expires})
        result.set_cookie(COOKIE, token, max_age=LIFETIME, httponly=True, secure=request.url.scheme == 'https' or os.getenv('PUBLIC_ORIGIN', '').startswith('https://'), samesite='strict', path='/')
        return result

    @app.get('/api/admin/session', include_in_schema=False)
    def restore(request: Request):
        expires = session(get_store(), request.cookies.get(COOKIE, ''))
        if not expires:
            raise HTTPException(401, 'Sign in to continue.')
        return response({'authenticated': True, 'expires_at': expires})

    @app.delete('/api/admin/session', include_in_schema=False)
    def logout(request: Request):
        same_origin(request)
        initialize(get_store())
        with get_store().connect() as db:
            db.execute('DELETE FROM owner_sessions WHERE digest=?', (digest(request.cookies.get(COOKIE, '')),))
        result = response({'authenticated': False})
        result.delete_cookie(COOKIE, path='/', httponly=True, samesite='strict', secure=request.url.scheme == 'https' or os.getenv('PUBLIC_ORIGIN', '').startswith('https://'))
        return result
