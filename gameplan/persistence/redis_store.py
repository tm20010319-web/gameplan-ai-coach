"""Redis hashes with optimistic, atomic multi-record transactions.

No application data expires. A shared revision fences every transaction; failed
WATCH commits retry the complete operation, including its ownership checks.
"""
from contextvars import ContextVar
from functools import lru_cache, wraps
import json
import os
import time

import redis

_transaction = ContextVar('redis_transaction', default=None)


@lru_cache(maxsize=8)
def _client(url):
    return redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=5,
                                socket_timeout=15, health_check_interval=30)


def client():
    return _client(os.environ['GAMEPLAN_REDIS_URL'])


def prefix():
    return os.getenv('GAMEPLAN_REDIS_PREFIX', 'gameplan:v1')


def key(table):
    return prefix() + ':' + table


def dumps(value):
    return json.dumps(value, ensure_ascii=False, default=str, allow_nan=False)


class Transaction:
    def __init__(self, pipe):
        self.pipe, self.tables, self.original = pipe, {}, {}

    def table(self, name):
        if name not in self.tables:
            raw = self.pipe.hgetall(key(name))
            self.original[name] = raw
            self.tables[name] = {k: json.loads(v) for k, v in raw.items()}
        return self.tables[name]

    def get(self, table, identity):
        return self.table(table).get(str(identity))

    def put(self, table, identity, row):
        self.table(table)[str(identity)] = row

    def rows(self, table):
        return list(self.table(table).values())

    def delete(self, table, identity):
        self.table(table).pop(str(identity), None)

    def commit(self):
        changes = []
        for name, rows in self.tables.items():
            before = self.original[name]
            after = {i: dumps(row) for i, row in rows.items()}
            updates = {i: raw for i, raw in after.items() if before.get(i) != raw}
            removed = set(before) - set(after)
            if updates or removed:
                changes.append((name, updates, removed))
        # Execute even a read-only transaction, validating the watched revision.
        self.pipe.multi()
        for name, updates, removed in changes:
            if updates:
                self.pipe.hset(key(name), mapping=updates)
            if removed:
                self.pipe.hdel(key(name), *removed)
        if changes:
            self.pipe.incr(key('revision'))
        self.pipe.execute()


def atomic(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if _transaction.get() is not None:
            return function(*args, **kwargs)
        for attempt in range(12):
            with client().pipeline() as pipe:
                pipe.watch(key('revision'))
                token = _transaction.set(Transaction(pipe))
                try:
                    result = function(*args, **kwargs)
                    _transaction.get().commit()
                    return result
                except redis.WatchError:
                    if attempt == 11:
                        raise
                    time.sleep(min(.002 * (attempt + 1), .02))
                finally:
                    _transaction.reset(token)
    return wrapped


def tx():
    value = _transaction.get()
    if value is None:
        raise RuntimeError('Redis operations require @atomic')
    return value


def composite(*parts):
    return dumps(list(parts))


def owned(table, identity, user_id):
    row = tx().get(table, identity)
    if not row or str(row.get('user_id')) != str(user_id):
        raise KeyError(identity)
    return row


def sequence(name):
    row = tx().get('sequences', name) or {'value': 0}
    row['value'] += 1
    tx().put('sequences', name, row)
    return row['value']
