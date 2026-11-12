"""Owner-requested, transactional actions for Command Center suggestions."""
import json
import re
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
    kind: Literal['draft_follow_up', 'draft_outreach', 'check_captured_replies', 'prepare_review', 'delegate_research']
    intentRevision: str = Field(default='', max_length=128)


RESULT_SCHEMA = """CREATE TABLE IF NOT EXISTS {name} (
    id TEXT PRIMARY KEY, card_id TEXT NOT NULL, kind TEXT NOT NULL,
    revision INTEGER NOT NULL, disposition_version INTEGER NOT NULL,
    request_id TEXT NOT NULL UNIQUE, result TEXT NOT NULL,
    intent_revision TEXT NOT NULL DEFAULT '',
    UNIQUE(card_id,kind,revision,disposition_version,intent_revision))"""


def _bind_legacy_intents(db):
    # A legacy result has no group fingerprint. Adopt its first current grouped
    # snapshot once, preserving the original result body and every request ID.
    for row in db.execute("SELECT * FROM command_action_results WHERE intent_revision=''").fetchall():
        card = command_center.resolve_card(db, row['card_id'])
        if not card or not card.get('intentRevision') or card['dispositionVersion']!=row['disposition_version']:
            continue
        intents = getattr(command_center, 'card_intents', None)
        if intents:
            old = intents.historical(db, row['card_id'], row['revision'], intents.catalog(db))
            matches = old and old['intentKey']==card['intentKey'] and set(card['evidenceFacts'])<=set(old['evidenceFacts'])
        else:
            original = db.execute('SELECT revision FROM crm_records WHERE id=?', (row['card_id'],)).fetchone()
            matches = original and original['revision']==row['revision']
        if matches:
            db.execute('UPDATE command_action_results SET intent_revision=? WHERE id=?',
                       (card['intentRevision'], row['id']))


def initialize(store):
    command_center.initialize(store)
    with store.connect() as db:
        db.execute(RESULT_SCHEMA.format(name='command_action_results'))
        db.execute("""CREATE TABLE IF NOT EXISTS command_action_requests (
            request_id TEXT PRIMARY KEY, result_id TEXT NOT NULL REFERENCES command_action_results(id))""")
        db.commit()
        db.execute('BEGIN IMMEDIATE')
        if 'intent_revision' not in {row['name'] for row in db.execute('PRAGMA table_info(command_action_results)')}:
            db.execute(RESULT_SCHEMA.format(name='command_action_results_revised'))
            db.execute("""INSERT INTO command_action_results_revised
                SELECT id,card_id,kind,revision,disposition_version,request_id,result,''
                FROM command_action_results""")
            db.execute('DROP TABLE command_action_results')
            db.execute('ALTER TABLE command_action_results_revised RENAME TO command_action_results')
        _bind_legacy_intents(db)


def suggestion(card):
    wording = re.sub(r'[\s_-]+', ' ', card['action'].casefold()).strip(' .!?')
    operation = card.get('intentOperation')
    if not wording or re.match(r"^(do not|don't|never|avoid|stop)\b", wording):
        return 'unsupported', 'Manual step', 'Inspect this instruction in the record before deciding what to do.'
    if (operation in ('review_reply', 'review_plan', 'review')
            or re.match(r'^(?:prepare (?:a |the )?)?(?:review|read|assess|evaluate|compare)\b', wording)):
        return 'prepare_review', 'Prepare review brief', ''
    if operation=='check_reply' or re.match(r'^(?:check|look|see|confirm).*(?:reply|replies|responded|response)\b', wording):
        if card.get('receiptId'):
            return 'check_captured_replies', 'Check captured replies', ''
        return 'unsupported', 'Inspect correspondence', 'Open the record to link captured correspondence for this check.'
    if operation in ('follow_up', 'draft_follow_up') or re.match(r'^(?:(?:draft|write|send|prepare) (?:a |the )?)?follow up\b|^check in\b', wording):
        if card.get('companyId'):
            return 'draft_follow_up', 'Draft a follow-up', 'Prepares a starter draft for review.'
    if re.match(r'^(?:(?:draft|write|compose|send) (?:an? |the )?(?:introduction|intro|outreach|email|message|note)\b|(?:contact|reach out)\b)', wording):
        if card.get('companyId'):
            return 'draft_outreach', 'Draft an outreach message', 'Prepares a starter message for review.'
    if operation=='research' or re.match(r'^(?:research|investigate|find out|learn about)\b', wording):
        return 'delegate_research', 'Request research from helpers', 'Queues scoped research with your helpers. Their reports remain unverified until reviewed.'
    if operation=='apply' or re.match(r'^(?:apply|submit|attend|call|schedule)\b', wording):
        return 'unsupported', 'Continue in the record', 'This step needs the application, meeting or delivery workflow.'
    return 'unsupported', 'Inspect this goal', 'Open the record to review the goal and choose its next step.'


def _target_ids(db, card):
    targets = set(card.get('memberIds') or [card['id']])
    aliases = db.execute("""SELECT alias_id FROM crm_redirects
        WHERE record_id IN (SELECT value FROM json_each(?))""", (json.dumps(sorted(targets)),))
    targets.update(row['alias_id'] for row in aliases)
    return json.dumps(sorted(targets))


def _current_result(row, card, kind):
    if not row or row['kind']!=kind:
        return False
    intent_revision = card.get('intentRevision', '')
    if intent_revision:
        return row['intent_revision']==intent_revision
    return (row['card_id'], row['revision'], row['disposition_version']) == (
        card['id'], card['revision'], card['dispositionVersion'])


def _result(row):
    return {**json.loads(row['result']), 'intentRevision': row['intent_revision']}


def descriptor(db, card):
    kind, label, reason = suggestion(card)
    targets = _target_ids(db, card)
    intent_revision = card.get('intentRevision', '')
    if intent_revision:
        row = db.execute("""SELECT * FROM command_action_results
            WHERE card_id IN (SELECT value FROM json_each(?)) AND kind=? AND intent_revision=?
            ORDER BY rowid DESC LIMIT 1""", (targets, kind, intent_revision)).fetchone()
    else:
        row = db.execute("""SELECT * FROM command_action_results WHERE card_id=? AND kind=?
            AND revision=? AND disposition_version=? ORDER BY rowid DESC LIMIT 1""",
            (card['id'], kind, card['revision'], card['dispositionVersion'])).fetchone()
    if row is None:
        row = db.execute("""SELECT * FROM command_action_results
            WHERE card_id IN (SELECT value FROM json_each(?)) ORDER BY rowid DESC LIMIT 1""", (targets,)).fetchone()
    if card['disposition'] != 'active':
        reason = 'Restore this suggestion to active before executing it.'
    button = {'draft_follow_up':'Draft follow-up', 'draft_outreach':'Draft message',
              'prepare_review':'Prepare brief', 'delegate_research':'Request research', 'check_captured_replies':'Check replies'}.get(kind, 'Open record')
    result = _result(row) if row else None
    if result and result.get('helperRequestId'):
        from .helper_delegation import read_request
        result['delegation'] = read_request(db, result['helperRequestId'])
    return {'kind': kind, 'label': label, 'supported': kind != 'unsupported' and not (
                card['disposition'] != 'active'),
            'reason': reason, 'buttonLabel': button,
            'revision': card['revision'], 'dispositionVersion': card['dispositionVersion'],
            'intentRevision': intent_revision, 'resultCurrent': _current_result(row, card, kind),
            'result': result}


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
    from .crm_imports import canonical_id
    replies = []
    for row in rows:
        activity_id = canonical_id(db, row['activity_id'])
        if activity_id not in hidden:
            replies.append({**dict(row), 'activity_id': activity_id})
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


def draft_outreach(db, card, action_id):
    draft = (f"Hi {card['person'] or 'there'},\n\n"
             f"I wanted to connect about {card['company']}. "
             "Would you be open to a short conversation about the team and potential opportunities?\n\nThank you.")
    ident = str(uuid.uuid4())
    record = crm.Activity(company_id=card['companyId'], person_id=card.get('personId') or '',
        title=('Outreach draft: '+card['title'])[:240]+' · '+action_id[:8],
        channel='note', stage='drafted', notes=draft,
        summary='Starter outreach draft for owner review. Goal: '+card['action'])
    crm.write_record(db, record.model_dump(mode='json'), ident, 0, actor='command-center-action')
    return {'status':'drafted', 'summary':'Saved a starter outreach draft in My Career for review.',
            'draftId':ident, 'draftText':draft,
            'scope':'Template draft saved for review and personalization before a separate send action.'}



def prepare_review(db, card):
    brief = (f"{card['title']}\nCompany: {card['company'] or 'Unlinked'}\n"
             f"Contact: {card['person'] or 'Unlinked'}\nStatus: {card['stage']}\n"
             f"Current next step: {card['action']}\nReason: {card['why']}\n"
             f"Source: {card['source']}\nDate: {card['date'] or 'Unknown'}\n\n"
             f"Captured evidence\n{card['evidence'] or 'No captured excerpt is available.'}")
    return {'status': 'completed', 'summary': 'Prepared a review brief from the current record and captured evidence.',
            'briefText': brief, 'checkedAt': datetime.now(timezone.utc).isoformat(),
            'scope': 'Evidence brief saved. The owner can review it and decide the next step.'}


def delegate_research(store, db, card, action_id):
    from .helper_delegation import WorkRequest, request_work
    job = request_work(store, WorkRequest(request_id=action_id, source_kind='card_action',
        source_id=action_id, kind='research', instruction=card['action'], card_id=card['id'],
        revision=card['revision'], disposition_version=card['dispositionVersion'],
        intent_revision=card.get('intentRevision'), include_card=True), connection=db)
    return {'status':'delegated', 'summary':'Saved a scoped research request for your helpers.',
            'helperRequestId':job['id'], 'delegation':job,
            'scope':'Helper reports require review. This request does not authorize outreach or external delivery.'}


def execute(store, ident, body):
    initialize(store)
    if body.kind=='delegate_research':
        from . import helper_delegation
        helper_delegation.initialize(store)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        card = command_center.resolve_card(db, ident)
        if card is None:
            raise HTTPException(404, 'This suggestion is no longer available.')
        # Eligibility is checked even on retries so hidden or removed cards cannot leak old content.
        old = db.execute('SELECT r.* FROM command_action_requests q JOIN command_action_results r ON r.id=q.result_id WHERE q.request_id=?', (body.request_id,)).fetchone()
        identity = (card['id'], body.kind, body.revision, body.dispositionVersion)
        intent_revision = body.intentRevision or card.get('intentRevision', '')
        if old:
            previous_card = command_center.resolve_card(db, old['card_id'])
            if (not previous_card or previous_card['id']!=card['id']
                    or (old['kind'],old['revision'],old['disposition_version']) != identity[1:]
                    or (body.intentRevision and old['intent_revision']!=body.intentRevision)):
                raise HTTPException(409, 'This request ID belongs to another action.')
            return {**descriptor(db, card), 'kind': body.kind, 'result': _result(old),
                    'resultCurrent': _current_result(old, card, body.kind)}
        if (card['revision'], card['dispositionVersion']) != (body.revision, body.dispositionVersion):
            raise HTTPException(409, 'This suggestion changed. Refresh before executing.')
        if body.intentRevision and body.intentRevision!=card.get('intentRevision', ''):
            raise HTTPException(409, 'This group changed. Refresh before executing.')
        info = descriptor(db, card)
        if info['kind'] != body.kind or not info['supported']:
            raise HTTPException(409, info['reason'] or 'This action is no longer available.')
        if info['resultCurrent']:
            db.execute('INSERT INTO command_action_requests VALUES (?,?)', (body.request_id, info['result']['id']))
            return info
        action_id = str(uuid.uuid4())
        try:
            executor = {'draft_follow_up': lambda: draft_follow_up(db, card, action_id),
                        'check_captured_replies': lambda: captured_replies(db, card),
                        'draft_outreach': lambda: draft_outreach(db, card, action_id),
                        'prepare_review': lambda: prepare_review(db, card),
                        'delegate_research': lambda: delegate_research(store, db, card, action_id)}
            result = executor[body.kind]()
        except ValueError:
            raise HTTPException(409, 'Related records changed. Refresh the suggestion before executing.')
        result.update(id=action_id, kind=body.kind, revision=body.revision,
                      dispositionVersion=body.dispositionVersion, intentRevision=intent_revision)
        db.execute('INSERT INTO command_action_results VALUES (?,?,?,?,?,?,?,?)',
                   (action_id, *identity, body.request_id, json.dumps(result), intent_revision))
        db.execute('INSERT INTO command_action_requests VALUES (?,?)', (body.request_id, action_id))
        return {**info, 'result': result, 'resultCurrent': True}


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
