"""Owner-requested, transactional actions for Command Center suggestions."""
import json
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import Field

from . import career_email, command_center, crm


class Execute(crm.Model):
    revision: int = Field(ge=1)
    dispositionVersion: int = Field(ge=0)
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{8,80}$')
    kind: Literal['draft_follow_up', 'check_captured_replies', 'prepare_review']


def initialize(store):
    command_center.initialize(store)
    with store.connect() as db:
        db.executescript("""CREATE TABLE IF NOT EXISTS command_action_results (
            id TEXT PRIMARY KEY, card_id TEXT NOT NULL, kind TEXT NOT NULL,
            revision INTEGER NOT NULL, disposition_version INTEGER NOT NULL,
            request_id TEXT NOT NULL UNIQUE, result TEXT NOT NULL,
            UNIQUE(card_id,kind,revision,disposition_version));
            CREATE TABLE IF NOT EXISTS command_action_requests (
                request_id TEXT PRIMARY KEY, result_id TEXT NOT NULL REFERENCES command_action_results(id));
            """)


def suggestion(card):
    wording = card['action'].strip().casefold().rstrip('.')
    if wording in ('review the reply', 'review the plan'):
        return 'prepare_review', 'Prepare review brief', ''
    if wording == 'check for a reply' and card.get('receiptId'):
        return 'check_captured_replies', 'Check captured replies', ''
    if wording in ('follow up', 'follow-up', 'draft follow-up', 'draft a follow-up', 'draft follow up'):
        if card.get('companyId'):
            return 'draft_follow_up', 'Draft a follow-up', ''
        return 'unsupported', 'Draft a follow-up', 'Link a company before preparing a draft.'
    return 'unsupported', 'Do it', 'Automatic execution of this specific goal is unavailable. You can discuss it in chat or complete it yourself.'


def descriptor(db, card):
    kind, label, reason = suggestion(card)
    row = db.execute("""SELECT result FROM command_action_results WHERE card_id=? AND kind=?
        AND revision=? AND disposition_version=?""",
        (card['id'], kind, card['revision'], card['dispositionVersion'])).fetchone()
    if row is None:
        row = db.execute('SELECT result FROM command_action_results WHERE card_id=? ORDER BY rowid DESC LIMIT 1',
                         (card['id'],)).fetchone()
    if card['disposition'] != 'active':
        reason = 'Restore this suggestion to active before executing it.'
    return {'kind': kind, 'label': label, 'supported': kind != 'unsupported' and not reason,
            'reason': reason, 'revision': card['revision'], 'dispositionVersion': card['dispositionVersion'],
            'result': json.loads(row['result']) if row else None}


def captured_replies(db, card):
    receipt = db.execute('SELECT * FROM crm_messages WHERE id=?', (card['receiptId'],)).fetchone()
    if not receipt:
        raise HTTPException(409, 'The captured message is no longer available.')
    if not receipt['occurred_at']:
        raise HTTPException(409, 'The captured message has no reliable date for checking later replies.')
    hidden = career_email.hidden_activity_ids(db)
    rows = db.execute("""SELECT id,activity_id,occurred_at FROM crm_messages
        WHERE provider=? AND account=? AND thread_id=? AND direction='inbound'
        AND occurred_at>? ORDER BY occurred_at,id""",
        (receipt['provider'], receipt['account'], receipt['thread_id'], receipt['occurred_at'])).fetchall()
    replies = [dict(row) for row in rows if row['activity_id'] not in hidden]
    return {'status': 'completed', 'summary': (
        f"Found {len(replies)} later captured reply/replies in this conversation."
        if replies else 'No later visible reply is captured in this conversation.'),
        'checkedAt': datetime.now(timezone.utc).isoformat(), 'replies': replies,
        'scope': 'Captured My Career messages only. Refresh the source integration to capture new messages.'}


def draft_follow_up(db, card, action_id):
    draft = (f"Hi {card['person'] or 'there'},\n\n"
             f"I'm following up regarding {card['company']}. "
             "Could you let me know whether there are any updates or a useful next step?\n\nThank you.")
    ident = str(uuid.uuid4())
    record = crm.Activity(company_id=card['companyId'], person_id=card.get('personId') or '',
        title=('Follow-up draft: '+card['title'])[:240]+' · '+action_id[:8],
        channel='note', stage='drafted', notes=draft,
        summary='Follow-up draft prepared from a Command Center suggestion. Review and personalize before sending.')
    crm.write_record(db, record.model_dump(mode='json'), ident, 0, actor='command-center-action')
    return {'status': 'drafted', 'summary': 'Saved a starter follow-up draft in My Career for review.',
            'draftId': ident, 'draftText': draft, 'scope': 'Template draft saved. Review and personalize before a separate send action.'}



def prepare_review(db, card):
    brief = (f"{card['title']}\nCompany: {card['company'] or 'Unlinked'}\n"
             f"Contact: {card['person'] or 'Unlinked'}\nStatus: {card['stage']}\n"
             f"Current next step: {card['action']}\nReason: {card['why']}\n"
             f"Source: {card['source']}\nDate: {card['date'] or 'Unknown'}\n\n"
             f"Captured evidence\n{card['evidence'] or 'No captured excerpt is available.'}")
    return {'status': 'completed', 'summary': 'Prepared a review brief from the current record and captured evidence.',
            'briefText': brief, 'checkedAt': datetime.now(timezone.utc).isoformat(),
            'scope': 'Evidence brief saved. The owner can review it and decide the next step.'}


def execute(store, ident, body):
    initialize(store)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        card = command_center.resolve_card(db, ident)
        if card is None:
            raise HTTPException(404, 'This suggestion is no longer available.')
        # Eligibility is checked even on retries so hidden or removed cards cannot leak old content.
        old = db.execute('SELECT r.* FROM command_action_requests q JOIN command_action_results r ON r.id=q.result_id WHERE q.request_id=?', (body.request_id,)).fetchone()
        identity = (card['id'], body.kind, body.revision, body.dispositionVersion)
        if old:
            if tuple(old[k] for k in ('card_id', 'kind', 'revision', 'disposition_version')) != identity:
                raise HTTPException(409, 'This request ID belongs to another action.')
            return {**descriptor(db, card), 'kind': body.kind, 'result': json.loads(old['result'])}
        if (card['revision'], card['dispositionVersion']) != (body.revision, body.dispositionVersion):
            raise HTTPException(409, 'This suggestion changed. Refresh before executing.')
        info = descriptor(db, card)
        if info['kind'] != body.kind or not info['supported']:
            raise HTTPException(409, info['reason'] or 'This action is no longer available.')
        if (info['result'] and info['result']['kind'] == body.kind
                and info['result']['revision'] == body.revision
                and info['result']['dispositionVersion'] == body.dispositionVersion):
            db.execute('INSERT INTO command_action_requests VALUES (?,?)', (body.request_id, info['result']['id']))
            return info
        action_id = str(uuid.uuid4())
        try:
            executor = {'draft_follow_up': lambda: draft_follow_up(db, card, action_id),
                        'check_captured_replies': lambda: captured_replies(db, card),
                        'prepare_review': lambda: prepare_review(db, card)}
            result = executor[body.kind]()
        except ValueError:
            raise HTTPException(409, 'Related records changed. Refresh the suggestion before executing.')
        result.update(id=action_id, kind=body.kind, revision=body.revision,
                      dispositionVersion=body.dispositionVersion)
        db.execute('INSERT INTO command_action_results VALUES (?,?,?,?,?,?,?)',
                   (action_id, *identity, body.request_id, json.dumps(result)))
        db.execute('INSERT INTO command_action_requests VALUES (?,?)', (body.request_id, action_id))
        return {**info, 'result': result}


def mount(app, get_store, owner):
    router = APIRouter(prefix='/api/admin/command-center/cards', dependencies=[Depends(owner)],
                       include_in_schema=False)

    def private(value):
        return JSONResponse(value, headers={'Cache-Control': 'private, no-store'})

    @router.get('/{ident}/execution')
    def read(ident: str):
        store = get_store()
        initialize(store)
        with store.connect() as db:
            db.execute('BEGIN')
            card = command_center.resolve_card(db, ident)
            if card is None:
                raise HTTPException(404, 'This suggestion is no longer available.')
            return private(descriptor(db, card))

    @router.post('/{ident}/execute')
    def run(ident: str, body: Execute):
        return private(execute(get_store(), ident, body))

    app.include_router(router)
