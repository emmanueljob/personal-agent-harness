import json

from fastapi.testclient import TestClient

from harness import activity_duplicates, crm
from harness.store import Store
from test_admin import admin_app, AUTH
from test_crm import add, company, source


def activity(store, company_id, **fields):
    return add(store, {'kind':'activity', 'company_id':company_id, 'title':'Interview follow-up',
                       'channel':'email', 'stage':'sent', 'happened_on':'2026-11-01', **fields})


def pairs(store, **options):
    with store.connect() as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        return activity_duplicates.preview(db, **options)


def pair_for(report, *ids):
    return next(pair for pair in report['pairs'] if {member['id'] for member in pair['members']} == set(ids))


def dump(store):
    with store.connect() as db:
        return list(db.iterdump())


def test_current_pairs_have_reasons_and_event_counterexamples(tmp_path):
    store = Store(tmp_path)
    co = add(store, company())
    person = add(store, {'kind':'person', 'company_id':co, 'name':'Synthetic Person'})
    first_app = add(store, {'kind':'application', 'company_id':co, 'title':'Platform Lead'})
    other_app = add(store, {'kind':'application', 'company_id':co, 'title':'Infrastructure Lead'})
    fields = {'person_id':person, 'application_id':first_app}
    first = activity(store, co, **fields)
    repeated = activity(store, co, title='Interview follow up', **fields)
    other_application = activity(store, co, person_id=person, application_id=other_app)
    other_date = activity(store, co, happened_on='2026-11-02', **fields)
    other_company = activity(store, add(store, company('Another Company')))
    other_person = activity(store, co, person_id=add(store, {'kind':'person', 'company_id':co, 'name':'Another Person'}))
    other_channel = activity(store, co, channel='call', **fields)
    other_stage = activity(store, co, stage='drafted', **fields)
    before = dump(store)
    result = pairs(store)
    assert pairs(store) == result
    assert dump(store) == before
    keys = [tuple(member['id'] for member in pair['members']) for pair in result['pairs']]
    assert len(keys) == len(set(keys)) == 6
    duplicate = pair_for(result, first, repeated)
    assert duplicate['assessment'] == 'possible_duplicate'
    assert set(duplicate['matched_fields']) == set(activity_duplicates.IDENTITY_FIELDS) | {'happened_on'}
    assert duplicate['different_fields'] == []
    assert duplicate['import_identity_match']
    different = pair_for(result, first, other_application)
    assert different['assessment'] == 'distinct_events'
    assert different['different_fields'] == ['application_id']
    assert different['import_identity_match']  # The existing importer omits application identity.
    dated = pair_for(result, first, other_date)
    assert dated['assessment'] == 'distinct_events' and not dated['import_identity_match']
    assert dated['different_fields'] == ['happened_on']
    assert not {other_company, other_person, other_channel, other_stage}.intersection(
        member['id'] for pair in result['pairs'] for member in pair['members'])
    for member in different['members']:
        assert member['relationships']['application_id'] in ('Platform Lead', 'Infrastructure Lead')


def test_missing_date_is_explicit_and_pagination_is_stable(tmp_path):
    store = Store(tmp_path)
    co = add(store, company())
    first = activity(store, co, happened_on=None)
    second = activity(store, co, happened_on=None)
    activity(store, co)
    full = pairs(store)
    missing = pair_for(full, first, second)
    assert missing['assessment'] == 'needs_review'
    assert 'happened_on' in missing['missing_fields']
    assert 'happened_on' not in missing['matched_fields']
    assert missing['import_identity_match']
    pages = [pairs(store, offset=offset, limit=1) for offset in range(3)]
    assert [page['pairs'][0] for page in pages] == full['pairs']
    assert [page['next_offset'] for page in pages] == [1, 2, None]
    assert pairs(store, offset=100)['pairs'] == []


def test_history_aliases_and_distinct_evidence_are_attributed_without_writes(tmp_path, monkeypatch):
    store = Store(tmp_path)
    co = add(store, company())
    first = activity(store, co)
    second = activity(store, co)
    alias = activity(store, co, title='Previous interview label')
    sid = source(store, report='Synthetic interview reference.')
    other_sid = crm.save_source(store, crm.Source(provider='muse', reference='fixture-2',
                                title='Second synthetic report', report='Separate synthetic receipt.'))
    monkeypatch.setattr('harness.outreach.report', lambda: {'links':[{'id':'link-one'}, {'id':'link-two'}]})
    with store.connect() as db:
        db.execute('INSERT INTO crm_redirects VALUES (?,?)', (alias, second))
        body = json.loads(db.execute('SELECT body FROM crm_records WHERE id=?', (second,)).fetchone()['body'])
        body.update(title='Owner corrected the interview label', link_id='link-two',
                    urls=['https://example.net/receipt-two'])
        crm.write_record(db, body, second, 1, source_id=other_sid, evidence='Separate synthetic receipt.')
        body = json.loads(db.execute('SELECT body FROM crm_records WHERE id=?', (first,)).fetchone()['body'])
        body.update(link_id='link-one', urls=['https://example.net/receipt-one'])
        crm.write_record(db, body, first, 1, source_id=sid, evidence='Synthetic interview reference.')
    before = dump(store)
    result = pairs(store)
    assert len(result['pairs']) == 1
    pair = result['pairs'][0]
    assert 'link_id' in pair['different_fields'] and 'urls' in pair['different_fields']
    assert any(basis['origin']=='history' for basis in pair['match_basis'])
    members = {member['id']:member for member in pair['members']}
    assert members[second]['aliases'] == [alias]
    assert alias not in members
    assert members[first]['record']['link_id'] == 'link-one'
    assert any(entry['source_id']==sid and entry['evidence']=='Synthetic interview reference.'
               for entry in members[first]['provenance'])
    assert any(entry['record_id']==alias and entry['origin']=='alias'
               for entry in members[second]['provenance'])
    assert pairs(store) == result and dump(store) == before


def test_route_is_private_read_only_and_works_before_crm_initialization(tmp_path, monkeypatch):
    module, store = admin_app(tmp_path, monkeypatch)
    route = '/api/admin/crm/activity-duplicates'
    with TestClient(module.app, base_url='https://testserver') as client:
        assert client.get(route).status_code == 401
        initial = dump(store)
        empty = client.get(route, headers=AUTH)
        assert empty.status_code == 200
        assert empty.json() == {'activities':0, 'pairs':[], 'next_offset':None}
        assert dump(store) == initial  # Preview never initializes storage.
        co = add(store, company())
        activity(store, co)
        activity(store, co)
        before = dump(store)
        response = client.get(route, headers=AUTH)
        assert response.status_code == 200 and len(response.json()['pairs']) == 1
        assert response.headers['cache-control'] == 'private, no-store'
        assert client.get(route, headers=AUTH).json() == response.json()
        assert dump(store) == before
        assert client.get(route+'?limit=51', headers=AUTH).status_code == 422
        assert client.post(route, headers=AUTH, json={}).status_code == 404
        assert route not in client.get('/openapi.json').text


def test_hidden_email_is_identified_and_old_storage_stays_read_only(tmp_path):
    store = Store(tmp_path)
    co = add(store, company())
    first = activity(store, co, title='Weekly newsletter', stage='completed')
    second = activity(store, co, title='Weekly newsletter', stage='completed')
    with store.connect() as db:
        db.execute('DROP TABLE crm_email_visibility')
    before = dump(store)
    result = pairs(store)
    assert dump(store) == before
    assert {member['id'] for member in result['pairs'][0]['members']} == {first, second}
    assert all(member['appears_in'][0]=='Hidden activity' for member in result['pairs'][0]['members'])
