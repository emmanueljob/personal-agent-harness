import os
import sqlite3
import time
import uuid
from pathlib import Path
from .paths import data_directory, brain_directory
from .brain import FileBrain
from .models import agent_runtime


class ClosingConnection(sqlite3.Connection):
    """Commit/roll back normally, then release SQLite files at context exit."""
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class Store:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else data_directory()
        self.brain = FileBrain(self.root / "brain" if root is not None or (os.getenv("DATA_DIR") and not os.getenv("BRAIN_DIR")) else brain_directory())
        if os.getenv("BRAIN_BACKEND", "files") == "gbrain":
            from .gbrain import GBrain
            self.brain = GBrain(self.brain.root)
        elif os.getenv("BRAIN_BACKEND", "files") != "files":
            raise ValueError("BRAIN_BACKEND must be files or gbrain")
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS threads(id TEXT PRIMARY KEY, mode TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS visitor_threads(thread_id TEXT PRIMARY KEY, visitor_hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS public_usage(bucket TEXT NOT NULL, window INTEGER NOT NULL, count INTEGER NOT NULL, PRIMARY KEY(bucket,window));
            CREATE TABLE IF NOT EXISTS turns(id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
              prompt TEXT NOT NULL, status TEXT NOT NULL, result TEXT, error TEXT, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
              turn_id TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
              UNIQUE(turn_id, title));
            CREATE TABLE IF NOT EXISTS profiles(mode TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS dispatch_attempts(turn_id TEXT PRIMARY KEY, generation INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS execution_context(turn_id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, turn_id TEXT NOT NULL, kind TEXT NOT NULL, label TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS tool_results(turn_id TEXT NOT NULL, tool TEXT NOT NULL, arguments TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(turn_id, tool, arguments));
            CREATE TABLE IF NOT EXISTS turn_personalization(turn_id TEXT PRIMARY KEY, profile_key TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS turn_model_choice(turn_id TEXT PRIMARY KEY, choice TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS turn_runtime(turn_id TEXT PRIMARY KEY, runtime TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS workflow_turns(workflow_id TEXT PRIMARY KEY,turn_id TEXT NOT NULL,runtime TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS deep_transcripts(turn_id TEXT PRIMARY KEY,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS routines(id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
              prompt TEXT NOT NULL, interval_seconds INTEGER NOT NULL, next_run REAL NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 1);
            """)
            # Existing turns keep their legacy execution semantics. New queue
            # entries write their route in the same transaction as the turn.
            db.execute("INSERT OR IGNORE INTO turn_runtime SELECT id,'legacy' FROM turns")
            for mode in ("public", "private"):
                db.execute("INSERT OR IGNORE INTO profiles VALUES (?,?)", (mode, "Profile not configured yet. Ask the owner to add verified information."))

    def connect(self):
        db = sqlite3.connect(self.root / "harness.sqlite", timeout=30, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        return db

    def query(self, sql, args=()):
        with self.connect() as db:
            return [dict(r) for r in db.execute(sql, args)]

    def thread(self, mode):
        ident = str(uuid.uuid4())
        with self.connect() as db:
            db.execute("INSERT INTO threads VALUES (?,?)", (ident, mode))
        return {"id": ident, "mode": mode}

    def enqueue(self, thread_id, prompt, ident=None, profile_key="default", model_choice="default"):
        if model_choice not in ("default","openai","qwen"):
            raise ValueError("Unknown model choice")
        ident = ident or str(uuid.uuid4())
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO turns VALUES (?,?,?,'queued',NULL,NULL,?)", (ident, thread_id, prompt, time.time()))
            from .personalization import active_profiles
            db.execute("INSERT OR IGNORE INTO turn_personalization VALUES (?,?)", (ident, profile_key if profile_key in active_profiles() else 'default'))
            db.execute("INSERT OR IGNORE INTO turn_runtime VALUES (?,?)",(ident,agent_runtime()))
            db.execute("INSERT OR IGNORE INTO turn_model_choice VALUES (?,?)", (ident, model_choice))
        return ident

    def artifact(self, thread_id, turn_id, title, body):
        ident = str(uuid.uuid5(uuid.NAMESPACE_URL, turn_id + ":" + title))
        with self.connect() as db:
            db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?) ON CONFLICT(turn_id,title) DO UPDATE SET body=excluded.body", (ident, thread_id, turn_id, title, body))
        return ident

    def tick(self, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for r in db.execute("SELECT * FROM routines WHERE enabled=1 AND next_run<=?", (now,)).fetchall():
                ident = str(uuid.uuid5(uuid.NAMESPACE_URL, f"routine:{r['id']}:{r['next_run']}"))
                db.execute("INSERT OR IGNORE INTO turns VALUES (?,?,?,'queued',NULL,NULL,?)", (ident, r["thread_id"], r["prompt"], now))
                db.execute("INSERT OR IGNORE INTO turn_runtime VALUES (?,?)",(ident,agent_runtime()))
                db.execute("UPDATE routines SET next_run=? WHERE id=?", (now + r["interval_seconds"], r["id"]))

    def profile(self, mode):
        saved = self.brain.profile(mode)
        return saved if saved is not None else self.query("SELECT body FROM profiles WHERE mode=?", (mode,))[0]["body"]

    def set_profile(self, mode, body):
        self.brain.set_profile(mode, body)
        with self.connect() as db:
            db.execute("UPDATE profiles SET body=? WHERE mode=?", (body, mode))

    def event(self, turn_id, kind, label):
        with self.connect() as db:
            db.execute("INSERT INTO events(turn_id,kind,label,created) VALUES (?,?,?,?)", (turn_id, kind, label, time.time()))

    def workflow_id(self, ident):
        rows = self.query("SELECT generation FROM dispatch_attempts WHERE turn_id=?", (ident,))
        return f"turn-{ident}-{rows[0]['generation'] if rows else 0}"

    def runtime_for(self, ident):
        rows=self.query('SELECT runtime FROM turn_runtime WHERE turn_id=?',(ident,))
        return rows[0]['runtime'] if rows else 'legacy'

    def retry(self, ident):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE turns SET status='queued',error=NULL WHERE id=? AND status='failed'", (ident,)).rowcount
            if changed:
                db.execute("INSERT INTO dispatch_attempts VALUES (?,1) ON CONFLICT(turn_id) DO UPDATE SET generation=generation+1", (ident,))
            return bool(changed)
