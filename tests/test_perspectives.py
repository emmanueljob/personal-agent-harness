import json
from harness.brain import FileBrain
from harness.store import Store
from harness.agent import prepare_turn,build_system_prompt,knowledge_tools


def record(view='I prefer small prototypes.',basis='owner_confirmed',status='owner_reported'):
    return {'id':'prototypes','basis':basis,'topics':['prototypes','iteration'],'view':view,
            'owner_source':'Owner statement in a test interview.',
            'influences':[{'source_id':'podcast:example:1','title':'Example episode','url':'https://example.org/episode','listening_status':status,'relevance':'A related idea.'}]}


def write(brain,scope,entry):
    root=brain.scope(scope);root.mkdir(parents=True,exist_ok=True)
    (root/'perspectives.json').write_text(json.dumps({'version':1,'entries':[entry]}))


def test_perspectives_preserve_basis_and_never_cross_public_scope(tmp_path):
    brain=FileBrain(tmp_path)
    write(brain,'public',record())
    write(brain,'private',record('Private unannounced project view.'))
    hits=brain.perspectives('what is your opinion of prototypes','public')
    assert len(hits)==1 and hits[0]['basis']=='owner_confirmed'
    assert 'owner_reported' in hits[0]['text']
    assert 'Private unannounced' not in json.dumps(hits)
    assert len(brain.perspectives('prototypes','private'))==2
    assert brain.perspectives('unrelated topic','public')==[]


def test_unknown_listening_and_inferred_views_are_not_promoted(tmp_path):
    brain=FileBrain(tmp_path)
    write(brain,'public',record(basis='inferred',status='unknown'))
    hit=brain.perspectives('prototypes','public')[0]
    assert hit['basis']=='inferred' and '"listening_status": "unknown"' in hit['text']
    assert 'owner_confirmed' not in hit['text']


def test_new_turn_snapshots_perspective_and_retry_keeps_original(tmp_path,monkeypatch):
    monkeypatch.setenv('MODEL_PROVIDER','openai');monkeypatch.setenv('OPENAI_API_KEY','test-only')
    monkeypatch.setenv('PUBLIC_ONLY','false')
    store=Store(tmp_path);store.set_profile('public','Approved career facts.')
    write(store.brain,'public',record())
    ident=store.enqueue(store.thread('public')['id'],'Your view on prototypes?')
    turn=store.query('SELECT * FROM turns WHERE id=?',(ident,))[0]
    mode,snapshot=prepare_turn(store,turn)
    write(store.brain,'public',record('Changed view.'))
    assert prepare_turn(store,turn)[1]['perspectives']==snapshot['perspectives']
    prompt=build_system_prompt(snapshot,mode)
    assert 'answer in first person' in prompt and 'explicitly AI representation' in prompt
    assert 'listening_status=owner_reported' in prompt
    assert 'I prefer small prototypes.' in prompt and 'Changed view.' not in prompt
    assert 'Do not conflate AI safety, cybersecurity' in prompt
    assert 'sponsor advertisements' in prompt


def test_tool_prioritizes_owner_perspective_over_reference_library(tmp_path):
    store=Store(tmp_path);write(store.brain,'public',record())
    tool=knowledge_tools({'mode':'public','store':store})[0]
    hits=json.loads(tool.invoke({'query':'prototypes'}))
    assert hits[0]['source'].startswith('public/perspectives.json#')
    assert 'owner_confirmed' in hits[0]['text']


def test_shared_search_preserves_complete_perspective_citations_and_budget(tmp_path,monkeypatch):
    monkeypatch.setenv('MODEL_PROVIDER','openai');monkeypatch.setenv('OPENAI_API_KEY','test-only')
    monkeypatch.setenv('PUBLIC_ONLY','false')
    store=Store(tmp_path);store.set_profile('public','Approved facts')
    write(store.brain,'public',record('I prefer prototypes. '*120))
    for i in range(3):store.brain.add_note('Prototypes '+str(i),'prototypes '*500,'public')
    result=store.brain.search('prototypes','public')[0]
    assert result['knowledge_role']=='owner_perspective'
    tool_result=json.loads(knowledge_tools({'mode':'public','store':store})[0].invoke({'query':'prototypes'}))
    evidence=json.loads(tool_result[0]['text'].split('\n\n',1)[1])
    assert evidence['influences'][0]['url']=='https://example.org/episode'
    ident=store.enqueue(store.thread('public')['id'],'Your view of prototypes?')
    _,snapshot=prepare_turn(store,store.query('SELECT * FROM turns WHERE id=?',(ident,))[0])
    assert len(json.dumps(snapshot['perspectives']))+sum(len(h['text']) for h in snapshot['knowledge'])<=10000
    assert all(h['source']!=result['source'] for h in snapshot['knowledge'])
