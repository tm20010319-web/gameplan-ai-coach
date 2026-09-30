"""Durable per-match samples, lifecycle and idempotent review jobs."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from gameplan.paths import ROOT

DATABASE_PATH = Path(os.getenv("COACH_DATA_DIR", str(ROOT / "data"))) / "reviews.sqlite3"
MAX_FRAMES = 480


@contextmanager
def database():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    try:
        with db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS review_streams (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS review_matches (
                    id TEXT PRIMARY KEY, stream TEXT NOT NULL, started REAL NOT NULL,
                    ended REAL, updated REAL NOT NULL, game TEXT NOT NULL, hero TEXT,
                    outcome TEXT NOT NULL DEFAULT 'unknown', status TEXT NOT NULL DEFAULT 'recording',
                    samples INTEGER NOT NULL DEFAULT 0, result TEXT, token TEXT);
                CREATE INDEX IF NOT EXISTS review_match_time ON review_matches(started DESC);
                CREATE TABLE IF NOT EXISTS review_frames (
                    id TEXT PRIMARY KEY, match_id TEXT NOT NULL, captured REAL NOT NULL,
                    observation TEXT NOT NULL, priority INTEGER NOT NULL, image BLOB NOT NULL);
                CREATE INDEX IF NOT EXISTS review_frame_match ON review_frames(match_id, captured);
            """)
            yield db
    finally:
        db.close()


def decode(row):
    if row is None:
        return None
    item = dict(row)
    item.pop("token", None)
    item["result"] = json.loads(item["result"]) if item.get("result") else None
    return item


def stream(stream_id):
    with database() as db:
        row = db.execute("SELECT data FROM review_streams WHERE id=?", (stream_id,)).fetchone()
    return json.loads(row[0]) if row else {}


def read(match_id, include_frames=False):
    with database() as db:
        item = decode(db.execute("SELECT * FROM review_matches WHERE id=?", (match_id,)).fetchone())
        if item is None:
            raise KeyError(match_id)
        if include_frames:
            item["frames"] = [{"id": r["id"], "captured_at": r["captured"], "priority": r["priority"],
                               "observation": json.loads(r["observation"])} for r in db.execute(
                                   "SELECT id, captured, priority, observation FROM review_frames WHERE match_id=? ORDER BY captured", (match_id,))]
    return item


def history(limit=20, offset=0):
    with database() as db:
        total = db.execute("SELECT count(*) FROM review_matches").fetchone()[0]
        items = [decode(r) for r in db.execute("SELECT * FROM review_matches ORDER BY started DESC LIMIT ? OFFSET ?", (limit, offset))]
    return {"items": items, "total": total}


def image(frame_id):
    with database() as db:
        row = db.execute("SELECT image FROM review_frames WHERE id=?", (frame_id,)).fetchone()
    if row is None:
        raise KeyError(frame_id)
    return row[0]


def ingest(stream_id, observation, captured, pixels):
    """Serialize frames; two distinct, ordered ending observations finalize once."""
    now = time.time()
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT data FROM review_streams WHERE id=?", (stream_id,)).fetchone()
        state = json.loads(row[0]) if row else {}
        if captured <= state.get("last_at", 0):
            return state
        gap = captured - state.get("last_at", captured)
        state.update(last_at=captured, last_observation=observation)
        match = db.execute("SELECT * FROM review_matches WHERE id=?", (state.get("match_id", ""),)).fetchone()
        active = match is not None and match["status"] == "recording"
        valid = observation["game"] != "unknown" and observation["confidence"] >= .85
        playing = valid and observation["phase"] in ("bp", "loading", "in_game") and observation["viewpoint"] != "spectator"
        clock = observation["game_time_s"]
        boundary = active and playing and (
            observation["game"] != match["game"] or gap > 1800 or
            (state.get("played", 0) and observation["phase"] in ("bp", "loading")) or
            (observation["phase"] == "in_game" and clock is not None and state.get("clock", 0) > clock + 120))
        starting = playing and (not active or boundary)
        if starting:
            key = observation["game"] + (":new" if boundary else ":start")
            state["start_count"] = state.get("start_count", 0) + 1 if state.get("start_key") == key and gap < 90 else 1
            state["start_key"] = key
            if state["start_count"] >= 2:
                if active:
                    message = {"status": "incomplete", "source": "local", "points": [],
                               "message": "这局未记录到明确结束画面，过程已保留。"}
                    db.execute("UPDATE review_matches SET status='incomplete', ended=?, updated=?, result=? WHERE id=?",
                               (captured, now, json.dumps(message, ensure_ascii=False), match["id"]))
                match_id = uuid4().hex
                db.execute("INSERT INTO review_matches (id,stream,started,updated,game) VALUES (?,?,?,?,?)",
                           (match_id, stream_id, captured, now, observation["game"]))
                state = {"match_id": match_id, "last_at": captured, "last_observation": observation, "played": 0, "end_count": 0}
                match = db.execute("SELECT * FROM review_matches WHERE id=?", (match_id,)).fetchone()
                active, boundary = True, False
        else:
            state.update(start_key=None, start_count=0)
        if active and not boundary:
            same_game = valid and observation["game"] == match["game"]
            if same_game and observation["phase"] == "in_game" and observation["viewpoint"] == "player":
                state["played"] = state.get("played", 0) + 1
                if clock is not None:
                    state["clock"] = clock
                hero = observation["player_hero"]
                state["hero_count"] = state.get("hero_count", 0) + 1 if hero and hero == state.get("hero_candidate") else int(bool(hero))
                state["hero_candidate"] = hero
                if hero and state["hero_count"] >= 2 and not match["hero"]:
                    db.execute("UPDATE review_matches SET hero=? WHERE id=?", (hero, match["id"]))
                # A different observed hero never replaces the bound player mid-match.
            ending = (same_game and observation["phase"] == "result" and observation["outcome"] != "unknown"
                      and bool(observation["end_text"]) and state.get("played", 0) >= 2)
            if ending:
                state["end_count"] = state.get("end_count", 0) + 1 if state.get("end_outcome") == observation["outcome"] and 1 <= gap <= 90 else 1
                state["end_outcome"] = observation["outcome"]
            else:
                state.update(end_count=0, end_outcome=None)
            if same_game and observation["phase"] in ("in_game", "result") and observation["viewpoint"] != "spectator":
                priority = 3 if ending else 2 if any(f["kind"] in ("death", "objective", "combat") for f in observation["facts"]) else 1 if observation["facts"] else 0
                db.execute("INSERT INTO review_frames VALUES (?,?,?,?,?,?)", (uuid4().hex, match["id"], captured,
                           json.dumps(observation, ensure_ascii=False), priority, pixels))
                db.execute("UPDATE review_matches SET samples=samples+1, updated=? WHERE id=?", (now, match["id"]))
                db.execute("DELETE FROM review_frames WHERE match_id=? AND id NOT IN (SELECT id FROM review_frames WHERE match_id=? ORDER BY priority DESC,captured DESC LIMIT ?)",
                           (match["id"], match["id"], MAX_FRAMES))
            if state.get("end_count", 0) >= 2:
                db.execute("UPDATE review_matches SET status='pending', outcome=?, ended=?, updated=? WHERE id=? AND status='recording'",
                           (observation["outcome"], captured, now, match["id"]))
        db.execute("INSERT INTO review_streams VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                   (stream_id, json.dumps(state, ensure_ascii=False)))
    return state


def pending():
    with database() as db:
        return [r[0] for r in db.execute("SELECT id FROM review_matches WHERE status='pending' OR (status='analyzing' AND updated<?) LIMIT 8", (time.time() - 120,))]


def close_stream(stream_id):
    with database() as db:
        result = {"status": "incomplete", "source": "local", "points": [],
                  "message": "采集已切换，这局未确认结束；已记录的过程保留在这里。"}
        db.execute("UPDATE review_matches SET status='incomplete',ended=?,updated=?,result=? WHERE stream=? AND status='recording'",
                   (time.time(), time.time(), json.dumps(result, ensure_ascii=False), stream_id))


def claim(match_id, retry=False):
    token = uuid4().hex
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT status,updated FROM review_matches WHERE id=?", (match_id,)).fetchone()
        if row is None:
            raise KeyError(match_id)
        allowed = row["status"] == "pending" or (row["status"] == "analyzing" and row["updated"] < time.time() - 120) or (retry and row["status"] == "failed")
        if not allowed:
            return None
        db.execute("UPDATE review_matches SET status='analyzing',updated=?,token=? WHERE id=?", (time.time(), token, match_id))
    return token


def finish(match_id, token, result):
    with database() as db:
        db.execute("UPDATE review_matches SET status=?,result=?,updated=?,token=NULL WHERE id=? AND token=?",
                   (result["status"], json.dumps(result, ensure_ascii=False), time.time(), match_id, token))
