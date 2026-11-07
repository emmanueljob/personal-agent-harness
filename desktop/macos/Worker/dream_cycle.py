"""Resumable all-import local dream cycle; canonical evidence remains authoritative.

GBrain's pinned synthesis subagent needs Anthropic. This app-owned cycle supplies
local extraction, hierarchical pattern synthesis and reviewable consolidation,
then uses the isolated GBrain index and deterministic native maintenance.
"""
import datetime
import hashlib
import json
import re
import time
import uuid

VERSION = 'local-dream-v1'
INPUT_LIMIT = 14000


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def setup(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS dream_runs (
        id TEXT PRIMARY KEY, signature TEXT NOT NULL, model TEXT NOT NULL,
        state TEXT NOT NULL, phase TEXT NOT NULL, total INTEGER NOT NULL,
        completed INTEGER NOT NULL DEFAULT 0, units_done INTEGER NOT NULL DEFAULT 0,
        units_total INTEGER NOT NULL DEFAULT 0, patterns INTEGER NOT NULL DEFAULT 0,
        pause_requested INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL,
        detail TEXT NOT NULL DEFAULT '');
      CREATE TABLE IF NOT EXISTS dream_units (
        key TEXT PRIMARY KEY, run_id TEXT NOT NULL, phase TEXT NOT NULL,
        output TEXT NOT NULL, created REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS dream_patterns (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL, claim TEXT NOT NULL,
        explanation TEXT NOT NULL, citations TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'proposed',
        created REAL NOT NULL);
    ''')
    db.commit()


def update(db, run_id, **fields):
    fields['updated']=time.time()
    with db:
        db.execute('UPDATE dream_runs SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',[*fields.values(),run_id])


def latest(db):
    setup(db)
    row=db.execute('SELECT * FROM dream_runs ORDER BY updated DESC LIMIT 1').fetchone()
    return dict(row) if row else None


def snapshot(db):
    return [dict(row) for row in db.execute('SELECT id,payload,hash FROM evidence ORDER BY occurred,id')]


def current_citations(db, citations):
    for cite in citations:
        row=db.execute('SELECT hash FROM evidence WHERE id=?',(cite['id'],)).fetchone()
        if not row or row['hash']!=cite['hash']:
            return False
    return True


def current_patterns(db):
    setup(db)
    result=[]
    for row in db.execute("SELECT * FROM dream_patterns WHERE state<>'rejected' ORDER BY created DESC"):
        value=dict(row)
        value['citations']=json.loads(value['citations'])
        if current_citations(db,value['citations']):result.append(value)
    return result


class Paused(Exception):
    pass


def check_pause(db, run_id):
    if db.execute('SELECT pause_requested FROM dream_runs WHERE id=?',(run_id,)).fetchone()[0]:
        raise Paused()


def batches(values, limit=INPUT_LIMIT):
    batch=[];size=0
    for value in values:
        length=len(json.dumps(value,ensure_ascii=False))
        if length>limit:
            raise ValueError('A synthesis item exceeds the context budget.')
        if batch and size+length>limit:
            yield batch;batch=[];size=0
        batch.append(value);size+=length
    if batch:yield batch


def chunks(rows):
    # Every textual part is covered; no silent 8K-character truncation.
    for row in rows:
        record=json.loads(row['payload']);meta=record['metadata']
        kind=record['kind']
        if kind=='message':
            if meta.get('body_status') not in ('plain_text','decoded_rich_text') or float(meta.get('date_retracted','0') or '0')>0:
                continue
            text=record['body'];speaker='owner' if meta.get('speaker')=='owner' else 'other'
        elif kind=='email':
            if meta.get('speaker')=='owner':
                text=meta.get('authored_text','');speaker='owner'
            else:
                text=record['title']+'\n'+record['body']
                speaker='shared_mail' if meta.get('account_role')=='shared' else 'email_received'
        elif kind=='calendar':
            text=record['title']+'\n'+record['body'];speaker='calendar_schedule'
        else:
            text=record['title']+'\n'+record['body'];speaker='external_source'
        date=datetime.datetime.fromtimestamp(record['occurred'],datetime.timezone.utc).isoformat()
        for offset in range(0,len(text),3000):
            yield dict(id=row['id'],hash=row['hash'],speaker=speaker,kind=kind,date=date,
                       offset=offset,text=text[offset:offset+3000])


def validate(values, available, key, minimum=1):
    if not isinstance(values,dict) or not isinstance(values.get(key),list):
        raise ValueError('Model returned an invalid dream response.')
    accepted=[]
    for value in values[key][:12]:
        if not isinstance(value,dict):continue
        claim=value.get('claim');explanation=value.get('explanation','')
        cites=value.get('citations')
        if not isinstance(claim,str) or not 1<=len(claim)<=600 or not isinstance(explanation,str) or len(explanation)>1000 or not isinstance(cites,list):continue
        result=[];seen=set()
        for cite in cites[:6]:
            if not isinstance(cite,dict):continue
            ident=cite.get('id');quote=cite.get('quote')
            evidence=available.get(ident)
            if not evidence or not isinstance(quote,str) or not 4<=len(quote)<=600:continue
            # Calendar can support schedule observations, not attended events.
            if not any(quote in span for span in evidence.get('spans',[evidence['text']])):continue
            if evidence['speaker'] not in ('owner','calendar_schedule','email_received','shared_mail'):continue
            if ident not in seen:
                result.append(dict(id=ident,hash=evidence['hash'],quote=quote,basis=evidence['speaker']))
                seen.add(ident)
        if len(result)<minimum:continue
        if all(c['basis']=='calendar_schedule' for c in result):
            claim=claim if claim.startswith('Calendar pattern (') else 'Calendar pattern (scheduled plans; attendance unverified): '+claim
        if any(c['basis'] in ('email_received','shared_mail') for c in result):
            claim=claim if claim.startswith('Email evidence (') else 'Email evidence (reported activity; owner attribution unverified): '+claim
        accepted.append(dict(claim=claim.strip(),explanation=explanation.strip(),citations=result))
    return accepted


def unit(db, run, phase, input_values, available, model_fn, minimum):
    key=digest(dict(version=VERSION,model=run['model'],phase=phase,input=input_values))
    if phase != 'extract':
        available = {k:dict(v, spans=[c['quote'] for value in input_values for c in value['citations'] if c['id']==k]) for k,v in available.items()}
    response_key='observations' if phase=='extract' else 'patterns'
    row=db.execute('SELECT output FROM dream_units WHERE key=?',(key,)).fetchone()
    if row:
        cached=json.loads(row['output'])
        valid=validate({response_key:cached},available,response_key,minimum)
        if len(valid)==len(cached) and all(current_citations(db,v['citations']) for v in valid):return valid
        with db:db.execute('DELETE FROM dream_units WHERE key=?',(key,))
    check_pause(db,run['id'])
    # Caller fixes model routing to the validated local endpoint. No tools are offered.
    output=model_fn(phase,input_values,run['model'])
    values=validate(output,available,response_key,minimum)
    # Do not publish candidates based on a source changed while inference was running.
    if any(not current_citations(db,v['citations']) for v in values):
        raise ValueError('Source changed during dreaming; start a new snapshot.')
    with db:
        db.execute('INSERT OR IGNORE INTO dream_units VALUES (?,?,?,?,?)',(key,run['id'],phase,json.dumps(values,ensure_ascii=False),time.time()))
    return values


def synthesis_inputs(values):
    for value in values:
        # Only validated source quotes propagate into later synthesis rounds.
        yield dict(claim=value['claim'],citations=[dict(id=c['id'],hash=c['hash'],quote=c['quote'],basis=c['basis']) for c in value['citations']])


def run(db, model, model_fn, repair_fn, index_fn, maintenance_fn):
    setup(db)
    # Repair all available rich records, not merely the first manual batch.
    previous=latest(db)
    if previous and previous['state'] in ('running','paused','failed') and previous['model']==model:
        run_id=previous['id']
        update(db,run_id,state='running',phase='decode',pause_requested=0,detail='Resuming saved work')
    else:
        run_id=uuid.uuid4().hex
        with db:
            db.execute('INSERT INTO dream_runs(id,signature,model,state,phase,total,created,updated) VALUES (?,?,?,\'running\',\'decode\',0,?,?)',(run_id,'',model,time.time(),time.time()))
    try:
        while True:
            check_pause(db,run_id)
            repair=repair_fn()
            if not repair.get('decoded',0) and not repair.get('unsupported',0):break
        rows=snapshot(db)
        signature=digest(dict(version=VERSION,model=model,sources=[(r['id'],r['hash']) for r in rows]))
        run=dict(id=run_id,model=model)
        update(db,run_id,signature=signature,total=len(rows),completed=0,phase='index',detail='Projecting source evidence into GBrain')
        index_fn()
        check_pause(db,run_id)
        source_chunks=list(chunks(rows))
        groups=list(batches(source_chunks))
        observations=[];covered=set()
        # Available quote spans are unit-local during extraction to avoid a model
        # fabricating a citation to an unseen part of a long record.
        for i,group in enumerate(groups):
            update(db,run_id,phase='extract',units_done=i,units_total=len(groups),detail='Extracting sourced observations')
            available={}
            for item in group:
                if item['id'] not in available:available[item['id']]=dict(item,spans=[item['text']])
                else:
                    available[item['id']]['spans'].append(item['text'])
            observations.extend(unit(db,run,'extract',group,available,model_fn,1))
            covered.update(v['id'] for v in group)
            update(db,run_id,completed=len(covered),units_done=i+1)
            check_pause(db,run_id)
        # Unsupported/no-text evidence is accounted for, never reported as learned.
        skipped=len(rows)-len({c['id'] for c in source_chunks})
        available={}
        for item in source_chunks:
            if item['id'] not in available:available[item['id']]=dict(item,spans=[item['text']])
            else:
                available[item['id']]['spans'].append(item['text'])
        patterns=[]
        groups=list(batches(list(synthesis_inputs(observations))))
        for i,group in enumerate(groups):
            update(db,run_id,phase='synthesize',units_done=i,units_total=len(groups),detail='Finding patterns across source observations')
            # Restrict references to those actually present in this synthesis input.
            ids={c['id'] for value in group for c in value['citations']}
            patterns.extend(unit(db,run,'synthesize',group,{k:v for k,v in available.items() if k in ids},model_fn,2))
            update(db,run_id,units_done=i+1)
            check_pause(db,run_id)
        # A second, hierarchical cross-batch pass can find themes spanning earlier
        # groups. Original candidates are retained, so a reduction never drops them.
        frontier=patterns[:]
        for level in range(4):
            groups=list(batches(list(synthesis_inputs(frontier))))
            if not groups:break
            new=[]
            for i,group in enumerate(groups):
                phase='patterns-'+str(level)
                update(db,run_id,phase=phase,units_done=i,units_total=len(groups),detail='Connecting themes across batches')
                ids={c['id'] for value in group for c in value['citations']}
                new.extend(unit(db,run,phase,group,{k:v for k,v in available.items() if k in ids},model_fn,2))
                update(db,run_id,units_done=i+1)
                check_pause(db,run_id)
            patterns.extend(new)
            if len(groups)==1 or not new or len(new)>=len(frontier):break
            frontier=new
        update(db,run_id,phase='consolidate',detail='Deduplicating tentative patterns with their evidence')
        for value in patterns:
            cites=sorted(value['citations'],key=lambda c:c['id'])
            if not current_citations(db,cites):continue
            # Keep review decisions stable across retries/runs for the same claim.
            claim_key=re.sub(r'\W+',' ',value['claim'].lower()).strip()
            ident=digest(dict(claim=claim_key,sources=[(c['id'],c['hash']) for c in cites]))[:32]
            with db:
                db.execute('INSERT OR IGNORE INTO dream_patterns(id,run_id,claim,explanation,citations,created) VALUES (?,?,?,?,?,?)',(ident,run_id,value['claim'],value['explanation'],json.dumps(cites,ensure_ascii=False),time.time()))
        count=len(current_patterns(db))
        update(db,run_id,patterns=count,phase='maintenance',detail='Updating GBrain links and current reviewed memories')
        index_fn()
        maintenance=maintenance_fn()
        final_signature=digest(dict(version=VERSION,model=model,sources=[(r['id'],r['hash']) for r in snapshot(db)]))
        state='complete' if final_signature==signature else 'outdated'
        update(db,run_id,state=state,phase='finished',completed=len(covered),detail=json.dumps(dict(observations=len(observations),pattern_candidates=count,unsupported_or_empty=skipped,native_maintenance=maintenance,hierarchical_pass_limit=4)))
        return latest(db)
    except Paused:
        update(db,run_id,state='paused',detail='Paused at a durable boundary; resume reuses completed model work')
        return latest(db)
    except BaseException as error:
        failure = 'invalid_model_json' if isinstance(error,json.JSONDecodeError) else 'timeout' if isinstance(error,TimeoutError) else 'interrupted' if isinstance(error,InterruptedError) else 'local_step_failed'
        update(db,run_id,state='failed',detail=json.dumps(dict(failure=failure, recovery='Completed units are saved; resume retries the pending unit')))
        raise
