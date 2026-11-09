"""Read-only Gmail OAuth and a persistent push-driven synchronization queue."""
import asyncio
import base64
import fcntl
import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
from datetime import datetime,timezone
from email.utils import getaddresses
from urllib.parse import urlencode

import httpx
import jwt
from cryptography.fernet import Fernet
from lxml import html
from . import correspondence as mail

SCOPE='https://www.googleapis.com/auth/gmail.readonly'
API='https://gmail.googleapis.com/gmail/v1/users/me'


class SetupError(ValueError):pass
class ReconnectError(ValueError):pass


def client_config():
    return (os.getenv('GMAIL_GOOGLE_CLIENT_ID') or os.getenv('ADMIN_GOOGLE_CLIENT_ID',''),os.getenv('GMAIL_GOOGLE_CLIENT_SECRET') or os.getenv('ADMIN_GOOGLE_CLIENT_SECRET',''))


def origin():return os.getenv('PUBLIC_ORIGIN','').rstrip('/')


class Vault:
    def __init__(self,store):self.root=store.root

    def cipher(self):
        path=self.root/'gmail-oauth.key'
        fd=os.open(path,os.O_RDWR|os.O_CREAT,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX);os.fchmod(fd,0o600)
            key=os.read(fd,100)
            if not key:
                key=Fernet.generate_key();os.write(fd,key);os.fsync(fd)
            return Fernet(key)
        finally:os.close(fd)

    def connect(self):
        path=self.root/'gmail-oauth.sqlite'
        fd=os.open(path,os.O_RDWR|os.O_CREAT,0o600);os.fchmod(fd,0o600);os.close(fd)
        db=sqlite3.connect(path,timeout=20);db.row_factory=sqlite3.Row
        db.execute('CREATE TABLE IF NOT EXISTS secrets(kind TEXT,id TEXT,value BLOB,expires REAL,PRIMARY KEY(kind,id))');db.commit()
        return db

    def put(self,kind,key,value,expires=0):
        data=self.cipher().encrypt(json.dumps(value).encode())
        with self.connect() as db:db.execute('INSERT OR REPLACE INTO secrets VALUES (?,?,?,?)',(kind,hashlib.sha256(key.encode()).hexdigest(),data,expires))

    def get(self,kind,key,consume=False):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');ident=hashlib.sha256(key.encode()).hexdigest()
            row=db.execute('SELECT value,expires FROM secrets WHERE kind=? AND id=?',(kind,ident)).fetchone()
            if consume:db.execute('DELETE FROM secrets WHERE kind=? AND id=?',(kind,ident))
        if not row or (row['expires'] and row['expires']<time.time()):return None
        return json.loads(self.cipher().decrypt(row['value']))

    def delete(self,kind,key):
        with self.connect() as db:db.execute('DELETE FROM secrets WHERE kind=? AND id=?',(kind,hashlib.sha256(key.encode()).hexdigest()))


def initialize(store):
    mail.initialize(store)
    with store.connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS crm_gmail_connection(id INTEGER PRIMARY KEY CHECK(id=1),account TEXT NOT NULL,cursor TEXT NOT NULL,timezone TEXT NOT NULL,state TEXT NOT NULL,watch_expires REAL NOT NULL DEFAULT 0,watch_renew REAL NOT NULL DEFAULT 0,last_sync REAL NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS crm_mail_jobs(id TEXT PRIMARY KEY,kind TEXT NOT NULL,body TEXT NOT NULL,state TEXT NOT NULL,attempt INTEGER NOT NULL DEFAULT 0,next_run REAL NOT NULL DEFAULT 0,lease_until REAL NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',created REAL NOT NULL,updated REAL NOT NULL);
        ''')


def enqueue(db,kind,body,key):
    ident=str(uuid.uuid5(uuid.NAMESPACE_URL,'gmail-job:'+kind+':'+key));now=time.time()
    db.execute("INSERT OR IGNORE INTO crm_mail_jobs VALUES (?,?,?,'queued',0,0,0,'',?,?)",(ident,kind,json.dumps(body),now,now))
    return ident


def status(store):
    initialize(store);rows=store.query('SELECT * FROM crm_gmail_connection WHERE id=1')
    return {'oauth_configured':all(client_config()) and origin().startswith('https://'),'push_configured':all(os.getenv(k) for k in ('GMAIL_PUBSUB_TOPIC','GMAIL_PUBSUB_AUDIENCE','GMAIL_PUBSUB_SERVICE_ACCOUNT')),'connection':rows[0] if rows else None,'jobs':store.query('SELECT id,kind,state,attempt,next_run,error,updated FROM crm_mail_jobs ORDER BY created DESC LIMIT 15'),'callback_uri':origin()+'/admin-auth/callback','push_uri':origin()+'/api/hooks/gmail','expected_email':os.getenv('GMAIL_OWNER_EMAIL') or os.getenv('ADMIN_GOOGLE_OWNER_EMAIL','')}


def begin(store,email,timezone_name,cookie):
    initialize(store)
    if not all(client_config()) or not origin().startswith('https://'):raise SetupError('Google OAuth client configuration is required.')
    mail.Address(email=email);mail.Import(messages=[mail.Message(provider='gmail',account=email,external_id='validation',thread_id='validation',direction='inbound',sender=mail.Address())],timezone=timezone_name)
    state='gmail_'+secrets.token_urlsafe(32);nonce=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48)
    Vault(store).put('pending',state,{'email':email.casefold(),'timezone':timezone_name,'nonce':nonce,'verifier':verifier,'cookie_hash':hashlib.sha256(cookie.encode()).hexdigest()},time.time()+600)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    return 'https://accounts.google.com/o/oauth2/v2/auth?'+urlencode({'client_id':client_config()[0],'redirect_uri':origin()+'/admin-auth/callback','response_type':'code','scope':'openid email '+SCOPE,'state':state,'nonce':nonce,'code_challenge':challenge,'code_challenge_method':'S256','access_type':'offline','prompt':'consent select_account','login_hint':email})


def verify_identity(token,nonce,email):
    key=jwt.PyJWKClient('https://www.googleapis.com/oauth2/v3/certs',timeout=10).get_signing_key_from_jwt(token)
    claims=jwt.decode(token,key.key,algorithms=['RS256'],audience=client_config()[0],issuer=['https://accounts.google.com','accounts.google.com'],options={'require':['exp','iat','sub','nonce','email','email_verified']})
    if claims.get('azp',client_config()[0])!=client_config()[0] or (isinstance(claims['aud'],list) and len(claims['aud'])>1 and 'azp' not in claims):raise ValueError('Wrong Google client.')
    if claims['email_verified'] is not True or claims['email'].casefold()!=email.casefold() or not secrets.compare_digest(claims['nonce'],nonce):raise ValueError('Wrong Gmail account or sign-in session.')
    return claims


def finish(store,state,code,cookie):
    vault=Vault(store);pending=vault.get('pending',state)
    if not pending or not secrets.compare_digest(pending['cookie_hash'],hashlib.sha256((cookie or '').encode()).hexdigest()):raise ValueError('Gmail sign-in session expired. Start again.')
    pending=vault.get('pending',state,consume=True)
    if not pending:raise ValueError('Gmail sign-in session was already used.')
    with httpx.Client(timeout=20,trust_env=False) as client:
        response=client.post('https://oauth2.googleapis.com/token',data={'client_id':client_config()[0],'client_secret':client_config()[1],'code':code,'code_verifier':pending['verifier'],'redirect_uri':origin()+'/admin-auth/callback','grant_type':'authorization_code'});response.raise_for_status();tokens=response.json()
        verify_identity(tokens['id_token'],pending['nonce'],pending['email'])
        scopes=set(tokens.get('scope','').split())
        if SCOPE not in scopes or any(s.startswith('https://www.googleapis.com/auth/gmail.') and s!=SCOPE for s in scopes) or 'https://mail.google.com/' in scopes:raise ValueError('Read-only Gmail permission was not granted.')
        profile=client.get(API+'/profile',headers={'Authorization':'Bearer '+tokens['access_token']});profile.raise_for_status();profile=profile.json()
    if profile['emailAddress'].casefold()!=pending['email']:raise ValueError('Gmail mailbox does not match the chosen account.')
    prior=vault.get('token','gmail') or {}
    refresh=tokens.get('refresh_token') or (prior.get('refresh_token') if prior.get('account')==pending['email'] else None)
    if not refresh:raise ValueError('Google did not provide offline access. Reconnect and grant read-only Gmail access.')
    vault.put('token','gmail',{'account':pending['email'],'access_token':tokens['access_token'],'refresh_token':refresh,'expires':time.time()+tokens.get('expires_in',3600)})
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("INSERT INTO crm_gmail_connection VALUES (1,?,?,?,'connected',0,0,0,'',?) ON CONFLICT(id) DO UPDATE SET account=excluded.account,cursor=excluded.cursor,timezone=excluded.timezone,state='connected',watch_expires=0,watch_renew=0,error='',updated=excluded.updated",(pending['email'],str(profile['historyId']),pending['timezone'],time.time()))
        enqueue(db,'backfill',{'account':pending['email'],'page':None},state)
    return pending['email']


def token(store,force=False,account=None):
    vault=Vault(store);value=vault.get('token','gmail')
    if account and value and value['account']!=account:raise ReconnectError('Gmail account changed; retry from the active connection.')
    if not value:raise ReconnectError('Reconnect Gmail to resume reading messages.')
    if force or value['expires']<time.time()+60:
        with httpx.Client(timeout=20,trust_env=False) as client:
            r=client.post('https://oauth2.googleapis.com/token',data={'client_id':client_config()[0],'client_secret':client_config()[1],'refresh_token':value['refresh_token'],'grant_type':'refresh_token'})
        if r.status_code in (400,401):raise ReconnectError('Google authorization expired or was revoked. Reconnect Gmail.')
        r.raise_for_status();t=r.json();value.update(access_token=t['access_token'],expires=time.time()+t.get('expires_in',3600));vault.put('token','gmail',value)
    return value['access_token']


def request(store,path,params=None,body=None,account=None):
    if path not in ('/profile','/messages','/history','/watch','/stop') and not path.startswith(('/messages/','/threads/')):raise ValueError('Unsupported read-only Gmail operation.')
    for attempt in range(2):
        with httpx.Client(timeout=25,trust_env=False) as client:
            r=client.request('POST' if body is not None else 'GET',API+path,params=params,json=body,headers={'Authorization':'Bearer '+token(store,force=bool(attempt),account=account)})
        if r.status_code!=401 or attempt:break
    if r.status_code==403:raise SetupError('Gmail API or Pub/Sub permission is not enabled for this Google project.')
    r.raise_for_status();return r.json() if r.content else {}


def addresses(value):return [mail.Address(name=name[:200],email=email) for name,email in getaddresses([value]) if email and '@' in email]


def decode_message(value,account):
    payload=value.get('payload',{});headers={h['name'].casefold():h['value'] for h in payload.get('headers',[])}
    def parts(part):
        if part.get('filename'):return []
        result=[];raw=part.get('body',{}).get('data')
        if raw and part.get('mimeType') in ('text/plain','text/html'):
            text=base64.urlsafe_b64decode(raw+'='*(-len(raw)%4)).decode('utf8',errors='replace')
            if part.get('mimeType')=='text/html':
                doc=html.fromstring(text,parser=html.HTMLParser(no_network=True))
                for element in doc.xpath('//script|//style|//iframe'):element.drop_tree()
                text=' '.join(doc.itertext())
            result.append((part.get('mimeType'),text))
        for child in part.get('parts',[]):result.extend(parts(child))
        return result
    content=parts(payload);plain=[text for kind,text in content if kind=='text/plain'];text='\n\n'.join(plain or [text for _,text in content]);sender=addresses(headers.get('from',''))
    return mail.Message(provider='gmail',account=account,external_id=value['id'],thread_id=value['threadId'],direction='outbound' if 'SENT' in value.get('labelIds',[]) else 'inbound',occurred_at=datetime.fromtimestamp(int(value['internalDate'])/1000,timezone.utc),sender=sender[0] if sender else mail.Address(),recipients=addresses(headers.get('to',''))+addresses(headers.get('cc','')),subject=headers.get('subject','')[:1000],text=text[:50000],truncated=len(text)>50000,automated=headers.get('auto-submitted','no').casefold()!='no',source_url='https://mail.google.com/mail/?'+urlencode({'authuser':account})+'#all/'+value['threadId'])


def scope_terms(store):
    values=set()
    for row in store.query("SELECT body FROM crm_records WHERE kind IN ('company','person') AND id NOT IN (SELECT alias_id FROM crm_redirects)"):
        r=json.loads(row['body'])
        if r.get('email'):values.add('"'+r['email'].replace('"','')+'"')
        if r.get('domain'):values.add('"'+r['domain'].replace('"','')+'"')
        if r.get('name'):values.add('"'+r['name'].replace('"','').replace('\\','')+'"')
    return sorted(values)


def relevant(store,value,account):
    labels=value.get('labelIds',[])
    if 'DRAFT' in labels or any(label in labels for label in ('SPAM','TRASH')):return False
    headers={h['name'].casefold():h['value'] for h in value.get('payload',{}).get('headers',[])}
    text=' '.join(headers.values()).casefold()
    prior=store.query('SELECT 1 FROM crm_messages WHERE provider=\'gmail\' AND account=? AND thread_id=? LIMIT 1',(account,value.get('threadId','')))
    return bool(prior or any(term.strip('"').casefold() in text for term in scope_terms(store)) or any(word in headers.get('subject','').casefold() for word in ('interview','recruit','your application','job application','referral')))


def fetch_message(store,ident,connection):
    metadata=request(store,'/messages/'+ident,params={'format':'metadata','metadataHeaders':['From','To','Cc','Subject']},account=connection['account'])
    if relevant(store,metadata,connection['account']):
        full=request(store,'/messages/'+ident,params={'format':'full'},account=connection['account'])
        if not any(label in full.get('labelIds',[]) for label in ('DRAFT','SPAM','TRASH')):mail.ingest(store,decode_message(full,connection['account']),method='gmail_api',timezone=connection['timezone'])


def process(store,job):
    connection=store.query('SELECT * FROM crm_gmail_connection WHERE id=1')[0];body=json.loads(job['body'])
    if body['account']!=connection['account'] or connection['state']=='disconnected':return
    if job['kind']=='watch':
        result=request(store,'/watch',body={'topicName':os.environ['GMAIL_PUBSUB_TOPIC']},account=connection['account'])
        with store.connect() as db:
            db.execute("UPDATE crm_gmail_connection SET watch_expires=?,watch_renew=?,state=CASE WHEN state='live' THEN 'live' ELSE 'watching' END,error='',updated=? WHERE id=1 AND account=? AND state!='disconnected'",(int(result['expiration'])/1000,time.time()+86400,time.time(),connection['account']))
            enqueue(db,'delta',{'account':connection['account']},'watch:'+str(result['historyId']))
    elif job['kind']=='backfill':
        terms=scope_terms(store);group=body.get('group',0)
        query='newer_than:90d -in:spam -in:trash {'+' '.join(terms[group*40:(group+1)*40])+ ' subject:interview subject:recruiting subject:"your application"}'
        result=request(store,'/messages',params={'q':query,'maxResults':25,**({'pageToken':body['page']} if body.get('page') else {})},account=connection['account'])
        threads=set()
        for item in result.get('messages',[]):
            fetch_message(store,item['id'],connection);threads.add(item['threadId'])
        # A thread can contain both sent mail and replies, even when only one matched search.
        for ident in threads:
            thread=request(store,'/threads/'+ident,params={'format':'full'},account=connection['account'])
            for value in thread.get('messages',[]):
                if not any(label in value.get('labelIds',[]) for label in ('DRAFT','SPAM','TRASH')):mail.ingest(store,decode_message(value,connection['account']),method='gmail_api',timezone=connection['timezone'])
        if result.get('nextPageToken'):
            with store.connect() as db:enqueue(db,'backfill',{'account':connection['account'],'page':result['nextPageToken'],'group':group},job['id']+':'+result['nextPageToken'])
        elif (group+1)*40<len(terms):
            with store.connect() as db:enqueue(db,'backfill',{'account':connection['account'],'page':None,'group':group+1},job['id']+':group:'+str(group+1))
    else:
        cursor=connection['cursor'];page=None;end=cursor;ids=set()
        try:
            while True:
                result=request(store,'/history',params={'startHistoryId':cursor,'historyTypes':'messageAdded','maxResults':100,**({'pageToken':page} if page else {})},account=connection['account'])
                for event in result.get('history',[]):
                    for added in event.get('messagesAdded',[]):ids.add(added['message']['id'])
                end=str(result.get('historyId',end));page=result.get('nextPageToken')
                if not page:break
            for ident in ids:
                try:fetch_message(store,ident,connection)
                except httpx.HTTPStatusError as error:
                    if error.response.status_code!=404:raise
            with store.connect() as db:db.execute('UPDATE crm_gmail_connection SET cursor=? WHERE id=1 AND cursor=? AND account=?',(end,cursor,connection['account']))
        except httpx.HTTPStatusError as error:
            if error.response.status_code!=404:raise
            profile=request(store,'/profile',account=connection['account'])
            with store.connect() as db:
                enqueue(db,'backfill',{'account':connection['account'],'page':None},'expired-history:'+str(profile['historyId']))
                db.execute('UPDATE crm_gmail_connection SET cursor=? WHERE id=1 AND account=? AND cursor=?',(str(profile['historyId']),connection['account'],cursor))
    with store.connect() as db:db.execute("UPDATE crm_gmail_connection SET last_sync=?,error='',updated=? WHERE id=1 AND account=? AND state!='disconnected'",(time.time(),time.time(),connection['account']))


def process_once(store,now=None):
    initialize(store);now=time.time() if now is None else now
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        connection=db.execute('SELECT * FROM crm_gmail_connection WHERE id=1').fetchone()
        if connection and connection['state'] not in ('reconnect','setup_error','disconnected') and all(os.getenv(k) for k in ('GMAIL_PUBSUB_TOPIC','GMAIL_PUBSUB_AUDIENCE','GMAIL_PUBSUB_SERVICE_ACCOUNT')) and connection['watch_renew']<=now:
            if not db.execute("SELECT 1 FROM crm_mail_jobs WHERE kind='watch' AND state IN ('queued','working','retry')").fetchone():enqueue(db,'watch',{'account':connection['account']},str(int(now)))
        row=db.execute("SELECT * FROM crm_mail_jobs WHERE (state IN ('queued','retry') AND next_run<=?) OR (state='working' AND lease_until<?) ORDER BY created LIMIT 1",(now,now)).fetchone()
        if not row:return False
        job=dict(row);db.execute("UPDATE crm_mail_jobs SET state='working',attempt=attempt+1,lease_until=?,updated=? WHERE id=?",(now+3600,now,job['id']))
    try:process(store,job)
    except (ReconnectError,SetupError) as error:
        with store.connect() as db:
            db.execute("UPDATE crm_gmail_connection SET state=?,error=?,updated=? WHERE id=1 AND account=? AND state!='disconnected'",('reconnect' if isinstance(error,ReconnectError) else 'setup_error',str(error),now,json.loads(job['body'])['account']))
            db.execute("UPDATE crm_mail_jobs SET state='failed',error=?,updated=? WHERE id=?",(str(error),now,job['id']))
    except Exception:
        with store.connect() as db:db.execute("UPDATE crm_mail_jobs SET state='retry',next_run=?,lease_until=0,error='Message sync interrupted; retrying automatically',updated=? WHERE id=?",(now+min(1800,30*2**min(job['attempt'],6)),now,job['id']))
    else:
        with store.connect() as db:db.execute("UPDATE crm_mail_jobs SET state='done',lease_until=0,error='',updated=? WHERE id=?",(now,job['id']))
    return True


async def worker(store):
    while True:
        try:await asyncio.to_thread(process_once,store)
        except Exception:pass
        await asyncio.sleep(1)


def receive_push(store,authorization,envelope):
    initialize(store)
    audience=os.getenv('GMAIL_PUBSUB_AUDIENCE','');expected=os.getenv('GMAIL_PUBSUB_SERVICE_ACCOUNT','')
    if not audience or not expected or not authorization.startswith('Bearer '):raise ValueError('Gmail push authentication is not configured or missing.')
    raw=authorization[7:];key=jwt.PyJWKClient('https://www.googleapis.com/oauth2/v3/certs',timeout=10).get_signing_key_from_jwt(raw)
    claims=jwt.decode(raw,key.key,algorithms=['RS256'],audience=audience,issuer=['https://accounts.google.com','accounts.google.com'],options={'require':['exp','iat','email','email_verified']})
    if claims['email']!=expected or claims['email_verified'] is not True:raise ValueError('Unexpected push service account.')
    message=envelope['message'];value=json.loads(base64.b64decode(message['data'],validate=True));history=str(value['historyId'])
    if not history.isdigit() or len(history)>30:raise ValueError('Invalid Gmail history ID.')
    connection=store.query("SELECT account FROM crm_gmail_connection WHERE id=1 AND state!='disconnected'")
    if not connection or value['emailAddress'].casefold()!=connection[0]['account']:raise ValueError('Unknown Gmail mailbox.')
    with store.connect() as db:
        enqueue(db,'delta',{'account':connection[0]['account']},'push:'+str(message['messageId']))
        db.execute("UPDATE crm_gmail_connection SET state='live',updated=? WHERE id=1",(time.time(),))
    return {'accepted':True}
