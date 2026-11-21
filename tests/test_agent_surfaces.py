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
        # A body-only extractor must see a usable path without JS or head metadata.
        from html.parser import HTMLParser
        class BodyText(HTMLParser):
            def __init__(self):super().__init__();self.body=False;self.skip=0;self.text=[]
            def handle_starttag(self,tag,attrs):
                if tag=='body':self.body=True
                if tag in ('script','style','noscript'):self.skip+=1
            def handle_endtag(self,tag):
                if tag=='body':self.body=False
                if tag in ('script','style','noscript'):self.skip-=1
            def handle_data(self,data):
                if self.body and not self.skip:self.text.append(data)
        text=BodyText();text.feed(home.text)
        assert 'Start here: /agents.md' in ''.join(text.text)
        md=c.get('/',headers={'Accept':'text/markdown'})
        assert md.headers['content-type'].startswith('text/markdown') and '/api/public/profile' in md.text
        assert 'Accept' in md.headers['vary'] and 'Accept' in home.headers['vary']
        assert c.get('/',headers={'Accept':'text/html, text/markdown;q=0.5'}).headers['content-type'].startswith('text/html')
        assert c.get('/',headers={'Accept':'text/markdown;q=0'}).headers['content-type'].startswith('text/html')
        assert '/sitemap.xml' in c.get('/robots.txt').text and '/agents.md' in c.get('/robots.txt').text
        import xml.etree.ElementTree as ET
        sitemap=ET.fromstring(c.get('/sitemap.xml').text)
        assert {n.text for n in sitemap.iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')} == {
            'http://testserver/', 'http://testserver/agents',
        }
        assert c.get('/about').status_code == 404
        assert c.get('/writing/harness-over-headcount').status_code == 404
        assert c.get('/writing/personal-agent-harness-restart-benchmark').status_code == 404
        assert c.get('/.well-known/agent-interface.json').json()['mcp']['url']=='/mcp'
        for path in ['/agents','/agents.md','/llms.txt','/.well-known/api-catalog','/api/public/openapi.json','/api/public/tools','/api/public/profile','/api/public/knowledge?query=leadership','/api/public/bookshelf']:
            r=c.get(path);assert r.status_code==200,(path,r.text)
            assert 'PRIVATE' not in r.text,path
        spec=c.get('/api/public/openapi.json').json()
        assert spec['paths'] and all(p.startswith('/api/public/') for p in spec['paths'])
        assert '/api/owner' not in c.get('/openapi.json').text
        tools=c.get('/api/public/tools').json()['tools']
        assert {t['name'] for t in tools}=={'get_profile','search_knowledge','browse_bookshelf','search_library','ask_portfolio_agent','get_answer'}
        assert 'ctx' not in next(t for t in tools if t['name']=='ask_portfolio_agent')['inputSchema']['properties']
        assert c.get('/api/public/bookshelf?query=PRIVATE').json()['books']==[]
        assert c.get('/api/public/knowledge?query=leadership&limit=99').status_code==422
        assert c.get('/api/public/knowledge?query=x&scope=private').json()['scope']=='public'
        assert c.post('/api/public/questions',json={'question':'hello','mode':'private'}).status_code==422


def test_public_reading_pages_and_canonical_sitemap_behind_fly(tmp_path, monkeypatch):
    from xml.etree import ElementTree

    module, store = fixture_app(tmp_path, monkeypatch)
    origin = 'https://portfolio.example.test'
    monkeypatch.setenv('PUBLIC_ORIGIN', origin)
    monkeypatch.setenv('FLY_APP_NAME', 'fixture-portfolio')
    (store.brain.scope('public') / 'portfolio.json').write_text(json.dumps({
        'name': 'Avery <Lee>', 'role': 'Engineering Lead', 'location': 'Bay Area',
        'summary': 'This bio is available as HTML.', 'linkedin': 'https://example.test/avery',
        'projects': [{'id': 'factory', 'eyebrow': 'Case study', 'title': 'Harness over headcount',
                      'company': 'Fixture', 'description': 'A study of review throughput.',
                      'detail': 'The team counted completed reviews.', 'outcome': 'Twenty reviews', 'question': ''}],
    }))
    case = '/writing/harness-over-headcount'
    benchmark = '/writing/personal-agent-harness-restart-benchmark'
    # The ASGI request has an HTTP base URL after TLS is terminated upstream.
    with TestClient(module.app, headers={'Fly-Forwarded-Proto': 'https'}) as c:
        urls = ElementTree.fromstring(c.get('/sitemap.xml').text)
        assert {item.text for item in urls.iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')} == {
            origin + path for path in ('/', '/about', '/agents', case, benchmark)
        }
        assert f'Sitemap: {origin}/sitemap.xml' in c.get('/robots.txt').text
        home, bio, article, report = (c.get(path) for path in ('/', '/about', case, benchmark))
        for path, response in zip(('/', '/about', case, benchmark), (home, bio, article, report)):
            assert response.status_code == 200
            assert f'rel="canonical" href="{origin}{path}"' in response.text
            assert 'PRIVATE PROFILE CANARY' not in response.text
        assert 'href="/about">About Avery &lt;Lee&gt;' in home.text
        assert '<h1>Avery &lt;Lee&gt;</h1>' in bio.text
        assert 'This bio is available as HTML.' in bio.text
        assert 'Avery <Lee>' not in bio.text
        assert '"sameAs": ["https://example.test/avery"]' in bio.text
        assert f'href="{case}">Harness over headcount' in bio.text
        assert '<h1>Harness over headcount</h1>' in article.text
        assert 'The team counted completed reviews.' in article.text
        assert 'Human approval' in article.text and 'href="/about">Avery &lt;Lee&gt;' in article.text
        for response in (home, bio, article):
            assert f'href="{benchmark}">Personal Agent Harness restart benchmark' in response.text
        assert '<h1>Personal Agent Harness restart benchmark</h1>' in report.text
        assert 'Across 18 trials' in report.text and 'Reproduce the benchmark' in report.text
        assert 'uv sync --locked' in report.text and 'git checkout dd0a21ef' in report.text
        assert '<th scope="row">Restart after commit, with deduplication</th><td>2</td><td>1</td>' in report.text
        assert '<th scope="row">Restart after commit, without deduplication</th><td>2</td><td>2</td>' in report.text
        assert '"@type": "TechArticle"' in report.text and 'Avery &lt;Lee&gt;' in report.text
        assert c.head(case).status_code == 200
        assert c.head(benchmark).status_code == 200
        assert c.get('/portfolio-reading.css').headers['content-type'].startswith('text/css')
        assert case not in c.get('/hi/unknown').text


def test_anonymous_question_tokens_scoping_and_rate_limit(tmp_path,monkeypatch):
    module,store=fixture_app(tmp_path,monkeypatch)
    monkeypatch.setenv('PUBLIC_DAILY_TURNS','1')
    with TestClient(module.app) as first:
        second=TestClient(module.app)
        r=first.post('/api/public/questions',json={'question':'What does the public profile say?'})
        assert r.status_code==202,r.text
        body=r.json();assert len(body['result_token'])==64
        assert body['speaker']['type']=='ai_digital_twin'
        assert body['speaker']['voice']=='first_person'
        assert first.get(body['status_url']).status_code==422
        assert second.get(body['status_url'],headers={'X-Result-Token':'0'*64}).status_code==404
        assert first.get(body['status_url'],headers={'X-Result-Token':body['result_token']}).json()['status']=='queued'
        with store.connect() as db:db.execute("UPDATE turns SET status='completed',result='Fixture public answer' WHERE id=?",(body['turn_id'],))
        result=first.get(body['status_url'],headers={'X-Result-Token':body['result_token']})
        assert result.json()['answer']=='Fixture public answer'
        assert result.json()['speaker']==body['speaker']
        assert 'not a verbatim statement' in result.json()['speaker']['attribution']
        assert 'first person' in first.get('/agents.md').text
        assert 'digital twin' in first.get('/agents').text
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
        assert len(tools)==6
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
