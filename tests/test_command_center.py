from fastapi.testclient import TestClient

from harness import correspondence, crm
from test_admin import AUTH, admin_app
from test_correspondence import message
from test_crm import add, company

HOME = '/api/admin/command-center'
MAIL = '/api/admin/crm/correspondence/messages/'


def test_home_ranks_live_records_and_keeps_corrections(tmp_path, monkeypatch):
    module, store = admin_app(tmp_path, monkeypatch)
    first = add(store, company('Example Instruments', domain='example.org'))
    second = add(store, company('Example Studio'))
    contact = add(store, dict(kind='person', company_id=second, name='Casey Fixture'))
    due = add(store, dict(kind='activity', company_id=first, title='Prepare discussion',
                          next_action='Prepare interview notes', follow_up='2020-01-01'))
    receipt = correspondence.ingest(store, message())
    digest = message('digest', 'inbound').model_copy(update={'subject': 'Weekly newsletter', 'thread_id': 'digest'})
    newsletter = correspondence.ingest(store, digest)
    with TestClient(module.app, base_url='https://testserver') as client:
        def cards():
            response = client.get(HOME, headers=AUTH)
            assert response.headers['cache-control'] == 'private, no-store'
            return {c['id']: c for c in response.json()['cards']}

        assert client.get(HOME).status_code == 401
        assert client.get(HOME+'/contacts').status_code == 401
        assert client.post(HOME+'/cards/'+due+'/complete', json={'revision': 1}).status_code == 401
        first_read = cards()
        assert next(iter(first_read)) == due
        sent = first_read[receipt['activity_id']]
        assert sent['action'] == 'Check for a reply' and sent['companyId'] == first
        assert 'Synthetic correspondence' in sent['evidence']
        assert newsletter['activity_id'] not in first_read

        linked = client.post(MAIL+receipt['id']+'/link', headers=AUTH, json={'company_id': second, 'person_id': contact})
        assert linked.status_code == 200
        corrected = cards()[sent['id']]
        assert (corrected['company'], corrected['person']) == ('Example Studio', 'Casey Fixture')
        path = HOME+'/cards/'+sent['id']+'/complete'
        assert client.post(path, headers=AUTH, json={'revision': sent['revision']}).status_code == 409
        assert client.post(path, headers=AUTH, json={'revision': corrected['revision']}).status_code == 200
        completed = cards()[sent['id']]
        assert completed['reviewed'] and not completed['action'] and completed['stage'] == 'sent'

        response = client.post(HOME+'/cards/'+due+'/complete', headers=AUTH, json={'revision': first_read[due]['revision']})
        assert response.status_code == 200
        assert not cards()[due]['action']
        assert not store.query("SELECT json_extract(body,'$.next_action') AS action FROM crm_records WHERE id=?", (due,))[0]['action']

        assert client.post(HOME+'/cards/'+due+'/reopen', headers=AUTH, json={'revision': response.json()['revision']}).status_code == 200
        assert cards()[due]['action'] == 'Prepare interview notes'
        assert cards()[due]['due'] == '2020-01-01'

        restored = client.post(MAIL+newsletter['id']+'/visibility', headers=AUTH, json={'hidden': False})
        assert restored.status_code == 200 and newsletter['activity_id'] in cards()


def test_a_later_reply_replaces_the_outbound_follow_up(tmp_path, monkeypatch):
    module, store = admin_app(tmp_path, monkeypatch)
    add(store, company(domain='example.org'))
    sent = correspondence.ingest(store, message())
    reply = correspondence.ingest(store, message('fixture-reply', 'inbound'))
    with TestClient(module.app, base_url='https://testserver') as client:
        cards = client.get(HOME, headers=AUTH).json()['cards']
        assert cards[0]['id'] == reply['activity_id'] and cards[0]['action'] == 'Review the reply'
        assert next(c for c in cards if c['id'] == sent['activity_id'])['action'] == ''


def test_home_explains_import_progress_and_failure_without_exposing_worker_details(tmp_path):
    from harness.command_center import feed
    from harness.store import Store

    store = Store(tmp_path)
    ident = crm.save_source(store, crm.Source(provider='other', reference='fixture',
                                              title='Synthetic career report', report='Fixture evidence only.'))
    work = next(c for c in feed(store)['cards'] if c['id'] == 'import-' + ident)
    assert work['stage'] in ('waiting', 'working') and not work['action']
    thread = store.thread('private')
    turn = store.enqueue(thread['id'], 'Fixture extraction')
    with store.connect() as db:
        db.execute("UPDATE turns SET status='failed',error='Internal fixture diagnostic' WHERE id=?", (turn,))
        db.execute('UPDATE crm_import_jobs SET turn_id=? WHERE source_id=?', (turn, ident))
    failed = feed(store)['cards'][0]
    assert failed['stage'] == 'failed' and '1 failed' in failed['evidence']
    assert 'Internal fixture diagnostic' not in str(failed)
