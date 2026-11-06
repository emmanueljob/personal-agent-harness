"""Clean outreach links and private attribution, stored outside Git."""
import os
import json
import re
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

from .paths import data_directory

FIELDS = ('utm_source', 'utm_campaign', 'utm_content')
TOKEN = re.compile(r't_[0-9a-f]{32}')
LINK_CODE = re.compile(r'[A-Za-z0-9_-]{16,64}')


def database_path():
    return Path(os.getenv('PORTFOLIO_LINKS_DB', str(data_directory() / 'outreach.sqlite'))).expanduser().resolve()


def lookup(value, field):
    if not TOKEN.fullmatch(value):
        return None
    path = database_path()
    if not path.is_file():
        return None
    try:
        with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=2) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='outreach_disabled_tags'").fetchone() and db.execute('SELECT 1 FROM outreach_disabled_tags WHERE token=?',(value,)).fetchone():
                return None
            row = db.execute('SELECT profile_key FROM outreach_tags WHERE token=? AND field=?', (value, field)).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None  # Missing/unavailable mapping fails closed to ordinary fallback.


def build_link(base, person='', company='', source='outreach'):
    """Generate a clean path; names and attribution fields stay in private storage."""
    from .personalization import resolve
    url = urlsplit(base)
    if url.scheme != 'https' or not url.netloc or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
        raise ValueError('Base must be an HTTPS origin without path, query or fragment')
    values = {'utm_source':source, 'utm_campaign':company, 'utm_content':person}
    aliases = {field:'t_'+secrets.token_hex(16) for field, value in values.items() if value}
    code = secrets.token_urlsafe(16)
    path = database_path()
    if path.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError('Outreach mappings must be stored outside the source repository')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Create with owner-only permissions before SQLite opens the file.
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    os.chmod(path, 0o600)
    with sqlite3.connect(path, timeout=5) as db:
        db.execute('CREATE TABLE IF NOT EXISTS outreach_tags (token TEXT PRIMARY KEY, field TEXT NOT NULL, profile_key TEXT NOT NULL, label TEXT NOT NULL, created REAL NOT NULL)')
        for field, token in aliases.items():
            # Resolve each level independently, preserving person > company > source.
            # Unknown person labels are just attribution, so company/source can win.
            profile = resolve({field: values[field]})
            db.execute('INSERT INTO outreach_tags VALUES (?,?,?,?,?)', (token, field, profile.key, values[field][:160], time.time()))
        ensure_links(db)
        db.execute('INSERT INTO outreach_links VALUES (?,?,?,?,1)', (code, json.dumps(aliases), json.dumps(values), time.time()))
    return base.rstrip('/')+'/r/'+code


def record_event(event):
    """Daily aggregate counts only: no IP, referrer, user agent or browser ID."""
    path = database_path()
    if not path.is_file():
        return  # Nothing to attribute until the owner registers an outreach link.
    aliases = {}
    for field in FIELDS:
        value = event.utm.get(field, '')
        # Include only registered codes; arbitrary/name-bearing tags are not stored.
        if lookup(value, field) is not None:
            aliases[field] = value
    if not aliases:
        return
    from datetime import datetime, timezone
    day = datetime.now(timezone.utc).date().isoformat()
    with sqlite3.connect(path, timeout=2) as db:
        db.execute('BEGIN IMMEDIATE')
        disabled_table = db.execute("SELECT 1 FROM sqlite_master WHERE name='outreach_disabled_tags'").fetchone()
        aliases = {field:value for field,value in aliases.items()
                   if db.execute('SELECT 1 FROM outreach_tags WHERE token=? AND field=?',(value,field)).fetchone()
                   and not (disabled_table and db.execute('SELECT 1 FROM outreach_disabled_tags WHERE token=?',(value,)).fetchone())}
        if not aliases:
            return
        db.execute('CREATE TABLE IF NOT EXISTS outreach_counts (day TEXT, event TEXT, source TEXT, campaign TEXT, content TEXT, target TEXT, count INTEGER NOT NULL, PRIMARY KEY(day,event,source,campaign,content,target))')
        db.execute('INSERT INTO outreach_counts VALUES (?,?,?,?,?,?,1) ON CONFLICT(day,event,source,campaign,content,target) DO UPDATE SET count=count+1',
                   (day, event.name, *[aliases.get(field, '') for field in FIELDS], event.target))


def report():
    path = database_path()
    if not path.is_file():
        return {'tags': [], 'counts': [], 'links': []}
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        disabled = {row[0] for row in db.execute('SELECT token FROM outreach_disabled_tags')} if db.execute("SELECT 1 FROM sqlite_master WHERE name='outreach_disabled_tags'").fetchone() else set()
        tags = [{**dict(row),'active':row['token'] not in disabled} for row in db.execute('SELECT token,field,label,profile_key FROM outreach_tags ORDER BY created DESC LIMIT 1000')]
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='outreach_counts'").fetchone()
        counts = [dict(row) for row in db.execute('SELECT * FROM outreach_counts ORDER BY day DESC LIMIT 5000')] if exists else []
        exists_links = db.execute("SELECT 1 FROM sqlite_master WHERE name='outreach_links'").fetchone()
        links = [{**dict(row),'tokens':json.loads(row['tokens']),'labels':json.loads(row['labels']), 'active':int(all(token not in disabled for token in json.loads(row['tokens']).values()))} for row in db.execute('SELECT * FROM outreach_links ORDER BY created DESC LIMIT 1000')] if exists_links else []
    return {'tags': tags, 'counts': counts, 'links':links}


def ensure_links(db):
    db.execute('CREATE TABLE IF NOT EXISTS outreach_links (id TEXT PRIMARY KEY,tokens TEXT NOT NULL,labels TEXT NOT NULL,created REAL NOT NULL,active INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS outreach_disabled_tags (token TEXT PRIMARY KEY)')


def set_link_active(ident, active):
    target = database_path()
    if not target.is_file():
        raise KeyError(ident)
    with sqlite3.connect(target, timeout=5) as db:
        ensure_links(db)
        row = db.execute('SELECT tokens FROM outreach_links WHERE id=?',(ident,)).fetchone()
        if not row:
            raise KeyError(ident)
        for token in json.loads(row[0]).values():
            if active:
                db.execute('DELETE FROM outreach_disabled_tags WHERE token=?',(token,))
            else:
                db.execute('INSERT OR IGNORE INTO outreach_disabled_tags VALUES (?)',(token,))
        db.execute('UPDATE outreach_links SET active=? WHERE id=?',(int(active),ident))


def set_tag_active(token, active):
    target = database_path()
    if not target.is_file() or not TOKEN.fullmatch(token):
        raise KeyError(token)
    with sqlite3.connect(target, timeout=5) as db:
        ensure_links(db)
        if not db.execute('SELECT 1 FROM outreach_tags WHERE token=?',(token,)).fetchone():
            raise KeyError(token)
        if active:
            db.execute('DELETE FROM outreach_disabled_tags WHERE token=?',(token,))
        else:
            db.execute('INSERT OR IGNORE INTO outreach_disabled_tags VALUES (?)',(token,))


def delete_links(*, ident=None, token=None, before=None):
    """Permanently remove selected links, private mappings and matching counts.

    A cutoff is for a cleanup snapshot: links created afterward survive. No other
    application data or site configuration is touched. All removal is atomic.
    """
    empty = {'links_deleted':0,'tags_deleted':0,'count_rows_deleted':0}
    target = database_path()
    if not target.is_file():
        if ident is not None or token is not None:
            raise KeyError('Link not found')
        return empty
    with sqlite3.connect(target, timeout=5) as db:
        db.execute('BEGIN IMMEDIATE')
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'outreach_tags' not in tables:
            if ident is not None or token is not None:
                raise KeyError('Link not found')
            return empty
        groups = [(row[0],set(json.loads(row[1]).values()),row[2]) for row in db.execute('SELECT id,tokens,created FROM outreach_links')] if 'outreach_links' in tables else []
        ids = set()
        if ident is not None:
            matching = [row for row in groups if row[0] == ident]
            if not matching:
                raise KeyError('Link not found')
            ids.add(ident)
            tokens = set(matching[0][1])
        elif token is not None:
            if not TOKEN.fullmatch(token) or not db.execute('SELECT 1 FROM outreach_tags WHERE token=?',(token,)).fetchone():
                raise KeyError('Tag not found')
            tokens = {token}
        else:
            tokens = {row[0] for row in (db.execute('SELECT token FROM outreach_tags') if before is None else db.execute('SELECT token FROM outreach_tags WHERE created<=?',(before,)))}
            ids = {row[0] for row in groups if before is None or row[2] <= before}
            for gid, members, _ in groups:
                if gid in ids:
                    tokens.update(members)
        # A tag belonging to a saved link removes the full link, not a broken row.
        while True:
            old_size = len(tokens),len(ids)
            for gid, members, _ in groups:
                if members & tokens:
                    ids.add(gid)
                    tokens.update(members)
            if old_size == (len(tokens),len(ids)):
                break
        result = dict(empty)
        values = list(tokens)
        for start in range(0,len(values),250):
            batch = values[start:start+250]
            marks = ','.join('?' for _ in batch)
            if 'outreach_counts' in tables:
                result['count_rows_deleted'] += db.execute(f'DELETE FROM outreach_counts WHERE source IN ({marks}) OR campaign IN ({marks}) OR content IN ({marks})',batch*3).rowcount
            result['tags_deleted'] += db.execute(f'DELETE FROM outreach_tags WHERE token IN ({marks})',batch).rowcount
            if 'outreach_disabled_tags' in tables:
                db.execute(f'DELETE FROM outreach_disabled_tags WHERE token IN ({marks})',batch)
        for gid in ids:
            result['links_deleted'] += db.execute('DELETE FROM outreach_links WHERE id=?',(gid,)).rowcount
        if ident is None and token is None and before is None:
            # Clear any historical orphan count/disabled rows as well.
            if 'outreach_counts' in tables:
                result['count_rows_deleted'] += db.execute('DELETE FROM outreach_counts').rowcount
            if 'outreach_disabled_tags' in tables:
                db.execute('DELETE FROM outreach_disabled_tags')
        return result


def link_tags(code):
    """Resolve a public clean-link code into private attribution fields."""
    if not LINK_CODE.fullmatch(code):
        return {}
    target = database_path()
    if not target.is_file():
        return {}
    try:
        with sqlite3.connect(target.as_uri()+'?mode=ro',uri=True,timeout=2) as db:
            db.execute('BEGIN')
            row = db.execute('SELECT tokens FROM outreach_links WHERE id=?',(code,)).fetchone()
            if not row:
                return {}
            disabled = {r[0] for r in db.execute('SELECT token FROM outreach_disabled_tags')}
            return {field:token for field,token in json.loads(row[0]).items() if field in FIELDS and token not in disabled
                    and db.execute('SELECT 1 FROM outreach_tags WHERE token=? AND field=?',(token,field)).fetchone()}
    except sqlite3.Error:
        return {}
