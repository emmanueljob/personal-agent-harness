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
    books = [h for h in unique if book_source(h)]
    if books and not any(book_source(h) for h in selected):
        replace = next((i for i in range(len(selected)-1,-1,-1)
                        if selected[i].get('knowledge_role') not in ('owner_perspective','owner_learning')),None)
        if replace is not None:
            selected[replace] = books[0]
    # Preserve complete owner evidence first, then books before long transcripts.
    return sorted(selected,key=lambda h:(0 if h.get('knowledge_role') in ('owner_perspective','owner_learning') else 1 if book_source(h) else 2, -h['score']))
