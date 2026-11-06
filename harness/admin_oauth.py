"""Owner-only Google sign-in for the admin MCP; OAuth state stays off Git."""
import asyncio
import base64
import hashlib
import json
import os
import secrets
import sqlite3
import time
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from mcp.server.auth.provider import AccessToken, AuthorizationCode, AuthorizeError, RefreshToken, TokenError
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

SCOPE = 'portfolio:admin'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def challenge(value):
    return base64.urlsafe_b64encode(hashlib.sha256(value.encode()).digest()).rstrip(b'=').decode()


class AdminOAuth:
    def __init__(self, get_store):
        self.get_store = get_store

    @property
    def origin(self):
        return os.getenv('PUBLIC_ORIGIN', '').rstrip('/')

    @property
    def resource(self):
        return self.origin+'/admin-mcp'

    @property
    def enabled(self):
        return (self.origin.startswith('https://') and all(os.getenv(k) for k in (
            'ADMIN_GOOGLE_CLIENT_ID', 'ADMIN_GOOGLE_CLIENT_SECRET', 'ADMIN_GOOGLE_OWNER_EMAIL'))
            and bool(os.getenv('OWNER_STUDIO_TOKEN') or os.getenv('OWNER_TOKEN')))

    def connect(self):
        path = self.get_store().root/'admin-auth.sqlite'
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        db = sqlite3.connect(path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE IF NOT EXISTS oauth_records(kind TEXT,key TEXT,body TEXT,expires REAL,PRIMARY KEY(kind,key))')
        db.execute('DELETE FROM oauth_records WHERE expires < ?', (time.time(),))
        db.commit()
        return db

    def put(self, kind, key, value, expires, db=None):
        if db is None:
            with self.connect() as db:
                self.put(kind, key, value, expires, db)
            return
        db.execute('INSERT OR REPLACE INTO oauth_records VALUES (?,?,?,?)',
                   (kind, digest(key), json.dumps(value), expires))

    def get(self, kind, key, consume=False):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT body,expires FROM oauth_records WHERE kind=? AND key=?', (kind,digest(key))).fetchone()
            if consume:
                db.execute('DELETE FROM oauth_records WHERE kind=? AND key=?', (kind,digest(key)))
            return json.loads(row['body']) if row and row['expires'] > time.time() else None

    async def get_client(self, client_id):
        data = self.get('client', client_id)
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self, client_info):
        from mcp.server.auth.provider import RegistrationError
        # Public native/web clients use PKCE; no client secret needs to be stored.
        valid = client_info.token_endpoint_auth_method == 'none' and client_info.redirect_uris
        for uri in client_info.redirect_uris or []:
            parsed = urlsplit(str(uri))
            valid = valid and not parsed.username and not parsed.password and not parsed.fragment
            valid = valid and (parsed.scheme == 'https' or (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1','localhost','::1')))
        if not valid or set(client_info.grant_types)-{'authorization_code','refresh_token'}:
            raise RegistrationError(error='invalid_client_metadata', error_description='Use a PKCE public client with HTTPS or loopback redirect URIs.')
        with self.connect() as db:
            if db.execute("SELECT count(*) FROM oauth_records WHERE kind='client'").fetchone()[0] >= 100:
                raise RegistrationError(error='invalid_client_metadata', error_description='Registration capacity reached.')
        data = client_info.model_dump(mode='json')
        data['client_secret'] = None
        self.put('client', client_info.client_id, data, time.time()+30*86400)

    async def authorize(self, client, params):
        if not self.enabled:
            raise AuthorizeError(error='temporarily_unavailable', error_description='Admin sign-in is not configured.')
        if params.resource != self.resource:
            raise AuthorizeError(error='invalid_target', error_description='Use the admin MCP resource URL.')
        if params.scopes and set(params.scopes) != {SCOPE}:
            raise AuthorizeError(error='invalid_scope')
        state = secrets.token_urlsafe(32)
        self.put('pending',state,dict(client_id=client.client_id,client_name=client.client_name or 'MCP client',
            params=params.model_dump(mode='json'),nonce=secrets.token_urlsafe(32),verifier=secrets.token_urlsafe(48)),time.time()+600)
        return self.origin+'/admin-auth/consent?'+urlencode({'state':state})

    def consent(self, state, cookie=None):
        pending = self.get('pending',state)
        if not pending:
            return None
        if cookie:
            pending['cookie_hash'] = digest(cookie)
            self.put('pending',state,pending,time.time()+600)
        return pending

    def google_url(self, state, cookie):
        pending = self.get('pending',state)
        if not pending or not cookie or not secrets.compare_digest(pending.get('cookie_hash',''),digest(cookie)):
            raise ValueError('Sign-in session expired. Start again from your MCP client.')
        return 'https://accounts.google.com/o/oauth2/v2/auth?'+urlencode({
            'client_id':os.environ['ADMIN_GOOGLE_CLIENT_ID'], 'redirect_uri':self.origin+'/admin-auth/callback',
            'response_type':'code', 'scope':'openid email', 'state':state, 'nonce':pending['nonce'],
            'code_challenge':challenge(pending['verifier']), 'code_challenge_method':'S256', 'prompt':'select_account',
        })

    def verify_google(self, token, nonce):
        key = jwt.PyJWKClient('https://www.googleapis.com/oauth2/v3/certs',timeout=10).get_signing_key_from_jwt(token)
        claims = jwt.decode(token,key.key,algorithms=['RS256'],audience=os.environ['ADMIN_GOOGLE_CLIENT_ID'],
                            issuer=['https://accounts.google.com','accounts.google.com'],options={'require':['exp','iat','sub','nonce','email','email_verified']})
        if claims.get('azp',os.environ['ADMIN_GOOGLE_CLIENT_ID'])!=os.environ['ADMIN_GOOGLE_CLIENT_ID'] or (isinstance(claims['aud'],list) and len(claims['aud'])>1 and 'azp' not in claims):
            raise ValueError('Invalid Google authorized party.')
        if (claims['email_verified'] is not True or claims['email'].casefold()!=os.environ['ADMIN_GOOGLE_OWNER_EMAIL'].casefold()
                or not secrets.compare_digest(claims['nonce'],nonce)):
            raise ValueError('This Google account is not authorized for the admin MCP.')
        return claims

    async def finish_google(self, state, code, cookie):
        pending = self.get('pending',state)
        if not pending or not cookie or not secrets.compare_digest(pending.get('cookie_hash',''),digest(cookie)):
            raise ValueError('Sign-in session expired. Start again from your MCP client.')
        # Consume before network I/O so concurrent callbacks cannot reuse a flow.
        pending = self.get('pending',state,consume=True)
        if not pending:
            raise ValueError('Sign-in session was already used.')
        async with httpx.AsyncClient(timeout=15,trust_env=False) as client:
            response = await client.post('https://oauth2.googleapis.com/token',data={
                'client_id':os.environ['ADMIN_GOOGLE_CLIENT_ID'], 'client_secret':os.environ['ADMIN_GOOGLE_CLIENT_SECRET'],
                'code':code, 'code_verifier':pending['verifier'], 'redirect_uri':self.origin+'/admin-auth/callback',
                'grant_type':'authorization_code'})
            response.raise_for_status()
            claims = await asyncio.to_thread(self.verify_google,response.json()['id_token'],pending['nonce'])
        raw = secrets.token_urlsafe(32)
        record = {**pending['params'],'client_id':pending['client_id'],'scopes':[SCOPE],
                  'expires_at':time.time()+120,'subject':claims['sub'],'email':claims['email']}
        self.put('code',raw,record,record['expires_at'])
        target = pending['params']['redirect_uri']
        query = {'code':raw}
        if pending['params'].get('state') is not None:
            query['state'] = pending['params']['state']
        return target+('&' if '?' in target else '?')+urlencode(query)

    async def load_authorization_code(self, client, authorization_code):
        data = self.get('code',authorization_code)
        if not data or data['client_id']!=client.client_id:
            return None
        return AuthorizationCode(code=authorization_code,**{k:v for k,v in data.items() if k!='email' and k!='state'})

    def owner_matches(self, data):
        return self.enabled and data.get('email','').casefold()==os.environ['ADMIN_GOOGLE_OWNER_EMAIL'].casefold()

    def exchange(self, kind, raw, client_id, scopes=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT body,expires FROM oauth_records WHERE kind=? AND key=?',(kind,digest(raw))).fetchone()
            data = json.loads(row['body']) if row else {}
            if not row or row['expires']<=time.time() or data.get('client_id')!=client_id or not self.owner_matches(data):
                raise TokenError(error='invalid_grant')
            if scopes and set(scopes)-set(data['scopes']):
                raise TokenError(error='invalid_scope')
            db.execute('DELETE FROM oauth_records WHERE kind=? AND key=?',(kind,digest(raw)))
            family = data.get('family') or secrets.token_hex(16)
            # Rotating a refresh token also invalidates its previous access token.
            db.execute("DELETE FROM oauth_records WHERE kind IN ('access','refresh') AND json_extract(body,'$.family')=?",(family,))
            access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            common = dict(client_id=client_id,scopes=[SCOPE],resource=self.resource,subject=data['subject'],email=data['email'],family=family)
            self.put('access',access,{**common,'expires_at':int(time.time()+3600)},time.time()+3600,db)
            self.put('refresh',refresh,{**common,'expires_at':int(time.time()+7*86400)},time.time()+7*86400,db)
        return OAuthToken(access_token=access,refresh_token=refresh,token_type='Bearer',expires_in=3600,scope=SCOPE)

    async def exchange_authorization_code(self, client, authorization_code):
        return self.exchange('code',authorization_code.code,client.client_id)

    async def load_refresh_token(self, client, refresh_token):
        data = self.get('refresh',refresh_token)
        if not data or data['client_id']!=client.client_id or not self.owner_matches(data):
            return None
        return RefreshToken(token=refresh_token,**{k:v for k,v in data.items() if k not in ('email','family')})

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        return self.exchange('refresh',refresh_token.token,client.client_id,scopes)

    async def load_access_token(self, token):
        data = self.get('access',token)
        if not data or not self.owner_matches(data):
            return None
        return AccessToken(token=token,claims={'email':data['email']},**{k:v for k,v in data.items() if k not in ('email','family')})

    async def revoke_token(self, token):
        data = self.get('access',token.token) or self.get('refresh',token.token)
        if data:
            with self.connect() as db:
                db.execute("DELETE FROM oauth_records WHERE kind IN ('access','refresh') AND json_extract(body,'$.family')=?",(data['family'],))
