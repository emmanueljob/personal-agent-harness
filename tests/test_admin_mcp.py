import asyncio
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt
import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from harness.admin_oauth import AdminOAuth, SCOPE, challenge
from test_agent_surfaces import fixture_app

HEADERS = {'Accept':'application/json, text/event-stream'}


def configure(tmp_path,monkeypatch):
    module,store = fixture_app(tmp_path,monkeypatch)
    monkeypatch.setenv('PUBLIC_ORIGIN','https://testserver')
    monkeypatch.setenv('OWNER_STUDIO_TOKEN','synthetic-owner-token')
    monkeypatch.setenv('ADMIN_GOOGLE_CLIENT_ID','synthetic-google-client')
    monkeypatch.setenv('ADMIN_GOOGLE_CLIENT_SECRET','synthetic-google-secret')
    monkeypatch.setenv('ADMIN_GOOGLE_OWNER_EMAIL','owner@example.test')
    private = rsa.generate_private_key(public_exponent=65537,key_size=2048)
    monkeypatch.setattr(jwt,'PyJWKClient',lambda *a,**kw:SimpleNamespace(get_signing_key_from_jwt=lambda token:SimpleNamespace(key=private.public_key())))
    return module,store,private


def connect(client,monkeypatch,private,email='owner@example.test',cookie=True):
    registration=client.post('/register',json={'client_name':'Synthetic owner client',
        'redirect_uris':['https://client.example/callback'],'token_endpoint_auth_method':'none',
        'grant_types':['authorization_code','refresh_token'],'response_types':['code'],'scope':SCOPE})
    assert registration.status_code==201,registration.text
    client_id=registration.json()['client_id']
    verifier='synthetic-pkce-verifier-for-test-12345678901234567890'
    response=client.get('/authorize',params={'response_type':'code','client_id':client_id,
        'redirect_uri':'https://client.example/callback','scope':SCOPE,'code_challenge':challenge(verifier),
        'code_challenge_method':'S256','resource':'https://testserver/admin-mcp','state':'client-state'},follow_redirects=False)
    assert response.status_code in (302,303),response.text
    target=response.headers['location'];state=parse_qs(urlsplit(target).query)['state'][0]
    consent=client.get(target)
    assert consent.status_code==200 and 'Synthetic owner client' in consent.text
    assert '/admin-auth/google?state=' in consent.text and '<form' not in consent.text
    assert 'visit.js' not in consent.text and 'client.example/callback' in consent.text
    assert 'httponly' in consent.headers['set-cookie'].lower() and 'secure' in consent.headers['set-cookie'].lower()
    # Opening or reloading the same consent in another browser does not destroy
    # this browser's session. All browsers still need to visit consent first.
    second=TestClient(client.app,base_url='https://testserver')
    assert second.get(target).status_code==200
    second_cookie=second.cookies.get('portfolio_admin_oauth')
    assert second_cookie!=client.cookies.get('portfolio_admin_oauth')
    assert second.get('/admin-auth/google',params={'state':state},follow_redirects=False).status_code==303
    assert client.get(target).status_code==200
    google=client.get('/admin-auth/google',params={'state':state},follow_redirects=False)
    assert google.status_code==303
    params=parse_qs(urlsplit(google.headers['location']).query)
    assert params['scope']==['openid email'] and params['code_challenge_method']==['S256']
    token=jwt.encode({'iss':'https://accounts.google.com','aud':'synthetic-google-client','sub':'synthetic-owner',
        'email':email,'email_verified':True,'nonce':params['nonce'][0],'iat':int(time.time()),'exp':int(time.time()+300)},private,algorithm='RS256')
    original=httpx.AsyncClient
    def upstream(request):
        assert str(request.url)=='https://oauth2.googleapis.com/token'
        body=parse_qs(request.content.decode())
        assert challenge(body['code_verifier'][0])==params['code_challenge'][0]
        return httpx.Response(200,json={'id_token':token,'access_token':'GOOGLE-TOKEN-MUST-NOT-PERSIST'})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(**({'transport':httpx.MockTransport(upstream)} if 'transport' not in kw else {}),**kw))
    if not cookie:client.cookies.clear()
    result=client.get('/admin-auth/callback',params={'state':state,'code':'synthetic-google-code'},follow_redirects=False)
    if email!='owner@example.test' or not cookie:
        assert result.status_code==403 and 'synthetic-google-secret' not in result.text
        return None
    assert result.status_code==303,result.text
    redirect=parse_qs(urlsplit(result.headers['location']).query)
    assert redirect['state']==['client-state']
    request={'grant_type':'authorization_code','client_id':client_id,'code':redirect['code'][0],
        'redirect_uri':'https://client.example/callback','code_verifier':verifier,'resource':'https://testserver/admin-mcp'}
    invalid=client.post('/token',data={**request,'code_verifier':'wrong-verifier'})
    assert invalid.status_code==400
    response=client.post('/token',data=request)
    assert response.status_code==200,response.text
    assert client.post('/token',data=request).status_code==400
    return {**response.json(),'client_id':client_id}


def rpc(client,name,args=None,token=None,path='/admin-mcp'):
    headers={**HEADERS,**({'Authorization':'Bearer '+token} if token else {})}
    return client.post(path,headers=headers,json={'jsonrpc':'2.0','id':1,'method':name,'params':args or {}})


def test_admin_oauth_mcp_scope_revisions_and_refresh_rotation(tmp_path,monkeypatch):
    module,store,private=configure(tmp_path,monkeypatch)
    with TestClient(module.app,base_url='https://testserver') as client:
        unauth=rpc(client,'tools/list')
        assert unauth.status_code==401 and 'resource_metadata=' in unauth.headers['www-authenticate']
        assert 'get_site_configuration' not in unauth.text
        metadata=client.get('/.well-known/oauth-protected-resource/admin-mcp').json()
        assert metadata['resource']=='https://testserver/admin-mcp'
        authorization=client.get('/.well-known/oauth-authorization-server').json()
        assert authorization['token_endpoint_auth_methods_supported']==['none']
        assert rpc(client,'tools/list',token='synthetic-owner-token').status_code==401
        tokens=connect(client,monkeypatch,private)
        access=tokens['access_token']
        tools=rpc(client,'tools/list',token=access).json()['result']['tools']
        names={t['name'] for t in tools}
        assert 'get_site_configuration' in names and 'publish_learning_note' in names
        assert not names & {'delete_conversation','run_shell','deploy','get_credentials'}
        public=rpc(client,'tools/list',path='/mcp').json()['result']['tools']
        assert not names & {t['name'] for t in public}
        result=rpc(client,'tools/call',{'name':'get_site_configuration','arguments':{}},access).json()['result']
        assert not result.get('isError'),result
        config=result['structuredContent']
        updated=rpc(client,'tools/call',{'name':'update_site_settings','arguments':{'edit':{'revision':config['settings_revision'],'value':config['settings']}}},access).json()['result']
        assert not updated.get('isError'),updated
        stale=rpc(client,'tools/call',{'name':'update_site_settings','arguments':{'edit':{'revision':config['settings_revision'],'value':config['settings']}}},access).json()['result']
        assert stale.get('isError')
        publish=rpc(client,'tools/call',{'name':'publish_learning_note','arguments':{'note_id':'synthetic-note','revision':'x'}},access).json()['result']
        assert publish.get('isError') and not store.brain.read('public','learning.json')
        assert 'private, no-store' in rpc(client,'tools/list',token=access).headers['cache-control']
        refreshed=client.post('/token',data={'grant_type':'refresh_token','client_id':tokens['client_id'],
            'refresh_token':tokens['refresh_token'],'resource':'https://testserver/admin-mcp'})
        assert refreshed.status_code==200,refreshed.text
        assert rpc(client,'tools/list',token=access).status_code==401
        assert client.post('/token',data={'grant_type':'refresh_token','client_id':tokens['client_id'],'refresh_token':tokens['refresh_token']}).status_code==400
        new=refreshed.json()
        assert rpc(client,'tools/list',token=new['access_token']).status_code==200
        revoked=client.post('/revoke',data={'token':new['refresh_token'],'client_id':tokens['client_id'],'token_type_hint':'refresh_token'})
        assert revoked.status_code==200,revoked.text
        assert rpc(client,'tools/list',token=new['access_token']).status_code==401
        oauth=AdminOAuth(lambda:store)
        with oauth.connect() as db:raw=' '.join(r['body'] for r in db.execute('SELECT body FROM oauth_records'))
        assert all(secret not in raw for secret in (access,new['access_token'],tokens['refresh_token'],'GOOGLE-TOKEN-MUST-NOT-PERSIST','synthetic-google-secret'))


@pytest.mark.parametrize('email,cookie',[('someone-else@example.test',True),('owner@example.test',False)])
def test_google_wrong_owner_and_missing_session_cookie_cannot_mint_tokens(tmp_path,monkeypatch,email,cookie):
    module,store,private=configure(tmp_path,monkeypatch)
    with TestClient(module.app,base_url='https://testserver') as client:
        connect(client,monkeypatch,private,email,cookie)
        oauth=AdminOAuth(lambda:store)
        with oauth.connect() as db:assert db.execute("SELECT count(*) FROM oauth_records WHERE kind IN ('access','code','refresh')").fetchone()[0]==0


def test_admin_mcp_is_disabled_without_google_configuration(tmp_path,monkeypatch):
    module,store=fixture_app(tmp_path,monkeypatch)
    monkeypatch.delenv('ADMIN_GOOGLE_CLIENT_ID',raising=False)
    with TestClient(module.app) as client:
        assert rpc(client,'tools/list').status_code==404
        assert client.get('/.well-known/oauth-authorization-server').status_code==404


def test_sign_in_link_still_requires_browser_binding_and_expires(tmp_path,monkeypatch):
    module,store,private=configure(tmp_path,monkeypatch)
    oauth=AdminOAuth(lambda:store)
    oauth.put('pending','synthetic-flow',{'nonce':'fixture','verifier':'fixture','cookie_hashes':[]},time.time()+60)
    with TestClient(module.app,base_url='https://testserver') as client:
        assert client.get('/admin-auth/google?state=synthetic-flow',follow_redirects=False).status_code==400
        assert client.get('/admin-auth/google?state=unknown-flow',follow_redirects=False).status_code==400
        oauth.put('pending','synthetic-flow',{'cookie_hashes':[]},time.time()-1)
        assert client.get('/admin-auth/google?state=synthetic-flow',follow_redirects=False).status_code==400


@pytest.mark.parametrize('override',[{'aud':'another-client'},{'iss':'https://evil.example'},{'exp':1},
    {'nonce':'another-login'},{'email_verified':False},{'azp':'another-client'}])
def test_google_signed_claims_must_match_issuer_client_nonce_and_verified_email(tmp_path,monkeypatch,override):
    module,store,private=configure(tmp_path,monkeypatch)
    claims={'iss':'https://accounts.google.com','aud':'synthetic-google-client','sub':'synthetic-owner',
        'email':'owner@example.test','email_verified':True,'nonce':'expected-nonce','iat':int(time.time()),'exp':int(time.time()+300),**override}
    token=jwt.encode(claims,private,algorithm='RS256')
    with pytest.raises((ValueError,jwt.PyJWTError)):
        AdminOAuth(lambda:store).verify_google(token,'expected-nonce')


def test_google_token_signature_and_admin_resource_are_verified(tmp_path,monkeypatch):
    module,store,private=configure(tmp_path,monkeypatch)
    claims={'iss':'https://accounts.google.com','aud':'synthetic-google-client','sub':'synthetic-owner',
        'email':'owner@example.test','email_verified':True,'nonce':'nonce','iat':int(time.time()),'exp':int(time.time()+300)}
    forged=jwt.encode(claims,rsa.generate_private_key(public_exponent=65537,key_size=2048),algorithm='RS256')
    with pytest.raises(jwt.PyJWTError):AdminOAuth(lambda:store).verify_google(forged,'nonce')
    with TestClient(module.app,base_url='https://testserver') as client:
        tokens=connect(client,monkeypatch,private)
        oauth=AdminOAuth(lambda:store)
        record=oauth.get('access',tokens['access_token'])
        record['resource']='https://somewhere-else.example/mcp'
        oauth.put('access',tokens['access_token'],record,time.time()+300)
        assert rpc(client,'tools/list',token=tokens['access_token']).status_code in (401,403)
