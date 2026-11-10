"""Synthesize the private career brief and incorporate targeted source replies."""
import json
from pydantic import Field
from . import crm, grok_connection, my_career as career


class Narrative(crm.Model):
    summary: str = Field(min_length=1, max_length=1800)
    questions: list[str] = Field(max_length=3)


class MuseQuestion(crm.Model):
    request_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{8,80}$')
    question: str = Field(min_length=1, max_length=300)


class MuseReply(MuseQuestion):
    answer: str = Field(min_length=1, max_length=20000)


def initialize_muse(store):
    with store.connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS career_muse_questions (
            request_id TEXT PRIMARY KEY, question TEXT NOT NULL, created REAL NOT NULL)""")


def prepare_muse(store, body):
    import time
    initialize_muse(store)
    with store.connect() as db:
        db.execute('INSERT OR IGNORE INTO career_muse_questions VALUES (?,?,?)',
                   (body.request_id, body.question, time.time()))
        if db.execute('SELECT question FROM career_muse_questions WHERE request_id=?',
                      (body.request_id,)).fetchone()[0] != body.question:
            raise ValueError('This question has changed. Start a new handoff.')


def conversation(store, provider, limit):
    crm.initialize(store)
    grok_connection.initialize(store)
    initialize_muse(store)
    if provider == 'grokbot':
        rows = store.query("""SELECT a.id, q.question, a.status, a.created, a.updated,
            json_extract(a.result,'$.report') AS answer, s.id AS source_id
            FROM grok_questions q JOIN grok_assignments a ON a.id=q.task_id
            LEFT JOIN crm_sources s ON s.provider='grokbot' AND s.reference='career-question:'||a.id
            ORDER BY a.created DESC,a.id LIMIT ?""", (limit+1,))
    else:
        rows = store.query("""WITH saved AS (
            SELECT substr(reference,17) AS id,id AS source_id,created,
                   json_extract(body,'$.title') AS question,json_extract(body,'$.report') AS answer
            FROM crm_sources WHERE provider='muse' AND reference LIKE 'career-question:%')
            SELECT s.id,s.question,'completed' AS status,coalesce(q.created,s.created) AS created,
                   s.created AS updated,s.answer,s.source_id
            FROM saved s LEFT JOIN career_muse_questions q ON q.request_id=s.id
            UNION ALL SELECT q.request_id,q.question,'handoff',q.created,q.created,NULL,NULL
            FROM career_muse_questions q WHERE NOT EXISTS (SELECT 1 FROM saved WHERE id=q.request_id)
            ORDER BY created DESC,id LIMIT ?""", (limit+1,))
    processed = set(career.brief(store).get('processed', []))
    for row in rows:
        row['reviewed'] = row['source_id'] in processed
    return {'messages': rows[:limit], 'more': len(rows)>limit}


def save_muse(store, body):
    prepare_muse(store, body)
    return crm.save_source(store, crm.Source(provider='muse', reference='career-question:'+body.request_id,
                                             title=body.question, report=body.answer))


def progress(store):
    crm.initialize(store)
    grok_connection.initialize(store)
    processed = set(career.brief(store).get('processed', []))
    replies = store.query("""SELECT id,provider,json_extract(body,'$.title') AS question
                            FROM crm_sources WHERE reference LIKE 'career-question:%' ORDER BY created DESC""")
    tasks = store.query('''SELECT q.task_id,q.question,a.status FROM grok_questions q
                           JOIN grok_assignments a ON a.id=q.task_id ORDER BY a.created DESC LIMIT 20''')
    return {'pending': [r['id'] for r in reversed(replies) if r['id'] not in processed], 'tasks': tasks,
            'recent': [r for r in replies if r['id'] in processed][:10]}


def incorporate(store, ident):
    current = career.brief(store)
    processed = current.get('processed', [])
    if ident in processed:
        return
    row = store.query("SELECT body FROM crm_sources WHERE id=? AND reference LIKE 'career-question:%'", (ident,))
    if not row:
        raise ValueError('Career reply not found.')
    source = crm.Source.model_validate_json(row[0]['body'])
    claims = list(current['claims'])
    statements = [c['statement'] for c in claims]
    findings = career.infer(career.Findings,
        'Extract only new career preferences directly supported by the reply. The question itself is not '
        'evidence. Exclude duplicates, guesses, suggestions and contradictions with current preferences. '
        'Use source index 0 and exact quotes from the reply. An empty list is valid.',
        {'current': statements, 'question': source.title, 'reply': source.report})
    known = {s.casefold() for s in statements}
    for claim in findings.claims:
        if claim.source == 0 and claim.quote in source.report and claim.statement.casefold() not in known:
            claims.append({'statement': claim.statement, 'source': f'{source.provider} · {source.title}',
                           'quote': claim.quote, 'history': []})
            known.add(claim.statement.casefold())
    career.save(store, current['revision'], claims, reply_id=ident)


def narrative(store):
    current = career.brief(store)
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS crm_career_narrative(revision INTEGER PRIMARY KEY, body TEXT NOT NULL)')
        cached = db.execute('SELECT body FROM crm_career_narrative WHERE revision=?', (current['revision'],)).fetchone()
    if cached:
        return json.loads(cached['body'])
    if not current['claims']:
        return {'summary':'','questions':['What has the owner said would make a role worth pursuing, and why?']}
    result = career.infer(Narrative,
        'Write one or two short connected paragraphs addressed to the owner about the work they want, '
        'their priorities and tradeoffs. Preserve floors versus targets and flexibility. Use only the '
        'current sourced preferences. Suggest up to three specific questions to ask an assistant that '
        'remembers conversations with the owner. Focus on gaps that would distinguish otherwise plausible jobs. '
        'Ask for past examples or the owner’s words. Keep each question under 300 characters.',
        [{'preference':c['statement'], 'evidence':c['quote']} for c in current['claims']])
    value = result.model_dump()
    value['questions'] = [q.strip() for q in result.questions if 0 < len(q.strip()) <= 300]
    with store.connect() as db:
        if career.brief(store)['revision'] != current['revision']:
            raise ValueError('Preferences changed while summarizing. Refresh the brief.')
        db.execute('INSERT OR IGNORE INTO crm_career_narrative VALUES (?,?)',
                   (current['revision'],json.dumps(value)))
        saved = db.execute('SELECT body FROM crm_career_narrative WHERE revision=?', (current['revision'],)).fetchone()
    return json.loads(saved['body'])
