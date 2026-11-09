"""Private browser view, event listener, recoverable history jobs and CRM outbox."""
import asyncio
import base64
import contextlib
import fcntl
import hashlib
import json
import os
import random
import re
import secrets
import signal
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import Depends,FastAPI,Header,HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field
from typing import Literal

from . import protocol as li
from .state import State


class Worker:
    def __init__(self,root):
        self.state=State(root);self.browser=None;self.lock=asyncio.Lock()
        self.status='starting';self.error='';self.transport=False;self.last_event=0
        self.session_ready=asyncio.Event();self.jobs_wake=asyncio.Event();self.reconnect=asyncio.Event()
        self.account=self.state.get('account','');self.http=None

    async def open(self):
        from linkedin_mcp_server.core.browser import BrowserManager
        self.browser=BrowserManager(user_data_dir=self.state.root/'profile',headless=True,viewport={'width':1280,'height':900},args=['--disable-dev-shm-usage'])
        await self.browser.start()
        await self.browser.context.route('**/*',self.guard)
        self.browser.page.on('request',self.observe)
        if self.browser.page.url=='about:blank':await self.browser.page.goto(li.BASE+'/login',wait_until='domcontentloaded')
        self.status='needs_login'

    async def guard(self,route):
        """Network boundary: no internal navigation, downloads, message writes or read receipts."""
        request=route.request;parsed=urlsplit(request.url);host=parsed.hostname or ''
        allowed=parsed.scheme=='https' and (host in ('linkedin.com','www.linkedin.com','www.google.com','www.gstatic.com','www.recaptcha.net','client-api.arkoselabs.com') or any(host.endswith('.'+suffix) for suffix in ('licdn.com','linkedin.com','arkoselabs.com')))
        messaging_write=request.method not in ('GET','HEAD','OPTIONS') and parsed.path.startswith('/voyager/api/')
        if not allowed or messaging_write:await route.abort()
        else:await route.continue_()

    def observe(self,request):
        parsed=urlsplit(request.url)
        if parsed.hostname!='www.linkedin.com' or parsed.path!='/voyager/api/voyagerMessagingGraphQL/graphql':return
        query=parse_qs(parsed.query).get('queryId',[''])[0]
        for kind,prefix in [('messages','messengerMessages.'),('conversations','messengerConversations.')]:
            if query.startswith(prefix) and re.fullmatch(r'[A-Za-z]+\.[a-f0-9]+',query):self.state.put('query_'+kind,query)

    async def client(self):
        cookies=await self.browser.context.cookies([li.BASE])
        jar={r['name']:r['value'] for r in cookies}
        if not jar.get('li_at') or not jar.get('JSESSIONID'):raise li.LoginRequired('Sign in to LinkedIn in the private browser view.')
        ua=await self.browser.page.evaluate('navigator.userAgent')
        return httpx.AsyncClient(cookies=jar,headers={'user-agent':ua,'csrf-token':jar['JSESSIONID'].strip('"'),'accept':'application/vnd.linkedin.normalized+json+2.1','x-restli-protocol-version':'2.0.0','x-li-recipe-accept':'application/vnd.linkedin.normalized+json+2.1','referer':li.BASE+'/messaging/'},trust_env=False,follow_redirects=False,timeout=httpx.Timeout(45,connect=15))

    async def read(self,url):
        r=await self.http.get(url)
        if r.status_code in (401,403) or r.status_code in (301,302,303,307,308):raise li.LoginRequired('LinkedIn needs sign-in or verification in the private browser view.')
        r.raise_for_status()
        if len(r.content)>10_000_000:raise li.SchemaError('LinkedIn response exceeded capture size boundary.')
        try:return r.json()
        except ValueError as error:raise li.SchemaError('LinkedIn returned an unexpected response.') from error

    async def establish(self):
        if self.http:await self.http.aclose()
        self.http=await self.client()
        account=li.me(await self.read(li.BASE+'/voyager/api/me'))
        if self.account and account!=self.account:raise li.LoginRequired('A different LinkedIn account is signed in. Use the original connected account.')
        self.account=account;self.state.put('account',account)
        self.status='recovering';self.error=''
        # Every connection starts a durable reconciliation. A missed SSE event or
        # crash cannot be repaired by reconnecting transport alone.
        self.state.enqueue('conversations',{'cursor':''},'reconcile:'+str(time.time_ns()))
        self.jobs_wake.set();self.session_ready.set()

    async def keepalive(self,session):
        first=True
        while True:
            await asyncio.sleep(60)
            r=await self.http.post(li.BASE+'/realtime/realtimeFrontendClientConnectivityTracking',json={'action':'sendHeartbeat','isFirstHeartbeat':first,'isLastHeartbeat':False,'realtimeSessionId':session,'mpName':'voyager-web','mpVersion':self.state.get('frontend_version','1.13.8751'),'clientId':'voyager-web','actorUrn':self.account,'contextUrns':[self.account]})
            r.raise_for_status();first=False

    async def stream(self):
        heartbeat=None;frame=[];size=0
        try:
            async with self.http.stream('GET',li.BASE+'/realtime/connect?rc=1',headers={'accept':'text/event-stream'},timeout=httpx.Timeout(100,connect=20)) as response:
                if response.status_code in (401,403):raise li.LoginRequired('LinkedIn session needs verification.')
                response.raise_for_status()
                if 'text/event-stream' not in response.headers.get('content-type',''):raise li.SchemaError('LinkedIn realtime stream format changed.')
                self.transport=True
                async for line in response.aiter_lines():
                    size+=len(line)
                    if size>1_000_000:raise li.SchemaError('LinkedIn realtime event exceeded the size boundary.')
                    if line:frame.append(line);continue
                    if heartbeat and heartbeat.done():await heartbeat
                    for value in li.sse_data(frame+['']):
                        connection=value.get('com.linkedin.realtimefrontend.ClientConnection')
                        if connection and connection.get('id'):
                            if heartbeat:heartbeat.cancel()
                            heartbeat=asyncio.create_task(self.keepalive(connection['id']))
                        thread=li.event_thread(value)
                        if thread:
                            key='event:'+hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
                            if thread=='*':self.state.enqueue('conversations',{'cursor':''},key)
                            else:self.state.enqueue('thread',{'thread':thread,'anchor':int(time.time()*1000)+1000,'peers':[],'subject':'','cutoff':self.state.get('watermark:'+thread,0)},key)
                            self.last_event=time.time();self.state.put('last_event',self.last_event);self.jobs_wake.set()
                    frame=[];size=0
        finally:
            self.transport=False
            if heartbeat:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError,Exception):await heartbeat

    async def supervise_browser(self):
        while True:
            await asyncio.sleep(3)
            try:unhealthy=self.browser.page_crashed or self.browser.page.is_closed()
            except Exception:unhealthy=True
            if unhealthy:
                # Let the container restart the whole process, reopen the profile,
                # and recover in-flight queue entries under the volume lock.
                os.kill(os.getpid(),signal.SIGTERM)
                return

    async def listener(self):
        attempt=0
        while True:
            if self.state.get('paused',False):
                self.status='paused';self.session_ready.clear();await asyncio.sleep(2);continue
            try:
                await self.establish();attempt=0
                self.reconnect.clear()
                stream=asyncio.create_task(self.stream());wake=asyncio.create_task(self.reconnect.wait())
                try:
                    done,_=await asyncio.wait([stream,wake],return_when=asyncio.FIRST_COMPLETED)
                    if stream in done:await stream
                    else:continue
                finally:
                    stream.cancel();wake.cancel()
                    await asyncio.gather(stream,wake,return_exceptions=True)
                raise ConnectionError('Realtime stream ended.')
            except asyncio.CancelledError:raise
            except li.LoginRequired:
                self.status='needs_login';self.error='Open the private cloud browser and complete LinkedIn sign-in or verification.'
                self.session_ready.clear();await asyncio.sleep(15)
            except li.SchemaError as error:
                self.status='needs_attention';self.error=str(error);self.session_ready.clear();await asyncio.sleep(60)
            except Exception:
                self.status='reconnecting';self.error='Realtime connection interrupted; saved messages are retained and history will reconcile on reconnect.'
                self.session_ready.clear();attempt+=1
                await asyncio.sleep(min(60,2**min(attempt,6))+random.random())

    async def process(self,row):
        value=json.loads(row['body']);kind=row['kind']
        query=self.state.get('query_'+('conversations' if kind=='conversations' else 'messages'),'')
        if kind=='conversations':
            data=await self.read(li.query_url('conversations',self.account,cursor=value['cursor'],query_id=query))
            threads,cursor=li.conversations(data)
            with self.state.db() as db:
                for thread in threads:
                    # Roster is recoverable with the queued thread; realtime reads
                    # also learn participants from their normalized response.
                    body={**thread,'anchor':int(time.time()*1000)+1000,'cutoff':self.state.get('watermark:'+thread['thread'],0)}
                    key=row['id']+':'+thread['thread']
                    db.execute('INSERT OR IGNORE INTO jobs(id,kind,body) VALUES (?,?,?)',(key,'thread',json.dumps(body)))
                if cursor:
                    if cursor==value['cursor']:raise li.SchemaError('LinkedIn conversation pagination stopped advancing.')
                    db.execute("UPDATE jobs SET body=?,state='queued' WHERE id=?",(json.dumps({'cursor':cursor}),row['id']))
                else:db.execute("UPDATE jobs SET state='done' WHERE id=?",(row['id'],))
        else:
            data=await self.read(li.query_url('messages',self.account,thread=value['thread'],anchor=value['anchor'],query_id=query))
            captured,oldest,more=li.messages(data,self.account,value['thread'],value['peers'],value['subject'])
            if more and oldest is None:raise li.SchemaError('LinkedIn returned an empty page with unfinished history.')
            self.state.save_messages(captured)
            # Write the outbox before advancing a page checkpoint. Repeating a
            # page after a crash is safe; skipping a page is not.
            latest=max((int(__import__('datetime').datetime.fromisoformat(m['occurred_at']).timestamp()*1000) for m in captured),default=0)
            value['newest']=max(value.get('newest',0),latest)
            if more and oldest is not None and oldest>value['cutoff']:
                if oldest>=value['anchor']:raise li.SchemaError('LinkedIn message pagination stopped advancing; history remains unfinished.')
                value['anchor']=oldest
                with self.state.db() as db:db.execute("UPDATE jobs SET body=?,state='queued' WHERE id=?",(json.dumps(value),row['id']))
            else:
                self.state.put('watermark:'+value['thread'],max(self.state.get('watermark:'+value['thread'],0),value['newest']-1000))
                self.state.finish('jobs',row['id'])

    async def jobs(self):
        while True:
            await self.session_ready.wait()
            if self.state.get('paused',False):await asyncio.sleep(2);continue
            row=self.state.take('jobs')
            if not row:
                pending=sum(n for state,n in self.state.counts('jobs').items() if state!='done')
                if self.transport and not pending:
                    self.status='listening';self.error=''
                elif self.transport and self.status!='needs_attention':self.status='recovering'
                self.jobs_wake.clear()
                with contextlib.suppress(asyncio.TimeoutError):await asyncio.wait_for(self.jobs_wake.wait(),5)
                continue
            try:
                await self.process(row)
                self.state.put('last_read',time.time())
                await asyncio.sleep(1) # Bound read-side API load; this is not an inbox poll.
            except asyncio.CancelledError:raise
            except Exception as error:
                reason='LinkedIn read failed; retry scheduled.'
                if isinstance(error,li.SchemaError):reason=str(error);self.status='needs_attention';self.error=reason
                if isinstance(error,li.LoginRequired):self.status='needs_login';self.session_ready.clear()
                self.state.retry('jobs',row,reason)
                await asyncio.sleep(2)

    async def deliver_one(self,row,client):
        try:
            target=os.environ['CRM_CAPTURE_URL']
            if not target.startswith('https://'):raise ValueError('HTTPS receiver required.')
            r=await client.post(target,headers={'Authorization':'Bearer '+os.environ['CRM_CAPTURE_TOKEN'],'Content-Type':'application/json'},content=row['body'].encode(),follow_redirects=False)
            r.raise_for_status();ack=r.json();message=json.loads(row['body'])
            if ack.get('saved') is not True or ack.get('external_id')!=message['external_id']:raise ValueError('Missing durable acknowledgement.')
            self.state.finish('outbox',row['id']);self.state.put('last_saved',time.time())
        except asyncio.CancelledError:raise
        except Exception:self.state.retry('outbox',row,'CRM did not acknowledge this message; retry scheduled.')

    async def deliver(self):
        async with httpx.AsyncClient(trust_env=False,timeout=30) as client:
            while True:
                if self.state.get('paused',False):await asyncio.sleep(2);continue
                row=self.state.take('outbox')
                if not row:await asyncio.sleep(1);continue
                await self.deliver_one(row,client)

    def snapshot(self):
        with self.state.db() as db:
            errors=[r['error'] for r in db.execute("SELECT DISTINCT error FROM outbox WHERE state='retry' AND error!='' LIMIT 3")]
        return {'delivery_errors':errors,'state':self.status,'error':self.error,'transport_connected':self.transport,'account':self.account,'paused':self.state.get('paused',False),'last_event':self.state.get('last_event',0),'last_read':self.state.get('last_read',0),'last_saved':self.state.get('last_saved',0),'jobs':self.state.counts('jobs'),'outbox':self.state.counts('outbox')}

worker=None


@asynccontextmanager
async def lifespan(app):
    global worker
    root=Path(os.getenv('BROWSER_DATA_DIR','/data/browser'));root.mkdir(parents=True,exist_ok=True,mode=0o700)
    lock=open(root/'worker.lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    worker=Worker(root)
    await worker.open()
    tasks=[asyncio.create_task(worker.listener()),asyncio.create_task(worker.jobs()),asyncio.create_task(worker.deliver()),asyncio.create_task(worker.supervise_browser())]
    try:yield
    finally:
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        if worker.http:await worker.http.aclose()
        await worker.browser.close();lock.close()


def owner(authorization:str=Header(default='')):
    token=os.getenv('BROWSER_WORKER_TOKEN','')
    if not token or not secrets.compare_digest(authorization.encode(),('Bearer '+token).encode()):raise HTTPException(401,'Private browser requires authorization.')


app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None,dependencies=[Depends(owner)])

@app.middleware('http')
async def no_store(request,call_next):
    response=await call_next(request);response.headers['Cache-Control']='private, no-store';return response

@app.get('/status')
async def status():return worker.snapshot()

@app.get('/frame')
async def frame():
    async with worker.lock:
        page=worker.browser.page
        data=await page.screenshot(type='jpeg',quality=65,timeout=15000)
        return {'image':'data:image/jpeg;base64,'+base64.b64encode(data).decode(),'width':1280,'height':900,'url':page.url}

class Control(BaseModel):
    action:Literal['navigate','click','type','key','scroll','start','pause','resume','disconnect']
    url:str=Field(default='',max_length=2000)
    text:str=Field(default='',max_length=2000)
    key:Literal['','Enter','Tab','Backspace','Escape','ControlOrMeta+A','ArrowDown','ArrowUp']=''
    x:int=Field(default=0,ge=0,le=1280)
    y:int=Field(default=0,ge=0,le=900)
    delta:int=Field(default=0,ge=-2000,le=2000)

@app.post('/control')
async def control(body:Control):
    async with worker.lock:
        page=worker.browser.page
        if body.action in ('pause','disconnect'):
            worker.state.put('paused',True);worker.session_ready.clear();worker.status='paused';worker.reconnect.set()
            if body.action=='disconnect':
                await worker.browser.context.clear_cookies();await page.goto(li.BASE+'/login',wait_until='domcontentloaded');worker.status='needs_login'
        elif body.action in ('start','resume'):
            worker.state.put('paused',False);worker.reconnect.set()
        elif body.action=='navigate':
            parsed=urlsplit(body.url)
            if parsed.scheme!='https' or parsed.hostname not in ('linkedin.com','www.linkedin.com') or parsed.port not in (None,443):raise HTTPException(422,'Use a LinkedIn HTTPS URL.')
            await page.goto(body.url,wait_until='domcontentloaded',timeout=25000)
        elif body.action=='click':await page.mouse.click(body.x,body.y)
        elif body.action=='type':await page.keyboard.insert_text(body.text)
        elif body.action=='key':
            if body.key:await page.keyboard.press(body.key)
        elif body.action=='scroll':await page.mouse.wheel(0,body.delta)
    return {'ok':True}
