import json,hashlib
import pytest
from fastapi.testclient import TestClient
from test_agent_surfaces import fixture_app
from test_resume import PDF


def publish(store,**changes):
    data={'version':1,'name':'Synthetic Person','headline':'Product engineering','summary':'A synthetic resume.',
          'contacts':[{'label':'Website','url':'https://example.org'}],
          'sections':[{'title':'Experience','entries':[{'title':'Engineering lead','organization':'Example Company','dates':'2020–2026',
                     'bullets':[{'text':'Delivered a synthetic project.','children':['A supporting point.']}]}]}],**changes}
    (store.brain.scope('public')/'resume.json').write_text(json.dumps(data))
    return data


def test_html_and_pdf_preferences_and_independent_withdrawal(tmp_path,monkeypatch):
    module,store=fixture_app(tmp_path,monkeypatch)
    with TestClient(module.app) as c:
        assert c.get('/resume').status_code==404
        publish(store)
        r=c.get('/resume');assert r.status_code==200
        assert '<h1>Synthetic Person</h1>' in r.text and 'Delivered a synthetic project.' in r.text
        assert '/visit.js' not in r.text and '/contact.css' not in r.text
        assert 'Download PDF' not in r.text
        assert c.get('/api/resume').json()['url']=='/resume'
        (store.brain.scope('public')/'resume.pdf').write_bytes(PDF)
        assert 'Download PDF' in c.get('/resume').text
        publish(store,preferred_format='pdf')
        assert c.get('/api/resume').json()['url']=='/resume.pdf'
        publish(store,formats={'html':True,'pdf':False})
        assert c.get('/resume').status_code==200 and c.get('/resume.pdf').status_code==404
        publish(store,formats={'html':False,'pdf':True})
        assert c.get('/resume').status_code==404 and c.get('/resume.pdf').content==PDF
        publish(store,enabled=False)
        assert c.get('/resume').status_code==c.get('/resume.pdf').status_code==404
        assert c.get('/api/resume').json()['available'] is False


def test_source_pdf_pin_prevents_a_stale_html_view(tmp_path,monkeypatch):
    module,store=fixture_app(tmp_path,monkeypatch)
    path=store.brain.scope('public')/'resume.pdf';path.write_bytes(PDF)
    publish(store,source_pdf_sha256=hashlib.sha256(PDF).hexdigest())
    with TestClient(module.app) as c:
        assert c.get('/resume').status_code==200
        path.write_bytes(PDF.replace(b'fixture',b'changed fixture'))
        assert c.get('/resume').status_code==404
        assert c.get('/api/resume').json()['url']=='/resume.pdf'


def test_resume_text_is_escaped_and_unsafe_links_are_rejected(tmp_path,monkeypatch):
    module,store=fixture_app(tmp_path,monkeypatch)
    publish(store,name='<script>alert(1)</script>')
    with TestClient(module.app) as c:
        text=c.get('/resume').text
        assert '&lt;script&gt;alert(1)&lt;/script&gt;' in text and '<script>' not in text
        publish(store,contacts=[{'label':'Bad','url':'javascript:alert(1)'}])
        assert c.get('/resume').status_code==404 and c.get('/api/resume').json()['available'] is False


def test_private_or_symlinked_html_data_is_not_published(tmp_path,monkeypatch):
    module,store=fixture_app(tmp_path,monkeypatch)
    p=store.brain.scope('private')/'resume.json';p.write_text('{"name":"PRIVATE CANARY"}')
    with TestClient(module.app) as c:
        assert c.get('/resume').status_code==404
        (store.brain.scope('public')/'resume.json').symlink_to(p)
        assert c.get('/resume').status_code==404


def test_data_publisher_requires_current_pdf_and_explicit_replacement(tmp_path,monkeypatch):
    from scripts.publish_resume_data import publish as install
    module,store=fixture_app(tmp_path,monkeypatch)
    root=store.brain.root
    data={'name':'Synthetic Person','source_pdf_sha256':hashlib.sha256(PDF).hexdigest()}
    source=tmp_path/'reviewed-data.json';source.write_text(json.dumps(data))
    with pytest.raises(ValueError):install(source,root,True)
    (store.brain.scope('public')/'resume.pdf').write_bytes(PDF)
    assert install(source,root)['published'] is False
    assert not (store.brain.scope('public')/'resume.json').exists()
    install(source,root,True)
    with pytest.raises(ValueError):install(source,root,True)
    install(source,root,True,True)


def test_public_export_preserves_html_format_configuration(tmp_path,monkeypatch):
    import tarfile
    from scripts.export_public_brain import export_public
    _,store=fixture_app(tmp_path,monkeypatch)
    store.brain.scope('public').joinpath('voice.md').write_text('Synthetic voice')
    store.brain.scope('public').joinpath('portfolio.json').write_text('{}')
    store.brain.scope('public').joinpath('resume.pdf').write_bytes(PDF)
    publish(store,formats={'html':True,'pdf':False})
    output=tmp_path/'export.tgz';export_public(store.brain.root,output)
    with tarfile.open(output) as archive:
        assert json.load(archive.extractfile('brain/public/resume.json'))['formats']['pdf'] is False
        assert archive.extractfile('brain/public/resume.pdf').read()==PDF
