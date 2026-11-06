import json
from harness.podcast_artwork import parse_feed,match_artwork,image_url
from harness.media_library import catalog
from test_media_library import fixture

FEED=b'''<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel><itunes:image href="https://cdn.example.org/show.jpg"/><item><title>Example: Episode!</title><guid>episode-one</guid><link>https://example.org/one</link><itunes:image href="https://cdn.example.org/one.jpg"/></item><item><title>Another episode</title><guid>two</guid></item></channel></rss>'''


def test_episode_image_and_show_fallback_match_original_identity():
    feed=parse_feed(FEED)
    assert match_artwork({'title':'Renamed title','source_url':'https://example.org/one','provenance':{}},feed)=={'url':'https://cdn.example.org/one.jpg','kind':'episode'}
    assert match_artwork({'title':'Example Episode','source_url':'https://example.org/unknown','provenance':{}},feed)['kind']=='episode'
    assert match_artwork({'title':'Another episode','source_url':'https://example.org/two','provenance':{}},feed)['kind']=='show'
    assert match_artwork({'title':'Older episode','source_url':'https://example.org/old','provenance':{}},feed)['url']=='https://cdn.example.org/show.jpg'


def test_unavailable_or_unsafe_artwork_keeps_empty_fallback():
    for url in ('javascript:alert(1)','http://cdn.example.org/image.jpg','https://127.0.0.1/image','https://user:password@example.org/image'):
        assert image_url(url)==''
    assert match_artwork({'title':'None','source_url':'https://example.org/none'},parse_feed(b'<rss><channel/></rss>')) is None


def test_catalog_scopes_artwork_and_updates_without_stale_cache(tmp_path,monkeypatch):
    store,_,episode,_=fixture(tmp_path,monkeypatch)
    private=store.brain.scope('private')/'podcast-artwork.json'
    private.write_text(json.dumps({'episodes':{episode['id']:{'url':'https://cdn.example.org/private.jpg','kind':'episode'}}}))
    assert next(r for r in catalog(store.brain) if r['id']==episode['id'])['artwork_url']==''
    public=store.brain.scope('public')/'podcast-artwork.json'
    public.write_text(json.dumps({'episodes':{episode['id']:{'url':'https://cdn.example.org/public.jpg','kind':'episode'}}}))
    assert next(r for r in catalog(store.brain) if r['id']==episode['id'])['artwork_url']=='https://cdn.example.org/public.jpg'
    public.unlink()
    assert next(r for r in catalog(store.brain) if r['id']==episode['id'])['artwork_url']==''


def test_artwork_export_includes_public_metadata_only(tmp_path,monkeypatch):
    import tarfile
    from scripts.export_public_brain import export_public
    store,_,episode,_=fixture(tmp_path,monkeypatch)
    public=store.brain.scope('public')
    for name,body in [('profile.md','Example published facts'),('voice.md','Example voice'),('portfolio.json','{}')]:
        (public/name).write_text(body)
    (public/'podcast-artwork.json').write_text(json.dumps({'version':1,'episodes':{episode['id']:{'url':'https://cdn.example.org/public.jpg','kind':'episode'}}}))
    (store.brain.scope('private')/'podcast-artwork.json').write_text('{"private":"not for publication"}')
    output=tmp_path/'public.tgz';export_public(store.brain.root,output)
    with tarfile.open(output) as archive:
        assert 'brain/public/podcast-artwork.json' in archive.getnames()
        assert 'private' not in archive.extractfile('brain/public/podcast-artwork.json').read().decode()
        assert not any('/private/' in name for name in archive.getnames())


def test_feed_rejects_entity_declarations():
    import pytest
    with pytest.raises(ValueError):
        parse_feed(b'<!DOCTYPE rss [<!ENTITY x "unsafe">]><rss><channel><title>&x;</title></channel></rss>')
