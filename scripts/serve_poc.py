"""Single-Machine PoC supervisor. Temporal and SQLite live on /data.

Temporal is a persistent development server here, by explicit PoC choice.
Only FastAPI is exposed; Temporal's API and UI bind to loopback.
"""
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness.paths import data_directory


def wait_port(port, process, seconds=45):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Temporal failed to start.')
        with socket.socket() as connection:
            if connection.connect_ex(('127.0.0.1', port)) == 0:
                return
        time.sleep(.2)
    raise RuntimeError('Temporal startup timed out.')


def main():
    os.umask(0o077)
    root = data_directory()
    root.mkdir(parents=True, exist_ok=True)
    children = []
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        temporal = subprocess.Popen([
            'temporal', 'server', 'start-dev', '--ip', '127.0.0.1',
            '--db-filename', str(root/'temporal.sqlite'), '--log-level', 'error',
        ])
        children.append(temporal)
        wait_port(7233, temporal)
        children.append(subprocess.Popen([sys.executable, '-m', 'harness.temporal_worker']))
        children.append(subprocess.Popen([sys.executable, '-m', 'uvicorn', 'harness.app:app',
            '--host', '0.0.0.0', '--port', '8080', '--no-access-log']))
        print('Public PoC ready: web, worker and loopback Temporal.', flush=True)
        while not stopping:
            if any(child.poll() is not None for child in children):
                raise RuntimeError('A service exited; restarting the Machine is required.')
            time.sleep(.5)
    finally:
        # Stop web and worker before the Temporal service. SIGINT gives asyncio
        # worker cleanup a chance to run rather than abruptly ending Activities.
        for child in reversed(children):
            if child.poll() is None:
                child.send_signal(signal.SIGINT)
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    main()
