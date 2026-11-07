import asyncio
import importlib
import io
import json
import tarfile
from pathlib import Path
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from harness.store import Store
from harness.public_access import consume, reserve_model_call
from scripts.export_public_brain import export_public


def configure(tmp_path,monkeypatch):
    monkeypatch.setenv('DATA_DIR',str(tmp_path/'state'))
    monkeypatch.setenv('BRAIN_BACKEND','files')
    monkeypatch.setenv('BRAIN_DIR',str(tmp_path/'brain'))
    monkeypatch.setenv('PUBLIC_ONLY','true')
    monkeypatch.setenv('PUBLIC_ORIGIN','https://portfolio.test')
    monkeypatch.setenv('MODEL_PROVIDER','openai')
    monkeypatch.setenv('OPENAI_API_KEY','fixture-key')
    monkeypatch.setenv('OWNER_TOKEN','fixture-owner')
    monkeypatch.setenv('EXECUTOR','local')
    monkeypatch.setenv('AGENT_RUNTIME','legacy')
    module=importlib.import_module('harness.app')
    store=Store(tmp_path/'state')
    monkeypatch.setattr(module,'store',store)
    async def idle(store):await asyncio.sleep(3600)
    monkeypatch.setattr(module,'local_worker',idle)
    return module,store


def test_public_chats_belong_to_browser_and_owner_routes_are_disabled(tmp_path,monkeypatch):
    module,store=configure(tmp_path,monkeypatch)
    with TestClient(module.app,base_url='https://portfolio.test') as first:
        r=first.post('/api/threads',json={'mode':'public'})
        assert r.status_code==200
        cookie=r.headers['set-cookie'].lower()
        assert 'httponly' in cookie and 'secure' in cookie and 'samesite=lax' in cookie
        ident=r.json()['id']
        # Starting fresh must remain possible after the former ten-chat cap.
        from harness.public_access import digest
        for _ in range(10):
            consume(store, [('day:threads:'+digest(first.cookies.get('portfolio_visitor')), 86400, 10)])
        for _ in range(11):
            assert first.post('/api/threads',json={'mode':'public'}).status_code==200
        assert first.get('/api/threads/'+ident).status_code==200
        turn=first.post('/api/threads/'+ident+'/turns',json={'prompt':'A short question','request_id':'same'})
        assert turn.status_code==202
        assert first.post('/api/threads/'+ident+'/turns',json={'prompt':'A short question','request_id':'same'}).json()['id']==turn.json()['id']
        first.cookies.clear()
        assert first.get('/api/threads/'+ident).status_code==404
        assert first.post('/api/turns/'+turn.json()['id']+'/retry').status_code==404
        auth={'Authorization':'Bearer fixture-owner'}
        assert first.post('/api/threads',json={'mode':'private'},headers=auth).status_code==404
        assert first.get('/api/profiles/private',headers=auth).status_code==404
        assert first.get('/api/owner/bookshelf',headers=auth).status_code==404
        assert first.get('/api/status').json()['public_only'] is True


def test_public_origin_queue_and_model_limits(tmp_path,monkeypatch):
    module,store=configure(tmp_path,monkeypatch)
    monkeypatch.setenv('PUBLIC_DAILY_TURNS','1')
    monkeypatch.setenv('PUBLIC_DAILY_MODEL_CALLS','1')
    with TestClient(module.app,base_url='https://portfolio.test') as client:
        assert client.post('/api/threads',json={'mode':'public'},headers={'Origin':'https://other.test'}).status_code==403
        ident=client.post('/api/threads',json={'mode':'public'}).json()['id']
        assert client.post('/api/threads/'+ident+'/turns',json={'prompt':'x'*2001}).status_code==422
        assert client.post('/api/threads/'+ident+'/turns',json={'prompt':'one'}).status_code==202
        assert client.post('/api/threads/'+ident+'/turns',json={'prompt':'two'}).status_code==429
        reserve_model_call(store)
        with pytest.raises(HTTPException):reserve_model_call(store)


def test_public_export_includes_only_selected_public_sources_and_assets(tmp_path):
    brain=tmp_path/'brain';root=brain/'public';root.mkdir(parents=True)
    for name in ('profile.md','voice.md'): (root/name).write_text('Published fixture')
    (root/'portfolio.json').write_text('{}')
    (root/'bookshelf.json').write_text(json.dumps({'books':[{'id':'selected','title':'Fixture'}]}))
    (root/'bookshelf-assets').mkdir();(root/'bookshelf-assets'/'selected.jpg').write_bytes(b'fixture')
    (root/'bookshelf-assets'/'unselected.jpg').write_bytes(b'not published')
    (root/'wiki').mkdir();(root/'wiki'/'topic.md').write_text('Published knowledge')
    (root/'perspectives.json').write_text(json.dumps({'version':1,'entries':[]}))
    (root/'secret.txt').write_text('Unapproved file')
    (brain/'private').mkdir();(brain/'private'/'profile.md').write_text('Private fixture')
    bundle=tmp_path/'export.tgz'
    result=export_public(brain,bundle)
    with tarfile.open(bundle) as archive:
        names=archive.getnames()
        assert 'brain/public/bookshelf-assets/selected.jpg' in names
        assert 'brain/public/wiki/topic.md' in names
        assert 'brain/public/perspectives.json' in names
        assert not any('private' in n or 'unselected' in n or 'secret' in n for n in names)
        assert result['files']==len(names)-1
    (root/'wiki'/'escape.md').symlink_to(brain/'private'/'profile.md')
    with pytest.raises(ValueError,match='symbolic'):export_public(brain,bundle)


def test_container_build_context_has_no_personal_or_runtime_inputs():
    root=Path(__file__).resolve().parents[1]
    rules=(root/'.dockerignore').read_text().splitlines()
    assert next(r for r in rules if r and not r.startswith('#'))=='**'
    assert not any(r.startswith('!') and any(x in r for x in ('.env','brain','state','.runtime','.git/')) for r in rules)
    dockerfile=(root/'Dockerfile').read_text()
    assert 'COPY . ' not in dockerfile
    assert 'sha256sum -c' in dockerfile


def test_public_theme_background_is_served_from_frontend_build(tmp_path, monkeypatch):
    module, store = configure(tmp_path, monkeypatch)
    built = tmp_path / 'frontend-build'
    image = built / 'images' / 'alpine-panorama.jpeg'
    image.parent.mkdir(parents=True)
    body = b'\xff\xd8synthetic-landscape\xff\xd9'
    image.write_bytes(body)
    monkeypatch.setattr(module, 'frontend_dist', built)
    with TestClient(module.app, base_url='https://portfolio.test') as client:
        response = client.get('/images/alpine-panorama.jpeg')
        assert response.status_code == 200
        assert response.headers['content-type'] == 'image/jpeg'
        assert response.content == body
        assert client.get('/images/unreviewed.jpeg').status_code == 404
        image.unlink()
        assert client.get('/images/alpine-panorama.jpeg').status_code == 404
