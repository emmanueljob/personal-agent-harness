"""Synthetic evidence-selection regressions: no personal corpus or model calls."""
import json

from harness.agent import prepare_turn, knowledge_tools, build_system_prompt
from harness.retrieval import evidence, grounding_query, rank_knowledge
from harness.store import Store


def test_references_cannot_crowd_out_relevant_personal_notes_or_books():
    hits = [dict(source=f'public/references/podcasts/{i}.json#chunk-0000',text='External engineering reference.',score=100-i) for i in range(8)]
    hits += [dict(source='public/wiki/leadership.md',text='A recorded principle.',score=2),
             dict(source='public/bookshelf.json#book',text='A book influence.',score=1)]
    selected = rank_knowledge(hits)
    assert len(selected) == 5
    assert any(h['source']=='public/wiki/leadership.md' for h in selected)
    assert any('bookshelf.json' in h['source'] for h in selected)
    assert sum('/references/' in h['source'] for h in selected) == 3
    # A narrowly requested source is still available; one-result searches keep
    # the highest lexical match instead of substituting a generic note.
    assert '/references/' in rank_knowledge(hits,limit=1)[0]['source']


def test_queries_resolve_followups_from_user_questions_not_generated_claims():
    previous=[dict(prompt='How do you approach testing?',result='UNSUPPORTED_MODEL_CLAIM')]
    query=grounding_query('Why?',previous,'Fixture Owner')
    assert 'testing' in query and 'UNSUPPORTED_MODEL_CLAIM' not in query
    assert 'testing' in grounding_query('Can you give an example?',previous+[dict(prompt='Why?',result='ANOTHER_UNVERIFIED_CLAIM')])
    assert grounding_query('What about cooking?',previous,'Fixture Owner')=='What about cooking?'
    assert 'interests' in grounding_query('Tell me about Fixture Owner',previous,'Fixture Owner')
    assert 'leadership' in grounding_query('Introduce yourself')


def test_evidence_retains_titles_attribution_and_only_supplied_links():
    body='# Owner learning note\n\n'+json.dumps(dict(title='Example book',url='https://example.org/book',basis='owner_confirmed',note='Small experiments.'))
    hit=dict(source='public/learning.json#book',knowledge_role='owner_learning',text=body)
    result=evidence(hit,body)
    assert result['title']=='Example book' and result['knowledge_role']=='owner_learning'
    assert result['basis']=='owner_confirmed'
    assert result['citation']==dict(label='Example book',url='https://example.org/book')
    result=evidence(dict(source='public/wiki/leadership.md',text='A recorded principle.'),'A recorded principle.')
    assert result['citation']==dict(label='leadership',url='')
    malicious=body.replace('https://example.org/book','javascript:alert(1)')
    assert evidence({**hit,'text':malicious},malicious)['citation']['url']==''


def test_turn_grounding_is_scoped_contextual_and_immutable(tmp_path,monkeypatch):
    monkeypatch.setenv('MODEL_PROVIDER','openai');monkeypatch.setenv('PUBLIC_ONLY','false')
    store=Store(tmp_path);store.set_profile('public','An example career profile.')
    store.brain.add_note('Testing principles','Prefer small software experiments.','public')
    store.brain.add_note('Private principle','PRIVATE_ONLY_EXPERIMENT','private')
    thread=store.thread('public')['id']
    first=store.enqueue(thread,'How do you approach testing?')
    with store.connect() as db:
        db.execute("UPDATE turns SET status='completed',result=? WHERE id=?",('GENERATED_UNVERIFIED_CLAIM',first))
    ident=store.enqueue(thread,'Can you give an example?')
    turn=store.query('SELECT * FROM turns WHERE id=?',(ident,))[0]
    mode,snapshot=prepare_turn(store,turn)
    assert 'testing' in snapshot['retrieval_query']
    encoded=json.dumps(snapshot['knowledge'])
    assert 'small software experiments' in encoded and 'PRIVATE_ONLY_EXPERIMENT' not in encoded
    assert 'GENERATED_UNVERIFIED_CLAIM' not in snapshot['retrieval_query']
    assert all('citation' in hit for hit in snapshot['knowledge'])
    store.brain.add_note('Testing changes','NEW_FACT_AFTER_SNAPSHOT','public')
    assert prepare_turn(store,turn)[1] == snapshot
    prompt=build_system_prompt(snapshot,mode)
    assert 'Cite the evidence you actually use' in prompt
    assert 'retrieved-but-unused' in prompt
    assert 'published note' in prompt


def test_tool_preserves_source_metadata(tmp_path):
    store=Store(tmp_path)
    store.brain.add_note('Testing principles','Small software experiments.','public')
    hits=json.loads(knowledge_tools(dict(store=store,mode='public'))[0].invoke(dict(query='testing')))
    assert hits and hits[0]['citation']['label']
    assert hits[0]['knowledge_role']=='published_note'
