import time
from gameplan.persistence.database import owner
from gameplan.persistence.redis_store import atomic, tx, sequence


@atomic
def exists(uid):
    row = tx().get('users', uid)
    return bool(row and row['active'])


def roles(uid):
    return [r['role'] for r in tx().rows('user_roles') if str(r['user_id']) == uid]


@atomic
def authorize(target):
    actor = owner()
    if not exists(target):
        raise KeyError(target)
    if actor != target:
        actor_roles = roles(actor)
        assigned = any(str(r['coach_id']) == actor and str(r['player_id']) == target for r in tx().rows('coach_assignments'))
        if 'admin' not in actor_roles and not ('coach' in actor_roles and assigned):
            raise PermissionError(target)
        identity = sequence('access_audit')
        tx().put('access_audit', identity, {'id': identity, 'actor_id': actor, 'target_id': target, 'action': 'read_profile', 'created_at': time.time()})
    return target


@atomic
def me():
    row = tx().get('users', owner())
    return {'id': row['id'], 'display_name': row['display_name'], 'roles': roles(owner())}


@atomic
def players():
    actor, actor_roles = owner(), roles(owner())
    assigned = {str(r['player_id']) for r in tx().rows('coach_assignments') if str(r['coach_id']) == actor}
    rows = [r for r in tx().rows('users') if r['active'] and (str(r['id']) == actor or 'admin' in actor_roles or ('coach' in actor_roles and str(r['id']) in assigned))]
    return [{'id': r['id'], 'display_name': r['display_name']} for r in sorted(rows, key=lambda r: str(r['created_at']))[:100]]


@atomic
def recover_users():
    return list({str(r['user_id']) for r in tx().rows('matches') if r['status'] == 'pending' or (r['status'] == 'analyzing' and r['updated'] < time.time()-120)})[:20]
