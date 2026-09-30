"""Opt-in integration tests against an isolated, disposable PostgreSQL schema.

Run with GAMEPLAN_TEST_POSTGRES=1. Credentials come from the local .env;
the production schema and legacy SQLite files are never changed.
"""
import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from gameplan.persistence import database as d, chats, reviews, profiles, timers
from gameplan.ai import player_profile, profile_worker


@pytest.fixture
def pg(monkeypatch, tmp_path):
    import os
    if os.getenv("GAMEPLAN_TEST_POSTGRES") != "1":
        pytest.skip("Set GAMEPLAN_TEST_POSTGRES=1 to run isolated PostgreSQL tests")
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo
    from dotenv import dotenv_values
    url = dotenv_values(".env").get("GAMEPLAN_DATABASE_URL")
    assert url, "Local PostgreSQL must be configured"
    schema = "gp_test_" + uuid4().hex
    legacy = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path("data").glob("*.sqlite3")}
    with psycopg.connect(url) as db:
        db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    monkeypatch.setenv("GAMEPLAN_DATABASE_URL", make_conninfo(url, options=f"-c search_path={schema}"))
    monkeypatch.setenv("GAMEPLAN_ASSET_DIR", str(tmp_path / "assets"))
    try:
        d.migrate()
        users = [str(uuid4()) for _ in range(3)]
        with d.connection() as db:
            for uid in users:
                db.execute("INSERT INTO users(id,display_name) VALUES(%s,'test player')", (uid,))
                db.execute("INSERT INTO user_roles VALUES(%s,'player')", (uid,))
        yield users
    finally:
        with psycopg.connect(url) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in legacy.items())


def say(sid, text, request=None):
    request = request or uuid4().hex
    state = chats.read(sid)
    job = chats.begin(sid, request, state["revision"], text)
    chats.finish(sid, job["token"], request, text, {"answer": "收到", "context": {}})
    return request


def match():
    from gameplan.ai.match_review import ReviewObservation
    observation = ReviewObservation(game="hok", phase="in_game", confidence=.99,
        viewpoint="player", player_hero="孙尚香", player_confidence=.99).model_dump()
    state = {}
    start = reviews.stream("same-stream").get("last_at", 90) + 10
    for i in range(3):
        state = reviews.ingest("same-stream", observation, start+i*10, b"test-image")
    observation.update(phase="result", outcome="win", end_text="胜利")
    for i in range(2):
        state = reviews.ingest("same-stream", observation, start+30+i*10, b"test-image")
    mid = state["match_id"]
    token = reviews.claim(mid)
    assert token
    reviews.finish(mid, token, {"status": "ready", "points": [{"issue": "这次站位靠前", "advice": "等前排再进场", "evidence_ids": []}]})
    reviews.finish(mid, token, {"status": "ready", "points": []})
    return mid


def change(source, value="建议只说一两点"):
    return {"scope": "global", "key": "response_length", "category": "communication",
            "value": value, "basis": "self_report", "evidence_ids": [source["id"]]}


def test_full_chat_retry_and_user_isolation(pg):
    a, b, _ = pg
    with d.as_user(a):
        sid = chats.create()["id"]
        first = say(sid, "first")
        for i in range(24):
            say(sid, f"message {i}")
        assert len(chats.read(sid)["turns"]) == 25
        assert "cached" in chats.begin(sid, first, 0, "first")
        page = chats.messages(sid, limit=20)
        assert len(page["items"]) == 20 and page["next_before"]
        job = chats.begin(sid, "failed-reply", 25, "keep my text")
        chats.release(sid, job["token"])
        assert chats.messages(sid)["items"][-1]["content"] == "keep my text"
        retried = chats.begin(sid, "failed-reply", 25, "keep my text")
        chats.finish(sid, retried["token"], "failed-reply", "keep my text", {"answer": "ok", "context": {}})
        mid = match()
        fid = reviews.read(mid, True)["frames"][0]["id"]
        timers.memory("same")
        timers.save_memory("same", {"private": "A"})
    with d.as_user(b):
        with pytest.raises(KeyError): chats.read(sid)
        with pytest.raises(KeyError): reviews.read(mid)
        with pytest.raises(KeyError): reviews.image(fid)
        assert reviews.stream("same-stream") == {}
        assert timers.memory("same") == {}
        assert reviews.history()["total"] == 0


def test_outbox_worker_versions_and_frozen_input(pg, monkeypatch):
    with d.as_user(pg[0]):
        sid = chats.create()["id"]
        say(sid, "建议只说一两点")
        match()
        assert len(profiles.jobs()) == 1
        job = profiles.claim()
        say(sid, "新消息应留给下一局")
        assert len(job["input"]["message_ids"]) == 1
        assert not profiles.claim()
        monkeypatch.setattr(player_profile, "extract", lambda p, sources: ([change(sources[0])], "mock"))
        profile_worker.process(job)
        assert profiles.read()["version"] == 1
        assert not profiles.complete(job, [], "mock")
        assert len(profiles.versions()) == 1
        assert profiles.evidence("global", "response_length")[0]["content"]["text"] == "建议只说一两点"
        match()
        next_job = profiles.claim()
        assert [s["text"] for s in next_job["input"]["sources"] if s["kind"] == "user_message"] == ["新消息应留给下一局"]
        profiles.complete(next_job, [change(next_job["input"]["sources"][0], "希望更多解释")], "mock")
        assert profiles.read()["version"] == 2
        assert len(profiles.evidence("global", "response_length")) == 1


def test_failed_jobs_lease_recovery_and_separate_users(pg, monkeypatch):
    for uid in pg[:2]:
        with d.as_user(uid): match()
    one, two = profiles.claim(), profiles.claim()
    assert one["user_id"] != two["user_id"]
    with d.connection() as db:
        db.execute("UPDATE profile_update_jobs SET lease_until=now()-interval '1 second' WHERE id=%s", (one["id"],))
    recovered = profiles.claim()
    assert recovered["id"] == one["id"] and recovered["token"] != one["token"]
    assert not profiles.checkpoint(one, 10, [], "old")
    assert not profiles.complete(one, [], "old")
    monkeypatch.setattr(player_profile, "extract", lambda *args: (_ for _ in ()).throw(ValueError("bad model")))
    profile_worker.process(recovered)
    with d.connection() as db:
        db.execute("UPDATE profile_update_jobs SET attempts=3 WHERE id=%s", (two["id"],))
    profiles.fail(two)
    with d.as_user(two["user_id"]):
        assert profiles.read()["version"] == 0
        assert profiles.jobs()[0]["status"] == "failed"
        assert profiles.retry(two["id"])


def test_api_auth_coach_assignment_and_spoofing(pg, monkeypatch):
    from fastapi.testclient import TestClient
    from gameplan.web.monitor_app import app
    a, b, coach = pg
    monkeypatch.setenv("GAMEPLAN_DEV_USER_ID", a)
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 4000))
    assert client.get("/api/me").json()["id"] == a
    assert client.get("/api/profile", params={"user_id": b}, headers={"X-User-Id": b}).status_code == 403
    assert client.get("/api/me", headers={"Origin": "https://other.test"}).status_code == 403
    monkeypatch.setattr(app.state, "resolve_user", lambda request: coach, raising=False)
    assert client.get("/api/profile", params={"user_id": b}).status_code == 403
    with d.connection() as db:
        db.execute("INSERT INTO user_roles VALUES(%s,'coach')", (coach,))
        db.execute("INSERT INTO coach_assignments VALUES(%s,%s)", (coach, b))
    assert client.get("/api/profile", params={"user_id": b}).status_code == 200
    assert client.get("/api/profile", params={"user_id": a}).status_code == 403
    with d.connection() as db:
        assert db.execute("SELECT count(*) AS n FROM access_audit").fetchone()["n"] == 1
    monkeypatch.delattr(app.state, "resolve_user")
    remote = TestClient(app, base_url="http://example.test", client=("10.0.0.5", 4000))
    assert remote.get("/api/me").status_code == 403
    monkeypatch.setattr(app.state, "resolve_user", lambda request: a, raising=False)
    assert remote.post("/api/chat/sessions").status_code == 200
    assert remote.get("/api/reviews").status_code == 200
    assert remote.post("/api/chat/sessions", headers={"Origin": "https://other.test"}).status_code == 403
    client.close()
    remote.close()


def test_explicit_preferences_not_overwritten_by_inference():
    source = {"id": "m:1", "kind": "user_message"}
    original = player_profile.merge({}, [change(source)], [source])
    inferred = {**change(source, "希望长篇说明"), "basis": "tentative"}
    assert player_profile.merge(original, [inferred], [source]) == original


def test_inference_needs_three_distinct_games():
    profile = {}
    for i in range(3):
        source = {"id": f"r:{i}", "kind": "review", "match_id": str(i)}
        c = {**change(source, "进场时机需要练习"), "scope": "hok", "category": "improvement", "basis": "observed"}
        profile = player_profile.merge(profile, [c], [source])
        assert profile["facts"][0]["status"] == ("observed" if i == 2 else "tentative")


def test_live_deepseek_to_postgres(pg, monkeypatch):
    import os
    if os.getenv("GAMEPLAN_TEST_LIVE_MODEL") != "1":
        pytest.skip("Live API calls require GAMEPLAN_TEST_LIVE_MODEL=1")
    from dotenv import dotenv_values
    for key, value in dotenv_values(".env").items():
        if value and key.startswith(("COACH_", "DEEPSEEK_", "DASHSCOPE_", "ARK_")) and key != "COACH_DATA_DIR":
            monkeypatch.setenv(key, value)
    with d.as_user(pg[0]):
        sid = chats.create()["id"]
        say(sid, "我希望小象助手每次只说最重要的一两点建议。")
        match()
        job = profiles.claim()
        profile_worker.process(job)
        assert profiles.jobs()[0]["status"] == "done"
        result = profiles.read()
        assert result["version"] == 1 and result["data"]["facts"]
        assert any(f["basis"] == "self_report" for f in result["data"]["facts"])
