"""Private conversation inspection and content-free UI analytics.

Uses the application's persistent volume, never the source tree. The analytics
relay deliberately cannot export prompts, tool payloads, URLs, or owner panel contents.
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


SCHEMA = """
CREATE TABLE IF NOT EXISTS ui_events(
 id INTEGER PRIMARY KEY, created REAL NOT NULL, session TEXT NOT NULL,
 name TEXT NOT NULL, section TEXT NOT NULL, target TEXT NOT NULL, theme TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ui_events_created ON ui_events(created);
"""
SECTIONS = {'conversation', 'work', 'person', 'bookshelf', 'behind-the-curtain'}
POSTHOG_HOSTS = {'us': 'https://us.i.posthog.com', 'eu': 'https://eu.i.posthog.com'}


def initialize(store):
    with store.connect() as db:
        db.executescript(SCHEMA)


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
    return {'event': event.name, 'distinct_id': session or str(uuid.uuid4()), 'properties': {
            '$process_person_profile': False, '$geoip_disable': True,
            'section': section, 'target': event.target, 'theme': event.theme}}


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
                      limit: int = Query(default=25, ge=1, le=100)):
        # Keyset pagination uses SQLite rowid so concurrent new chats do not shift
        # already-inspected pages. Search text is bound, never interpolated SQL.
        store = request.app.state.public_store
        where = ' WHERE (? IS NULL OR h.rowid < ?) AND (? = \'\' OR EXISTS (SELECT 1 FROM turns s WHERE s.thread_id=h.id AND (instr(lower(s.prompt),lower(?))>0 OR instr(lower(COALESCE(s.result,\'\')),lower(?))>0)))'
        args = (before, before, q, q, q)
        rows = store.query('SELECT h.rowid AS cursor,h.id,h.mode,COUNT(t.id) AS turn_count,MIN(t.created) AS created,MAX(t.created) AS updated, SUM(CASE WHEN t.status=\'failed\' THEN 1 ELSE 0 END) AS failed FROM threads h LEFT JOIN turns t ON t.thread_id=h.id'+where+' GROUP BY h.rowid ORDER BY h.rowid DESC LIMIT ?', (*args, limit+1))
        page = rows[:limit]
        for row in page:
            prompt = store.query('SELECT prompt FROM turns WHERE thread_id=? ORDER BY created,id LIMIT 1', (row['id'],))
            row['preview'] = prompt[0]['prompt'][:180] if prompt else 'Empty conversation'
        return JSONResponse({'conversations':page, 'next': page[-1]['cursor'] if len(rows)>limit else None}, headers={'Cache-Control':'no-store'})

    @app.get('/api/owner/conversations/{ident}', dependencies=dependencies, include_in_schema=False)
    def detail(ident: str, request: Request):
        with request.app.state.public_store.connect() as db:
            db.execute('BEGIN')  # Read a consistent snapshot while the worker writes.
            result = conversation(db, ident)
        return JSONResponse(result, headers={'Cache-Control':'no-store'})
