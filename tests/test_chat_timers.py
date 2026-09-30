import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from gameplan.ai import knowledge_chat as chat
from gameplan.core import chat_store
from gameplan.knowledge import chat_knowledge as kb, chat_timers
from gameplan.web.monitor_app import app


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_store, "DATABASE_PATH", tmp_path / "chat.sqlite3")
    def forbidden(*args, **kwargs):
        raise AssertionError("Timer must not call a model")
    monkeypatch.setattr(chat, "post_json", forbidden)


def test_alias_report_starts_full_default_at_receipt_and_pronoun_can_follow():
    result = chat.answer("小明大招用了", received_at=1000)
    timer = result["timer_events"][0]["timer"]
    assert result["status"] == "timer" and result["external_status"] == "not_needed"
    assert (timer["hero"], timer["cooldown_s"], timer["ready_at"]) == ("明世隐", 40, 1040)
    assert "40秒" in result["answer"]
    followup = chat.answer("他的一技能用了", result["context"], received_at=1010)
    assert followup["timer_events"][0]["timer"]["slot"] == 1


@pytest.mark.parametrize("phrase", ["小明大招用了", "小明用了大招", "小明用大招了", "小明放大了", "小明开大了", "小明刚刚放了大招", "小象助手，小明大招用了！", "对面小明的闪现用了"])
def test_cast_phrasings(phrase):
    assert chat.answer(phrase)["status"] == "timer"


@pytest.mark.parametrize("text", ["小明大招没用了", "小明没放大了", "小明大招用了？", "小明大招用了吗", "如果小明大招用了", "小明大招用了怎么办", "小明大招冷却多久", "别给小明大招用了", "小明大招还没用"])
def test_queries_negation_and_hypotheticals_do_not_start_timer(text):
    resolved = kb.resolve(text, {})
    assert chat_timers.answer(resolved, {}) is None


@pytest.mark.parametrize("text", ["鲁班大招用了", "鲁班没大招了"])
def test_ambiguous_alias_confirmation_retains_original_timer_intent(text):
    result = chat.answer(text, received_at=1000)
    assert result["status"] == "clarification" and not result.get("timer_events")
    chosen = chat.answer("鲁班七号", result["context"], received_at=1005)
    assert chosen["timer_events"][0]["timer"]["hero"] == "鲁班七号"
    assert chosen["timer_events"][0]["timer"]["started_at"] == 1005


@pytest.mark.parametrize("report", ["大招用了", "没大招", "没大招了"])
def test_all_132_heroes_and_curated_aliases_are_available(report):
    aliases = json.loads(kb.ALIASES.read_text(encoding="utf-8"))["heroes"]
    rows = [r for r in kb.directory() if r["game"] == "hok"]
    assert len(rows) == len(aliases) == 132
    for row in rows:
        assert aliases[row["name"]]["aliases"]
        assert set(aliases[row["name"]]["aliases"]) <= row["aliases"]
        result = chat.answer("王者荣耀的" + row["name"] + report, received_at=1000)
        assert result["status"] == "timer", (row["name"], result)
        timer = result["timer_events"][0]["timer"]
        assert timer["hero"] == row["name"]
        assert timer["ready_at"] is None or timer["ready_at"] == 1000 + timer["cooldown_s"]


@pytest.mark.parametrize("phrase", ["孙策没大招", "孙策没大招了", "小明没大招", "小明没有大招了", "小明大招没了", "小明没大了", "小明大招用掉了", "对面小明现在没大招了"])
def test_unavailable_ultimate_report_starts_timer(phrase):
    result = chat.answer(phrase, received_at=2000)
    assert result["status"] == "timer" and result["external_status"] == "not_needed"
    timer = result["timer_events"][0]["timer"]
    assert timer["hero"] == ("孙策" if "孙策" in phrase else "明世隐")
    assert timer["skill"] == "大招" and timer["started_at"] == 2000
    assert timer["ready_at"] == 2000 + timer["cooldown_s"]


@pytest.mark.parametrize("phrase", ["小明没大招了吗", "小明没大招？", "如果小明没大招了", "小明没大招怎么办", "小明还没用大招", "小明大招没用", "小明大招没用了"])
def test_unavailable_ultimate_queries_and_not_used_are_not_casts(phrase):
    assert chat_timers.answer(kb.resolve(phrase, {}), {}) is None


def test_unknown_cooldown_never_fabricates_deadline(monkeypatch):
    original = chat_timers.skill_knowledge.get_hero
    def reviewed(hero):
        result = original(hero)
        result["ultimate"]["cooldown_review_required"] = True
        return result
    monkeypatch.setattr(chat_timers.skill_knowledge, "get_hero", reviewed)
    result = chat.answer("小明大招用了")
    assert result["timer_events"][0]["timer"]["ready_at"] is None
    assert "暂不显示倒计时" in result["answer"]


@pytest.mark.parametrize("text", [
    "小明没大招，诸葛亮也没大", "小明没大招,诸葛亮也没大", "小明和诸葛亮都没大招了",
    "小明、诸葛亮没大招", "小象助手，小明没大招；诸葛亮也没大",
])
def test_multiple_reports_start_independent_timers_at_same_receipt(text):
    result = chat.answer(text, received_at=1000)
    timers = [e["timer"] for e in result["timer_events"]]
    assert {t["hero"] for t in timers} == {"明世隐", "诸葛亮"}
    assert all(t["started_at"] == 1000 and t["ready_at"] == 1000 + t["cooldown_s"] for t in timers)
    assert result["external_status"] == "not_needed" and not result["choices"]


def test_batch_supports_five_heroes_different_skills_and_deduplicates_reports():
    result = chat.answer("小明没大招，孙策大招用了，妲己闪现了，诸葛亮一技能用了，铠没大招，小明也没大", received_at=1000)
    timers = [e["timer"] for e in result["timer_events"]]
    assert len(timers) == 5
    assert {t["hero"] for t in timers} == {"明世隐", "孙策", "妲己", "诸葛亮", "铠"}
    assert next(t for t in timers if t["hero"] == "妲己")["slot"] == 5


def test_partial_batch_queues_ambiguities_without_replaying_started_timers():
    initial = chat.answer("小明没大招，鲁班也没大，百里没大招", received_at=1000)
    assert [e["timer"]["hero"] for e in initial["timer_events"]] == ["明世隐"]
    assert len(initial["choices"]) == 2
    second = chat.answer("王者荣耀的鲁班七号", initial["context"], received_at=1010)
    assert [e["timer"]["hero"] for e in second["timer_events"]] == ["鲁班七号"]
    assert second["timer_events"][0]["timer"]["started_at"] == 1010
    assert len(second["choices"]) == 2
    third = chat.answer("百里守约", second["context"], received_at=1020)
    assert [e["timer"]["hero"] for e in third["timer_events"]] == ["百里守约"]
    assert not third["choices"] and third["context"]["pending"] is None
    assert "timer_queue" not in third["context"]


def test_unknown_batch_name_can_be_corrected_and_negative_does_not_start():
    result = chat.answer("小明没大招，不认识的英雄没大招，妲己还没用大招", received_at=1000)
    assert [e["timer"]["hero"] for e in result["timer_events"]] == ["明世隐"]
    fixed = chat.answer("孙策", result["context"], received_at=1010)
    assert [e["timer"]["hero"] for e in fixed["timer_events"]] == ["孙策"]


def test_batch_api_retry_and_confirmation_preserve_original_deadline():
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 4444)) as client:
        session = client.post("/api/chat/sessions").json()
        path = "/api/chat/sessions/" + session["id"] + "/messages"
        data = {"text": "小明没大招，诸葛亮也没大，鲁班没大招", "revision": 0, "request_id": uuid4().hex}
        first = client.post(path, json=data).json()["session"]
        assert len(first["timers"]) == 2
        assert client.post(path, json=data).json()["session"]["timers"] == first["timers"]
        second = client.post(path, json={"text": "鲁班七号", "revision": 1, "request_id": uuid4().hex}).json()["session"]
        assert len(second["timers"]) == 3
        assert second["timers"][:2] == first["timers"]


def test_new_report_and_cancelling_pending_do_not_replay_previous_timers():
    first = chat.answer("小明没大招，鲁班没大招", received_at=1000)
    next_report = chat.answer("孙策没大招", first["context"], received_at=1010)
    assert [e["timer"]["hero"] for e in next_report["timer_events"]] == ["孙策"]
    assert len(next_report["choices"]) == 2
    cancelled = chat.answer("取消确认", next_report["context"])
    assert not cancelled["timer_events"] and cancelled["context"]["pending"] is None
    cleared = chat.answer("清空计时器", first["context"])
    assert cleared["timer_events"] == [{"action": "clear"}]
    assert "timer_queue" not in cleared["context"]


def test_conjunction_inside_official_title_is_not_split_into_heroes():
    result = chat.answer("王者荣耀的和平守望没大招，孙策没大招", received_at=1000)
    assert {e["timer"]["hero"] for e in result["timer_events"]} == {"墨子", "孙策"}


@pytest.mark.parametrize("phrase,hero,slot,seconds", [
    ("寒冰没大招", "Ashe", "R", 100), ("寒冰R用了", "Ashe", "R", 100),
    ("剑魔大招用了", "Aatrox", "R", 120), ("寒冰闪现用了", "Ashe", 5, 300),
    ("寒冰没闪", "Ashe", 5, 300), ("英雄联盟的孙悟空闪现用了", "MonkeyKing", 5, 300),
])
def test_lol_ultimate_and_flash_use_lol_defaults(phrase, hero, slot, seconds):
    result = chat.answer(phrase, received_at=1000)
    timer = result["timer_events"][0]["timer"]
    assert result["external_status"] == "not_needed"
    assert (timer["game"], timer["hero_id"], timer["slot"], timer["ready_at"]) == ("lol", hero, slot, 1000 + seconds)
    assert "急速" in result["answer"]


@pytest.mark.parametrize("skill", ["Q", "W", "E", "一技能", "二技能"])
def test_lol_other_skills_do_not_start_timers(skill):
    result = chat.answer("寒冰" + skill + "用了")
    assert result["timer_events"] == []
    assert "只计大招和闪现" in result["answer"]


def test_lol_all_heroes_have_grounded_ultimate_timer_or_unknown_record():
    records = chat_timers.lol_knowledge.catalog()["heroes"]
    assert len(records) == 173
    for record in records:
        result = chat.answer("英雄联盟的" + record["name"] + "大招用了", received_at=1000)
        assert result.get("timer_events"), (record["id"], result)
        timer = result["timer_events"][0]["timer"]
        skill = next(s for s in record["skills"] if s["slot"] == "R")
        assert timer["hero_id"] == record["id"] and timer["game"] == "lol"
        if not skill["cooldowns"] or any(v <= 0 for v in skill["cooldowns"]):
            assert timer["ready_at"] is None
        else:
            assert timer["ready_at"] == 1000 + skill["cooldowns"][0]


def test_lol_batch_context_ambiguity_and_flash_followup():
    result = chat.answer("寒冰没大招，剑魔闪现用了", received_at=1000)
    assert {(e["timer"]["hero_id"], e["timer"]["slot"]) for e in result["timer_events"]} == {("Ashe", "R"), ("Aatrox", 5)}
    first = chat.answer("猴子大招用了")
    assert first["status"] == "clarification"
    chosen = chat.answer("英雄联盟", first["context"], received_at=1000)
    assert chosen["timer_events"][0]["timer"]["hero_id"] == "MonkeyKing"
    flash = chat.answer("他闪现用了", chosen["context"], received_at=1001)
    assert flash["timer_events"][0]["timer"]["ready_at"] == 1301


@pytest.mark.parametrize("text,expected", [
    ("盖伦，盲僧没大招", {"Garen", "LeeSin"}),
    ("盖伦,寒冰,盲僧没大招", {"Garen", "Ashe", "LeeSin"}),
    ("盖伦和寒冰，盲僧闪现用了", {"Garen", "Ashe", "LeeSin"}),
])
def test_comma_separated_names_share_the_following_cast_report(text, expected):
    result = chat.answer(text, received_at=1000)
    timers = [e["timer"] for e in result["timer_events"]]
    assert {t["hero_id"] for t in timers} == expected
    assert all(t["started_at"] == 1000 for t in timers)
    assert "未作为计时指令" not in result["answer"]


@pytest.mark.parametrize("text", [
    "奶妈，牛头，阿狸没大没闪", "奶妈、牛头和阿狸都没大招没闪现",
    "奶妈，牛头，阿狸大招和闪现都用了", "奶妈，牛头，阿狸没大招也没闪现",
    "奶妈，牛头，阿狸没有大招和闪现", "奶妈，牛头，阿狸大招用了闪现也用了",
])
def test_group_reports_multiple_skills_for_every_hero(text):
    result = chat.answer(text, {"game": "lol"}, received_at=1000)
    timers = [e["timer"] for e in result["timer_events"]]
    assert {(t["hero_id"], t["slot"]) for t in timers} == {
        (hero, slot) for hero in ("Soraka", "Alistar", "Ahri") for slot in ("R", 5)}
    assert len(timers) == 6 and not result["choices"]
    assert all(t["started_at"] == 1000 for t in timers)
    assert all(t["ready_at"] == 1300 for t in timers if t["slot"] == 5)


def test_compound_skill_does_not_spread_to_unrelated_hero_and_deduplicates():
    result = chat.answer("寒冰没大没闪，盖伦没大招，寒冰闪现用了", received_at=1000)
    timers = [e["timer"] for e in result["timer_events"]]
    assert {(t["hero_id"], t["slot"]) for t in timers} == {("Ashe", "R"), ("Ashe", 5), ("Garen", "R")}
    assert len(timers) == 3


def test_compound_ambiguous_name_confirmed_once_starts_both_skills():
    first = chat.answer("鲁班，小明没大没闪", received_at=1000)
    assert len(first["timer_events"]) == 2 and len(first["choices"]) == 2
    second = chat.answer("鲁班七号", first["context"], received_at=1005)
    assert len(second["timer_events"]) == 2 and not second["choices"]
    assert {e["timer"]["hero"] for e in second["timer_events"]} == {"鲁班七号"}
    assert {e["timer"]["slot"] for e in second["timer_events"]} == {3, 5}


@pytest.mark.parametrize("text", ["阿狸没大没闪吗", "阿狸没大没闪？", "如果阿狸没大没闪", "阿狸大招和闪现都没用", "阿狸没大没闪怎么办"])
def test_compound_query_or_hypothetical_does_not_start_timers(text):
    result = chat_timers.batch_answer(text, {"game": "lol"}, 1000)
    assert result is None or not result["timer_events"]


def test_corrected_compound_report_replaces_old_malformed_pending():
    context = {"game": "lol", "pending": {"question": "阿狸没大闪现用了", "choices": []}, "timer_queue": []}
    result = chat.answer("奶妈，牛头，阿狸没大没闪", context, received_at=1000)
    assert len(result["timer_events"]) == 6
    assert result["context"]["pending"] is None and not result["choices"]


def test_compound_timers_persist_retry_without_reset_and_cancel_only_one():
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 4444)) as client:
        state = client.post("/api/chat/sessions").json()
        path = "/api/chat/sessions/" + state["id"]
        payload = {"text": "英雄联盟的奶妈，牛头，阿狸没大没闪", "revision": 0, "request_id": uuid4().hex}
        first = client.post(path + "/messages", json=payload).json()["session"]
        assert len(first["timers"]) == 6
        assert client.get(path).json()["timers"] == first["timers"]
        assert client.post(path + "/messages", json=payload).json()["session"]["timers"] == first["timers"]
        payload = {"text": "取消英雄联盟的阿狸闪现计时", "revision": 1, "request_id": uuid4().hex}
        result = client.post(path + "/messages", json=payload).json()["session"]
        assert result["timers"] == [t for t in first["timers"] if (t["hero_id"], t["slot"]) != ("Ahri", 5)]


def test_shared_report_keeps_ambiguous_name_pending_and_does_not_infer_from_questions():
    result = chat.answer("鲁班，小明没大招", received_at=1000)
    assert [e["timer"]["hero"] for e in result["timer_events"]] == ["明世隐"]
    assert len(result["choices"]) == 2
    chosen = chat.answer("鲁班七号", result["context"], received_at=1005)
    assert [e["timer"]["hero"] for e in chosen["timer_events"]] == ["鲁班七号"]
    assert not chat_timers.batch_answer("盖伦，盲僧大招多久", {}, 1000)
    not_cast = chat_timers.batch_answer("盖伦大招没用，盲僧没大招", {}, 1000)
    assert [e["timer"]["hero_id"] for e in not_cast["timer_events"]] == ["LeeSin"]


def test_cross_game_same_name_flash_persists_and_cancels_independently():
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 4444)) as client:
        state = client.post("/api/chat/sessions").json()
        path = "/api/chat/sessions/" + state["id"]
        payload = {"text": "王者荣耀的孙悟空闪现用了，英雄联盟的孙悟空闪现用了", "revision": 0, "request_id": uuid4().hex}
        state = client.post(path + "/messages", json=payload).json()["session"]
        assert {(t["game"], t["cooldown_s"]) for t in state["timers"]} == {("hok", 120), ("lol", 300)}
        assert client.get(path).json()["timers"] == state["timers"]
        assert client.post(path + "/messages", json=payload).json()["session"]["timers"] == state["timers"]
        payload = {"text": "取消英雄联盟的孙悟空闪现计时", "revision": 1, "request_id": uuid4().hex}
        state = client.post(path + "/messages", json=payload).json()["session"]
        assert len(state["timers"]) == 1 and state["timers"][0]["game"] == "hok"


def test_chat_api_persistence_replay_recast_cancel_and_new_session():
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 4444)) as client:
        session = client.post("/api/chat/sessions").json()
        path = "/api/chat/sessions/" + session["id"]
        def send(text, revision, request_id=None):
            response = client.post(path + "/messages", json={"text": text, "revision": revision, "request_id": request_id or uuid4().hex})
            assert response.status_code == 200, response.text
            return response.json()["session"]
        rid = uuid4().hex
        first = send("小明大招用了", 0, rid)
        assert send("小明大招用了", 0, rid)["timers"] == first["timers"]
        assert client.get(path).json()["timers"] == first["timers"]
        second = send("孙策没大招", 1)
        assert len(second["timers"]) == 2
        third = send("小明大招用了", 2)
        assert len(third["timers"]) == 2 and third["timers"][-1]["id"] != first["timers"][0]["id"]
        fourth = send("你好", 3)
        assert fourth["timers"] == third["timers"]
        fifth = send("取消王者荣耀的明世隐大招计时", 4)
        assert len(fifth["timers"]) == 1 and fifth["timers"][0]["hero"] == "孙策"
        assert client.post("/api/chat/sessions").json()["timers"] == []
        assert send("清空计时器", 5)["timers"] == []
