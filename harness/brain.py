"""Portable, scoped knowledge provider. A GBrain/MCP adapter can implement this interface."""
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


class KnowledgeProvider(Protocol):
    def profile(self, mode: str) -> str | None: ...
    def search(self, query: str, mode: str) -> list[dict]: ...


class FileBrain:
    backend = "files"

    def __init__(self, root):
        self.root = Path(root).expanduser()

    def scope(self, mode):
        if mode not in ("public", "private"):
            raise ValueError("Unknown knowledge scope")
        scope = self.root.resolve() / mode
        if scope.is_symlink():
            raise ValueError("Knowledge scope cannot be a symbolic link")
        return scope

    def read(self, mode, filename):
        scope = self.scope(mode)
        p = scope / filename
        if p.is_symlink() or not p.resolve().is_relative_to(scope):
            raise ValueError("Knowledge file escapes its visibility scope")
        return p.read_text() if p.is_file() else None

    def profile(self, mode):
        return self.read(mode, "profile.md")

    def voice(self, mode):
        return self.read(mode, "voice.md") or ""

    def set_profile(self, mode, body):
        scope = self.scope(mode)
        scope.mkdir(parents=True, exist_ok=True, mode=0o700)
        p = scope / "profile.md"
        tmp = p.with_name(".profile-" + uuid.uuid4().hex)
        tmp.write_text(body)
        os.chmod(tmp, 0o600)
        tmp.replace(p)

    def public_portfolio(self):
        body = self.read("public", "portfolio.json")
        return json.loads(body) if body is not None else {}

    def add_note(self, title, body, mode="private"):
        parent = self.scope(mode)
        scope = parent / "wiki"
        if scope.is_symlink() or not scope.resolve().is_relative_to(parent):
            raise ValueError("Knowledge directory escapes its visibility scope")
        scope.mkdir(parents=True, exist_ok=True, mode=0o700)
        ident = uuid.uuid4().hex
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "note"
        p = scope / (slug + "-" + ident[:8] + ".md")
        now = datetime.now(timezone.utc).isoformat()
        p.write_text(f"# {title}\n\nSource: owner-provided note\nAdded: {now}\nVisibility: {mode}\n\n{body}\n")
        os.chmod(p, 0o600)
        return {"id": p.name, "scope": mode, "title": title}

    def search(self, query, mode):
        self.scope(mode)  # Validate before selecting directories.
        tokens = set(re.findall(r"\w+", query.lower()))
        tokens -= {"the", "a", "an", "about", "what", "how", "is", "and", "does", "he", "his", "my"}
        if not tokens:
            return []
        results = []
        for visibility in (["public"] if mode == "public" else ["public", "private"]):
            scope = self.scope(visibility)
            if not scope.exists():
                continue
            for p in scope.rglob("*.md"):
                if p.is_symlink() or not p.resolve().is_relative_to(scope.resolve()):
                    continue
                body = p.read_text()[:60000]
                paragraphs = body.split("\n\n")
                scored = [(len(tokens & set(re.findall(r"\w+", part.lower()))), part) for part in paragraphs]
                matching = sorted((x for x in scored if x[0]), key=lambda x: x[0], reverse=True)[:3]
                if matching:
                    results.append({"source": visibility + "/" + p.relative_to(scope).as_posix(), "text": "\n\n".join(x[1] for x in matching)[:3500], "score": sum(x[0] for x in matching)})
        # Only the curated public snapshot is available to visitor retrieval.
        for visibility in (["public"] if mode == "public" else ["public", "private"]):
            shelf = json.loads(self.read(visibility, "bookshelf.json") or '{"books":[]}')
            for book in shelf["books"]:
                text = json.dumps(book, ensure_ascii=False)
                score = len(tokens & set(re.findall(r"\w+", text.lower())))
                if score:
                    results.append({"source": visibility + "/bookshelf.json#" + book["id"], "text": text, "score": score})
        from .ingestion import external_documents
        for visibility in (["public"] if mode == "public" else ["public", "private"]):
            for source, body in external_documents(self, visibility).items():
                score = len(tokens & set(re.findall(r"\w+", body.lower())))
                if score:
                    results.append({"source":source,"text":body,"score":score,"knowledge_role":"external_reference"})
        return sorted(results, key=lambda x: x["score"], reverse=True)[:5]
