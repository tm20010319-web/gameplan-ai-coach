"""Full, independently queryable messages with durable request idempotency."""
import time
from uuid import uuid4

from gameplan.core.chat_store import ChatConflict
from gameplan.persistence.database import connection, jsonb, owner


def create():
    sid = uuid4().hex
    with connection() as db:
        db.execute("INSERT INTO chat_sessions(id,user_id,updated) VALUES(%s,%s,%s)", (sid, owner(), time.time()))
    return read(sid)


def session_row(db, sid, lock=False):
    row = db.execute("SELECT * FROM chat_sessions WHERE id=%s AND user_id=%s" + (" FOR UPDATE" if lock else ""), (sid, owner())).fetchone()
    if not row:
        raise KeyError(sid)
    return row


def snapshot(db, row):
    # Limit the UI's initial payload, not durable message storage or retries.
    rows = db.execute("""SELECT u.request_id,u.content AS user,a.response FROM chat_messages u
        JOIN chat_messages a ON a.session_id=u.session_id AND a.request_id=u.request_id
            AND a.user_id=u.user_id AND a.role='assistant'
        WHERE u.user_id=%s AND u.session_id=%s AND u.role='user' ORDER BY u.id DESC LIMIT 100""", (owner(), row["id"])).fetchall()
    return {"id": row["id"], "user_id": owner(), "revision": row["revision"], "turns": list(reversed(rows)),
            "context": row["context"], "timers": row["timers"], "busy": row["busy_until"] > time.time(), "server_epoch": time.time()}


def read(sid):
    with connection() as db:
        return snapshot(db, session_row(db, sid))


def messages(sid, before=None, limit=50):
    with connection() as db:
        session_row(db, sid)
        rows = db.execute("SELECT id,role,content,created_at,match_id FROM chat_messages WHERE session_id=%s AND user_id=%s AND (%s::bigint IS NULL OR id<%s) ORDER BY id DESC LIMIT %s",
                          (sid, owner(), before, before, limit)).fetchall()
    return {"items": list(reversed(rows)), "next_before": rows[-1]["id"] if len(rows) == limit else None}


def begin(sid, request_id, revision, text):
    with connection() as db:
        row = session_row(db, sid, lock=True)
        old = db.execute("SELECT content FROM chat_messages WHERE session_id=%s AND user_id=%s AND request_id=%s AND role='user'", (sid, owner(), request_id)).fetchone()
        if old:
            if old["content"] != text:
                raise ChatConflict("同一请求编号不能用于不同问题。")
            response = db.execute("SELECT response FROM chat_messages WHERE session_id=%s AND user_id=%s AND request_id=%s AND role='assistant'", (sid, owner(), request_id)).fetchone()
            if response:
                return {"cached": response["response"], "state": snapshot(db, row)}
        if row["revision"] != revision:
            raise ChatConflict("对话已更新，请同步后再发送。")
        if row["busy_until"] > time.time():
            raise ChatConflict("上一条问题仍在处理，请稍后重试。")
        token = uuid4().hex
        # Persist the user's words even if answering fails or the process exits.
        active = db.execute("SELECT id FROM matches WHERE user_id=%s AND status='recording' ORDER BY started DESC LIMIT 1", (owner(),)).fetchone()
        db.execute("INSERT INTO chat_messages(user_id,session_id,request_id,role,content,match_id) VALUES(%s,%s,%s,'user',%s,%s) ON CONFLICT(session_id,request_id,role) DO NOTHING",
                   (owner(), sid, request_id, text, active["id"] if active else None))
        db.execute("UPDATE chat_sessions SET busy_until=%s,token=%s WHERE id=%s AND user_id=%s", (time.time()+100, token, sid, owner()))
        return {"token": token, "state": snapshot(db, row)}


def finish(sid, token, request_id, text, response):
    result = {k: v for k, v in response.items() if k not in ("context", "timer_events")}
    with connection() as db:
        row = session_row(db, sid, lock=True)
        if row["token"] != token:
            raise ChatConflict("本次回答已过期，请重试。")
        original = db.execute("SELECT content,match_id FROM chat_messages WHERE user_id=%s AND session_id=%s AND request_id=%s AND role='user'", (owner(), sid, request_id)).fetchone()
        if not original or original["content"] != text:
            raise ChatConflict("问题与原记录不一致。")
        timers = row["timers"]
        for event in response.get("timer_events", []):
            if event["action"] == "clear":
                timers = []
            else:
                item = event["timer"] if event["action"] == "start" else event
                def key(t):
                    return (t.get("game", "hok"), t.get("hero_id", t["hero"]), t["slot"])
                timers = [t for t in timers if key(t) != key(item)]
                if event["action"] == "start":
                    timers.append(item)
        db.execute("INSERT INTO chat_messages(user_id,session_id,request_id,role,content,response,match_id) VALUES(%s,%s,%s,'assistant',%s,%s,%s)",
                   (owner(), sid, request_id, result["answer"], jsonb(result), original["match_id"]))
        db.execute("UPDATE chat_sessions SET updated=%s,revision=revision+1,context=%s,timers=%s,busy_until=0,token=NULL WHERE id=%s AND user_id=%s",
                   (time.time(), jsonb(response["context"]), jsonb(timers), sid, owner()))
    return read(sid)


def release(sid, token):
    with connection() as db:
        db.execute("UPDATE chat_sessions SET busy_until=0,token=NULL WHERE id=%s AND user_id=%s AND token=%s", (sid, owner(), token))


from gameplan.persistence.database import dispatch
dispatch(globals(), 'gameplan.persistence.redis_chats', 'create read messages begin finish release')
