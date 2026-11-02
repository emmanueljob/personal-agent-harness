import importlib
from fastapi.testclient import TestClient
from harness.store import Store


def test_routines_catch_up_once_and_survive_restart(tmp_path):
    s = Store(tmp_path)
    t = s.thread("private")
    with s.connect() as db:
        db.execute("INSERT INTO routines VALUES ('r',?,?,60,100,1)", (t["id"], "draft"))
    s.tick(300)
    Store(tmp_path).tick(300)
    assert len(s.query("SELECT * FROM turns")) == 1
    assert s.query("SELECT next_run FROM routines")[0]["next_run"] == 360


def test_artifacts_idempotent(tmp_path):
    s = Store(tmp_path)
    first = s.artifact("thread", "turn", "Bio", "a")
    assert first == s.artifact("thread", "turn", "Bio", "b")
    assert s.query("SELECT body FROM artifacts") == [{"body": "b"}]


def test_private_routes_and_public_thread_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OWNER_TOKEN", "test-owner")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    module = importlib.import_module("harness.app")
    monkeypatch.setattr(module, "store", Store(tmp_path))
    with TestClient(module.app) as c:
        auth = {"Authorization": "Bearer test-owner"}
        assert c.post("/api/threads", json={"mode": "private"}).status_code == 401
        private = c.post("/api/threads", json={"mode": "private"}, headers=auth).json()
        assert c.get('/api/threads/' + private['id']).status_code == 401
        assert c.get('/api/threads/' + private['id'], headers=auth).status_code == 200
        private_turn = module.store.enqueue(private['id'], 'private test')
        module.store.event(private_turn, 'generating', 'private event')
        assert c.put("/api/profiles/public", json={"body": "test"}).status_code == 401
        public = c.post("/api/threads", json={"mode": "public"}).json()
        assert c.get('/api/threads/' + public['id']).json()['events'] == []
        assert c.post('/api/threads/' + public['id'] + '/turns', json={"prompt":"Hi"}).status_code == 503


def test_turn_recovery_and_completed_idempotency(tmp_path, monkeypatch):
    from harness import runner
    s = Store(tmp_path)
    tid = s.thread("private")["id"]
    ident = s.enqueue(tid, "hello")
    calls = []
    monkeypatch.setattr(runner, "execute", lambda store, turn: calls.append(turn["id"]) or "answer")
    assert runner.run_turn(s, ident) == "answer"
    assert runner.run_turn(Store(tmp_path), ident) == "answer"
    assert calls == [ident]


def test_public_has_no_artifact_tools(tmp_path):
    from harness.agent import plugins
    assert [t.name for t in plugins.tools({"mode":"public", "store":Store(tmp_path)})] == ["search_knowledge", "search_web", "read_webpage", "browse_bookshelf"]


def test_graph_checkpoints_resume_without_second_model_call(tmp_path, monkeypatch):
    from harness import agent
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    model = FakeListChatModel(responses=["Verified response", "Should not be used"])
    monkeypatch.setattr(FakeListChatModel, "bind_tools", lambda self, *args, **kwargs: self)
    monkeypatch.setattr(agent, "ChatOpenAI", lambda **kwargs: model)
    s = Store(tmp_path)
    tid = s.thread("public")["id"]
    ident = s.enqueue(tid, "Tell me about yourself")
    turn = s.query("SELECT * FROM turns WHERE id=?", (ident,))[0]
    assert agent.execute(s, turn) == "Verified response"
    assert agent.execute(Store(tmp_path), turn) == "Verified response"
    assert model.i == 1


def test_worker_recovers_interrupted_turn(tmp_path, monkeypatch):
    import asyncio
    from harness import runner
    s = Store(tmp_path)
    ident = s.enqueue(s.thread("private")["id"], "resume me")
    with s.connect() as db:
        db.execute("UPDATE turns SET status='running' WHERE id=?", (ident,))
    monkeypatch.setattr(runner, "execute", lambda store, turn: "Recovered")

    async def recover():
        task = asyncio.create_task(runner.local_worker(Store(tmp_path)))
        try:
            async with asyncio.timeout(3):
                while s.query("SELECT status FROM turns WHERE id=?", (ident,))[0]["status"] != "completed":
                    await asyncio.sleep(0.02)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    asyncio.run(recover())
    assert s.query("SELECT result FROM turns WHERE id=?", (ident,))[0]["result"] == "Recovered"


def test_retry_uses_new_workflow_id_without_new_turn(tmp_path):
    s = Store(tmp_path)
    ident = s.enqueue(s.thread('private')['id'], 'retry me')
    original = s.workflow_id(ident)
    with s.connect() as db:
        db.execute("UPDATE turns SET status='failed' WHERE id=?", (ident,))
    assert s.retry(ident)
    assert s.workflow_id(ident) != original
    assert not s.retry(ident)
    assert len(s.query('SELECT * FROM turns')) == 1


def test_model_snapshot_survives_profile_and_provider_change(tmp_path, monkeypatch):
    from harness import agent
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    monkeypatch.setenv('MODEL_PROVIDER', 'openai')
    monkeypatch.setenv('OPENAI_API_KEY', 'fake')
    monkeypatch.setenv('OPENAI_MODEL', 'original-model')
    model = FakeListChatModel(responses=['original answer', 'wrong'])
    models = []
    monkeypatch.setattr(FakeListChatModel, 'bind_tools', lambda self, *args, **kwargs: self)
    monkeypatch.setattr(agent, 'ChatOpenAI', lambda **kw: models.append(kw['model']) or model)
    s = Store(tmp_path)
    s.set_profile('public', 'Original profile')
    voice = s.brain.scope('public') / 'voice.md'
    voice.write_text('Original conversational voice')
    note = s.brain.add_note('Orchid preferences', 'Original orchid preference', 'public')
    s.brain.add_note('Orchid secret', 'Private orchid secret', 'private')
    ident = s.enqueue(s.thread('public')['id'], 'orchid preferences')
    turn = s.query('SELECT * FROM turns WHERE id=?', (ident,))[0]
    assert agent.execute(s, turn) == 'original answer'
    monkeypatch.setenv('MODEL_PROVIDER', 'ollama')
    s.set_profile('public', 'new profile')
    voice.write_text('New voice')
    (s.brain.scope('public') / 'wiki' / note['id']).write_text('Changed orchid preference')
    assert agent.execute(s, turn) == 'original answer'
    assert models == ['original-model', 'original-model']
    snapshot = s.query('SELECT body FROM execution_context')[0]['body']
    assert 'new profile' not in snapshot
    assert 'Original conversational voice' in snapshot
    assert 'Original orchid preference' in snapshot
    assert 'Changed orchid preference' not in snapshot
    assert 'Private orchid secret' not in snapshot


def test_local_model_submissions_need_no_openai_key(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('MODEL_PROVIDER', 'ollama')
    monkeypatch.setenv('EXECUTOR', 'temporal')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    module = importlib.import_module('harness.app')
    monkeypatch.setattr(module, 'store', Store(tmp_path))
    # No lifespan: exercise queue persistence without a worker/inference call.
    c = TestClient(module.app)
    tid = c.post('/api/threads', json={'mode':'public'}).json()['id']
    body = {'prompt':'hello', 'request_id':'same-request'}
    first = c.post('/api/threads/'+tid+'/turns',json=body)
    again = c.post('/api/threads/'+tid+'/turns',json=body)
    assert first.status_code == 202
    assert first.json()['id'] == again.json()['id']
    assert c.post('/api/threads/'+tid+'/turns',json={**body,'prompt':'different'}).status_code == 409
    assert len(module.store.query('SELECT * FROM turns')) == 1


def test_brain_visibility_and_symlink_boundaries(tmp_path):
    import pytest
    from harness.brain import FileBrain
    brain = FileBrain(tmp_path / 'brain')
    brain.add_note('Public fact', 'Orchids are a public interest.', 'public')
    brain.add_note('Private fact', 'The secret orchid code is violet.')
    assert all(r['source'].startswith('public/') for r in brain.search('orchid orchids', 'public'))
    assert not brain.search('violet', 'public')
    assert brain.search('violet', 'private')
    public = tmp_path / 'brain' / 'public'
    (public / 'leak.md').symlink_to(tmp_path / 'brain' / 'private' / 'wiki' / next((tmp_path / 'brain' / 'private' / 'wiki').iterdir()).name)
    assert not brain.search('violet', 'public')
    (public / 'profile.md').symlink_to(tmp_path / 'secret.md')
    with pytest.raises(ValueError):
        brain.profile('public')
    import shutil
    shutil.rmtree(public / 'wiki')
    (public / 'wiki').symlink_to(tmp_path / 'brain' / 'private' / 'wiki')
    with pytest.raises(ValueError):
        brain.add_note('Escape', 'must not write', 'public')


def test_public_presentation_and_owner_note_boundary(tmp_path, monkeypatch):
    import json
    module = importlib.import_module('harness.app')
    s = Store(tmp_path)
    monkeypatch.setattr(module, 'store', s)
    monkeypatch.setenv('OWNER_TOKEN', 'test-owner')
    public = s.brain.scope('public')
    public.mkdir(parents=True)
    (public / 'portfolio.json').write_text(json.dumps({'name': 'Test Owner', 'private_resume': 'secret', 'phone': 'secret'}))
    s.brain.set_profile('public', 'Published biography')
    s.brain.set_profile('private', 'Private biography')
    (public / 'voice.md').write_text('Warm and concise')
    (s.brain.scope('private') / 'voice.md').write_text('Private analysis')
    c = TestClient(module.app)
    presentation = c.get('/api/portfolio').json()
    assert presentation['name'] == 'Test Owner'
    assert 'secret' not in json.dumps(presentation)
    assert s.brain.voice('public') == 'Warm and concise'
    assert Store(tmp_path).profile('public') == 'Published biography'
    body = {'title': 'Test note', 'body': 'Confidential note'}
    assert c.post('/api/brain/notes', json=body).status_code == 401
    result = c.post('/api/brain/notes', json=body, headers={'Authorization': 'Bearer test-owner'})
    assert result.status_code == 200
    assert not s.brain.search('Confidential', 'public')
    assert s.brain.search('Confidential', 'private')


def test_local_template_analysis_is_not_returned_as_the_answer():
    from harness.agent import final_answer
    assert final_answer('<think>internal draft</think>Visible answer', 'ollama') == 'Visible answer'
    assert final_answer('draft without opening marker</think>Final answer', 'ollama') == 'Final answer'
    assert final_answer('Ordinary answer', 'ollama') == 'Ordinary answer'
    assert final_answer('Literal </think> example', 'openai') == 'Literal </think> example'
