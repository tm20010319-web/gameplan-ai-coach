import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from gameplan.ai import knowledge_chat as chat
from gameplan.core import chat_store
from gameplan.knowledge import chat_knowledge as kb
from gameplan.web.monitor_app import app as monitor
from gameplan.web.app import app as workbench


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_store, "DATABASE_PATH", tmp_path / "chat.sqlite3")
    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected model request")
    monkeypatch.setattr(chat, "post_json", forbidden)


def test_direct_facts_are_local_and_followups_preserve_hero():
    first = chat.answer("剑魔怎么打")
    assert first["status"] == "local" and first["external_status"] == "not_needed"
    assert first["context"]["heroes"][0]["id"] == "Aatrox"
    second = chat.answer("他的大招冷却多久", first["context"])
    assert "120、100、80秒" in second["answer"]
    assert len(second["sources"]) == 1 and second["sources"][0]["id"] == "lol:Aatrox:cd-R"
    third = chat.answer("那Q呢", second["context"])
    assert "14、12、10、8、6秒" in third["answer"]
    assert "。。" not in first["answer"]


def test_shared_name_clarifies_and_answering_game_completes_original_question():
    initial = chat.answer("孙悟空怎么出装")
    assert initial["status"] == "clarification" and len(initial["choices"]) == 2
    chosen = chat.answer("英雄联盟", initial["context"])
    assert chosen["status"] == "local" and "装备" in chosen["sources"][0]["title"]
    assert chosen["context"]["heroes"][0]["id"] == "MonkeyKing"
    assert all(source["game"] == "lol" for source in chosen["sources"])


def test_cross_game_switch_and_curated_asr_alias():
    old = chat.answer("剑魔怎么打")["context"]
    result = chat.answer("妲已大招冷却多久", old)
    assert result["context"]["game"] == "hok"
    assert "妲己" in result["answer"] and "15、13.5、12秒" in result["answer"]
    scoped = chat.answer("孙悟空大招多久", result["context"])
    assert scoped["context"]["heroes"][0]["game"] == "hok"
    assert chat.answer("王者荣耀")["status"] == "clarification"


def test_skill_name_can_find_hero_without_fuzzy_guessing():
    result = chat.answer("提伯斯之怒冷却多久")
    assert result["context"]["heroes"][0]["id"] == "Annie"
    assert "130、115、100秒" in result["answer"]
    assert kb.resolve("这个很容易理解", {})["clarification"]


def test_existing_item_and_lore_data_are_retrieved_before_external_fallback(monkeypatch):
    def select(question, docs, history):
        kind = "lore" if "背景" in question else "equipment"
        return chat.LocalAnswer(evidence_ids=[next(d["id"] for d in docs if d["kind"] == kind)], missing=[])
    monkeypatch.setattr(chat, "local_answer", select)
    item = chat.answer("无尽之刃有什么效果")
    assert item["status"] == "local" and item["context"]["game"] == "lol"
    assert "无尽之刃" in item["answer"] and item["sources"][0]["id"].startswith("lol:item:")
    lore = chat.answer("安妮的背景故事是什么")
    assert lore["status"] == "local" and lore["sources"][0]["id"].endswith(":lore")


def test_partial_build_and_runes_only_supplements_missing_part(monkeypatch):
    calls = []
    def supplement(question, missing, documents, resolved, history):
        calls.append((missing, documents, resolved))
        return "符文选择需要结合对线和打法，爆发路线可考虑电刑。", "used", "test-model"
    monkeypatch.setattr(chat, "supplement", supplement)
    result = chat.answer("安妮出装和符文")
    assert result["status"] == "hybrid"
    assert "卢登" in result["local_answer"] and result["supplement"]
    assert len(calls) == 1 and calls[0][0] == ["安妮的符文/铭文"]
    assert all(source["kind"] == "build" for source in calls[0][1])


def test_semantic_question_assesses_local_coverage_before_fallback(monkeypatch):
    def local(question, documents, history):
        return chat.LocalAnswer(evidence_ids=[documents[0]["id"]], missing=[])
    monkeypatch.setattr(chat, "local_answer", local)
    complete = chat.answer("剑魔被敌方打野盯上该怎么办")
    assert complete["external_status"] == "not_needed" and complete["sources"]
    monkeypatch.setattr(chat, "local_answer", lambda *args: chat.LocalAnswer(evidence_ids=[], missing=["具体连招没有收录"]))
    monkeypatch.setattr(chat, "supplement", lambda *args: ("建议先练习技能与位移衔接。", "used", "test"))
    incomplete = chat.answer("剑魔连招怎么练")
    assert incomplete["status"] == "external" and incomplete["missing"]


def test_local_model_failure_is_not_mislabeled_as_knowledge_gap(monkeypatch):
    def unavailable(*args):
        raise TimeoutError()
    monkeypatch.setattr(chat, "local_answer", unavailable)
    result = chat.answer("剑魔为什么要留E躲控制")
    assert result["status"] == "partial" and result["external_status"] == "not_needed"
    assert "语义整理暂不可用" in result["answer"]


def test_unavailable_external_preserves_known_local_answer(monkeypatch):
    monkeypatch.setattr(chat, "supplement", lambda *args: ("", "unavailable", ""))
    result = chat.answer("安妮出装和符文")
    assert result["status"] == "partial" and "卢登" in result["answer"]
    assert "暂未完成补充" in result["answer"]


def test_billing_error_is_shown_without_claiming_success(monkeypatch):
    from urllib.error import HTTPError
    monkeypatch.setattr(chat, "text_model_config", lambda: {"provider":"deepseek", "base":"https://example.test", "model":"test", "key":"SECRET"})
    monkeypatch.setattr(chat, "post_json", lambda *args: (_ for _ in ()).throw(HTTPError("https://example.test", 402, "payment", {}, None)))
    result = chat.answer("安妮出装和符文")
    assert result["status"] == "partial" and result["external_status"] == "billing_required"
    assert "402" in result["answer"] and "SECRET" not in json.dumps(result)


def test_generic_topic_retains_hero_for_later_pronoun(monkeypatch):
    context = chat.answer("剑魔怎么打")["context"]
    monkeypatch.setattr(chat, "supplement", lambda *args: ("技能急速影响冷却。", "used", "test"))
    generic = chat.answer("英雄联盟技能急速怎么计算", context)
    assert generic["context"]["heroes"] == context["heroes"]
    assert "120、100、80" in chat.answer("他的大招多久", generic["context"])["answer"]


def test_model_calls_use_local_only_for_synthesis_and_validate_evidence(monkeypatch):
    docs = kb.hero_documents({"game":"lol", "id":"Annie", "name":"安妮"})[:2]
    monkeypatch.setattr(chat, "local_config", lambda: {"base":"http://127.0.0.1:11434", "model":"test"})
    def local(url, payload, timeout, headers=None):
        assert url == "http://127.0.0.1:11434/api/chat"
        assert payload["think"] is False
        return {"message":{"content":json.dumps({"evidence_ids":["fabricated"], "missing":[]})}}
    monkeypatch.setattr(chat, "post_json", local)
    with pytest.raises(ValueError):
        chat.local_answer("安妮", docs, [])
    monkeypatch.setattr(chat, "local_config", lambda: {"base":"https://remote.example", "model":"test"})
    with pytest.raises(ValueError):
        chat.local_answer("安妮", docs, [])


def test_external_payload_is_gap_scoped_and_error_does_not_expose_key(monkeypatch):
    monkeypatch.setattr(chat, "text_model_config", lambda: {"provider":"deepseek", "base":"https://example.test", "model":"test", "key":"SECRET"})
    def remote(url, payload, timeout, headers):
        data = json.loads(payload["messages"][1]["content"])
        assert data["knowledge_gaps"] == ["符文"] and len(data["recent_dialogue"]) == 4
        assert headers["Authorization"] == "Bearer SECRET"
        assert "不联网" in payload["messages"][0]["content"]
        return {"choices":[{"finish_reason":"stop","message":{"content":'{"answer":"按对线选择符文。"}'}}]}
    monkeypatch.setattr(chat, "post_json", remote)
    assert chat.supplement("符文", ["符文"], [], {"game":"lol","heroes":[]}, [{}]*8) == ("按对线选择符文。", "used", "test")
    monkeypatch.setattr(chat, "post_json", lambda *args: (_ for _ in ()).throw(OSError("SECRET")))
    result = chat.supplement("符文", ["符文"], [], {"game":"lol","heroes":[]}, [])
    assert result == ("", "unavailable", "")


def doubao_config(monkeypatch):
    monkeypatch.setattr(chat, "text_model_config", lambda: {
        "provider": "doubao", "base": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-2-0-pro-260215", "key": "SECRET"})


def test_doubao_responses_request_and_completed_text(monkeypatch):
    doubao_config(monkeypatch)
    def remote(url, payload, timeout, headers):
        assert url == "https://ark.cn-beijing.volces.com/api/v3/responses"
        assert headers["Authorization"] == "Bearer SECRET"
        assert "response_format" not in payload and "messages" not in payload
        assert payload["store"] is False and payload["thinking"] == {"type": "disabled"}
        assert payload["input"][0]["role"] == "system"
        assert payload["input"][1]["content"][0]["type"] == "input_text"
        data = json.loads(payload["input"][1]["content"][0]["text"])
        assert data["knowledge_gaps"] == ["符文"] and len(data["recent_dialogue"]) == 4
        return {"status": "completed", "output": [
            {"type": "reasoning", "summary": [{"text": "PRIVATE"}]},
            {"type": "message", "role": "assistant", "status": "completed", "content": [
                {"type": "output_text", "text": '{"answer":"按对线选择符文。"}'}]}]}
    monkeypatch.setattr(chat, "post_json", remote)
    assert chat.supplement("符文", ["符文"], [], {"game": "lol", "heroes": []}, [{}] * 8) == (
        "按对线选择符文。", "used", "doubao-seed-2-0-pro-260215")


@pytest.mark.parametrize("response", [
    {"status": "incomplete", "output": []},
    {"status": "completed", "error": {"message": "SECRET"}, "output": []},
    {"status": "completed", "output": [{"type": "reasoning", "summary": []}]},
    {"status": "completed", "output": [{"type": "function_call"}]},
    *[{"status": "completed", "output": [{"type": "message", "role": "assistant", "status": "completed",
        "content": [{"type": "output_text", "text": text}]}]}
      for text in ['not JSON', '{"answer":" "}', '{"answer":"ok","extra":1}', '{"answer":"' + '长' * 351 + '"}']],
    {"status": "completed", "output": [{"type": "message", "role": "assistant", "status": "incomplete", "content": []}]},
    {"status": "completed", "output": [{"type": "message", "role": "assistant", "status": "completed",
        "content": [{"type": "refusal", "refusal": "SECRET"}]}]},
])
def test_doubao_rejects_incomplete_or_invalid_output(monkeypatch, response):
    doubao_config(monkeypatch)
    monkeypatch.setattr(chat, "post_json", lambda *args: response)
    assert chat.supplement("符文", ["符文"], [], {"game": "lol", "heroes": []}, []) == ("", "unavailable", "")


def test_doubao_model_access_error_preserves_local_answer(monkeypatch):
    from urllib.error import HTTPError
    doubao_config(monkeypatch)
    monkeypatch.setattr(chat, "post_json", lambda *args: (_ for _ in ()).throw(
        HTTPError("https://example.test", 404, "SECRET", {}, None)))
    result = chat.answer("安妮出装和符文")
    assert result["external_status"] == "model_unavailable" and result["status"] == "partial"
    assert "卢登" in result["local_answer"] and "访问权限" in result["answer"]
    assert "SECRET" not in json.dumps(result)


@pytest.mark.parametrize("app", [monitor, workbench])
def test_conversation_api_persists_context_retries_and_starts_new_chat(app):
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 4444)) as client:
        assert client.get("/chat").status_code == 200
        session = client.post("/api/chat/sessions").json()
        path = "/api/chat/sessions/" + session["id"]
        first = {"text":"剑魔怎么打", "revision":0, "request_id":uuid4().hex}
        result = client.post(path + "/messages", json=first)
        assert result.status_code == 200, result.text
        replay = client.post(path + "/messages", json=first).json()
        assert replay["session"]["revision"] == 1 and len(replay["session"]["turns"]) == 1
        assert client.post(path + "/messages", json={**first,"text":"其他问题"}).status_code == 409
        followup = client.post(path + "/messages", json={"text":"他的大招多久", "revision":1, "request_id":uuid4().hex}).json()
        assert "120、100、80" in followup["response"]["answer"]
        assert client.get(path).json()["revision"] == 2
        assert client.post(path + "/messages", json={"text":" ","revision":2,"request_id":uuid4().hex}).status_code == 422
        fresh = client.post("/api/chat/sessions").json()
        assert fresh["context"] == {} and fresh["turns"] == []
        assert client.post("/api/chat/sessions",headers={"Origin":"https://untrusted.example"}).status_code == 403


def test_store_serializes_active_turns_and_release_allows_retry():
    session = chat_store.create()
    job = chat_store.begin(session["id"], "first-request", 0, "问题")
    with pytest.raises(chat_store.ChatConflict):
        chat_store.begin(session["id"], "second-request", 0, "问题2")
    chat_store.release(session["id"], job["token"])
    assert chat_store.begin(session["id"], "first-request", 0, "问题")["token"]
