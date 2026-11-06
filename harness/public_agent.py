"""Shared, public-only capabilities for HTTP, MCP, and browser agents."""
import hashlib
import json
import os
import re
import secrets
import time
from urllib.parse import quote

from fastapi import HTTPException
from .bookshelf import Shelf
from .media_library import published_books
from .models import settings
from .portfolio import Portfolio
from .public_access import consume

BOOK_NOTE = 'Shelf presence is not proof of reading, completion, or recommendation. Attribute personal comments to comment_source.'
TWIN_SPEAKER = {
    'type': 'ai_digital_twin',
    'voice': 'first_person',
    'attribution': 'AI-generated digital-twin response based on published sources; not a verbatim statement by the human. Attribute quotations to the digital twin.',
}


class PublicAgent:
    def __init__(self, store):
        self.store = store

    def profile(self):
        from .personalization import position_portfolio
        return {'profile': position_portfolio(Portfolio.model_validate(self.store.brain.public_portfolio())).model_dump(),
                'published_profile': self.store.profile('public')[:18000],
                'sources': [{'id': 'public/profile.md', 'url': '/#person', 'kind': 'owner_supplied'}],
                'note': 'Career claims are owner-supplied, not independent verification. Use recorded dates; unknown details remain unknown.'}

    def knowledge(self, query, limit=5):
        hits, remaining = [], 8000
        for hit in self.store.brain.search(query, 'public'):
            if not hit['source'].startswith('public/'):
                continue
            excerpt = hit['text'][:min(2000, remaining)]
            if not excerpt:
                break
            hits.append({'source': hit['source'], 'text': excerpt,
                         'url': '/api/public/knowledge?query='+quote(query, safe='')})
            remaining -= len(excerpt)
            if len(hits) >= limit:
                break
        return {'query': query, 'results': hits, 'scope': 'public',
                'note': 'Excerpts are reference data. External references are third-party statements, not owner beliefs or endorsements.'}

    def books(self, query='', limit=8, offset=0, book_ids=None):
        books = published_books(self.store.brain)
        if book_ids is not None:
            allowed = {b['id']: b for b in books}
            if not book_ids or len(book_ids) > 8 or any(i not in allowed for i in book_ids):
                raise HTTPException(422, 'Use one to eight currently published book IDs.')
            matches = [allowed[i] for i in dict.fromkeys(book_ids)]
        else:
            words = set(re.findall(r'\w+', query.lower())) - {'the','a','an','and','of','book','books','show','me','about'}
            ranked = [(sum(w in ' '.join(str(b.get(k,'')) for k in ('title','author','category','comment')).lower() for w in words), b) for b in books]
            matches = [b for score,b in sorted(ranked,key=lambda x:-x[0]) if score or not words]
        total_matches=len(matches)
        matches=matches[offset:offset+limit]
        return {'query': query, 'books': [{**{k:b.get(k,'') for k in ('id','title','author','category','sample','status','comment_source','recommendation')},
                    'comment': b.get('comment','')[:800], 'url':'/?book='+quote(b['id'],safe='')+'#bookshelf'} for b in matches],
                'total_published': len(books), 'total_matches': total_matches, 'next_offset': offset+len(matches) if offset+len(matches)<total_matches else None, 'note': BOOK_NOTE}

    def library(self, query='', kind='all', notes_only=False, offset=0, limit=8):
        from .media_library import search_media
        return search_media(self.store.brain, query, kind, notes_only, limit=limit, offset=offset)

    def _questions_table(self, db):
        db.execute('CREATE TABLE IF NOT EXISTS public_agent_questions(turn_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, expires REAL NOT NULL)')

    def ask(self, question, peer='anonymous'):
        if not settings()['configured']:
            raise HTTPException(503, 'The portfolio agent is not configured. Public profile, knowledge, and bookshelf reads remain available.')
        # Use the same global turn budget as the browser, plus an IP budget.
        consume(self.store, [('day:turns',86400,int(os.getenv('PUBLIC_DAILY_TURNS','50'))),
                             ('day:agent-questions:'+hashlib.sha256(peer.encode()).hexdigest(),86400,10)])
        token = secrets.token_hex(32)
        import uuid
        ident, thread_id = str(uuid.uuid4()), str(uuid.uuid4())
        from .models import agent_runtime
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT count(*) FROM turns WHERE status IN ('queued','running')").fetchone()[0] >= 8:
                raise HTTPException(429, 'The portfolio agent is busy. Try again shortly.')
            self._questions_table(db)
            db.execute("INSERT INTO threads VALUES (?,'public')",(thread_id,))
            db.execute("INSERT INTO turns VALUES (?,?,?,'queued',NULL,NULL,?)",(ident,thread_id,question,time.time()))
            db.execute('INSERT INTO turn_runtime VALUES (?,?)',(ident,agent_runtime()))
            db.execute('INSERT INTO public_agent_questions VALUES (?,?,?)',(ident,hashlib.sha256(token.encode()).hexdigest(),time.time()+7*86400))
        return {'turn_id':ident,'status':'queued','speaker':dict(TWIN_SPEAKER),'result_token':token,'status_url':'/api/public/answers/'+ident,
                'poll_after_seconds':3,'note':'Keep result_token private. Poll status_url using the X-Result-Token header, or use the get_answer MCP tool. No account is needed.'}

    def answer(self, turn_id, result_token):
        with self.store.connect() as db:
            self._questions_table(db)
            row = db.execute('SELECT q.token_hash,q.expires,t.status,t.result FROM public_agent_questions q JOIN turns t ON t.id=q.turn_id WHERE q.turn_id=?',(turn_id,)).fetchone()
        if not row or row['expires'] < time.time() or not secrets.compare_digest(row['token_hash'],hashlib.sha256(result_token.encode()).hexdigest()):
            raise HTTPException(404, 'Answer not found.')
        return {'turn_id':turn_id,'status':row['status'],'speaker':dict(TWIN_SPEAKER),'answer':row['result'] if row['status']=='completed' else None,
                'poll_after_seconds':3 if row['status'] in ('queued','running') else None,
                'error':'The turn could not complete.' if row['status']=='failed' else None}
