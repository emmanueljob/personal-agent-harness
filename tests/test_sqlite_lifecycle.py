"""Bound database resources even when cyclic garbage collection is delayed."""
import subprocess
import sys
import sqlite3

import pytest
from harness.store import Store
from harness.gmail_connection import Vault


def test_context_closes_files_and_preserves_commit_and_rollback(tmp_path):
    store=Store(tmp_path)
    with store.connect() as db:db.execute("INSERT INTO threads VALUES ('committed','private')")
    with pytest.raises(sqlite3.ProgrammingError):db.execute('SELECT 1')
    with pytest.raises(ValueError):
        with store.connect() as db:
            db.execute("INSERT INTO threads VALUES ('rolled-back','private')")
            raise ValueError('synthetic interruption')
    assert [r['id'] for r in store.query('SELECT id FROM threads')]==['committed']
    with Vault(store).connect() as vault:vault.execute('SELECT 1')
    with pytest.raises(sqlite3.ProgrammingError):vault.execute('SELECT 1')


def test_repeated_private_reads_under_background_process_file_limit(tmp_path):
    code='''
import gc,resource,sys
from harness.store import Store
from harness.gmail_connection import Vault
soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE)
resource.setrlimit(resource.RLIMIT_NOFILE,(min(256,soft),hard))
gc.disable()
store=Store(sys.argv[1]);vault=Vault(store)
for i in range(300):
    assert store.query('SELECT 1 AS value')[0]['value']==1
    vault.put('token','synthetic',{'value':i})
    assert vault.get('token','synthetic')['value']==i
'''
    result=subprocess.run([sys.executable,'-c',code,str(tmp_path)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
