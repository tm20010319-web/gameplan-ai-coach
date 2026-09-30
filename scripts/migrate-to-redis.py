"""Snapshot sources, prepare Redis records, verify before enabling Redis."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values, set_key
from gameplan.persistence import redis_migration as migration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--enable', action='store_true')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    settings = dotenv_values(ROOT / '.env')
    pending = dotenv_values(ROOT / 'data/redis-private/application.env')
    for name in ('GAMEPLAN_REDIS_URL', 'GAMEPLAN_REDIS_PREFIX'):
        value = os.getenv(name) or settings.get(name) or pending.get(name)
        if value:
            os.environ[name] = value
    uid = settings.get('GAMEPLAN_DEV_USER_ID')
    if not uid:
        raise SystemExit('An existing legacy owner (GAMEPLAN_DEV_USER_ID) is required')
    target = args.snapshot or migration.snapshot(ROOT, settings.get('GAMEPLAN_DATABASE_URL'), settings.get('COACH_DATA_DIR'))
    raw = target.read_bytes()
    checksums = json.loads((target.parent / 'checksums.json').read_text(encoding='utf-8'))
    if hashlib.sha256(raw).hexdigest() != checksums[target.name]:
        raise SystemExit('Snapshot checksum mismatch')
    data = json.loads(raw)
    tables = migration.prepare(data, uid, settings.get('GAMEPLAN_ASSET_DIR') or ROOT / 'data/postgres-assets')
    prepared = target.parent / 'redis-prepared.json'
    prepared.write_text(migration.dumps(tables), encoding='utf-8')
    print('Snapshot:', target)
    print('Prepared:', prepared)
    print(json.dumps({table: len(rows) for table, rows in tables.items()}, ensure_ascii=True))
    if args.enable and not (args.apply or args.verify):
        raise SystemExit('--enable requires --apply or --verify')
    if args.apply or args.verify:
        from gameplan.persistence.redis_store import client
        server = client()
        if not str(server.info('server')['redis_version']).startswith('8.'):
            raise SystemExit('Redis 8 is required')
        config = server.config_get('appendonly', 'appendfsync', 'maxmemory-policy')
        if config != {'appendonly': 'yes', 'appendfsync': 'always', 'maxmemory-policy': 'noeviction'}:
            raise SystemExit('Required Redis settings: AOF yes, fsync always, noeviction')
        if args.apply:
            migration.apply(tables, hashlib.sha256(raw).hexdigest())
        migration.verify(tables)
        if any(server.ttl(k) != -1 for k in server.scan_iter(match=migration.key('*'))):
            raise SystemExit('Unexpected expiring Redis data')
        server.save()
        print('Verified every row, no TTL, and synchronous persistence snapshot.')
        if args.enable:
            set_key(ROOT / '.env', 'GAMEPLAN_REDIS_URL', os.environ['GAMEPLAN_REDIS_URL'])
            set_key(ROOT / '.env', 'GAMEPLAN_REDIS_PREFIX', os.getenv('GAMEPLAN_REDIS_PREFIX', 'gameplan:v1'))
            set_key(ROOT / '.env', 'GAMEPLAN_LOCAL_REDIS', '1')
            set_key(ROOT / '.env', 'GAMEPLAN_LOCAL_POSTGRES', '0')
            print('Redis enabled; restart the application. Original source databases are retained.')
    else:
        print('Preparation only: Redis and application configuration have not been changed.')


if __name__ == '__main__':
    main()
