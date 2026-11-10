"""Owner-reviewed source annotations and corrections. Canonical data stays outside Git."""
import fcntl
import json
import os
import re
import uuid
import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, Field, field_validator, model_validator

STOP = set('the a an about what how is and does he his my i you your to of for in on with from this that it do think'.split())


class Annotation(BaseModel):
    kind: Literal['book', 'podcast', 'feedback']
    target_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')
    note: str = Field(default='', max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=12)
    experience: Literal['unknown', 'reading', 'read', 'listening', 'listened'] = 'unknown'
    revision: str | None = None
    approved_response: str = Field(default='', max_length=20000)

    @model_validator(mode='after')
    def feedback_only(self):
        if self.approved_response.strip() and self.kind != 'feedback':
            raise ValueError('A better answer belongs to a reply correction.')
        return self

    @field_validator('tags')
    @classmethod
    def clean_tags(cls, tags):
        if any(not t.strip() or len(t.strip()) > 40 for t in tags):
            raise ValueError('Use tags between 1 and 40 characters.')
        return list(dict.fromkeys(t.strip().lower() for t in tags))


class Revision(BaseModel):
    revision: str


class Conflict(ValueError):
    pass


class Learning:
    def __init__(self, brain):
        self.brain = brain

    def read(self, scope):
        data = json.loads(self.brain.read(scope, 'learning.json') or '{"version":1,"entries":[]}')
        if data.get('version') != 1 or not isinstance(data.get('entries'), list):
            raise ValueError('Invalid learning records.')
        return {r['id']: r for r in data['entries']}

    @contextmanager
    def locked(self):
        root = self.brain.root.resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = root/'.learning.lock'
        if path.is_symlink():
            raise ValueError('Learning lock cannot be a symbolic link.')
        with path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def write(self, scope, entries):
        root = self.brain.scope(scope)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = root/'learning.json'
        if target.is_symlink():
            raise ValueError('Learning records cannot be a symbolic link.')
        tmp = root/('.learning-'+uuid.uuid4().hex)
        tmp.write_text(json.dumps(dict(version=1, entries=list(entries.values())), ensure_ascii=False, indent=2))
        tmp.chmod(0o600)
        tmp.replace(target)

    def catalog(self):
        from .ingestion import reference_root, read_record
        resources = {}
        for scope in ('public', 'private'):
            shelf = json.loads(self.brain.read(scope, 'bookshelf.json') or '{"books":[]}')
            for b in shelf['books']:
                resources['book:'+b['id']] = dict(kind='book',target_id=b['id'],title=b['title'],
                    author=b.get('author',''),url='/?book='+quote(b['id'])+'#bookshelf')
            root = reference_root(self.brain, scope, 'podcasts')
            for path in sorted(root.glob('*.json')):
                r = read_record(path)
                if r['scope'] != scope or r['collection'] != 'podcasts':
                    raise ValueError('Reference scope mismatch.')
                if r['active']:
                    resources['podcast:'+path.stem] = dict(kind='podcast',target_id=path.stem,
                        title=r['title'],author=str(r.get('provenance',{}).get('show','')),url=r['source_url'])
        return list(resources.values())

    def inventory(self):
        public = self.read('public')
        entries = [{**r,'published': r['id'] in public,
                    'publication_current': public.get(r['id'],{}).get('revision') == r['revision']}
                   for r in self.read('private').values()]
        return dict(resources=self.catalog(), entries=entries)

    def save(self, body, store, draft=False):
        ident = body.kind+'-'+body.target_id
        exchange = None
        if body.kind == 'feedback':
            existing = self.read('private').get(ident)
            try:
                exchange = capture_exchange(store, body.target_id)
            except KeyError:
                if not existing:
                    raise
                exchange = existing.get('exchange')
            if body.approved_response.strip() and (draft or not exchange or exchange['status'] != 'completed' or not exchange['original_response']):
                raise ValueError('Approve a better answer only for a completed reply.')
            resource = dict(title='Correction: '+(body.note.strip().split('\n')[0][:90] or 'reply feedback'),author='',url='')
        else:
            resource = next((r for r in self.catalog() if r['kind']==body.kind and r['target_id']==body.target_id), None)
            if not resource:
                raise KeyError('Source not found.')
        if body.kind == 'book' and body.experience not in ('unknown','reading','read'):
            raise ValueError('Choose a book reading status.')
        if body.kind == 'podcast' and body.experience not in ('unknown','listening','listened'):
            raise ValueError('Choose an episode listening status.')
        with self.locked():
            entries = self.read('private')
            old = entries.get(ident)
            # A repeated durable tool call must not overwrite subsequent owner edits.
            if draft and old:
                return old
            if body.revision != (old['revision'] if old else None):
                raise Conflict('This note changed. Reload before saving.')
            record = dict(id=ident, **body.model_dump(exclude={'revision'}),
                title=resource['title'],author=resource['author'],url=resource['url'],
                state='draft' if draft else 'active', basis='owner_confirmed' if not draft else 'proposed',
                revision=uuid.uuid4().hex,updated=datetime.now(timezone.utc).isoformat())
            if exchange:
                # Snapshot once. Hosted conversation databases do not sync with the brain.
                prior = old.get('exchange') if old else None
                record['exchange'] = prior if prior and prior.get('status') == 'completed' else exchange
            record['approved_response'] = body.approved_response.strip()
            entries[ident] = record
            self.write('private',entries)
            return record

    def publish(self, ident, revision):
        with self.locked():
            record = self.read('private').get(ident)
            if not record:
                raise KeyError('Note not found.')
            if record['revision'] != revision:
                raise Conflict('This note changed. Review the latest version.')
            if record['state'] != 'active':
                raise ValueError('Review and save this draft before publishing.')
            entries = self.read('public')
            # Conversation snapshots and training targets are always private, even
            # when the owner publishes the correction note itself.
            entries[ident] = {k: v for k, v in record.items()
                              if k not in ('exchange', 'approved_response')}
            self.write('public', entries)
            return record

    def remove(self, ident, public_only=False):
        with self.locked():
            for scope in (['public'] if public_only else ['public','private']):
                entries = self.read(scope)
                entries.pop(ident, None)
                self.write(scope,entries)

    def training_data(self):
        examples = []
        pending = 0
        for record in self.read('private').values():
            if record.get('kind') != 'feedback' or record.get('state') != 'active' or record.get('basis') != 'owner_confirmed':
                continue
            exchange = record.get('exchange') or {}
            answer = record.get('approved_response', '').strip()
            if not answer or not exchange.get('messages') or not exchange.get('original_response') or not exchange.get('context_complete'):
                pending += 1
                continue
            group = exchange['thread_id']
            examples.append(dict(id=record['id'], revision=record['revision'],
                group_id=group, split='evaluation' if int(hashlib.sha256(group.encode()).hexdigest(),16) % 5 == 0 else 'train',
                messages=[*exchange['messages'], dict(role='assistant',content=answer)],
                original_response=exchange['original_response'], correction=record['note'],
                tags=record['tags'], source='owner_approved_correction',
                context_scope='visible_conversation_only'))
        return dict(version=1, examples=examples, ready=len(examples), pending=pending,
                    note='Local training candidates, not a trained model. Split by conversation; includes visible chat context, not tool traces. Review before training.')


def capture_exchange(store, ident):
    rows = store.query('SELECT rowid AS sequence, * FROM turns WHERE id=?', (ident,))
    if not rows:
        raise KeyError('Reply not found.')
    turn = rows[0]
    earlier = store.query('SELECT * FROM turns WHERE thread_id=? AND rowid<? ORDER BY rowid',
                          (turn['thread_id'], turn['sequence']))
    messages = []
    complete = True
    for previous in earlier:
        if previous['status'] != 'completed' or not previous['result']:
            complete = False
            continue
        messages.extend([dict(role='user',content=previous['prompt']),
                         dict(role='assistant',content=previous['result'])])
    messages.append(dict(role='user',content=turn['prompt']))
    return dict(thread_id=turn['thread_id'], turn_id=ident, messages=messages,
                original_response=turn['result'], status=turn['status'], context_complete=complete)


def learning_hits(brain, query, mode):
    brain.scope(mode)
    tokens = set(re.findall(r'\w+',query.lower())) - STOP
    records = {}
    learning = Learning(brain)
    for scope in (['public'] if mode=='public' else ['public','private']):
        for ident, record in learning.read(scope).items():
            records[ident] = (scope,record)
    hits = []
    for scope,r in records.values():
        if r['state'] != 'active':
            continue
        text = ' '.join([r['title'],r['note'],*r['tags']])
        score = len(tokens & set(re.findall(r'\w+',text.lower())))
        if not score and 'always' not in r['tags']:
            continue
        evidence = {k:r[k] for k in ('kind','title','author','url','note','tags','experience','basis')}
        evidence['provenance'] = 'Explicit owner-reviewed note. Only read/listened status confirms consumption. This note supports this specific perspective, not endorsement of the entire source.'
        hits.append(dict(source=scope+'/learning.json#'+r['id'],text='# Owner learning note\n\n'+json.dumps(evidence,ensure_ascii=False),
                         score=150+score,knowledge_role='owner_learning'))
    return sorted(hits,key=lambda h:-h['score'])[:3]


def feedback_tools(ctx):
    if ctx['mode'] != 'private':
        return []
    from langchain.tools import tool

    @tool
    def propose_memory(correction: str, topics: list[str]) -> str:
        """Draft a lasting correction or preference explicitly supplied by the owner in this private conversation. Use their words, not an inferred belief. Topics are short tags; 'always' is for general voice preferences. Does not activate or publish anything: owner reviews in Reading & listening. Never store credentials or private source excerpts."""
        body = Annotation(kind='feedback',target_id=ctx['turn_id'],note=correction,tags=topics)
        record = Learning(ctx['store'].brain).save(body,ctx['store'],draft=True)
        return json.dumps(dict(id=record['id'],state=record['state'],message='Saved a private proposal. Review it in Owner studio → Reading & listening before it affects future replies.'))
    return [propose_memory]
