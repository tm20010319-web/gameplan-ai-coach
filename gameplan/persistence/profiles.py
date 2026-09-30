"""Durable outbox jobs, frozen inputs, checkpoints and atomic profile versions."""
from uuid import uuid4

from gameplan.ai.player_profile import PROMPT_VERSION, merge
from gameplan.persistence.database import connection, jsonb, owner


def read(user_id=None):
    uid = user_id or owner()
    with connection() as db:
        row = db.execute("SELECT version,data,updated_at FROM player_profiles WHERE user_id=%s", (uid,)).fetchone()
    return {"user_id": str(uid), **(row or {"version": 0, "data": {"facts": []}, "updated_at": None})}


def versions(user_id=None, limit=20, offset=0):
    with connection() as db:
        return db.execute("SELECT version,match_id,changes,model,created_at FROM profile_versions WHERE user_id=%s ORDER BY version DESC LIMIT %s OFFSET %s", (user_id or owner(), limit, offset)).fetchall()


def evidence(scope, key, user_id=None):
    with connection() as db:
        return db.execute("""SELECT e.source_id,e.content FROM profile_evidence e
            JOIN profile_facts f USING(user_id,scope,key)
            WHERE e.user_id=%s AND e.scope=%s AND e.key=%s
            AND (f.data->'evidence_ids') ? e.source_id
            ORDER BY e.source_id LIMIT 100""", (user_id or owner(), scope, key)).fetchall()


def jobs(user_id=None):
    with connection() as db:
        return db.execute("SELECT id,match_id,status,attempts,error,created_at,updated_at FROM profile_update_jobs WHERE user_id=%s ORDER BY created_at DESC LIMIT 20", (user_id or owner(),)).fetchall()


def retry(job_id):
    with connection() as db:
        return db.execute("UPDATE profile_update_jobs SET status='pending',attempts=0,error=NULL,available_at=now(),updated_at=now() WHERE id=%s AND user_id=%s AND status='failed' RETURNING id", (job_id, owner())).fetchone() is not None


def claim():
    """One active job per user, including during recovery after a process crash."""
    with connection() as db:
        candidates = db.execute("""SELECT * FROM profile_update_jobs j
            WHERE ((status='pending' AND available_at<=now()) OR (status='running' AND lease_until<now()))
            AND NOT EXISTS (SELECT 1 FROM profile_update_jobs old WHERE old.user_id=j.user_id
                AND old.status IN ('pending','running','failed') AND (old.created_at,old.id)<(j.created_at,j.id))
            ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 8""").fetchall()
        for row in candidates:
            # All workers take this lock before modifying a user's profile.
            if not db.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS acquired", (str(row["user_id"]),)).fetchone()["acquired"]:
                continue
            other = db.execute("SELECT 1 FROM profile_update_jobs WHERE user_id=%s AND id<>%s AND status='running' AND lease_until>now()", (row["user_id"], row["id"])).fetchone()
            if other:
                continue
            token = uuid4().hex
            db.execute("INSERT INTO player_profiles(user_id) VALUES(%s) ON CONFLICT DO NOTHING", (row["user_id"],))
            profile = db.execute("SELECT * FROM player_profiles WHERE user_id=%s FOR UPDATE", (row["user_id"],)).fetchone()
            if row["input"] is None:
                review = db.execute("SELECT r.result,m.game,m.hero FROM match_reviews r JOIN matches m ON m.id=r.match_id AND m.user_id=r.user_id WHERE r.match_id=%s AND r.user_id=%s AND r.version=%s", (row["match_id"], row["user_id"], row["review_version"])).fetchone()
                if not review:
                    db.execute("UPDATE profile_update_jobs SET status='failed',error='复盘版本已变化，请核对记录。' WHERE id=%s", (row["id"],))
                    continue
                messages = db.execute("""SELECT m.id,m.content,m.created_at FROM chat_messages m
                    WHERE m.user_id=%s AND m.role='user' AND m.created_at<=%s
                    AND NOT EXISTS (SELECT 1 FROM profile_consumed_messages c WHERE c.message_id=m.id)
                    ORDER BY m.id""", (row["user_id"], row["created_at"])).fetchall()
                sources = [{"id": "message:"+str(m["id"]), "kind": "user_message", "text": m["content"]} for m in messages]
                for i, point in enumerate(review["result"].get("points", [])):
                    sources.append({"id": f"review:{row['match_id']}:{row['review_version']}:{i}", "kind": "review",
                        "game": review["game"], "match_id": row["match_id"], "hero": review["hero"],
                        "text": point["issue"] + "；建议：" + point["advice"], "frame_ids": point.get("evidence_ids", [])})
                row["input"] = {"base_version": profile["version"], "profile": profile["data"], "sources": sources,
                                "message_ids": [m["id"] for m in messages], "prompt_version": PROMPT_VERSION}
            row.update(token=token, status="running")
            db.execute("UPDATE profile_update_jobs SET status='running',attempts=attempts+1,token=%s,lease_until=now()+interval '120 seconds',input=%s,updated_at=now() WHERE id=%s", (token, jsonb(row["input"]), row["id"]))
            return row
    return None


def checkpoint(job, progress, changes, model):
    with connection() as db:
        return db.execute("UPDATE profile_update_jobs SET progress=%s,changes=%s,model=%s,lease_until=now()+interval '120 seconds',updated_at=now() WHERE id=%s AND token=%s AND status='running' RETURNING id",
            (progress, jsonb(changes), model, job["id"], job["token"])).fetchone() is not None


def complete(job, changes, model):
    uid, payload = str(job["user_id"]), job["input"]
    with connection() as db:
        locked = db.execute("SELECT * FROM profile_update_jobs WHERE id=%s AND token=%s AND status='running' FOR UPDATE", (job["id"], job["token"])).fetchone()
        if not locked:
            return False
        row = db.execute("SELECT * FROM player_profiles WHERE user_id=%s FOR UPDATE", (uid,)).fetchone()
        if row["version"] != payload["base_version"]:
            raise RuntimeError("画像版本发生变化，保留输入等待核对。")
        data = merge(row["data"], changes, payload["sources"])
        version = row["version"]+1
        db.execute("INSERT INTO profile_versions(user_id,version,job_id,match_id,data,changes,model) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                   (uid, version, job["id"], job["match_id"], jsonb(data), jsonb(changes), model))
        sources = {s["id"]: s for s in payload["sources"]}
        for fact in data["facts"]:
            db.execute("""INSERT INTO profile_facts(user_id,scope,key,data,updated_version) VALUES(%s,%s,%s,%s,%s)
                ON CONFLICT(user_id,scope,key) DO UPDATE SET data=excluded.data,updated_version=excluded.updated_version""",
                (uid, fact["scope"], fact["key"], jsonb(fact), version))
            for eid in fact["evidence_ids"]:
                if eid in sources:
                    db.execute("INSERT INTO profile_evidence VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", (uid, fact["scope"], fact["key"], eid, job["id"], jsonb(sources[eid])))
        for message_id in payload["message_ids"]:
            db.execute("INSERT INTO profile_consumed_messages VALUES(%s,%s,%s) ON CONFLICT DO NOTHING", (message_id, uid, job["id"]))
        db.execute("UPDATE player_profiles SET version=%s,data=%s,updated_at=now() WHERE user_id=%s", (version, jsonb(data), uid))
        db.execute("UPDATE profile_update_jobs SET status='done',changes=%s,model=%s,error=NULL,token=NULL,lease_until=NULL,updated_at=now() WHERE id=%s", (jsonb(changes), model, job["id"]))
    return True


def fail(job):
    with connection() as db:
        db.execute("""UPDATE profile_update_jobs SET status=CASE WHEN attempts>=3 THEN 'failed' ELSE 'pending' END,
            error='画像分析暂未完成，旧画像和原始记录已保留。',available_at=now()+interval '30 seconds',
            token=NULL,lease_until=NULL,updated_at=now() WHERE id=%s AND token=%s""", (job["id"], job["token"]))


from gameplan.persistence.database import dispatch
dispatch(globals(), 'gameplan.persistence.redis_profiles', 'read versions evidence jobs retry claim checkpoint complete fail')
