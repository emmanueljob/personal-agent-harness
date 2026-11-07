import json
import pytest
from fastapi.testclient import TestClient
from harness import models
from harness.agent import prepare_turn
from test_agent_surfaces import fixture_app


def test_provider_context_allowances_are_independent_and_saved_limits_win(monkeypatch):
    monkeypatch.setenv('OLLAMA_CONTEXT', '16384')
    monkeypatch.delenv('OPENAI_CONTEXT', raising=False)
    assert models.context_window(dict(provider='openai')) == 131072
    assert models.context_window(dict(provider='ollama')) == 16384
    monkeypatch.setenv('OPENAI_CONTEXT', '262144')
    assert models.context_window(dict(provider='openai')) == 262144
    assert models.context_window(dict(provider='ollama')) == 16384
    assert models.context_window(dict(provider='openai', context_window=16384)) == 16384


def test_new_openai_turns_have_more_room_and_retries_keep_saved_allowance(tmp_path, monkeypatch):
    from harness.agent import conversation_input
    _, store = fixture_app(tmp_path, monkeypatch)
    monkeypatch.setenv('MODEL_PROVIDER', 'openai')
    monkeypatch.setenv('OLLAMA_CONTEXT', '16384')
    monkeypatch.delenv('OPENAI_CONTEXT', raising=False)
    thread = store.thread('public')['id']
    ident = store.enqueue(thread, 'A short question')
    turn = store.query('SELECT * FROM turns WHERE id=?', (ident,))[0]
    _, snapshot = prepare_turn(store, turn)
    assert snapshot['config']['context_window'] == 131072
    config = {**snapshot['config'], 'public_deployment': True}
    # A large background fits OpenAI while the laptop allowance still rejects it.
    background = 'Synthetic published evidence. ' * 3000
    assert conversation_input('Profile', [], 'Why?', config, background)
    with pytest.raises(ValueError, match='configured model context'):
        conversation_input('Profile', [], 'Why?', {**config, 'context_window': 16384}, background)
    monkeypatch.setenv('OPENAI_CONTEXT', '262144')
    assert prepare_turn(store, turn)[1] == snapshot
    new = store.enqueue(thread, 'Another short question')
    assert prepare_turn(store, store.query('SELECT * FROM turns WHERE id=?', (new,))[0])[1]['config']['context_window'] == 262144


def test_openai_default_is_not_overridden_by_online_mac(monkeypatch):
    monkeypatch.setenv('MODEL_PROVIDER','openai')
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-openai-key')
    monkeypatch.setenv('MODEL_BRIDGE_TOKEN','synthetic-bridge-key')
    monkeypatch.setattr('harness.model_bridge.available',lambda:True)
    default=models.settings()
    assert default['provider']=='openai' and default['configured']
    qwen=models.settings('qwen')
    assert qwen['provider']=='ollama' and qwen['bridged']
    assert qwen['base_url']=='http://127.0.0.1:8080/api/model-bridge'
    assert 'synthetic-bridge-key' not in json.dumps(qwen)
    monkeypatch.setattr('harness.model_bridge.available',lambda:False)
    with pytest.raises(ValueError,match='offline'):models.settings('qwen')
    assert models.settings()['provider']=='openai'


def test_public_qwen_needs_a_bridge_and_invalid_choice_is_rejected(monkeypatch):
    monkeypatch.setenv('MODEL_PROVIDER','openai')
    monkeypatch.setenv('PUBLIC_ONLY','true')
    monkeypatch.delenv('MODEL_BRIDGE_TOKEN',raising=False)
    assert not models.qwen_available()
    with pytest.raises(ValueError):models.settings('qwen')
    with pytest.raises(ValueError):models.settings('arbitrary-provider')


def test_choice_is_durable_idempotent_and_does_not_expose_connection_settings(tmp_path,monkeypatch):
    module,store=fixture_app(tmp_path,monkeypatch)
    monkeypatch.setenv('MODEL_PROVIDER','openai')
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-openai-key')
    monkeypatch.setenv('MODEL_BRIDGE_TOKEN','synthetic-bridge-key')
    monkeypatch.setenv('OLLAMA_CONTEXT','32768')
    monkeypatch.setattr('harness.model_bridge.available',lambda:True)
    with TestClient(module.app,base_url='https://testserver') as c:
        ident=c.post('/api/threads',json={'mode':'public'}).json()['id']
        body={'prompt':'A synthetic question','request_id':'fixed','model_choice':'qwen'}
        queued=c.post('/api/threads/'+ident+'/turns',json=body)
        assert queued.status_code==202
        tid=queued.json()['id']
        assert store.query('SELECT choice FROM turn_model_choice WHERE turn_id=?',(tid,))[0]['choice']=='qwen'
        turn=store.query('SELECT * FROM turns WHERE id=?',(tid,))[0]
        _,snapshot=prepare_turn(store,turn)
        assert snapshot['config']['provider']=='ollama' and snapshot['config']['bridged']
        monkeypatch.setattr('harness.model_bridge.available',lambda:False)
        assert prepare_turn(store,turn)[1]==snapshot
        assert c.post('/api/threads/'+ident+'/turns',json=body).status_code==202
        assert c.post('/api/threads/'+ident+'/turns',json={**body,'model_choice':'openai'}).status_code==409
        history=c.get('/api/threads/'+ident).json()
        assert history['turns'][0]['provider']=='ollama'
        assert 'synthetic-bridge-key' not in json.dumps(history) and 'base_url' not in json.dumps(history)
        offline=c.post('/api/threads/'+ident+'/turns',json={**body,'request_id':'other'})
        assert offline.status_code==503 and 'offline' in offline.json()['detail']
        assert len(store.query('SELECT * FROM turns'))==1
        assert c.post('/api/threads/'+ident+'/turns',json={'prompt':'Another synthetic question'}).status_code==202
        assert c.post('/api/threads/'+ident+'/turns',json={'prompt':'Bad','model_choice':'arbitrary'}).status_code==422
        assert c.get('/api/status').json()['qwen_available'] is False


def test_legacy_snapshots_keep_the_original_provider_and_default_new_turns_use_openai(tmp_path,monkeypatch):
    _,store=fixture_app(tmp_path,monkeypatch)
    monkeypatch.setenv('MODEL_PROVIDER','openai')
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-openai-key')
    monkeypatch.setenv('OLLAMA_CONTEXT','32768')
    ident=store.enqueue(store.thread('public')['id'],'A synthetic question')
    old={'config':{'provider':'ollama','model':'legacy-fixture'},'profile':'fixture','messages':[],'voice':'','knowledge':[],'perspectives':[]}
    with store.connect() as db:
        db.execute('DELETE FROM turn_model_choice WHERE turn_id=?',(ident,))
        db.execute('INSERT INTO execution_context VALUES (?,?)',(ident,json.dumps(old)))
    assert prepare_turn(store,store.query('SELECT * FROM turns WHERE id=?',(ident,))[0])[1]==old
    new=store.enqueue(store.thread('public')['id'],'Another synthetic question')
    assert prepare_turn(store,store.query('SELECT * FROM turns WHERE id=?',(new,))[0])[1]['config']['provider']=='openai'
