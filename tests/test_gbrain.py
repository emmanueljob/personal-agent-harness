import json
import os
from pathlib import Path

import pytest

from harness.gbrain import GBrain, GBrainError


class IndexedBrain(GBrain):
    """Exercise synchronization and retrieval policy without requiring a binary."""
    def __init__(self, root):
        super().__init__(root)
        self.calls = []
        self.pages = {"public": {}, "private": {}}
        for mode in self.pages:
            config = self.index_root / mode / '.gbrain/config.json'
            config.parent.mkdir(parents=True)
            config.write_text('{}')

    def _run(self, visibility, args, body=None):
        self.calls.append((visibility, args[0]))
        pages = self.pages[visibility]
        if args[0] == 'put':
            pages[args[1]] = body
            return {'state': 'committed'}
        if args[0] == 'delete':
            pages.pop(args[1], None)
            return {'state': 'committed'}
        # Include unrecognized stale records to exercise the live allowlist.
        return [{'slug': slug, 'score': 1} for slug in [*pages, 'unmanaged-secret']]


def test_scopes_updates_and_withdrawals(tmp_path):
    b = IndexedBrain(tmp_path / 'brain')
    b.set_profile('public', 'Public orchard')
    b.set_profile('private', 'Private violet secret')
    b.add_note('Owner note', 'Only the owner knows violet')
    assert all(x['source'].startswith('public/') for x in b.search('orchard', 'public'))
    assert all(scope == 'public' for scope, _ in b.calls)
    assert 'secret' not in json.dumps(b.pages['public'])
    assert any(x['source'].startswith('private/') for x in b.search('violet', 'private'))
    puts = sum(op == 'put' for _, op in b.calls)
    b.search('orchard', 'public')
    assert sum(op == 'put' for _, op in b.calls) == puts
    b.set_profile('public', 'Replacement plum')
    assert 'Replacement plum' in b.search('plum', 'public')[0]['text']
    b.set_profile('public', 'Public orchard')
    assert 'Public orchard' in b.search('orchard', 'public')[0]['text']
    p = b.scope('public') / 'profile.md'
    p.unlink()
    assert b.search('orchard', 'public') == []
    assert b.pages['public'] == {}


def test_curation_change_during_search_fails_closed(tmp_path, monkeypatch):
    b = IndexedBrain(tmp_path / 'brain')
    b.set_profile('public', 'Withdrawn violet')
    run = b._run
    def revoke(mode, args, body=None):
        result = run(mode, args, body)
        if args[0] == 'search':
            (b.scope('public') / 'profile.md').unlink()
        return result
    monkeypatch.setattr(b, '_run', revoke)
    assert b.search('violet', 'public') == []


def test_keyless_environment_and_failure_have_no_private_output(tmp_path, monkeypatch):
    import subprocess
    b = IndexedBrain(tmp_path / 'brain')
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-inherit')
    monkeypatch.setenv('GBRAIN_SOURCE', 'private')
    def failed(args, **kwargs):
        assert 'OPENAI_API_KEY' not in kwargs['env']
        assert 'GBRAIN_SOURCE' not in kwargs['env']
        assert kwargs['env']['GBRAIN_HOME'].endswith('/public')
        return subprocess.CompletedProcess(args, 1, 'private-source-text', 'private-error')
    monkeypatch.setattr(subprocess, 'run', failed)
    with pytest.raises(GBrainError) as err:
        GBrain._run(b, 'public', ['search', 'example', '--json'])
    assert 'private' not in str(err.value)


def test_store_provider_selection_preserves_file_operations(tmp_path, monkeypatch):
    from harness.store import Store
    monkeypatch.setenv('BRAIN_BACKEND', 'gbrain')
    s = Store(tmp_path)
    assert isinstance(s.brain, GBrain)
    s.set_profile('public', 'Approved profile')
    assert s.profile('public') == 'Approved profile'
    assert s.brain.public_portfolio() == {}
    monkeypatch.setenv('BRAIN_BACKEND', 'files')
    assert Store(tmp_path).profile('public') == 'Approved profile'
    monkeypatch.setenv('BRAIN_BACKEND', 'unknown')
    with pytest.raises(ValueError):
        Store(tmp_path)


@pytest.mark.skipif(not os.getenv('GBRAIN_TEST_BINARY'), reason='Opt-in real local GBrain integration')
def test_real_gbrain_index_and_visibility(tmp_path):
    import subprocess
    binary = str(Path(os.environ['GBRAIN_TEST_BINARY']).resolve())
    b = GBrain(tmp_path / 'brain', binary=binary)
    for mode in ('public', 'private'):
        home = b.index_root / mode
        home.mkdir(parents=True)
        env = {k: v for k, v in os.environ.items() if k in ('PATH','HOME','USER','TMPDIR','LANG')}
        env['GBRAIN_HOME'] = str(home)
        subprocess.run([binary, 'init', '--pglite', '--no-embedding', '--db-only', '--non-interactive', '--search-mode', 'conservative', '--json'], cwd=home, env=env, check=True, capture_output=True, timeout=90)
    b.set_profile('public', '# Public\n\nOrchard leadership and prototypes.')
    b.set_profile('private', '# Private\n\nViolet zeppelin is confidential.')
    assert any('Orchard' in x['text'] for x in b.search('orchard prototypes','public'))
    assert b.search('violet zeppelin', 'public') == []
    assert any(x['source'].startswith('private/') for x in b.search('violet zeppelin','private'))
    b.set_profile('public', '# Public\n\nNew plum direction.')
    assert b.search('orchard', 'public') == []
    assert b.search('plum', 'public')
    (b.scope('public')/'profile.md').unlink()
    assert b.search('plum', 'public') == []
