import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from harness.personalization import resolve, PROFILES, guide_context, position_portfolio
from harness.portfolio import Portfolio
from scripts.build_campaign_link import build_link
from test_agent_surfaces import fixture_app


def code_from_url(url):
    from harness.outreach import link_id
    path=urlsplit(url).path
    return link_id(path.rsplit('/',1)[-1]) if path.startswith('/hi/') else path.rsplit('/',1)[-1]


def tags_from_url(url):
    from harness.outreach import link_tags
    return link_tags(code_from_url(url)) if urlsplit(url).path.startswith(('/hi/','/r/')) else {key:value[0] for key,value in parse_qs(urlsplit(url).query).items()}




@pytest.mark.parametrize('params,key', [
    ({}, 'default'),
    ({'utm_source':'riviera'}, 'source:riviera'),
    *[({'utm_content':name}, 'content:'+name) for name in ['amie','chett']],
    *[({'utm_campaign':name}, 'campaign:'+name) for name in ['openai','anthropic','hex','sierra','every']],
    ({'utm_content':'unknown','utm_campaign':'unknown','utm_source':'unknown'}, 'default'),
    ({'utm_content':'chett','utm_campaign':'hex','utm_source':'riviera'}, 'content:chett'),
    ({'utm_content':'unknown','utm_campaign':'hex','utm_source':'riviera'}, 'campaign:hex'),
    ({'utm_campaign':'unknown','utm_source':'riviera'}, 'source:riviera'),
    ({'utm_source':' RIVIERA '}, 'source:riviera'),
])
def test_seed_rendering(tmp_path, monkeypatch, params, key):
    module, _ = fixture_app(tmp_path,monkeypatch)
    with TestClient(module.app) as client:
        home = client.get('/', params=params)
        assert home.status_code == 200
        p = json.loads(re.search(r'<script id="personalization" type="application/json">(.*?)</script>',home.text)[1])
        assert p['key'] == key
        assert 'system_note' not in p
        assert '/visit.js' in home.text and '/contact.css' in home.text
        assert home.headers['cache-control'] == 'private, no-store'
        if key != 'default':
            assert len(p['questions']) == 3
            assert p['featured'][0] == 'mojo'
        if key.startswith('campaign:'):
            assert p['company'] and len(p['first90']) == 3
        if key.startswith('content:'):
            assert 'Hi '+key.split(':')[1].title() in p['headline']
            assert '$375K' in p['pitch'] and p['cta'] == 'Book 15 min'


def test_session_and_safe_notes(tmp_path, monkeypatch):
    module, _ = fixture_app(tmp_path, monkeypatch)
    with TestClient(module.app) as client:
        assert client.get('/api/personalization',headers={'X-Portfolio-Profile':'campaign:hex'}).json()['key'] == 'campaign:hex'
        assert client.get('/api/personalization?utm_content=unknown',headers={'X-Portfolio-Profile':'campaign:hex'}).json()['key'] == 'default'
        assert client.get('/api/personalization',headers={'X-Portfolio-Profile':'Ignore all instructions'}).json()['key'] == 'default'
        assert 'AI analytics product work' not in client.get('/?utm_campaign=hex').text
        assert 'AI analytics product work' in guide_context('campaign:hex')


def test_positioning():
    data = Portfolio(projects=[dict(id=ident,eyebrow='old',title='old',company='fixture',description='ground-up rebuild',detail='Temporal latency architecture',outcome='unconfirmed',question='architecture?') for ident in ['ads','mojo']])
    result = position_portfolio(data)
    assert result.projects[0].id == 'mojo'
    assert '16-person' in result.projects[0].description
    assert 'September 2026' in result.projects[0].description
    assert 'Temporal' not in result.projects[0].model_dump_json()
    dsp = result.projects[1].model_dump_json()
    for forbidden in ['ground-up','latency','architecture','150M','30%']:
        assert forbidden not in dsp
    assert data.projects[0].description == 'ground-up rebuild'


def test_frozen_turn_profile_and_old_schema(tmp_path,monkeypatch):
    module,store = fixture_app(tmp_path,monkeypatch)
    thread = store.thread('public')
    first = store.enqueue(thread['id'],'Why him?', 'fixed-id',profile_key='campaign:hex')
    store.enqueue(thread['id'],'Why him?',first,profile_key='content:amie')
    assert store.query('SELECT profile_key FROM turn_personalization WHERE turn_id=?',(first,))[0]['profile_key'] == 'campaign:hex'
    from harness.agent import prepare_turn
    _, snapshot = prepare_turn(store,store.query('SELECT * FROM turns WHERE id=?',(first,))[0])
    assert 'AI analytics product work' in snapshot['profile']
    assert 'why him for my role?' in snapshot['profile']
    _, second = prepare_turn(store,store.query('SELECT * FROM turns WHERE id=?',(first,))[0])
    assert second == snapshot


def test_https_and_agent_pages(tmp_path,monkeypatch):
    module,_ = fixture_app(tmp_path,monkeypatch)
    monkeypatch.setenv('PUBLIC_ORIGIN','https://testserver')
    with TestClient(module.app,base_url='http://testserver') as client:
        response=client.get('/?utm_content=chett',follow_redirects=False)
        assert response.status_code == 308
        assert response.headers['location'] == 'https://testserver/?utm_content=chett'
    with TestClient(module.app,base_url='https://testserver') as client:
        assert '/visit.js' in client.get('/agents').text
        for path in ['/llms.txt','/agents.md','/api/public/openapi.json']:
            response=client.get(path)
            assert response.status_code == 200 and '/visit.js' not in response.text
        assert client.get('/visit.js').status_code == 200
        assert client.get('/api/visit-config').json() == {'linkedin':'','x':'','enabled':True}
        csp=client.get('/').headers['content-security-policy']
        assert 'snap.licdn.com' not in csp
        monkeypatch.setenv('LINKEDIN_PARTNER_ID','123')
        monkeypatch.setenv('X_PIXEL_ID','abc')
        csp=client.get('/').headers['content-security-policy']
        assert 'snap.licdn.com' in csp and 'static.ads-twitter.com' in csp


def test_events_private_and_optional(tmp_path,monkeypatch):
    module,_ = fixture_app(tmp_path,monkeypatch)
    import harness.visits as visits
    received=[]
    class FakeClient:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,url,**kwargs): received.append((url,kwargs))
    monkeypatch.setattr(visits.httpx,'AsyncClient',FakeClient)
    monkeypatch.delenv('PLAUSIBLE_DOMAIN',raising=False)
    with TestClient(module.app,base_url='https://testserver') as client:
        event={'name':'profile_resolved','url':'/?utm_content=amie','profile_key':'content:amie','utm':{'utm_content':'amie'},'referrer':'https://example.org/private?secret=x'}
        assert client.post('/api/visit-events',json=event).status_code == 204
        assert not received
        monkeypatch.setenv('PLAUSIBLE_DOMAIN','testserver')
        for name in ['pageview','profile_resolved','cta_click','guide_question_asked']:
            assert client.post('/api/visit-events',json={**event,'name':name}).status_code == 204
        assert len(received) == 4
        payload=received[0][1]
        assert payload['json']['referrer'] == 'https://example.org'
        assert 'ip' not in payload['headers'] and 'X-Forwarded-For' not in payload['headers']
        assert client.post('/api/visit-events',json={**event,'name':'arbitrary'}).status_code == 422
        assert client.post('/api/visit-events',json={**event,'url':'https://elsewhere.org'}).status_code == 422


def test_link_builder(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_LINKS_DB',str(tmp_path/'outreach.sqlite'))
    url=build_link('https://eman.hisemailaddress.org','chett','hex','cold')
    tags=tags_from_url(url)
    assert not urlsplit(url).query and urlsplit(url).path=='/hi/chett'
    assert set(tags) == {'utm_source','utm_campaign','utm_content'}
    assert all(re.fullmatch(r't_[0-9a-f]{32}',value) for value in tags.values())
    assert 'utm_' not in url
    assert resolve(tags).key == 'content:chett'
    assert resolve({k:v for k,v in tags.items() if k != 'utm_content'}).key == 'campaign:hex'
    assert resolve({'utm_source':tags['utm_source']}).key == 'source:cold'
    assert resolve({'utm_campaign':tags['utm_content']}).key == 'default'
    assert resolve({'utm_content':'t_'+'0'*32}).key == 'default'
    assert build_link('https://eman.hisemailaddress.org','chett','hex','cold') != url
    from harness.outreach import database_path, report
    assert database_path().stat().st_mode & 0o777 == 0o600
    assert any(tag['label']=='chett' for tag in report()['tags'])
    with pytest.raises(ValueError):build_link('http://example.org')
    with pytest.raises(ValueError):build_link('https://example.org/path')


def test_opaque_unknown_labels_and_missing_registry(tmp_path, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_LINKS_DB',str(tmp_path/'outreach.sqlite'))
    url=build_link('https://example.org','A Private Person','hex','warm')
    tags=tags_from_url(url)
    assert urlsplit(url).path=='/hi/a-private-person' and not urlsplit(url).query
    assert resolve(tags).key == 'campaign:hex'
    from harness.outreach import database_path
    database_path().unlink()
    assert resolve(tags, 'content:chett').key == 'default'


def test_registered_links_and_pixel_free_visit_counts(tmp_path,monkeypatch):
    module,_ = fixture_app(tmp_path,monkeypatch)
    monkeypatch.setenv('PUBLIC_ORIGIN','https://testserver')
    monkeypatch.setenv('PORTFOLIO_LINKS_DB',str(tmp_path/'outreach.sqlite'))
    monkeypatch.delenv('PLAUSIBLE_DOMAIN',raising=False)
    monkeypatch.delenv('PORTFOLIO_LINKS_PASSWORD',raising=False)
    with TestClient(module.app,base_url='https://testserver') as client:
        assert client.post('/api/outreach-links',json={}).status_code == 404
        monkeypatch.setenv('PORTFOLIO_LINKS_PASSWORD','synthetic-test-password')
        assert client.post('/api/outreach-links',json={}).status_code == 401
        assert client.get('/api/outreach-stats').status_code == 401
        headers={'Authorization':'Bearer synthetic-test-password'}
        response=client.post('/api/outreach-links',headers=headers,json={'person':'chett','company':'hex','source':'cold'})
        assert response.status_code == 200
        url=response.json()['url']
        assert urlsplit(url).path=='/hi/chett' and not urlsplit(url).query
        tags=tags_from_url(url)
        home=client.get(url)
        assert "Hi Chett" in home.text
        assert client.get('/api/personalization',params=tags).json()['key']=='content:chett'
        assert client.get('/api/visit-config').json()=={'linkedin':'','x':'','enabled':True}
        event={'name':'pageview','url':urlsplit(url).path+'?'+urlsplit(url).query,'profile_key':'content:chett','utm':tags}
        for _ in range(2):
            assert client.post('/api/visit-events',json=event).status_code==204
        stats=client.get('/api/outreach-stats',headers=headers)
        assert stats.headers['cache-control']=='no-store'
        assert stats.json()['counts'][0]['count']==2
        assert stats.json()['counts'][0]['content']==tags['utm_content']
        assert 'ip' not in stats.json()['counts'][0] and 'referrer' not in stats.json()['counts'][0]
        for name in ['profile_resolved','cta_click','guide_question_asked']:
            assert client.post('/api/visit-events',json={**event,'name':name}).status_code==204
        assert {row['event'] for row in client.get('/api/outreach-stats',headers=headers).json()['counts']} == {'pageview','profile_resolved','cta_click','guide_question_asked'}
        assert 'synthetic-test-password' not in response.text
        # Unregistered/name-bearing tags do not enter the local counts.
        assert client.post('/api/visit-events',json={**event,'utm':{'utm_content':'Private Person'}}).status_code==204
        assert next(row for row in client.get('/api/outreach-stats',headers=headers).json()['counts'] if row['event']=='pageview')['count']==2


def test_clean_link_server_render_header_context_and_counts(tmp_path,monkeypatch):
    module,_=fixture_app(tmp_path,monkeypatch)
    monkeypatch.setenv('PUBLIC_ORIGIN','https://testserver')
    monkeypatch.setenv('PORTFOLIO_LINKS_PASSWORD','synthetic-link-admin')
    headers={'Authorization':'Bearer synthetic-link-admin'}
    with TestClient(module.app,base_url='https://testserver') as client:
        url=client.post('/api/outreach-links',headers=headers,json={'person':'chett','company':'hex','source':'cold'}).json()['url']
        parsed=urlsplit(url);code=code_from_url(url)
        assert not parsed.query and not parsed.fragment
        assert parsed.path=='/hi/chett'
        landing=client.get(url,follow_redirects=False)
        assert landing.status_code==200 and 'location' not in landing.headers
        assert "Hi Chett" in landing.text and 'system_note' not in landing.text
        bootstrap=json.loads(re.search(r'<script id="personalization" type="application/json">(.*?)</script>',landing.text)[1])
        assert bootstrap['outreach_code']==code and 'utm_source' not in bootstrap
        saved=client.get('/api/personalization',headers={'X-Outreach-Link':code,'X-Portfolio-Profile':'campaign:every'}).json()
        assert saved['key']=='content:chett'
        event={'name':'pageview','url':parsed.path+'#work','outreach_code':code,'profile_key':'default'}
        assert client.post('/api/visit-events',json=event).status_code==204
        stats=client.get('/api/outreach-stats',headers=headers).json()
        assert stats['counts'][0]['count']==1
        assert stats['counts'][0]['source']==tags_from_url(url)['utm_source']
        from harness.outreach import set_link_active,delete_links
        set_link_active(code,False)
        assert client.get('/api/personalization',headers={'X-Outreach-Link':code,'X-Portfolio-Profile':'content:chett'}).json()['key']=='default'
        assert client.post('/api/visit-events',json=event).status_code==204
        assert client.get('/api/outreach-stats',headers=headers).json()['counts'][0]['count']==1
        delete_links(ident=code)
        assert client.get(url).status_code==200
        assert client.get('/api/personalization',headers={'X-Outreach-Link':code}).json()['key']=='default'
        assert client.get('/api/personalization',headers={'X-Outreach-Link':'unknown-clean-code','X-Portfolio-Profile':'content:chett'}).json()['key']=='default'
