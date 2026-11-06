"""Read the published shelf and request bounded, replayable UI selections."""
import json
import re
from langchain.tools import tool
from .bookshelf import Shelf
from .media_library import published_books


def bookshelf_tools(ctx):
    @tool
    def browse_bookshelf(query: str = "", book_ids: list[str] | None = None) -> str:
        """Show books on the visitor's interactive shelf. Search published titles, authors, categories and attributed comments using short keywords; empty query lists a sample. To show a specific selection, pass up to 8 exact book_ids from earlier results. Returns actual book IDs and a saved UI selection; never changes the library or publishes books. Use this when asked to show, find or pull out books. Library presence does not imply reading or recommendation."""
        books = published_books(ctx['store'].brain)
        if len(query) > 200 or (book_ids is not None and (not book_ids or len(book_ids) > 8)):
            return json.dumps({'status':'error','message':'Use a short query or one to eight published book IDs.'})
        if book_ids is not None:
            allowed = {b['id']: b for b in books}
            if any(ident not in allowed for ident in book_ids):
                return json.dumps({'status':'error','message':'Selection must use current published book IDs.'})
            matches = [allowed[ident] for ident in dict.fromkeys(book_ids)]
        else:
            terms = set(re.findall(r'\w+', query.lower())) - {'the','a','an','and','of','book','books','show','me','about'}
            ranked = []
            for book in books:
                text = ' '.join(str(book.get(k,'')) for k in ('title','author','category','comment')).lower()
                score = sum(term in text for term in terms)
                if not terms or score:
                    ranked.append((score,book))
            matches = [b for _,b in sorted(ranked,key=lambda item:-item[0])[:8]]
        result = {'status':'ok','books':[{k:b.get(k,'') for k in ('id','title','author','sample','status','comment','comment_source','recommendation')} for b in matches],
                  'ui_action': {'type':'bookshelf_selection','book_ids':[b['id'] for b in matches]},
                  'note':'The page displays these books. Personal opinions require attributed notes; membership alone is not a recommendation.'}
        arguments = json.dumps({'query':query,'book_ids':book_ids},sort_keys=True)
        # Bounded evidence, with full canonical notes remaining in the external brain.
        for book in result['books']:
            book['comment'] = book['comment'][:600]
        with ctx['store'].connect() as db:
            db.execute('INSERT OR REPLACE INTO tool_results VALUES (?,?,?,?)',(ctx['turn_id'],'browse_bookshelf',arguments,json.dumps(result)))
        return json.dumps(result)
    return [browse_bookshelf]
