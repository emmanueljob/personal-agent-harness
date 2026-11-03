"""Authenticated outbound Mac worker; Ollama itself never listens on the internet."""
import asyncio
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from .paths import data_directory

PREFIX = '/api/model-bridge'


@contextmanager
def connect():
    root = data_directory()
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root/'model-bridge.sqlite', timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE IF NOT EXISTS heartbeat(id INTEGER PRIMARY KEY, model TEXT, seen REAL);
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, payload TEXT, status TEXT,
            created REAL, claim TEXT, result TEXT);
    ''')
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def available():
    if not os.getenv('MODEL_BRIDGE_TOKEN') or not (data_directory()/'model-bridge.sqlite').exists():
        return False
    with connect() as db:
        row = db.execute('SELECT model,seen FROM heartbeat WHERE id=1').fetchone()
    return bool(row and row['model']==os.getenv('OLLAMA_MODEL','qwen3.8:27b') and row['seen']>time.time()-25)


def authorize(request):
    token = os.getenv('MODEL_BRIDGE_TOKEN', '')
    if not token:
        raise HTTPException(404, 'Not found')
    if not secrets.compare_digest(request.headers.get('authorization','').encode(), ('Bearer '+token).encode()):
        raise HTTPException(401, 'Bridge authentication required.')


async def body(request):
    content = bytearray()
    async for part in request.stream():
        content.extend(part)
        if len(content)>128000:
            raise HTTPException(413, 'Bridge payload too large.')
    try:
        value = json.loads(content)
        if not isinstance(value, dict): raise ValueError()
        return value
    except (ValueError, UnicodeError):
        raise HTTPException(422, 'Invalid bridge payload.') from None


def install(app):
    router = APIRouter(prefix=PREFIX, include_in_schema=False)

    @router.post('/heartbeat')
    async def heartbeat(request: Request):
        authorize(request)
        value = await body(request)
        if value.get('model')!=os.getenv('OLLAMA_MODEL','qwen3.8:27b'):
            raise HTTPException(422, 'Unexpected local model.')
        with connect() as db:
            db.execute('INSERT OR REPLACE INTO heartbeat VALUES (1,?,?)',
                       (value['model'], time.time() if value.get('online') is True else 0))
            db.execute('DELETE FROM jobs WHERE created<?', (time.time()-600,))
        return {'ok':True}

    @router.post('/claim')
    async def claim(request: Request):
        authorize(request)
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE status='queued' AND created>? ORDER BY created LIMIT 1", (time.time()-12,)).fetchone()
            if not row: return {'job':None}
            claim = secrets.token_hex(32)
            db.execute("UPDATE jobs SET status='running',claim=? WHERE id=?", (claim,row['id']))
            return {'job':{'id':row['id'],'claim':claim,'payload':json.loads(row['payload'])}}

    @router.post('/jobs/{ident}')
    async def finish(ident: str, request: Request):
        authorize(request)
        value = await body(request)
        result = value.get('result')
        if result is not None:
            if not isinstance(result,dict) or not isinstance(result.get('message'),dict) or result['message'].get('role')!='assistant':
                raise HTTPException(422, 'Invalid model response.')
            result = {k:v for k,v in result.items() if k in ('model','created_at','message','done','done_reason','total_duration','load_duration','prompt_eval_count','prompt_eval_duration','eval_count','eval_duration')}
        with connect() as db:
            row = db.execute("SELECT claim FROM jobs WHERE id=? AND status='running'", (ident,)).fetchone()
            if not row or not isinstance(value.get('claim'),str) or not secrets.compare_digest(row['claim'],value['claim']):
                raise HTTPException(404, 'Job not found.')
            db.execute('UPDATE jobs SET status=?,result=? WHERE id=?',
                       ('done' if result is not None else 'failed',json.dumps(result),ident))
        return {'ok':True}

    @router.post('/api/chat')
    async def chat(request: Request):
        authorize(request)
        if not request.client or request.client.host not in ('127.0.0.1','::1'):
            raise HTTPException(403, 'Internal inference endpoint.')
        value = await body(request)
        if not available(): raise HTTPException(503, 'Local model is offline.')
        if value.get('model')!=os.getenv('OLLAMA_MODEL','qwen3.8:27b') or not isinstance(value.get('messages'),list):
            raise HTTPException(422, 'Unexpected local inference request.')
        # Forward only the Ollama chat contract, never URLs, files, or service credentials.
        payload = {k:v for k,v in value.items() if k in ('model','messages','tools','format','options','think','keep_alive')}
        payload['stream'] = False
        ident = str(uuid.uuid4())
        created = time.time()
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running') AND created>?",(created-250,)).fetchone()[0]>=2:
                raise HTTPException(503, 'Local model is busy.')
            db.execute("INSERT INTO jobs VALUES (?,?,'queued',?,NULL,NULL)",(ident,json.dumps(payload),created))
        try:
            while time.time()-created<250:
                with connect() as db: row = db.execute('SELECT status,result FROM jobs WHERE id=?',(ident,)).fetchone()
                if not row or row['status']=='failed': raise HTTPException(503, 'Local inference unavailable.')
                if row['status']=='done':
                    return Response(row['result']+'\n', media_type='application/x-ndjson' if value.get('stream') else 'application/json')
                if row['status']=='queued' and time.time()-created>12:
                    raise HTTPException(503, 'Local worker disconnected.')
                await asyncio.sleep(.2)
            raise HTTPException(504, 'Local inference timed out.')
        finally:
            with connect() as db: db.execute('DELETE FROM jobs WHERE id=?',(ident,))

    app.include_router(router)
