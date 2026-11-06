import hashlib
import pytest
from fastapi.testclient import TestClient
from test_agent_surfaces import fixture_app

PDF = b'%PDF-1.4\n% Synthetic public document fixture\n%%EOF\n'


def test_missing_and_private_resume_are_never_exposed(tmp_path,monkeypatch):
    module,store=fixture_app(tmp_path,monkeypatch)
    (store.brain.scope('private')/'resume.pdf').write_bytes(PDF)
    with TestClient(module.app) as client:
        assert client.get('/api/resume').json()['available'] is False
        assert client.get('/api/public/profile').json()['resume']['url']==''
        assert client.get('/resume.pdf').status_code==404


def test_exact_public_pdf_download_and_withdrawal(tmp_path,monkeypatch):
    module,store=fixture_app(tmp_path,monkeypatch)
    path=store.brain.scope('public')/'resume.pdf';path.write_bytes(PDF)
    with TestClient(module.app) as client:
        info=client.get('/api/resume').json()
        assert info['available'] and info['url']=='/resume.pdf'
        assert client.get('/api/public/profile').json()['resume']==info
        response=client.get(info['url'])
        assert response.status_code==200 and response.content==PDF
        assert hashlib.sha256(response.content).digest()==hashlib.sha256(PDF).digest()
        assert response.headers['content-type']=='application/pdf'
        assert 'attachment' in response.headers['content-disposition']
        assert response.headers['cache-control']=='no-store'
        assert response.headers['x-content-type-options']=='nosniff'
        path.unlink()
        assert client.get('/resume.pdf').status_code==404
        assert client.get('/api/resume').json()['available'] is False


@pytest.mark.parametrize('kind',['symlink','scope-symlink','invalid','oversize'])
def test_unsafe_resume_is_unavailable(tmp_path,monkeypatch,kind):
    module,store=fixture_app(tmp_path,monkeypatch)
    scope=store.brain.scope('public');path=scope/'resume.pdf'
    if kind=='symlink':path.symlink_to(store.brain.scope('private')/'resume.pdf')
    elif kind=='scope-symlink':
        import shutil
        shutil.rmtree(scope);scope.symlink_to(store.brain.scope('private'),target_is_directory=True)
    elif kind=='invalid':path.write_bytes(b'<html>not a PDF</html>')
    else:
        from harness.resume import MAX_BYTES
        with path.open('wb') as f:f.write(b'%PDF-1.4\n');f.truncate(MAX_BYTES+1)
    with TestClient(module.app) as client:
        assert client.get('/api/resume').json()['available'] is False
        assert client.get('/resume.pdf').status_code==404


def test_publication_is_explicit_and_replacement_requires_intent(tmp_path):
    from scripts.publish_resume import publish
    source=tmp_path/'reviewed.pdf';source.write_bytes(PDF)
    root=tmp_path/'brain'
    assert publish(source,root)['published'] is False
    assert not (root/'public'/'resume.pdf').exists()
    assert publish(source,root,True)['published'] is True
    assert (root/'public'/'resume.pdf').read_bytes()==PDF
    assert (root/'public'/'resume.pdf').stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):publish(source,root,True)
    assert publish(source,root,True,True)['published'] is True


def test_export_carries_only_the_published_resume(tmp_path):
    from scripts.export_public_brain import export_public
    import tarfile,json
    root=tmp_path/'brain';public=root/'public';private=root/'private'
    public.mkdir(parents=True);private.mkdir()
    for name,body in [('profile.md','Synthetic profile'),('voice.md','Synthetic voice'),('portfolio.json','{}'),('bookshelf.json','{"books":[]}')]:
        (public/name).write_text(body)
    (private/'resume.pdf').write_bytes(PDF+b'private')
    output=tmp_path/'export.tgz'
    export_public(root,output)
    with tarfile.open(output) as archive:assert 'brain/public/resume.pdf' not in archive.getnames()
    (public/'resume.pdf').write_bytes(PDF)
    export_public(root,output)
    with tarfile.open(output) as archive:
        assert archive.extractfile('brain/public/resume.pdf').read()==PDF
        manifest=json.load(archive.extractfile('public-export.json'))
        assert manifest['sha256']['brain/public/resume.pdf']==hashlib.sha256(PDF).hexdigest()
    (public/'resume.pdf').unlink();(public/'resume.pdf').symlink_to(private/'resume.pdf')
    with pytest.raises(ValueError):export_public(root,output)
