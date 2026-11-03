import asyncio
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
import pytest
from temporalio.client import Client
from harness.store import Store
from harness.durable import dispatch_once, reconcile_schedules

pytestmark = pytest.mark.skipif(not os.getenv("TEMPORAL_TEST_ADDRESS"), reason="Requires a running Temporal dev server")


def test_worker_crash_outbox_recovery_and_schedules(tmp_path, monkeypatch):
    queue = "test-" + str(uuid.uuid4())
    monkeypatch.setenv("TEMPORAL_TASK_QUEUE", queue)
    s = Store(tmp_path)
    thread_id = s.thread("private")["id"]
    turn_id = s.enqueue(thread_id, "Test recovery")
    marker = tmp_path / "entered"
    env = {**os.environ, "DATA_DIR": str(tmp_path), "TEMPORAL_TASK_QUEUE": queue,
           "TEMPORAL_ADDRESS": os.environ["TEMPORAL_TEST_ADDRESS"], "PYTHONPATH": str(Path.cwd())}
    processes = []

    def worker(crash=False):
        proc = subprocess.Popen([sys.executable, "tests/durable_worker_fixture.py"], env={**env, **({"CRASH_MARKER": str(marker)} if crash else {})}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processes.append(proc)
        return proc

    async def check():
        client = await Client.connect(env["TEMPORAL_ADDRESS"])
        first = worker(crash=True)
        await dispatch_once(client, s)
        # Same outbox delivery again must attach to the existing workflow.
        await dispatch_once(client, Store(tmp_path))
        async with asyncio.timeout(25):
            while not marker.exists():
                await asyncio.sleep(.1)
        first.kill()
        first.wait()
        worker()
        handle = client.get_workflow_handle(s.workflow_id(turn_id))
        assert await asyncio.wait_for(handle.result(), 75) == turn_id
        assert s.query("SELECT status FROM turns WHERE id=?", (turn_id,))[0]["status"] == "completed"
        assert len(s.query("SELECT * FROM artifacts")) == 1

        rid = str(uuid.uuid4())
        with s.connect() as db:
            db.execute("INSERT INTO routines VALUES (?,?,?,?,?,1)", (rid, thread_id, "scheduled prompt", 3600, time.time() + 3600))
        await reconcile_schedules(client, s)
        await reconcile_schedules(client, Store(tmp_path))
        sh = client.get_schedule_handle("routine-" + rid)
        try:
            await sh.trigger()
            async with asyncio.timeout(20):
                while len(s.query("SELECT * FROM turns")) != 2:
                    await asyncio.sleep(.1)
            with s.connect() as db:
                db.execute("UPDATE routines SET enabled=0 WHERE id=?", (rid,))
            await reconcile_schedules(client, s)
            assert (await sh.describe()).schedule.state.paused
        finally:
            await sh.delete()

    try:
        asyncio.run(check())
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.kill()
            proc.wait()


def test_deepagents_per_call_crash_recovery(tmp_path, monkeypatch):
    queue = 'deep-test-' + str(uuid.uuid4())
    monkeypatch.setenv('AGENT_RUNTIME', 'deepagents')
    monkeypatch.setenv('TEMPORAL_TASK_QUEUE', queue)
    store = Store(tmp_path)
    ident = store.enqueue(store.thread('private')['id'], 'Save a recovery fixture')
    marker = tmp_path/'model-entered'
    env = {**os.environ, 'DATA_DIR': str(tmp_path), 'BRAIN_DIR':str(tmp_path/'brain'),
           'BRAIN_BACKEND':'files', 'MODEL_PROVIDER':'ollama', 'OLLAMA_MODEL':'offline-fixture',
           'OLLAMA_CONTEXT':'16384', 'WEB_SEARCH_PROVIDER':'disabled', 'EXECUTOR':'temporal',
           'TEMPORAL_TASK_QUEUE':queue, 'TEMPORAL_ADDRESS':os.environ['TEMPORAL_TEST_ADDRESS'], 'PYTHONPATH':str(Path.cwd())}
    processes=[]
    def worker(crash=False):
        process=subprocess.Popen([sys.executable,'tests/deep_worker_fixture.py'], env={**env, **({'CRASH_MARKER':str(marker)} if crash else {})}, stdout=subprocess.DEVNULL, stderr=open(tmp_path/'worker.log','a'))
        processes.append(process)
        return process
    async def check():
        client=await Client.connect(env['TEMPORAL_ADDRESS'])
        first=worker(True)
        await dispatch_once(client,store)
        async with asyncio.timeout(40):
            while not marker.exists():await asyncio.sleep(.1)
        first.kill();first.wait()
        worker()
        handle=client.get_workflow_handle(store.workflow_id(ident))
        assert await asyncio.wait_for(handle.result(),75)==ident
        assert store.query('SELECT status FROM turns')[0]['status']=='completed'
        assert len(store.query('SELECT * FROM artifacts'))==1
        history=await handle.fetch_history()
        started=[e.activity_task_scheduled_event_attributes.activity_type.name for e in history.events if e.HasField('activity_task_scheduled_event_attributes')]
        assert started.count('deepagents.invoke_tool')==1
        assert started.count('deepagents.invoke_model')==2
        assert 'portfolio.deep.checkpoint' in started

        # Explicit retry uses a new workflow generation and resumes a pending
        # tool call from the saved transcript, without adding the user twice.
        import json
        from langchain_core.messages import AIMessage, HumanMessage, messages_to_dict
        retry_id=store.enqueue(store.thread('private')['id'], 'Retry the fixture')
        pending=[HumanMessage(content='Retry the fixture'), AIMessage(content='',tool_calls=[dict(name='save_artifact',args=dict(title='Recovery fixture',markdown='Durable fixture output'),id='retry-call')])]
        store.artifact(store.query('SELECT thread_id FROM turns WHERE id=?',(retry_id,))[0]['thread_id'],retry_id,'Recovery fixture','prior write')
        with store.connect() as db:
            db.execute("UPDATE turns SET status='failed' WHERE id=?",(retry_id,))
            db.execute('INSERT INTO deep_transcripts VALUES (?,?)',(retry_id,json.dumps(messages_to_dict(pending))))
        assert store.retry(retry_id)
        await dispatch_once(client,store)
        retried=client.get_workflow_handle(store.workflow_id(retry_id))
        assert await asyncio.wait_for(retried.result(),30)==retry_id
        assert len(store.query('SELECT * FROM artifacts WHERE turn_id=?',(retry_id,)))==1
        retried_history=await retried.fetch_history()
        calls=[e.activity_task_scheduled_event_attributes.activity_type.name for e in retried_history.events if e.HasField('activity_task_scheduled_event_attributes')]
        assert calls.index('deepagents.invoke_tool') < calls.index('deepagents.invoke_model')
    try:asyncio.run(check())
    finally:
        for proc in processes:
            if proc.poll() is None:proc.kill()
            proc.wait()
