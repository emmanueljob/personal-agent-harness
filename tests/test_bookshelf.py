import json
import pytest
from pydantic import ValidationError
from harness.brain import FileBrain
from harness.bookshelf import Shelf, Book, ImportInput, Selection, BookEdit


def test_import_is_private_and_public_snapshot_requires_review(tmp_path):
    brain = FileBrain(tmp_path / 'brain')
    shelf = Shelf(brain)
    data = shelf.ingest(ImportInput(books=[Book(id='test-work', title='Example Systems', category='work'), Book(id='test-personal', title='Secret Example Memoir', comment='Private comment')]))
    assert not shelf.read('public')['books']
    assert not brain.search('Secret Example Memoir', 'public')
    assert brain.search('Secret Example Memoir', 'private')
    public = shelf.publish(Selection(ids=['test-work'], revision=data['revision']))
    assert [b['id'] for b in public['books']] == ['test-work']
    assert 'Secret Example' not in json.dumps(shelf.read('public'))
    assert 'Secret Example' not in json.dumps(brain.search('Secret Example Memoir', 'public'))
    assert brain.search('Systems', 'public')
    assert (brain.scope('private') / 'bookshelf.json').stat().st_mode & 0o777 == 0o600
    changed = shelf.edit('test-work', BookEdit(category='work', status='unknown', comment='New draft'))
    assert not shelf.read('public')['books'][0]['comment']
    with pytest.raises(ValueError):
        shelf.publish(Selection(ids=['test-work'], revision=data['revision']))
    shelf.publish(Selection(ids=[], revision=changed['revision']))
    assert not brain.search('Systems', 'public')


def test_reimport_preserves_curation_and_rejects_unsafe_data(tmp_path):
    shelf = Shelf(FileBrain(tmp_path))
    shelf.ingest(ImportInput(books=[Book(id='example', title='Example')]))
    shelf.edit('example', BookEdit(category='personal', status='not recorded', comment='My actual comment'))
    d = shelf.ingest(ImportInput(books=[Book(id='example', title='Example updated', comment='Overwrite attempt')]))
    assert d['books'][0]['comment'] == 'My actual comment'
    assert len(d['books']) == 1
    assert d['books'][0]['recommendation'] == 'unknown'
    for cover in ('javascript:alert(1)', 'https://evil.example/cover', 'https://m.media-amazon.com@evil.example/a'):
        with pytest.raises(ValidationError):
            Book(id='valid', title='Example', cover=cover)
    for ids in (['unknown'], ['example','example']):
        with pytest.raises(ValueError):
            shelf.publish(Selection(ids=ids, revision=d['revision']))
    with pytest.raises(ValidationError):
        Book(id='../escape', title='Example')


def test_owner_api_requires_auth_and_public_uses_only_selected_fields(tmp_path, monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    from harness.store import Store
    monkeypatch.setenv('OWNER_TOKEN', 'test-only')
    monkeypatch.setenv('BRAIN_DIR', str(tmp_path / 'brain'))
    module = importlib.import_module('harness.app')
    monkeypatch.setattr(module, 'store', Store(tmp_path / 'state'))
    c = TestClient(module.app)
    auth = {'Authorization':'Bearer test-only'}
    body = {'books':[{'id':'private-test','title':'Private Test Title'}]}
    assert c.get('/api/owner/bookshelf').status_code == 401
    assert c.post('/api/owner/bookshelf/import', json=body).status_code == 401
    assert c.put('/api/owner/bookshelf/private-test', json={}).status_code == 401
    assert c.post('/api/owner/bookshelf/publish', json={'ids':[],'revision':'initial'}).status_code == 401
    inventory = c.post('/api/owner/bookshelf/import', json=body, headers=auth).json()
    assert c.get('/api/bookshelf').json() == {'books':[]}
    assert c.get('/api/portfolio').status_code == 200
    result = c.post('/api/owner/bookshelf/publish', headers=auth, json={'ids':['private-test'],'revision':inventory['revision']})
    assert result.status_code == 200
    public = c.get('/api/bookshelf').json()
    assert set(public) == {'books'}
    assert public['books'][0]['title'] == 'Private Test Title'
    assert c.get('/api/owner/bookshelf', headers=auth).status_code == 200


def test_spine_references_stay_private_until_selected_and_survive_import(tmp_path):
    from harness.bookshelf import SpineReference
    ref = SpineReference(url='https://i.ebayimg.com/images/example.jpg', source='https://www.ebay.com/itm/example', width=800, height=800, points=[(10,10),(30,10),(30,700),(10,700)])
    shelf = Shelf(FileBrain(tmp_path))
    first = shelf.ingest(ImportInput(books=[Book(id='example',title='Example',spine=ref)]))
    assert shelf.read('public')['books'] == []
    again = shelf.ingest(ImportInput(books=[Book(id='example',title='Example')]))
    assert again['books'][0]['spine'] == ref.model_dump(mode='json')
    shelf.publish(Selection(ids=['example'],revision=again['revision']))
    assert shelf.read('public')['books'][0]['spine']['url'] == ref.url
    with pytest.raises(ValidationError):
        SpineReference(url='https://untrusted.example/image.jpg',source='https://example.com',width=10,height=10,points=[(0,0)]*4)


def test_cached_spines_require_public_selection_and_never_serve_private_files(tmp_path, monkeypatch):
    import importlib
    from fastapi.testclient import TestClient
    from harness.store import Store
    from harness.bookshelf import SpineReference
    monkeypatch.setenv('BRAIN_DIR', str(tmp_path/'brain'))
    module=importlib.import_module('harness.app')
    monkeypatch.setattr(module,'store',Store(tmp_path/'state'))
    shelf=Shelf(module.store.brain)
    book=Book(id='test-asset',title='Example',spine=SpineReference(url='/api/bookshelf/spine/test-asset',source='https://example.com/photo',width=100,height=100,points=[(0,0),(10,0),(10,100),(0,100)]))
    inventory=shelf.ingest(ImportInput(books=[book]))
    root=module.store.brain.scope('public')/'bookshelf-assets';root.mkdir(parents=True)
    (root/'test-asset.jpg').write_bytes(b'public-photo')
    c=TestClient(module.app)
    assert c.get('/api/bookshelf/spine/test-asset').status_code==404
    shelf.publish(Selection(ids=['test-asset'],revision=inventory['revision']))
    assert c.get('/api/bookshelf/spine/test-asset').content==b'public-photo'
    shelf.publish(Selection(ids=[],revision=inventory['revision']))
    assert c.get('/api/bookshelf/spine/test-asset').status_code==404
    shelf.publish(Selection(ids=['test-asset'],revision=inventory['revision']))
    (root/'test-asset.jpg').unlink()
    secret=tmp_path/'secret.jpg';secret.write_bytes(b'private')
    (root/'test-asset.jpg').symlink_to(secret)
    assert c.get('/api/bookshelf/spine/test-asset').status_code==404


def test_cover_cache_is_scoped_to_current_public_selection(tmp_path, monkeypatch):
    import importlib,hashlib
    from fastapi.testclient import TestClient
    from harness.store import Store
    monkeypatch.setenv('BRAIN_DIR',str(tmp_path/'brain'))
    module=importlib.import_module('harness.app');monkeypatch.setattr(module,'store',Store(tmp_path/'state'))
    shelf=Shelf(module.store.brain);url='https://m.media-amazon.com/images/example.jpg'
    inventory=shelf.ingest(ImportInput(books=[Book(id='test-cover',title='Example',cover=url)]))
    root=module.store.brain.scope('public')/'bookshelf-covers';root.mkdir(parents=True)
    name='test-cover-'+hashlib.sha256(url.encode()).hexdigest()[:16]+'.jpg'
    (root/name).write_bytes(b'cached-cover')
    c=TestClient(module.app)
    assert c.get('/api/bookshelf/cover/test-cover').status_code==404
    shelf.publish(Selection(ids=['test-cover'],revision=inventory['revision']))
    assert c.get('/api/bookshelf/cover/test-cover').content==b'cached-cover'
    shelf.publish(Selection(ids=[],revision=inventory['revision']))
    assert c.get('/api/bookshelf/cover/test-cover').status_code==404
    shelf.publish(Selection(ids=['test-cover'],revision=inventory['revision']))
    (root/name).unlink();secret=tmp_path/'secret';secret.write_bytes(b'private');(root/name).symlink_to(secret)
    assert c.get('/api/bookshelf/cover/test-cover').status_code==404


def test_spine_corner_winding_cannot_mirror_photographed_text():
    from harness.bookshelf import SpineReference
    ref = SpineReference(url='/api/bookshelf/spine/book', source='https://example.com/photo', width=1000, height=1000,
                         points=[(100,400),(100,500),(900,500),(900,400)])
    assert ref.points == [(100,500),(100,400),(900,400),(900,500)]
    # Both axes keep their photographed handedness when the horizontal spine turns upright.
    a,b,c,d = ref.points
    assert sum(p[0]*q[1]-q[0]*p[1] for p,q in zip([a,b,c,d],[b,c,d,a])) > 0
