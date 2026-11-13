"""Portable, scoped knowledge provider. A GBrain/MCP adapter can implement this interface."""
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


def external_title_match(body, query):
    title = body.split('\n', 1)[0].removeprefix('# External reference: ').lower()
    phrase = ' '.join(re.findall(r'\w+', query.lower()))
    normalized_title = ' '.join(re.findall(r'\w+', title))
    return bool((len(phrase.split()) >= 2 and phrase in normalized_title)
                or (len(normalized_title.split()) >= 2 and normalized_title in phrase))



def matching_words(text, tokens):
    """Find whole query words without allocating every word in each excerpt."""
    if len(tokens) > 24:
        return set(tokens) & set(re.findall(r"\w+", text))
    matches = set()
    for token in tokens:
        start = text.find(token)
        if start == -1:
            continue
        end = start + len(token)
        left = text[start-1] if start else ''
        right = text[end] if end < len(text) else ''
        whole = not (left.isalnum() or left == '_') and not (right.isalnum() or right == '_')
        if whole or re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", text):
            matches.add(token)
    return matches


def external_score(body, tokens, query):
    title = body.split('\n', 1)[0].removeprefix('# External reference: ').lower()
    words = set(re.findall(r'\w+', title))
    matches = matching_words(body.lower(), tokens)
    return len(matches) + 5*len(set(tokens)&words) + (60 if external_title_match(body, query) else 0)


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

    def perspectives(self, query, mode):
        from .perspectives import perspective_hits
        return perspective_hits(self,query,mode)

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
        from .learning import learning_hits
        from .retrieval import rank_knowledge
        hits=learning_hits(self,query,mode)+self.perspectives(query,mode)+self._search_scopes(query, ["public"] if mode == "public" else ["public", "private"])
        return rank_knowledge(hits)

    def _search_scopes(self, query, visibilities):
        for visibility in visibilities:
            self.scope(visibility)
        tokens = set(re.findall(r"\w+", query.lower()))
        tokens -= {"the", "a", "an", "about", "what", "how", "is", "and", "does", "he", "his", "my"}
        if not tokens:
            return []
        results = []
        for visibility in visibilities:
            scope = self.scope(visibility)
            if not scope.exists():
                continue
            for p in scope.rglob("*.md"):
                if p.is_symlink() or not p.resolve().is_relative_to(scope.resolve()):
                    continue
                body = p.read_text()[:60000]
                paragraphs = body.split("\n\n")
                scored = [(len(matching_words(part.lower(), tokens)), part) for part in paragraphs]
                matching = sorted((x for x in scored if x[0]), key=lambda x: x[0], reverse=True)[:3]
                if matching:
                    excerpts = [x[1] for x in matching]
                    # A title match should carry the note's actual ideas, not
                    # just a heading the agent cannot answer from.
                    if all(part.lstrip().startswith('#') for part in excerpts):
                        supporting = [part for part in paragraphs if part.strip()
                                      and not part.lstrip().startswith(('#', 'Source:', 'Added:', 'Visibility:'))]
                        excerpts.extend(supporting[:2])
                    if p.parent.name == 'influences':
                        # Preserve the work's identity and provenance beside its ideas.
                        excerpts = list(dict.fromkeys(paragraphs[:2] + excerpts))
                    results.append({"source": visibility + "/" + p.relative_to(scope).as_posix(), "title": body.splitlines()[0].lstrip("# ")[:200] if body.splitlines() else p.stem, "text": "\n\n".join(excerpts)[:3500], "score": sum(x[0] for x in matching)})
        # Only the curated public snapshot is available to visitor retrieval.
        for visibility in visibilities:
            shelf = json.loads(self.read(visibility, "bookshelf.json") or '{"books":[]}')
            for book in shelf["books"]:
                text = json.dumps(book, ensure_ascii=False)
                score = len(tokens & set(re.findall(r"\w+", text.lower())))
                if score:
                    results.append({"source": visibility + "/bookshelf.json#" + book["id"], "text": text, "score": score})
        from .ingestion import iter_external_documents, reference_version
        references = {}
        versions = {}
        reference_order = 0
        for visibility in visibilities:
            for source, body in iter_external_documents(self, visibility, tokens, versions):
                score = external_score(body, tokens, query)
                key = source.split('#chunk-', 1)[0]
                previous = references.get(key)
                if score and (previous is None or score > previous[1]['score']):
                    references[key] = (reference_order, {"source":source,"text":body,"score":score,"knowledge_role":"external_reference","exact_title_match":external_title_match(body,query)})
                reference_order += 1
        for _, hit in sorted(references.values(), key=lambda item: item[0]):
            path, version = versions[hit['source'].split('#chunk-', 1)[0]]
            try:
                if reference_version(path) == version:
                    results.append(hit)
            except FileNotFoundError:
                continue
        from .retrieval import rank_knowledge
        return rank_knowledge(results, limit=12)
