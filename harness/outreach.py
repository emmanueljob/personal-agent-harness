"""Opaque outreach tags; the mapping lives in private runtime state, never Git."""
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from .paths import data_directory

FIELDS = ('utm_source', 'utm_campaign', 'utm_content')
TOKEN = re.compile(r't_[0-9a-f]{32}')


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
            row = db.execute('SELECT profile_key FROM outreach_tags WHERE token=? AND field=?', (value, field)).fetchone()
        return row[0] if row else None
    except sqlite3.Error:
        return None  # Missing/unavailable mapping fails closed to ordinary fallback.


def build_link(base, person='', company='', source='outreach'):
    """Generate fresh random aliases; names are private labels, never URL values."""
    from .personalization import resolve
    url = urlsplit(base)
    if url.scheme != 'https' or not url.netloc or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
        raise ValueError('Base must be an HTTPS origin without path, query or fragment')
    values = {'utm_source':source, 'utm_campaign':company, 'utm_content':person}
    aliases = {field:'t_'+secrets.token_hex(16) for field, value in values.items() if value}
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
    return base.rstrip('/')+'/?'+urlencode(aliases)


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
        db.execute('CREATE TABLE IF NOT EXISTS outreach_counts (day TEXT, event TEXT, source TEXT, campaign TEXT, content TEXT, target TEXT, count INTEGER NOT NULL, PRIMARY KEY(day,event,source,campaign,content,target))')
        db.execute('INSERT INTO outreach_counts VALUES (?,?,?,?,?,?,1) ON CONFLICT(day,event,source,campaign,content,target) DO UPDATE SET count=count+1',
                   (day, event.name, *[aliases.get(field, '') for field in FIELDS], event.target))


def report():
    path = database_path()
    if not path.is_file():
        return {'tags': [], 'counts': []}
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        tags = [dict(row) for row in db.execute('SELECT token,field,label,profile_key FROM outreach_tags ORDER BY created DESC LIMIT 1000')]
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='outreach_counts'").fetchone()
        counts = [dict(row) for row in db.execute('SELECT * FROM outreach_counts ORDER BY day DESC LIMIT 5000')] if exists else []
    return {'tags': tags, 'counts': counts}
