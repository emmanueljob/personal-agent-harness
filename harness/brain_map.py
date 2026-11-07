"""A derived, public-only knowledge map. Never changes source visibility."""
import hashlib
import json
import re
from urllib.parse import urlsplit

from .media_library import catalog

TOPICS = (
    ('agents', 'AI & agents', ('ai', 'agent', 'agents', 'llm', 'llms', 'claude', 'models', 'prompt', 'prompts', 'automation')),
    ('building', 'Product & craft', ('product', 'products', 'startup', 'startups', 'design', 'customer', 'customers', 'founder', 'founders', 'growth', 'craft', 'validation')),
    ('teams', 'People & leadership', ('leadership', 'leader', 'leaders', 'team', 'teams', 'culture', 'management', 'hiring', 'ownership', 'feedback', 'trust')),
    ('engineering', 'Engineering & systems', ('engineering', 'software', 'code', 'coding', 'architecture', 'systems', 'infrastructure', 'data', 'developer', 'developers', 'testing')),
    ('life', 'Work & life', ('work', 'career', 'interests', 'learning', 'personal', 'life', 'creativity', 'writing', 'reading', 'music', 'travel')),
)


def safe_url(value):
    if not isinstance(value, str):
        return ''
    if len(value) > 2000 or any(ord(c) < 32 for c in value):
        return ''
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ''
    if parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password:
        return value
    if value.startswith('/?'):
        return value
    return ''


def plain(value):
    value = re.sub(r'\[([^]]+)\]\([^)]+\)', r'\1', str(value))
    return ' '.join(re.sub(r'[#*_`>]', '', value).split())


def snippet(value):
    text = plain(value)
    return text if len(text) <= 440 else text[:440].rsplit(' ', 1)[0] + '…'


def public_map(brain):
    sources = []
    root = brain.scope('public')
    wiki = root / 'wiki'
    if wiki.exists() and (wiki.is_symlink() or not wiki.resolve().is_relative_to(root.resolve())):
        raise ValueError('Public wiki must stay inside its knowledge scope.')
    for path in sorted(wiki.rglob('*.md')):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or path.stat().st_size > 2_000_000:
            continue
        body = path.read_text()[:60000]
        title = re.search(r'^#\s+(.+)', body, re.M)
        title = plain(title[1]) if title else path.stem.replace('-', ' ')
        paragraphs = [p for p in body.split('\n\n') if p.strip() and not p.lstrip().startswith(('#', 'Source:', 'Added:', 'Visibility:'))]
        source = 'public/' + path.relative_to(root).as_posix()
        sources.append(dict(id='note:'+hashlib.sha256(source.encode()).hexdigest()[:20], kind='note',
                            title=title[:180], author='', excerpt=snippet(' '.join(paragraphs)),
                            source=source, url='', annotated=False, basis='published_note',
                            _match=title+' '+body))
    for row in catalog(brain, mode='public'):
        owner_note = row['note'] or (row['comment'] if row['has_attributed_comment'] else '')
        sources.append(dict(id=row['kind']+':'+row['id'], kind=row['kind'], title=row['title'][:180],
                            author=row.get('author', '')[:140], excerpt=snippet(owner_note),
                            source=row.get('note_source') or 'Published '+row['kind']+' catalog',
                            url=safe_url(row.get('site_url') or row.get('url')),
                            annotated=bool(owner_note.strip()), basis='owner_annotation' if row['has_owner_note'] else 'attributed_comment' if owner_note.strip() else 'external_reference',
                            _match=' '.join([row['title'], owner_note, *row.get('tags', [])])))
    data = json.loads(brain.read('public', 'perspectives.json') or '{"entries":[]}')
    for entry in data.get('entries', []):
        if entry.get('basis') not in ('owner_confirmed', 'inferred'):
            continue
        topics = [str(t) for t in entry.get('topics', [])]
        view = str(entry.get('view', ''))
        sources.append(dict(id='perspective:'+str(entry['id']), kind='perspective',
                            title=plain(' · '.join(topics) or 'Published perspective')[:180], author='',
                            excerpt=snippet(view), source='public/perspectives.json#'+str(entry['id']),
                            url='', annotated=False, basis=entry['basis'], _match=' '.join(topics)+' '+view))
    sources.sort(key=lambda s:(s['kind'] not in ('note', 'perspective'), not s['annotated'], s['title'].lower(), s['id']))
    # Bound a derived response, without copying transcripts or raw source material.
    truncated = len(sources) > 2000
    sources = sources[:2000]
    for source in sources:
        tokens = set(re.findall(r'\w+', source.pop('_match').lower()))
        links = []
        for ident, _, words in TOPICS:
            matched = sorted(tokens.intersection(words))
            if matched:
                links.append(dict(topic_id=ident, terms=matched, basis='keyword_match'))
        source['connections'] = links or [dict(topic_id='other', terms=[], basis='uncategorized')]
    topics = [dict(id=ident, title=title, count=sum(any(c['topic_id']==ident for c in s['connections']) for s in sources))
              for ident, title, _ in (*TOPICS, ('other', 'Other threads', ()))]
    return dict(scope='public', sources=sources, topics=topics, truncated=truncated,
                totals={kind:sum(s['kind']==kind for s in sources) for kind in ('note','perspective','book','podcast')},
                annotated=sum(s['annotated'] for s in sources), connection_basis='keyword_match')
