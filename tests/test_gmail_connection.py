"""Synthetic provider responses; never connects to a real mailbox."""
import base64
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs,urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from harness import gmail_connection as gmail
from harness.store import Store
from test_crm import add,company


def connection(store,state='connected'):
    gmail.initialize(store)
    with store.connect() as db:
        db.execute('INSERT INTO crm_gmail_connection VALUES (1,?,?,?, ?,0,0,0,?,?)',('owner@example.net','100','UTC',state,'',time.time()))


def test_oauth_browser_binding_and_encrypted_vault(tmp_path,monkeypatch):
    monkeypatch.setenv('ADMIN_GOOGLE_CLIENT_ID','synthetic-client');monkeypatch.setenv('ADMIN_GOOGLE_CLIENT_SECRET','synthetic-secret');monkeypatch.setenv('PUBLIC_ORIGIN','https://app.example.org')
    store=Store(tmp_path);params=parse_qs(urlsplit(gmail.begin(store,'owner@example.net','UTC','browser-cookie')).query)
    assert params['redirect_uri']==['https://app.example.org/admin-auth/callback']
    assert params['scope']==['openid email '+gmail.SCOPE] and params['code_challenge_method']==['S256']
    assert params['access_type']==['offline']
    state=params['state'][0]
    with pytest.raises(ValueError,match='session expired'):gmail.finish(store,state,'fake-code','different-browser')
    assert gmail.Vault(store).get('pending',state)
    gmail.Vault(store).put('token','gmail',{'refresh_token':'synthetic-sensitive-value'})
    assert b'synthetic-sensitive-value' not in (tmp_path/'gmail-oauth.sqlite').read_bytes()
    assert (tmp_path/'gmail-oauth.key').stat().st_mode & 0o777 == 0o600
    assert (tmp_path/'gmail-oauth.sqlite').stat().st_mode & 0o777 == 0o600
    assert gmail.Vault(store).get('pending',state,consume=True)
    assert gmail.Vault(store).get('pending',state) is None


def signed(monkeypatch,claims):
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    monkeypatch.setattr(jwt.PyJWKClient,'get_signing_key_from_jwt',lambda self,token:SimpleNamespace(key=private.public_key()))
    return jwt.encode({'iss':'https://accounts.google.com','exp':time.time()+120,'iat':time.time(),**claims},private,algorithm='RS256')


def test_push_authentication_persistence_and_duplicate_delivery(tmp_path,monkeypatch):
    store=Store(tmp_path);connection(store)
    monkeypatch.setenv('GMAIL_PUBSUB_AUDIENCE','https://app.example.org/api/hooks/gmail')
    monkeypatch.setenv('GMAIL_PUBSUB_SERVICE_ACCOUNT','push@example.iam.gserviceaccount.com')
    claims={'aud':'https://app.example.org/api/hooks/gmail','email':'push@example.iam.gserviceaccount.com','email_verified':True}
    raw=signed(monkeypatch,claims)
    envelope={'message':{'messageId':'notification-1','data':base64.b64encode(json.dumps({'emailAddress':'owner@example.net','historyId':'101'}).encode()).decode()}}
    assert gmail.receive_push(store,'Bearer '+raw,envelope)['accepted']
    gmail.receive_push(store,'Bearer '+raw,envelope)
    assert len(store.query('SELECT * FROM crm_mail_jobs'))==1
    assert gmail.status(store)['connection']['state']=='live'
    wrong=signed(monkeypatch,{**claims,'email':'other@example.org'})
    with pytest.raises(ValueError):gmail.receive_push(store,'Bearer '+wrong,envelope)
    with store.connect() as db:db.execute("UPDATE crm_gmail_connection SET state='disconnected'")
    with pytest.raises(ValueError):gmail.receive_push(store,'Bearer '+wrong,envelope)


def test_identity_checks_exact_account_nonce_and_audience(monkeypatch):
    monkeypatch.setenv('ADMIN_GOOGLE_CLIENT_ID','synthetic-client')
    claims={'aud':'synthetic-client','sub':'123','nonce':'nonce','email':'owner@example.net','email_verified':True}
    raw=signed(monkeypatch,claims)
    assert gmail.verify_identity(raw,'nonce','owner@example.net')['sub']=='123'
    with pytest.raises(ValueError):gmail.verify_identity(raw,'other','owner@example.net')
    with pytest.raises(ValueError):gmail.verify_identity(raw,'nonce','other@example.net')


def test_mime_prefers_plain_text_and_marks_automated(tmp_path):
    raw={'id':'m1','threadId':'t1','internalDate':'1760000000000','labelIds':['SENT'],'payload':{'headers':[{'name':'Subject','value':'Interview'},{'name':'Auto-Submitted','value':'auto-generated'}],'parts':[{'mimeType':'text/plain','body':{'data':base64.urlsafe_b64encode(b'  exact plain text  ').decode()}},{'mimeType':'text/html','body':{'data':base64.urlsafe_b64encode(b'<script>never()</script><b>HTML</b>').decode()}}]}}
    msg=gmail.decode_message(raw,'owner@example.net')
    assert msg.text=='  exact plain text  ' and msg.direction=='outbound' and msg.automated
    assert msg.occurred_at.tzinfo
    raw['payload']['parts']=raw['payload']['parts'][1:]
    assert 'never' not in gmail.decode_message(raw,'owner@example.net').text
    store=Store(tmp_path);gmail.initialize(store)
    raw['labelIds']=['DRAFT']
    assert not gmail.relevant(store,raw,'owner@example.net')


def test_queue_recovers_leases_and_retries_without_polling_inbox(tmp_path,monkeypatch):
    store=Store(tmp_path);connection(store)
    with store.connect() as db:
        ident=gmail.enqueue(db,'delta',{'account':'owner@example.net'},'one')
        db.execute("UPDATE crm_mail_jobs SET state='working',lease_until=10")
    calls=[]
    def interrupted(store,job):calls.append(job['id']);raise httpx.ConnectError('synthetic outage')
    monkeypatch.setattr(gmail,'process',interrupted)
    assert gmail.process_once(store,now=20)
    job=store.query('SELECT * FROM crm_mail_jobs')[0]
    assert job['state']=='retry' and job['next_run']==50
    assert not gmail.process_once(store,now=30)
    monkeypatch.setattr(gmail,'process',lambda store,job:calls.append(job['id']))
    assert gmail.process_once(Store(tmp_path),now=51)
    assert store.query('SELECT state FROM crm_mail_jobs')[0]['state']=='done'
    assert calls==[ident,ident]
    for key in ('GMAIL_PUBSUB_TOPIC','GMAIL_PUBSUB_AUDIENCE','GMAIL_PUBSUB_SERVICE_ACCOUNT'):monkeypatch.setenv(key,'synthetic')
    with store.connect() as db:db.execute("UPDATE crm_gmail_connection SET state='disconnected'")
    assert not gmail.process_once(store,now=100)


def test_delta_cursor_only_advances_after_all_messages_are_saved(tmp_path,monkeypatch):
    store=Store(tmp_path);connection(store);calls=[]
    def request(store,path,**kwargs):
        assert path=='/history'
        if 'pageToken' in kwargs['params']:return {'historyId':'103','history':[{'messagesAdded':[{'message':{'id':'second'}}]}]}
        return {'historyId':'102','nextPageToken':'page-2','history':[{'messagesAdded':[{'message':{'id':'first'}}]}]}
    monkeypatch.setattr(gmail,'request',request)
    def fetch(store,ident,connection):
        calls.append(ident)
        assert store.query('SELECT cursor FROM crm_gmail_connection')[0]['cursor']=='100'
        if ident=='second':raise httpx.ConnectError('interruption')
    monkeypatch.setattr(gmail,'fetch_message',fetch)
    job={'kind':'delta','body':json.dumps({'account':'owner@example.net'})}
    with pytest.raises(httpx.ConnectError):gmail.process(store,job)
    assert store.query('SELECT cursor FROM crm_gmail_connection')[0]['cursor']=='100'
    monkeypatch.setattr(gmail,'fetch_message',lambda *args:None)
    gmail.process(store,job)
    assert store.query('SELECT cursor FROM crm_gmail_connection')[0]['cursor']=='103'


def test_stale_history_recovers_with_backfill(tmp_path,monkeypatch):
    store=Store(tmp_path);connection(store)
    def request(store,path,**kwargs):
        if path=='/profile':return {'historyId':'200'}
        response=httpx.Response(404,request=httpx.Request('GET','https://gmail.googleapis.com/history'))
        raise httpx.HTTPStatusError('stale cursor',request=response.request,response=response)
    monkeypatch.setattr(gmail,'request',request)
    gmail.process(store,{'kind':'delta','body':json.dumps({'account':'owner@example.net'})})
    assert store.query('SELECT cursor FROM crm_gmail_connection')[0]['cursor']=='200'
    assert store.query('SELECT kind FROM crm_mail_jobs')[0]['kind']=='backfill'


def test_backfill_covers_every_identity_and_excludes_drafts(tmp_path,monkeypatch):
    store=Store(tmp_path);connection(store)
    monkeypatch.setattr(gmail,'scope_terms',lambda store:['"Company '+str(i)+'"' for i in range(85)])
    queries=[];captured=[]
    def request(store,path,**kwargs):
        if path=='/messages':
            queries.append(kwargs['params']['q'])
            return {'messages':[{'id':'one','threadId':'thread'}]}
        return {'messages':[{'id':'sent','labelIds':['SENT']},{'id':'draft','labelIds':['DRAFT']},{'id':'trash','labelIds':['TRASH']}]}
    monkeypatch.setattr(gmail,'request',request)
    monkeypatch.setattr(gmail,'fetch_message',lambda *args:None)
    monkeypatch.setattr(gmail,'decode_message',lambda value,account:value['id'])
    monkeypatch.setattr(gmail.mail,'ingest',lambda store,message,**kw:captured.append(message))
    with store.connect() as db:gmail.enqueue(db,'backfill',{'account':'owner@example.net'},'initial')
    for i in range(3):assert gmail.process_once(store,now=time.time()+i)
    assert len(queries)==3 and 'Company 84' in queries[-1]
    assert captured==['sent']*3


def test_finish_consumes_state_and_checks_mailbox_before_storing_tokens(tmp_path,monkeypatch):
    monkeypatch.setenv('ADMIN_GOOGLE_CLIENT_ID','synthetic-client');monkeypatch.setenv('ADMIN_GOOGLE_CLIENT_SECRET','synthetic-secret');monkeypatch.setenv('PUBLIC_ORIGIN','https://app.example.org')
    store=Store(tmp_path)
    state=parse_qs(urlsplit(gmail.begin(store,'owner@example.net','UTC','cookie')).query)['state'][0]
    verified=[]
    monkeypatch.setattr(gmail,'verify_identity',lambda token,nonce,email:verified.append(email))
    requests=[]
    class Client:
        def __init__(self,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,**kw):
            requests.append(kw['data'])
            return httpx.Response(200,json={'access_token':'synthetic-access','refresh_token':'synthetic-refresh','id_token':'synthetic-id','scope':'openid email '+gmail.SCOPE},request=httpx.Request('POST',url))
        def get(self,url,**kw):return httpx.Response(200,json={'emailAddress':'owner@example.net','historyId':'100'},request=httpx.Request('GET',url))
    monkeypatch.setattr(httpx,'Client',Client)
    assert gmail.finish(store,state,'code','cookie')=='owner@example.net'
    assert verified==['owner@example.net'] and requests[0]['code_verifier']
    assert gmail.status(store)['connection']['cursor']=='100'
    assert gmail.status(store)['jobs'][0]['kind']=='backfill'
    assert gmail.Vault(store).get('token','gmail')['refresh_token']=='synthetic-refresh'
    with pytest.raises(ValueError):gmail.finish(store,state,'code','cookie')
