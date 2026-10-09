"""Service boundary, real knowledge answers, durable sessions and private ownership."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from gameplan.ai import knowledge_chat
from gameplan.persistence import database, king_sessions, chats, redis_store as redis
from gameplan.web.king_agent_routes import router
from tests.test_redis_persistence import redis_store
from tests.test_player_profiles_pg import pg

TOKEN = "test-service-token-for-root-connection-123456"


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setenv("KING_AGENT_SERVICE_TOKEN", TOKEN)
    def no_model(*args, **kwargs):
        raise AssertionError("Local facts must not call an external model")
    monkeypatch.setattr(knowledge_chat, "post_json", no_model)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


def ask(client, text="妲己大招冷却多久", *, tenant="sale", user="101", session="root-chat", request="q-1", token=TOKEN):
    return client.post("/api/king-agent/query", headers={"Authorization": "Bearer " + token}, json={
        "identity": {"tenant_id": tenant, "user_id": user}, "session_id": session,
        "request_id": request, "query": text})


def check_chat(client):
    first = ask(client)
    assert first.status_code == 200, first.text
    result = first.json()
    assert (result["agent"], result["status"]) == ("KING", "SUCCESS")
    assert "15、13.5、12秒" in result["answer"]
    assert result["session_id"] == "root-chat"
    uid, sid = king_sessions.ensure("sale", "101", "root-chat")
    assert result["data"]["chat_session_id"] == sid
    assert ask(client, "他的一技能冷却多久", request="q-2").json()["status"] == "SUCCESS"
    cast = ask(client, "小明大招用了", request="q-3").json()
    assert cast["data"]["timers"][0]["hero"] == "明世隐"
    replay = ask(client, "小明大招用了", request="q-3").json()
    assert replay["data"]["timers"] == cast["data"]["timers"]
    assert replay["answer"] == cast["answer"]
    assert ask(client, "妲己大招用了", request="q-3").status_code == 409
    # A fresh binding lookup has no process-local mapping; durable context survives it.
    assert king_sessions.ensure("sale", "101", "root-chat") == (uid, sid)
    with database.as_user(uid):
        state = chats.read(sid)
        assert state["revision"] == 3 and len(state["turns"]) == 3
    for tenant, user in (("sale", "102"), ("other-sale", "101")):
        other_uid, other_sid = king_sessions.ensure(tenant, user, "root-chat")
        assert (other_uid, other_sid) != (uid, sid)
        with database.as_user(other_uid):
            with pytest.raises(KeyError):
                chats.read(sid)
            assert not chats.read(other_sid)["turns"]


def test_real_answers_followups_timer_retry_and_isolation_redis(gateway, redis_store):
    check_chat(gateway)


def test_real_answers_followups_timer_retry_and_isolation_postgres(gateway, pg, monkeypatch):
    monkeypatch.delenv("GAMEPLAN_REDIS_URL", raising=False)
    check_chat(gateway)


def test_ambiguous_hero_keeps_pending_timer_context(gateway, redis_store):
    result = ask(gateway, "鲁班大招用了").json()
    assert result["status"] == "NEED_CLARIFICATION" and result["data"]["choices"]
    selected = ask(gateway, "鲁班七号", request="q-2").json()
    assert selected["status"] == "SUCCESS"
    assert selected["data"]["timers"][0]["hero"] == "鲁班七号"


def test_auth_configuration_and_schema_fail_closed(gateway, redis_store, monkeypatch):
    assert ask(gateway, token="wrong").status_code == 401
    assert gateway.post("/api/king-agent/query", json={}).status_code == 422
    malformed = {"identity": {"tenant_id": "sale", "user_id": " 101 "},
                 "session_id": "s", "request_id": "r", "query": "test"}
    assert gateway.post("/api/king-agent/query", json=malformed,
        headers={"Authorization": "Bearer " + TOKEN}).status_code == 422
    monkeypatch.setenv("KING_AGENT_SERVICE_TOKEN", "")
    assert ask(gateway).status_code == 503


def test_missing_database_never_falls_back_to_legacy(gateway, monkeypatch):
    monkeypatch.delenv("GAMEPLAN_REDIS_URL", raising=False)
    monkeypatch.delenv("GAMEPLAN_DATABASE_URL", raising=False)
    assert ask(gateway).status_code == 503


def test_disabled_user_cannot_be_reactivated_by_root(gateway, redis_store):
    uid, sid = king_sessions.ensure("sale", "101", "root-chat")
    @redis.atomic
    def disable():
        redis.tx().get("users", uid)["active"] = False
    disable()
    assert ask(gateway).status_code == 403


def test_backend_error_does_not_leak_internal_details(gateway, redis_store, monkeypatch):
    def broken(*args):
        raise RuntimeError("private password and database path")
    monkeypatch.setattr(king_sessions, "ensure", broken)
    response = ask(gateway)
    assert response.status_code == 503 and "password" not in response.text


@pytest.mark.parametrize("module", ["gameplan.web.app", "gameplan.web.monitor_app"])
def test_existing_servers_mount_bridge(module, monkeypatch):
    from importlib import import_module
    app = import_module(module).app
    monkeypatch.setenv("KING_AGENT_SERVICE_TOKEN", TOKEN)
    response = ask(TestClient(app), token="wrong")
    assert response.status_code == 401
