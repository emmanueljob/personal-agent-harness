"""Automatic CRM reconciliation and event-driven extraction; no inbox polling."""
import json
import re
import time
import unicodedata
import uuid
from urllib.parse import urlsplit, parse_qs
from datetime import date


def normalized(value):
    return ' '.join(re.findall(r'[a-z0-9]+',unicodedata.normalize('NFKD',value).casefold()))


def organization(value):
    value=re.sub(r'\([^)]*(?:undisclosed|unnamed|unknown)[^)]*\)','',value,flags=re.I)
    return normalized(value)


def unknown_role(value):
    return bool(re.search(r'\b(?:unknown|unverified|unnamed|unspecified|not named)\b',value,re.I))


def role(value):
    value=re.sub(r'\([^)]*(?:undisclosed|unnamed)[^)]*\)','',value,flags=re.I)
    value=normalized(value)
    value=re.sub(r'\b(?:se|swe)\s?(\d+)\b',r'\1',value)
    for short,long in [('swe','software engineer'),('em','engineering manager'),('sr','senior'),('vp','vice president')]:
        value=re.sub(r'\b'+short+r'\b',long,value)
    return ' '.join(w for w in value.split() if w not in ('software','engineer','engineering','eng','and','of','the'))


def job_urls(record):
    result=set()
    for url in record.get('urls',[]):
        parsed=urlsplit(url);parts=[p for p in parsed.path.split('/') if p]
        query=parse_qs(parsed.query)
        job_query={k:v for k,v in query.items() if k.lower() in ('gh_jid','jobid','job_id','postingid','reqid')}
        if job_query or (len(parts)>=2 and parts[-1].lower() not in ('careers','jobs','openings','positions')):
            result.add((parsed.hostname,parsed.path.rstrip('/'),tuple(sorted((k,tuple(v)) for k,v in job_query.items()))))
    return result


def job_ids(record):
    text=' '.join([record.get('title','')]+record.get('urls',[])).lower()
    return set(re.findall(r'\b(?:jr|req|r)\s?\d{4,}\b',text))


def canonical_id(db,ident):
    row=db.execute('SELECT record_id FROM crm_redirects WHERE alias_id=?',(ident,)).fetchone()
    return row['record_id'] if row else ident


def identity_matches(db,record,exclude_id=None):
    candidates=[dict(r) for r in db.execute('SELECT * FROM crm_records WHERE kind=? AND id NOT IN (SELECT alias_id FROM crm_redirects) ORDER BY created,id',(record['kind'],))]
    exact=[]; related=[]
    for row in candidates:
        if row['id']==exclude_id:continue
        current=json.loads(row['body']);kind=record['kind']
        different_company=kind!='company' and current['company_id']!=record['company_id']
        if different_company and 'company_id' not in owner_fields(db,row['id']):continue
        # Imported identities remain aliases after the owner corrects a name/email/title.
        # Otherwise a later report could recreate the old identity as a duplicate.
        variants=[current]+[json.loads(r['body']) for r in db.execute('SELECT body FROM crm_changes WHERE record_id=? OR record_id IN (SELECT alias_id FROM crm_redirects WHERE record_id=?) ORDER BY revision DESC',(row['id'],row['id']))]
        variants.extend(json.loads(r['body']) for r in db.execute('SELECT body FROM crm_records WHERE id IN (SELECT alias_id FROM crm_redirects WHERE record_id=?)',(row['id'],)))
        strength=None
        for old in variants:
            if different_company and old.get('company_id')!=record['company_id']:continue
            if kind=='company':
                same_domain=bool(old.get('domain') and record.get('domain') and old['domain']==record['domain'])
                if same_domain or organization(old['name'])==organization(record['name']):strength='exact';break
            elif kind=='person':
                if old.get('email') and record.get('email'):
                    if old['email'].casefold()==record['email'].casefold():strength='exact';break
                    continue
                if normalized(old['name'])==normalized(record['name']):strength='related'
            elif kind=='application':
                if old.get('applied_on') and record.get('applied_on') and old['applied_on']!=record['applied_on']:continue
                if job_urls(old) & job_urls(record) or job_ids(old) & job_ids(record) or role(old['title'])==role(record['title']):
                    strength='exact';break
                same_submission=bool(old.get('applied_on') and old['applied_on']==record.get('applied_on'))
                if same_submission:
                    a,b=set(role(old['title']).split()),set(role(record['title']).split())
                    if min(len(a),len(b))>=2 and (a<=b or b<=a):strength='related'
                    if unknown_role(old['title']) or unknown_role(record['title']):strength='related'
                if record['stage'] in ('considering','preparing') and current['stage'] in ('applied','screen','interview','offer'):
                    a,b=set(role(old['title']).split()),set(role(record['title']).split())
                    if len(a&b)>=3 and len(a&b)/len(a|b)>=.75:strength='related'
            elif kind=='activity' and all(old.get(k)==record.get(k) for k in ('person_id','happened_on','channel','stage')) and normalized(old['title'])==normalized(record['title']):
                strength='exact';break
        if strength=='exact':exact.append(row)
        elif strength=='related':related.append(row)
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
            if field=='title' and unknown_role(value) and present and not unknown_role(present):continue
            # Schema defaults do not roll back a known pipeline stage.
            if field=='stage':
                if incoming['kind']=='company' and present in ('closed','paused') and value in ('interested','researching'):continue
                if incoming['kind']=='application' and present in ('rejected','withdrawn') and value in ('considering','preparing','applied'):continue
                if incoming['kind']=='activity' and present=='cancelled' and value in ('proposed','drafted'):continue
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
                if record.get(field):record[field]=canonical_id(db,record[field])
            if unresolved:continue
            db.execute('SAVEPOINT crm_auto_record')
            try:
                record=RECORD.validate_python(record).model_dump(mode='json')
                if record['kind'] in ('activity','application') and record.get('person_id'):
                    person=db.execute("SELECT company_id FROM crm_records WHERE id=? AND kind='person'",(record['person_id'],)).fetchone()
                    company=db.execute("SELECT body FROM crm_records WHERE id=? AND kind='company'",(record['company_id'],)).fetchone()
                    if person and company and person['company_id']!=record['company_id']:
                        label=json.loads(company['body'])['name']
                        if re.search(r'unknown|not (?:stated|specified)|unspecified',label,re.I) and not owner_fields(db,record['company_id']):
                            merge_records(db,record['company_id'],person['company_id'])
                            record['company_id']=person['company_id']
                            record['notes']=(record['notes']+'\nCompany association inferred from the known contact affiliation; original source used an unknown-company placeholder.').strip()[:12000]
                            c['certainty']='uncertain'
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
        consolidated=consolidate(db)
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
    return {'imported':imported,'consolidated':consolidated,'extraction_jobs_queued':queued,'requires_owner_review':False}


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


def merge_records(db,alias_id,record_id):
    alias_id=canonical_id(db,alias_id);record_id=canonical_id(db,record_id)
    if alias_id==record_id:return record_id
    alias=dict(db.execute('SELECT * FROM crm_records WHERE id=?',(alias_id,)).fetchone())
    target=dict(db.execute('SELECT * FROM crm_records WHERE id=?',(record_id,)).fetchone())
    if alias['kind']!=target['kind']:raise ValueError('Cannot merge different record types.')
    a_locks,t_locks=owner_fields(db,alias_id),owner_fields(db,record_id)
    if a_locks and t_locks:return None
    if a_locks:return merge_records(db,record_id,alias_id)
    value=merge(db,target,json.loads(alias['body']),alias['source_id'],alias['observed_on'])
    if target['kind']=='company':
        original=json.loads(target['body'])
        value['name']=original['name'];value['domain']=original['domain']
    # All reference rewrites, source bindings and history are retained atomically.
    rows=[dict(r) for r in db.execute('SELECT * FROM crm_records WHERE id NOT IN (SELECT alias_id FROM crm_redirects)')]
    now=time.time()
    for row in rows:
        if row['id']==alias_id:continue
        body=value if row['id']==record_id else json.loads(row['body'])
        changed=row['id']==record_id
        for field in ('company_id','person_id','application_id'):
            if body.get(field)==alias_id:body[field]=record_id;changed=True
        if not changed:continue
        revision=row['revision']+1;encoded=json.dumps(body)
        provenance=alias if row['id']==record_id and source_time(db,alias['source_id'],alias['observed_on'])>source_time(db,target['source_id'],target['observed_on']) else row
        db.execute('UPDATE crm_records SET body=?,company_id=?,revision=?,updated=?,source_id=?,evidence=?,observed_on=?,certainty=? WHERE id=?',(encoded,body.get('company_id',''),revision,now,provenance['source_id'],provenance['evidence'],provenance['observed_on'],'uncertain' if row['id']==record_id and 'uncertain' in (alias['certainty'],target['certainty']) else row['certainty'],row['id']))
        db.execute('INSERT INTO crm_changes(record_id,revision,body,source_id,evidence,actor,created) VALUES (?,?,?,?,?,?,?)',(row['id'],revision,encoded,row['source_id'],row['evidence'],'automatic_merge',now))
    db.execute('INSERT OR REPLACE INTO crm_redirects VALUES (?,?)',(alias_id,record_id))
    db.execute('UPDATE crm_redirects SET record_id=? WHERE record_id=?',(record_id,alias_id))
    db.execute('UPDATE crm_candidates SET accepted_id=? WHERE accepted_id=?',(record_id,alias_id))
    return record_id


def consolidate(db):
    merged=0
    for kind in ('company','person','application','activity'):
        rows=[dict(r) for r in db.execute('SELECT * FROM crm_records WHERE kind=? AND id NOT IN (SELECT alias_id FROM crm_redirects) ORDER BY created,id',(kind,))]
        for row in rows:
            if canonical_id(db,row['id'])!=row['id']:continue
            current=dict(db.execute('SELECT * FROM crm_records WHERE id=?',(row['id'],)).fetchone())
            matches=identity_matches(db,json.loads(current['body']),exclude_id=row['id'])
            if not matches:continue
            other=matches[0]
            # Keep the first stable identity; owner edits can override this choice.
            keep,alias=(current,other) if (current['created'],current['id'])<(other['created'],other['id']) else (other,current)
            if merge_records(db,alias['id'],keep['id']):merged+=1
    return merged
