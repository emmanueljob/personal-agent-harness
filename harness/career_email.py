"""Career feed visibility. Corrections live outside immutable message evidence."""
import json
import re

from pydantic import BaseModel


class VisibilityEdit(BaseModel):
    hidden: bool


def decision(subject, body='', *, outbound=False, mailing_list=False, override=None):
    if override is not None:
        return {'hidden': bool(override), 'reason': 'Hidden by you.' if override else 'Restored by you.'}
    if not outbound:
        subject = subject.casefold()
        if re.search(r'\b(newsletter|weekly digest|monthly digest|news roundup)\b', subject):
            return {'hidden': True, 'reason': 'The subject identifies a bulk newsletter or digest.'}
        if re.search(r'\b(property (?:alerts?|listings?)|listing alerts?|new listings|homes? for sale|open houses?|properties matching|homes (?:matching|for you))\b', subject):
            return {'hidden': True, 'reason': 'The subject advertises property listings.'}
        if re.search(r'\b(your application|schedule (?:a|an|your) (?:call|interview)|interview invitation|interview availability)\b', subject):
            return {'hidden': False, 'reason': 'The subject refers to direct recruiting correspondence.'}
        subscription_footer = re.search(r'\b(unsubscribe|manage (?:your )?subscriptions?)\b', body, re.I)
        bulk_content = re.search(r'\b(newsletter|weekly digest|listing alerts?|properties for sale)\b', body, re.I)
        if mailing_list or (subscription_footer and bulk_content):
            return {'hidden': True, 'reason': 'The email carries mailing-list or bulk subscription signals.'}
    return {'hidden': False, 'reason': 'Included in career activity.'}


def message_decision(message, override):
    return decision(message.get('subject',''), message.get('text',''),
                    outbound=message.get('direction')=='outbound',
                    mailing_list=message.get('mailing_list',False), override=override)


def activity_decisions(db):
    """Return canonical email activity IDs and their reviewable visibility decisions."""
    from .correspondence import is_security_notice
    decisions = {}
    # CRM can be used before the first correspondence connection exists.
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='crm_messages'").fetchone():
        for row in db.execute("""
            SELECT m.id, m.body, m.method, COALESCE(r.record_id,m.activity_id) AS activity_id, v.hidden
            FROM crm_messages m LEFT JOIN crm_redirects r ON r.alias_id=m.activity_id
            LEFT JOIN crm_email_visibility v ON v.item_id=m.id
            WHERE m.provider='gmail' AND m.activity_id!='' ORDER BY m.created,m.id
        """):
            message = json.loads(row['body'])
            choice = message_decision(message, row['hidden'])
            if row['method']=='gmail_api' and is_security_notice(message['subject']):
                choice = {'hidden': True, 'reason': 'Authentication notice.', 'reviewable': False}
            # If consolidation joined receipts, keep the activity out of routine
            # suggestions whenever its evidence includes a hidden message.
            ident = row['activity_id']
            if ident not in decisions or choice['hidden']:
                decisions[ident] = {**choice, 'message_id': row['id']}

    for row in db.execute("""
        SELECT r.id,r.body,v.hidden FROM crm_records r
        LEFT JOIN crm_email_visibility v ON v.item_id=r.id
        WHERE r.kind='activity' AND json_extract(r.body,'$.channel')='email'
          AND r.id NOT IN (SELECT alias_id FROM crm_redirects)
    """):
        if row['id'] not in decisions:
            record = json.loads(row['body'])
            decisions[row['id']] = decision(record['title'], record.get('notes',''),
                                              outbound=record['stage']=='sent', override=row['hidden'])
    return decisions


def hidden_activity_ids(db):
    return {ident for ident, choice in activity_decisions(db).items() if choice['hidden']}


def set_override(db, ident, hidden):
    db.execute('INSERT INTO crm_email_visibility(item_id,hidden) VALUES (?,?) '
               'ON CONFLICT(item_id) DO UPDATE SET hidden=excluded.hidden', (ident, int(hidden)))
