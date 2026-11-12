"""Narrow outbound worker contract for the owner's paired Mac."""
import os
import secrets
import time

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class CheckIn(BaseModel):
    ready: bool


class Outcome(BaseModel):
    delivered: bool | None = None
    reply: str | None = Field(default=None, min_length=1, max_length=20000)


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS muse_bridge_state (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), seen REAL NOT NULL, ready INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS muse_turns (
            id TEXT PRIMARY KEY REFERENCES agent_turns(id), status TEXT NOT NULL, reply TEXT);
    """)


def availability(store):
    if not os.getenv('MUSE_BRIDGE_TOKEN'):
        return 'Muse companion is unavailable.'
    rows = store.query('SELECT ready,seen FROM muse_bridge_state WHERE singleton=1')
    if not rows or time.time() - rows[0]['seen'] > 90:
        return 'Waiting for the paired Muse companion to connect.'
    return '' if rows[0]['ready'] else 'Muse companion is disconnected or needs pairing.'


def mount(app, get_store):
    from . import conversations

    def authorize(authorization: str = Header(default='')):
        token = os.getenv('MUSE_BRIDGE_TOKEN')
        if not token or not secrets.compare_digest(authorization.encode(), ('Bearer '+token).encode()):
            raise HTTPException(401, 'Muse companion authentication required.')

    router = APIRouter(prefix='/api/muse-companion', dependencies=[Depends(authorize)], include_in_schema=False)

    def private(value):
        return JSONResponse(value, headers={'Cache-Control': 'private, no-store'})

    @router.post('/claim')
    def claim(body: CheckIn):
        store = get_store()
        conversations.initialize(store)
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR REPLACE INTO muse_bridge_state VALUES (1,?,?)', (time.time(), body.ready))
            if not body.ready:
                return private({'send': None, 'pending': []})
            pending = [r[0] for r in db.execute("""SELECT id FROM muse_turns
                WHERE status IN ('sending','delivered','delivery_unknown')""")]
            row = db.execute("""SELECT t.id,t.prompt FROM agent_turns t JOIN muse_turns m ON m.id=t.id
                WHERE m.status='queued' ORDER BY t.created LIMIT 1""").fetchone()
            if row:
                # Reserve before returning. A lost HTTP response can never trigger another send.
                db.execute("UPDATE muse_turns SET status='sending' WHERE id=?", (row['id'],))
            return private({'send': dict(row) if row else None, 'pending': pending})

    @router.post('/turns/{ident}')
    def outcome(ident: str, body: Outcome):
        if (body.reply is None) == (body.delivered is None):
            raise HTTPException(422, 'Supply a send outcome or a reply.')
        store = get_store()
        conversations.initialize(store)
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM muse_turns WHERE id=?', (ident,)).fetchone()
            if not row or row['status'] not in ('sending', 'delivered', 'delivery_unknown', 'completed'):
                raise HTTPException(404, 'Claimed Muse turn not found.')
            new_reply = body.reply is not None and row['reply'] != body.reply
            if body.reply is not None:
                if row['reply'] is not None and row['reply'] != body.reply:
                    raise HTTPException(409, 'A reply already exists.')
                db.execute("UPDATE muse_turns SET status='completed',reply=? WHERE id=?", (body.reply, ident))
            elif row['status'] == 'sending':
                status = 'delivered' if body.delivered else 'delivery_unknown'
                db.execute('UPDATE muse_turns SET status=? WHERE id=?', (status, ident))
        if new_reply:
            from .helper_delegation import mark_result
            mark_result(store, ident)
        return private({'accepted': True})

    app.include_router(router)
