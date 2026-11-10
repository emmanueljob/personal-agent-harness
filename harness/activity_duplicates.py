"""Read-only comparisons of canonical CRM activities and their identity history."""
import json
from collections import defaultdict

from .crm_imports import normalized

IDENTITY_FIELDS = ('company_id', 'person_id', 'title', 'channel', 'stage')
COMPARE_FIELDS = (*IDENTITY_FIELDS, 'happened_on', 'application_id', 'link_id', 'urls')
EVENT_FIELDS = ('company_id', 'person_id', 'application_id', 'happened_on', 'channel', 'stage')


def comparison_value(record, field, redirects):
    value = record.get(field, '')
    if field.endswith('_id'):
        return redirects.get(value, value)
    if field == 'title':
        return normalized(value)
    return value


def signature(record, redirects):
    return tuple(comparison_value(record, field, redirects) for field in IDENTITY_FIELDS)


def best_match(left, right, variants, redirects):
    """At least one current record must match the other's current or historical identity."""
    choices = []
    for current, other in ((left, right), (right, left)):
        key = signature(current['record'], redirects)
        if not key[2]:
            continue
        for variant in variants[other['id']].get(key, []):
            pair = (current, variant) if current is left else (variant, current)
            left_date = pair[0]['record'].get('happened_on')
            right_date = pair[1]['record'].get('happened_on')
            score = (bool(left_date and left_date == right_date),
                     left_date == right_date,
                     sum(value['origin'] == 'current' for value in pair))
            choices.append((score, pair))
    return max(choices, key=lambda choice: choice[0])[1] if choices else None


def preview(db, offset=0, limit=20):
    """Read one deterministic page from the caller's SQLite snapshot without writing."""
    limit = max(1, min(limit, 50))
    offset = max(0, offset)
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='crm_records'").fetchone():
        return {'activities': 0, 'pairs': [], 'next_offset': None}
    redirects = dict(db.execute('SELECT alias_id,record_id FROM crm_redirects'))
    records = {row['id']: dict(row) for row in db.execute('SELECT * FROM crm_records ORDER BY id')}
    activities = {ident: row for ident, row in records.items()
                  if row['kind'] == 'activity' and ident not in redirects}
    from .career_email import activity_decisions
    visibility = activity_decisions(db)
    activities = {ident: row for ident, row in activities.items()
                  if visibility.get(ident, {}).get('reviewable', True)}
    sources = {row['id']: json.loads(row['body']) for row in db.execute('SELECT id,body FROM crm_sources')}
    variants = defaultdict(lambda: defaultdict(list))
    provenance = defaultdict(list)
    current = {}

    def append(row, origin):
        ident = redirects.get(row['record_id'], row['record_id'])
        if ident not in activities:
            return
        record = json.loads(row['body'])
        variant = {'id': ident, 'record_id': row['record_id'], 'revision': row['revision'],
                   'origin': origin, 'record': record}
        variants[ident][signature(record, redirects)].append(variant)
        source = sources.get(row['source_id'], {})
        provenance[ident].append({
            'record_id': row['record_id'], 'revision': row['revision'], 'origin': origin,
            'source_id': row['source_id'], 'source_title': source.get('title', ''),
            'provider': source.get('provider', ''), 'reference': source.get('reference', ''),
            'evidence': row['evidence'][:2000], 'evidence_truncated': len(row['evidence']) > 2000,
        })
        if origin == 'current':
            current[ident] = variant

    for ident, row in records.items():
        if row['kind'] == 'activity':
            append({**row, 'record_id': ident}, 'alias' if ident in redirects else 'current')
    for row in db.execute("""
        SELECT c.* FROM crm_changes c JOIN crm_records r ON r.id=c.record_id
        WHERE r.kind='activity' ORDER BY c.record_id,c.revision DESC,c.id DESC
    """):
        append(row, 'history')

    by_signature = defaultdict(set)
    for ident, groups in variants.items():
        for key in groups:
            if key[2]:  # A title with no normalized text cannot establish identity.
                by_signature[key].add(ident)

    def member(ident):
        row = activities[ident]
        record = current[ident]['record']
        relationships = {}
        for field in ('company_id', 'person_id', 'application_id'):
            related = records.get(redirects.get(record.get(field), record.get(field)))
            body = json.loads(related['body']) if related else {}
            relationships[field] = body.get('name') or body.get('title') or ''
        choice = visibility.get(ident, {})
        appearances = ['Hidden activity' if choice.get('hidden') else 'All activity',
                       'Activity dossier and revision history']
        if not choice.get('hidden'):
            for field, label in (('company_id', 'Company'), ('person_id', 'Person'), ('application_id', 'Application')):
                if record.get(field):
                    appearances.append(label + ' dossier activity history')
        return {
            'id': ident, 'revision': row['revision'],
            'record': {field: record.get(field) for field in COMPARE_FIELDS},
            'relationships': relationships, 'provenance': provenance[ident][:50],
            'provenance_total': len(provenance[ident]),
            'aliases': sorted(alias for alias, target in redirects.items() if target == ident),
            'visibility': choice,
            'appears_in': appearances,
        }

    pairs = []
    count = 0
    for left_id in sorted(activities):
        possible = set()
        for key in variants[left_id]:
            possible.update(by_signature[key])
        for right_id in sorted(ident for ident in possible if ident > left_id):
            match = best_match(current[left_id], current[right_id], variants, redirects)
            if match is None:
                continue
            if count < offset:
                count += 1
                continue
            if len(pairs) == limit:
                return {'activities': len(activities), 'pairs': pairs, 'next_offset': offset + limit}
            left, right = match
            differences = [field for field in COMPARE_FIELDS
                           if comparison_value(current[left_id]['record'], field, redirects)
                           != comparison_value(current[right_id]['record'], field, redirects)]
            missing = [field for field in COMPARE_FIELDS if field != 'urls'
                       and (not left['record'].get(field) or not right['record'].get(field))]
            matched = [field for field in (*IDENTITY_FIELDS, 'happened_on')
                       if field not in missing and comparison_value(left['record'], field, redirects)
                       == comparison_value(right['record'], field, redirects)]
            conflicting = [field for field in EVENT_FIELDS if field in differences
                           and current[left_id]['record'].get(field) and current[right_id]['record'].get(field)]
            assessment = 'possible_duplicate'
            if differences or 'happened_on' in missing:
                assessment = 'needs_review'
            if conflicting:
                assessment = 'distinct_events'
            pairs.append({
                'members': [member(left_id), member(right_id)],
                'assessment': assessment,
                'matched_fields': matched, 'missing_fields': missing, 'different_fields': differences,
                'match_basis': [{
                    'record_id': variant['record_id'], 'revision': variant['revision'], 'origin': variant['origin'],
                    'record': {field: variant['record'].get(field) for field in COMPARE_FIELDS},
                } for variant in match],
                'import_identity_match': left['record'].get('happened_on') == right['record'].get('happened_on'),
            })
            count += 1
    return {'activities': len(activities), 'pairs': pairs, 'next_offset': None}
