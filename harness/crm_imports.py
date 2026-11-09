"""Automatic CRM reconciliation and event-driven extraction; no inbox polling."""
import json
import re
import time
import unicodedata
import uuid
from datetime import date


def normalized(value):
    return ' '.join(re.findall(r'[a-z0-9]+',unicodedata.normalize('NFKD',value).casefold()))


def role(value):
    value=normalized(value)
    for short,long in [('swe','software engineer'),('em','engineering manager'),('sr','senior'),('vp','vice president')]:
        value=re.sub(r'\b'+short+r'\b',long,value)
    return ' '.join(w for w in value.split() if w not in ('software','engineer'))


def identity_matches(db,record):
    candidates=[dict(r) for r in db.execute('SELECT * FROM crm_records WHERE kind=? ORDER BY created,id',(record['kind'],))]
    exact=[]; related=[]
    for row in candidates:
        old=json.loads(row['body']); kind=record['kind']
        if kind=='company':
            same_domain=bool(old.get('domain') and record.get('domain') and old['domain']==record['domain'])
            same_name=normalized(old['name'])==normalized(record['name'])
            if same_domain or same_name: exact.append(row)
            continue
        if old['company_id']!=record['company_id']: continue
        if kind=='person':
            if old.get('email') and record.get('email'):
                if old['email'].casefold()==record['email'].casefold():exact.append(row)
                continue
            if normalized(old['name'])==normalized(record['name']):related.append(row)
        elif kind=='application':
            if old.get('applied_on') and record.get('applied_on') and old['applied_on']!=record['applied_on']:continue
            if set(old.get('urls',[])) & set(record.get('urls',[])) or role(old['title'])==role(record['title']):
                exact.append(row)
            elif record['stage'] in ('considering','preparing') and old['stage'] in ('applied','screen','interview','offer'):
                a,b=set(role(old['title']).split()),set(role(record['title']).split())
                if len(a&b)>=3 and len(a&b)/len(a|b)>=.75:related.append(row)
        elif kind=='activity' and all(old.get(k)==record.get(k) for k in ('person_id','happened_on','channel','stage')) and normalized(old['title'])==normalized(record['title']):
            exact.append(row)
    return exact if exact else related if len(related)==1 else []


def owner_fields(db,ident):
    """A correction, including clearing a field, wins over all later imports."""
    locked=set(); previous={}
    for row in db.execute('SELECT body,actor FROM crm_changes WHERE record_id=? ORDER BY revision,id',(ident,)):
        body=json.loads(row['body'])
        if row['actor']=='owner':
            locked.update(k for k,v in body.items() if (v not in ('',None,[],{}) if not previous else v!=previous.get(k)))
        previous=body
    return locked


def source_time(db,source_id,observed=None):
    row=db.execute('SELECT body,created FROM crm_sources WHERE id=?',(source_id,)).fetchone()
    if not row:return 0
    value=json.loads(row['body'])
    stamp=observed or value.get('observed_on')
    if stamp:
        return date.fromisoformat(stamp).toordinal()*86400+row['created']%86400
    return date.fromtimestamp(row['created']).toordinal()*86400+row['created']%86400


def merge(db,old,incoming,source_id,observed):
    current=json.loads(old['body']); locked=owner_fields(db,old['id'])
    newer=source_time(db,source_id,observed)>=source_time(db,old['source_id'],old['observed_on'])
    for field,value in incoming.items():
        if field in locked or field in ('kind','company_id'):continue
        present=current.get(field)
        if value in ('',None,[],{}):continue
        if field=='notes':
            if value not in present:
                joined=(present+'\n\n'+value).strip()
                current[field]=joined if len(joined)<=12000 else joined[:11945]+'\n[More detail in original source reports.]'
        elif field=='urls':current[field]=list(dict.fromkeys((present or [])+value))[:12]
        elif field=='stats':current[field]={**value,**present} if not newer else {**present,**value}
        elif field=='priority':
            ranks={'low':0,'medium':1,'high':2}
            if ranks[value]>ranks[present]:current[field]=value
        elif present in ('',None,[],{}) or newer:
            # Schema defaults do not roll back a known pipeline stage.
            if field=='stage':
                order={'company':['interested','researching','pursuing'], 'application':['considering','preparing','applied','screen','interview','offer'], 'person':['suggested','to_contact','drafted','contacted','connected','replied'],'activity':['proposed','drafted','sent','replied','completed']}[incoming['kind']]
                if present in order and value in order and order.index(value)<order.index(present):continue
            current[field]=value
    return current


def apply_pending(db,source_id):
    from .crm import RECORD,write_record
    rows={r['key']:dict(r) for r in db.execute('SELECT * FROM crm_candidates WHERE source_id=?',(source_id,))}
    targets={k:r['accepted_id'] for k,r in rows.items() if r['accepted_id']}
    pending={k:r for k,r in rows.items() if not r['accepted_id']}
    imported=0
    while pending:
        progressed=False
        for key,row in list(pending.items()):
            c=json.loads(row['body']);record=c['record']
            unresolved=False
            for field in ('company_id','person_id','application_id'):
                ref=record.get(field,'')
                if ref.startswith('$'):
                    if ref[1:] not in targets:unresolved=True;break
                    record[field]=targets[ref[1:]]
            if unresolved:continue
            db.execute('SAVEPOINT crm_auto_record')
            try:
                record=RECORD.validate_python(record).model_dump(mode='json')
                matches=identity_matches(db,record)
                old=matches[0] if matches else None
                ident=old['id'] if old else str(uuid.uuid4())
                if old:record=merge(db,old,record,source_id,c['observed_on'])
                if not old or json.loads(old['body'])!=record:
                    write_record(db,record,ident,old['revision'] if old else 0,source_id,c['excerpt'],c['observed_on'],
                                 'uncertain' if c['certainty']=='uncertain' or (old and old['certainty']=='uncertain') else 'reported','automatic_import')
                    if old and source_time(db,source_id,c['observed_on'])<source_time(db,old['source_id'],old['observed_on']):
                        db.execute('UPDATE crm_records SET source_id=?,evidence=?,observed_on=? WHERE id=?',(old['source_id'],old['evidence'],old['observed_on'],ident))
                db.execute('UPDATE crm_candidates SET accepted_id=? WHERE source_id=? AND key=?',(ident,source_id,key))
                db.execute('DELETE FROM crm_import_issues WHERE source_id=? AND key=?',(source_id,key))
                targets[key]=ident;imported+=1
                db.execute('RELEASE crm_auto_record')
            except ValueError as error:
                db.execute('ROLLBACK TO crm_auto_record');db.execute('RELEASE crm_auto_record')
                db.execute('INSERT OR REPLACE INTO crm_import_issues VALUES (?,?,?,?)',(source_id,key,str(error)[:400],time.time()))
            del pending[key];progressed=True
        if not progressed:break
    for key in pending:
        db.execute('INSERT OR REPLACE INTO crm_import_issues VALUES (?,?,?,?)',(source_id,key,'Related record is not available yet; original source is retained.',time.time()))
    return imported


def ranges(report,width=2400):
    offset=0
    while offset<len(report):
        end=min(offset+width,len(report))
        if end<len(report):
            boundary=report.rfind('\n',offset+width//2,end)
            if boundary>offset:end=boundary+1
        yield offset,end
        offset=end


def queue_source(db,source_id):
    from .models import settings,agent_runtime
    row=db.execute('SELECT body FROM crm_sources WHERE id=?',(source_id,)).fetchone()
    if not row:return 0
    report=json.loads(row['body'])['report']; queued=0
    try:ready=settings()['configured']
    except ValueError:ready=False
    for offset,end in ranges(report):
        db.execute('INSERT OR IGNORE INTO crm_import_jobs VALUES (?,?,?,NULL)',(source_id,offset,end))
        job=db.execute('SELECT turn_id FROM crm_import_jobs WHERE source_id=? AND offset=?',(source_id,offset)).fetchone()
        if job['turn_id'] or not ready:continue
        ident=str(uuid.uuid5(uuid.NAMESPACE_URL,f'crm-auto:{source_id}:{offset}'))
        thread=str(uuid.uuid5(uuid.NAMESPACE_URL,'thread:'+ident))
        prompt=(f'Automatically import existing knowledge from source {source_id}, characters {offset} through {end}. '
                f'Call read_job_import with offset={offset} and limit={end-offset}. Extract all CRM entities and actions in this range. '
                'Use search_job_crm to resolve existing companies and contacts; use $key for new dependencies. '
                'Call propose_job_import with exact short excerpts copied from this report. It imports immediately. '
                'Import every supported company, person, role/application and activity; distinguish sent from drafted and applied from considering. '
                'Keep uncertain details labeled. Never request owner acceptance. Do not start research, exports, outreach, applications or spending. '
                'If a tool reports an invalid excerpt, correct it and continue. State any material data gaps briefly.')
        db.execute("INSERT OR IGNORE INTO threads VALUES (?,'private')",(thread,))
        db.execute("INSERT OR IGNORE INTO turns VALUES (?,?,?,'queued',NULL,NULL,?)",(ident,thread,prompt,time.time()))
        db.execute('INSERT OR IGNORE INTO turn_runtime VALUES (?,?)',(ident,agent_runtime()))
        db.execute("INSERT OR IGNORE INTO turn_model_choice VALUES (?,'default')",(ident,))
        db.execute("INSERT OR IGNORE INTO turn_personalization VALUES (?,'default')",(ident,))
        db.execute('UPDATE crm_import_jobs SET turn_id=? WHERE source_id=? AND offset=?',(ident,source_id,offset))
        queued+=1
    return queued


def resume(store):
    from .crm import initialize
    initialize(store);imported=queued=0
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for row in db.execute('SELECT id FROM crm_sources ORDER BY created,id').fetchall():
            imported+=apply_pending(db,row['id']);queued+=queue_source(db,row['id'])
        from .models import settings
        try:ready=settings()['configured']
        except ValueError:ready=False
        if ready:
            failed=db.execute("SELECT t.id,COALESCE(d.generation,0) AS generation FROM crm_import_jobs j JOIN turns t ON t.id=j.turn_id LEFT JOIN dispatch_attempts d ON d.turn_id=t.id WHERE t.status='failed'").fetchall()
            for turn in failed:
                if turn['generation']>=2:continue
                db.execute("UPDATE turns SET status='queued',error=NULL WHERE id=?",(turn['id'],))
                db.execute('INSERT INTO dispatch_attempts VALUES (?,1) ON CONFLICT(turn_id) DO UPDATE SET generation=generation+1',(turn['id'],))
                queued+=1
    return {'imported':imported,'extraction_jobs_queued':queued,'requires_owner_review':False}


def job_for_turn(store,ident):
    # Safe during registration against old databases; no mutation/read of private sources.
    if not store or not store.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name='crm_import_jobs'"):return None
    rows=store.query('SELECT * FROM crm_import_jobs WHERE turn_id=?',(ident,))
    return rows[0] if rows else None


def retry_failed_job(store,ident):
    """A terminal import failure schedules at most two new workflow generations."""
    if not job_for_turn(store,ident):return False
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT t.status,COALESCE(d.generation,0) AS generation FROM turns t LEFT JOIN dispatch_attempts d ON d.turn_id=t.id WHERE t.id=?",(ident,)).fetchone()
        if not row or row['status']!='failed' or row['generation']>=2:return False
        db.execute("UPDATE turns SET status='queued',error=NULL WHERE id=?",(ident,))
        db.execute('INSERT INTO dispatch_attempts VALUES (?,1) ON CONFLICT(turn_id) DO UPDATE SET generation=generation+1',(ident,))
    return True
