"""Keep relevant book knowledge visible beside a larger podcast corpus."""
def book_source(hit):
    source = hit['source']
    return '/bookshelf.json#' in source or ('/wiki/influences/' in source and not any(n in source for n in ('agile-manifesto','pull-framework','working-influences')))


def rank_knowledge(hits, limit=5):
    ordered = sorted(hits,key=lambda h:-h['score'])
    unique = []
    seen = set()
    for hit in ordered:
        # Several excerpts from one transcript must not crowd out other sources.
        key = hit['source'].split('#chunk-',1)[0]
        if key not in seen:
            seen.add(key)
            unique.append(hit)
    selected = unique[:limit]
    # A large transcript corpus must not displace every relevant personal note.
    personal = [h for h in unique if personal_source(h)]
    if limit >= 2 and personal and not any(personal_source(h) for h in selected):
        selected[-1] = personal[0]
    books = [h for h in unique if book_source(h)]
    if limit >= 2 and books and not any(book_source(h) for h in selected):
        replace = next((i for i in range(len(selected)-1,-1,-1)
                        if not personal_source(selected[i])),None)
        if replace is not None:
            selected[replace] = books[0]
    # Preserve complete owner evidence first, then books before long transcripts.
    return sorted(selected,key=lambda h:(0 if h.get('knowledge_role') in ('owner_perspective','owner_learning') else 1 if personal_source(h) else 2 if book_source(h) else 3, -h['score']))


def personal_source(hit):
    source = hit['source']
    return (hit.get('knowledge_role') in ('owner_perspective', 'owner_learning')
            or ('/wiki/' in source and ('/influences/' not in source or '/working-influences' in source)))


def grounding_query(prompt, previous=(), names=''):
    """Resolve short follow-ups using user questions, never generated answers."""
    import re
    tokens = re.findall(r'\w+', prompt.lower())
    filler = set('what who why can could would will do does is are you your me my he his him she her it its this that those these about of for in on to and the a an tell give say please more else expand elaborate explain detail details really then know yourself introduce introduction overview biography background apply mean example examples'.split())
    subjects = set(re.findall(r'\w+', names.lower())) | {'owner'}
    substantive = set(tokens) - filler - subjects
    if not substantive:
        if set(tokens) & (subjects | {'who', 'yourself', 'introduction', 'overview', 'biography'}) or not previous:
            return 'work strengths leadership interests working influences'
        if previous:
            earlier = next((str(row['prompt']) for row in reversed(previous[-3:]) if set(re.findall(r'\w+', str(row['prompt']).lower())) - filler - subjects), str(previous[-1]['prompt']))
            return earlier[:800] + '\nFollow-up: ' + prompt[:200]
    return prompt


def evidence(hit, text):
    """Preserve identity and attribution alongside bounded evidence text."""
    import json
    import re
    from urllib.parse import urlsplit
    body = hit['text']
    title = hit.get('title')
    heading = re.search(r'^#\s+(?:External reference:\s*)?(.+)', body, re.M)
    if not title and heading:
        title = heading[1]
    citation_url = ''
    role = hit.get('knowledge_role') or ('external_reference' if '/references/' in hit['source'] else 'published_note')
    basis = hit.get('basis', '')
    # Structured annotations and provenance carry their own original title/URL.
    chunks = body.split('\n\n')
    for chunk in chunks:
        payload = chunk.removeprefix('Provenance: ').strip()
        if not payload.startswith('{'):
            continue
        try:
            record = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        title = record.get('title') or title
        basis = record.get('basis') or basis
        candidate = record.get('source_url') or record.get('url') or ''
        try:
            parsed = urlsplit(candidate)
            if (parsed.scheme in ('https', 'http') and parsed.hostname and not parsed.username and not parsed.password) or candidate.startswith('/?'):
                citation_url = candidate
        except (ValueError, AttributeError):
            pass
    if not title:
        title = hit['source'].rsplit('/', 1)[-1].split('#')[0].removesuffix('.md')
        title = re.sub(r'-[a-f0-9]{8}$', '', title).replace('-', ' ')
    return dict(source=hit['source'], title=str(title)[:200], knowledge_role=role, basis=basis,
                citation=dict(label=str(title)[:200], url=citation_url), text=text)
