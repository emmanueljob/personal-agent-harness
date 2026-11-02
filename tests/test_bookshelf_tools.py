import json
from harness.store import Store
from harness.bookshelf import Shelf
from harness.bookshelf_tools import bookshelf_tools


def setup_shelf(tmp_path):
    store=Store(tmp_path)
    shelf=Shelf(store.brain)
    shelf.write('public',{'books':[{'id':'public-book','title':'No Rules Rules','author':'Hastings','category':'work','comment':'Trust and autonomy','comment_source':'Interview'}]})
    shelf.write('private',{'books':[{'id':'secret-book','title':'Private title','author':'Private author'}]})
    tid=store.thread('public')['id']; turn=store.enqueue(tid,'show books')
    tool=bookshelf_tools({'store':store,'turn_id':turn,'mode':'public'})[0]
    return store,shelf,tid,turn,tool


def test_selection_is_published_only_and_durable(tmp_path):
    store,_,_,turn,tool=setup_shelf(tmp_path)
    result=json.loads(tool.invoke({'query':'No Rules Rules'}))
    assert result['ui_action']['book_ids']==['public-book']
    assert result['books'][0]['comment_source']=='Interview'
    assert 'Private title' not in json.dumps(result)
    assert store.query('select body from tool_results where turn_id=?',(turn,))
    assert json.loads(tool.invoke({'book_ids':['secret-book']}))['status']=='error'
    assert json.loads(tool.invoke({'book_ids':['public-book']*9}))['status']=='error'


def test_old_actions_revalidate_current_publication(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from harness import app
    store,shelf,tid,_,tool=setup_shelf(tmp_path)
    tool.invoke({'query':'Rules'})
    monkeypatch.setattr(app,'store',store)
    client=TestClient(app.app)
    assert client.get('/api/threads/'+tid).json()['tool_calls'][0]['books'][0]['id']=='public-book'
    shelf.write('public',{'books':[]})
    data=client.get('/api/threads/'+tid).json()
    assert data['tool_calls'][0]['books']==[]
    assert data['tool_calls'][0]['ui_action']['book_ids']==[]
