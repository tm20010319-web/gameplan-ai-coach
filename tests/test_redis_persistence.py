"""Redis contract tests run on an isolated namespace (real Redis is opt-in)."""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from gameplan.persistence import database as d, chats, reviews, profiles, timers
from gameplan.persistence import redis_store as r, redis_migration as migration
from gameplan.ai import player_profile, profile_worker
from tests.test_player_profiles_pg import say, match, change


@pytest.fixture
def redis_store(monkeypatch, tmp_path):
    url = os.getenv('GAMEPLAN_TEST_REDIS_URL')
    if url:
        import redis
        server = redis.Redis.from_url(url, decode_responses=True)
        assert server.info('server')['redis_version'].startswith('8.')
    else:
        import fakeredis
        server = fakeredis.FakeRedis(decode_responses=True)
    namespace = 'gameplan:test:'+uuid4().hex
    monkeypatch.setenv('GAMEPLAN_REDIS_URL', url or 'redis://unused/0')
    monkeypatch.setenv('GAMEPLAN_REDIS_PREFIX', namespace)
    monkeypatch.setenv('GAMEPLAN_ASSET_DIR', str(tmp_path / 'assets'))
    monkeypatch.setattr(r, 'client', lambda: server)
    d.migrate()
    users = [str(uuid4()) for _ in range(3)]
    @r.atomic
    def seed():
        for uid in users:
            r.tx().put('users', uid, {'id': uid, 'display_name': 'test player', 'active': True, 'created_at': 0})
            r.tx().put('user_roles', r.composite(uid, 'player'), {'user_id': uid, 'role': 'player'})
    seed()
    try:
        yield users, server
    finally:
        keys = list(server.scan_iter(match=namespace+':*'))
        if keys:
            server.delete(*keys)


def test_chat_retry_pagination_user_isolation(redis_store):
    a, b, _ = redis_store[0]
    with d.as_user(a):
        sid = chats.create()['id']
        request = say(sid, 'first')
        for i in range(24):
            say(sid, f'message {i}')
        assert len(chats.read(sid)['turns']) == 25
        assert 'cached' in chats.begin(sid, request, 0, 'first')
        assert len(chats.messages(sid, limit=20)['items']) == 20
        assert chats.messages(sid, limit=20)['next_before']
        job = chats.begin(sid, 'failed', 25, 'preserve original text')
        chats.release(sid, job['token'])
        assert chats.messages(sid)['items'][-1]['content'] == 'preserve original text'
        retried = chats.begin(sid, 'failed', 25, 'preserve original text')
        chats.finish(sid, retried['token'], 'failed', 'preserve original text', {'answer': 'ok', 'context': {}})
        mid = match()
        fid = reviews.read(mid, True)['frames'][0]['id']
        timers.memory('same')
        timers.save_memory('same', {'private': 'A'})
    with d.as_user(b):
        with pytest.raises(KeyError):
            chats.read(sid)
        with pytest.raises(KeyError):
            reviews.read(mid)
        with pytest.raises(KeyError):
            reviews.image(fid)
        assert reviews.stream('same-stream') == {}
        assert timers.memory('same') == {}
        assert reviews.history()['total'] == 0


def test_profile_frozen_input_checkpoint_versions(redis_store, monkeypatch):
    with d.as_user(redis_store[0][0]):
        sid = chats.create()['id']
        say(sid, '建议只说一两点')
        match()
        assert len(profiles.jobs()) == 1
        job = profiles.claim()
        say(sid, '新消息留给下一局')
        assert len(job['input']['message_ids']) == 1
        assert not profiles.claim()
        monkeypatch.setattr(player_profile, 'extract', lambda p, sources: ([change(sources[0])], 'mock'))
        profile_worker.process(job)
        assert profiles.read()['version'] == 1
        assert not profiles.complete(job, [], 'mock')
        assert len(profiles.versions()) == 1
        assert profiles.evidence('global', 'response_length')[0]['content']['text'] == '建议只说一两点'
        match()
        next_job = profiles.claim()
        assert [s['text'] for s in next_job['input']['sources'] if s['kind'] == 'user_message'] == ['新消息留给下一局']
        profiles.complete(next_job, [change(next_job['input']['sources'][0], '希望更多解释')], 'mock')
        assert profiles.read()['version'] == 2
        assert len(profiles.evidence('global', 'response_length')) == 1


def test_lease_recovery_stale_workers_and_failure(redis_store):
    for uid in redis_store[0][:2]:
        with d.as_user(uid):
            match()
    one, two = profiles.claim(), profiles.claim()
    assert one['user_id'] != two['user_id']
    @r.atomic
    def expire():
        r.tx().get('profile_update_jobs', one['id'])['lease_until'] = 0
    expire()
    recovered = profiles.claim()
    assert recovered['id'] == one['id'] and recovered['token'] != one['token']
    assert not profiles.checkpoint(one, 10, [], 'stale')
    assert not profiles.complete(one, [], 'stale')
    @r.atomic
    def exhaust():
        r.tx().get('profile_update_jobs', two['id'])['attempts'] = 3
    exhaust()
    profiles.fail(two)
    with d.as_user(two['user_id']):
        assert profiles.read()['version'] == 0
        assert profiles.jobs()[0]['status'] == 'failed'
        assert profiles.retry(two['id'])


def test_atomic_conflict_retry_and_exception_rollback(redis_store):
    @r.atomic
    def increment():
        row = r.tx().get('counter', 'one') or {'value': 0}
        row['value'] += 1
        r.tx().put('counter', 'one', row)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: increment(), range(30)))
    @r.atomic
    def value():
        return r.tx().get('counter', 'one')['value']
    assert value() == 30
    @r.atomic
    def fail():
        r.tx().get('counter', 'one')['value'] = -1
        raise ValueError('rollback')
    with pytest.raises(ValueError):
        fail()
    assert value() == 30


def test_auth_coach_assignment_and_api_contract(redis_store, monkeypatch):
    from fastapi.testclient import TestClient
    from gameplan.web.monitor_app import app
    a, b, coach = redis_store[0]
    monkeypatch.setenv('GAMEPLAN_DEV_USER_ID', a)
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 4000)) as client:
        assert client.get('/api/me').json()['id'] == a
        assert client.get('/api/profile', params={'user_id': b}, headers={'X-User-Id': b}).status_code == 403
        assert client.get('/api/me', headers={'Origin': 'https://other.test'}).status_code == 403
        monkeypatch.setattr(app.state, 'resolve_user', lambda request: coach, raising=False)
        assert client.get('/api/profile', params={'user_id': b}).status_code == 403
        @r.atomic
        def assign():
            r.tx().put('user_roles', r.composite(coach, 'coach'), {'user_id': coach, 'role': 'coach'})
            r.tx().put('coach_assignments', r.composite(coach, b), {'coach_id': coach, 'player_id': b})
        assign()
        assert client.get('/api/profile', params={'user_id': b}).status_code == 200
        assert client.get('/api/profile', params={'user_id': a}).status_code == 403
        monkeypatch.delattr(app.state, 'resolve_user')
        chat = client.post('/api/chat/sessions')
        assert chat.status_code == 200, chat.text
        path = '/api/matches/redis-text-check'
        response = client.post(path+'/text-command', json={'text': '妲己放大了'})
        assert response.status_code == 200, response.text
        timers_before = client.get(path+'/skill-timers').json()['timers']
        assert timers_before
        assert client.get(path+'/skill-timers').json()['timers'] == timers_before
        assert client.get('/api/knowledge/skills/status').json()['storage'] == 'redis'


def test_source_document_idempotency(redis_store, tmp_path):
    from scripts.import_hero_document import import_document
    source = tmp_path / 'guide.md'
    source.write_text('# 英雄资料\n## 技能\n未经核验的数值\n', encoding='utf-8')
    first = import_document(source, '李元芳', '打野', tmp_path)
    assert import_document(source, '李元芳', '打野', tmp_path) == first
    @r.atomic
    def check():
        assert len(r.tx().rows('source_documents')) == 1
        assert all(row['status'] == 'pending_review' for row in r.tx().rows('source_sections'))
    check()


def test_workbench_records_socket_and_public_report(redis_store, monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi import WebSocketDisconnect
    from gameplan.web.app import app
    from gameplan.core import storage
    uid, other, _ = redis_store[0]
    monkeypatch.setenv('GAMEPLAN_DEV_USER_ID', uid)
    monkeypatch.setattr(app.state, 'resolve_user', lambda request: uid, raising=False)
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 4000)) as client:
        response = client.post('/api/matches')
        assert response.status_code == 200, response.text
        mid = response.json()['match_id']
        with d.as_user(uid):
            storage.save_record(mid, {'advice_id': 'redis-advice', 'issued_epoch': 100, 'summary': 'test'})
            storage.audit(mid, {'kind': 'test'})
            rid = uuid4().hex
            storage.save_report({'id': rid, 'match_id': mid, 'summary': 'share test'})
        assert len(client.get('/api/matches/'+mid).json()['records']) == 1
        response = client.post('/api/matches/'+mid+'/text-command', json={'text': '妲己放大了'})
        assert response.status_code == 200
        assert client.get('/api/matches/'+mid).json()['skill_timers']['timers'] == response.json()['skill_timers']['timers']
        assert client.get('/api/reports/'+rid).json() == {'id': rid, 'summary': 'share test'}
        with client.websocket_connect('/ws/'+mid) as socket:
            assert socket.receive_json()['data']['match_id'] == mid
        monkeypatch.setattr(app.state, 'resolve_user', lambda request: other, raising=False)
        assert client.get('/api/matches/'+mid).status_code == 404
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/ws/'+mid):
                pass
        # Public report share links preserve the original behavior.
        assert client.get('/api/reports/'+rid).status_code == 200
        monkeypatch.delattr(app.state, 'resolve_user')


def test_migration_all_rows_idempotent_and_nonempty_refused(redis_store, monkeypatch, tmp_path):
    # Use a fresh, separate namespace for the initial data import.
    monkeypatch.setenv('GAMEPLAN_REDIS_PREFIX', 'gameplan:test:'+uuid4().hex)
    uid = redis_store[0][0]
    data = {'postgres': {'users': [{'id': uid, 'active': True}]}, 'sqlite': {
        'coach.sqlite3': {'matches': [{'id': 'old', 'created': 123, 'memory': '{"skill_timers":[{"ready_epoch":456}]}'}],
                         'audit': [{'id': 7, 'match_id': 'old', 'created': 123, 'data': '{"kind":"old"}'}]},
        'hero_knowledge.sqlite3': {'metadata': [{'key': 'review', 'value': 'official'}],
            'heroes': [{'name': '李元芳', 'data': '{"hero":"李元芳"}'}]},
        'chat.sqlite3': {'chats': [{'id': 'old-chat', 'updated': 123, 'revision': 1, 'context': '{}', 'timers': '[]',
            'turns': '[{"request_id":"original","user":"你好","response":{"answer":"收到"}}]'}]}}}
    tables = migration.prepare(data, uid, tmp_path)
    assert migration.apply(tables, 'test-digest')
    assert migration.verify(tables)['chat_messages'] == 2
    assert not migration.apply(tables, 'test-digest')
    with pytest.raises(ValueError):
        migration.apply(tables, 'different-digest')
    with d.as_user(uid):
        assert timers.memory('old')['skill_timers'][0]['ready_epoch'] == 456
        assert chats.begin('old-chat', 'original', 0, '你好')['cached']['answer'] == '收到'
    changed = copy.deepcopy(tables)
    changed['heroes']['李元芳']['data']['hero'] = 'wrong'
    with pytest.raises(ValueError):
        migration.verify(changed)
    # No expiry, and no production namespace deletion.
    server = redis_store[1]
    keys = list(server.scan_iter(match=r.key('*')))
    assert all(server.ttl(k) == -1 for k in keys)
    server.delete(*keys)


def test_real_snapshot_prepared_without_source_changes(redis_store, monkeypatch, tmp_path):
    folders = sorted(Path('data/database-backups').glob('*/snapshot.json'))
    if not folders:
        pytest.skip('No prepared local snapshot')
    path = folders[-1]
    data = json.loads(path.read_text(encoding='utf-8'))
    uid = data['postgres']['users'][0]['id']
    tables = migration.prepare(data, uid, tmp_path)
    for filename, collections in data['sqlite'].items():
        for table, rows in collections.items():
            assert list(tables['legacy:'+filename+':'+table].values()) == rows
    monkeypatch.setenv('GAMEPLAN_REDIS_PREFIX', 'gameplan:test:'+uuid4().hex)
    migration.apply(tables, hashlib.sha256(path.read_bytes()).hexdigest())
    assert migration.verify(tables)['heroes'] == len(data['sqlite']['hero_knowledge.sqlite3']['heroes'])
    redis_store[1].delete(*list(redis_store[1].scan_iter(match=r.key('*'))))
