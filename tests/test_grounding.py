"""Synthetic evidence-selection regressions: no personal corpus or model calls."""
import json
import pytest

from harness.agent import prepare_turn, knowledge_tools, build_system_prompt, conversation_input
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


@pytest.mark.parametrize('provider', ['openai', 'ollama'])
def test_short_followup_fits_large_serialized_evidence(tmp_path, monkeypatch, provider):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('MODEL_PROVIDER', provider)
    monkeypatch.setenv('PUBLIC_ONLY', 'true')
    monkeypatch.setenv('OLLAMA_CONTEXT', '16384')
    monkeypatch.setenv('WEB_SEARCH_PROVIDER', 'disabled')
    store = Store(tmp_path)
    store.set_profile('public', 'Verified career facts. ' * 350)
    voice = 'Use a conversational tone. ' * 180
    monkeypatch.setattr(store.brain, 'voice', lambda mode: voice)
    # Escaping structured non-ASCII notes consumes much more space than their
    # raw text. Keep the basis, limits and original citations intact.
    perspectives = [dict(source='public/perspectives.json#view', score=100,
                         knowledge_role='owner_perspective', basis='inferred',
                         text=json.dumps(dict(view='Consider small experiments.',
                                              limits='Tentative interpretation.',
                                              url='https://example.org/idea')))]
    hits = [dict(source=f'public/wiki/note-{i}.md', score=5-i,
                 text='Evidence with attribution. ' + '漢' * 3500) for i in range(5)]
    hits.insert(0, dict(source='public/wiki/short.md', score=6, text='A small relevant note.'))
    monkeypatch.setattr(store.brain, 'perspectives', lambda query, mode: perspectives)
    monkeypatch.setattr(store.brain, 'search', lambda query, mode: hits)
    ident = store.enqueue(store.thread('public')['id'], 'try again')
    turn = store.query('SELECT * FROM turns WHERE id=?', (ident,))[0]
    config = dict(provider=provider, public_deployment=True, context_window=16384)
    with pytest.raises(ValueError, match='configured model context'):
        conversation_input(store.profile('public'), [], turn['prompt'], config,
                           voice + json.dumps(hits) + json.dumps(perspectives))
    _, snapshot = prepare_turn(store, turn)
    from harness.personalization import guide_context
    assert snapshot['profile'] == store.profile('public') + guide_context('default')
    assert snapshot['voice'] == voice
    assert snapshot['messages'][-1]['content'] == 'try again'
    assert snapshot['perspectives'] == perspectives
    assert len(snapshot['knowledge']) < len(hits)
    assert any(hit['text'] == 'A small relevant note.' for hit in snapshot['knowledge'])
    conversation_input(snapshot['profile'], [], turn['prompt'], snapshot['config'],
                       voice + json.dumps(snapshot['knowledge']) + json.dumps(snapshot['perspectives']))
    monkeypatch.setattr(store.brain, 'search', lambda *args: pytest.fail('Retry re-ran retrieval'))
    assert prepare_turn(store, turn)[1] == snapshot


def test_required_input_still_rejects_oversized_question():
    from harness.agent import fit_turn_evidence
    with pytest.raises(ValueError, match='configured model context'):
        fit_turn_evidence('Required profile', 'large question ' * 5000,
                          dict(provider='ollama', context_window=8192), '', [], [])
