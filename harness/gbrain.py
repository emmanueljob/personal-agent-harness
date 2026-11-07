"""Local GBrain retrieval over disposable, separately scoped indexes.

Markdown and bookshelf JSON remain canonical. No provider keys or inherited
GBrain routing/configuration enter the subprocess environment.
"""
import fcntl
import hashlib
import json
import os
import re
import subprocess
import uuid
from pathlib import Path

from .brain import FileBrain, external_score, external_title_match


class GBrainError(RuntimeError):
    pass


class GBrain(FileBrain):
    backend = "gbrain"

    def __init__(self, root, index_root=None, binary=None):
        super().__init__(root)
        self.index_root = Path(index_root or self.root.parent / "gbrain").expanduser()
        self.binary = Path(binary or os.getenv("GBRAIN_BINARY") or Path(__file__).resolve().parents[1] / ".runtime/tools/gbrain/gbrain").resolve()

    def _run(self, visibility, args, body=None):
        home = self.index_root / visibility
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "USER", "LANG", "TMPDIR")}
        env["GBRAIN_HOME"] = str(home.resolve())
        try:
            result = subprocess.run([str(self.binary), *args], input=body, text=True,
                                    capture_output=True, cwd=home, env=env, timeout=90)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GBrainError("Local knowledge search is unavailable; check the GBrain installation.") from exc
        if result.returncode:
            # Diagnostics can contain private source text; never surface raw output.
            raise GBrainError("Local knowledge search failed; no alternate scope was searched.")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise GBrainError("GBrain returned an unreadable response.") from exc

    def documents(self, visibility):
        scope = self.scope(visibility)
        docs = {}
        profile = self.profile(visibility)
        if profile:
            docs[visibility + "/profile.md"] = profile
        wiki = scope / "wiki"
        if wiki.is_symlink():
            raise ValueError("Knowledge directory cannot be a symbolic link")
        for p in sorted(wiki.rglob("*.md")):
            if p.is_symlink() or not p.resolve().is_relative_to(scope):
                continue
            # Voice instructions and import manifests are deliberately not facts.
            docs[visibility + "/" + p.relative_to(scope).as_posix()] = p.read_text()
        shelf = json.loads(self.read(visibility, "bookshelf.json") or '{"books":[]}')
        for book in shelf["books"]:
            source = visibility + "/bookshelf.json#" + book["id"]
            docs[source] = "# Book: " + book.get("title", "Untitled") + "\n\n" + json.dumps(book, ensure_ascii=False)
        from .ingestion import external_documents
        docs.update(external_documents(self, visibility))
        return docs

    @staticmethod
    def _records(docs):
        return {"afterhours/" + hashlib.sha256(source.encode()).hexdigest()[:24]:
                {"source": source, "sha256": hashlib.sha256(body.encode()).hexdigest()}
                for source, body in docs.items()}

    def _sync_locked(self, visibility):
        docs = self.documents(visibility)
        records = self._records(docs)
        manifest = self.index_root / visibility / "index.json"
        previous = json.loads(manifest.read_text()) if manifest.exists() else {}
        for slug, record in records.items():
            if previous.get(slug) == record:
                continue
            body = docs[record["source"]]
            result = self._run(visibility, ["put", slug, "--force", "--content-origin", "tool_output",
                               "--request-id", str(uuid.uuid4()), "--json"], body)
            if result.get("state") != "committed":
                raise GBrainError("Knowledge update is pending; retrieval has not been enabled for it.")
        for slug in previous.keys() - records.keys():
            result = self._run(visibility, ["delete", slug, "--force", "--json"])
            if result.get("state") != "committed":
                raise GBrainError("Knowledge withdrawal is pending; retrieval has been stopped.")
        temporary = manifest.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, indent=2) + "\n")
        temporary.chmod(0o600)
        temporary.replace(manifest)
        return docs, records

    def _home(self, visibility):
        self.scope(visibility)
        home = self.index_root / visibility
        if home.is_symlink():
            raise ValueError("GBrain index cannot be a symbolic link")
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not (home / ".gbrain/config.json").is_file():
            raise GBrainError("GBrain is not initialized. Run scripts/setup_gbrain.py first.")
        return home

    def sync(self, visibility):
        home = self._home(visibility)
        with (home / "adapter.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            _, records = self._sync_locked(visibility)
        return len(records)

    def search(self, query, mode):
        self.scope(mode)
        tokens = list(dict.fromkeys(re.findall(r"\w+", query.lower())))
        stop = {"the", "a", "an", "about", "what", "how", "is", "and", "does", "he", "his", "my", "do", "you", "like", "likes", "it", "its", "to", "be", "before", "after", "will", "would", "can", "could", "should", "need", "needs", "someone", "something", "me", "as", "of", "for", "that", "this", "in", "on", "with", "from", "are", "was", "were", "when", "which", "who", "give", "tell"}
        tokens = [t for t in tokens if t not in stop][:24]
        if not tokens:
            return []
        # GBrain's keyword mode supports an explicit OR over quoted terms.
        keywords = " OR ".join('"' + t + '"' for t in tokens)
        results = []
        for visibility in (["public"] if mode == "public" else ["public", "private"]):
            home = self._home(visibility)
            with (home / "adapter.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    # Bulk indexing must not make retrieval wait. Re-read the
                    # same canonical scope to discard concurrently withdrawn or
                    # changed excerpts; never query another scope or stale index.
                    first = FileBrain._search_scopes(self, query, [visibility])
                    current = {hit['source']: hit['text'] for hit in FileBrain._search_scopes(self, query, [visibility])}
                    results.extend({**hit, 'backend':'files', 'fallback_reason':'index_busy'}
                                   for hit in first if current.get(hit['source']) == hit['text'])
                    continue
                docs, records = self._sync_locked(visibility)
                hits = self._run(visibility, ["search", keywords, "--match", "keyword", "--json", "--limit", "20"])
                if not isinstance(hits, list):
                    raise GBrainError("GBrain returned an unexpected search result.")
                # Check current publication again: withdrawn/changed records never
                # escape through a stale index, even during concurrent curation.
                current = self._records(self.documents(visibility))
                seen = set()
                for hit in hits:
                    slug = hit.get("slug")
                    record = records.get(slug)
                    if not record or current.get(slug) != record or slug in seen:
                        continue
                    seen.add(slug)
                    body = docs[record["source"]]
                    paragraphs = body.split("\n\n")
                    ranked = sorted(paragraphs, key=lambda p: len(set(tokens) & set(re.findall(r"\w+", p.lower()))), reverse=True)
                    score = len(set(tokens) & set(re.findall(r"\w+", body.lower())))
                    if '/references/' in record['source']:
                        score = external_score(body,tokens,query)
                    if not score:
                        continue
                    # Keep identity/author/provenance near excerpts, so a matching
                    # opinion paragraph does not lose which work it describes.
                    evidence = list(dict.fromkeys(paragraphs[:3] + ranked[:4]))
                    results.append({"source": record["source"], "text": "\n\n".join(evidence)[:5000], "score": score, "backend": "gbrain", "exact_title_match": external_title_match(body,query) if "/references/" in record["source"] else False})
        from .learning import learning_hits
        from .retrieval import rank_knowledge
        return rank_knowledge(learning_hits(self,query,mode)+self.perspectives(query,mode)+results)
