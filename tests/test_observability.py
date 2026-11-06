import json
import time
import uuid

import httpx
from fastapi.testclient import TestClient
from test_agent_surfaces import fixture_app


def setup(tmp_path, monkeypatch):
    module, store = fixture_app(tmp_path, monkeypatch)
    monkeypatch.setenv('OWNER_STUDIO_TOKEN', 'fixture-studio')
    monkeypatch.delenv('POSTHOG_PROJECT_TOKEN', raising=False)
    monkeypatch.delenv('PLAUSIBLE_DOMAIN', raising=False)
    return module, store


AUTH = {'Authorization': 'Bearer fixture-studio'}


def test_logs_are_owner_only_and_export_complete_history(tmp_path, monkeypatch):
    module, store = setup(tmp_path, monkeypatch)
    thread = store.thread('public')
    ident = store.enqueue(thread['id'], 'synthetic question')
    private = store.thread('private')
    store.enqueue(private['id'], 'synthetic private question')
    with store.connect() as db:
        db.execute("UPDATE turns SET status='failed',error='Synthetic failure' WHERE id=?", (ident,))
        db.executemany('INSERT INTO events(turn_id,kind,label,created) VALUES (?,?,?,?)', [(ident,'tool_end','synthetic event',time.time())]*205)
        db.execute('INSERT INTO deep_transcripts VALUES (?,?)', (ident,json.dumps([{'type':'human','content':'synthetic question'}])))
        db.execute('INSERT INTO tool_results VALUES (?,?,?,?)', (ident,'search_web','{"query":"synthetic"}','{"result":"fixture"}'))
        db.execute('INSERT INTO execution_context VALUES (?,?)', (ident,json.dumps({'config':{'provider':'ollama','model':'fake','api_key':'DO NOT EXPORT','base_url':'PRIVATE URL'},'profile':'PRIVATE CONTEXT'})))
    with TestClient(module.app) as client:
        paths = ['/api/owner/observability','/api/owner/conversations','/api/owner/conversations/'+thread['id']]
        for path in paths:
            assert client.get(path).status_code == 401
            assert client.get(path,headers={'Authorization':'Bearer wrong'}).status_code == 401
            assert client.get(path,headers=AUTH).headers['cache-control'] == 'no-store'
        data = client.get(paths[-1],headers=AUTH).json()
        turn = data['turns'][0]
        assert turn['prompt'] == 'synthetic question' and turn['error'] == 'Synthetic failure'
        assert len(turn['events']) == 205  # Public chat view truncates; owner view must not.
        assert turn['tools'][0]['arguments'] == '{"query":"synthetic"}'
        assert turn['transcript'][0]['content'] == 'synthetic question'
        assert turn['model'] == {'provider':'ollama','model':'fake'}
        assert not any(s in json.dumps(data) for s in ('DO NOT EXPORT','PRIVATE URL','PRIVATE CONTEXT'))
        assert client.get('/api/owner/conversations/'+private['id'],headers=AUTH).json()['mode'] == 'private'
        assert client.get('/api/owner/conversations/missing',headers=AUTH).status_code == 404
        assert '/api/owner/conversations' not in client.get('/api/public/openapi.json').text
        client.cookies.clear()
        assert client.get('/api/owner/conversations',headers=AUTH).status_code == 200  # Independent of visitor cookie.
        monkeypatch.delenv('OWNER_STUDIO_TOKEN')
        assert client.get('/api/owner/conversations',headers=AUTH).status_code == 404


def test_keyset_pagination_search_and_legacy_database(tmp_path, monkeypatch):
    module, store = setup(tmp_path, monkeypatch)
    threads = [store.thread('public') for _ in range(5)]
    for i,thread in enumerate(threads):store.enqueue(thread['id'],f'Question {i}')
    with TestClient(module.app) as client:
        first = client.get('/api/owner/conversations?limit=2',headers=AUTH).json()
        newer = store.thread('public');store.enqueue(newer['id'],'newest')
        second = client.get('/api/owner/conversations',params={'limit':2,'before':first['next']},headers=AUTH).json()
        third = client.get('/api/owner/conversations',params={'limit':2,'before':second['next']},headers=AUTH).json()
        ids = [r['id'] for page in (first,second,third) for r in page['conversations']]
        assert len(ids) == 5 and len(set(ids)) == 5 and third['next'] is None
        match = client.get('/api/owner/conversations?q=Question%202',headers=AUTH).json()
        assert len(match['conversations']) == 1 and match['conversations'][0]['id'] == threads[2]['id']
        assert not client.get('/api/owner/conversations',params={'q':"' OR 1=1 --"},headers=AUTH).json()['conversations']
        assert client.get('/api/owner/conversations?limit=101',headers=AUTH).status_code == 422


def test_metrics_and_content_free_posthog_relay(tmp_path, monkeypatch):
    module, store = setup(tmp_path, monkeypatch)
    monkeypatch.setenv('POSTHOG_PROJECT_TOKEN','test-project-token')
    monkeypatch.setenv('POSTHOG_REGION','eu')
    sent = []
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):sent.append((url,kwargs['json']))
    monkeypatch.setattr('harness.observability.httpx.AsyncClient',Client)
    payload = {'name':'pageview','url':'/secret-name?token=SECRET#bookshelf','referrer':'https://secret.example/private','session_id':'a'*32,'utm':{'utm_source':'private-company'}}
    with TestClient(module.app) as client:
        assert client.post('/api/visit-events',json=payload).status_code == 204
        assert client.post('/api/visit-events',json=payload).status_code == 204
        assert client.post('/api/visit-events',json=payload,headers={'DNT':'1'}).status_code == 204
        assert client.post('/api/visit-events',json=payload,headers={'Sec-GPC':'1'}).status_code == 204
        assert client.post('/api/visit-events',json={**payload,'name':'arbitrary prompt'}).status_code == 422
        assert client.post('/api/visit-events',json={**payload,'session_id':'email@example.test'}).status_code == 422
        metrics = client.get('/api/owner/observability',headers=AUTH).json()
        assert metrics['sessions'] == 1 and metrics['events'] == [{'name':'pageview','count':2}]
        assert metrics['sections'] == [{'section':'bookshelf','count':2}]
        assert metrics['posthog_configured'] and len(sent) == 2
        assert sent[0][0] == 'https://eu.i.posthog.com/i/v0/e/'
        exported = json.dumps(sent)+json.dumps(store.query('SELECT * FROM ui_events'))
        for secret in ('SECRET','secret-name','secret.example','private-company','a'*32):assert secret not in exported
        assert sent[0][1]['properties']['$geoip_disable'] is True
        assert sent[0][1]['event'] == '$pageview'
        assert sent[0][1]['properties']['$pathname'] == '/'
        assert sent[0][1]['properties']['$current_url'].endswith('/#bookshelf')
        monkeypatch.delenv('POSTHOG_PROJECT_TOKEN')
        assert client.post('/api/visit-events',json=payload).status_code == 204 and len(sent) == 2
        assert client.get('/api/owner/observability',headers=AUTH).json()['events'][0]['count'] == 3


def test_posthog_safe_pages_and_hashed_v7_session(tmp_path, monkeypatch):
    from harness.observability import record_ui, initialize
    from harness.visits import VisitEvent
    module, store = setup(tmp_path, monkeypatch)
    initialize(store)
    monkeypatch.setenv('PUBLIC_ORIGIN', 'https://portfolio.example')
    session = '019a548012347abc8def0123456789ab'
    events = [record_ui(store, VisitEvent(name=name, url=url, session_id=session))
              for name, url in [('pageview','/hi/private-person?utm_content=secret#work'),
                                ('cta_click','/agents?token=secret#arbitrary-secret')]]
    properties = [e['properties'] for e in events]
    assert events[0]['event'] == '$pageview' and events[1]['event'] == 'cta_click'
    assert properties[0]['$current_url'] == 'https://portfolio.example/#work'
    assert properties[1]['$current_url'] == 'https://portfolio.example/agents'
    assert properties[0]['$session_id'] == properties[1]['$session_id']
    ident = uuid.UUID(properties[0]['$session_id'])
    assert ident.version == 7 and ident.hex[:12] == session[:12]
    assert ident.hex != session
    assert events[0]['distinct_id'] == events[1]['distinct_id']
    assert not any(s in json.dumps(events) for s in ('private-person','secret',session))


def test_metrics_retention_and_upstream_failure_do_not_break_chat(tmp_path, monkeypatch):
    module, store = setup(tmp_path, monkeypatch)
    monkeypatch.setenv('POSTHOG_PROJECT_TOKEN','test-project-token')
    class FailingClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):raise httpx.ConnectError('synthetic unavailable')
        async def __aexit__(self,*args):pass
    monkeypatch.setattr('harness.observability.httpx.AsyncClient',FailingClient)
    with TestClient(module.app) as client:
        with store.connect() as db:
            db.executemany('INSERT INTO ui_events(created,session,name,section,target,theme) VALUES (?,?,?,?,?,?)',[(time.time(),'','pageview','conversation','','')]*50002)
            db.execute('INSERT INTO ui_events(created,session,name,section,target,theme) VALUES (?,?,?,?,?,?)',(1,'','pageview','conversation','',''))
        assert client.post('/api/visit-events',json={'name':'pageview','url':'/'}).status_code == 204
        assert store.query('SELECT COUNT(*) AS n,MIN(created) AS oldest FROM ui_events')[0]['n'] == 50000
        assert store.query('SELECT MIN(created) AS oldest FROM ui_events')[0]['oldest'] > 1
        assert client.post('/api/threads',json={'mode':'public'}).status_code == 200
        assert client.get('/healthz').status_code == 200


def test_admin_tracking_switch_stops_local_metrics_and_posthog(tmp_path, monkeypatch):
    module, store = setup(tmp_path, monkeypatch)
    from harness.site_config import Settings, save
    save('settings', Settings(tracking_enabled=False).model_dump(), 0)
    with TestClient(module.app) as client:
        assert client.get('/api/visit-config').json()['enabled'] is False
        assert client.post('/api/visit-events',json={'name':'pageview','url':'/'}).status_code == 204
        assert store.query('SELECT * FROM ui_events') == []
        save('settings', Settings(tracking_enabled=True).model_dump(), 1)
        assert client.post('/api/visit-events',json={'name':'pageview','url':'/'}).status_code == 204
        assert len(store.query('SELECT * FROM ui_events')) == 1
