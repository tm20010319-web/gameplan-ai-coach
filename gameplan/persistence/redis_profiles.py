"""Atomic player profile versions, frozen jobs and lease recovery."""
from datetime import datetime
import time
from uuid import uuid4
from gameplan.ai.player_profile import PROMPT_VERSION, merge
from gameplan.persistence.database import owner
from gameplan.persistence.redis_store import atomic, tx, composite


def epoch(value):
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return value
    return datetime.fromisoformat(value).timestamp()


@atomic
def read(user_id=None):
    uid = user_id or owner()
    row = tx().get('player_profiles', uid)
    return {'user_id': str(uid), **({k: row[k] for k in ('version', 'data', 'updated_at')} if row else {'version': 0, 'data': {'facts': []}, 'updated_at': None})}


@atomic
def versions(user_id=None, limit=20, offset=0):
    uid = user_id or owner()
    rows = sorted((r for r in tx().rows('profile_versions') if str(r['user_id']) == uid), key=lambda r: r['version'], reverse=True)
    return [{k: r[k] for k in ('version', 'match_id', 'changes', 'model', 'created_at')} for r in rows[offset:offset+limit]]


@atomic
def evidence(scope, key, user_id=None):
    uid = user_id or owner()
    fact = tx().get('profile_facts', composite(uid, scope, key))
    ids = fact['data']['evidence_ids'] if fact else []
    rows = sorted((r for r in tx().rows('profile_evidence') if str(r['user_id']) == uid and r['scope'] == scope and r['key'] == key and r['source_id'] in ids), key=lambda r: r['source_id'])[:100]
    return [{k: r[k] for k in ('source_id', 'content')} for r in rows]


@atomic
def jobs(user_id=None):
    uid = user_id or owner()
    rows = sorted((r for r in tx().rows('profile_update_jobs') if str(r['user_id']) == uid), key=lambda r: epoch(r['created_at']), reverse=True)[:20]
    return [{k: r[k] for k in ('id', 'match_id', 'status', 'attempts', 'error', 'created_at', 'updated_at')} for r in rows]


@atomic
def retry(job_id):
    row = tx().get('profile_update_jobs', job_id)
    if row and str(row['user_id']) == owner() and row['status'] == 'failed':
        row.update(status='pending', attempts=0, error=None, available_at=time.time(), updated_at=time.time())
        return True
    return False


@atomic
def claim():
    rows = sorted(tx().rows('profile_update_jobs'), key=lambda r: (epoch(r['created_at']), r['id']))
    now = time.time()
    for row in rows:
        uid = str(row['user_id'])
        if not ((row['status'] == 'pending' and epoch(row['available_at']) <= now) or (row['status'] == 'running' and epoch(row['lease_until']) < now)):
            continue
        if any(other['user_id'] == row['user_id'] and other['status'] in ('pending', 'running', 'failed') and
               (epoch(other['created_at']), other['id']) < (epoch(row['created_at']), row['id']) for other in rows):
            continue
        if any(other['user_id'] == row['user_id'] and other['id'] != row['id'] and other['status'] == 'running' and epoch(other['lease_until']) > now for other in rows):
            continue
        profile = tx().get('player_profiles', uid)
        if profile is None:
            profile = {'user_id': uid, 'version': 0, 'data': {'facts': []}, 'updated_at': now}
            tx().put('player_profiles', uid, profile)
        if row['input'] is None:
            review = tx().get('match_reviews', row['match_id'])
            match = tx().get('matches', row['match_id'])
            if not review or not match or str(review['user_id']) != uid or str(match['user_id']) != uid or review['version'] != row['review_version']:
                row.update(status='failed', error='复盘版本已变化，请核对记录。', updated_at=now)
                continue
            messages = sorted((m for m in tx().rows('chat_messages') if str(m['user_id']) == uid and m['role'] == 'user' and
                epoch(m['created_at']) <= epoch(row['created_at']) and not tx().get('profile_consumed_messages', m['id'])), key=lambda m: m['id'])
            sources = [{'id': 'message:'+str(m['id']), 'kind': 'user_message', 'text': m['content']} for m in messages]
            for i, point in enumerate(review['result'].get('points', [])):
                sources.append({'id': f"review:{row['match_id']}:{row['review_version']}:{i}", 'kind': 'review', 'game': match['game'],
                    'match_id': row['match_id'], 'hero': match['hero'], 'text': point['issue']+'；建议：'+point['advice'], 'frame_ids': point.get('evidence_ids', [])})
            row['input'] = {'base_version': profile['version'], 'profile': profile['data'], 'sources': sources,
                            'message_ids': [m['id'] for m in messages], 'prompt_version': PROMPT_VERSION}
        row.update(token=uuid4().hex, status='running', attempts=row['attempts']+1, lease_until=now+120, updated_at=now)
        return row
    return None


@atomic
def checkpoint(job, progress, changes, model):
    row = tx().get('profile_update_jobs', job['id'])
    if not row or row['token'] != job['token'] or row['status'] != 'running':
        return False
    row.update(progress=progress, changes=changes, model=model, lease_until=time.time()+120, updated_at=time.time())
    return True


@atomic
def complete(job, changes, model):
    uid, payload = str(job['user_id']), job['input']
    row = tx().get('profile_update_jobs', job['id'])
    if not row or row['token'] != job['token'] or row['status'] != 'running':
        return False
    profile = tx().get('player_profiles', uid)
    if profile['version'] != payload['base_version']:
        raise RuntimeError('画像版本发生变化，保留输入等待核对。')
    data = merge(profile['data'], changes, payload['sources'])
    version = profile['version']+1
    tx().put('profile_versions', composite(uid, version), {'user_id': uid, 'version': version, 'job_id': job['id'],
        'match_id': job['match_id'], 'data': data, 'changes': changes, 'model': model, 'created_at': time.time()})
    sources = {s['id']: s for s in payload['sources']}
    for fact in data['facts']:
        tx().put('profile_facts', composite(uid, fact['scope'], fact['key']), {'user_id': uid, 'scope': fact['scope'],
            'key': fact['key'], 'data': fact, 'updated_version': version})
        for eid in fact['evidence_ids']:
            identity = composite(uid, fact['scope'], fact['key'], eid)
            if eid in sources and not tx().get('profile_evidence', identity):
                tx().put('profile_evidence', identity, {'user_id': uid, 'scope': fact['scope'], 'key': fact['key'], 'source_id': eid, 'job_id': job['id'], 'content': sources[eid]})
    for mid in payload['message_ids']:
        tx().put('profile_consumed_messages', mid, {'message_id': mid, 'user_id': uid, 'job_id': job['id']})
    profile.update(version=version, data=data, updated_at=time.time())
    row.update(status='done', changes=changes, model=model, error=None, token=None, lease_until=None, updated_at=time.time())
    return True


@atomic
def fail(job):
    row = tx().get('profile_update_jobs', job['id'])
    if row and row['token'] == job['token']:
        row.update(status='failed' if row['attempts'] >= 3 else 'pending', error='画像分析暂未完成，旧画像和原始记录已保留。',
                   available_at=time.time()+30, token=None, lease_until=None, updated_at=time.time())
