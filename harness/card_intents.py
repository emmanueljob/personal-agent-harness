"""Deterministic task identities and evidence snapshots for Home decisions."""
import hashlib
import json
import re
import unicodedata


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def catalog(db):
    redirects = dict(db.execute('SELECT alias_id,record_id FROM crm_redirects'))
    entities = {}
    for row in db.execute("SELECT id,kind,body FROM crm_records WHERE kind IN ('company','person','application')"):
        entities[row['id']] = {'kind': row['kind'], **json.loads(row['body'])}
        entities[row['id']]['labels'] = {entities[row['id']].get('name') or entities[row['id']].get('title') or ''}
    for row in db.execute('''SELECT c.record_id,json_extract(c.body,'$.name') AS name,
        json_extract(c.body,'$.title') AS title FROM crm_changes c
        JOIN crm_records r ON r.id=c.record_id WHERE r.kind IN ('company','person','application')'''):
        entities[row['record_id']]['labels'].add(row['name'] or row['title'] or '')
    return redirects, entities


def wording(value):
    text = ' '.join(re.findall(r'[^\W_]+', unicodedata.normalize('NFKC', value).casefold()))
    if not text:
        return value.strip().casefold()
    for old, new in (
        (r'\bfollowup\b', 'follow up'), (r'\bcheck in\b', 'follow up'),
        (r'\bcircle back\b', 'follow up'), (r'\bresponse\b', 'reply'),
        (r'\bopportunity\b', 'role'), (r'\bposition\b', 'role'),
        (r'\bassess\b', 'review'), (r'\bevaluate\b', 'review'),
        (r'\binvestigate\b', 'research'),
    ):
        text = re.sub(old, new, text)
    return ' '.join(word for word in text.split() if word not in {'a', 'an', 'the', 'please', 'your', 'my', 'to', 'this', 'that', 'current'})


def operation(action):
    text = wording(action)
    patterns = (
        ('draft_follow_up', r'^(?:prepare|write|draft)(?: an?)? (?:follow up)(?: draft)?\b'),
        ('follow_up', r'^(?:send )?follow up\b'),
        ('check_reply', r'^check (?:for |inbox for )?(?:a )?reply\b'),
        ('review_reply', r'^(?:review|read) (?:new )?reply\b'),
        ('review_plan', r'^(?:review|read) plan\b'),
        ('research', r'^research\b'),
        ('review', r'^review\b'),
        ('contact', r'^(?:reach out|contact|send (?:a )?(?:message|email)|email|message)\b'),
        ('apply', r'^(?:apply|submit application)\b'),
        ('prepare', r'^(?:prepare|write|draft)\b'),
    )
    for family, pattern in patterns:
        match = re.match(pattern, text)
        if match:
            return family, text[match.end():].strip()
    return 'custom', text




def without_schedule(value):
    value = re.sub(r'\b(?:today|tomorrow|tonight|soon|later|(?:this|next) (?:week|month))\b', '', value)
    weekday = r'(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)'
    value = re.sub(r'\b(?:on|next|this) '+weekday+r'\b', '', value)
    if re.fullmatch(weekday, value.strip()):
        return ''
    value = re.sub(r'\bin \d+ (?:days?|weeks?|months?)\b', '', value)
    value = re.sub(r'\bat \d{1,2}(?: \d{2})? ?(?:am|pm)\b', '', value)
    value = re.sub(r'\b(?:on )?20\d{2} \d{1,2} \d{1,2}\b', '', value)
    return value


def evidence_wording(value, labels):
    text = wording(value)
    for label, ident in labels:
        text = re.sub(r'\b'+re.escape(label)+r'\b', 'target-'+ident, text)
    return text


def describe(entry, record, receipt, context):
    redirects, entities = context
    company = redirects.get(entry['companyId'], entry['companyId'])
    person = redirects.get(entry['personId'], entry['personId'])
    application = entry['id'] if entry['kind']=='application' else record.get('application_id', '')
    application = redirects.get(application, application)
    action = entry['action']
    text = wording(action)
    # An explicitly named, unique contact provides a target for a company-level task.
    if not person:
        matches = {redirects.get(ident, ident) for ident, entity in entities.items() if entity['kind']=='person'
                   and redirects.get(entity.get('company_id'), entity.get('company_id'))==company
                   and any(len(label.split())>=2 and re.search(r'\b'+re.escape(wording(label))+r'\b', text)
                           for label in entity['labels'])}
        if len(matches)==1:
            person = next(iter(matches))
    if not application:
        matches = {redirects.get(ident, ident) for ident, entity in entities.items() if entity['kind']=='application'
                   and redirects.get(entity.get('company_id'), entity.get('company_id'))==company
                   and any(len(label.split())>=2 and re.search(r'\b'+re.escape(wording(label))+r'\b', text)
                           for label in entity['labels'])}
        if len(matches)==1:
            application = next(iter(matches))
    peer = []
    if receipt and not person:
        people = receipt.get('recipients', []) if receipt['direction']=='outbound' else [receipt['sender']]
        peer = sorted({value.get('email', '').casefold() or value.get('profile_url', '').rstrip('/').casefold()
                       for value in people} - {''})
    family, qualifier = operation(action)
    roles = {company:'company', person:'person', application:'application'}
    labels = {(wording(label), roles[redirects.get(target, target)])
              for target, entity in entities.items()
              if redirects.get(target, target) in roles
              for label in entity['labels'] if wording(label)}
    labels = sorted(labels, key=lambda value: (-len(value[0]), value[0], value[1]))
    for label, _ in labels:
        qualifier = re.sub(r'\b'+re.escape(label)+r'\b', '', qualifier)
    if family!='custom':
        qualifier = without_schedule(qualifier)
    qualifier = ' '.join(word for word in qualifier.split()
                         if word not in {'with', 'for', 'at', 'about', 'on', 'from', 'regarding', 'company', 'contact'})
    conversation = ''
    if receipt and wording(action).startswith('check'):
        conversation = [receipt['provider'], receipt['account'].casefold(), receipt['thread_id']]
    identity = [company, person or peer, application, family, qualifier, conversation]
    # Informational cards keep their own identities. An absent action is not an intent.
    if not action:
        identity = ['record', entry['id']]
    facts = set()
    stage = record.get('stage')
    if stage in ('sent', 'contacted') and family!='follow_up':
        facts.add('stage:outreach_sent')
    elif stage in ('replied', 'applied', 'screen', 'interview', 'offer', 'rejected', 'withdrawn', 'connected'):
        facts.add('stage:'+stage)
    if record.get('happened_on'):
        facts.add('date:'+record['happened_on'])
    content = receipt.get('text', '') if receipt else record.get('notes') or record.get('summary') or ''
    content = evidence_wording(content, labels)
    if receipt:
        facts.add('message:'+digest([receipt['direction'], receipt.get('occurred_at'), content]))
    elif content:
        facts.add('content:'+digest(content))
    if entry.get('evidence') and not receipt:
        facts.add('evidence:'+digest(evidence_wording(entry['evidence'], labels)))
    return {'intentKey': digest(identity), 'intentIdentity': identity, 'intentOperation': family, 'evidenceFacts': sorted(facts),
            'personId': person, 'person': entities.get(person, {}).get('name', '') or entry['person'],
            'intentApplicationId': application}


def save(db, entry, state, *, request_id=None, version=0, resume_at=None):
    snapshot = {key: entry.get(key) for key in ('action', 'due', 'stage', 'id', 'revision')}
    snapshot['action'] = entry.get('intentAction') or entry.get('action', '')
    snapshot['intentIdentity'] = entry.get('intentIdentity')
    db.execute('''INSERT OR IGNORE INTO command_center_intent_decisions
        (request_id,card_id,intent_key,evidence,state,resume_at,version,snapshot) VALUES (?,?,?,?,?,?,?,?)''',
        (request_id, entry['id'], entry['intentKey'], json.dumps(entry['evidenceFacts']), state,
         resume_at, version, json.dumps(snapshot)))


def historical(db, ident, revision, context, review=None):
    """Recover the task as seen by old clients, using the requested CRM revision."""
    from .command_center import card
    row = db.execute('SELECT * FROM crm_records WHERE id=?', (ident,)).fetchone()
    if not row:
        return None
    row = dict(row)
    if revision:
        old = db.execute('SELECT body,evidence FROM crm_changes WHERE record_id=? AND revision=? ORDER BY id DESC LIMIT 1',
                         (ident, revision)).fetchone()
        if old:
            row.update(body=old['body'], evidence=old['evidence'], revision=revision)
    record = json.loads(row['body'])
    if review:
        record.update(stage=review['stage'], next_action=review['next_action'], follow_up=review['follow_up'])
        row['body'] = json.dumps(record)
    entities = context[1]
    message = db.execute('SELECT body,id,occurred_at FROM crm_messages WHERE activity_id=? ORDER BY created,id LIMIT 1',
                         (ident,)).fetchone()
    receipt = json.loads(message['body']) if message else None
    row.update(company=entities.get(record.get('company_id'), {}).get('name', ''),
               person=entities.get(record.get('person_id'), {}).get('name', ''),
               receipt_id=message['id'] if message else None,
               provider=receipt['provider'] if receipt else None, direction=receipt['direction'] if receipt else None,
               occurred_at=message['occurred_at'] if message else None, excerpt=receipt.get('text', '')[:400] if receipt else '',
               source_url=receipt.get('source_url', '') if receipt else '', has_reply=False, reviewed_stage=None)
    entry = card(row, '0000-01-01')
    if entry:
        entry.update(describe(entry, record, receipt, context))
    return entry


def backfill(db):
    """Import old receipts once. Their requested revisions preserve harmless-edit history."""
    context = catalog(db)
    for row in db.execute('''SELECT d.* FROM command_center_dispositions d
        WHERE NOT EXISTS (SELECT 1 FROM command_center_intent_decisions i
                          WHERE i.card_id=d.card_id AND i.version=d.version)
          AND NOT EXISTS (SELECT 1 FROM command_center_disposition_requests q
                          WHERE json_extract(q.result,'$.id')=d.card_id
                            AND json_extract(q.result,'$.dispositionVersion')=d.version)
        ORDER BY d.rowid'''):
        review = db.execute('SELECT * FROM command_center_reviews WHERE record_id=?', (row['card_id'],)).fetchone()
        entry = historical(db, row['card_id'], None, context, review)
        if entry:
            save(db, entry, 'done' if review and row['state']=='active' else row['state'],
                 request_id='legacy-disposition:'+row['card_id']+':'+str(row['version']),
                 version=row['version'], resume_at=row['resume_at'])
    for review in db.execute('SELECT * FROM command_center_reviews ORDER BY rowid'):
        key = 'legacy-review:'+review['record_id']
        if db.execute('SELECT 1 FROM command_center_intent_decisions WHERE card_id=?', (review['record_id'],)).fetchone():
            continue
        entry = historical(db, review['record_id'], None, context, review)
        if entry:
            save(db, entry, 'done', request_id=key)

    for row in db.execute('''SELECT q.* FROM command_center_disposition_requests q
        LEFT JOIN command_center_intent_decisions d ON d.request_id=q.request_id
        WHERE d.seq IS NULL ORDER BY q.rowid'''):
        payload, result = json.loads(row['payload']), json.loads(row['result'])
        entry = historical(db, result.get('id', payload['id']), payload.get('revision'), context)
        if entry:
            save(db, entry, payload['state'], request_id=row['request_id'],
                 version=result.get('dispositionVersion', 0), resume_at=result.get('resumeAt'))

    # Reopening a record removes its review marker. If an older client left
    # a marker after its last restore, that direct completion happened later.
    for review in db.execute('SELECT * FROM command_center_reviews'):
        entry = historical(db, review['record_id'], None, context, review)
        if not entry:
            continue
        latest = db.execute('SELECT * FROM command_center_intent_decisions WHERE intent_key=? ORDER BY seq DESC LIMIT 1',
                            (entry['intentKey'],)).fetchone()
        if latest and latest['state']=='active' and latest['card_id']==review['record_id']:
            save(db, entry, 'done', request_id='legacy-review-after:'+str(latest['seq']))


def group(db, ranked):
    """Apply one decision to the equivalent task before ranking, scopes and pagination."""
    from datetime import datetime, timezone
    decisions = {}
    redirects = dict(db.execute('SELECT alias_id,record_id FROM crm_redirects'))
    versions = dict(db.execute('SELECT card_id,version FROM command_center_dispositions'))
    for row in db.execute('''SELECT i.*,d.resume_at AS current_resume,d.version AS current_version
        FROM command_center_intent_decisions i
        LEFT JOIN command_center_dispositions d ON d.card_id=i.card_id ORDER BY i.seq'''):
        identity = json.loads(row['snapshot']).get('intentIdentity')
        key = row['intent_key']
        if identity:
            targets = (1,) if identity[0]=='record' else (0, 1, 2)
            for index in targets:
                if isinstance(identity[index], str):
                    identity[index] = redirects.get(identity[index], identity[index])
            key = digest(identity)
        decisions[key] = dict(row)
    groups = {}
    for order, entry in ranked:
        groups.setdefault(entry['intentKey'], []).append((order, entry))
    now = datetime.now(timezone.utc).isoformat()
    result = []
    for key, members in groups.items():
        # A stable oldest record supplies the actual CRM target; receipt IDs break join ties.
        _, representative = min(members, key=lambda item: (item[1]['created'], item[1]['id'], item[1]['receiptId'] or ''))
        entry = dict(representative)
        facts = sorted({fact for _, member in members for fact in member['evidenceFacts']})
        entry.update(memberIds=sorted({member['id'] for _, member in members}), evidenceFacts=facts)
        saved = decisions.get(key)
        state = saved['state'] if saved else ('done' if any(member['reviewed'] for _, member in members) else 'active')
        resume = saved['resume_at'] if saved else None
        if saved and state=='later' and saved['current_version']==saved['version']:
            resume = saved['current_resume']
        if state=='later' and resume and resume <= now:
            state = 'active'
        novel = set()
        if saved and state=='dismissed':
            novel = set(facts)-set(json.loads(saved['evidence']))
            if novel:
                state = 'active'
        new_evidence = []
        for _, member in sorted(members, key=lambda item: item[0]):
            if novel.intersection(member['evidenceFacts']):
                evidence = {field: member[field] for field in ('id', 'evidence', 'source', 'sourceUrl', 'date', 'stage')}
                if evidence not in new_evidence:
                    new_evidence.append(evidence)
        entry['newEvidence'] = new_evidence[:3]
        entry.update(disposition=state, reviewed=state=='done',
                     dispositionVersion=max([saved['version'] if saved else 0,
                                             *(versions.get(ident, 0) for ident in entry['memberIds'])]),
                     resumeAt=resume if state=='later' else None)
        entry['intentRevision'] = digest([key, facts, saved['seq'] if saved else None, state])
        order, urgent = min(members, key=lambda item: item[0])
        entry.update(action=entry['intentAction'], rank=order[0],
                     due=urgent.get('intentDue', urgent['due']),
                     why=urgent.get('intentWhy', urgent['why']))
        if state=='done':
            entry.update(action='', rank=4, why='The next step was marked done.')
        result.append(((entry['rank'], order[1], order[2], entry['id']), entry))
    result.sort(key=lambda item: item[0])
    return [entry for _, entry in result]
