import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from harness.store import Store
from harness.media_library import catalog, search_media, media_tools, published_books
from harness.learning import Annotation, Learning
from harness.ingestion import normalize, upsert_batch


def fixture(tmp_path,monkeypatch):
    monkeypatch.setenv('BRAIN_BACKEND','files')
    monkeypatch.setenv('PUBLIC_ONLY','false')
    store=Store(tmp_path/'state')
    for scope in ('public','private'):
        root=store.brain.scope(scope);root.mkdir(parents=True,exist_ok=True)
        (root/'bookshelf.json').write_text(json.dumps({'books':[{'id':scope+'-book','title':scope+' systems book','author':'Fixture author','comment':'','category':'work'}]}))
        records=[normalize({'source_id':'podcast:'+scope+':one','title':scope+' episode about building teams','text':'REFERENCE TEXT CANARY this is a transcript, not owner beliefs.','source_url':'https://example.test/'+scope,'source_kind':'podcast','provenance':{'show':scope+' show','published':'2026-01-02'}},'podcasts',scope)]
        upsert_batch(store.brain,records,'podcasts',scope,apply=True,allow_public=scope=='public')
    learning=Learning(store.brain)
    public_episode=next(r for r in catalog(store.brain) if r['kind']=='podcast')
    note=learning.save(Annotation(kind='podcast',target_id=public_episode['id'],note='Autonomy was the useful idea here. Not convinced by the advice on hiring.',tags=['leadership'],experience='listened'),store)
    learning.publish(note['id'],note['revision'])
    private_episode=next(r for r in catalog(store.brain,'private') if r['kind']=='podcast' and r['author']=='private show')
    learning.save(Annotation(kind='podcast',target_id=private_episode['id'],note='PRIVATE NOTE CANARY',tags=['secret']),store)
    return store,learning,public_episode,note


def test_recommendations_list_notes_without_topic_overlap_and_keep_provenance(tmp_path,monkeypatch):
    store,_,episode,_=fixture(tmp_path,monkeypatch)
    data=search_media(store.brain,'give some good podcast recommendations from owner',kind='podcast',notes_only=True)
    assert data['total']==1
    row=data['results'][0]
    assert row['id']==episode['id'] and row['experience']=='listened'
    assert row['recommendation_basis']=='owner_note' and 'Not convinced' in row['note']
    assert 'explicit recommendation' in data['guidance']
    assert 'PRIVATE NOTE CANARY' not in json.dumps(data) and 'private show' not in json.dumps(data)
    assert 'REFERENCE TEXT CANARY' not in json.dumps(data)
    assert search_media(store.brain,'autonomy',kind='podcast')['total']==1
    assert search_media(store.brain,'leadership',kind='podcast')['total']==1
    assert search_media(store.brain,'nothing-matches',kind='podcast')['total']==0
    assert len(search_media(store.brain,kind='podcast',show='public show')['results'])==1
    assert search_media(store.brain,kind='podcast',show='private show')['total']==0
    assert 'PRIVATE NOTE CANARY' in json.dumps(search_media(store.brain,mode='private'))


def test_private_edits_and_drafts_do_not_change_public_notes_and_cache_invalidates(tmp_path,monkeypatch):
    store,learning,episode,note=fixture(tmp_path,monkeypatch)
    learning.save(Annotation(kind='podcast',target_id=episode['id'],revision=note['revision'],note='PRIVATE EDIT CANARY',experience='unknown'),store)
    assert 'PRIVATE EDIT CANARY' not in json.dumps(search_media(store.brain))
    learning.remove(note['id'],public_only=True)
    row=next(r for r in catalog(store.brain) if r['id']==episode['id'])
    assert not row['has_owner_note'] and row['experience']=='unknown'
    assert not search_media(store.brain,notes_only=True)['results']
    root=store.brain.scope('public')/'references/podcasts'
    next(root.glob('*.json')).unlink()
    assert not search_media(store.brain,kind='podcast')['results']


def test_books_search_and_shelf_use_reviewed_notes_without_changing_source_files(tmp_path,monkeypatch):
    store,learning,_,_=fixture(tmp_path,monkeypatch)
    original=(store.brain.scope('public')/'bookshelf.json').read_bytes()
    note=learning.save(Annotation(kind='book',target_id='public-book',note='Useful experiments and a shorter feedback loop.',tags=['validation'],experience='read'),store)
    learning.publish(note['id'],note['revision'])
    data=search_media(store.brain,'validation',kind='book')
    assert data['results'][0]['note']=='Useful experiments and a shorter feedback loop.'
    book=published_books(store.brain)[0]
    assert book['comment']==data['results'][0]['note'] and book['status']=='read'
    assert book['comment_source']=='Owner-reviewed reading note'
    assert (store.brain.scope('public')/'bookshelf.json').read_bytes()==original


def test_library_tool_is_scoped_bounded_and_records_evidence(tmp_path,monkeypatch):
    store,_,_,_=fixture(tmp_path,monkeypatch)
    tid=store.thread('public')['id'];ident=store.enqueue(tid,'podcast recommendations')
    tool=media_tools({'store':store,'mode':'public','turn_id':ident})[0]
    result=json.loads(tool.invoke({'kind':'podcast','notes_only':True}))
    assert result['sources'][0]['url']=='https://example.test/public'
    assert store.query('SELECT body FROM tool_results WHERE turn_id=? AND tool=?',(ident,'search_library'))
    assert 'private show' not in json.dumps(result)
    for kwargs in ({'limit':1000},{'query':'x'*201},{'kind':'bad'},{'offset':-1}):
        with pytest.raises(ValueError):search_media(store.brain,**kwargs)


def test_public_http_mcp_catalog_and_deep_link_hide_private_sources(tmp_path,monkeypatch):
    from test_agent_surfaces import fixture_app
    module,_=fixture_app(tmp_path,monkeypatch)
    store,_,episode,_=fixture(tmp_path,monkeypatch)
    monkeypatch.setenv('PUBLIC_ONLY','true')
    monkeypatch.setattr(module,'store',store)
    with TestClient(module.app) as c:
        for path in ('/api/library?kind=podcast&notes_only=true','/api/public/library?kind=podcast&notes_only=true'):
            r=c.get(path);assert r.status_code==200
            assert r.json()['total']==1 and 'PRIVATE NOTE CANARY' not in r.text
        assert c.get('/api/library/podcast/'+episode['id']).json()['note']
        assert c.get('/api/library/podcast/private-id').status_code==404
        assert c.get('/api/library?kind=private').status_code==422
        tools=c.get('/api/public/tools').json()['tools']
        assert 'search_library' in [t['name'] for t in tools]
        assert '/api/public/library' in c.get('/api/public/openapi.json').json()['paths']


def test_new_recommendation_turn_snapshots_published_notes_and_retry_keeps_snapshot(tmp_path,monkeypatch):
    from harness.agent import prepare_turn,build_system_prompt
    store,learning,episode,note=fixture(tmp_path,monkeypatch)
    monkeypatch.setenv('MODEL_PROVIDER','ollama')
    monkeypatch.setenv('OLLAMA_CONTEXT','32768')
    monkeypatch.setenv('WEB_SEARCH_PROVIDER','disabled')
    ident=store.enqueue(store.thread('public')['id'],'give some good podcast recommendations from owner')
    turn=store.query('SELECT * FROM turns WHERE id=?',(ident,))[0]
    mode,snapshot=prepare_turn(store,turn)
    assert mode=='public'
    evidence=json.dumps(snapshot['knowledge'])
    assert 'Autonomy was the useful idea' in evidence
    assert 'PRIVATE NOTE CANARY' not in evidence
    assert sum(len(r['text']) for r in snapshot['knowledge'])<=10000
    assert 'no separate curated playlist is required' in build_system_prompt(snapshot,mode)
    learning.remove(note['id'],public_only=True)
    assert prepare_turn(store,turn)[1]==snapshot
