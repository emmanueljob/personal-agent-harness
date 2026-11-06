import json
import socket

import httpx
import pytest

from harness import web_tools as web
from harness.store import Store


def test_repeated_tool_calls_force_an_answer_from_existing_evidence():
    from harness.agent import finish_bounded_research
    from langchain.agents.middleware.types import ModelRequest
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.messages import ToolMessage, SystemMessage
    request = ModelRequest(
        model=FakeListChatModel(responses=['answer']),
        messages=[ToolMessage(content='evidence', tool_call_id=str(i)) for i in range(4)],
        tools=[{'name': 'search_knowledge'}],
        system_message=SystemMessage(content='Keep facts grounded.'),
    )
    bounded = finish_bounded_research.wrap_model_call(request, lambda value: value)
    assert bounded.tools == []
    assert 'Keep facts grounded.' in bounded.system_message.content
    assert 'Answer now' in bounded.system_message.content
    assert all(not isinstance(m, ToolMessage) for m in bounded.messages)
    assert 'evidence' in bounded.messages[-1].content
    earlier = request.override(messages=request.messages[:3])
    assert finish_bounded_research.wrap_model_call(earlier, lambda value: value).tools == request.tools
    oversized = request.override(messages=[ToolMessage(content='x'*50000, tool_call_id=str(i)) for i in range(4)])
    bounded = finish_bounded_research.wrap_model_call(oversized, lambda value: value)
    assert sum(len(message.content) for message in bounded.messages) < 12500
    assert all(not isinstance(m, ToolMessage) for m in bounded.messages)


@pytest.mark.parametrize('url', ['http://localhost/', 'http://127.0.0.1:8000/', 'http://[::1]/', 'http://169.254.169.254/', 'http://192.168.1.1/', 'http://224.0.0.1/', 'https://private.internal/', 'file:///etc/passwd', 'https://user:password@example.org/', 'https://example.org:8443/'])
def test_reader_rejects_nonpublic_destinations(url):
    with pytest.raises(ValueError):
        web.public_url(url)


def test_dns_mixed_private_addresses_are_rejected(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [(2,1,6,'',('8.8.8.8',443)), (2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(ValueError):
        web.public_address('https://example.org/')


def reader_transport(monkeypatch, handler):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [(2,1,6,'',('8.8.8.8',443))])
    original = httpx.Client
    monkeypatch.setattr(web.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


def test_reader_pins_checked_ip_keeps_tls_identity_and_extracts_text(monkeypatch):
    def handler(request):
        assert request.url.host == '8.8.8.8'
        assert request.headers['host'] == 'publisher.example'
        assert request.extensions['sni_hostname'] == 'publisher.example'
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        return httpx.Response(200, headers={'content-type':'text/html'}, text='<title>Original source</title><script>steal secrets</script><main><h1>Useful research</h1><p>A practical example.</p></main>')
    reader_transport(monkeypatch, handler)
    result = web.read_public_page('https://publisher.example/article')
    assert 'practical example' in result['text']
    assert 'steal secrets' not in result['text']
    assert result['sources'][0]['url'] == 'https://publisher.example/article'


def test_redirect_to_private_network_is_not_followed(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={'location':'http://127.0.0.1/secrets'})
    reader_transport(monkeypatch, handler)
    with pytest.raises(ValueError):
        web.read_public_page('https://publisher.example/article')
    assert len(requests) == 1


def test_reader_rejects_large_or_binary_responses(monkeypatch):
    reader_transport(monkeypatch, lambda r: httpx.Response(200, headers={'content-type':'application/pdf'}, content=b'%PDF fake'))
    with pytest.raises(ValueError):
        web.read_public_page('https://publisher.example/article')


def test_search_filters_bad_links_and_bounds_snippets(monkeypatch):
    import ddgs
    class Search:
        def text(self, *args, **kwargs):
            return [{'title':'Unsafe', 'href':'javascript:alert(1)', 'body':'bad'}, {'title':'Local', 'href':'http://127.0.0.1/', 'body':'bad'}, {'title':'Source', 'href':'https://publisher.example/a', 'body':'x'*2000}]
    monkeypatch.setenv('WEB_SEARCH_PROVIDER','ddgs')
    monkeypatch.setattr(ddgs,'DDGS',lambda **kw: Search())
    result=web.search_public_web('public research')
    assert len(result['sources'])==1
    assert len(result['sources'][0]['snippet'])==450


def test_durable_cache_is_turn_scoped_and_bounded(tmp_path):
    s=Store(tmp_path)
    tid=s.thread('public')['id'];turn=s.enqueue(tid,'research')
    ctx={'store':s,'turn_id':turn};calls=[]
    def operation():
        calls.append(1)
        return {'status':'ok','sources':[{'title':'Source','url':'https://example.org/'}]}
    first=web.cached_call(ctx,'search_web',{'query':'same'},operation)
    again=web.cached_call({**ctx,'store':Store(tmp_path)},'search_web',{'query':'same'},operation)
    assert first['status']=='ok' and again['cached']
    assert len(calls)==1
    other=s.enqueue(tid,'new turn')
    web.cached_call({**ctx,'turn_id':other},'search_web',{'query':'same'},operation)
    assert len(calls)==2
    for query in ['two','three','four']:
        web.cached_call(ctx,'search_web',{'query':query},operation)
    assert web.cached_call(ctx,'search_web',{'query':'five'},operation)['status']=='error'
    assert len(calls)==5


def test_provider_errors_do_not_expose_sensitive_diagnostics(tmp_path):
    s=Store(tmp_path)
    def failed():
        raise RuntimeError('secret-token-in-upstream-error')
    result=web.cached_call({'store':s,'turn_id':'t'},'search_web',{'query':'public'},failed)
    assert result['status']=='error' and 'secret-token' not in json.dumps(result)


def test_failed_searches_are_durable_cached_and_budgeted(tmp_path):
    store=Store(tmp_path);calls=[]
    ctx={'store':store,'turn_id':store.enqueue(store.thread('public')['id'],'A synthetic question')}
    def fail():
        calls.append(1)
        raise RuntimeError('PRIVATE diagnostics must not be persisted')
    web.cached_call(ctx,'search_web',{'query':'one'},fail)
    retried=web.cached_call({**ctx,'store':Store(tmp_path)},'search_web',{'query':'one'},fail)
    assert retried['cached'] and retried['status']=='error' and len(calls)==1
    for query in ('two','three','four','five'):
        web.cached_call(ctx,'search_web',{'query':query},fail)
    assert len(calls)==4
    saved=store.query('SELECT body FROM tool_results')
    assert len(saved)==4 and 'PRIVATE' not in json.dumps(saved)


def test_openai_search_contract_sources_and_no_personal_context(tmp_path,monkeypatch):
    monkeypatch.setenv('WEB_SEARCH_PROVIDER','openai')
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-test-key')
    monkeypatch.setenv('OPENAI_MODEL','synthetic-search-model')
    monkeypatch.setenv('OPENAI_SEARCH_MODEL','')
    monkeypatch.setenv('PUBLIC_ONLY','true')
    requests=[]
    def handler(request):
        requests.append(request)
        body=json.loads(request.content)
        assert body['model']=='synthetic-search-model' and body['input']=='public topic'
        assert body['store'] is False and body['tool_choice']=='required' and body['max_tool_calls']==1
        assert body['tools']==[{'type':'web_search','search_context_size':'low'}]
        assert body['max_output_tokens']==1500
        assert 'PRIVATE' not in request.content.decode()
        return httpx.Response(200,json={'output':[
            {'type':'web_search_call','status':'completed','action':{'sources':[
                {'url':'http://127.0.0.1/secret'}, {'url':'https://publisher.example/article'}]}},
            {'type':'message','content':[{'type':'output_text','text':'A short search summary.','annotations':[
                {'type':'url_citation','title':'Original source','url':'https://publisher.example/article'}]}]},
        ]})
    original=httpx.Client
    monkeypatch.setattr(web.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    store=Store(tmp_path);store.set_profile('private','PRIVATE profile canary')
    ctx={'store':store,'turn_id':store.enqueue(store.thread('public')['id'],'public topic'),'mode':'public','web_enabled':True}
    search=next(t for t in web.web_tools(ctx) if t.name=='search_web')
    result=json.loads(search.invoke({'query':'public topic'}))
    assert result['provider']=='openai' and result['sources']==[{'title':'Original source','url':'https://publisher.example/article','snippet':''}]
    assert result['summary']=='A short search summary.'
    assert 'synthetic-test-key' not in json.dumps(store.query('SELECT * FROM tool_results'))
    assert json.loads(search.invoke({'query':'public topic'}))['cached']
    assert len(requests)==1
    assert store.query("SELECT count FROM public_usage WHERE bucket='day:model'")[0]['count']==1


def test_openai_search_requires_configuration_and_a_completed_search(monkeypatch):
    monkeypatch.setenv('WEB_SEARCH_PROVIDER','openai')
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    with pytest.raises(web.WebInputError,match='not configured'):web.search_public_web('public topic')
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-test-key')
    original=httpx.Client
    monkeypatch.setattr(web.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'output':[]})),**kw))
    with pytest.raises(web.WebInputError,match='did not complete'):web.search_public_web('public topic')


def test_web_tools_can_be_disabled(tmp_path,monkeypatch):
    monkeypatch.setenv('WEB_SEARCH_PROVIDER','disabled')
    assert web.web_tools({'store':Store(tmp_path)})==[]


def test_source_traces_respect_thread_authorization(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from harness import app as module
    s=Store(tmp_path);monkeypatch.setattr(module,'store',s);monkeypatch.setenv('OWNER_TOKEN','owner')
    pub=s.thread('public')['id'];priv=s.thread('private')['id']
    for tid,label in [(pub,'public'),(priv,'private')]:
        turn=s.enqueue(tid,'research')
        web.cached_call({'store':s,'turn_id':turn},'read_webpage',{'url':'https://example.org/'+label},lambda:{'status':'ok','sources':[{'title':label,'url':'https://example.org/'+label}]})
    c=TestClient(module.app)
    data=c.get('/api/threads/'+pub).json()
    assert data['tool_calls'][0]['sources'][0]['title']=='public'
    assert 'private' not in json.dumps(data)
    assert c.get('/api/threads/'+priv).status_code==401
    assert c.get('/api/threads/'+priv,headers={'Authorization':'Bearer owner'}).json()['tool_calls'][0]['sources'][0]['title']=='private'
