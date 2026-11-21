"""Synthetic model, reviewer and external service used by the restart benchmark."""

import asyncio
import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from temporalio import activity
from temporalio.client import Client
from temporalio.deepagents import tool_as_activity
from temporalio.deepagents.testing import FakeModel
from temporalio.worker import Worker

from harness import deep_runtime
from harness.agent import PluginRegistry
from harness.temporal_worker import RoutineTick, enqueue_routine

PROMPT = 'Use the synthetic research note to prepare a digest and request approval before recording it.'
SOURCE = 'The fixture library added Saturday hours from 10 to 2.'
PAYLOAD = 'Saturday library hours are 10 to 2.'
PAYLOAD_SHA = hashlib.sha256(PAYLOAD.encode()).hexdigest()
TOOL_TIMEOUT_SECONDS = 10


class Journal:
    """A separate SQLite database models the human inbox and downstream receipts."""

    def __init__(self, path):
        self.path = path

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialize(self):
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE decisions(turn_id TEXT PRIMARY KEY, decision TEXT, payload_sha TEXT);
                CREATE TABLE effects(id INTEGER PRIMARY KEY, dedup_key TEXT UNIQUE, payload TEXT);
                CREATE TABLE events(id INTEGER PRIMARY KEY, stage TEXT, attempt INTEGER, detail TEXT, at REAL);
            ''')

    def record(self, stage, *, attempt=None, detail=None):
        with self.connect() as db:
            db.execute('INSERT INTO events(stage, attempt, detail, at) VALUES (?, ?, ?, ?)',
                       (stage, attempt, detail, time.time()))

    def decision(self, turn_id):
        with self.connect() as db:
            return db.execute('SELECT decision, payload_sha FROM decisions WHERE turn_id=?', (turn_id,)).fetchone()

    def review(self, turn_id, decision):
        with self.connect() as db:
            db.execute('INSERT INTO decisions VALUES (?, ?, ?)', (turn_id, decision, PAYLOAD_SHA))
            db.execute('INSERT INTO events(stage, detail, at) VALUES (?, ?, ?)',
                       ('decision_recorded', decision, time.time()))

    def write(self, turn_id, deduplicate):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            decision = db.execute('SELECT decision, payload_sha FROM decisions WHERE turn_id=?', (turn_id,)).fetchone()
            if decision is None or tuple(decision) != ('approve', PAYLOAD_SHA):
                raise ValueError('The sink requires approval for this exact payload.')
            key = f'{turn_id}:{PAYLOAD_SHA}' if deduplicate else None
            row = db.execute('INSERT INTO effects(dedup_key, payload) VALUES (?, ?) '
                             'ON CONFLICT(dedup_key) DO NOTHING RETURNING id', (key, PAYLOAD)).fetchone()
            if row is None:
                row = db.execute('SELECT id FROM effects WHERE dedup_key=?', (key,)).fetchone()
            receipt = f'receipt-{row[0]}'
            db.execute('INSERT INTO events(stage, attempt, detail, at) VALUES (?, ?, ?, ?)',
                       ('sink_write_attempt', activity.info().attempt, receipt, time.time()))
            return receipt

    def evidence(self):
        with self.connect() as db:
            return {
                'events': [dict(row) for row in db.execute('SELECT stage, attempt, detail, at FROM events ORDER BY id')],
                'effects': [dict(row) for row in db.execute('SELECT id, payload FROM effects ORDER BY id')],
            }


def fixture_tools(context):
    journal = Journal(Path(os.environ['RESTART_JOURNAL']))
    stop_at = os.environ.get('RESTART_STOP_AT', '')
    deduplicate = os.environ['RESTART_DEDUPLICATE'] == '1'
    turn_id = context['turn_id']

    async def checkpoint(stage):
        journal.record(stage, attempt=activity.info().attempt)
        if stage == stop_at:
            await asyncio.Event().wait()

    @tool
    async def wait_for_fixture_decision() -> str:
        """Wait for the reviewer to approve or decline the exact synthetic digest."""
        await checkpoint('waiting_for_approval')
        while (decision := journal.decision(turn_id)) is None:
            await asyncio.sleep(0.05)
        if decision['payload_sha'] != PAYLOAD_SHA:
            raise ValueError('Approval does not match the pending digest.')
        return decision['decision']

    @tool
    async def write_fixture_digest() -> str:
        """Record the approved synthetic digest in the mock service and return its receipt."""
        await checkpoint('before_sink_write')
        receipt = journal.write(turn_id, deduplicate)
        await checkpoint('after_sink_commit')
        return receipt

    return [wait_for_fixture_decision, write_fixture_digest]


class FixtureModel(FakeModel):
    async def _agenerate(self, messages, **kwargs):
        if not any(isinstance(message, HumanMessage) and message.content == PROMPT for message in messages):
            raise ValueError('Scheduled context was lost.')
        if not any(SOURCE in str(message.content) for message in messages):
            raise ValueError('Synthetic source context was lost.')
        results = {message.name: message.content for message in messages if isinstance(message, ToolMessage)}
        decision = results.get('wait_for_fixture_decision')
        receipt = results.get('write_fixture_digest')
        if decision is None:
            message = AIMessage(content='', tool_calls=[dict(name='wait_for_fixture_decision', args={}, id='review')])
        elif decision == 'decline':
            message = AIMessage(content='Declined. No digest was recorded.')
        elif decision == 'approve' and receipt is None:
            message = AIMessage(content='', tool_calls=[dict(name='write_fixture_digest', args={}, id='write')])
        elif decision == 'approve' and isinstance(receipt, str) and receipt.startswith('receipt-'):
            message = AIMessage(content=f'Recorded {PAYLOAD} Receipt {receipt}.')
        else:
            raise ValueError('Unexpected fixture decision or tool result.')
        return ChatResult(generations=[ChatGeneration(message=message)])


def fixture_model(_name):
    # Exercise the production lookup of workflow ID, turn ID and visibility.
    deep_runtime.binding()
    return FixtureModel(responses=['unused'])


def benchmark_activity(tool_proxy, *, start_to_close_timeout, activity_options):
    return tool_as_activity(tool_proxy, start_to_close_timeout=timedelta(seconds=TOOL_TIMEOUT_SECONDS),
                            activity_options=activity_options)


async def worker():
    deep_runtime.plugins = PluginRegistry()
    deep_runtime.plugins.register('restart_benchmark', fixture_tools)
    deep_runtime.provide_model = fixture_model
    deep_runtime.tool_as_activity = benchmark_activity
    address, queue = os.environ['TEMPORAL_ADDRESS'], os.environ['TEMPORAL_TASK_QUEUE']
    client = await Client.connect(address)
    schedules = Worker(client, task_queue=queue, workflows=[RoutineTick], activities=[enqueue_routine])
    turns = await deep_runtime.make_worker(address, queue + '-deepagents-v1')
    await asyncio.gather(schedules.run(), turns.run())


if __name__ == '__main__':
    asyncio.run(worker())
