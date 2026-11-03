import asyncio
import json
import time
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from harness import model_bridge, models
from harness.public_access import PublicAccess


@pytest.fixture
def bridge(tmp_path,monkeypatch):
    monkeypatch.setenv('DATA_DIR',str(tmp_path))
    monkeypatch.setenv('MODEL_BRIDGE_TOKEN','test-bridge-token')
    monkeypatch.setenv('OLLAMA_MODEL','fixture-local')
    app=FastAPI();model_bridge.install(app)
    return app, {'Authorization':'Bearer test-bridge-token'}


def test_bridge_auth_heartbeat_and_offline_fallback(bridge,monkeypatch):
    app,headers=bridge
    monkeypatch.setenv('MODEL_PROVIDER','hybrid')
    with TestClient(app) as c:
        assert c.post('/api/model-bridge/claim',json={}).status_code==401
        assert c.post('/api/model-bridge/heartbeat',json={'model':'fixture-local','online':True}).status_code==401
        assert models.settings()['provider']=='openai'
        assert c.post('/api/model-bridge/heartbeat',headers=headers,json={'model':'other','online':True}).status_code==422
        assert c.post('/api/model-bridge/heartbeat',headers=headers,json={'model':'fixture-local','online':True}).status_code==200
        config=models.settings()
        assert config['provider']=='ollama' and config['bridged']
        assert 'test-bridge-token' not in json.dumps(config)
        with model_bridge.connect() as db:db.execute('UPDATE heartbeat SET seen=?',(time.time()-30,))
        assert models.settings()['provider']=='openai'
        assert c.post('/api/model-bridge/api/chat',headers=headers,json={}).status_code==403
        assert '/api/model-bridge' not in c.get('/openapi.json').text


def test_model_job_roundtrip_claim_isolation_and_cleanup(bridge):
    app,headers=bridge
    async def check():
        transport=httpx.ASGITransport(app=app,client=('127.0.0.1',123))
        async with httpx.AsyncClient(transport=transport,base_url='http://localhost') as c:
            await c.post('/api/model-bridge/heartbeat',headers=headers,json={'model':'fixture-local','online':True})
            inference=asyncio.create_task(c.post('/api/model-bridge/api/chat',headers=headers,json={'model':'fixture-local','messages':[{'role':'user','content':'synthetic question'}],'stream':True,'url':'https://never-forward.example'}))
            try:
                for _ in range(20):
                    job=(await c.post('/api/model-bridge/claim',headers=headers,json={})).json()['job']
                    if job:break
                    await asyncio.sleep(.02)
                assert job and 'url' not in job['payload'] and job['payload']['stream'] is False
                assert (await c.post('/api/model-bridge/claim',headers=headers,json={})).json()['job'] is None
                result={'model':'fixture-local','message':{'role':'assistant','content':'synthetic answer'},'done':True}
                assert (await c.post('/api/model-bridge/jobs/'+job['id'],headers=headers,json={'claim':'wrong','result':result})).status_code==404
                assert (await c.post('/api/model-bridge/jobs/'+job['id'],headers=headers,json={'claim':job['claim'],'result':result})).status_code==200
                response=await inference
                assert response.status_code==200 and response.json()['message']['content']=='synthetic answer'
                assert response.headers['content-type']=='application/x-ndjson'
                with model_bridge.connect() as db:assert db.execute('SELECT count(*) FROM jobs').fetchone()[0]==0
            finally:
                if not inference.done():inference.cancel()
    asyncio.run(check())


def test_canonical_redirect_preserves_path_and_cannot_be_host_injected(monkeypatch):
    monkeypatch.setenv('REDIRECT_HOSTS','example.org')
    monkeypatch.setenv('PUBLIC_ORIGIN','https://eman.example.org')
    monkeypatch.setenv('PUBLIC_ONLY','false')
    app=FastAPI();app.add_middleware(PublicAccess)
    @app.get('/healthz')
    def health():return {'status':'ok'}
    with TestClient(app,base_url='https://example.org',follow_redirects=False) as c:
        r=c.get('/books?q=one')
        assert r.status_code==308 and r.headers['location']=='https://eman.example.org/books?q=one'
        assert c.post('/books').status_code==421
        assert c.get('/healthz',headers={'Host':'evil.example'}).status_code==200


def test_ollama_factory_adds_bridge_auth_without_snapshotting_credentials(bridge,monkeypatch):
    import langchain_ollama
    calls=[]
    monkeypatch.setattr(langchain_ollama,'ChatOllama',lambda **kw:calls.append(kw) or kw)
    models.local_model({'provider':'ollama','model':'fixture-local','bridged':True,'base_url':'http://127.0.0.1:8080/api/model-bridge'})
    assert calls[0]['client_kwargs']['headers']['Authorization']=='Bearer test-bridge-token'
