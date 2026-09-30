from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import os
from uuid import UUID

from gameplan.paths import ROOT

current_user = ContextVar("gameplan_user", default=None)


def enabled():
    return bool(os.getenv("GAMEPLAN_REDIS_URL") or os.getenv("GAMEPLAN_DATABASE_URL"))


def redis_enabled():
    return bool(os.getenv("GAMEPLAN_REDIS_URL"))


def owner():
    value = current_user.get()
    if not value:
        raise PermissionError("An authenticated user context is required")
    return str(UUID(str(value)))


@contextmanager
def as_user(user_id):
    token = current_user.set(str(UUID(str(user_id))))
    try:
        yield
    finally:
        current_user.reset(token)


def stream_key(stream_id):
    if not enabled():
        return stream_id
    return hashlib.sha256((owner() + ":" + stream_id).encode()).hexdigest()


@contextmanager
def connection():
    import psycopg
    from psycopg.rows import dict_row
    url = os.getenv("GAMEPLAN_DATABASE_URL")
    if not url:
        raise RuntimeError("GAMEPLAN_DATABASE_URL is not configured")
    with psycopg.connect(url, row_factory=dict_row, connect_timeout=5) as db:
        db.execute("SET LOCAL statement_timeout = '15s'")
        yield db


def jsonb(value):
    from psycopg.types.json import Jsonb
    return Jsonb(value)


def migrate():
    if redis_enabled():
        from gameplan.persistence.redis_store import atomic, tx
        @atomic
        def install():
            for path in (ROOT / 'gameplan/persistence/migrations').glob('*.sql'):
                tx().put('schema_migrations', path.stem, {'version': path.stem})
        install()
        return
    with connection() as db:
        db.execute("SELECT pg_advisory_xact_lock(17092901)")
        db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        for path in sorted((ROOT / "gameplan" / "persistence" / "migrations").glob("*.sql")):
            if not db.execute("SELECT 1 FROM schema_migrations WHERE version=%s", (path.stem,)).fetchone():
                db.execute(path.read_text(encoding="utf-8"))
                db.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (path.stem,))


class StoreProxy:
    """Select one store; PostgreSQL failures never fall back into old user data."""
    def __init__(self, legacy, module):
        self.legacy, self.module = legacy, module

    def __getattr__(self, name):
        if not enabled():
            return getattr(self.legacy, name)
        from importlib import import_module
        return getattr(import_module(self.module), name)


def dispatch(namespace, module, names):
    """Keep the previous backend available for rollback; Redis never falls back."""
    from functools import wraps
    from importlib import import_module
    for name in names.split():
        original = namespace[name]
        def wrap(original, name):
            @wraps(original)
            def selected(*args, **kwargs):
                if redis_enabled():
                    return getattr(import_module(module), name)(*args, **kwargs)
                return original(*args, **kwargs)
            return selected
        namespace[name] = wrap(original, name)
