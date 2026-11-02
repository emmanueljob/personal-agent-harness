"""SQLite queue is an outbox; deterministic workflow IDs close the delivery gap."""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from temporalio.client import (
    Client, WorkflowExecutionStatus, Schedule, ScheduleSpec, ScheduleIntervalSpec,
    ScheduleActionStartWorkflow, ScheduleState, SchedulePolicy, ScheduleOverlapPolicy,
    ScheduleAlreadyRunningError,
)
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

logger = logging.getLogger(__name__)


async def reconcile_schedules(client, store):
    for r in store.query("SELECT * FROM routines"):
        sid = "routine-" + r["id"]
        try:
            await client.create_schedule(sid, Schedule(
                action=ScheduleActionStartWorkflow("RoutineTick", r["id"], id=sid, task_queue=os.getenv("TEMPORAL_TASK_QUEUE", "personal-agent")),
                spec=ScheduleSpec(
                    intervals=[ScheduleIntervalSpec(every=timedelta(seconds=r["interval_seconds"]))],
                    start_at=datetime.fromtimestamp(r["next_run"], timezone.utc),
                ),
                policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP, catchup_window=timedelta(minutes=5)),
                state=ScheduleState(paused=not bool(r["enabled"])),
            ))
        except ScheduleAlreadyRunningError:
            handle = client.get_schedule_handle(sid)
            desc = await handle.describe()
            if desc.schedule.state.paused == bool(r["enabled"]):
                if r["enabled"]:
                    await handle.unpause()
                else:
                    await handle.pause()


async def dispatch_once(client, store):
    # Serial execution for this single-owner SQLite deployment prevents later
    # turns overtaking an interrupted turn while Temporal retries it.
    turns = store.query("SELECT * FROM turns WHERE status IN ('queued','running') ORDER BY created,rowid LIMIT 1")
    if not turns:
        return
    turn = turns[0]
    wid = store.workflow_id(turn["id"])
    try:
        handle = await client.start_workflow(
            "AgentTurn", turn["id"], id=wid, task_queue=os.getenv("TEMPORAL_TASK_QUEUE", "personal-agent"),
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
    except WorkflowAlreadyStartedError:
        handle = client.get_workflow_handle(wid)
    status = (await handle.describe()).status
    if status in (WorkflowExecutionStatus.FAILED, WorkflowExecutionStatus.TIMED_OUT,
                  WorkflowExecutionStatus.CANCELED, WorkflowExecutionStatus.TERMINATED):
        with store.connect() as db:
            db.execute("UPDATE turns SET status='failed',error=? WHERE id=? AND status!='completed'",
                       ("Durable execution failed after retries. Retry to resume checkpoints.", turn["id"]))


async def temporal_dispatcher(store):
    client = None
    ticks = 0
    while True:
        try:
            if client is None:
                client = await Client.connect(os.getenv("TEMPORAL_ADDRESS", "localhost:7233"))
            if ticks % 10 == 0:
                await reconcile_schedules(client, store)
            await dispatch_once(client, store)
            ticks += 1
        except Exception:
            logger.warning("Temporal dispatch unavailable; persisted work remains queued.")
            client = None
        await asyncio.sleep(1)
