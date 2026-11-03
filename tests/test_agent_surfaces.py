import asyncio
import importlib
import json
from fastapi.testclient import TestClient
from harness.store import Store


def fixture_app(tmp_path,monkeypatch):
    monkeypatch.setenv('DATA_DIR',str(tmp_path/'state'))
    monkeypatch.setenv('BRAIN_DIR',str(tmp_path/'brain'))
    monkeypatch.setenv('BRAIN_BACKEND','files')
    monkeypatch.setenv('PUBLIC_ONLY','true')
    monkeypatch.setenv('PUBLIC_ORIGIN','http://testserver')
    monkeypatch.setenv('EXECUTOR','local')
    monkeypatch.setenv('AGENT_RUNTIME','legacy')
    monkeypatch.setenv('MODEL_PROVIDER','ollama')
    module=importlib.import_module('harness.app')
    store=Store(tmp_path/'state')
    store.set_profile('public','Public fixture leader bridges engineering and product.')
    store.set_profile('private','PRIVATE PROFILE CANARY never publish this text.')
    store.brain.add_note('Leadership','Leadership means the public fixture leader values trust and autonomy.','public')
    store.brain.add_note('Private leadership','PRIVATE NOTE CANARY hidden preferences.','private')
    (store.brain.scope('public')/'bookshelf.json').write_text(json.dumps({'books':[{'id':'public-book','title':'Fixture Systems','author':'Fixture Author','comment':'A published note','comment_source':'Owner interview','sample':False}]}))
    (store.brain.scope('private')/'bookshelf.json').write_text(json.dumps({'books':[{'id':'private-book','title':'PRIVATE BOOK CANARY'}]}))
    monkeypatch.setattr(module,'store',store)
    async def idle(store):await asyncio.sleep(3600)
    monkeypatch.setattr(module,'local_worker',idle)
    return module,store


def test_discovery_contracts_public_reads_and_no_private_exposure(tmp_path,monkeypatch):
    module,store=fixture_app(tmp_path,monkeypatch)
    with TestClient(module.app) as c:
        home=c.get('/')
        assert '/agents' in home.text and 'service-desc' in home.headers['link']
        for path in ['/agents','/agents.md','/llms.txt','/.well-known/api-catalog','/api/public/openapi.json','/api/public/tools','/api/public/profile','/api/public/knowledge?query=leadership','/api/public/bookshelf']:
            r=c.get(path);assert r.status_code==200,(path,r.text)
            assert 'PRIVATE' not in r.text,path
        spec=c.get('/api/public/openapi.json').json()
        assert spec['paths'] and all(p.startswith('/api/public/') for p in spec['paths'])
        assert '/api/owner' not in c.get('/openapi.json').text
        tools=c.get('/api/public/tools').json()['tools']
        assert {t['name'] for t in tools}=={'get_profile','search_knowledge','browse_bookshelf','ask_portfolio_agent','get_answer'}
        assert 'ctx' not in next(t for t in tools if t['name']=='ask_portfolio_agent')['inputSchema']['properties']
        assert c.get('/api/public/bookshelf?query=PRIVATE').json()['books']==[]
        assert c.get('/api/public/knowledge?query=leadership&limit=99').status_code==422
        assert c.get('/api/public/knowledge?query=x&scope=private').json()['scope']=='public'
        assert c.post('/api/public/questions',json={'question':'hello','mode':'private'}).status_code==422


def test_anonymous_question_tokens_scoping_and_rate_limit(tmp_path,monkeypatch):
    module,store=fixture_app(tmp_path,monkeypatch)
    monkeypatch.setenv('PUBLIC_DAILY_TURNS','1')
    with TestClient(module.app) as first:
        second=TestClient(module.app)
        r=first.post('/api/public/questions',json={'question':'What does the public profile say?'})
        assert r.status_code==202,r.text
        body=r.json();assert len(body['result_token'])==64
        assert first.get(body['status_url']).status_code==422
        assert second.get(body['status_url'],headers={'X-Result-Token':'0'*64}).status_code==404
        assert first.get(body['status_url'],headers={'X-Result-Token':body['result_token']}).json()['status']=='queued'
        with store.connect() as db:db.execute("UPDATE turns SET status='completed',result='Fixture public answer' WHERE id=?",(body['turn_id'],))
        result=first.get(body['status_url'],headers={'X-Result-Token':body['result_token']})
        assert result.json()['answer']=='Fixture public answer'
        assert 'prompt' not in result.json() and result.headers['cache-control']=='no-store'
        raw=store.query('SELECT token_hash FROM public_agent_questions')[0]['token_hash']
        assert raw!=body['result_token']
        assert first.post('/api/public/questions',json={'question':'Second'}).status_code==429
        thread=store.query('SELECT thread_id FROM turns WHERE id=?',(body['turn_id'],))[0]['thread_id']
        assert second.get('/api/threads/'+thread).status_code==404
        with store.connect() as db:db.execute('UPDATE public_agent_questions SET expires=0')
        assert first.get(body['status_url'],headers={'X-Result-Token':body['result_token']}).status_code==404


def test_mcp_protocol_scope_validation_and_origin_protection(tmp_path,monkeypatch):
    module,store=fixture_app(tmp_path,monkeypatch)
    headers={'Accept':'application/json, text/event-stream'}
    def call(c,method,params):
        r=c.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':1,'method':method,'params':params})
        assert r.status_code==200,r.text
        return r.json()['result']
    with TestClient(module.app) as c:
        initialized=call(c,'initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'fixture','version':'1'}})
        assert 'tools' in initialized['capabilities']
        tools=call(c,'tools/list',{})['tools']
        assert len(tools)==5
        result=call(c,'tools/call',{'name':'search_knowledge','arguments':{'query':'leadership'}})
        assert not result.get('isError') and 'autonomy' in json.dumps(result)
        assert 'PRIVATE' not in json.dumps(result)
        invalid=call(c,'tools/call',{'name':'search_knowledge','arguments':{'query':'x'*201}})
        assert invalid.get('isError')
        blocked=call(c,'tools/call',{'name':'save_artifact','arguments':{'title':'probe','markdown':'probe'}})
        assert blocked.get('isError') and not store.query('SELECT * FROM artifacts')
        assert c.post('/mcp',headers={**headers,'Origin':'https://untrusted.test'},json={'jsonrpc':'2.0','id':1,'method':'tools/list'}).status_code==403
        question=call(c,'tools/call',{'name':'ask_portfolio_agent','arguments':{'question':'A public question'}})
        assert not question.get('isError'),question
        assert 'result_token' in json.dumps(question)


def test_pagination_and_mcp_provider_errors_do_not_disclose_paths(tmp_path,monkeypatch):
    module,store=fixture_app(tmp_path,monkeypatch)
    (store.brain.scope('public')/'bookshelf.json').write_text(json.dumps({'books':[{'id':'book-'+str(i),'title':'Example '+str(i)} for i in range(3)]}))
    with TestClient(module.app) as c:
        page=c.get('/api/public/bookshelf?limit=2').json()
        assert page['next_offset']==2 and page['total_matches']==3
        last=c.get('/api/public/bookshelf?limit=2&offset=2').json()
        assert last['next_offset'] is None and last['books'][0]['id']=='book-2'
        def fail(*args):raise RuntimeError('/private/runtime/never-disclose source details')
        monkeypatch.setattr(store.brain,'search',fail)
        result=c.post('/mcp',headers={'Accept':'application/json, text/event-stream'},json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'search_knowledge','arguments':{'query':'test'}}}).json()['result']
        assert result['isError'] and 'never-disclose' not in json.dumps(result)
