"""Representative synthetic reference scale, with exact matching and freshness."""
import json
import re
import time

import pytest

from harness.brain import FileBrain, external_score, external_title_match, matching_words
from harness.ingestion import normalize, upsert_batch, withdraw_sources, external_documents


@pytest.fixture
def corpus(tmp_path):
    brain = FileBrain(tmp_path/'brain')
    brain.set_profile('public', 'Synthetic published profile.\n' * 260)
    brain.set_profile('private', 'PRIVATE_CANARY ' * 3500)
    records = []
    total = 25_226_594
    for index in range(383):
        length = 165_989 if index == 0 else (total-165_989)//382
        text = ('Fictional archive prose on unrelated botany and weather. ' * (length//53+1))[:length]
        if index in (0, 382):
            text += '\n\nOrchard leadership favors reversible experiments.'
        records.append(normalize({
            'source_id': f'synthetic:{index}', 'title': f'Synthetic reference {index}',
            'source_url': f'https://example.org/source/{index}', 'text': text,
            'provenance': {'published': '2001-01-01' if index == 0 else '2026-01-01'},
        }, 'synthetic', scope='public'))
    upsert_batch(brain, records, 'synthetic', scope='public', apply=True, allow_public=True)
    for index in range(14):
        brain.add_note(f'Synthetic note {index}', 'Fictional preferences about small experiments. '*30, 'public')
    return brain, records


def test_representative_cold_and_warm_search_includes_old_and_new(corpus):
    brain, records = corpus
    timings = []
    for _ in range(2):
        start = time.monotonic()
        hits = brain.search('orchard leadership', 'public')
        timings.append(time.monotonic()-start)
        text = json.dumps(hits)
        assert 'PRIVATE_CANARY' not in text
        assert '2001-01-01' in text and '2026-01-01' in text
        assert all(hit['source'].startswith('public/') for hit in hits)
        assert all('NOT ESTABLISHED' in hit['text'] for hit in hits)
    print(f'Representative FileBrain cold/warm seconds {timings}')
    # All validated records remain visible to explicit full export/index callers.
    assert len(external_documents(brain, 'public')) > 5_000


def test_edits_new_records_withdrawals_and_corruption_are_current(corpus):
    brain, records = corpus
    assert brain.search('orchard', 'public')
    changed = normalize({
        'source_id': records[0]['source_id'], 'title': 'Corrected synthetic source',
        'source_url': 'https://example.org/correction', 'text': 'New zirconiumbadger evidence.',
    }, 'synthetic', scope='public')
    upsert_batch(brain, [changed], 'synthetic', scope='public', apply=True, allow_public=True)
    assert 'zirconiumbadger' in json.dumps(brain.search('zirconiumbadger', 'public'))
    withdrawn = records[-1]['source_id']
    withdraw_sources(brain, [withdrawn], 'synthetic', scope='public', apply=True, allow_public=True)
    assert brain.search('orchard', 'public') == []
    new = normalize({
        'source_id': 'synthetic:new', 'title': 'Newly published fictional evidence',
        'source_url': 'https://example.org/new', 'text': 'Orchard latest factual source.',
    }, 'synthetic', scope='public')
    upsert_batch(brain, [new], 'synthetic', scope='public', apply=True, allow_public=True)
    assert 'latest factual source' in brain.search('orchard', 'public')[0]['text']
    path = next((brain.scope('public')/'references'/'synthetic').glob('*.json'))
    record = json.loads(path.read_text())
    record['text'] += ' Unreviewed corrupt bytes.'
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='integrity'):
        brain.search('orchard', 'public')


@pytest.mark.parametrize('query', ['orchard leadership', 'CAFÉ résumé', 'foo_bar 42', 'external source data', '001 excerpt'])
def test_optimized_word_scores_equal_original(query):
    tokens = set(re.findall(r'\w+', query.lower()))
    body = '# External reference: Café résumé\n\nOrchard leadership, café foo_bar 42; external source data.\n\n001 excerpt'
    title = body.split('\n', 1)[0].removeprefix('# External reference: ').lower()
    expected = (len(tokens & set(re.findall(r'\w+', body.lower())))
                + 5*len(tokens & set(re.findall(r'\w+', title)))
                + (60 if external_title_match(body, query) else 0))
    assert external_score(body, tokens, query) == expected
    assert matching_words(body.lower(), tokens) == tokens & set(re.findall(r'\w+', body.lower()))


def test_streamed_references_discard_concurrent_changes_and_reject_symlinks(tmp_path, monkeypatch):
    from harness import ingestion
    brain = FileBrain(tmp_path/'brain')
    record = normalize({'source_id': 'synthetic:changed', 'title': 'Synthetic orchard source',
                        'source_url': 'https://example.org/source', 'text': 'Orchard original evidence.'},
                       'synthetic', scope='public')
    upsert_batch(brain, [record], 'synthetic', scope='public', apply=True, allow_public=True)
    original = ingestion.iter_external_documents
    def changed(*args, **kwargs):
        yield from original(*args, **kwargs)
        withdraw_sources(brain, [record['source_id']], 'synthetic', scope='public', apply=True, allow_public=True)
    monkeypatch.setattr(ingestion, 'iter_external_documents', changed)
    assert brain.search('orchard', 'public') == []
    monkeypatch.setattr(ingestion, 'iter_external_documents', original)
    secret = tmp_path/'private.json'
    secret.write_text('PRIVATE_CANARY')
    (brain.scope('public')/'references'/'synthetic'/'symlink.json').symlink_to(secret)
    with pytest.raises(ValueError, match='regular files'):
        brain.search('orchard', 'public')


@pytest.mark.parametrize('text', [
    'partialart end_art artist art. 中文 café résumé Ⅷ 42',
    'artificial ' * 1500 + ' art ',
    'artificial ' * 1500,
    'İstanbul i\u0307 ı foo_bar _bar foo42',
])
def test_query_word_matching_preserves_unicode_prefixes_and_long_queries(text):
    text = text.lower()
    tokens = set(re.findall(r'\w+', text)) | {'art', 'end', 'foo', 'bar', '42', 'i'}
    assert matching_words(text, tokens) == tokens & set(re.findall(r'\w+', text))
    long_query = tokens | {f'word{index}' for index in range(30)}
    assert matching_words(text, long_query) == long_query & set(re.findall(r'\w+', text))
