from fastapi.testclient import TestClient

from harness import career_email, correspondence, crm
from test_admin import admin_app, AUTH
from test_correspondence import message
from test_crm import add, company


def test_career_email_visibility_across_receipts_records_and_reimport(tmp_path, monkeypatch):
    module, store = admin_app(tmp_path, monkeypatch)
    co = add(store, company(domain='example.org'))
    def incoming(ident, subject, **fields):
        return message(ident, 'inbound').model_copy(update={'subject': subject, **fields})

    listing = incoming('listing', 'New listings near the river')
    recruiter = incoming('direct', 'Schedule an interview')
    newsletter = incoming('bulk', 'Studio news', mailing_list=True)

    def ingest(mail):
        return correspondence.ingest(store, mail, method='gmail_api')
    listing_row = ingest(listing)
    # Simulate a pre-feature database and receipt (the optional header is absent).
    with store.connect() as db:
        db.execute('DROP TABLE crm_email_visibility')
    recruiter_row = ingest(recruiter)
    news_row = ingest(newsletter)
    root = '/api/admin/crm'
    inbox = root + '/correspondence'
    with TestClient(module.app, base_url='https://testserver') as client:
        def get(path):
            response = client.get(path, headers=AUTH)
            assert response.status_code == 200
            return response.json()

        def visible():
            return {r['id'] for r in get(root)['records'] if r['kind']=='activity'}

        assert visible() == {recruiter_row['activity_id']}
        assert [r['id'] for r in get(inbox)['messages']] == [recruiter_row['id']]
        hidden = get(inbox + '?visibility=hidden&limit=1')
        assert hidden['total'] == 2 and hidden['next_offset'] == 1
        item = inbox + '/messages/' + listing_row['id']
        details = get(item)
        assert 'property listings' in details['visibility']['reason']
        assert details['message']['source_url'] == listing.source_url
        receipts_before = store.query('SELECT * FROM crm_message_versions ORDER BY message_id')
        history_before = store.query('SELECT * FROM crm_changes ORDER BY id')

        assert client.post(item+'/visibility', json={'hidden':False}).status_code == 401
        assert client.post(item+'/visibility', headers=AUTH, json={'hidden':False}).status_code == 200
        assert client.post(root+'/records/'+recruiter_row['activity_id']+'/visibility',
                           headers=AUTH, json={'hidden':True}).status_code == 200
        for mail in (listing, newsletter, recruiter):
            ingest(mail)
        assert [r['id'] for r in get(inbox)['messages']] == [listing_row['id']]
        assert visible() == {listing_row['activity_id']}
        with store.connect() as db:
            assert career_email.hidden_activity_ids(db) == {recruiter_row['activity_id'], news_row['activity_id']}
        related = get(root+'/records/'+co)['related']
        assert {r['id'] for r in related} == visible()
        assert get(item)['visibility']['reason'] == 'Restored by you.'
        assert store.query('SELECT * FROM crm_message_versions ORDER BY message_id') == receipts_before
        assert store.query('SELECT * FROM crm_changes ORDER BY id') == history_before


def test_report_only_email_can_be_inspected_and_restored_without_receipts(tmp_path, monkeypatch):
    module, store = admin_app(tmp_path, monkeypatch)
    co = add(store, company())
    ident = add(store, {'kind':'activity', 'title':'Monthly newsletter', 'company_id':co,
                        'channel':'email', 'notes':'Synthetic report excerpt.'})
    with TestClient(module.app, base_url='https://testserver') as client:
        url = '/api/admin/crm'
        hidden = client.get(url+'/hidden-email-activities', headers=AUTH).json()
        assert [row['id'] for row in hidden] == [ident]
        assert hidden[0]['record']['notes'] == 'Synthetic report excerpt.'
        assert client.post(url+'/records/'+ident+'/visibility', headers=AUTH, json={'hidden':False}).status_code == 200
        assert ident in {r['id'] for r in crm.overview(store)['records']}
