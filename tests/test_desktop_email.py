"""Synthetic Gmail ingestion, durability and attribution tests. No network or personal mail."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0,str(Path(__file__).parents[1]/'desktop/macos/Worker'))
import email_ingest as mail
import dream_cycle as dream


@pytest.fixture
def db():
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE evidence(id TEXT PRIMARY KEY, source TEXT,kind TEXT,title TEXT,body TEXT,occurred REAL,payload TEXT,hash TEXT,revision INTEGER DEFAULT 1)')
    mail.setup(db)
    mail.handle(db,dict(action='begin',run_id='fixture',start=100,end=200,query='synthetic',accounts=[dict(account='owner@example.invalid',role='personal'),dict(account='family@example.invalid',role='shared')]))
    yield db
    db.close()


def message(ident='one',sender='owner@example.invalid',labels=None,text='I prefer quiet hotels.'):
    return dict(id=ident,internal_date='150000',label_ids=['SENT'] if labels is None else labels,thread_id='thread',
                payload=dict(mime_type='text/plain',headers=[dict(name='From',value=sender),dict(name='Subject',value='Synthetic trip')],body=dict(content=text)))


def ingest(db,values,account='owner@example.invalid'):
    mail.handle(db,dict(action='page',run_id='fixture',account=account,message_ids=[v['id'] for v in values]))
    return mail.handle(db,dict(action='messages',run_id='fixture',account=account,messages=values))


def test_all_pages_atomic_receipts_idempotence_and_changes(db):
    values=[message(str(i)) for i in range(99)]
    result=ingest(db,values)
    assert result['accounts'][1]['counts']==dict(imported=99)
    assert db.execute('SELECT count(*) FROM evidence').fetchone()[0]==99
    ingest(db,values)
    assert db.execute('SELECT max(revision) FROM evidence').fetchone()[0]==1
    # Failed batches roll back canonical data and receipt changes together.
    broken=message('new')
    mail.handle(db,dict(action='page',run_id='fixture',account='owner@example.invalid',message_ids=['new']))
    with pytest.raises(ValueError):
        mail.handle(db,dict(action='messages',run_id='fixture',account='owner@example.invalid',messages=[broken,message('not-queued')]))
    assert db.execute("SELECT count(*) FROM evidence WHERE id LIKE '%:new'").fetchone()[0]==0
    assert db.execute("SELECT state FROM email_queue WHERE message_id='new'").fetchone()[0]=='pending'


def test_account_scoping_shared_attribution_and_quote_removal(db):
    text='I prefer quiet hotels.\n\nOn Tuesday someone wrote:\n> I love loud resorts.'
    ingest(db,[message(text=text)])
    ingest(db,[message()],account='family@example.invalid')
    rows=[json.loads(r[0]) for r in db.execute('SELECT payload FROM evidence ORDER BY source')]
    assert len({r['id'] for r in rows})==2
    personal=next(r for r in rows if r['metadata']['account_role']=='personal')
    shared=next(r for r in rows if r['metadata']['account_role']=='shared')
    assert personal['metadata']['speaker']=='owner'
    assert 'loud resorts' in personal['body']
    assert personal['metadata']['authored_text']=='I prefer quiet hotels.'
    assert shared['metadata']['speaker']=='shared_mail'
    received=mail.record('owner@example.invalid','personal',message(sender='seller@example.invalid',labels=['INBOX']),100,200)
    assert received['metadata']['speaker']=='email_received'


def test_mime_alternatives_html_attachments_and_charset():
    plain=dict(mime_type='text/plain',body=dict(content='Plain body'))
    html=dict(mime_type='text/html',body=dict(content='<script>ignore()</script><p>Hello &amp; goodbye</p><img src="https://example.invalid/tracker">'))
    attachment=dict(mime_type='text/plain',filename='attachment.txt',body=dict(content='Never ingest this attachment'))
    assert mail.mime_text(dict(mime_type='multipart/alternative',parts=[html,plain]))[0]=='Plain body'
    text=mail.mime_text(dict(mime_type='multipart/mixed',parts=[html,attachment]))[0]
    assert 'Hello & goodbye' in text and 'ignore' not in text and 'Never ingest' not in text
    assert 'tracker' not in text
    assert mail.mime_text(dict(mime_type='text/plain',body=dict(attachment_id='external')))[0]==''
    assert mail.mime_text(dict(mime_type='text/plain',body=dict(base64_url_content='Y2Fmw6k')))[0]=='café'


def test_exclusions_date_bounds_and_failure_accounting(db):
    spam=message('spam',labels=['SPAM'])
    old=message('old');old['internal_date']='99000'
    future=message('future');future['internal_date']='200000'
    bad=message('bad');bad['internal_date']='invalid'
    status=ingest(db,[spam,old,future,bad])
    assert status['accounts'][1]['counts']==dict(excluded=3,failed=1)
    assert mail.handle(db,dict(action='finish',run_id='fixture'))['state']=='needs_retry'
    assert len(mail.handle(db,dict(action='pending',run_id='fixture',retry_failed=True))['pending'])==1


def test_selection_frozen_pause_resume_and_complete(db):
    with pytest.raises(ValueError):
        mail.handle(db,dict(action='begin',run_id='fixture',start=0,end=200,query='changed',accounts=[dict(account='owner@example.invalid',role='personal')]))
    with db:db.execute("UPDATE email_runs SET pause_requested=1")
    mail.handle(db,dict(action='page',run_id='fixture',account='owner@example.invalid',message_ids=['one']))
    assert mail.handle(db,dict(action='messages',run_id='fixture',account='owner@example.invalid',messages=[message()]))==dict(paused=True)
    mail.handle(db,dict(action='resume',run_id='fixture'))
    ingest(db,[message()])
    mail.handle(db,dict(action='page',run_id='fixture',account='family@example.invalid',message_ids=[]))
    assert mail.handle(db,dict(action='finish',run_id='fixture'))['state']=='complete'


def test_html_quoted_material_cannot_become_owner_speech():
    part=dict(mime_type='text/html',body=dict(content='<p>My own words.</p><blockquote>Someone else said this.</blockquote>'))
    text,_=mail.mime_text(part)
    assert 'Someone else said this.' in text
    assert mail.authored_text(text)=='My own words.'


def test_email_dreaming_keeps_shared_received_and_owner_sources_separate(db):
    ingest(db,[message()])
    ingest(db,[message('received',sender='merchant@example.invalid',labels=['INBOX'],text='Your reservation was received.')])
    ingest(db,[message()],account='family@example.invalid')
    rows=[dict(r) for r in db.execute('SELECT id,payload,hash FROM evidence')]
    chunks=list(dream.chunks(rows))
    assert {c['speaker'] for c in chunks}=={'owner','shared_mail','email_received'}
    received=next(c for c in chunks if c['speaker']=='email_received')
    shared=next(c for c in chunks if c['speaker']=='shared_mail')
    result=dream.validate({'patterns':[dict(claim='Reported email topics recur.',citations=[dict(id=received['id'],quote='Your reservation was received.'),dict(id=shared['id'],quote='I prefer quiet hotels.')])]}, {c['id']:c for c in chunks},'patterns',2)
    assert result[0]['claim'].startswith('Email evidence (reported activity; owner attribution unverified):')


def test_owner_dream_input_excludes_quoted_history(db):
    ingest(db,[message(text='My own words.\n> Someone else likes beach resorts.')])
    values=list(dream.chunks([dict(r) for r in db.execute('SELECT id,payload,hash FROM evidence')]))
    assert len(values)==1 and values[0]['text']=='My own words.'


def test_explicit_reprocessing_updates_changed_canonical_mail_once(db):
    ingest(db,[message()])
    mail.handle(db,dict(action='reprocess',run_id='fixture',account='owner@example.invalid',message_ids=['one']))
    mail.handle(db,dict(action='messages',run_id='fixture',account='owner@example.invalid',messages=[message(text='Corrected owner statement.')]))
    row=db.execute('SELECT body,revision FROM evidence').fetchone()
    assert row['body']=='Corrected owner statement.' and row['revision']==2
    ingest(db,[message(text='Corrected owner statement.')])
    assert db.execute('SELECT revision FROM evidence').fetchone()[0]==2
