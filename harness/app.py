import asyncio
import contextlib
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .store import Store
from .runner import local_worker
from .durable import temporal_dispatcher
from .models import settings
from .public_access import PublicAccess, public_only, admit_turn, consume
from .portfolio import Portfolio
from .bookshelf import Shelf, ImportInput, Selection, BookEdit
from .web_tools import provider as web_provider

store = Store()


@asynccontextmanager
async def lifespan(app):
    app.state.public_store = store
    executor = os.getenv("EXECUTOR", "local")
    if executor not in ("local", "temporal"):
        raise RuntimeError("EXECUTOR must be local or temporal")
    if settings()['agent_runtime']=='deepagents' and executor!='temporal':
        raise RuntimeError('Deep Agents runtime requires EXECUTOR=temporal.')
    task = asyncio.create_task(temporal_dispatcher(store) if executor == "temporal" else local_worker(store))
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="Personal Agent Harness", lifespan=lifespan)
app.add_middleware(PublicAccess)
frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if (frontend_dist / "assets").exists():
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="frontend-assets")


def owner(authorization: str = Header(default="")):
    if public_only():
        raise HTTPException(404, "Owner workspace is available only in the local edition.")
    token = os.getenv("OWNER_TOKEN", "")
    if not token or not secrets.compare_digest(authorization, "Bearer " + token):
        raise HTTPException(401, "Private workspace requires OWNER_TOKEN.")


def access(ident, authorization):
    rows = store.query("SELECT * FROM threads WHERE id=?", (ident,))
    if not rows:
        raise HTTPException(404, "Thread not found")
    if rows[0]["mode"] == "private":
        owner(authorization)
    return rows[0]


class ThreadInput(BaseModel):
    mode: str = "public"


class TurnInput(BaseModel):
    prompt: str = Field(min_length=1, max_length=16000)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)


class ProfileInput(BaseModel):
    body: str = Field(max_length=50000)


class RoutineInput(TurnInput):
    interval_seconds: int = Field(ge=60, le=31536000)


@app.get("/")
def index():
    return FileResponse(frontend_dist / "index.html" if (frontend_dist / "index.html").exists() else Path(__file__).parent / "index.html")


@app.get("/healthz")
def health():
    import socket
    store.query('SELECT 1')
    if os.getenv('EXECUTOR') == 'temporal':
        try:
            host, port = os.getenv('TEMPORAL_ADDRESS', 'localhost:7233').rsplit(':', 1)
            with socket.create_connection((host, int(port)), timeout=1):
                pass
        except OSError:
            raise HTTPException(503, 'Worker services are starting.')
    return {'status':'ok'}


@app.get("/api/status")
def status():
    config = settings()
    return {"public_only": public_only(), "model_configured": config["configured"], "model": config["model"], "provider": config["provider"], "agent_runtime":config['agent_runtime'], "executor": os.getenv("EXECUTOR", "local"), "knowledge_backend": store.brain.backend, "knowledge_fallback_policy":"same-scope-canonical-files-when-index-busy" if store.brain.backend == 'gbrain' else None, "web_search_provider": web_provider(), "profile_configured": not store.profile("public").startswith("Profile not configured")}


@app.get("/api/tools")
def tool_catalog():
    from .agent import plugins
    return [{"name": t.name, "description": t.description} for t in plugins.tools({"mode": "public", "store": store})]


@app.get("/api/portfolio", response_model=Portfolio)
def portfolio():
    return Portfolio.model_validate(store.brain.public_portfolio())


@app.post("/api/threads")
def create_thread(body: ThreadInput, request: Request, authorization: str = Header(default="")):
    if body.mode not in ("public", "private"):
        raise HTTPException(422, "Choose public or private")
    if body.mode == "private":
        owner(authorization)
    if public_only():
        consume(store, [('day:threads:'+request.state.visitor_hash, 86400, 10)])
    thread = store.thread(body.mode)
    if public_only():
        with store.connect() as db:
            db.execute('INSERT INTO visitor_threads VALUES (?,?)', (thread['id'], request.state.visitor_hash))
    return thread


@app.get("/api/threads/{ident}")
def thread(ident: str, authorization: str = Header(default="")):
    data = access(ident, authorization)
    import json
    calls = []
    for row in store.query("SELECT r.* FROM tool_results r JOIN turns t ON r.turn_id=t.id WHERE t.thread_id=? ORDER BY t.created,r.rowid", (ident,)):
        result = json.loads(row["body"])
        call = {"turn_id": row["turn_id"], "name": row["tool"], "sources": [{"title": s["title"], "url": s["url"]} for s in result.get("sources", [])], "fetched_at": result.get("fetched_at")}
        if row['tool'] == 'browse_bookshelf' and result.get('status') == 'ok':
            # Revalidate against today's public selection, even for historical turns.
            published = {b['id']: b for b in Shelf(store.brain).read('public')['books']}
            ids = [i for i in result.get('ui_action',{}).get('book_ids',[])[:8] if i in published]
            call['ui_action'] = {'type':'bookshelf_selection','book_ids':ids}
            call['books'] = [{'id':i,'title':published[i]['title']} for i in ids]
        calls.append(call)
    return {**data, "tool_calls": calls, "turns": store.query("SELECT * FROM turns WHERE thread_id=? ORDER BY created", (ident,)), "artifacts": store.query("SELECT * FROM artifacts WHERE thread_id=?", (ident,)), "events": store.query("SELECT e.* FROM events e JOIN turns t ON e.turn_id=t.id WHERE t.thread_id=? ORDER BY e.id DESC LIMIT 200", (ident,))[::-1]}


@app.get("/api/profiles/{mode}", dependencies=[Depends(owner)])
def read_profile(mode: str):
    if mode not in ("public", "private"):
        raise HTTPException(422)
    return {"body": store.profile(mode)}


@app.post("/api/threads/{ident}/turns", status_code=202)
def enqueue(ident: str, body: TurnInput, request: Request, authorization: str = Header(default="")):
    access(ident, authorization)
    if not settings()["configured"]:
        raise HTTPException(503, "Add OPENAI_API_KEY to .env and restart to enable chat.")
    turn_id = str(uuid.uuid5(uuid.NAMESPACE_URL, ident + ":" + body.request_id)) if body.request_id else None
    if turn_id:
        existing = store.query("SELECT prompt FROM turns WHERE id=?", (turn_id,))
        if existing and existing[0]["prompt"] != body.prompt:
            raise HTTPException(409, "request_id was already used for another prompt")
    if public_only() and len(body.prompt) > 2000:
        raise HTTPException(422, 'Please keep demo questions under 2,000 characters.')
    if not turn_id or not store.query('SELECT id FROM turns WHERE id=?', (turn_id,)):
        admit_turn(store, request)
    turn_id = store.enqueue(ident, body.prompt, turn_id)
    return {"id": turn_id, "status": store.query("SELECT status FROM turns WHERE id=?", (turn_id,))[0]["status"], "workflow_id": store.workflow_id(turn_id)}


@app.post("/api/turns/{ident}/retry", status_code=202)
def retry(ident: str, request: Request, authorization: str = Header(default="")):
    rows = store.query("SELECT * FROM turns WHERE id=?", (ident,))
    if not rows:
        raise HTTPException(404)
    access(rows[0]["thread_id"], authorization)
    admit_turn(store, request)
    changed = store.retry(ident)
    if not changed:
        raise HTTPException(409, "Only failed turns can be retried")
    return {"id": ident}


@app.put("/api/profiles/{mode}", dependencies=[Depends(owner)])
def profile(mode: str, body: ProfileInput):
    if mode not in ("public", "private"):
        raise HTTPException(422)
    store.set_profile(mode, body.body)
    return {"saved": True}


class NoteInput(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    body: str = Field(min_length=1, max_length=30000)
    scope: str = "private"


@app.post("/api/brain/notes", dependencies=[Depends(owner)])
def add_note(body: NoteInput):
    if body.scope not in ("public", "private"):
        raise HTTPException(422, "Choose public or private")
    return store.brain.add_note(body.title, body.body, body.scope)


@app.post("/api/threads/{ident}/routines", dependencies=[Depends(owner)])
def routine(ident: str, body: RoutineInput):
    rows = store.query("SELECT mode FROM threads WHERE id=?", (ident,))
    if not rows or rows[0]["mode"] != "private":
        raise HTTPException(422, "Routines require a private thread")
    rid = str(uuid.uuid4())
    with store.connect() as db:
        db.execute("INSERT INTO routines VALUES (?,?,?,?,?,1)", (rid, ident, body.prompt, body.interval_seconds, time.time() + body.interval_seconds))
    return {"id": rid}


@app.get("/api/routines", dependencies=[Depends(owner)])
def routines():
    return store.query("SELECT * FROM routines ORDER BY next_run")


@app.post("/api/routines/{ident}/trigger", dependencies=[Depends(owner)], status_code=202)
def trigger_routine(ident: str):
    rows = store.query("SELECT * FROM routines WHERE id=? AND enabled=1", (ident,))
    if not rows:
        raise HTTPException(404, "Enabled routine not found")
    r = rows[0]
    return {"id": store.enqueue(r["thread_id"], r["prompt"])}


@app.delete("/api/routines/{ident}", dependencies=[Depends(owner)])
def disable(ident: str):
    with store.connect() as db:
        db.execute("UPDATE routines SET enabled=0 WHERE id=?", (ident,))
    return {"disabled": True}


@app.get("/api/bookshelf")
def public_bookshelf():
    data = Shelf(store.brain).read("public")
    return {"books": data["books"]}


@app.get("/api/owner/bookshelf", dependencies=[Depends(owner)])
def private_bookshelf():
    return Shelf(store.brain).inventory()


@app.post("/api/owner/bookshelf/import", dependencies=[Depends(owner)])
def import_bookshelf(body: ImportInput):
    return Shelf(store.brain).ingest(body)


@app.put("/api/owner/bookshelf/{ident}", dependencies=[Depends(owner)])
def edit_book(ident: str, body: BookEdit):
    try:
        return Shelf(store.brain).edit(ident, body)
    except KeyError:
        raise HTTPException(404, "Book not found")
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/owner/bookshelf/publish", dependencies=[Depends(owner)])
def publish_bookshelf(body: Selection):
    try:
        return Shelf(store.brain).publish(body)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.get("/api/bookshelf/spine/{ident}")
def spine_asset(ident: str):
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", ident):
        raise HTTPException(404)
    public = Shelf(store.brain).read("public")
    if not any(b["id"] == ident and b.get("spine") for b in public["books"]):
        raise HTTPException(404)
    root = store.brain.scope("public") / "bookshelf-assets"
    if root.is_symlink():
        raise HTTPException(404)
    for suffix, mime in (("jpg", "image/jpeg"), ("png", "image/png"), ("webp", "image/webp")):
        p = root / (ident + "." + suffix)
        if p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(root.resolve()):
            return FileResponse(p, media_type=mime, headers={"Cache-Control":"private, max-age=0, must-revalidate"})
    raise HTTPException(404)


@app.get("/api/bookshelf/cover/{ident}")
def cover_asset(ident: str):
    import re
    import httpx
    from urllib.parse import urlparse
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", ident):
        raise HTTPException(404)
    book = next((b for b in Shelf(store.brain).read('public')['books'] if b['id'] == ident), None)
    if not book or not book.get('cover'):
        raise HTTPException(404)
    url = urlparse(book['cover'])
    if url.scheme != 'https' or url.hostname != 'm.media-amazon.com' or url.username or url.password or url.query:
        raise HTTPException(404)
    root = store.brain.scope('public') / 'bookshelf-covers'
    if root.is_symlink():
        raise HTTPException(404)
    root.mkdir(exist_ok=True, parents=True, mode=0o700)
    # Key by source URL too, so an owner's later cover change cannot reuse an old jacket.
    import hashlib
    key = ident + '-' + hashlib.sha256(book['cover'].encode()).hexdigest()[:16]
    p = root / (key + '.jpg')
    if p.is_symlink():
        raise HTTPException(404)
    if not p.is_file():
        try:
            with httpx.stream('GET', book['cover'], timeout=12, follow_redirects=False) as response:
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 5_000_000:
                        raise ValueError('Image exceeds limit')
                    chunks.append(chunk)
            data = b''.join(chunks)
            if not data.startswith(b'\xff\xd8'):
                raise ValueError('Not a JPEG cover')
            tmp = root / ('.cover-' + uuid.uuid4().hex)
            tmp.write_bytes(data)
            tmp.chmod(0o600)
            tmp.replace(p)
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, 'Cover image unavailable')
    return FileResponse(p, media_type='image/jpeg', headers={'Cache-Control':'private, max-age=0, must-revalidate'})
