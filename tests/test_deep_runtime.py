import asyncio
import json
from types import SimpleNamespace
import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage, ToolMessage
from temporalio.exceptions import ApplicationError
from harness.store import Store
from harness import deep_runtime
from harness.deep_workflows import scoped_middleware


def test_routes_are_frozen_and_migrate_legacy(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_RUNTIME', 'legacy')
    store = Store(tmp_path)
    tid = store.thread('public')['id']
    old = store.enqueue(tid, 'old')
    monkeypatch.setenv('AGENT_RUNTIME', 'deepagents')
    assert store.enqueue(tid, 'old', old) == old
    new = store.enqueue(tid, 'new')
    assert store.runtime_for(old) == 'legacy'
    assert store.runtime_for(new) == 'deepagents'
    with store.connect() as db:
        db.execute('DELETE FROM turn_runtime WHERE turn_id=?', (old,))
        db.execute("INSERT INTO routines VALUES ('r',?,'routine',60,0,1)", (tid,))
    Store(tmp_path).tick(1)
    assert store.runtime_for(old) == 'legacy'
    assert store.runtime_for(store.query("SELECT id FROM turns WHERE prompt='routine'")[0]['id']) == 'deepagents'
    monkeypatch.setenv('AGENT_RUNTIME', 'legacy')
    assert Store(tmp_path).runtime_for(new) == 'deepagents'


def test_public_activity_cannot_invoke_private_tool_or_stale_binding(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('BRAIN_BACKEND', 'files')
    monkeypatch.setenv('AGENT_RUNTIME', 'deepagents')
    store = Store()
    ident = store.enqueue(store.thread('public')['id'], 'test')
    wid = store.workflow_id(ident)
    monkeypatch.setattr(deep_runtime.activity, 'info', lambda: SimpleNamespace(workflow_id=wid))
    with pytest.raises(ApplicationError, match='Unknown'):deep_runtime.binding()
    with store.connect() as db:
        db.execute('INSERT INTO workflow_turns VALUES (?,?,?)', (wid, ident, 'deepagents'))
        db.execute('INSERT INTO execution_context VALUES (?,?)', (ident, json.dumps({'config': {'web_enabled': False}})))
    with pytest.raises(ApplicationError, match='unavailable'):
        asyncio.run(deep_runtime.invoke_tool('save_artifact', {'title':'probe', 'markdown':'probe'}))
    assert not store.query('SELECT * FROM artifacts')
    with store.connect() as db:db.execute('INSERT INTO dispatch_attempts VALUES (?,1)', (ident,))
    with pytest.raises(ApplicationError, match='Stale'):deep_runtime.binding()


def test_middleware_only_exposes_application_tools(monkeypatch):
    from harness import deep_workflows
    captured = []
    async def save(messages):captured.append(messages)
    monkeypatch.setattr(deep_workflows, 'save_messages', save)
    middleware = scoped_middleware(['search_knowledge'])
    request = ModelRequest(model=FakeListChatModel(responses=['unused']), messages=[HumanMessage(content='test')], tools=[{'name':'search_knowledge'}, {'name':'execute'}, {'name':'task'}])
    async def handler(value):return value
    result = asyncio.run(middleware[0].awrap_model_call(request, handler))
    assert result.tools == [{'name':'search_knowledge'}] and captured
    result = asyncio.run(middleware[1].awrap_tool_call(SimpleNamespace(tool_call={'name':'execute','id':'probe'}), handler))
    assert isinstance(result, ToolMessage)


@pytest.mark.parametrize('asynchronous', [False, True])
def test_dispatch_preserves_sync_and_async_tool_failures(tmp_path, monkeypatch, asynchronous):
    from langchain_core.tools import StructuredTool

    store = Store(tmp_path)
    ident = store.enqueue(store.thread('private')['id'], 'Synthetic failure')
    turn = store.query('SELECT * FROM turns WHERE id=?', (ident,))[0]
    monkeypatch.setattr(deep_runtime, 'binding', lambda: (store, turn, 'private'))
    monkeypatch.setattr(deep_runtime, 'saved_config', lambda *_: {'web_enabled': False})
    def fail():
        raise ValueError('Synthetic tool failure')
    async def async_fail():
        raise ValueError('Synthetic tool failure')
    tool = StructuredTool.from_function(
        func=None if asynchronous else fail, coroutine=async_fail if asynchronous else None,
        name='fixture_failure', description='Fail with a synthetic error.')
    monkeypatch.setattr(deep_runtime.plugins, 'tools', lambda _: [tool])
    with pytest.raises(ValueError, match='Synthetic tool failure'):
        asyncio.run(deep_runtime.invoke_tool('fixture_failure', {}))
