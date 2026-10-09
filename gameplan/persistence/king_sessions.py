"""Map service-authenticated Sale identities and Root chats to durable Gameplan records."""
import json
import time
from uuid import NAMESPACE_URL, uuid5

from gameplan.persistence import database


def stable_id(*parts):
    return uuid5(NAMESPACE_URL, json.dumps(parts, ensure_ascii=False, separators=(",", ":")))


def ensure(tenant, subject, root_session):
    if not database.enabled():
        raise RuntimeError("A user-owned database is required")
    provider = "sale-root:" + tenant
    uid = str(stable_id("gameplan-sale-user-v1", tenant, subject))
    if database.redis_enabled():
        return _redis_ensure(provider, subject, uid, root_session)
    with database.connection() as db:
        db.execute("""INSERT INTO users(id,display_name,auth_provider,auth_subject)
            VALUES(%s,'王者助手用户',%s,%s) ON CONFLICT(auth_provider,auth_subject) DO NOTHING""",
            (uid, provider, subject))
        user = db.execute("SELECT id,active FROM users WHERE auth_provider=%s AND auth_subject=%s",
                          (provider, subject)).fetchone()
        if not user or not user["active"]:
            raise PermissionError("Disabled player")
        uid = str(user["id"])
        # The bridge grants only player access; it never imports Sale's admin role.
        db.execute("INSERT INTO user_roles(user_id,role) VALUES(%s,'player') ON CONFLICT DO NOTHING", (uid,))
        sid = stable_id("gameplan-sale-chat-v1", uid, root_session).hex
        db.execute("""INSERT INTO chat_sessions(id,user_id,updated) VALUES(%s,%s,%s)
            ON CONFLICT(id) DO NOTHING""", (sid, uid, time.time()))
        row = db.execute("SELECT user_id FROM chat_sessions WHERE id=%s", (sid,)).fetchone()
        if str(row["user_id"]) != uid:
            raise PermissionError("Session owner mismatch")
    return uid, sid


def _redis_ensure(provider, subject, uid, root_session):
    from gameplan.persistence.redis_store import atomic, tx, composite

    @atomic
    def create():
        nonlocal uid
        user = next((r for r in tx().rows("users")
                     if r.get("auth_provider") == provider and r.get("auth_subject") == subject), None)
        if user:
            uid = str(user["id"])
            if not user["active"]:
                raise PermissionError("Disabled player")
        else:
            if tx().get("users", uid):
                raise PermissionError("Identity binding mismatch")
            tx().put("users", uid, {"id": uid, "display_name": "王者助手用户", "active": True,
                "auth_provider": provider, "auth_subject": subject, "created_at": time.time()})
        tx().put("user_roles", composite(uid, "player"), {"user_id": uid, "role": "player"})
        sid = stable_id("gameplan-sale-chat-v1", uid, root_session).hex
        row = tx().get("chat_sessions", sid)
        if row and str(row["user_id"]) != uid:
            raise PermissionError("Session owner mismatch")
        if not row:
            tx().put("chat_sessions", sid, {"id": sid, "user_id": uid, "revision": 0, "context": {},
                "timers": [], "updated": time.time(), "busy_until": 0, "token": None})
        return uid, sid

    return create()
