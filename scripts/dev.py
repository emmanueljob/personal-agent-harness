"""Start local dependencies and the harness. Ctrl-C stops processes started here."""
import fcntl
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

root = Path(__file__).resolve().parents[1]
os.chdir(root)
load_dotenv()
sys.path.insert(0, str(root))
from harness.paths import data_directory
state_dir = data_directory()
(state_dir / 'logs').mkdir(parents=True, exist_ok=True)
lock = open(state_dir / 'dev.lock', 'a')
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit('Local stack is already running.')
children = []
logs = []


def alive(port):
    with socket.socket() as sock:
        return sock.connect_ex(('127.0.0.1', port)) == 0


def start(name, args, port=None, env=None):
    if port and alive(port):
        print(f'{name}: using existing service on {port}', flush=True)
        return
    log = open(state_dir / 'logs' / (name + '.log'), 'a')
    logs.append(log)
    p = subprocess.Popen(args, env={**os.environ, **(env or {})}, stdout=log, stderr=log)
    children.append((name, p))
    if port:
        until = time.monotonic() + 45
        while not alive(port):
            if p.poll() is not None or time.monotonic() > until:
                raise RuntimeError(f'{name} failed to start; see the application state logs.')
            time.sleep(.2)
    print(f'{name}: started', flush=True)


def stop(*_):
    raise KeyboardInterrupt


signal.signal(signal.SIGTERM, stop)
try:
    if os.getenv('MODEL_PROVIDER') == 'ollama':
        start('ollama', [str(root / '.runtime/tools/ollama/ollama'), 'serve'], 11434,
              {'OLLAMA_MODELS': str(root / '.runtime/models'), 'OLLAMA_HOST':'127.0.0.1:11434', 'OLLAMA_NO_CLOUD':'1'})
    if os.getenv('EXECUTOR') == 'temporal':
        start('temporal', [str(root / '.runtime/tools/temporal/temporal'), 'server', 'start-dev', '--ip', '127.0.0.1', '--db-filename', str(state_dir / 'temporal.sqlite'), '--log-level', 'error'], 7233)
        start('worker', [sys.executable, '-m', 'harness.temporal_worker'])
    start('web', [sys.executable, '-m', 'uvicorn', 'harness.app:app', '--host', '127.0.0.1', '--port', '8000'], 8000)
    print('Chat: http://localhost:8000 | Temporal: http://localhost:8233', flush=True)
    while True:
        for name, p in children:
            if p.poll() is not None:
                raise RuntimeError(f'{name} exited; see the application state logs.')
        time.sleep(1)
except KeyboardInterrupt:
    pass
finally:
    for _, p in reversed(children):
        p.terminate()
    for _, p in reversed(children):
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    for log in logs:
        log.close()
