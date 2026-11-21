"""Run a restart benchmark on a temporary Temporal server with synthetic data."""

import argparse
import asyncio
import json
import os
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

from temporalio.client import Client, WorkflowFailureError

from experiments.restart_benchmark_worker import Journal, PAYLOAD, PROMPT, SOURCE, TOOL_TIMEOUT_SECONDS
from harness.durable import dispatch_once, reconcile_schedules
from harness.store import Store

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Scenario:
    name: str
    interrupt_at: str = ''
    decision: str = 'approve'
    deduplicate: bool = True


SCENARIOS = [
    Scenario('no_restart'),
    Scenario('before_approval', 'waiting_for_approval'),
    Scenario('after_approval', 'before_sink_write'),
    Scenario('after_write', 'after_sink_commit'),
    Scenario('declined_after_restart', 'waiting_for_approval', decision='decline'),
    Scenario('after_write_without_idempotency', 'after_sink_commit', deduplicate=False),
]


def stop(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


async def observe(get_value, process, label, timeout=30):
    try:
        async with asyncio.timeout(timeout):
            while process.poll() is None:
                if value := get_value():
                    return value
                await asyncio.sleep(0.05)
    except TimeoutError:
        raise RuntimeError(f'Timed out waiting for {label}.') from None
    raise RuntimeError(f'The subprocess exited before {label}.')


def tcp_port():
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        return reservation.getsockname()[1]


def reachable(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.15):
            return True
    except OSError:
        return False


def resolved(cli):
    location = shutil.which(cli)
    if not location:
        raise SystemExit('Pass --temporal-cli with the path to the Temporal CLI executable.')
    return str(Path(location).resolve())


def environment(root, address, queue):
    return {
        'PATH': os.defpath, 'AFTERHOURS_HOME': str(root), 'DATA_DIR': str(root),
        'BRAIN_DIR': str(root / 'brain'), 'BRAIN_BACKEND': 'files', 'EXECUTOR': 'temporal',
        'AGENT_RUNTIME': 'deepagents', 'MODEL_PROVIDER': 'ollama', 'OLLAMA_MODEL': 'synthetic-restart-fixture',
        'WEB_SEARCH_PROVIDER': 'disabled', 'TEMPORAL_ADDRESS': address, 'TEMPORAL_TASK_QUEUE': queue,
    }


def start_worker(env, journal, scenario, log, *, first):
    worker_env = {**env, 'RESTART_JOURNAL': str(journal.path),
                  'RESTART_STOP_AT': scenario.interrupt_at if first else '',
                  'RESTART_DEDUPLICATE': '1' if scenario.deduplicate else '0'}
    return subprocess.Popen([sys.executable, '-m', 'experiments.restart_benchmark_worker'],
                            cwd=ROOT, env=worker_env, stdout=log, stderr=log)


def activity_trace(history):
    activities = {}
    for event in history.events:
        if event.HasField('activity_task_scheduled_event_attributes'):
            attrs = event.activity_task_scheduled_event_attributes
            activities[event.event_id] = {'activity': attrs.activity_type.name,
                                          'scheduled_event_id': event.event_id, 'attempt': None,
                                          'completed_event_id': None}
        if event.HasField('activity_task_started_event_attributes'):
            attrs = event.activity_task_started_event_attributes
            activities[attrs.scheduled_event_id]['attempt'] = attrs.attempt
        if event.HasField('activity_task_completed_event_attributes'):
            attrs = event.activity_task_completed_event_attributes
            activities[attrs.scheduled_event_id]['completed_event_id'] = event.event_id
    return list(activities.values())


async def trigger_routine(client, store, worker):
    thread_id = store.thread('private')['id']
    routine_id = uuid.uuid4().hex
    with store.connect() as db:
        db.execute('INSERT INTO routines VALUES (?, ?, ?, ?, ?, 1)',
                   (routine_id, thread_id, PROMPT, 86400, time.time() + 86400))
    await reconcile_schedules(client, store)
    await client.get_schedule_handle('routine-' + routine_id).trigger()
    turns = await observe(lambda: store.query('SELECT id FROM turns WHERE thread_id=?', (thread_id,)),
                          worker, 'the scheduled turn')
    await dispatch_once(client, store)
    ident = turns[0]['id']
    return ident, client.get_workflow_handle(store.workflow_id(ident))


async def run_case(client, address, root, scenario, trial):
    env = environment(root, address, 'restart-bench-' + uuid.uuid4().hex)
    os.environ.update(env)
    store = Store(root)
    store.set_profile('private', SOURCE)
    journal = Journal(root / 'mock-service.sqlite')
    journal.initialize()

    def events_at(stage):
        return [event for event in journal.evidence()['events'] if event['stage'] == stage]

    with (root / 'worker.log').open('wb') as log:
        worker = start_worker(env, journal, scenario, log, first=True)
        try:
            started = time.monotonic()
            turn_id, handle = await trigger_routine(client, store, worker)
            await observe(lambda: events_at('waiting_for_approval'), worker, 'the approval gate')
            decided_before_crash = scenario.interrupt_at != 'waiting_for_approval'
            if decided_before_crash:
                journal.review(turn_id, scenario.decision)
            before_restart = None
            restart_at = None
            if scenario.interrupt_at:
                await observe(lambda: events_at(scenario.interrupt_at), worker, scenario.interrupt_at)
                before_restart = {
                    'decision_present': journal.decision(turn_id) is not None,
                    'effective_writes': len(journal.evidence()['effects']),
                }
                if worker.poll() is not None:
                    raise RuntimeError('The worker exited before the planned interruption.')
                restart_at = time.monotonic()
                stop(worker)
                journal.record('worker_killed')
                if not decided_before_crash:
                    journal.review(turn_id, scenario.decision)
                worker = start_worker(env, journal, scenario, log, first=False)
                journal.record('worker_restarted')
            completion_error = None
            try:
                result = await asyncio.wait_for(handle.result(), timeout=45)
            except (TimeoutError, WorkflowFailureError) as error:
                completion_error = type(error).__name__
                result = None
            finished = time.monotonic()
            journal.record('workflow_observed_complete' if result == turn_id else 'workflow_completion_failed')
        finally:
            stop(worker)

    evidence = journal.evidence()
    events, effects = evidence['events'], evidence['effects']
    writes = [event for event in events if event['stage'] == 'sink_write_attempt']
    approvals = [event for event in events if event['stage'] == 'decision_recorded']
    waits = [event['attempt'] for event in events if event['stage'] == 'waiting_for_approval']
    sink_attempts = [event['attempt'] for event in events if event['stage'] == 'before_sink_write']
    expected_writes = 0 if scenario.decision == 'decline' else (1 if scenario.deduplicate else 2)
    expected_attempts = 0 if scenario.decision == 'decline' else (2 if scenario.interrupt_at == 'after_sink_commit' else 1)
    expected_gate_attempts = [1, 2] if scenario.interrupt_at == 'waiting_for_approval' else [1]
    expected_sink_attempts = ([1, 2] if scenario.interrupt_at in ('before_sink_write', 'after_sink_commit') else [1])
    turn = store.query('SELECT status, result FROM turns WHERE id=?', (turn_id,))[0]
    answer = turn['result'] or ''
    checks = {
        'scheduled_exactly_one_turn': len(store.query('SELECT id FROM turns')) == 1,
        'workflow_completed': result == turn_id and turn['status'] == 'completed',
        'decision_recorded_once': len(approvals) == 1 and approvals[0]['detail'] == scenario.decision,
        'no_write_before_approval': bool(approvals) and all(event['at'] >= approvals[0]['at'] for event in writes),
        'approval_activity_attempts': waits == expected_gate_attempts,
        'sink_activity_attempts': sink_attempts == ([] if scenario.decision == 'decline' else expected_sink_attempts),
        'sink_write_attempt_count': len(writes) == expected_attempts,
        'effective_write_count': len(effects) == expected_writes,
        'payload_preserved': all(row['payload'] == PAYLOAD for row in effects),
        'final_answer_matches_decision_and_receipt': answer == (
            'Declined. No digest was recorded.' if scenario.decision == 'decline'
            else (f'Recorded {PAYLOAD} Receipt {writes[-1]["detail"]}.' if writes else '')),
    }
    if before_restart is not None:
        checks['interrupted_at_planned_boundary'] = before_restart == {
            'decision_present': decided_before_crash,
            'effective_writes': int(scenario.interrupt_at == 'after_sink_commit'),
        }
    history = await asyncio.wait_for(handle.fetch_history(), timeout=10)
    activities = activity_trace(history)
    tools = [entry for entry in activities if entry['activity'] == 'deepagents.invoke_tool']
    expected_completed_tools = expected_gate_attempts[-1:]
    if scenario.decision == 'approve':
        expected_completed_tools += expected_sink_attempts[-1:]
    checks['temporal_recorded_tool_results'] = [
        entry['attempt'] for entry in tools if entry['completed_event_id'] is not None
    ] == expected_completed_tools
    return {
        'scenario': scenario.name, 'trial': trial, 'interruption': scenario.interrupt_at or None,
        'decision': scenario.decision, 'idempotency_enabled': scenario.deduplicate,
        'passed': all(checks.values()), 'checks': checks, 'answer': answer,
        'before_restart': before_restart, 'completion_error': completion_error,
        'total_seconds': round(finished - started, 3),
        'seconds_from_kill_to_completion': round(finished - restart_at, 3) if restart_at is not None else None,
        'effective_writes': len(effects), 'sink_write_attempts': len(writes),
        'temporal_event_count': len(history.events), 'temporal_activities': activities, **evidence,
    }


async def benchmark(cli, trials, scenarios):
    port = tcp_port()
    address = f'127.0.0.1:{port}'
    results = []
    with tempfile.TemporaryDirectory(prefix='personal-harness-restart-') as work:
        root = Path(work)
        with (root / 'temporal.log').open('wb') as log:
            server = subprocess.Popen([cli, 'server', 'start-dev', '--headless', '--ip', '127.0.0.1',
                                       '--port', str(port)], cwd=root, env={'PATH': os.defpath}, stdout=log, stderr=log)
            try:
                await observe(lambda: reachable(port), server, 'the isolated Temporal server')
                client = await asyncio.wait_for(Client.connect(address), timeout=10)
                for trial in range(1, trials + 1):
                    for scenario in scenarios:
                        case_dir = root / f'{scenario.name}-{trial}'
                        result = await run_case(client, address, case_dir, scenario, trial)
                        results.append(result)
                        state = 'pass' if result['passed'] else 'FAIL'
                        print(f'{scenario.name} [{trial}] {state}, effective writes {result["effective_writes"]}',
                              file=sys.stderr, flush=True)
            finally:
                stop(server)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--temporal-cli', default='temporal')
    parser.add_argument('--trials', type=int, default=1)
    parser.add_argument('--case', action='append', choices=[scenario.name for scenario in SCENARIOS])
    parser.add_argument('--output', type=Path, help='Write the JSON report to a new file instead of standard output.')
    args = parser.parse_args()
    if args.trials < 1:
        parser.error('--trials must be positive')
    if args.output and (args.output.exists() or not args.output.parent.is_dir()):
        parser.error('--output requires a new file in an existing directory')
    cli = resolved(args.temporal_cli)
    scenarios = [scenario for scenario in SCENARIOS if not args.case or scenario.name in args.case]
    report = {
        'schema_version': 1, 'run_at_utc': datetime.now(timezone.utc).isoformat(),
        'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'source_has_uncommitted_changes': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT)),
        'platform': platform.platform(), 'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version,
        'temporal_python_sdk': version('temporalio'),
        'temporal_cli': subprocess.check_output([cli, '--version'], text=True).strip(),
        'tool_start_to_close_seconds': TOOL_TIMEOUT_SECONDS,
        'results': asyncio.run(benchmark(cli, args.trials, scenarios)),
    }
    output = json.dumps(report, indent=2) + '\n'
    if args.output:
        with args.output.open('x') as file:
            file.write(output)
    else:
        print(output, end='')
    return 0 if all(result['passed'] for result in report['results']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
