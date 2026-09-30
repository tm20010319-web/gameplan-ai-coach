"""Workbench records in Redis, retaining filesystem image/report assets."""
import time
from gameplan.persistence.redis_store import atomic, tx, sequence, owned
from gameplan.persistence.database import owner
from gameplan.persistence import redis_timers


@atomic
def memory(match_id):
    row = tx().get('coach_matches', match_id)
    if row is None:
        row = {'id': match_id, 'user_id': owner(), 'created': time.time(), 'memory': {}}
        tx().put('coach_matches', match_id, row)
    elif str(row['user_id']) != owner():
        raise KeyError(match_id)
    row['memory'] = redis_timers.memory(match_id)
    return row['memory']


@atomic
def save_memory(match_id, value):
    memory(match_id)
    tx().get('coach_matches', match_id)['memory'] = value
    redis_timers.save_memory(match_id, value)


@atomic
def audit(match_id, value):
    memory(match_id)
    identity = sequence('coach_audit')
    tx().put('coach_audit', identity, {'id': identity, 'user_id': owner(), 'match_id': match_id, 'created': time.time(), 'data': value})


@atomic
def save_record(match_id, value):
    memory(match_id)
    old = tx().get('coach_records', value['advice_id'])
    if old and str(old['user_id']) != owner():
        raise KeyError(value['advice_id'])
    tx().put('coach_records', value['advice_id'], {'id': value['advice_id'], 'user_id': owner(), 'match_id': match_id, 'created': value['issued_epoch'], 'data': value})


@atomic
def records(match_id):
    memory(match_id)
    rows = sorted((r for r in tx().rows('coach_records') if r['match_id'] == match_id and str(r['user_id']) == owner()), key=lambda r: r['created'], reverse=True)[:100]
    return [r['data'] for r in rows]


@atomic
def save_report(report):
    memory(report['match_id'])
    if tx().get('coach_reports', report['id']):
        raise ValueError('Report already exists')
    tx().put('coach_reports', report['id'], {'id': report['id'], 'user_id': owner(), 'match_id': report['match_id'], 'created': time.time(), 'data': report})


@atomic
def get_report(report_id):
    row = tx().get('coach_reports', report_id)
    if not row:
        return None
    # Existing share links intentionally expose a report by its opaque UUID.
    return row['data']
