"""Private conversation inspection and content-free UI analytics.

Uses the application's persistent volume, never the source tree. The analytics
relay deliberately cannot export prompts, tool payloads, arbitrary URLs, or owner panel contents.
"""
import hashlib
import json
import os
import time
import uuid
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from typing import Literal


SCHEMA = """
CREATE TABLE IF NOT EXISTS ui_events(
 id INTEGER PRIMARY KEY, created REAL NOT NULL, session TEXT NOT NULL,
 name TEXT NOT NULL, section TEXT NOT NULL, target TEXT NOT NULL, theme TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ui_events_created ON ui_events(created);
CREATE TABLE IF NOT EXISTS owner_browsers(visitor_hash TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS visitor_arrivals(
 id INTEGER PRIMARY KEY AUTOINCREMENT, visitor_hash TEXT NOT NULL,
 created REAL NOT NULL, last_seen REAL NOT NULL);
CREATE INDEX IF NOT EXISTS visitor_arrivals_browser ON visitor_arrivals(visitor_hash,last_seen);

"""
SECTIONS = {'conversation', 'work', 'person', 'bookshelf', 'podcasts', 'brain', 'behind-the-curtain'}
POSTHOG_HOSTS = {'us': 'https://us.i.posthog.com', 'eu': 'https://eu.i.posthog.com'}


def initialize(store):
    with store.connect() as db:
        db.executescript(SCHEMA)


def record_arrival(store, visitor_hash):
    if not visitor_hash:
        return
    now = time.time()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT id,last_seen FROM visitor_arrivals WHERE visitor_hash=? ORDER BY id DESC LIMIT 1', (visitor_hash,)).fetchone()
        if row and now-row['last_seen'] < 1800:
            db.execute('UPDATE visitor_arrivals SET last_seen=? WHERE id=?', (now,row['id']))
        else:
            db.execute('INSERT INTO visitor_arrivals(visitor_hash,created,last_seen) VALUES (?,?,?)', (visitor_hash,now,now))
        db.execute('DELETE FROM visitor_arrivals WHERE last_seen < ?', (now-30*86400,))
        db.execute('DELETE FROM visitor_arrivals WHERE id < (SELECT id FROM visitor_arrivals ORDER BY id DESC LIMIT 1 OFFSET 49999)')


def enabled():
    return bool(os.getenv('POSTHOG_PROJECT_TOKEN')) and os.getenv('POSTHOG_REGION', 'us') in POSTHOG_HOSTS


def record_ui(store, event):
    # Only enums and a one-way hash leave this function. Discard arbitrary URL
    # paths, queries, hashes, attribution tags, names and referrers entirely.
    section = urlsplit(event.url).fragment
    section = section if section in SECTIONS else 'conversation'
    session = hashlib.sha256(event.session_id.encode()).hexdigest() if event.session_id else ''
    row = {'name': event.name, 'section': section, 'target': event.target, 'theme': event.theme}
    with store.connect() as db:
        db.execute('INSERT INTO ui_events(created,session,name,section,target,theme) VALUES (?,?,?,?,?,?)',
                   (time.time(), session, *row.values()))
        db.execute('DELETE FROM ui_events WHERE created < ?', (time.time()-30*86400,))
        db.execute('DELETE FROM ui_events WHERE id < (SELECT id FROM ui_events ORDER BY id DESC LIMIT 1 OFFSET 49999)')
    properties = {
            '$process_person_profile': False, '$geoip_disable': True,
            'section': section, 'target': event.target, 'theme': event.theme}
    # Allow only public static routes. Outreach slugs and arbitrary paths can
    # contain names, so report them as the home page, never as raw URLs.
    path = urlsplit(event.url).path
    path = path if path in {'/', '/agents', '/agents.md', '/llms.txt'} else '/'
    origin = os.getenv('PUBLIC_ORIGIN', '').rstrip('/')
    properties.update({'$current_url': origin+path+('#'+section if section != 'conversation' else ''),
                       '$host': urlsplit(origin).netloc, '$pathname': path})
    if event.session_id and event.session_id[12] == '7':
        # Preserve the UUIDv7 time prefix, hash its random bits. No raw browser
        # identifier or conversation cookie is exported.
        value = event.session_id[:12]+'7'+session[13:16]+'8'+session[17:32]
        properties['$session_id'] = str(uuid.UUID(hex=value))
    return {'event': '$pageview' if event.name == 'pageview' else event.name,
            'distinct_id': session or str(uuid.uuid4()), 'properties': properties}


async def deliver_posthog(payload):
    token = os.getenv('POSTHOG_PROJECT_TOKEN', '')
    if not token:
        return
    # Fixed regional upstream; neither visitors nor configuration can supply a URL.
    host = POSTHOG_HOSTS.get(os.getenv('POSTHOG_REGION', 'us'))
    if not host:
        return
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            await client.post(host+'/i/v0/e/', json={'api_key': token, **payload})
    except httpx.HTTPError:
        pass  # Best effort external delivery; local metrics are the fallback.


def conversation(db, ident):
    row = db.execute('SELECT * FROM threads WHERE id=?', (ident,)).fetchone()
    if not row:
        raise HTTPException(404, 'Conversation not found.')
    result = dict(row)
    turns = []
    for record in db.execute('SELECT * FROM turns WHERE thread_id=? ORDER BY created,id', (ident,)):
        turn = dict(record)
        tid = turn['id']
        turn['events'] = [dict(r) for r in db.execute('SELECT * FROM events WHERE turn_id=? ORDER BY id', (tid,))]
        turn['tools'] = [dict(r) for r in db.execute('SELECT * FROM tool_results WHERE turn_id=? ORDER BY rowid', (tid,))]
        turn['artifacts'] = [dict(r) for r in db.execute('SELECT * FROM artifacts WHERE turn_id=? ORDER BY rowid', (tid,))]
        transcripts = db.execute('SELECT body FROM deep_transcripts WHERE turn_id=?', (tid,)).fetchone()
        turn['transcript'] = json.loads(transcripts['body']) if transcripts else None
        runtime = db.execute('SELECT runtime FROM turn_runtime WHERE turn_id=?', (tid,)).fetchone()
        turn['runtime'] = runtime['runtime'] if runtime else 'legacy'
        snapshot = db.execute('SELECT body FROM execution_context WHERE turn_id=?', (tid,)).fetchone()
        config = json.loads(snapshot['body']).get('config', {}) if snapshot else {}
        # Do not export system prompts, retrieved brain context, tokens or URLs.
        turn['model'] = {k: config[k] for k in ('provider', 'model') if k in config}
        turn['workflow_id'] = 'turn-'+tid+'-'+str((db.execute('SELECT generation FROM dispatch_attempts WHERE turn_id=?', (tid,)).fetchone() or [0])[0])
        turns.append(turn)
    result['turns'] = turns
    return result


def install(app, owner_dependency):
    dependencies = [Depends(owner_dependency)]

    @app.put('/api/owner/browser', dependencies=dependencies, include_in_schema=False)
    def mark_browser(request: Request, mine: bool = True):
        import re
        import secrets
        from .public_access import COOKIE, digest
        token = request.cookies.get(COOKIE, '')
        if not re.fullmatch(r'[a-f0-9]{64}', token):
            token = secrets.token_hex(32)
        with request.app.state.public_store.connect() as db:
            if mine:
                db.execute('INSERT OR IGNORE INTO owner_browsers VALUES (?)', (digest(token),))
            else:
                db.execute('DELETE FROM owner_browsers WHERE visitor_hash=?', (digest(token),))
        result = JSONResponse({'mine':mine}, headers={'Cache-Control':'no-store'})
        result.set_cookie(COOKIE,token,max_age=30*86400,secure=request.url.scheme=='https' or os.getenv('PUBLIC_ORIGIN','').startswith('https://'),httponly=True,samesite='lax')
        return result

    @app.get('/api/owner/visitors', dependencies=dependencies, include_in_schema=False)
    def visitors(request: Request, after: int = Query(default=0,ge=0)):
        from .public_access import COOKIE, digest
        store = request.app.state.public_store
        with store.connect() as db:
            # Keep the alert cursor and rows in one snapshot so arrivals cannot
            # fall between the two reads and be silently skipped by the browser.
            db.execute('BEGIN')
            rows = [dict(row) for row in db.execute("SELECT a.id,a.created,a.last_seen,substr(a.visitor_hash,1,12) AS browser FROM visitor_arrivals a LEFT JOIN owner_browsers o ON o.visitor_hash=a.visitor_hash WHERE o.visitor_hash IS NULL AND a.id>? ORDER BY a.id DESC LIMIT 100", (after,))]
            cursor = db.execute('SELECT COALESCE(MAX(id),0) FROM visitor_arrivals').fetchone()[0]
            mine = bool(db.execute('SELECT 1 FROM owner_browsers WHERE visitor_hash=?', (digest(request.cookies.get(COOKIE,'')),)).fetchone())
        return JSONResponse({'arrivals':rows,'cursor':cursor,'mine':mine},headers={'Cache-Control':'no-store'})

    @app.get('/api/owner/observability', dependencies=dependencies, include_in_schema=False)
    def metrics(request: Request):
        store = request.app.state.public_store
        since = time.time()-30*86400
        counts = store.query('SELECT name,COUNT(*) AS count FROM ui_events WHERE created>=? GROUP BY name', (since,))
        sessions = store.query("SELECT COUNT(DISTINCT session) AS count FROM ui_events WHERE created>=? AND session!=''", (since,))[0]['count']
        sections = store.query("SELECT section,COUNT(*) AS count FROM ui_events WHERE created>=? AND name='pageview' GROUP BY section ORDER BY count DESC", (since,))
        turns = store.query('SELECT status,COUNT(*) AS count FROM turns WHERE created>=? GROUP BY status', (since,))
        return JSONResponse({'days': 30, 'events': counts, 'sessions': sessions, 'sections': sections,
                             'turns': turns, 'posthog_configured': enabled(),
                             'posthog_region': os.getenv('POSTHOG_REGION', 'us')}, headers={'Cache-Control':'no-store'})

    @app.get('/api/owner/conversations', dependencies=dependencies, include_in_schema=False)
    def conversations(request: Request, before: int | None = None,
                      q: str = Query(default='', max_length=200),
                      origin: Literal['all','visitor','owner','unknown'] = 'all',
                      limit: int = Query(default=25, ge=1, le=100)):
        # Keyset pagination uses SQLite rowid so concurrent new chats do not shift
        # already-inspected pages. Search text is bound, never interpolated SQL.
        store = request.app.state.public_store
        where = ' WHERE (? IS NULL OR h.rowid < ?) AND (? = \'\' OR EXISTS (SELECT 1 FROM turns s WHERE s.thread_id=h.id AND (instr(lower(s.prompt),lower(?))>0 OR instr(lower(COALESCE(s.result,\'\')),lower(?))>0)))'
        classification = "CASE WHEN EXISTS (SELECT 1 FROM visitor_threads v JOIN owner_browsers o ON o.visitor_hash=v.visitor_hash WHERE v.thread_id=h.id) THEN 'owner' WHEN EXISTS (SELECT 1 FROM visitor_threads v WHERE v.thread_id=h.id) THEN 'visitor' ELSE 'unknown' END"
        where += " AND (?='all' OR ("+classification+")=?)"
        args = (before, before, q, q, q, origin, origin)
        rows = store.query('SELECT h.rowid AS cursor,h.id,h.mode,COUNT(t.id) AS turn_count,MIN(t.created) AS created,MAX(t.created) AS updated, SUM(CASE WHEN t.status=\'failed\' THEN 1 ELSE 0 END) AS failed FROM threads h LEFT JOIN turns t ON t.thread_id=h.id'+where+' GROUP BY h.rowid ORDER BY h.rowid DESC LIMIT ?', (*args, limit+1))
        page = rows[:limit]
        for row in page:
            prompt = store.query('SELECT prompt FROM turns WHERE thread_id=? ORDER BY created,id LIMIT 1', (row['id'],))
            browser = store.query('SELECT substr(visitor_hash,1,12) AS browser FROM visitor_threads WHERE thread_id=?', (row['id'],))
            row['browser'] = browser[0]['browser'] if browser else None
            row['origin'] = store.query('SELECT '+classification+' AS origin FROM threads h WHERE h.id=?', (row['id'],))[0]['origin']
            row['preview'] = prompt[0]['prompt'][:180] if prompt else 'Empty conversation'
        return JSONResponse({'conversations':page, 'next': page[-1]['cursor'] if len(rows)>limit else None}, headers={'Cache-Control':'no-store'})

    @app.get('/api/owner/conversations/{ident}', dependencies=dependencies, include_in_schema=False)
    def detail(ident: str, request: Request):
        with request.app.state.public_store.connect() as db:
            db.execute('BEGIN')  # Read a consistent snapshot while the worker writes.
            result = conversation(db, ident)
        return JSONResponse(result, headers={'Cache-Control':'no-store'})
