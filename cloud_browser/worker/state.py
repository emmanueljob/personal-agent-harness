"""Persistent single-worker queue/outbox. Nothing in this directory belongs in Git."""
import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

class State:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        os.chmod(self.root,0o700)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,kind TEXT NOT NULL,body TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'queued',attempt INTEGER NOT NULL DEFAULT 0,due REAL NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY,body TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'queued',attempt INTEGER NOT NULL DEFAULT 0,due REAL NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '');
            CREATE INDEX IF NOT EXISTS jobs_due ON jobs(state,due);
            CREATE INDEX IF NOT EXISTS outbox_due ON outbox(state,due);
            ''')
            # This process owns the volume lock before State is instantiated.
            for table in ('jobs','outbox'):db.execute(f"UPDATE {table} SET state='queued' WHERE state='working'")

    @contextmanager
    def db(self):
        path=self.root/'capture.sqlite';fd=os.open(path,os.O_CREAT|os.O_RDWR,0o600);os.close(fd)
        db=sqlite3.connect(path,timeout=20);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def get(self,key,default=None):
        with self.db() as db:row=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        return json.loads(row['value']) if row else default

    def put(self,key,value):
        with self.db() as db:db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,json.dumps(value)))

    def enqueue(self,kind,body,key):
        with self.db() as db:
            db.execute("INSERT INTO jobs(id,kind,body) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,state=CASE WHEN jobs.state='working' THEN 'working' ELSE 'queued' END,due=0",(key,kind,json.dumps(body)))

    def take(self,table):
        assert table in ('jobs','outbox')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute(f"SELECT * FROM {table} WHERE state IN ('queued','retry') AND due<=? ORDER BY due,id LIMIT 1",(time.time(),)).fetchone()
            if row:db.execute(f"UPDATE {table} SET state='working' WHERE id=?",(row['id'],))
        return dict(row) if row else None

    def finish(self,table,ident):
        assert table in ('jobs','outbox')
        with self.db() as db:db.execute(f"UPDATE {table} SET state='done',error='' WHERE id=?",(ident,))

    def retry(self,table,row,reason):
        assert table in ('jobs','outbox')
        n=row['attempt']+1
        with self.db() as db:db.execute(f"UPDATE {table} SET state='retry',attempt=?,due=?,error=? WHERE id=?",(n,time.time()+min(900,2**min(n,10)),reason,row['id']))

    def save_messages(self,messages):
        with self.db() as db:
            for message in messages:
                body=json.dumps(message,sort_keys=True);ident=hashlib.sha256(body.encode()).hexdigest()
                db.execute('INSERT OR IGNORE INTO outbox(id,body) VALUES (?,?)',(ident,body))

    def counts(self,table):
        assert table in ('jobs','outbox')
        with self.db() as db:return {r['state']:r['n'] for r in db.execute(f'SELECT state,count(*) n FROM {table} GROUP BY state')}
