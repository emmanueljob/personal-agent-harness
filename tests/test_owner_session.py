"""Synthetic browser session coverage across existing owner API boundaries."""
from fastapi.testclient import TestClient
from test_admin import admin_app
from harness import owner_session as sessions


def test_session_restores_all_studio_boundaries_and_revokes_on_logout(tmp_path, monkeypatch):
    module, store = admin_app(tmp_path, monkeypatch)
    with TestClient(module.app, base_url='https://testserver') as client:
        assert client.post('/api/admin/session', json={'password': 'wrong'}).status_code == 401
        login = client.post('/api/admin/session', json={'password': 'synthetic-owner-access'})
        assert login.status_code == 200
        cookie = client.cookies.get(sessions.COOKIE)
        assert 'HttpOnly' in login.headers['set-cookie']
        assert 'Secure' in login.headers['set-cookie']
        assert 'SameSite=strict' in login.headers['set-cookie']
        assert login.headers['cache-control'] == 'private, no-store'
        assert login.json()['authenticated']
        row, = store.query('SELECT * FROM owner_sessions')
        assert row['digest'] != cookie and row['credential'] != 'synthetic-owner-access'
        # These routers have separate dependencies, including a route outside admin.
        for path in ('/api/admin/config', '/api/admin/crm', '/api/admin/crm/correspondence',
                     '/api/admin/cloud-browser/status', '/api/owner/learning'):
            assert client.get(path).status_code == 200, path
        assert client.get('/api/admin/session').json() == login.json()
        assert client.get('/api/admin/config', headers={'Authorization': 'Bearer wrong'}).status_code == 401
        assert client.delete('/api/admin/session', headers={'Origin': 'https://other.example'}).status_code == 403
        assert client.get('/api/admin/session').status_code == 200
        assert client.delete('/api/admin/session').json() == {'authenticated': False}
        client.cookies.set(sessions.COOKIE, cookie)
        assert client.get('/api/admin/session').status_code == 401
        assert client.get('/api/admin/crm').status_code == 401


def test_session_rotation_expiry_and_login_csrf(tmp_path, monkeypatch):
    module, store = admin_app(tmp_path, monkeypatch)
    with TestClient(module.app, base_url='https://testserver') as client:
        for headers in ({'Origin': 'https://other.example'}, {'Sec-Fetch-Site': 'cross-site'}):
            assert client.post('/api/admin/session', json={'password': 'synthetic-owner-access'}, headers=headers).status_code == 403
        client.post('/api/admin/session', json={'password': 'synthetic-owner-access'})
        old = client.cookies.get(sessions.COOKIE)
        client.post('/api/admin/session', json={'password': 'synthetic-owner-access'})
        assert sessions.session(store, old) is None
        monkeypatch.setenv('OWNER_STUDIO_TOKEN', 'rotated-synthetic-secret')
        assert client.get('/api/admin/session').status_code == 401
        assert client.get('/api/admin/config').status_code == 401
        client.post('/api/admin/session', json={'password': 'rotated-synthetic-secret'})
        with store.connect() as db:
            db.execute('UPDATE owner_sessions SET expires=0')
        assert client.get('/api/admin/session').status_code == 401
        assert client.get('/api/admin/crm').status_code == 401


def test_local_cookie_authenticates_explicit_owner_checks_and_blocks_csrf(tmp_path, monkeypatch):
    module, store = admin_app(tmp_path, monkeypatch)
    monkeypatch.setenv('PUBLIC_ONLY', 'false')
    monkeypatch.setenv('OWNER_TOKEN', 'synthetic-local-owner')
    with TestClient(module.app, base_url='https://testserver') as client:
        assert client.post('/api/admin/session', json={'password': 'synthetic-local-owner'}).status_code == 200
        assert client.get('/api/profiles/private').status_code == 200
        thread = client.post('/api/threads', json={'mode': 'private'})
        assert thread.status_code == 200
        assert client.get('/api/threads/'+thread.json()['id']).status_code == 200
        assert client.post('/api/threads', json={'mode': 'private'}, headers={'Origin': 'https://other.example'}).status_code == 403
        assert client.delete('/api/admin/session').status_code == 200
        assert client.get('/api/profiles/private').status_code == 401
