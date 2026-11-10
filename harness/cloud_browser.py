"""Owner-only gateway to a separately deployed private browser worker."""
import os
import secrets
import time
from typing import Literal

import httpx
from pydantic import BaseModel, Field
from . import correspondence as mail


class Control(BaseModel):
    action: Literal['navigate','click','type','key','scroll','start','pause','resume','disconnect']
    url: str = Field(default='',max_length=2000)
    text: str = Field(default='',max_length=2000)
    key: Literal['','Enter','Tab','Backspace','Escape','ControlOrMeta+A','ArrowDown','ArrowUp'] = ''
    x: int = Field(default=0,ge=0,le=1440)
    y: int = Field(default=0,ge=0,le=1000)
    delta: int = Field(default=0,ge=-2000,le=2000)


def initialize(store):
    mail.initialize(store)
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS crm_linkedin_capture(id INTEGER PRIMARY KEY CHECK(id=1),account TEXT NOT NULL,last_saved REAL NOT NULL)')


def configured():
    return bool(os.getenv('CLOUD_BROWSER_URL') and os.getenv('CLOUD_BROWSER_TOKEN'))


async def request(path,body=None):
    if not configured():raise ValueError('Cloud browser is not deployed yet.')
    try:
        async with httpx.AsyncClient(trust_env=False,timeout=30) as client:
            r=await client.request('GET' if body is None else 'POST',os.environ['CLOUD_BROWSER_URL'].rstrip('/')+path,headers={'Authorization':'Bearer '+os.environ['CLOUD_BROWSER_TOKEN']},json=body)
            r.raise_for_status()
            return r.json()
    except (httpx.HTTPError,ValueError) as error:
        raise ValueError('Cloud browser is temporarily unavailable; its saved queue will retry independently.') from error


def receive(store,authorization,body):
    token=os.getenv('LINKEDIN_CAPTURE_TOKEN','')
    if not token or not secrets.compare_digest(authorization.encode(),('Bearer '+token).encode()):raise PermissionError
    message=mail.Message.model_validate(body)
    if message.provider!='linkedin' or not message.account.startswith('urn:li:fsd_profile:'):raise ValueError('Invalid LinkedIn capture.')
    initialize(store)
    # Pin the first account from the independently authenticated worker. A different
    # account cannot silently blend its history into the existing connection.
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT account FROM crm_linkedin_capture WHERE id=1').fetchone()
        if row and row['account']!=message.account:raise ValueError('LinkedIn account changed; owner action required.')
        db.execute('INSERT OR IGNORE INTO crm_linkedin_capture VALUES (1,?,0)',(message.account,))
    result=mail.ingest(store,message,method='linkedin_browser')
    with store.connect() as db:db.execute('UPDATE crm_linkedin_capture SET last_saved=? WHERE id=1',(time.time(),))
    return {**result,'saved':True,'external_id':message.external_id}


def mount(app,get_store,owner):
    from fastapi import APIRouter,Depends,HTTPException,Request,WebSocket,WebSocketDisconnect
    from fastapi.responses import JSONResponse
    router=APIRouter(prefix='/api/admin/cloud-browser',dependencies=[Depends(owner)],include_in_schema=False)
    def private(value,status=200):return JSONResponse(value,status_code=status,headers={'Cache-Control':'private, no-store'})

    @router.get('/status')
    async def status():
        initialize(get_store());saved=get_store().query('SELECT * FROM crm_linkedin_capture')
        if not configured():return private({'configured':False,'state':'not_deployed','capture':saved[0] if saved else None})
        try:return private({**await request('/status'),'configured':True,'capture':saved[0] if saved else None})
        except ValueError as error:return private({'configured':True,'state':'unavailable','error':str(error),'capture':saved[0] if saved else None})

    @router.get('/frame')
    async def frame():
        try:return private(await request('/frame'))
        except ValueError as error:raise HTTPException(503,str(error)) from error

    @router.post('/control')
    async def control(body:Control):
        try:return private(await request('/control',body.model_dump()))
        except ValueError as error:raise HTTPException(503,str(error)) from error

    @router.post('/desktop-ticket')
    def ticket():
        if not configured():raise HTTPException(503,'Private desktop is not configured.')
        return private({'ticket':desktop_ticket(get_store()),'expires_in':60})

    @app.websocket('/api/admin/cloud-browser/desktop')
    async def desktop(socket:WebSocket):
        import asyncio
        import contextlib
        from urllib.parse import urlsplit,urlunsplit
        from websockets.asyncio.client import connect
        expected=os.getenv('PUBLIC_ORIGIN','').rstrip('/')
        protocols=[p.strip() for p in socket.headers.get('sec-websocket-protocol','').split(',')]
        token=next((p[7:] for p in protocols if p.startswith('ticket.')),'')
        if not expected or socket.headers.get('origin','').rstrip('/')!=expected or 'desktop' not in protocols or not configured() or not consume_desktop_ticket(get_store(),token):
            await socket.close(code=1008);return
        endpoint=urlsplit(os.environ['CLOUD_BROWSER_URL'])
        if endpoint.scheme not in ('http','https'):
            await socket.close(code=1008);return
        target=urlunsplit(('wss' if endpoint.scheme=='https' else 'ws',endpoint.netloc,endpoint.path.rstrip('/')+'/desktop','',''))
        try:
            async with connect(target,additional_headers={'Authorization':'Bearer '+os.environ['CLOUD_BROWSER_TOKEN']},proxy=None,max_size=4_194_304,open_timeout=10) as upstream:
                await socket.accept(subprotocol='desktop')
                async def to_owner():
                    async for value in upstream:
                        if not isinstance(value,bytes):raise ValueError('Expected desktop binary data.')
                        await socket.send_bytes(value)
                async def to_worker():
                    while True:
                        value=await socket.receive_bytes()
                        if len(value)>4_194_304:raise ValueError('Desktop frame too large.')
                        await upstream.send(value)
                tasks=[asyncio.create_task(to_owner()),asyncio.create_task(to_worker())]
                try:
                    async with asyncio.timeout(1800):
                        done,_=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
                        for task in done:await task
                finally:
                    for task in tasks:task.cancel()
                    await asyncio.gather(*tasks,return_exceptions=True)
        except (Exception,asyncio.TimeoutError):pass
        finally:
            with contextlib.suppress(RuntimeError):await socket.close(code=1013)

    app.include_router(router)

    @app.post('/api/hooks/linkedin',include_in_schema=False)
    async def capture(request:Request):
        try:return private(receive(get_store(),request.headers.get('authorization',''),await request.json()))
        except PermissionError:return private({'detail':'Invalid capture authorization.'},403)
        except ValueError:return private({'detail':'Invalid capture or account mismatch.'},422)
        except Exception:return private({'detail':'Capture could not be saved; retry delivery.'},503)


def tools(ctx):
    if ctx['mode']!='private':return []
    from langchain.tools import tool

    @tool
    async def cloud_browser_status() -> dict:
        """Read the cloud LinkedIn listener state, saved-message counts, and retry backlog. Transport connectivity alone is not proof that a message was saved."""
        try:return await request('/status')
        except ValueError as error:return {'error':str(error)}

    @tool
    async def control_cloud_browser(action: str) -> dict:
        """Start, pause, resume or disconnect the private LinkedIn capture worker. Captured messages are retained. Does not send LinkedIn messages. Sign-in and credential entry belong to the owner in the private browser view."""
        from .crm_imports import job_for_turn
        from .crm_enrichment import job_for_turn as enrichment_for_turn
        if job_for_turn(ctx['store'],ctx.get('turn_id','')) or enrichment_for_turn(ctx['store'],ctx.get('turn_id','')):return {'error':'This task is restricted to its assigned record.'}
        if action not in ('start','pause','resume','disconnect'):return {'error':'Unsupported capture action.'}
        try:return await request('/control',{'action':action})
        except ValueError as error:return {'error':str(error)}

    return [cloud_browser_status,control_cloud_browser]


# Tickets are single-use, short-lived, and hashed at rest. They travel in a
# WebSocket subprotocol rather than a URL/query string or a browser storage slot.
def desktop_ticket(store):
    import hashlib
    initialize(store)
    token=secrets.token_urlsafe(32)
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS crm_desktop_tickets(digest TEXT PRIMARY KEY,expires REAL NOT NULL)')
        db.execute('DELETE FROM crm_desktop_tickets WHERE expires<?',(time.time(),))
        db.execute('INSERT INTO crm_desktop_tickets VALUES (?,?)',(hashlib.sha256(token.encode()).hexdigest(),time.time()+60))
    return token


def consume_desktop_ticket(store,token):
    import hashlib
    if not isinstance(token,str) or len(token)>100:return False
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS crm_desktop_tickets(digest TEXT PRIMARY KEY,expires REAL NOT NULL)')
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('DELETE FROM crm_desktop_tickets WHERE digest=? RETURNING expires',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    return bool(row and row['expires']>=time.time())
