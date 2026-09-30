"""Per-user match lifecycle and durable review/profile outbox in Redis."""
import os
from pathlib import Path
import time
from uuid import uuid4
from gameplan.paths import ROOT
from gameplan.persistence.database import owner
from gameplan.persistence.redis_store import atomic, tx, owned, composite


def asset_root():
    return Path(os.getenv('GAMEPLAN_ASSET_DIR') or ROOT / 'data' / 'postgres-assets')


def public(row):
    value = dict(row)
    value.pop('token', None)
    review = tx().get('match_reviews', row['id'])
    value['result'] = review['result'] if review else None
    return value


@atomic
def stream(stream_id):
    row = tx().get('review_streams', composite(owner(), stream_id))
    return row['data'] if row else {}


@atomic
def read(match_id, include_frames=False):
    row = public(owned('matches', match_id, owner()))
    if include_frames:
        rows = sorted((r for r in tx().rows('match_events') if r['match_id'] == match_id and str(r['user_id']) == owner()), key=lambda r: r['captured'])
        row['frames'] = [{'id': r['id'], 'captured_at': r['captured'], 'observation': r['observation'], 'priority': r['priority']} for r in rows]
    return row


@atomic
def history(limit=20, offset=0):
    rows = sorted((r for r in tx().rows('matches') if str(r['user_id']) == owner()), key=lambda r: r['started'], reverse=True)
    return {'items': [public(r) for r in rows[offset:offset+limit]], 'total': len(rows)}


@atomic
def image(frame_id):
    row = owned('match_assets', frame_id, owner())
    path = (asset_root() / row['storage_key']).resolve()
    if not path.is_relative_to(asset_root().resolve()):
        raise KeyError(frame_id)
    try:
        return path.read_bytes()
    except OSError:
        raise KeyError(frame_id)


def put_result(match_id, result):
    old = tx().get('match_reviews', match_id)
    version = old['version']+1 if old else 1
    tx().put('match_reviews', match_id, {'match_id': match_id, 'user_id': owner(), 'version': version, 'result': result, 'updated_at': time.time()})
    return version


@atomic
def ingest(stream_id, observation, captured, pixels):
    now, uid = time.time(), owner()
    state = stream(stream_id)
    if captured <= state.get('last_at', 0):
        return state
    gap = captured-state.get('last_at', captured)
    state.update(last_at=captured, last_observation=observation)
    match = tx().get('matches', state.get('match_id', ''))
    if match and str(match['user_id']) != uid:
        raise PermissionError('Stream ownership mismatch')
    active = match is not None and match['status'] == 'recording'
    valid = observation['game'] != 'unknown' and observation['confidence'] >= .85
    playing = valid and observation['phase'] in ('bp', 'loading', 'in_game') and observation['viewpoint'] != 'spectator'
    clock = observation['game_time_s']
    boundary = active and playing and (observation['game'] != match['game'] or gap > 1800 or
        (state.get('played', 0) and observation['phase'] in ('bp', 'loading')) or
        (observation['phase'] == 'in_game' and clock is not None and state.get('clock', 0) > clock+120))
    if playing and (not active or boundary):
        start_key = observation['game'] + (':new' if boundary else ':start')
        state['start_count'] = state.get('start_count', 0)+1 if state.get('start_key') == start_key and gap < 90 else 1
        state['start_key'] = start_key
        if state['start_count'] >= 2:
            if active:
                match.update(status='incomplete', ended=captured, updated=now)
                put_result(match['id'], {'status': 'incomplete', 'source': 'local', 'points': [], 'message': '本局未确认结束，已保存过程记录。'})
            mid = uuid4().hex
            match = {'id': mid, 'user_id': uid, 'stream': stream_id, 'started': captured, 'ended': None,
                     'updated': now, 'game': observation['game'], 'hero': None, 'outcome': 'unknown',
                     'status': 'recording', 'samples': 0, 'token': None}
            tx().put('matches', mid, match)
            state = {'match_id': mid, 'last_at': captured, 'last_observation': observation, 'played': 0, 'end_count': 0}
            active, boundary = True, False
    else:
        state.update(start_key=None, start_count=0)
    if active and not boundary:
        same = valid and observation['game'] == match['game']
        if same and observation['phase'] == 'in_game' and observation['viewpoint'] == 'player':
            state['played'] = state.get('played', 0)+1
            if clock is not None:
                state['clock'] = clock
            hero = observation['player_hero']
            state['hero_count'] = state.get('hero_count', 0)+1 if hero and hero == state.get('hero_candidate') else int(bool(hero))
            state['hero_candidate'] = hero
            if hero and state['hero_count'] >= 2 and not match['hero']:
                match['hero'] = hero
        ending = same and observation['phase'] == 'result' and observation['outcome'] != 'unknown' and bool(observation['end_text']) and state.get('played', 0) >= 2
        if ending:
            state['end_count'] = state.get('end_count', 0)+1 if state.get('end_outcome') == observation['outcome'] and 1 <= gap <= 90 else 1
            state['end_outcome'] = observation['outcome']
        else:
            state.update(end_count=0, end_outcome=None)
        if same and observation['phase'] in ('in_game', 'result') and observation['viewpoint'] != 'spectator':
            # Stable across WATCH retries, so a retry overwrites the same orphan-safe file.
            import hashlib
            fid = hashlib.sha256(f'{uid}:{stream_id}:{captured}'.encode()).hexdigest()[:32]
            storage_key = uid + '/' + fid + '.jpg'
            path = asset_root() / storage_key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(pixels)
            priority = 3 if ending else 2 if any(f['kind'] in ('death', 'objective', 'combat') for f in observation['facts']) else int(bool(observation['facts']))
            tx().put('match_assets', fid, {'id': fid, 'user_id': uid, 'match_id': match['id'], 'storage_key': storage_key, 'mime_type': 'image/jpeg'})
            tx().put('match_events', fid, {'id': fid, 'user_id': uid, 'match_id': match['id'], 'captured': captured, 'observation': observation, 'priority': priority})
            match.update(samples=match['samples']+1, updated=now)
        if state.get('end_count', 0) >= 2:
            match.update(status='pending', outcome=observation['outcome'], ended=captured, updated=now)
    tx().put('review_streams', composite(uid, stream_id), {'user_id': uid, 'id': stream_id, 'data': state})
    return state


@atomic
def pending():
    return [r['id'] for r in tx().rows('matches') if str(r['user_id']) == owner() and
            (r['status'] == 'pending' or (r['status'] == 'analyzing' and r['updated'] < time.time()-120))][:8]


@atomic
def close_stream(stream_id):
    for row in tx().rows('matches'):
        if str(row['user_id']) == owner() and row['stream'] == stream_id and row['status'] == 'recording':
            row.update(status='incomplete', ended=time.time(), updated=time.time())
            put_result(row['id'], {'status': 'incomplete', 'source': 'local', 'points': [], 'message': '采集已切换，本局过程已保存。'})


@atomic
def claim(match_id, retry=False):
    row = owned('matches', match_id, owner())
    if not (row['status'] == 'pending' or (row['status'] == 'analyzing' and row['updated'] < time.time()-120) or (retry and row['status'] == 'failed')):
        return None
    token = uuid4().hex
    row.update(status='analyzing', updated=time.time(), token=token)
    return token


@atomic
def finish(match_id, token, result):
    row = owned('matches', match_id, owner())
    if row['token'] != token:
        return
    row.update(status=result['status'], updated=time.time(), token=None)
    version = put_result(match_id, result)
    if result['status'] in ('ready', 'insufficient'):
        identity = uuid4().hex
        tx().put('profile_update_jobs', identity, {'id': identity, 'user_id': owner(), 'match_id': match_id,
            'review_version': version, 'status': 'pending', 'attempts': 0, 'token': None, 'lease_until': None,
            'input': None, 'progress': 0, 'changes': [], 'model': None, 'error': None,
            'available_at': time.time(), 'created_at': time.time(), 'updated_at': time.time()})
