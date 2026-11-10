"""Synthetic correction capture, publication isolation and dataset eligibility."""
import importlib
import json

import pytest
from fastapi.testclient import TestClient

from harness.learning import Annotation, Learning
from harness.learning_sync import merge_snapshots
from harness.store import Store


def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('BRAIN_BACKEND', 'files')
    monkeypatch.setenv('PUBLIC_ONLY', 'false')
    store = Store(tmp_path / 'state')
    return store, Learning(store.brain)


def completed(store, thread, prompt, response):
    ident = store.enqueue(thread, prompt)
    with store.connect() as db:
        db.execute("UPDATE turns SET status='completed',result=? WHERE id=?", (response, ident))
    return ident


def test_capture_survives_sync_without_source_database_and_never_publishes(tmp_path, monkeypatch):
    store, learning = setup(tmp_path, monkeypatch)
    thread = store.thread('private')['id']
    completed(store, thread, 'An earlier private question', 'Earlier private answer')
    ident = completed(store, thread, 'Draft my campaign', 'Original private draft')
    saved = learning.save(Annotation(kind='feedback', target_id=ident,
        note='Lead with the specific problem.', approved_response='My approved private rewrite.'), store)
    data = learning.training_data()
    assert data['ready'] == 1 and data['pending'] == 0
    assert data['examples'][0]['messages'] == [
        {'role':'user','content':'An earlier private question'},
        {'role':'assistant','content':'Earlier private answer'},
        {'role':'user','content':'Draft my campaign'},
        {'role':'assistant','content':'My approved private rewrite.'}]
    learning.publish(saved['id'], saved['revision'])
    public = json.dumps(learning.read('public'))
    assert 'Lead with the specific problem.' in public
    assert 'private' not in public and 'exchange' not in public and 'approved_response' not in public
    remote = Store(tmp_path / 'remote')
    replica = Learning(remote.brain)
    merged = merge_snapshots({}, {}, {'private':learning.read('private'), 'public':learning.read('public')})
    replica.write('private', merged['private'])
    assert replica.training_data() == data
    edited = replica.save(Annotation(kind='feedback',target_id=ident,note='Updated note',
        approved_response='New approved answer',revision=saved['revision']),remote)
    assert edited['exchange'] == saved['exchange']
    replica.remove(saved['id'])
    assert replica.training_data()['ready'] == 0


def test_only_explicit_complete_answers_are_candidates_and_split_by_thread(tmp_path, monkeypatch):
    store, learning = setup(tmp_path, monkeypatch)
    thread = store.thread('private')['id']
    one = completed(store, thread, 'Question one', 'Answer one')
    first = learning.save(Annotation(kind='feedback',target_id=one,note='A short correction'),store)
    assert learning.training_data()['ready'] == 0 and learning.training_data()['pending'] == 1
    first = learning.save(Annotation(kind='feedback',target_id=one,note='A short correction',
        approved_response='Correct answer one',revision=first['revision']),store)
    two = completed(store, thread, 'Question two', 'Answer two')
    learning.save(Annotation(kind='feedback',target_id=two,note='Second correction',
        approved_response='Correct answer two'),store)
    rows = learning.training_data()['examples']
    assert len(rows) == 2 and rows[0]['split'] == rows[1]['split']
    assert rows[0]['group_id'] == rows[1]['group_id']
    learning.save(Annotation(kind='feedback',target_id=one,note='A short correction',
        approved_response='',revision=first['revision']),store)
    assert learning.training_data()['ready'] == 1
    pending = store.enqueue(thread,'Unfinished question')
    with pytest.raises(ValueError):
        learning.save(Annotation(kind='feedback',target_id=pending,approved_response='Not approved'),store)
    draft = learning.save(Annotation(kind='feedback',target_id=pending,note='Draft only'),store,draft=True)
    assert draft['state'] == 'draft' and learning.training_data()['pending'] == 1
    later = completed(store,thread,'After unfinished turn','Some answer')
    learning.save(Annotation(kind='feedback',target_id=later,approved_response='Better answer'),store)
    assert learning.training_data()['ready'] == 1  # incomplete conversation is excluded


def test_legacy_notes_without_exchange_remain_editable(tmp_path, monkeypatch):
    store, learning = setup(tmp_path, monkeypatch)
    ident = completed(store,store.thread('private')['id'],'Question','Answer')
    saved = learning.save(Annotation(kind='feedback',target_id=ident,note='Old note'),store)
    saved.pop('exchange')
    learning.write('private',{saved['id']:saved})
    with store.connect() as db:
        db.execute('DELETE FROM turns WHERE id=?',(ident,))
    edited = learning.save(Annotation(kind='feedback',target_id=ident,note='New note',revision=saved['revision']),store)
    assert edited['note'] == 'New note'
    with pytest.raises(ValueError):
        learning.save(Annotation(kind='feedback',target_id=ident,note='New note',approved_response='Cannot invent source',revision=edited['revision']),store)


def test_training_export_owner_boundary(tmp_path, monkeypatch):
    store, learning = setup(tmp_path, monkeypatch)
    monkeypatch.setenv('OWNER_TOKEN','test-training-owner')
    monkeypatch.delenv('OWNER_STUDIO_TOKEN',raising=False)
    monkeypatch.setenv('EXECUTOR','local')
    monkeypatch.setenv('AGENT_RUNTIME','legacy')
    module=importlib.import_module('harness.app')
    monkeypatch.setattr(module,'store',store)
    auth={'Authorization':'Bearer test-training-owner'}
    with TestClient(module.app) as client:
        assert client.get('/api/owner/training-data').status_code == 401
        response=client.get('/api/owner/training-data',headers=auth)
        assert response.status_code == 200 and response.headers['cache-control']=='no-store'
        monkeypatch.setenv('PUBLIC_ONLY','true')
        assert client.get('/api/owner/training-data',headers=auth).status_code == 404
        monkeypatch.setenv('OWNER_STUDIO_TOKEN','test-studio-owner')
        assert client.get('/api/owner/training-data',headers={'Authorization':'Bearer test-studio-owner'}).status_code == 200
