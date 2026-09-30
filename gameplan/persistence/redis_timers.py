import time
from gameplan.persistence.database import owner
from gameplan.persistence.redis_store import atomic, tx, composite, sequence


@atomic
def memory(match_id):
    identity = composite(owner(), match_id)
    row = tx().get('timer_sessions', identity)
    if row is None:
        row = {'user_id': owner(), 'id': match_id, 'memory': {}, 'updated_at': time.time()}
        tx().put('timer_sessions', identity, row)
    return row['memory']


@atomic
def save_memory(match_id, value):
    memory(match_id)
    tx().get('timer_sessions', composite(owner(), match_id)).update(memory=value, updated_at=time.time())


@atomic
def audit(match_id, value):
    memory(match_id)
    identity = sequence('timer_audit')
    tx().put('timer_audit', identity, {'id': identity, 'user_id': owner(), 'session_id': match_id, 'data': value, 'created_at': time.time()})
