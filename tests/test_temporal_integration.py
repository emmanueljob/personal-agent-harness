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
from test_retrieval_latency import corpus

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


def test_company_reads_then_async_browser_status_through_tool_activity(tmp_path, monkeypatch):
    import json
    from harness import crm

    queue = 'async-dispatch-' + str(uuid.uuid4())
    monkeypatch.setenv('AGENT_RUNTIME', 'deepagents')
    monkeypatch.setenv('TEMPORAL_TASK_QUEUE', queue)
    store = Store(tmp_path)
    crm.initialize(store)
    with store.connect() as db:
        crm.write_record(db, {'kind': 'company', 'name': 'Synthetic Company'}, 'synthetic-company', 0)
    from harness import correspondence
    correspondence.ingest(store, correspondence.Message(
        provider='linkedin', account='urn:li:fsd_profile:SyntheticOwner',
        external_id='synthetic-message', thread_id='synthetic-thread',
        direction='outbound', sender=correspondence.Address(name='Synthetic Owner'),
        recipients=[correspondence.Address(name='Synthetic Recipient')],
        subject='Synthetic Company outreach', text='Fictional company lookup fixture.'))
    ident = store.enqueue(store.thread('private')['id'], 'Read the synthetic company and check browser status')
    env = {**os.environ, 'DATA_DIR': str(tmp_path), 'BRAIN_DIR': str(tmp_path/'brain'),
           'BRAIN_BACKEND': 'files', 'MODEL_PROVIDER': 'ollama', 'OLLAMA_MODEL': 'offline-fixture',
           'OLLAMA_CONTEXT': '16384', 'WEB_SEARCH_PROVIDER': 'disabled', 'EXECUTOR': 'temporal',
           'TEMPORAL_TASK_QUEUE': queue, 'TEMPORAL_ADDRESS': os.environ['TEMPORAL_TEST_ADDRESS'],
           'TOOL_SEQUENCE_FIXTURE': '1', 'PYTHONPATH': str(Path.cwd())}
    async def check():
        client = await Client.connect(env['TEMPORAL_ADDRESS'])
        await dispatch_once(client, store)
        handle = client.get_workflow_handle(store.workflow_id(ident))
        assert await asyncio.wait_for(handle.result(), 60) == ident
        turn = store.query('SELECT status,result FROM turns WHERE id=?', (ident,))[0]
        assert turn == {'status': 'completed', 'result': 'Synthetic company lookup completed'}
        transcript = json.loads(store.query('SELECT body FROM deep_transcripts WHERE turn_id=?', (ident,))[0]['body'])
        results = [m['data']['name'] for m in transcript if m['type'] == 'tool']
        assert results == ['read_job_messages', 'read_job_workspace', 'cloud_browser_status']
        history = await handle.fetch_history()
        activities = [e.activity_task_scheduled_event_attributes.activity_type.name
                      for e in history.events if e.HasField('activity_task_scheduled_event_attributes')]
        assert activities.count('deepagents.invoke_tool') == 3
        assert activities.count('deepagents.invoke_model') == 4
        assert not any(e.HasField('activity_task_failed_event_attributes') for e in history.events)
    with open(tmp_path/'worker.log', 'w') as log:
        process = subprocess.Popen([sys.executable, 'tests/deep_worker_fixture.py'], env=env, stdout=log, stderr=log)
        try:
            asyncio.run(check())
        finally:
            process.kill()
            process.wait()


def test_public_chat_completes_under_home_polling(tmp_path, monkeypatch, corpus):
    from fastapi.testclient import TestClient
    from test_admin import admin_app, AUTH
    from test_crm import add, company
    from harness import command_actions

    module, store = admin_app(tmp_path, monkeypatch)
    import shutil
    shutil.copytree(corpus[0].root, store.brain.root, dirs_exist_ok=True)
    queue = 'public-latency-' + str(uuid.uuid4())
    monkeypatch.setenv('AGENT_RUNTIME', 'deepagents')
    monkeypatch.setenv('EXECUTOR', 'temporal')
    monkeypatch.setenv('TEMPORAL_TASK_QUEUE', queue)
    monkeypatch.setenv('MODEL_PROVIDER', 'ollama')
    monkeypatch.setenv('OLLAMA_MODEL', 'offline-fixture')
    async def idle(_):
        await asyncio.sleep(3600)
    monkeypatch.setattr(module, 'temporal_dispatcher', idle)
    ident = add(store, company('Synthetic polling company', next_action='Review role'))
    command_actions.initialize(store)
    env = {**os.environ, 'DATA_DIR': str(store.root), 'BRAIN_DIR': str(store.brain.root),
           'BRAIN_BACKEND': 'files', 'OLLAMA_CONTEXT': '16384', 'WEB_SEARCH_PROVIDER': 'disabled',
           'TEMPORAL_ADDRESS': os.environ['TEMPORAL_TEST_ADDRESS'], 'PUBLIC_LATENCY_FIXTURE': '1',
           'PYTHONPATH': str(Path.cwd())}
    with open(tmp_path/'latency-worker.log', 'w') as log:
        process = subprocess.Popen([sys.executable, 'tests/deep_worker_fixture.py'], env=env, stdout=log, stderr=log)
        try:
            with TestClient(module.app, base_url='https://testserver') as visitor:
                thread = visitor.post('/api/threads', json={'mode': 'public'})
                assert thread.status_code == 200
                thread_id = thread.json()['id']
                for prompt in ('Hi', 'What does the published orchard leadership evidence say?'):
                    started = time.monotonic()
                    accepted = visitor.post('/api/threads/'+thread_id+'/turns', json={'prompt': prompt})
                    assert accepted.status_code == 202
                    turn_id = accepted.json()['id']
                    async def start():
                        client = await Client.connect(env['TEMPORAL_ADDRESS'])
                        await dispatch_once(client, store)
                    asyncio.run(start())
                    polls = 0
                    while time.monotonic() - started < 12:
                        for path in ('/api/admin/command-center',
                                     '/api/admin/command-center/cards/'+ident+'/execution'):
                            before = time.monotonic()
                            assert visitor.get(path, headers=AUTH).status_code == 200
                            assert time.monotonic() - before < 1
                        response = visitor.get('/api/threads/'+thread_id)
                        assert response.status_code == 200
                        polls += 1
                        turn = next(t for t in response.json()['turns'] if t['id'] == turn_id)
                        if turn['status'] == 'completed':
                            break
                        time.sleep(.05)
                    assert turn['status'] == 'completed'
                    assert turn['result'] == 'Synthetic public answer'
                    import json
                    snapshot = json.loads(store.query(
                        'SELECT body FROM execution_context WHERE turn_id=?', (turn_id,))[0]['body'])
                    if prompt == 'Hi':
                        assert snapshot['knowledge'] == [] and snapshot['perspectives'] == []
                    else:
                        evidence = json.dumps(snapshot['knowledge'])
                        assert '2001-01-01' in evidence and '2026-01-01' in evidence
                    assert 'PRIVATE_CANARY' not in json.dumps(snapshot)
                    assert polls
                    async def history():
                        client = await Client.connect(env['TEMPORAL_ADDRESS'])
                        return await client.get_workflow_handle(store.workflow_id(turn_id)).fetch_history()
                    events = asyncio.run(history()).events
                    assert not any(e.HasField('activity_task_failed_event_attributes')
                                   or e.HasField('activity_task_timed_out_event_attributes') for e in events)
                    scheduled = {e.event_id: e for e in events if e.HasField('activity_task_scheduled_event_attributes')}
                    activity_starts = {}
                    stages = []
                    for event in events:
                        if event.HasField('activity_task_started_event_attributes'):
                            start = event.activity_task_started_event_attributes
                            assert start.attempt == 1
                            activity_starts[start.scheduled_event_id] = event
                        elif event.HasField('activity_task_completed_event_attributes'):
                            key = event.activity_task_completed_event_attributes.scheduled_event_id
                            queued = scheduled[key]
                            running = activity_starts[key]
                            stages.append({
                                'activity': queued.activity_task_scheduled_event_attributes.activity_type.name,
                                'queue_seconds': (running.event_time - queued.event_time).total_seconds(),
                                'run_seconds': (event.event_time - running.event_time).total_seconds(),
                            })
                    print(f'{prompt!r}: accepted-to-visible {time.monotonic()-started:.3f}s, {polls} polls, {stages}')
        finally:
            process.kill()
            process.wait()
