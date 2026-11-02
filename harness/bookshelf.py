"""Runtime-only inventory and explicit public snapshots. Imported text is data."""
import json
import os
import threading
import uuid
from pydantic import BaseModel, Field, field_validator

_lock = threading.RLock()

class SpineReference(BaseModel):
    url: str = Field(max_length=2000)
    source: str = Field(max_length=1000)
    width: int = Field(ge=1, le=10000)
    height: int = Field(ge=1, le=10000)
    points: list[tuple[float, float]] = Field(min_length=4, max_length=4)

    @field_validator('points')
    @classmethod
    def clockwise_corners(cls, points):
        # Image coordinates increase downward. Negative winding reflects the texture.
        area = sum(points[i][0] * points[(i + 1) % 4][1] - points[(i + 1) % 4][0] * points[i][1] for i in range(4))
        if abs(area) < 1e-6:
            raise ValueError('Spine corners must enclose an area')
        return [points[1], points[0], points[3], points[2]] if area < 0 else points

    @field_validator('url', 'source')
    @classmethod
    def safe_url(cls, value, info):
        from urllib.parse import urlparse
        import re
        if info.field_name == 'url' and re.fullmatch(r'/api/bookshelf/spine/[A-Za-z0-9_-]{1,80}', value):
            return value
        u = urlparse(value)
        allowed = ('images.pangobooks.com', 'images2.pangobooks.com', 'i.ebayimg.com')
        if u.scheme != 'https' or not u.hostname or u.username or u.password or (info.field_name == 'url' and u.hostname not in allowed):
            raise ValueError('Use an HTTPS spine reference from an approved image host')
        return value


class Book(BaseModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    title: str = Field(min_length=1, max_length=1000)
    author: str = Field(default='', max_length=500)
    source: str = Field(default='', max_length=200)
    cover: str = ''
    sample: bool = False
    spine: SpineReference | None = None
    status: str = Field(default='unknown', max_length=100)
    category: str = 'unclassified'
    comment: str = Field(default='', max_length=4000)
    comment_source: str = Field(default='', max_length=300)
    recommendation: str = Field(default='unknown', max_length=100)
    @field_validator('cover')
    @classmethod
    def cover_url(cls, value):
        from urllib.parse import urlparse
        u = urlparse(value)
        if value and (u.scheme != 'https' or u.hostname != 'm.media-amazon.com' or u.query or u.username or u.password):
            raise ValueError('Use a Kindle cover URL from m.media-amazon.com')
        return value
    @field_validator('category')
    @classmethod
    def category_value(cls, value):
        if value not in ('work', 'personal', 'unclassified'):
            raise ValueError('Unknown category')
        return value

class ImportInput(BaseModel):
    books: list[Book] = Field(max_length=5000)
    source: str = Field(default='Kindle browser library', max_length=200)
    complete: bool = False

class Selection(BaseModel):
    ids: list[str] = Field(max_length=5000)
    revision: str

class BookEdit(BaseModel):
    category: str
    status: str = Field(max_length=100)
    comment: str = Field(max_length=4000)
    comment_source: str = Field(default='Owner studio', max_length=300)
    recommendation: str = Field(default='unknown', max_length=100)

class Shelf:
    def __init__(self, brain):
        self.brain = brain
    def read(self, scope):
        return json.loads(self.brain.read(scope, 'bookshelf.json') or '{"books":[],"revision":"initial","selected":[]}')
    def write(self, scope, data):
        root = self.brain.scope(scope)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        dest = root / 'bookshelf.json'
        if dest.is_symlink():
            raise ValueError('Bookshelf cannot be a symbolic link')
        tmp = root / ('.bookshelf-' + uuid.uuid4().hex)
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(dest)
    def inventory(self):
        data = self.read('private')
        data['selected'] = [b['id'] for b in self.read('public')['books']]
        return data
    def ingest(self, body):
        with _lock:
            data = self.read('private')
            old = {b['id']: b for b in data['books']}
            for book in body.books:
                b = book.model_dump()
                b['source'] = b['source'] or body.source
                if b['id'] in old:
                    for k in ('category','comment','comment_source','status','recommendation','spine'):
                        b[k] = old[b['id']].get(k, b[k])
                old[b['id']] = b
            data.update(books=list(old.values()), source=body.source, complete=body.complete or data.get('complete', False), revision=uuid.uuid4().hex)
            self.write('private', data)
            return self.inventory()
    def edit(self, ident, body):
        with _lock:
            data = self.read('private')
            found = False
            for b in data['books']:
                if b['id'] == ident:
                    b.update(body.model_dump())
                    Book.model_validate(b)
                    found = True
            if not found:
                raise KeyError(ident)
            data['revision'] = uuid.uuid4().hex
            self.write('private', data)
            return self.inventory()
    def publish(self, selection):
        with _lock:
            data = self.read('private')
            if data['revision'] != selection.revision:
                raise ValueError('Inventory changed. Reload and review your selection.')
            by_id = {b['id']: b for b in data['books']}
            if len(selection.ids) != len(set(selection.ids)) or any(i not in by_id for i in selection.ids):
                raise ValueError('Selection includes duplicate or unknown books')
            public = {'books':[Book.model_validate(by_id[i]).model_dump() for i in selection.ids], 'revision':uuid.uuid4().hex}
            self.write('public', public)
            return public
