import json
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
import pytest

from harness import command_center as home, correspondence, crm
from harness.store import Store
from test_correspondence import message
from test_crm import add, company


def review(store):
    return home.feed(store, scope='review')['cards']


def decide(store, entry, state, request=None):
    return home.disposition(store, entry['id'], home.Disposition(
        revision=entry['revision'], disposition_version=entry['dispositionVersion'],
        intent_revision=entry['intentRevision'], state=state, request_id=request or str(uuid4())))


def edit(store, ident, **updates):
    with store.connect() as db:
        row = db.execute('SELECT * FROM crm_records WHERE id=?', (ident,)).fetchone()
        body = json.loads(row['body'])
        body.update(updates)
        crm.write_record(db, body, ident, row['revision'])


def test_equivalent_paraphrases_keep_people_and_tasks_separate(tmp_path):
    store = Store(tmp_path)
    co = add(store, company())
    person = add(store, {'kind':'person', 'company_id':co, 'name':'Casey Fixture'})
    other = add(store, {'kind':'person', 'company_id':co, 'name':'Jordan Fixture'})
    first = add(store, {'kind':'activity', 'company_id':co, 'person_id':person,
                        'title':'Follow-up', 'next_action':'Follow up with Casey Fixture'})
    repeated = add(store, {'kind':'activity', 'company_id':co, 'person_id':person,
                           'title':'Another description', 'next_action':'Please check in with Casey Fixture.'})
    distinct = add(store, {'kind':'activity', 'company_id':co, 'person_id':other,
                          'title':'Follow-up', 'next_action':'Follow up'})
    research = add(store, {'kind':'activity', 'company_id':co, 'person_id':person,
                          'title':'Research', 'next_action':'Research compensation'})
    cards = review(store)
    assert len(cards)==3
    merged = next(card for card in cards if first in card['memberIds'])
    assert merged['id']==first and merged['memberIds']==sorted([first, repeated])
    assert merged['intentOperation']=='follow_up'
    assert {distinct, research}.issubset(card['id'] for card in cards)
    with store.connect() as db:
        assert home.resolve_card(db, repeated)['id']==first
    decide(store, merged, 'dismissed')
    assert not any(first in card['memberIds'] for card in review(store))
    edit(store, repeated, title='A harmless title edit', next_action='Circle back with Casey Fixture')
    assert not any(first in card['memberIds'] for card in review(store))


def test_dismissed_task_returns_only_for_new_evidence_and_done_stays_closed(tmp_path):
    store = Store(tmp_path)
    co = add(store, company())
    first = add(store, {'kind':'activity','company_id':co,'title':'Role','next_action':'Review role',
                        'notes':'Recruiter requested an introductory discussion.'})
    entry = review(store)[0]
    decide(store, entry, 'dismissed')
    duplicate = add(store, {'kind':'activity','company_id':co,'title':'Another role note',
                            'next_action':'Assess this opportunity',
                            'notes':'Recruiter requested an introductory discussion.'})
    assert not review(store)
    edit(store, duplicate, notes='Recruiter requested an introductory discussion. Budget approved for the role.')
    returned = review(store)[0]
    assert returned['intentKey']==entry['intentKey'] and returned['intentRevision']!=entry['intentRevision']
    # The representative revision is unchanged; the group snapshot still rejects an old decision.
    assert returned['id']==first and returned['revision']==entry['revision']
    with pytest.raises(HTTPException) as stale:
        decide(store, entry, 'done')
    assert stale.value.status_code==409
    decide(store, returned, 'done')
    edit(store, duplicate, notes='Recruiter shared more role evidence.')
    assert not review(store)
    done = home.feed(store, scope='completed')['cards'][0]
    assert done['reviewed'] and done['disposition']=='done' and not done['action']
    decide(store, done, 'active')
    assert review(store)[0]['action']=='Review role'


def test_duplicate_receipts_do_not_reopen_a_dismissal(tmp_path):
    store = Store(tmp_path)
    add(store, company(domain='example.org'))
    first = correspondence.ingest(store, message())
    before = review(store)[0]
    decide(store, before, 'dismissed')
    repeated = correspondence.ingest(store, message('duplicate-provider-id'))
    assert first['activity_id']!=repeated['activity_id'] and not review(store)
    later = message('new-evidence').model_copy(update={
        'occurred_at':datetime(2026, 1, 2, 12, tzinfo=timezone.utc), 'text':'A new outreach message.'})
    correspondence.ingest(store, later)
    returned = review(store)
    assert len(returned)==1 and len(returned[0]['memberIds'])==3


def test_legacy_dispositions_aliases_and_completed_reviews_survive(tmp_path):
    store = Store(tmp_path)
    co = add(store, company())
    dismissed = add(store, {'kind':'activity','company_id':co,'title':'Original','next_action':'Follow up'})
    repeated = add(store, {'kind':'activity','company_id':co,'title':'Repeated','next_action':'Check in'})
    done = add(store, {'kind':'activity','company_id':co,'title':'Research original','next_action':''})
    other_done = add(store, {'kind':'activity','company_id':co,'title':'Research again','next_action':'Research company'})
    home.initialize(store)
    # Existing production tables, before intent snapshots existed.
    with store.connect() as db:
        db.execute("INSERT INTO command_center_dispositions VALUES (?,1,'dismissed',NULL)", (dismissed,))
        db.execute('INSERT INTO command_center_reviews VALUES (?,?,?,NULL)', (done,'proposed','Research company'))
        db.execute('INSERT INTO crm_redirects VALUES (?,?)', (dismissed, repeated))
    assert not review(store)
    assert home.feed(store, scope='dismissed')['cards'][0]['id']==repeated
    completed = home.feed(store, scope='completed')['cards'][0]
    assert {done, other_done}==set(completed['memberIds'])
    # Reopen works even if the representative differs from the reviewed record.
    decide(store, completed, 'active')
    assert len(review(store))==1


def test_historical_request_uses_original_revision_after_action_wording_edits(tmp_path):
    store = Store(tmp_path)
    target = add(store, company('History example', next_action='Review the role'))
    home.initialize(store)
    old = review(store)[0]
    request = home.Disposition(revision=1, disposition_version=0, state='dismissed', request_id='legacy-request')
    payload = json.dumps({'id':target, **request.model_dump(exclude={'intent_revision'})}, sort_keys=True)
    result = {'id':target,'state':'dismissed','resumeAt':None,'dispositionVersion':1}
    with store.connect() as db:
        db.execute("INSERT INTO command_center_dispositions VALUES (?,1,'dismissed',NULL)", (target,))
        db.execute('INSERT INTO command_center_disposition_requests VALUES (?,?,?)',
                   ('legacy-request',payload,json.dumps(result)))
    edit(store, target, next_action='Assess this opportunity', notes='')
    assert not review(store)
    assert home.disposition(store, target, request)==result
    entry = home.feed(store, scope='dismissed')['cards'][0]
    assert entry['intentKey']==old['intentKey']
    assert decide(store, entry, 'active')['state']=='active'
    assert len(review(store))==1


def test_grouping_and_suppression_happen_before_ranking_and_pagination(tmp_path):
    store = Store(tmp_path)
    co = add(store, company())
    for index in range(45):
        add(store, {'kind':'activity','company_id':co,'title':f'Duplicate {index}',
                    'next_action':'Follow up', 'follow_up':'2020-01-01'})
    for index in range(42):
        add(store, company(f'Distinct {index}', next_action='Review'))
    page = home.feed(store, scope='review')
    assert len(page['cards'])==40 and page['nextOffset']==40
    duplicate = next(card for card in page['cards'] if len(card['memberIds'])==45)
    assert page['cards'][0]['id']==duplicate['id']
    second = home.feed(store, scope='review', offset=40)
    assert len(second['cards'])==3 and second['nextOffset'] is None
    assert len({entry['intentKey'] for entry in page['cards']+second['cards']})==43
    decide(store, duplicate, 'later')
    assert len(review(store))==40  # Forty distinct tasks fill the first page.
    deferred = home.feed(store, scope='later')['cards']
    assert len(deferred)==1 and datetime.fromisoformat(deferred[0]['resumeAt'])>datetime.now(timezone.utc)
    with store.connect() as db:
        db.execute("UPDATE command_center_dispositions SET resume_at='2000-01-01T00:00:00+00:00' WHERE card_id=?",
                   (duplicate['id'],))
    assert review(store)[0]['id']==duplicate['id']
    # Merely revisiting pages (the Next operation) creates no disposition.
    before = store.query('SELECT * FROM command_center_dispositions')
    assert home.feed(store, scope='review')['cards']==home.feed(store, scope='review')['cards']
    assert store.query('SELECT * FROM command_center_dispositions')==before


def test_historical_target_names_and_representative_reopening_are_stable(tmp_path):
    store = Store(tmp_path)
    co = add(store, company())
    person = add(store, {'kind':'person','company_id':co,'name':'Original Contact'})
    first = add(store, {'kind':'activity','company_id':co,'person_id':person,
                        'title':'First task','next_action':'Follow up with Original Contact',
                        'notes':'Original Contact requested a discussion.'})
    other = add(store, {'kind':'activity','company_id':co,'person_id':person,
                        'title':'Other task','next_action':'Check in with Original Contact',
                        'notes':'Original Contact requested a discussion.'})
    decide(store, review(store)[0], 'dismissed')
    edit(store, person, name='Corrected Contact')
    edit(store, first, next_action='Follow up with Corrected Contact', notes='Corrected Contact requested a discussion.')
    assert not review(store)
    hidden = home.feed(store, scope='dismissed')['cards'][0]
    decide(store, hidden, 'active')
    # A legacy client can complete a different member of the same intent.
    home.mark_done(store, other, 1)
    assert not review(store)
    done = home.feed(store, scope='completed')['cards'][0]
    assert done['id']==first
    decide(store, done, 'active')
    reopened = review(store)[0]
    assert reopened['action']=='Follow up with Corrected Contact'
    decide(store, reopened, 'done')
    assert not review(store)


def test_explicit_unique_contact_uses_the_same_target_as_its_record(tmp_path):
    store = Store(tmp_path)
    co = add(store, company('Target example', next_action='Follow up with Casey Fixture'))
    person = add(store, {'kind':'person','company_id':co,'name':'Casey Fixture','next_action':'Check in'})
    cards = review(store)
    assert len(cards)==1 and cards[0]['id']==co
    assert cards[0]['memberIds']==sorted([co, person])
    assert cards[0]['personId']==person and cards[0]['person']=='Casey Fixture'


def test_unrecognized_unicode_actions_remain_distinct(tmp_path):
    store = Store(tmp_path)
    co = add(store, company())
    first = add(store, {'kind':'activity','company_id':co,'title':'Synthetic task','next_action':'调查薪资'})
    second = add(store, {'kind':'activity','company_id':co,'title':'Synthetic task','next_action':'安排面试'})
    assert {entry['id'] for entry in review(store)}=={first, second}


def test_saved_decision_follows_later_entity_redirection(tmp_path):
    store = Store(tmp_path)
    old_company = add(store, company('Original company'))
    canonical = add(store, company('Canonical company'))
    target = add(store, {'kind':'activity','company_id':old_company,'title':'Role',
                         'next_action':'Review role','notes':'Original company shared role evidence.'})
    decide(store, review(store)[0], 'dismissed')
    with store.connect() as db:
        db.execute('INSERT INTO crm_redirects VALUES (?,?)', (old_company, canonical))
    edit(store, target, company_id=canonical, notes='Canonical company shared role evidence.')
    assert not review(store)


def test_checking_for_reply_and_following_up_are_distinct_operations(tmp_path):
    store = Store(tmp_path)
    co = add(store, company('Separate operations'))
    first = add(store, {'kind':'activity','company_id':co,'title':'Follow-up','next_action':'Follow up'})
    second = add(store, {'kind':'activity','company_id':co,'title':'Reply check','next_action':'Check for a reply'})
    cards = review(store)
    assert len(cards)==2
    follow_up = next(entry for entry in cards if entry['id']==first)
    assert follow_up['intentOperation']=='follow_up'
    decide(store, follow_up, 'dismissed')
    pending = review(store)
    assert [entry['id'] for entry in pending]==[second]
    assert pending[0]['intentOperation']=='check_reply'


def test_schedule_wording_is_not_another_task_but_substantive_qualifiers_are(tmp_path):
    store = Store(tmp_path)
    co = add(store, company())
    first = add(store, {'kind':'activity','company_id':co,'title':'First','next_action':'Follow up'})
    add(store, {'kind':'activity','company_id':co,'title':'Tomorrow','next_action':'Follow up tomorrow'})
    add(store, {'kind':'activity','company_id':co,'title':'Scheduled','next_action':'Follow up next Monday'})
    compensation = add(store, {'kind':'activity','company_id':co,'title':'Pay','next_action':'Follow up about compensation'})
    add(store, {'kind':'activity','company_id':co,'title':'Scheduled pay',
                'next_action':'Follow up on Friday about compensation'})
    cards = review(store)
    assert len(cards)==2
    entry = next(card for card in cards if card['id']==first)
    decide(store, entry, 'dismissed')
    assert [card['id'] for card in review(store)]==[compensation]


def test_explicit_legacy_restore_overrides_untimed_review_of_equivalent_member(tmp_path):
    store = Store(tmp_path)
    co = add(store, company())
    done = add(store, {'kind':'activity','company_id':co,'title':'Old review','next_action':''})
    restored = add(store, {'kind':'activity','company_id':co,'title':'Other member','next_action':'Research company'})
    home.initialize(store)
    with store.connect() as db:
        db.execute('INSERT INTO command_center_reviews VALUES (?,?,?,NULL)', (done,'proposed','Research company'))
        payload = {'id':restored,'revision':1,'disposition_version':1,'state':'active','request_id':'old-restore'}
        result = {'id':restored,'dispositionVersion':2,'state':'active','resumeAt':None}
        db.execute("INSERT INTO command_center_dispositions VALUES (?,2,'active',NULL)", (restored,))
        db.execute('INSERT INTO command_center_disposition_requests VALUES (?,?,?)',
                   ('old-restore',json.dumps(payload),json.dumps(result)))
    assert len(review(store))==1
    assert review(store)[0]['action']=='Research company'


def test_direct_legacy_completion_after_restoring_same_record_stays_done(tmp_path):
    store = Store(tmp_path)
    target = add(store, company('Legacy completion', next_action='Research company'))
    home.initialize(store)
    with store.connect() as db:
        payload = {'id':target,'revision':1,'disposition_version':1,'state':'active','request_id':'old-restore'}
        result = {'id':target,'dispositionVersion':2,'state':'active','resumeAt':None}
        db.execute("INSERT INTO command_center_dispositions VALUES (?,2,'active',NULL)", (target,))
        db.execute('INSERT INTO command_center_disposition_requests VALUES (?,?,?)',
                   ('old-restore',json.dumps(payload),json.dumps(result)))
        body = json.loads(db.execute('SELECT body FROM crm_records WHERE id=?', (target,)).fetchone()['body'])
        body['next_action']=''
        crm.write_record(db, body, target, 1)
        db.execute('INSERT INTO command_center_reviews VALUES (?,?,?,NULL)',
                   (target,'interested','Research company'))
    assert not review(store)
    assert home.feed(store, scope='completed')['cards'][0]['id']==target


def test_real_group_evidence_digest_invalidates_selected_chat_snapshot(tmp_path):
    from harness import conversations

    store = Store(tmp_path)
    co = add(store, company())
    first = add(store, {'kind':'activity','company_id':co,'title':'First','next_action':'Review role'})
    other = add(store, {'kind':'activity','company_id':co,'title':'Second','next_action':'Assess opportunity'})
    conversations.initialize(store)
    original = review(store)[0]
    scope = {'surface':'command-center','card':first,'revision':original['revision'],
             'dispositionVersion':original['dispositionVersion'],'intentRevision':original['intentRevision']}
    edit(store, other, notes='The recruiter confirmed a new interview date.')
    latest = review(store)[0]
    assert latest['revision']==original['revision']
    assert latest['dispositionVersion']==original['dispositionVersion']
    with store.connect() as db:
        with pytest.raises(HTTPException) as stale:
            conversations.current_evidence(db, 'fixture-thread', json.dumps(scope))
        assert stale.value.status_code==409
        scope['intentRevision']=latest['intentRevision']
        current = conversations.current_evidence(db, 'fixture-thread', json.dumps(scope))
        assert current['references'][0]['id']==first
        assert current['references'][0]['memberIds']==sorted([first, other])
