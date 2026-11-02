"""Temporal owns retries/timers; LangGraph owns agent step checkpoints."""
import asyncio
import contextlib
import os
from datetime import timedelta
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import Worker


@activity.defn
async def execute_turn(ident: str) -> str:
    from .runner import run_turn
    from .store import Store

    async def heartbeat():
        while True:
            activity.heartbeat(ident)
            await asyncio.sleep(2)

    beat = asyncio.create_task(heartbeat())
    try:
        await asyncio.to_thread(run_turn, Store(), ident, False)
        return ident
    except Exception:
        from temporalio.exceptions import ApplicationError
        raise ApplicationError("Agent execution failed; retrying from checkpoint.", type="AgentExecutionError") from None
    finally:
        beat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beat


@activity.defn
async def enqueue_routine(data: dict) -> str:
    import uuid
    from .store import Store
    store = Store()
    import time
    ident = str(uuid.uuid5(uuid.NAMESPACE_URL, data["execution_id"]))
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        r = db.execute("SELECT * FROM routines WHERE id=? AND enabled=1", (data["routine_id"],)).fetchone()
        if not r:
            return "disabled"
        db.execute("INSERT OR IGNORE INTO turns VALUES (?,?,?,'queued',NULL,NULL,?)", (ident, r["thread_id"], r["prompt"], time.time()))
    return ident


@workflow.defn
class AgentTurn:
    @workflow.run
    async def run(self, ident: str) -> str:
        await workflow.execute_activity(
            "execute_turn", ident, start_to_close_timeout=timedelta(minutes=15),
            heartbeat_timeout=timedelta(seconds=20),
            retry_policy=RetryPolicy(initial_interval=timedelta(seconds=2), maximum_attempts=3),
        )
        return ident


@workflow.defn
class RoutineTick:
    @workflow.run
    async def run(self, routine_id: str) -> str:
        return await workflow.execute_activity(
            "enqueue_routine", {"routine_id": routine_id, "execution_id": workflow.info().workflow_id},
            start_to_close_timeout=timedelta(seconds=30),
        )


async def main():
    from dotenv import load_dotenv
    load_dotenv()
    client = await Client.connect(os.getenv("TEMPORAL_ADDRESS", "localhost:7233"))
    worker = Worker(
        client, task_queue=os.getenv("TEMPORAL_TASK_QUEUE", "personal-agent"), workflows=[AgentTurn, RoutineTick],
        activities=[execute_turn, enqueue_routine], max_concurrent_activities=1,
        max_heartbeat_throttle_interval=timedelta(seconds=2),
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
