"""Read-only source snapshots, lossless archives and checked Redis import."""
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from gameplan.persistence.redis_store import atomic, tx, composite, dumps, client, key
from gameplan.persistence import redis_store


def encode(value):
    if isinstance(value, bytes):
        return {'__bytes_base64__': base64.b64encode(value).decode('ascii')}
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [encode(v) for v in value]
    return value


def snapshot(root, postgres_url=None, data_dir=None):
    root = Path(root)
    source_data = Path(data_dir) if data_dir else root / 'data'
    destination = root / 'data/database-backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    destination.mkdir(parents=True)
    data = {'sqlite': {}, 'postgres': {}}
    for path in sorted(source_data.glob('*.sqlite3')):
        copy = destination / path.name
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True) as source, sqlite3.connect(copy) as backup:
            source.backup(backup)
        with sqlite3.connect(copy) as db:
            db.row_factory = sqlite3.Row
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            data['sqlite'][path.name] = {table: [encode(dict(r)) for r in db.execute('SELECT * FROM "'+table.replace('"','""')+'"')] for table in tables}
    if postgres_url:
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row
        with psycopg.connect(postgres_url, row_factory=dict_row, connect_timeout=5) as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            tables = [r['tablename'] for r in db.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")]
            for table in tables:
                data['postgres'][table] = [dict(r) for r in db.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(table)))]
    target = destination / 'snapshot.json'
    target.write_text(dumps(data), encoding='utf-8')
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in destination.iterdir() if p.is_file()}
    (destination / 'checksums.json').write_text(dumps(manifest), encoding='utf-8')
    return target


def identity(table, row):
    compound = {'user_roles': ('user_id', 'role'), 'coach_assignments': ('coach_id', 'player_id'),
        'review_streams': ('user_id', 'id'), 'timer_sessions': ('user_id', 'id'),
        'profile_versions': ('user_id', 'version'), 'profile_facts': ('user_id', 'scope', 'key'),
        'profile_evidence': ('user_id', 'scope', 'key', 'source_id'), 'source_sections': ('document_id', 'ordinal')}
    if table in compound:
        return composite(*(row[k] for k in compound[table]))
    field = {'schema_migrations': 'version', 'player_profiles': 'user_id', 'match_reviews': 'match_id',
             'profile_consumed_messages': 'message_id', 'knowledge_metadata': 'key', 'heroes': 'name'}.get(table, 'id')
    return str(row[field])


def prepare(data, user_id, asset_dir):
    tables = {}
    def add(table, row):
        ident = identity(table, row)
        collection = tables.setdefault(table, {})
        if ident in collection and collection[ident] != row:
            raise ValueError('Conflicting migration row in '+table)
        collection[ident] = row
    for table, rows in data['postgres'].items():
        for row in rows:
            add(table, row)
    if user_id not in tables.get('users', {}):
        raise ValueError('Legacy owner must be an existing authenticated user')
    for filename, collections in data['sqlite'].items():
        for table, rows in collections.items():
            # Retain every original row verbatim in Redis for traceability.
            tables['legacy:'+filename+':'+table] = {str(i): row for i, row in enumerate(rows)}
        if filename == 'coach.sqlite3':
            for table, rows in collections.items():
                for row in rows:
                    converted = dict(row, user_id=user_id)
                    for field in ('memory', 'data'):
                        if field in converted and converted[field] is not None:
                            converted[field] = json.loads(converted[field])
                    add('coach_'+table, converted)
                    if table == 'matches':
                        ident = composite(user_id, row['id'])
                        existing = tables.setdefault('timer_sessions', {}).get(ident)
                        # PostgreSQL's newer session wins a collision; original
                        # SQLite values remain in both coach_matches and archive.
                        if existing is None:
                            add('timer_sessions', {'id': row['id'], 'user_id': user_id, 'memory': converted['memory'], 'updated_at': row['created']})
        elif filename == 'hero_knowledge.sqlite3':
            for table, rows in collections.items():
                for row in rows:
                    converted = dict(row)
                    if table == 'heroes':
                        converted['data'] = json.loads(converted['data'])
                    add('knowledge_metadata' if table == 'metadata' else table, converted)
        elif filename == 'chat.sqlite3':
            counter = max([int(r['id']) for r in tables.get('chat_messages', {}).values()] or [0])
            for row in collections.get('chats', []):
                add('chat_sessions', {'id': row['id'], 'user_id': user_id, 'updated': row['updated'], 'revision': row['revision'],
                    'context': json.loads(row['context']), 'timers': json.loads(row.get('timers') or '[]'),
                    'busy_until': 0, 'token': None})
                for turn in json.loads(row['turns']):
                    for role in ('user', 'assistant'):
                        counter += 1
                        add('chat_messages', {'id': counter, 'user_id': user_id, 'session_id': row['id'], 'request_id': turn['request_id'],
                            'role': role, 'content': turn['user'] if role == 'user' else turn['response']['answer'],
                            'response': turn['response'] if role == 'assistant' else None,
                            'created_at': datetime.fromtimestamp(row['updated'], timezone.utc).isoformat(), 'match_id': None})
        elif filename == 'reviews.sqlite3':
            for row in collections.get('review_streams', []):
                add('review_streams', {'user_id': user_id, 'id': row['id'], 'data': json.loads(row['data'])})
            for row in collections.get('review_matches', []):
                converted = {k: v for k, v in row.items() if k != 'result'}
                converted['user_id'] = user_id
                add('matches', converted)
                if row['result']:
                    add('match_reviews', {'user_id': user_id, 'match_id': row['id'], 'result': json.loads(row['result']), 'version': 1, 'updated_at': row['updated']})
            for row in collections.get('review_frames', []):
                storage_key = user_id+'/'+row['id']+'.jpg'
                path = Path(asset_dir) / storage_key
                content = base64.b64decode(row['image']['__bytes_base64__'])
                if path.exists() and path.read_bytes() != content:
                    raise ValueError('Existing frame file differs; refusing overwrite')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                add('match_assets', {'id': row['id'], 'user_id': user_id, 'match_id': row['match_id'], 'storage_key': storage_key, 'mime_type': 'image/jpeg'})
                add('match_events', {'id': row['id'], 'user_id': user_id, 'match_id': row['match_id'], 'captured': row['captured'], 'observation': json.loads(row['observation']), 'priority': row['priority']})
    for table, field in [('chat_messages', 'id'), ('coach_audit', 'id'), ('timer_audit', 'id'), ('access_audit', 'id')]:
        tables.setdefault('sequences', {})[table] = {'value': max([int(r[field]) for r in tables.get(table, {}).values()] or [0])}
    return tables


@atomic
def apply(tables, source_digest):
    old = tx().get('migration', 'source')
    if old:
        if old['sha256'] != source_digest:
            raise ValueError('A different migration already exists in this namespace')
        return False
    # This is an initial import, never an overwrite of an active namespace.
    if any(redis_store.client().scan_iter(match=key('*'))):
        raise ValueError('Redis namespace is not empty; choose a fresh prefix')
    for table, rows in tables.items():
        for ident, row in rows.items():
            tx().put(table, ident, row)
    tx().put('migration', 'source', {'sha256': source_digest, 'counts': {t: len(r) for t, r in tables.items()}, 'imported_at': datetime.now(timezone.utc).isoformat()})
    return True


@atomic
def verify(tables):
    for table, expected in tables.items():
        actual = tx().table(table)
        if actual != expected:
            raise ValueError('Redis verification failed for '+table)
    return {table: len(rows) for table, rows in tables.items()}
