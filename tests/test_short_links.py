import json
from urllib.parse import urlsplit,parse_qs

import pytest
from fastapi.testclient import TestClient
from harness.outreach import build_link,build_short_link,short_tokens,report,shorten_link,set_link_active,set_tag_active,database_path
from harness.personalization import resolve
from test_admin import admin_app,AUTH


def setup(tmp_path,monkeypatch):
    module,store=admin_app(tmp_path,monkeypatch)
    monkeypatch.setenv('PORTFOLIO_LINKS_DB',str(tmp_path/'links.sqlite'))
    return module,store


def test_clean_url_serves_personalization_without_redirect_or_attribution_codes(tmp_path,monkeypatch):
    module,_=setup(tmp_path,monkeypatch)
    url=build_short_link('https://testserver','chett','hex','warm')
    assert url=='https://testserver/hi/chett'
    tokens=short_tokens('chett')
    assert resolve(tokens).key=='content:chett'
    with TestClient(module.app,base_url='https://testserver') as client:
        home=client.get('/hi/chett',follow_redirects=False)
        assert home.status_code==200 and 'location' not in home.headers
        assert 'Hi Chett' in home.text
        assert all(token not in home.text for token in tokens.values())
        assert home.headers['cache-control']=='private, no-store'
        assert home.headers['x-robots-tag']=='noindex, nofollow'
        assert client.get('/api/personalization?greeting=chett',headers={'X-Portfolio-Profile':'campaign:hex'}).json()['key']=='content:chett'
        assert client.get('/hi/missing').status_code==404
        assert client.get('/api/personalization?greeting=missing').status_code==404


def test_unique_names_custom_paths_and_collision_roll_back(tmp_path,monkeypatch):
    setup(tmp_path,monkeypatch)
    assert build_short_link('https://testserver','Alex One',source='warm').endswith('/hi/alex')
    assert build_short_link('https://testserver','Alex Two',source='cold').endswith('/hi/alex-2')
    assert build_short_link('https://testserver','José Smith',source='warm').endswith('/hi/jose')
    assert build_short_link('https://testserver','Private Full Name',source='warm',slug='hello-there').endswith('/hi/hello-there')
    before=report()
    for invalid in ('hello-there','../admin','x/y','<script>','a'*61):
        with pytest.raises(ValueError):build_short_link('https://testserver','Another',slug=invalid)
    assert len(report()['links'])==len(before['links']) and len(report()['tags'])==len(before['tags'])
    for invalid in ('../admin','x/y','<script>','unknown'):assert short_tokens(invalid) is None
    with pytest.raises(ValueError):build_short_link('https://testserver',source='')


def test_existing_links_can_be_shortened_without_changing_codes_or_counts(tmp_path,monkeypatch):
    setup(tmp_path,monkeypatch)
    legacy=build_link('https://testserver','chett','hex','warm')
    record=report()['links'][0]
    shortened=shorten_link('https://testserver',record['id'])
    assert shortened=='https://testserver/hi/chett'
    assert shorten_link('https://testserver',record['id'])==shortened
    assert short_tokens('chett')=={k:v[0] for k,v in parse_qs(urlsplit(legacy).query).items()}
    assert report()['links'][0]['slug']=='chett' and len(report()['links'])==1
    assert resolve(short_tokens('chett')).key=='content:chett'


def test_revocation_reactivation_and_disabled_tag_apply_to_short_routes(tmp_path,monkeypatch):
    module,_=setup(tmp_path,monkeypatch)
    build_short_link('https://testserver','chett','hex','warm')
    record=report()['links'][0];tokens=record['tokens']
    with TestClient(module.app,base_url='https://testserver') as client:
        set_link_active(record['id'],False)
        assert client.get('/hi/chett').status_code==404
        assert client.get('/api/personalization?greeting=chett').status_code==404
        set_link_active(record['id'],True)
        assert client.get('/hi/chett').status_code==200
        set_tag_active(tokens['utm_campaign'],False)
        assert short_tokens('chett') is None and client.get('/hi/chett').status_code==404
        set_tag_active(tokens['utm_campaign'],True)
        assert client.get('/hi/chett').status_code==200


def test_short_visit_attribution_is_server_resolved_without_names_in_analytics(tmp_path,monkeypatch):
    module,store=setup(tmp_path,monkeypatch)
    build_short_link('https://testserver','chett','hex','warm')
    captured=[]
    async def capture(event,origin):captured.append({'url':event.url,'referrer':event.referrer,'utm':event.utm})
    monkeypatch.setattr('harness.visits.deliver',capture)
    with TestClient(module.app,base_url='https://testserver') as client:
        event={'name':'pageview','url':'/hi/chett#bookshelf','greeting':'chett','profile_key':'default','utm':{'utm_source':'wrong'},'referrer':'https://testserver/hi/other-person'}
        assert client.post('/api/visit-events',json=event).status_code==204
        result=report()
        assert result['counts'][0]['content']==short_tokens('chett')['utm_content']
        assert captured[0]['url']=='/#bookshelf' and captured[0]['referrer']==''
        assert captured[0]['utm']==short_tokens('chett')
        assert 'chett' not in json.dumps(store.query('SELECT * FROM ui_events'))
        set_link_active(result['links'][0]['id'],False)
        assert client.post('/api/visit-events',json=event).status_code==204
        assert report()['counts'][0]['count']==1


def test_owner_builder_supports_short_and_legacy_clients(tmp_path,monkeypatch):
    module,_=setup(tmp_path,monkeypatch)
    with TestClient(module.app,base_url='https://testserver') as client:
        assert client.post('/api/admin/links',json={'person':'chett','short':True}).status_code==401
        created=client.post('/api/admin/links',headers=AUTH,json={'person':'chett','company':'hex','source':'warm','short':True,'slug':'hello-chett'})
        assert created.json()['url']=='https://testserver/hi/hello-chett'
        assert client.get('/hi/hello-chett').status_code==200
        ident=report()['links'][0]['id']
        assert client.post('/api/admin/links/'+ident+'/shorten',headers=AUTH).json()['url']==created.json()['url']
        assert client.post('/api/admin/links/'+ident+'/shorten').status_code==401
        assert client.post('/api/admin/links/missing/shorten',headers=AUTH).status_code==404
        legacy=client.post('/api/admin/links',headers=AUTH,json={'source':'cold'}).json()['url']
        assert '?utm_source=t_' in legacy
