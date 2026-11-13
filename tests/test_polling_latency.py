"""Synthetic contention checks for web polling and durable activity scheduling."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from harness import command_actions, command_center, deep_runtime
from harness.store import Store
from test_admin import AUTH, admin_app
from test_crm import add, company


def test_initialized_home_polls_do_not_wait_for_writer(tmp_path, monkeypatch):
    module, store = admin_app(tmp_path, monkeypatch)
    ident = add(store, company('Synthetic latency company', next_action='Review the role'))
    command_actions.initialize(store)
    # Current decisions already have intent history and require no backfill.
    card = command_center.feed(store)['cards'][0]
    command_center.disposition(store, ident, command_center.Disposition(
        revision=card['revision'], disposition_version=card['dispositionVersion'], state='later', request_id='synthetic-latency-decision'))
    # The fixture application's data directory is tmp_path/state.
    other_store = Store(store.root)
    with TestClient(module.app, base_url='https://testserver') as client:
        paths = ['/api/admin/command-center', '/api/admin/command-center/cards/'+ident+'/execution']
        for path in paths:
            assert client.get(path, headers=AUTH).status_code == 200
        with ThreadPoolExecutor(max_workers=1) as pool:
            with store.connect() as writer:
                writer.execute('BEGIN IMMEDIATE')
                def poll():
                    command_center.feed(other_store)
                    command_actions.initialize(other_store)
                    return [client.get(path, headers=AUTH) for path in paths]
                future = pool.submit(poll)
                try:
                    replies = future.result(timeout=1)
                finally:
                    writer.rollback()
            assert all(reply.status_code == 200 for reply in replies)


@pytest.mark.parametrize('stage', ['prepare', 'checkpoint', 'complete', 'tool'])
def test_activity_database_wait_does_not_block_event_loop(tmp_path, monkeypatch, stage):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('BRAIN_BACKEND', 'files')
    monkeypatch.setenv('AGENT_RUNTIME', 'deepagents')
    store = Store(tmp_path)
    ident = store.enqueue(store.thread('public')['id'], 'Synthetic greeting')
    wid = store.workflow_id(ident)
    with store.connect() as db:
        db.execute('INSERT INTO workflow_turns VALUES (?,?,?)', (wid, ident, 'deepagents'))
        db.execute('INSERT INTO execution_context VALUES (?,?)', (ident, json.dumps({'config': {'web_enabled': False}})))
    monkeypatch.setattr(deep_runtime.activity, 'info', lambda: SimpleNamespace(workflow_id=wid))
    monkeypatch.setattr(deep_runtime, 'prepare_turn', lambda *_: (
        'public', {'config': {'web_enabled': False, 'provider': 'ollama', 'model': 'fake'}, 'messages': []}))
    monkeypatch.setattr(deep_runtime, 'build_system_prompt', lambda *_: 'Synthetic')
    monkeypatch.setattr(deep_runtime.plugins, 'tools', lambda _: [])

    async def exercise():
        if stage == 'prepare':
            pending = deep_runtime.prepare(ident)
        elif stage == 'checkpoint':
            pending = deep_runtime.checkpoint([])
        elif stage == 'complete':
            pending = deep_runtime.complete('Synthetic reply')
        else:
            pending = deep_runtime.invoke_tool('unavailable', {})
        task = asyncio.create_task(pending)
        started = time.monotonic()
        await asyncio.sleep(.05)
        delay = time.monotonic() - started
        release.set()
        if stage == 'tool':
            from temporalio.exceptions import ApplicationError
            with pytest.raises(ApplicationError, match='unavailable'):
                await task
        else:
            await task
        assert delay < .5, f'{stage} blocked event loop for {delay:.3f}s'

    acquired, release = threading.Event(), threading.Event()
    def hold():
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            acquired.set()
            release.wait(1.5)
    with ThreadPoolExecutor(max_workers=1) as pool:
        holder = pool.submit(hold)
        assert acquired.wait(2)
        try:
            asyncio.run(exercise())
        finally:
            release.set()
            holder.result()
