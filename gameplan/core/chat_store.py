"""Small independent local conversation store, with idempotent turn submission."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from gameplan.paths import ROOT

DATABASE_PATH = Path(os.getenv("COACH_DATA_DIR", str(ROOT / "data"))) / "chat.sqlite3"


class ChatConflict(Exception):
    pass


@contextmanager
def database():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS chats (id TEXT PRIMARY KEY, updated REAL, revision INTEGER, context TEXT, turns TEXT, busy_until REAL, token TEXT)")
            if "timers" not in {row[1] for row in db.execute("PRAGMA table_info(chats)")}:
                db.execute("ALTER TABLE chats ADD COLUMN timers TEXT NOT NULL DEFAULT '[]'")
            yield db
    finally:
        db.close()


def create():
    sid = uuid4().hex
    with database() as db:
        db.execute("INSERT INTO chats (id, updated, revision, context, turns, busy_until, token) VALUES (?, ?, 0, '{}', '[]', 0, '')", (sid, time.time()))
    return read(sid)


def snapshot(row):
    return {"id": row["id"], "revision": row["revision"], "turns": json.loads(row["turns"]),
            "context": json.loads(row["context"]), "busy": row["busy_until"] > time.time(),
            "timers": json.loads(row["timers"]), "server_epoch": time.time()}


def read(sid):
    with database() as db:
        row = db.execute("SELECT * FROM chats WHERE id=?", (sid,)).fetchone()
    if row is None:
        raise KeyError(sid)
    return snapshot(row)


def begin(sid, request_id, revision, text):
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM chats WHERE id=?", (sid,)).fetchone()
        if row is None:
            raise KeyError(sid)
        state = snapshot(row)
        for turn in state["turns"]:
            if turn["request_id"] == request_id:
                if turn["user"] != text:
                    raise ChatConflict("同一请求编号不能用于不同问题。")
                return {"cached": turn["response"], "state": state}
        if row["revision"] != revision:
            raise ChatConflict("对话已更新，请同步后再发送。")
        if state["busy"]:
            raise ChatConflict("上一条问题仍在处理，请稍后重试。")
        token = uuid4().hex
        db.execute("UPDATE chats SET busy_until=?, token=? WHERE id=?", (time.time() + 100, token, sid))
        return {"token": token, "state": state}


def finish(sid, token, request_id, text, response):
    result = {key: value for key, value in response.items() if key not in ("context", "timer_events")}
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM chats WHERE id=? AND token=?", (sid, token)).fetchone()
        if row is None:
            raise ChatConflict("本次回答已过期，请重试。")
        turns = json.loads(row["turns"])
        timers = json.loads(row["timers"])
        for event in response.get("timer_events", []):
            if event["action"] == "clear":
                timers = []
            else:
                item = event["timer"] if event["action"] == "start" else event
                def timer_key(t):
                    return (t.get("game", "hok"), t.get("hero_id", t["hero"]), t["slot"])
                timers = [t for t in timers if timer_key(t) != timer_key(item)]
                if event["action"] == "start":
                    timers.append(item)
        turns.append({"request_id": request_id, "user": text, "response": result})
        db.execute("UPDATE chats SET updated=?, revision=revision+1, context=?, turns=?, timers=?, busy_until=0, token='' WHERE id=?",
                   (time.time(), json.dumps(response["context"], ensure_ascii=False), json.dumps(turns[-20:], ensure_ascii=False), json.dumps(timers, ensure_ascii=False), sid))
    return read(sid)


def release(sid, token):
    with database() as db:
        db.execute("UPDATE chats SET busy_until=0, token='' WHERE id=? AND token=?", (sid, token))
