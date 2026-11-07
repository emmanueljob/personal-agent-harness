"""All-import dream lifecycle with synthetic sources and deterministic fake inference."""
import json

from test_desktop_memory import db, add, memory
import dream_cycle as dream


def fake_model(phase, values, model):
    if phase=='extract':
        return {'observations':[dict(claim='Quiet lodging preference.',citations=[dict(id=v['id'],quote=v['text'][:80])]) for v in values]}
    cites=[]
    for v in values:
        for c in v['citations']:
            if c['id'] not in {x['id'] for x in cites}:cites.append(c)
    return {'patterns':[dict(claim='Quiet lodging recurs in owner statements.', explanation='Tentative; source statements only.',citations=cites[:2])]} if len(cites)>1 else {'patterns':[]}


def execute(db, fn=fake_model, **kwargs):
    return dream.run(db,'fake',fn,kwargs.get('repair',lambda:{}),kwargs.get('index',lambda:None),kwargs.get('maintenance',lambda:{'status':'success'}))


def test_full_corpus_checkpoints_review_projection_and_idempotence(db):
    for i in range(17):add(db,str(i))
    calls=[]
    def infer(*args):
        calls.append(args[0]);return fake_model(*args)
    result=execute(db,infer)
    assert result['state']=='complete' and result['completed']==17 and result['total']==17
    assert result['patterns']>0
    assert json.loads(result['detail'])['unsupported_or_empty']==0
    assert not any('pattern-' in key for key in memory.pages(db))
    old_calls=len(calls)
    assert execute(db,infer)['state']=='complete'
    assert len(calls)==old_calls
    with db:db.execute("UPDATE dream_patterns SET state='confirmed'")
    assert any('pattern-' in key for key in memory.pages(db))
    with db:db.execute("UPDATE dream_patterns SET state='rejected'")
    execute(db,infer)
    assert dream.current_patterns(db)==[]


def test_resume_after_failure_reuses_units_and_covers_long_source(db):
    for i in range(10):add(db,str(i),body='I prefer quiet hotels. '*300)
    seen=[]
    def infer(phase, values, model):
        seen.append((phase,values))
        if len(seen)==3:raise RuntimeError('Synthetic interruption')
        return fake_model(phase,values,model)
    import pytest
    with pytest.raises(RuntimeError):execute(db,infer)
    assert dream.latest(db)['state']=='failed'
    first_inputs=seen[0][1]
    resumed=[]
    def resume(phase,values,model):
        resumed.append((phase,values));return fake_model(phase,values,model)
    result=execute(db,resume)
    assert result['state']=='complete' and result['completed']==10
    assert not any(values==first_inputs for phase,values in resumed if phase=='extract')
    pieces=list(dream.chunks(dream.snapshot(db)))
    assert len(pieces)>10
    assert sum(len(v['text']) for v in pieces)==10*len('I prefer quiet hotels. '*300)


def test_pause_then_resume_and_unsupported_accounting(db):
    add(db,'owner');add(db,'other',speaker='other')
    record=add(db,'unsupported')
    record['metadata']['body_status']='rich_body_or_attachment_not_decoded'
    with db:db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(record),'unsupported'))
    def pause(phase,values,model):
        row=dream.latest(db)
        dream.update(db,row['id'],pause_requested=1)
        return fake_model(phase,values,model)
    assert execute(db,pause)['state']=='paused'
    result=execute(db)
    assert result['completed']==2 and result['total']==3
    assert json.loads(result['detail'])['unsupported_or_empty']==1
    assert not dream.current_patterns(db) # other speaker cannot ground owner patterns


def test_validation_requires_distinct_current_sources_and_exact_supplied_quotes(db):
    add(db,'one');add(db,'two')
    available={c['id']:c for c in dream.chunks(dream.snapshot(db))}
    candidate=dict(claim='Test',citations=[dict(id='one',quote='I prefer quiet hotels.'),dict(id='one',quote='I prefer quiet hotels.')])
    assert dream.validate({'patterns':[candidate]},available,'patterns',2)==[]
    candidate['citations'][1]=dict(id='two',quote='I prefer quiet hotels.')
    values=dream.validate({'patterns':[candidate]},available,'patterns',2)
    assert len(values)==1
    with db:db.execute("UPDATE evidence SET hash='changed' WHERE id='one'")
    assert not dream.current_citations(db,values[0]['citations'])
    available['two']['speaker']='other'
    assert dream.validate({'patterns':[candidate]},available,'patterns',2)==[]


def test_synthesis_cannot_cite_unseen_text(db):
    add(db,'one',body='I prefer quiet hotels. Unseen long sentence.')
    add(db,'two')
    dream.setup(db)
    available={c['id']:c for c in dream.chunks(dream.snapshot(db))}
    inputs=[dict(claim='Preference',citations=[dict(id='one',quote='I prefer quiet hotels.')]),
            dict(claim='Preference',citations=[dict(id='two',quote='I prefer quiet hotels.')])]
    with db:db.execute("INSERT INTO dream_runs(id,signature,model,state,phase,total,created,updated) VALUES ('fake-run','','fake','running','synthesize',2,0,0)")
    run=dict(id='fake-run',model='fake')
    def malicious(*_):
        return {'patterns':[dict(claim='Unsupported',citations=[dict(id='one',quote='Unseen long sentence.'),dict(id='two',quote='I prefer quiet hotels.')])]}
    assert dream.unit(db,run,'synthesize',inputs,available,malicious,2)==[]


def test_all_repair_batches_and_maintenance_execute(db):
    add(db,'one')
    repairs=iter([dict(decoded=200),dict(unsupported=2),{}])
    phases=[]
    execute(db,repair=lambda:next(repairs),index=lambda:phases.append('index'),maintenance=lambda:phases.append('maintenance'))
    assert phases==['index','index','maintenance']


def test_calendar_pattern_keeps_schedule_qualification(db):
    add(db,'one',kind='calendar');add(db,'two',kind='calendar')
    assert execute(db)['state']=='complete'
    assert all(p['claim'].startswith('Calendar pattern (scheduled plans; attendance unverified):') for p in dream.current_patterns(db))


def test_empty_corpus_finishes_without_model_calls(db):
    def fail(*_):raise AssertionError('No inference needed')
    result=execute(db,fail)
    assert result['state']=='complete' and result['total']==0
