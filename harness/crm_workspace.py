"""Owner CRM operations shared by the chat and admin workspace."""
import json
import time
import uuid
from typing import Literal

from pydantic import Field, model_validator
from . import crm


class Column(crm.Model):
    key: str = Field(pattern=r'^[a-zA-Z][a-zA-Z0-9_]{0,59}$')
    label: str = Field(min_length=1, max_length=100)
    numeric: bool = False


class Artifact(crm.Model):
    title: str = Field(min_length=1, max_length=200)
    format: Literal['markdown', 'dashboard'] = 'dashboard'
    markdown: str = Field(default='', max_length=40000)
    columns: list[Column] = Field(default_factory=list, max_length=16)
    rows: list[dict[str, str | float | None]] = Field(default_factory=list, max_length=300)
    description: str = Field(default='', max_length=2000)
    record_ids: list[str] = Field(default_factory=list, max_length=300)

    @model_validator(mode='after')
    def valid_dashboard(self):
        keys=[c.key for c in self.columns]
        if len(keys)!=len(set(keys)):
            raise ValueError('Column keys must be unique.')
        if self.format=='dashboard' and not keys:
            raise ValueError('A dashboard needs columns.')
        if any(set(row)-set(keys) or any(isinstance(v,str) and len(v)>2000 for v in row.values()) for row in self.rows):
            raise ValueError('Use declared columns and cells up to 2000 characters.')
        if len(self.model_dump_json().encode())>240000:
            raise ValueError('Keep artifact content under 240 KB.')
        return self


class ArtifactEdit(crm.Model):
    revision: int = Field(ge=0)
    artifact: Artifact


def initialize(store):
    crm.initialize(store)
    with store.connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS crm_artifacts(id TEXT PRIMARY KEY,body TEXT NOT NULL,revision INTEGER NOT NULL,turn_id TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS crm_artifact_changes(id INTEGER PRIMARY KEY AUTOINCREMENT,artifact_id TEXT NOT NULL,revision INTEGER NOT NULL,body TEXT NOT NULL,created REAL NOT NULL);
        ''')


def artifacts(store):
    initialize(store)
    return [{**{k:v for k,v in r.items() if k!='body'},'artifact':json.loads(r['body'])} for r in store.query('SELECT * FROM crm_artifacts ORDER BY updated DESC')]


def save_artifact(store, artifact, ident='', revision=0, turn_id=''):
    initialize(store)
    artifact=Artifact.model_validate(artifact).model_dump(mode='json')
    if not ident:
        ident=str(uuid.uuid5(uuid.NAMESPACE_URL,'crm-artifact:'+turn_id+':'+artifact['title'])) if turn_id else str(uuid.uuid4())
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT * FROM crm_artifacts WHERE id=?',(ident,)).fetchone()
        body=json.dumps(artifact)
        if old and old['body']==body:return {'id':ident,'revision':old['revision']}
        if (old and old['revision']!=revision) or (not old and revision!=0):
            raise ValueError('Artifact changed. Read its current revision before editing.')
        for rid in artifact['record_ids']:
            if not db.execute('SELECT 1 FROM crm_records WHERE id=?',(rid,)).fetchone():raise ValueError('Linked CRM record not found.')
        now=time.time()
        db.execute('INSERT INTO crm_artifacts VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,revision=excluded.revision,updated=excluded.updated',
                   (ident,body,revision+1,turn_id,now,now))
        db.execute('INSERT INTO crm_artifact_changes(artifact_id,revision,body,created) VALUES (?,?,?,?)',(ident,revision+1,body,now))
    return {'id':ident,'revision':revision+1}


def write(store, record, ident='', revision=0, actor='owner'):
    crm.initialize(store)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        ident=crm.write_record(db,record,ident or str(uuid.uuid4()),revision,actor=actor)
        row=db.execute('SELECT * FROM crm_records WHERE id=?',(ident,)).fetchone()
    return crm.record_row(row)


def dossier(store, ident):
    crm.initialize(store)
    from .crm_imports import canonical_id
    with store.connect() as db:
        ident=canonical_id(db,ident)
        row=db.execute('SELECT * FROM crm_records WHERE id=?',(ident,)).fetchone()
        if not row:raise ValueError('Record not found.')
        row=crm.record_row(row)
        related=[]
        for other in db.execute('SELECT * FROM crm_records WHERE id NOT IN (SELECT alias_id FROM crm_redirects)'):
            r=crm.record_row(other)
            if ident in [r['record'].get(f) for f in ('company_id','person_id','application_id')]:related.append(r)
        evidence=[]
        for candidate in db.execute('SELECT c.*,s.body AS source FROM crm_candidates c JOIN crm_sources s ON s.id=c.source_id WHERE c.accepted_id=?',(ident,)):
            c=json.loads(candidate['body']);source=json.loads(candidate['source'])
            evidence.append({**c,'source_id':candidate['source_id'],'source_title':source['title'],'provider':source['provider']})
        history=[dict(r) for r in db.execute('SELECT * FROM crm_changes WHERE record_id=? ORDER BY revision DESC',(ident,))]
    return {'record':row,'related':related,'research_evidence':evidence,'history':history}


def chat_state(store):
    crm.initialize(store)
    workspace=store.query('SELECT thread_id FROM crm_workspace WHERE id=1')
    turns=store.query('SELECT id,prompt,status,result,error,created FROM turns WHERE thread_id=? ORDER BY created DESC LIMIT 100',(workspace[0]['thread_id'],))[::-1] if workspace else []
    for turn in turns:
        turn['events']=store.query('SELECT kind,label,created FROM events WHERE turn_id=? ORDER BY id DESC LIMIT 8',(turn['id'],))[::-1]
        turn['documents']=store.query('SELECT id,title,body FROM artifacts WHERE turn_id=?',(turn['id'],))
    return {'turns':turns,'artifacts':artifacts(store)}


def tools(ctx):
    if ctx['mode']!='private':return []
    from langchain.tools import tool

    def allowed():
        from .crm_imports import job_for_turn
        if job_for_turn(ctx['store'],ctx.get('turn_id','')):
            raise ValueError('Extraction jobs may only import their source. Use propose_job_import.')

    @tool
    def read_job_workspace(query: str = '', kind: str = '', stage: str = '', offset: int = 0, limit: int = 20) -> dict:
        """Read CRM totals, source IDs and paginated records. Search full research notes. Use read_job_dossier for full evidence and history."""
        crm.initialize(ctx['store'])
        rows=[crm.record_row(r) for r in ctx['store'].query('SELECT * FROM crm_records WHERE id NOT IN (SELECT alias_id FROM crm_redirects) ORDER BY updated DESC,id')]
        from collections import Counter
        matching=[r for r in rows if (not kind or r['kind']==kind) and (not stage or r['record']['stage']==stage) and query.casefold() in json.dumps(r['record']).casefold()]
        offset=max(0,offset);end=offset+max(1,min(limit,30))
        sources=[{'id':r['id'],**{k:v for k,v in json.loads(r['body']).items() if k!='report'}} for r in ctx['store'].query('SELECT * FROM crm_sources')]
        page=[{**r,'record':{k:(v[:500]+'…' if isinstance(v,str) and len(v)>500 else v) for k,v in r['record'].items() if k not in ('job_description','news')}} for r in matching[offset:end]]
        return {'totals':dict(Counter(r['kind'] for r in rows)), 'application_stages':dict(Counter(r['record']['stage'] for r in rows if r['kind']=='application')), 'matching':len(matching),'records':page,'next_offset':end if end<len(matching) else None,'sources':sources}

    @tool
    def read_job_dossier(record_id: str) -> dict:
        """Read a company/contact/application/activity, full research, source evidence, related records and revision history before editing."""
        return dossier(ctx['store'],record_id)

    @tool
    def write_job_record(record: crm.Record, record_id: str = '', revision: int = 0) -> dict:
        """Create or edit any CRM field, as the owner can in the UI. Read the latest record revision first; provide the complete record. Explicit owner instructions take effect immediately, with no approval queue. Public research needs source URLs and dated notes; do not invent findings."""
        allowed()
        crm.initialize(ctx['store'])
        body=record.model_dump(mode='json')
        editing=bool(record_id)
        if not record_id:
            record_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'crm-write:'+ctx.get('turn_id','')+':'+json.dumps(body,sort_keys=True)))
        existing=ctx['store'].query('SELECT * FROM crm_records WHERE id=?',(record_id,))
        if editing and not existing:return {'error':'Record not found. Omit record_id to create a new record.'}
        if existing and json.loads(existing[0]['body'])==body:return crm.record_row(existing[0])
        return write(ctx['store'],body,record_id,revision,actor='owner')

    @tool
    def update_job_record(record_id: str, revision: int, changes: dict) -> dict:
        """Edit selected fields on a CRM record without replacing the other fields. Read the latest revision first. All UI fields are supported; schema validation and relation checks are shared with the form. For research append to existing notes instead of replacing owner context."""
        allowed()
        crm.initialize(ctx['store'])
        from .crm_imports import canonical_id
        with ctx['store'].connect() as db:
            record_id=canonical_id(db,record_id)
            old=db.execute('SELECT * FROM crm_records WHERE id=?',(record_id,)).fetchone()
        if not old:return {'error':'Record not found.'}
        body=crm.RECORD.validate_python({**json.loads(old['body']),**changes}).model_dump(mode='json')
        if body==json.loads(old['body']):return crm.record_row(old)
        return write(ctx['store'],body,record_id,revision,actor='owner')

    @tool
    def import_job_report(source: crm.Source) -> dict:
        """Save an original report and queue autonomous extraction, just like Imports in the UI."""
        allowed()
        return {'id':crm.save_source(ctx['store'],source)}

    @tool
    def process_job_reports() -> dict:
        """Process saved imports and reconcile aliases, just like Process saved reports in the UI."""
        allowed()
        from .crm_imports import resume
        return resume(ctx['store'])

    @tool
    def read_job_artifacts() -> list:
        """Read saved private visual dashboards and documents, including revisions for editing."""
        return artifacts(ctx['store'])

    @tool
    def save_job_artifact(artifact: Artifact, artifact_id: str = '', revision: int = 0) -> dict:
        """Create or update a saved Markdown document or interactive dashboard. Dashboards support sortable/filterable tables, bar charts and grouped boards. Supply columns with key,label,numeric and rows using those keys. Link record_ids, include as-of dates/source limits in description. Data is a snapshot; regenerate to refresh. No HTML or JavaScript needed."""
        allowed()
        return save_artifact(ctx['store'],artifact.model_dump(mode='json'),artifact_id,revision,ctx.get('turn_id',''))

    @tool
    def read_job_links() -> dict:
        """Read existing personalized outreach links to attach link_id to CRM activities. Does not send outreach."""
        from .outreach import report
        return report()

    catalog=[read_job_workspace,read_job_dossier,write_job_record,update_job_record,import_job_report,process_job_reports,read_job_artifacts,save_job_artifact,read_job_links]
    # Tool execution errors are corrective feedback, not failed coordinator turns.
    for item in catalog:
        original=item.func
        def safe(*args,_fn=original,**kwargs):
            try:return _fn(*args,**kwargs)
            except ValueError as e:return {'error':str(e)}
        item.func=safe
        item.handle_validation_error=True
    return catalog
