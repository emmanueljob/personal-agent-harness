import json
from pathlib import Path

import pytest

from harness.brain import FileBrain
from harness.brain_map import public_map, safe_url


def write(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if isinstance(value, dict) else value)


def test_map_is_derived_from_public_sources_and_marks_keyword_edges(tmp_path):
    write(tmp_path, 'public/wiki/agents.md', '# Building AI agents\n\nNotes on software architecture and product teams.')
    write(tmp_path, 'private/wiki/secret.md', '# PRIVATE_SENTINEL\n\nNever publish this.')
    write(tmp_path, 'public/bookshelf.json', {'books': [dict(id='book-1', title='A book about leadership', author='Example Author')]})
    write(tmp_path, 'public/learning.json', {'version':1, 'entries':[dict(id='book-book-1',kind='book',target_id='book-1',title='A book about leadership',author='Example Author',url='',note='A useful idea about trust.',tags=[],experience='read',state='active',basis='owner_confirmed')]})
    write(tmp_path, 'public/perspectives.json', {'entries':[dict(id='systems',basis='inferred',topics=['engineering'],view='Prefer explicit systems boundaries.')]})
    result = public_map(FileBrain(tmp_path))
    serialized = json.dumps(result)
    assert result['scope'] == 'public'
    assert 'PRIVATE_SENTINEL' not in serialized
    assert result['totals'] == dict(note=1, perspective=1, book=1, podcast=0)
    assert result['annotated'] == 1
    book = next(s for s in result['sources'] if s['kind']=='book')
    assert book['basis'] == 'owner_annotation'
    assert any(c['topic_id']=='teams' and 'trust' in c['terms'] for c in book['connections'])
    assert all(c['basis']=='keyword_match' for c in book['connections'])
    assert all('_match' not in s for s in result['sources'])
    assert next(s for s in result['sources'] if s['kind']=='perspective')['basis'] == 'inferred'
    assert public_map(FileBrain(tmp_path)) == result


def test_public_map_never_enriches_from_private_annotations(tmp_path):
    write(tmp_path, 'public/bookshelf.json', {'books':[dict(id='shared',title='Field guide',author='A. Writer')]})
    write(tmp_path, 'private/learning.json', {'version':1,'entries':[dict(id='book-shared',kind='book',target_id='shared',note='PRIVATE_ANNOTATION',state='active',basis='owner_confirmed')]})
    result = public_map(FileBrain(tmp_path))
    row = result['sources'][0]
    assert row['annotated'] is False and row['basis'] == 'external_reference'
    assert row['excerpt'] == '' and row['connections'][0]['topic_id'] == 'other'
    assert 'PRIVATE_ANNOTATION' not in json.dumps(result)


def test_map_ignores_symlinked_notes_and_rejects_symlinked_wiki(tmp_path):
    write(tmp_path, 'private/secret.md', '# PRIVATE_SYMLINK')
    wiki = tmp_path/'public/wiki'
    wiki.mkdir(parents=True)
    (wiki/'escape.md').symlink_to(tmp_path/'private/secret.md')
    assert public_map(FileBrain(tmp_path))['sources'] == []
    (wiki/'escape.md').unlink()
    wiki.rmdir()
    wiki.symlink_to(tmp_path/'private', target_is_directory=True)
    with pytest.raises(ValueError, match='knowledge scope'):
        public_map(FileBrain(tmp_path))


def test_podcast_transcripts_do_not_become_map_text_or_owner_views(tmp_path):
    from harness.ingestion import normalize, upsert_batch
    brain = FileBrain(tmp_path)
    # Use the canonical importer, with a synthetic external transcript.
    record = normalize(dict(source_id='podcast:example:episode-1',title='Birdsong',text='TRANSCRIPT_SENTINEL AI agents engineering',source_url='https://example.org/episode',source_kind='podcast',provenance={'show':'Example show'}), 'podcasts','public')
    upsert_batch(brain, [record], 'podcasts', 'public', apply=True, allow_public=True)
    result = public_map(brain)
    row = result['sources'][0]
    assert row['kind'] == 'podcast' and row['basis'] == 'external_reference'
    assert row['excerpt'] == '' and row['connections'][0]['topic_id'] == 'other'
    assert 'TRANSCRIPT_SENTINEL' not in json.dumps(result)


@pytest.mark.parametrize('url', ['javascript:alert(1)','//example.org','https://user:secret@example.org','file:///private/note'])
def test_unsafe_source_links_are_not_emitted(url):
    assert safe_url(url) == ''
