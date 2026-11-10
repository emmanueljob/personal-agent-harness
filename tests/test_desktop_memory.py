"""Synthetic desktop memory tests: no personal sources or inference provider calls."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "desktop/macos/Worker"))

import pytest

spec = importlib.util.spec_from_file_location('desktop_memory_worker', Path(__file__).parents[1] / 'desktop/macos/Worker/memory_worker.py')
memory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memory)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / 'brain.sqlite'
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE evidence (id TEXT PRIMARY KEY,kind TEXT,body TEXT,payload TEXT,hash TEXT,revision INTEGER DEFAULT 1,occurred REAL DEFAULT 1700000000)')
    db.commit(); db.close()
    result = memory.connect(tmp_path)
    yield result
    result.close()


def add(db, ident, speaker='owner', body='I prefer quiet hotels.', kind='message'):
    record = dict(id=ident, source='synthetic', kind=kind, title='Synthetic trip', body=body, occurred=1700000000,
                  metadata=dict(speaker=speaker, body_status='plain_text'))
    with db:
        db.execute('INSERT OR REPLACE INTO evidence(id,kind,body,payload,hash,revision) VALUES (?,?,?,?,?,1)', (ident,kind,body,json.dumps(record),memory.payload_hash(record)))
    return record


class FakeBrain:
    def __init__(self): self.calls=[]
    def initialize(self): pass
    def run(self, args, body=None):
        self.calls.append((args,body))
        return json.dumps(dict(state='committed'))


def test_projection_idempotence_changed_content_and_withdrawal(db):
    add(db,'one')
    brain=FakeBrain()
    assert memory.index(db,brain)==dict(indexed=2,changed=2)
    assert memory.index(db,brain)==dict(indexed=2,changed=0)
    assert len(brain.calls)==2
    add(db,'one',body='I prefer small hotels.')
    assert memory.index(db,brain)['changed']==1
    with db: db.execute('DELETE FROM evidence')
    assert memory.index(db,brain)['indexed']==0
    assert [args[0] for args,_ in brain.calls].count('delete')==2


def test_learning_only_eligible_records_and_confirmation(db):
    add(db,'owner')
    add(db,'other',speaker='someone-else')
    seen=[]
    def infer(record,model):
        seen.append(record['id'])
        return [dict(claim='Prefers quiet hotels.',quote='I prefer quiet hotels.',kind='preference')]
    assert memory.learn(db,'fake',infer)==dict(processed=1,proposed=1)
    assert seen==['owner']
    assert len(memory.pages(db))==3  # two evidence records and a source hub
    with db: db.execute("UPDATE memory_proposals SET state='confirmed'")
    assert len(memory.pages(db))==4
    assert memory.learn(db,'fake',infer)==dict(processed=0,proposed=0)
    add(db,'owner',body='A changed source')
    assert len(memory.pages(db))==3  # stale confirmed memory is excluded immediately


def test_learning_batch_cap_and_rejection(db):
    for i in range(7): add(db,str(i))
    def infer(*_): return [dict(claim='Prefers quiet hotels.',quote='I prefer quiet hotels.',kind='preference')]
    assert memory.learn(db,'fake',infer)['processed']==5
    with db: db.execute("UPDATE memory_proposals SET state='rejected'")
    assert not any('memory-' in slug for slug in memory.pages(db))
    assert memory.learn(db,'fake',infer)['processed']==2


def test_repair_is_resumable_and_does_not_decode_retractions(db,monkeypatch):
    record=add(db,'rich')
    record['metadata'].update(body_status='rich_body_or_attachment_not_decoded',attributed_hex='0102')
    with db: db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(record),'rich'))
    monkeypatch.setattr(memory,'decode_rich',lambda *_:'Synthetic decoded text')
    assert memory.repair(db,Path('/unused'))==dict(decoded=1,unsupported=0)
    assert memory.repair(db,Path('/unused'))==dict(decoded=0,unsupported=0)
    row=db.execute('SELECT * FROM evidence').fetchone()
    assert row['body']=='Synthetic decoded text' and row['revision']==2
    assert 'attributed_hex' not in '\n'.join(memory.pages(db).values())


def test_unsupported_repair_is_not_repeated_for_same_decoder(db,monkeypatch):
    record=add(db,'rich')
    record['metadata'].update(body_status='rich_body_or_attachment_not_decoded',attributed_hex='0102')
    with db: db.execute('UPDATE evidence SET payload=? WHERE id=?',(json.dumps(record),'rich'))
    monkeypatch.setattr(memory,'decode_rich',lambda *_:None)
    assert memory.repair(db,Path('/unused'))==dict(decoded=0,unsupported=1)
    assert memory.repair(db,Path('/unused'))==dict(decoded=0,unsupported=0)


def test_calendar_never_promoted_as_preference(db):
    add(db,'event',kind='calendar')
    def infer(*_): return [dict(claim='Loves travel',quote='Synthetic trip',kind='preference'),dict(claim='Attended trip',quote='Synthetic trip',kind='fact')]
    assert memory.learn(db,'fake',infer)['proposed']==1
    claim=db.execute('SELECT claim FROM memory_proposals').fetchone()[0]
    assert claim=='Calendar scheduled: Synthetic trip'


def test_local_model_output_requires_exact_quote_and_valid_schema(db,monkeypatch):
    record=add(db,'owner')
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self,*args):
            return json.dumps(dict(message=dict(content=json.dumps(dict(memories=[
                dict(claim='Unsupported',quote='not in evidence',kind='fact'),
                dict(claim='Prefers quiet hotels',quote='I prefer quiet hotels.',kind='preference')]))))).encode()
    class Opener:
        def open(self,request,timeout):
            assert request.full_url=='http://127.0.0.1:11434/api/chat'
            assert isinstance(request.data, bytes)
            return Response()
    monkeypatch.setattr(memory.urllib.request,'build_opener',lambda *_:Opener())
    assert memory.infer(record,'fake')==[dict(claim='Prefers quiet hotels',quote='I prefer quiet hotels.',kind='preference')]


def test_failed_local_inference_leaves_record_pending(db):
    add(db,'owner')
    def fail(*_): raise RuntimeError('synthetic local model failure')
    with pytest.raises(RuntimeError): memory.learn(db,'fake',fail)
    assert db.execute('SELECT count(*) FROM learning_receipts').fetchone()[0]==0


def test_gbrain_environment_isolated(db,tmp_path):
    brain=memory.GBrain(tmp_path,Path('/synthetic-resources'))
    assert not any('KEY' in key or 'TOKEN' in key for key in brain.env)
    assert brain.env['GBRAIN_HOME']==str(tmp_path/'gbrain')
    assert brain.env['HOME']==brain.env['GBRAIN_HOME']


def test_real_isolated_gbrain_index_search_and_maintenance(db,tmp_path):
    import os
    resources=os.getenv('DESKTOP_MEMORY_RESOURCES')
    if not resources: pytest.skip('Set DESKTOP_MEMORY_RESOURCES to a built app Resources directory')
    add(db,'synthetic')
    brain=memory.GBrain(tmp_path,Path(resources))
    assert memory.index(db,brain)==dict(indexed=2,changed=2)
    assert memory.index(db,brain)['changed']==0
    hits=json.loads(brain.run(['search','quiet','--match','keyword','--limit','5','--json']))
    assert any('evidence-' in hit['slug'] for hit in hits)
    report=json.loads(brain.run(['dream','--phase','extract','--phase','orphans','--json']))
    assert {p['phase'] for p in report['phases']}=={'extract','orphans'}
    assert report['totals']['facts_consolidated']==0


def test_cloud_routed_ollama_model_is_rejected(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self,*args): return json.dumps(dict(remote_host='https://example.com',details=dict(format='gguf'))).encode()
    class Opener:
        def open(self,request,timeout):
            assert request.full_url=='http://127.0.0.1:11434/api/show'
            return Response()
    monkeypatch.setattr(memory.urllib.request,'build_opener',lambda *_:Opener())
    with pytest.raises(RuntimeError): memory.ensure_local_model('fake')
