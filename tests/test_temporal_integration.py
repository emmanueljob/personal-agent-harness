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
