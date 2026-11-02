"""Subprocess fixture for real Temporal worker-crash tests; never loaded by the app."""
import os
import time
from pathlib import Path
from harness import runner
from harness.temporal_worker import main
import asyncio


def fake_execute(store, turn):
    store.artifact(turn["thread_id"], turn["id"], "Recovery test", "saved once")
    if os.getenv("CRASH_MARKER"):
        Path(os.environ["CRASH_MARKER"]).write_text("activity entered")
        time.sleep(3600)
    return "Recovered durable turn"


runner.execute = fake_execute
asyncio.run(main())
