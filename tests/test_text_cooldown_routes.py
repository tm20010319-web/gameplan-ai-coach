from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from gameplan.web.monitor_app import app as monitor
from gameplan.web.app import app as workbench
from gameplan.core import storage


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    # Copy only the schema into an isolated database, never user records.
    with storage.database() as db:
        schema = [row[0] for row in db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL")]
    monkeypatch.setattr(storage, "DATA", tmp_path)
    with storage.database() as db:
        for sql in schema:
            db.execute(sql)


@pytest.mark.parametrize("app", [monitor, workbench])
def test_text_loop_persists_and_replaces(app):
    path = f"/api/matches/{uuid4().hex}"
    with TestClient(app) as client:
        for text in ["妲己放大了", "妲己闪现了", "朵莉亚放大了", "大乔放大了"]:
            response = client.post(path + "/text-command", json={"text": text})
            assert response.status_code == 200, response.text
        rows = client.get(path + "/skill-timers").json()["timers"]
        assert len(rows) == 4
        assert all(row["source"] == "text" for row in rows)
        assert next(r for r in rows if r["slot"] == 5)["seconds"] == 120
        assert next(r for r in rows if r["hero"] == "朵莉亚")["ready_epoch"] is None
        assert next(r for r in rows if r["hero"] == "大乔")["slot"] == 4
        old = next(r for r in rows if r["hero"] == "妲己" and r["slot"] == 3)
        updated = client.post(path + "/text-command", json={"text": "小象助手，妲己放大了"}).json()["skill_timers"]
        assert len(updated["timers"]) == 4
        assert old["id"] not in [r["id"] for r in updated["timers"]]
        assert client.post(path + "/text-command", json={"text": "妲己没有放大了"}).status_code == 422
    with TestClient(app) as reloaded:
        assert reloaded.get(path + "/skill-timers").json()["timers"] == updated["timers"]
        unknown = next(r for r in updated["timers"] if r["ready_epoch"] is None)
        removed = reloaded.post(path + f"/skill-timers/{unknown['id']}/cancel").json()
        assert len(removed["timers"]) == 3
        assert reloaded.post(path + "/skill-timers/clear").json()["timers"] == []
        assert len(reloaded.get(path + "/skill-timers").json()["history"]) == 5


def test_voice_still_requires_wake_word():
    with TestClient(monitor) as client:
        path = f"/api/matches/{uuid4().hex}/voice-command"
        assert client.post(path, json={"text": "妲己放大了"}).status_code == 422
        assert client.post(path, json={"text": "小象助手，妲己闪现了"}).status_code == 200


@pytest.mark.parametrize("app", [monitor, workbench])
def test_group_aliases_persistence_and_replacement(app):
    match = uuid4().hex
    path = f"/api/matches/{match}"
    with TestClient(app) as client:
        response = client.post(path + "/text-command", json={"text": "后裔小明都放大招了"})
        assert response.status_code == 200, response.text
        data = response.json()
        assert [row["hero"] for row in data["timers"]] == ["后羿", "明世隐"]
        assert all(row["recognized_text"] == "小象助手，后裔小明都放大招了" for row in data["timers"])
        assert "后羿" in data["message"] and "明世隐" in data["message"]
        assert client.get(path + "/skill-timers").json()["timers"] == data["timers"]
        again = client.post(path + "/text-command", json={"text": "后羿和明世隐都开大了"}).json()
        assert len(again["skill_timers"]["timers"]) == 2
        assert not set(r["id"] for r in again["timers"]) & set(r["id"] for r in data["timers"])
        flash = client.post(path + "/text-command", json={"text": "后羿、小明都闪现了"}).json()
        assert len(flash["skill_timers"]["timers"]) == 4
        assert all(r["seconds"] == 120 for r in flash["timers"])


@pytest.mark.parametrize("text", [
    "后裔未知英雄都放大招了", "后裔小明都没放大招", "后裔小明都放大招了吗",
    "后裔小明都放大招了？", "后裔小明可能都放大招了", "后裔和都放大招了",
])
def test_invalid_group_does_not_partially_register(text):
    path = f"/api/matches/{uuid4().hex}"
    with TestClient(monitor) as client:
        assert client.post(path + "/text-command", json={"text": text}).status_code == 422
        assert client.get(path + "/skill-timers").json()["history"] == []


def test_group_failure_rolls_back_first_registration():
    match = uuid4().hex
    memory = storage.memory(match)
    existing = [{"id": str(i), "hero": "placeholder" + str(i)} for i in range(39)]
    memory["skill_timers"] = existing
    storage.save_memory(match, memory)
    with TestClient(monitor) as client:
        response = client.post(f"/api/matches/{match}/text-command", json={"text": "后裔小明都放大招了"})
        assert response.status_code == 422
    assert storage.memory(match) == memory


def test_group_unknown_cooldown_is_kept_and_duplicates_are_collapsed():
    with TestClient(monitor) as client:
        response = client.post(f"/api/matches/{uuid4().hex}/text-command", json={"text": "后裔后羿朵莉亚都放大招了"})
        assert response.status_code == 200, response.text
        rows = response.json()["timers"]
        assert len(rows) == 2
        assert rows[1]["ready_epoch"] is None


@pytest.mark.parametrize("app", [monitor, workbench])
@pytest.mark.parametrize("text", ["鲁班放大了", "鲁班小明都放大了", "元流之子放大了"])
def test_ambiguous_names_require_confirmation_before_saving(app, text):
    match = uuid4().hex
    path = f"/api/matches/{match}"
    with TestClient(app) as client:
        before = client.get(path + "/skill-timers").json()
        data = client.post(path + "/text-command", json={"text": text}).json()
        assert data["ok"] is False
        assert data["status"] == "needs_confirmation"
        after = client.get(path + "/skill-timers").json()
        assert after["history"] == before["history"] == []
        assert after["revision"] == before["revision"]
        choice = data["candidates"][-1]
        confirmed = client.post(path + "/text-command", json={"text": choice["text"]}).json()
        assert confirmed["ok"] is True
        assert [r["hero"] for r in confirmed["timers"]] == choice["heroes"]


@pytest.mark.parametrize("app", [monitor, workbench])
@pytest.mark.parametrize("text", ["六耳没大招", "六耳没大招了", "六耳放大了", "猴子闪现了", "六耳小明都没大招"])
def test_report_delay_applies_once_and_survives_reload(app, text):
    from unittest.mock import patch
    path = f"/api/matches/{uuid4().hex}"
    with TestClient(app) as client, patch("gameplan.skills.cooldowns.time.time", return_value=1000):
        response = client.post(path + "/text-command", json={"text": text})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["ok"] is True
        for timer in data["timers"]:
            assert timer["recorded_epoch"] == 1000
            assert timer["cast_epoch"] == 997
            assert timer["ready_epoch"] == 997 + timer["seconds"]
            assert timer["report_delay_s"] == 3
        assert "已补偿3秒" in data["message"]
        assert client.get(path + "/skill-timers").json()["timers"] == data["timers"]
    with TestClient(app) as client:
        assert client.get(path + "/skill-timers").json()["timers"] == data["timers"]


def test_voice_and_short_cooldown_delay_and_unknown():
    from unittest.mock import patch
    path = f"/api/matches/{uuid4().hex}"
    with TestClient(monitor) as client:
        flash = client.post(path + "/voice-command", json={"text": "小象助手，猴子闪现了"}).json()["timer"]
        assert flash["ready_epoch"] - flash["recorded_epoch"] == 117
        unknown = client.post(path + "/text-command", json={"text": "朵莉亚没大招"}).json()["timer"]
        assert unknown["ready_epoch"] is None
        with patch("gameplan.voice.cooldown_assistant.VoiceCooldownAssistant._cooldown_for", return_value=("女王崇拜", 2)):
            short = client.post(path + "/text-command", json={"text": "妲己没大招"}).json()["timer"]
            assert short["ready_epoch"] == short["recorded_epoch"]


@pytest.mark.parametrize("text", ["六耳没有放大了", "六耳没学大招", "六耳没大招吗", "六耳没大招？", "六耳可能没大招"])
def test_unavailability_shorthand_does_not_accept_other_negatives(text):
    with TestClient(monitor) as client:
        path = f"/api/matches/{uuid4().hex}"
        assert client.post(path + "/text-command", json={"text": text}).status_code == 422
        assert client.get(path + "/skill-timers").json()["timers"] == []
