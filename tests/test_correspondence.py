import json
from datetime import datetime,timezone
from fastapi.testclient import TestClient
from harness import correspondence as mail
from harness.store import Store
from test_crm import add,company
from test_admin import admin_app,AUTH


def message(ident='sent-1',direction='outbound',**kw):
    return mail.Message(provider='gmail',account='owner@example.net',external_id=ident,thread_id='thread-1',direction=direction,occurred_at=datetime(2026,1,1,12 if direction=='outbound' else 13,tzinfo=timezone.utc),sender=mail.Address(name='Owner' if direction=='outbound' else 'Riley Test',email='owner@example.net' if direction=='outbound' else 'riley@example.org'),recipients=[mail.Address(name='Riley Test',email='riley@example.org')] if direction=='outbound' else [mail.Address(email='owner@example.net')],subject='Platform interview',text='  Synthetic correspondence.\n\nExact text.  ',source_url='https://mail.google.com/mail/#all/thread-1',**kw)


def test_idempotent_evidence_and_reply_reconciliation_without_drafts(tmp_path):
    store=Store(tmp_path);co=add(store,company(domain='example.org'));person=add(store,dict(kind='person',company_id=co,name='Riley Test',email='riley@example.org'))
    incoming=mail.ingest(store,message('reply-1','inbound'),method='gmail_api')
    sent=mail.ingest(store,message(),method='gmail_api')
    assert mail.ingest(store,message(),method='gmail_api')==sent
    assert len(store.query('SELECT * FROM crm_messages'))==2
    activities=store.query("SELECT * FROM crm_records WHERE kind='activity'")
    assert len(activities)==2
    reply=next(r for r in activities if r['id']==incoming['activity_id'])
    assert json.loads(reply['body'])['stage']=='replied' and reply['certainty']=='provider_observed'
    assert json.loads(reply['body'])['person_id']==person
    assert json.loads(store.query('SELECT body FROM crm_messages WHERE id=?',(sent['id'],))[0]['body'])['text'].startswith('  ')


def test_unmatched_messages_are_retained_and_can_be_linked_without_inventing_company(tmp_path):
    store=Store(tmp_path);result=mail.ingest(store,message())
    assert not result['linked'] and not result['activity_id']
    co=add(store,company());mail.link(store,result['id'],co)
    assert len(store.query("SELECT * FROM crm_records WHERE kind='activity'"))==1
    assert mail.messages(store)['messages'][0]['method']=='owner_import'


def test_headers_do_not_merge_namesakes_across_companies(tmp_path):
    store=Store(tmp_path);a=add(store,company('A',domain='example.org'));b=add(store,company('B',domain='other.example'))
    add(store,dict(kind='person',company_id=b,name='Riley Test'))
    result=mail.ingest(store,message(),method='gmail_api')
    row=store.query('SELECT * FROM crm_messages WHERE id=?',(result['id'],))[0]
    assert row['company_id']==a and not row['person_id']


def test_owner_boundaries_and_no_public_message_exposure(tmp_path,monkeypatch):
    module,store=admin_app(tmp_path,monkeypatch)
    with TestClient(module.app,base_url='https://testserver') as client:
        assert client.get('/api/admin/crm/correspondence').status_code==401
        assert client.post('/api/admin/crm/correspondence/import',json={'messages':[message().model_dump(mode='json')]}).status_code==401
        r=client.post('/api/admin/crm/correspondence/import',headers=AUTH,json={'messages':[message().model_dump(mode='json')]})
        assert r.status_code==200
        ident=r.json()['messages'][0]['id']
        detail=client.get('/api/admin/crm/correspondence/messages/'+ident,headers=AUTH)
        assert detail.json()['message']['text'].startswith('  ') and detail.headers['cache-control']=='private, no-store'
        assert 'Synthetic correspondence' not in client.get('/').text
        assert 'correspondence' not in client.get('/openapi.json').text
        assert client.post('/api/admin/crm/correspondence/import',headers={**AUTH,'Origin':'https://bad.example'},json={'messages':[message().model_dump(mode='json')]}).status_code==403


def test_authentication_notice_receipts_stay_private_without_cluttering_search(tmp_path):
    store=Store(tmp_path)
    notice=message().model_copy(update={'subject':'Please verify your new device'})
    mail.ingest(store,notice,method='gmail_api')
    assert mail.messages(store)['total']==0
    assert len(store.query('SELECT * FROM crm_messages'))==1
    assert len(store.query('SELECT * FROM crm_message_versions'))==1


def test_sent_filter_finds_older_outreach_before_pagination(tmp_path):
    store=Store(tmp_path)
    sent=mail.ingest(store,message('older-outreach'),method='gmail_api')
    for i in range(35):mail.ingest(store,message('incoming-'+str(i),'inbound'),method='gmail_api')
    assert not any(r['id']==sent['id'] for r in mail.messages(store)['messages'])
    outbox=mail.messages(store,direction='outbound')
    assert outbox['total']==1 and outbox['messages'][0]['id']==sent['id']
    assert outbox['next_offset'] is None
    incoming=mail.messages(store,direction='inbound',offset=30)
    assert incoming['total']==35 and len(incoming['messages'])==5
