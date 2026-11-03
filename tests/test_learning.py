import importlib
import json
import tarfile

import pytest
from fastapi.testclient import TestClient

from harness.brain import FileBrain
from harness.learning import Annotation, Conflict, Learning, feedback_tools
from harness.store import Store
from harness.agent import prepare_turn, knowledge_tools, build_system_prompt
from harness.ingestion import normalize, upsert_batch
from scripts.export_public_brain import export_public


def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('BRAIN_BACKEND','files')
    monkeypatch.setenv('BRAIN_DIR',str(tmp_path/'brain'))
    monkeypatch.setenv('PUBLIC_ONLY','false')
    store=Store(tmp_path/'state')
    shelf=store.brain.scope('public');shelf.mkdir(parents=True)
    (shelf/'bookshelf.json').write_text(json.dumps({'books':[{'id':'example','title':'Example Book','author':'Example Author'}]}))
    return store,Learning(store.brain)


def test_notes_require_explicit_publication_and_private_edits_do_not_leak(tmp_path,monkeypatch):
    store,learning=setup(tmp_path,monkeypatch)
    body=Annotation(kind='book',target_id='example',note='I prefer autonomy with clear outcomes.',tags=['leadership'],experience='read')
    first=learning.save(body,store)
    assert not store.brain.search('autonomy','public')
    hit=store.brain.search('autonomy','private')[0]
    assert hit['knowledge_role']=='owner_learning' and '"experience": "read"' in hit['text']
    learning.publish(first['id'],first['revision'])
    assert 'clear outcomes' in store.brain.search('autonomy','public')[0]['text']
    second=learning.save(body.model_copy(update={'revision':first['revision'],'note':'A private change about autonomy.'}),store)
    assert 'private change' not in json.dumps(store.brain.search('autonomy','public'))
    assert 'private change' in store.brain.search('autonomy','private')[0]['text']
    with pytest.raises(Conflict):learning.publish(first['id'],first['revision'])
    with pytest.raises(Conflict):learning.save(body,store)
    learning.publish(second['id'],second['revision'])
    learning.remove(first['id'],public_only=True)
    assert not store.brain.search('autonomy','public')
    assert store.brain.search('autonomy','private')
    learning.remove(first['id'])
    assert not store.brain.search('autonomy','private')


def test_private_chat_proposals_wait_for_review_and_replay_preserves_edits(tmp_path,monkeypatch):
    store,learning=setup(tmp_path,monkeypatch)
    ident=store.enqueue(store.thread('private')['id'],'My correction about clear outcomes.')
    ctx=dict(mode='private',store=store,turn_id=ident)
    tool=feedback_tools(ctx)[0]
    args=dict(correction='I care about outcomes, not ceremonies.',topics=['outcomes'])
    draft=json.loads(tool.invoke(args))
    assert draft['state']=='draft'
    assert not store.brain.search('outcomes','private')
    with pytest.raises(ValueError):learning.publish(draft['id'],learning.read('private')[draft['id']]['revision'])
    record=learning.read('private')[draft['id']]
    approved=learning.save(Annotation(kind='feedback',target_id=ident,note='I prefer outcomes.',tags=['outcomes'],revision=record['revision']),store)
    tool.invoke(args)
    assert learning.read('private')[draft['id']]['revision']==approved['revision']
    assert 'I prefer outcomes.' in store.brain.search('outcomes','private')[0]['text']
    assert feedback_tools({**ctx,'mode':'public'})==[]


def test_corrected_new_turn_uses_memory_and_retry_keeps_snapshot(tmp_path,monkeypatch):
    monkeypatch.setenv('MODEL_PROVIDER','openai');monkeypatch.setenv('OPENAI_API_KEY','synthetic-test')
    store,learning=setup(tmp_path,monkeypatch)
    thread=store.thread('private')['id']
    original=store.enqueue(thread,'What matters in leadership?')
    turn=store.query('SELECT * FROM turns WHERE id=?',(original,))[0]
    snapshot=prepare_turn(store,turn)[1]
    learning.save(Annotation(kind='feedback',target_id=original,note='My leadership starts with context.',tags=['leadership']),store)
    assert prepare_turn(store,turn)[1]==snapshot
    next_id=store.enqueue(thread,'What matters in leadership?')
    newer=prepare_turn(store,store.query('SELECT * FROM turns WHERE id=?',(next_id,))[0])[1]
    assert 'My leadership starts with context.' in json.dumps(newer['knowledge'])
    assert 'propose_memory' in build_system_prompt(newer,'private')
    assert not store.brain.search('leadership','public')


def test_book_influence_survives_podcast_volume_and_keeps_identity(tmp_path,monkeypatch):
    store,learning=setup(tmp_path,monkeypatch)
    root=store.brain.scope('public')/'wiki/influences';root.mkdir(parents=True)
    (root/'example-book.md').write_text('# Example Book — Example Author\n\nOwner-reported influence.\n\nLeadership needs autonomy.')
    records=[normalize(dict(source_id=f'podcast:example:{i}',title='Leadership autonomy',text='Leadership autonomy advice.',source_url=f'https://example.org/{i}'),'podcasts','public') for i in range(8)]
    upsert_batch(store.brain,records,'podcasts','public',apply=True,allow_public=True)
    hits=store.brain.search('leadership autonomy','public')
    books=[h for h in hits if '/wiki/influences/' in h['source']]
    assert len(books)==1 and 'Example Book' in books[0]['text']
    assert len({h['source'].split('#chunk-')[0] for h in hits})==len(hits)
    assert any('/references/podcasts/' in h['source'] for h in hits)
    tool=json.loads(knowledge_tools(dict(store=store,mode='public'))[0].invoke({'query':'leadership autonomy'}))
    assert any('Example Book' in h['text'] for h in tool)


def test_podcast_status_does_not_get_inferred_from_import(tmp_path,monkeypatch):
    store,learning=setup(tmp_path,monkeypatch)
    record=normalize(dict(source_id='podcast:example:1',title='Example episode',text='Autonomy matters.',source_url='https://example.org/episode'),'podcasts','public')
    upsert_batch(store.brain,[record],'podcasts','public',apply=True,allow_public=True)
    episode=next(r for r in learning.catalog() if r['kind']=='podcast')
    saved=learning.save(Annotation(kind='podcast',target_id=episode['target_id'],tags=['autonomy']),store)
    assert saved['experience']=='unknown'
    assert '"experience": "unknown"' in store.brain.search('autonomy','private')[0]['text']
    with pytest.raises(ValueError):learning.save(Annotation(kind='podcast',target_id=episode['target_id'],experience='read',revision=saved['revision']),store)
    with pytest.raises(KeyError):learning.save(Annotation(kind='podcast',target_id='missing'),store)


def test_learning_export_is_public_only(tmp_path,monkeypatch):
    store,learning=setup(tmp_path,monkeypatch)
    for name,body in [('profile.md','Approved example facts'),('voice.md','Example voice'),('portfolio.json','{}')]:
        (store.brain.scope('public')/name).write_text(body)
    saved=learning.save(Annotation(kind='book',target_id='example',note='Public approved autonomy note.'),store)
    learning.publish(saved['id'],saved['revision'])
    learning.save(Annotation(kind='book',target_id='example',note='Unpublished edit.',revision=saved['revision']),store)
    bundle=tmp_path/'public.tgz';export_public(store.brain.root,bundle)
    with tarfile.open(bundle) as archive:
        body=archive.extractfile('brain/public/learning.json').read().decode()
        assert 'Public approved autonomy note.' in body and 'Unpublished edit.' not in body
        assert not any('/private/' in n for n in archive.getnames())


def test_owner_routes_require_auth_and_are_disabled_on_public_deployment(tmp_path,monkeypatch):
    store,learning=setup(tmp_path,monkeypatch)
    monkeypatch.setenv('OWNER_TOKEN','fixture-owner');monkeypatch.setenv('EXECUTOR','local');monkeypatch.setenv('AGENT_RUNTIME','legacy')
    module=importlib.import_module('harness.app');monkeypatch.setattr(module,'store',store)
    auth={'Authorization':'Bearer fixture-owner'}
    with TestClient(module.app) as c:
        assert c.get('/api/owner/learning').status_code==401
        body=dict(kind='book',target_id='example',note='Reviewed autonomy note.')
        assert c.post('/api/owner/learning',json=body).status_code==401
        result=c.post('/api/owner/learning',json=body,headers=auth)
        assert result.status_code==200
        entry=result.json()
        assert c.post('/api/owner/learning/'+entry['id']+'/publish',json={'revision':'old'},headers=auth).status_code==409
        monkeypatch.setenv('PUBLIC_ONLY','true')
        assert c.get('/api/owner/learning',headers=auth).status_code==404
        assert c.post('/api/owner/learning',json=body,headers=auth).status_code==404
        assert c.post('/api/owner/learning/'+entry['id']+'/publish',json={'revision':entry['revision']},headers=auth).status_code==404
        assert c.delete('/api/owner/learning/'+entry['id'],headers=auth).status_code==404
