import asyncio
import fcntl
from .agent import execute


def run_turn(store, ident, terminal_on_error=True):
    # OS lock also serializes a timed-out activity whose Python thread has not
    # stopped yet. Worker death releases it automatically.
    with open(store.root / "execution.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _run_turn(store, ident, terminal_on_error)


def _run_turn(store, ident, terminal_on_error=True):
    turn = store.query("SELECT * FROM turns WHERE id=?", (ident,))[0]
    if turn["status"] == "completed":
        return turn["result"]
    with store.connect() as db:
        db.execute("UPDATE turns SET status='running',error=NULL WHERE id=?", (ident,))
    try:
        result = execute(store, turn)
    except Exception:
        # Avoid persisting provider exceptions that could contain sensitive request data.
        with store.connect() as db:
            db.execute("UPDATE turns SET status=?,error=? WHERE id=?", ("failed" if terminal_on_error else "running", "Execution failed. Check model configuration, then retry.", ident))
        if terminal_on_error:
            from .crm_imports import retry_failed_job
            retry_failed_job(store,ident)
        raise
    with store.connect() as db:
        db.execute("UPDATE turns SET status='completed',result=? WHERE id=?", (result, ident))
    return result


async def local_worker(store):
    # Single local worker only. Serial execution preserves conversation ordering.
    with store.connect() as db:
        db.execute("UPDATE turns SET status='queued' WHERE status='running'")
    while True:
        store.tick()
        from .helper_delegation import resume as resume_helpers
        await asyncio.to_thread(resume_helpers, store)
        from .crm_enrichment import resume as resume_enrichment
        resume_enrichment(store)
        turns = store.query("SELECT id FROM turns WHERE status='queued' ORDER BY created LIMIT 1")
        if turns:
            try:
                await asyncio.to_thread(run_turn, store, turns[0]["id"])
            except Exception:
                pass
        await asyncio.sleep(0.5)
