"""Per-user review records; the previous SQLite stores are never opened here."""
import os
from pathlib import Path
import time
from uuid import uuid4

from gameplan.paths import ROOT
from gameplan.persistence.database import connection, jsonb, owner

SELECT = "SELECT m.*, r.result FROM matches m LEFT JOIN match_reviews r ON r.match_id=m.id AND r.user_id=m.user_id"


def asset_root():
    return Path(os.getenv("GAMEPLAN_ASSET_DIR") or ROOT / "data" / "postgres-assets")


def public(row):
    if row is None:
        raise KeyError("match")
    result = dict(row)
    result.pop("token", None)
    result["user_id"] = str(result["user_id"])
    return result


def stream(stream_id):
    with connection() as db:
        row = db.execute("SELECT data FROM review_streams WHERE user_id=%s AND id=%s", (owner(), stream_id)).fetchone()
        return row["data"] if row else {}


def read(match_id, include_frames=False):
    with connection() as db:
        item = public(db.execute(SELECT + " WHERE m.id=%s AND m.user_id=%s", (match_id, owner())).fetchone())
        if include_frames:
            item["frames"] = db.execute("SELECT id,captured AS captured_at,observation,priority FROM match_events WHERE match_id=%s AND user_id=%s ORDER BY captured", (match_id, owner())).fetchall()
    return item


def history(limit=20, offset=0):
    with connection() as db:
        total = db.execute("SELECT count(*) AS n FROM matches WHERE user_id=%s", (owner(),)).fetchone()["n"]
        rows = db.execute(SELECT + " WHERE m.user_id=%s ORDER BY started DESC LIMIT %s OFFSET %s", (owner(), limit, offset)).fetchall()
    return {"items": [public(r) for r in rows], "total": total}


def image(frame_id):
    with connection() as db:
        row = db.execute("SELECT storage_key FROM match_assets WHERE id=%s AND user_id=%s", (frame_id, owner())).fetchone()
    if not row:
        raise KeyError(frame_id)
    try:
        path = (asset_root() / row["storage_key"]).resolve()
        if not path.is_relative_to(asset_root().resolve()):
            raise KeyError(frame_id)
        return path.read_bytes()
    except OSError:
        raise KeyError(frame_id)


def put_result(db, match_id, result):
    return db.execute("""INSERT INTO match_reviews(match_id,user_id,result) VALUES(%s,%s,%s)
        ON CONFLICT(match_id) DO UPDATE SET result=excluded.result,version=match_reviews.version+1,updated_at=now()
        WHERE match_reviews.user_id=excluded.user_id RETURNING version""", (match_id, owner(), jsonb(result))).fetchone()["version"]


def ingest(stream_id, observation, captured, pixels):
    now, uid = time.time(), owner()
    with connection() as db:
        db.execute("INSERT INTO review_streams(user_id,id) VALUES(%s,%s) ON CONFLICT DO NOTHING", (uid, stream_id))
        state = db.execute("SELECT data FROM review_streams WHERE user_id=%s AND id=%s FOR UPDATE", (uid, stream_id)).fetchone()["data"]
        if captured <= state.get("last_at", 0):
            return state
        gap = captured-state.get("last_at", captured)
        state.update(last_at=captured, last_observation=observation)
        match = db.execute("SELECT * FROM matches WHERE id=%s AND user_id=%s FOR UPDATE", (state.get("match_id", ""), uid)).fetchone()
        active = match is not None and match["status"] == "recording"
        valid = observation["game"] != "unknown" and observation["confidence"] >= .85
        playing = valid and observation["phase"] in ("bp", "loading", "in_game") and observation["viewpoint"] != "spectator"
        clock = observation["game_time_s"]
        boundary = active and playing and (observation["game"] != match["game"] or gap > 1800 or
            (state.get("played", 0) and observation["phase"] in ("bp", "loading")) or
            (observation["phase"] == "in_game" and clock is not None and state.get("clock", 0) > clock+120))
        if playing and (not active or boundary):
            key = observation["game"] + (":new" if boundary else ":start")
            state["start_count"] = state.get("start_count", 0)+1 if state.get("start_key") == key and gap < 90 else 1
            state["start_key"] = key
            if state["start_count"] >= 2:
                if active:
                    db.execute("UPDATE matches SET status='incomplete',ended=%s,updated=%s WHERE id=%s AND user_id=%s", (captured, now, match["id"], uid))
                    put_result(db, match["id"], {"status": "incomplete", "source": "local", "points": [], "message": "本局未确认结束，已保存过程记录。"})
                mid = uuid4().hex
                db.execute("INSERT INTO matches(id,user_id,stream,started,updated,game) VALUES(%s,%s,%s,%s,%s,%s)", (mid, uid, stream_id, captured, now, observation["game"]))
                state = {"match_id": mid, "last_at": captured, "last_observation": observation, "played": 0, "end_count": 0}
                match = db.execute("SELECT * FROM matches WHERE id=%s AND user_id=%s", (mid, uid)).fetchone()
                active, boundary = True, False
        else:
            state.update(start_key=None, start_count=0)
        if active and not boundary:
            same = valid and observation["game"] == match["game"]
            if same and observation["phase"] == "in_game" and observation["viewpoint"] == "player":
                state["played"] = state.get("played", 0)+1
                if clock is not None:
                    state["clock"] = clock
                hero = observation["player_hero"]
                state["hero_count"] = state.get("hero_count", 0)+1 if hero and hero == state.get("hero_candidate") else int(bool(hero))
                state["hero_candidate"] = hero
                if hero and state["hero_count"] >= 2 and not match["hero"]:
                    db.execute("UPDATE matches SET hero=%s WHERE id=%s AND user_id=%s", (hero, match["id"], uid))
            ending = same and observation["phase"] == "result" and observation["outcome"] != "unknown" and bool(observation["end_text"]) and state.get("played", 0) >= 2
            if ending:
                state["end_count"] = state.get("end_count", 0)+1 if state.get("end_outcome") == observation["outcome"] and 1 <= gap <= 90 else 1
                state["end_outcome"] = observation["outcome"]
            else:
                state.update(end_count=0, end_outcome=None)
            if same and observation["phase"] in ("in_game", "result") and observation["viewpoint"] != "spectator":
                fid = uuid4().hex
                storage_key = uid + "/" + fid + ".jpg"
                path = asset_root() / storage_key
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(pixels)
                priority = 3 if ending else 2 if any(f["kind"] in ("death", "objective", "combat") for f in observation["facts"]) else int(bool(observation["facts"]))
                db.execute("INSERT INTO match_assets(id,user_id,match_id,storage_key) VALUES(%s,%s,%s,%s)", (fid, uid, match["id"], storage_key))
                db.execute("INSERT INTO match_events VALUES(%s,%s,%s,%s,%s,%s)", (fid, uid, match["id"], captured, jsonb(observation), priority))
                db.execute("UPDATE matches SET samples=samples+1,updated=%s WHERE id=%s AND user_id=%s", (now, match["id"], uid))
            if state.get("end_count", 0) >= 2:
                db.execute("UPDATE matches SET status='pending',outcome=%s,ended=%s,updated=%s WHERE id=%s AND user_id=%s AND status='recording'", (observation["outcome"], captured, now, match["id"], uid))
        db.execute("UPDATE review_streams SET data=%s WHERE user_id=%s AND id=%s", (jsonb(state), uid, stream_id))
    return state


def pending():
    with connection() as db:
        return [r["id"] for r in db.execute("SELECT id FROM matches WHERE user_id=%s AND (status='pending' OR (status='analyzing' AND updated<%s)) LIMIT 8", (owner(), time.time()-120)).fetchall()]


def close_stream(stream_id):
    with connection() as db:
        rows = db.execute("UPDATE matches SET status='incomplete',ended=%s,updated=%s WHERE user_id=%s AND stream=%s AND status='recording' RETURNING id", (time.time(), time.time(), owner(), stream_id)).fetchall()
        for row in rows:
            put_result(db, row["id"], {"status": "incomplete", "source": "local", "points": [], "message": "采集已切换，本局过程已保存。"})


def claim(match_id, retry=False):
    token = uuid4().hex
    with connection() as db:
        row = db.execute("SELECT status,updated FROM matches WHERE id=%s AND user_id=%s FOR UPDATE", (match_id, owner())).fetchone()
        if not row:
            raise KeyError(match_id)
        if not (row["status"] == "pending" or (row["status"] == "analyzing" and row["updated"] < time.time()-120) or (retry and row["status"] == "failed")):
            return None
        db.execute("UPDATE matches SET status='analyzing',updated=%s,token=%s WHERE id=%s AND user_id=%s", (time.time(), token, match_id, owner()))
    return token


def finish(match_id, token, result):
    with connection() as db:
        row = db.execute("UPDATE matches SET status=%s,updated=%s,token=NULL WHERE id=%s AND user_id=%s AND token=%s RETURNING id", (result["status"], time.time(), match_id, owner(), token)).fetchone()
        if not row:
            return
        version = put_result(db, match_id, result)
        if result["status"] in ("ready", "insufficient"):
            db.execute("""INSERT INTO profile_update_jobs(id,user_id,match_id,review_version) VALUES(%s,%s,%s,%s)
                ON CONFLICT(user_id,match_id,review_version) DO NOTHING""", (uuid4().hex, owner(), match_id, version))


from gameplan.persistence.database import dispatch
dispatch(globals(), 'gameplan.persistence.redis_reviews', 'asset_root stream read history image ingest pending close_stream claim finish')
