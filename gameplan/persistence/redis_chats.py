import time
from datetime import datetime, timezone
from uuid import uuid4
from gameplan.core.chat_store import ChatConflict
from gameplan.persistence.database import owner
from gameplan.persistence.redis_store import atomic, tx, owned, sequence


def session_row(sid):
    return owned('chat_sessions', sid, owner())


def session_messages(sid):
    return sorted((r for r in tx().rows('chat_messages') if r['session_id'] == sid and str(r['user_id']) == owner()), key=lambda r: r['id'])


def snapshot(row):
    messages = session_messages(row['id'])
    users = {r['request_id']: r for r in messages if r['role'] == 'user'}
    turns = [{'request_id': r['request_id'], 'user': users[r['request_id']]['content'], 'response': r['response']}
             for r in messages if r['role'] == 'assistant' and r['request_id'] in users]
    return {'id': row['id'], 'user_id': owner(), 'revision': row['revision'], 'turns': turns[-100:],
            'context': row['context'], 'timers': row['timers'], 'busy': row['busy_until'] > time.time(), 'server_epoch': time.time()}


def add_message(sid, request, role, content, response=None, match_id=None):
    identity = sequence('chat_messages')
    tx().put('chat_messages', identity, {'id': identity, 'user_id': owner(), 'session_id': sid,
        'request_id': request, 'role': role, 'content': content, 'response': response, 'match_id': match_id,
        'created_at': datetime.now(timezone.utc).isoformat()})


@atomic
def create():
    sid = uuid4().hex
    tx().put('chat_sessions', sid, {'id': sid, 'user_id': owner(), 'revision': 0, 'context': {},
        'timers': [], 'updated': time.time(), 'busy_until': 0, 'token': None})
    return read(sid)


@atomic
def read(sid):
    return snapshot(session_row(sid))


@atomic
def messages(sid, before=None, limit=50):
    session_row(sid)
    rows = [r for r in reversed(session_messages(sid)) if before is None or r['id'] < before][:limit]
    return {'items': [{k: r[k] for k in ('id', 'role', 'content', 'created_at', 'match_id')} for r in reversed(rows)],
            'next_before': rows[-1]['id'] if len(rows) == limit else None}


@atomic
def begin(sid, request_id, revision, text):
    row = session_row(sid)
    old = [r for r in session_messages(sid) if r['request_id'] == request_id]
    original = next((r for r in old if r['role'] == 'user'), None)
    if original:
        if original['content'] != text:
            raise ChatConflict('同一请求编号不能用于不同问题。')
        response = next((r for r in old if r['role'] == 'assistant'), None)
        if response:
            return {'cached': response['response'], 'state': snapshot(row)}
    if row['revision'] != revision:
        raise ChatConflict('对话已更新，请同步后再发送。')
    if row['busy_until'] > time.time():
        raise ChatConflict('上一条问题仍在处理，请稍后重试。')
    state = snapshot(row)
    if not original:
        active = sorted((m for m in tx().rows('matches') if str(m['user_id']) == owner() and m['status'] == 'recording'), key=lambda m: m['started'], reverse=True)
        add_message(sid, request_id, 'user', text, match_id=active[0]['id'] if active else None)
    token = uuid4().hex
    row.update(busy_until=time.time()+100, token=token)
    return {'token': token, 'state': state}


@atomic
def finish(sid, token, request_id, text, response):
    row = session_row(sid)
    if row['token'] != token:
        raise ChatConflict('本次回答已过期，请重试。')
    original = next((r for r in session_messages(sid) if r['request_id'] == request_id and r['role'] == 'user'), None)
    if not original or original['content'] != text:
        raise ChatConflict('问题与原记录不一致。')
    timers = row['timers']
    for event in response.get('timer_events', []):
        if event['action'] == 'clear':
            timers = []
        else:
            item = event['timer'] if event['action'] == 'start' else event
            def timer_key(t):
                return (t.get('game', 'hok'), t.get('hero_id', t['hero']), t['slot'])
            timers = [t for t in timers if timer_key(t) != timer_key(item)]
            if event['action'] == 'start':
                timers.append(item)
    result = {k: v for k, v in response.items() if k not in ('context', 'timer_events')}
    add_message(sid, request_id, 'assistant', result['answer'], result, original['match_id'])
    row.update(updated=time.time(), revision=row['revision']+1, context=response['context'], timers=timers, busy_until=0, token=None)
    return read(sid)


@atomic
def release(sid, token):
    row = session_row(sid)
    if row['token'] == token:
        row.update(busy_until=0, token=None)
