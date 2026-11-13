"""Owner home cards derived from current career records and their evidence."""
import json
from datetime import date, datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import Field
from typing import Literal

from . import career_email, correspondence, crm, card_intents


class Review(crm.Model):
    revision: int = Field(ge=1)


class Disposition(crm.Model):
    revision: int = Field(ge=0)
    disposition_version: int = Field(ge=0)
    state: Literal['done', 'later', 'dismissed', 'active']
    request_id: str = Field(min_length=8, max_length=100)
    intent_revision: str = Field(default='', max_length=64)


def initialize(store):
    correspondence.initialize(store)
    with store.connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS command_center_reviews (
            record_id TEXT PRIMARY KEY, stage TEXT NOT NULL, next_action TEXT NOT NULL, follow_up TEXT)""")
        db.execute('''CREATE TABLE IF NOT EXISTS command_center_dispositions (
            card_id TEXT PRIMARY KEY, version INTEGER NOT NULL, state TEXT NOT NULL, resume_at TEXT)''')
        db.execute('''CREATE TABLE IF NOT EXISTS command_center_disposition_requests (
            request_id TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS command_center_intent_decisions (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT UNIQUE, card_id TEXT NOT NULL,
            intent_key TEXT NOT NULL, evidence TEXT NOT NULL, state TEXT NOT NULL,
            resume_at TEXT, version INTEGER NOT NULL, snapshot TEXT NOT NULL)''')
        db.execute('BEGIN')
        pending = card_intents.needs_backfill(db)
        db.rollback()
        if pending:
            db.execute('BEGIN IMMEDIATE')
            card_intents.backfill(db)


ROWS = """
    SELECT r.id, r.kind, r.body, r.revision, r.evidence, r.certainty, r.updated, r.created,
           json_extract(c.body,'$.name') AS company, json_extract(p.body,'$.name') AS person,
           m.id AS receipt_id, m.provider, m.direction, m.occurred_at, m.body AS message_body,
           json_extract(m.body,'$.subject') AS subject,
           substr(json_extract(m.body,'$.text'),1,400) AS excerpt,
           json_extract(m.body,'$.source_url') AS source_url, seen.stage AS reviewed_stage, seen.next_action AS reviewed_action, seen.follow_up AS reviewed_due,
           EXISTS (SELECT 1 FROM crm_messages later WHERE later.provider=m.provider
              AND later.account=m.account AND later.thread_id=m.thread_id AND later.direction='inbound'
              AND later.occurred_at>m.occurred_at) AS has_reply
    FROM crm_records r
    LEFT JOIN crm_records c ON c.id=r.company_id
    LEFT JOIN crm_records p ON p.id=json_extract(r.body,'$.person_id')
    LEFT JOIN crm_messages m ON m.activity_id=r.id
    LEFT JOIN command_center_reviews seen ON seen.record_id=r.id
    WHERE r.id NOT IN (SELECT alias_id FROM crm_redirects)
      AND (r.kind='activity' OR json_extract(r.body,'$.next_action')!=''
                             OR json_extract(r.body,'$.follow_up') IS NOT NULL OR seen.record_id IS NOT NULL)
"""


def card(row, today):
    record = json.loads(row['body'])
    stage = record['stage']
    if stage in ('cancelled', 'closed', 'withdrawn', 'rejected'):
        return None
    due = record.get('follow_up')
    action = record.get('next_action') or ''
    if due and not action:
        action = 'Follow up'
    why = ''
    rank = 4
    if action:
        rank = 0 if due and due <= today else 2
        why = f'Follow-up scheduled for {due}.' if due else 'My Career has a next step for this record.'
    elif row['receipt_id'] and row['direction'] == 'outbound' and not row['has_reply']:
        rank, action = 2, 'Check for a reply'
        why = 'Outreach was sent. No later reply is recorded in this conversation.'
    elif stage == 'replied':
        rank, action, why = 1, 'Review the reply', 'A reply was recorded after outreach.'
    elif stage in ('drafted', 'proposed'):
        rank, action, why = 3, 'Review the plan', 'This activity is still in preparation.'
    elif row['has_reply']:
        why = 'A later reply is recorded in this conversation.'
    else:
        why = 'Recorded in My Career.'
    reviewed = row['reviewed_stage'] == stage and not record.get('next_action') and not due
    if reviewed:
        action, rank, why = '', 4, 'The next step was marked done.'
    evidence = row['excerpt'] or row['evidence'] or record.get('summary') or record.get('notes') or ''
    provider = {'linkedin': 'LinkedIn', 'gmail': 'Gmail'}.get(row['provider'])
    source = f'{provider} message' if provider else row['certainty'].replace('_', ' ')
    return {
        'id': row['id'], 'revision': row['revision'], 'kind': row['kind'],
        'title': record.get('title') or record['name'], 'company': record['name'] if row['kind'] == 'company' else row['company'] or '',
        'companyId': row['id'] if row['kind'] == 'company' else record.get('company_id', ''),
        'person': record['name'] if row['kind'] == 'person' else row['person'] or '',
        'personId': row['id'] if row['kind'] == 'person' else record.get('person_id', ''), 'stage': stage, 'action': action,
        'why': why, 'source': source, 'evidence': evidence[:400],
        'date': (row['occurred_at'] or record.get('happened_on') or '')[:10],
        'sourceUrl': row['source_url'] or '', 'receiptId': row['receipt_id'],
        'rank': rank, 'reviewed': reviewed, 'due': due,
    }


def import_cards(db):
    """Show the latest status per report without exposing report bodies or worker errors."""
    rows = db.execute("""SELECT s.id, s.created, json_extract(s.body,'$.title') AS title,
               s.provider, count(*) AS total,
               sum(CASE WHEN t.status='failed' THEN 1 ELSE 0 END) AS failed,
               sum(CASE WHEN t.status='completed' THEN 1 ELSE 0 END) AS completed,
               sum(CASE WHEN t.status IN ('running','queued') THEN 1 ELSE 0 END) AS active
        FROM crm_import_jobs j JOIN crm_sources s ON s.id=j.source_id
        LEFT JOIN turns t ON t.id=j.turn_id GROUP BY s.id
        ORDER BY s.created DESC, s.id""")
    for row in rows:
        if row['failed']:
            stage, rank, why = 'failed', 0, 'Part of this career report could not be imported.'
        elif row['completed'] == row['total']:
            stage, rank, why = 'completed', 4, 'The career report finished importing.'
        elif row['active']:
            stage, rank, why = 'working', 3, 'Your agent is importing this career report.'
        else:
            stage, rank, why = 'waiting', 3, 'This career report is waiting for a model.'
        entry = {
            'id': 'import-' + row['id'], 'kind': 'import', 'revision': 0,
            'title': row['title'], 'company': '', 'companyId': '', 'person': '', 'personId': '',
            'stage': stage, 'action': '', 'why': why, 'source': row['provider'] + ' career report',
            'evidence': f"{row['completed']} of {row['total']} parts completed; {row['failed']} failed.",
            'date': datetime.fromtimestamp(row['created'], timezone.utc).date().isoformat(),
            'sourceUrl': '', 'receiptId': None, 'rank': rank, 'reviewed': False, 'due': None,
        }
        yield ((rank, '9999', -row['created'], entry['id']), entry)


def ranked_cards(db, hidden):
    today = date.today().isoformat()
    ranked = []
    context = card_intents.catalog(db)
    snapshots = {row['card_id']: json.loads(row['snapshot']) for row in
                 db.execute('SELECT card_id,snapshot FROM command_center_intent_decisions ORDER BY seq')}
    for row in db.execute(ROWS):
        if row['id'] in hidden:
            continue
        entry = card(row, today)
        if entry:
            record = json.loads(row['body'])
            task = entry
            if entry['reviewed']:
                old_action = row['reviewed_action'] or snapshots.get(row['id'], {}).get('action', '')
                record.update(next_action=old_action, follow_up=row['reviewed_due'])
                task = card({**dict(row), 'body':json.dumps(record), 'reviewed_stage':None}, today)
            receipt = json.loads(row['message_body']) if row['message_body'] else None
            entry.update(card_intents.describe(task, record, receipt, context))
            entry.update(created=row['created'], intentAction=task['action'], intentRank=task['rank'], intentWhy=task['why'], intentDue=task['due'])
            ranked.append(((task['rank'], task['due'] or '9999', -row['updated'], entry['id']), entry))
    for order, entry in import_cards(db):
        entry.update(intentKey='import:'+entry['id'], intentOperation='information',
                     evidenceFacts=[], created=-order[2], intentAction='')
        ranked.append((order, entry))
    return card_intents.group(db, ranked)


def feed(store, scope='all', offset=0):
    initialize(store)
    with store.connect() as db:
        db.execute('BEGIN')
        entries = ranked_cards(db, career_email.hidden_activity_ids(db))
    entries = [entry for entry in entries if (
        (scope == 'completed' and entry['reviewed'])
        or (scope == 'review' and entry['disposition'] == 'active' and entry['action'])
        or (scope in ('later', 'dismissed') and entry['disposition'] == scope)
        or (scope == 'all' and entry['disposition'] in ('active', 'done')))]
    return {'cards': entries[offset:offset+40],
            'nextOffset': offset+40 if offset+40 < len(entries) else None}


def chat_summary(entry):
    return {key: entry[key] for key in (
        'id', 'title', 'company', 'person', 'stage', 'why', 'action', 'source', 'date', 'reviewed', 'disposition', 'resumeAt',
        'intentKey', 'memberIds', 'intentOperation', 'intentRevision', 'newEvidence')}


def chat_evidence(db, selected_id='', previous_ids=()):
    """Read inside the send transaction, after initialize(store). Hidden content stays excluded."""
    hidden = career_email.hidden_activity_ids(db)
    entries = ranked_cards(db, hidden)
    by_id = {ident: entry for entry in entries for ident in entry['memberIds']}
    from .crm_imports import canonical_id
    references = []
    # Recheck prior card identities even if they have fallen beyond the home page.
    identities = list(dict.fromkeys([selected_id, *previous_ids]))[:21]
    for ident in identities:
        if not ident:
            continue
        current_id = canonical_id(db, ident)
        if current_id in hidden:
            references.append({'id': ident, 'eligibility': 'hidden', 'action': ''})
        elif current_id in by_id:
            references.append({'requestedId': ident, 'eligibility': 'visible' if by_id[current_id]['disposition'] in ('active', 'done') else by_id[current_id]['disposition'],
                               **chat_summary(by_id[current_id]),
                               'evidence': by_id[current_id]['evidence'] if ident == selected_id else ''})
        else:
            references.append({'id': ident, 'eligibility': 'unavailable', 'action': ''})
    eligible = [entry for entry in entries if entry['disposition'] in ('active', 'done')]
    return {
        'surface': 'command-center',
        'asOf': datetime.now(timezone.utc).isoformat(),
        'coverage': {'visibleCards': len(eligible), 'includedCards': min(8, len(eligible)),
                     'complete': len(eligible) <= 8},
        'cards': [chat_summary(entry) for entry in eligible[:8]],
        'references': references,
    }


def complete_step(db, ident, revision):
    row = db.execute(ROWS + ' AND r.id=?', (ident,)).fetchone()
    if not row or ident in career_email.hidden_activity_ids(db):
        raise HTTPException(404, 'This card is no longer available.')
    if row['revision'] != revision:
        raise HTTPException(409, 'The record changed. Refresh before completing this step.')
    entry = resolve_card(db, ident)
    if not entry or not (entry['action'] or entry['reviewed']):
        raise HTTPException(409, 'This step is no longer pending.')
    record = json.loads(row['body'])
    if entry['reviewed']:
        return {'id': ident, 'revision': revision}
    previous_action, previous_due = record.get('next_action', ''), record.get('follow_up')
    if previous_action or previous_due:
        record.update(next_action='', follow_up=None)
        crm.write_record(db, record, ident, revision)
        revision += 1
    db.execute('INSERT OR REPLACE INTO command_center_reviews VALUES (?,?,?,?)',
               (ident, record['stage'], previous_action, previous_due))
    card_intents.save(db, entry, 'done')
    return {'id': ident, 'revision': revision}


def reopen_step(db, ident, revision):
    row = db.execute(ROWS + ' AND r.id=?', (ident,)).fetchone()
    if not row or ident in career_email.hidden_activity_ids(db):
        raise HTTPException(404, 'This card is no longer available.')
    entry = resolve_card(db, ident)
    if not entry or not entry['reviewed'] or row['revision'] != revision:
        raise HTTPException(409, 'The record changed. Refresh before reopening this step.')
    previous = db.execute('SELECT next_action, follow_up FROM command_center_reviews WHERE record_id=?',
                          (ident,)).fetchone()
    record = json.loads(row['body'])
    if previous and (previous['next_action'] or previous['follow_up']):
        record.update(next_action=previous['next_action'], follow_up=previous['follow_up'])
        crm.write_record(db, record, ident, revision)
    db.execute('DELETE FROM command_center_reviews WHERE record_id=?', (ident,))
    card_intents.save(db, entry, 'active')
    return {'id': ident}


def mark_done(store, ident, revision):
    initialize(store)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        return complete_step(db, ident, revision)


def reopen(store, ident, revision):
    initialize(store)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        return reopen_step(db, ident, revision)


def resolve_card(db, ident):
    from .crm_imports import canonical_id
    current_id = canonical_id(db, ident)
    return next((entry for entry in ranked_cards(db, career_email.hidden_activity_ids(db))
                 if current_id in entry['memberIds']), None)


def disposition(store, ident, body):
    initialize(store)
    payload = json.dumps({'id': ident, **body.model_dump()}, sort_keys=True)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT * FROM command_center_disposition_requests WHERE request_id=?',
                              (body.request_id,)).fetchone()
        if previous:
            original = json.loads(previous['payload'])
            original.setdefault('intent_revision', '')
            if original != json.loads(payload):
                raise HTTPException(409, 'This request belongs to a different card decision.')
            return json.loads(previous['result'])
        entry = resolve_card(db, ident)
        if not entry:
            raise HTTPException(404, 'This card is no longer available.')
        if ((entry['revision'], entry['dispositionVersion']) != (body.revision, body.disposition_version)
                or (body.intent_revision and body.intent_revision != entry['intentRevision'])):
            raise HTTPException(409, 'This card changed. Refresh before deciding.')
        if body.state != 'active' and (entry['disposition'] != 'active' or not entry['action']):
            raise HTTPException(409, 'This suggestion is no longer pending.')
        if body.state == 'done':
            complete_step(db, entry['id'], body.revision)
        elif body.state == 'active' and entry['reviewed']:
            reopen_step(db, entry['id'], body.revision)
        resume_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat() if body.state == 'later' else None
        version = entry['dispositionVersion'] + 1
        db.execute('INSERT OR REPLACE INTO command_center_dispositions VALUES (?,?,?,?)',
                   (entry['id'], version, 'active' if body.state == 'done' else body.state, resume_at))
        card_intents.save(db, entry, body.state, request_id=body.request_id, version=version, resume_at=resume_at)
        result = {'id': entry['id'], 'dispositionVersion': version, 'state': body.state, 'resumeAt': resume_at}
        db.execute('INSERT INTO command_center_disposition_requests VALUES (?,?,?)',
                   (body.request_id, payload, json.dumps(result)))
        return result


def mount(app, get_store, owner):
    router = APIRouter(prefix='/api/admin/command-center', dependencies=[Depends(owner)],
                       include_in_schema=False)

    def private(data):
        return JSONResponse(data, headers={'Cache-Control': 'private, no-store'})

    @router.get('')
    def home(scope: Literal['all', 'review', 'completed', 'later', 'dismissed'] = 'all', offset: int = Query(default=0, ge=0)):
        return private(feed(get_store(), scope, offset))

    @router.get('/cards/{ident}')
    def detail(ident: str):
        store = get_store()
        initialize(store)
        with store.connect() as db:
            entry = resolve_card(db, ident)
        if not entry:
            raise HTTPException(404, 'This card is no longer available.')
        return private(entry)

    @router.get('/contacts')
    def contacts():
        store = get_store()
        crm.initialize(store)
        return private(store.query("""SELECT id,kind,company_id,json_extract(body,'$.name') AS name
            FROM crm_records WHERE kind IN ('company','person')
              AND id NOT IN (SELECT alias_id FROM crm_redirects) ORDER BY name,id"""))

    @router.post('/cards/{ident}/complete')
    def complete(ident: str, body: Review):
        return private(mark_done(get_store(), ident, body.revision))

    @router.post('/cards/{ident}/reopen')
    def undo(ident: str, body: Review):
        return private(reopen(get_store(), ident, body.revision))

    @router.post('/cards/{ident}/disposition')
    def decide(ident: str, body: Disposition):
        return private(disposition(get_store(), ident, body))

    app.include_router(router)
