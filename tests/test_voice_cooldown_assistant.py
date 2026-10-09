import pytest

from gameplan.voice.cooldown_assistant import VoiceCooldownAssistant, parse_command


def test_voice_command_creates_ultimate_timer_and_snapshot_updates():
    clock = [100.0]
    assistant = VoiceCooldownAssistant(now=lambda: clock[0])

    result = assistant.process("小象助手，妲己放大了", recorded_at=100.0)

    assert result["ok"] is True
    assert result["command"]["hero"] == "妲己"
    assert result["command"]["skill_kind"] == "ultimate"
    assert result["timer"]["cooldown_s"] == 15.0
    assert result["timer"]["remaining_s"] == 12.0

    clock[0] = 105.0
    timer = assistant.snapshot()[0]
    assert timer["remaining_s"] == 7.0
    assert timer["status"] == "cooling"

    clock[0] = 112.0
    assert assistant.snapshot()[0]["status"] == "ready"
    assert assistant.snapshot()[0]["remaining_s"] == 0.0


def test_flash_replaces_only_the_same_hero_skill_and_uses_knowledge_default():
    assistant = VoiceCooldownAssistant(now=lambda: 200.0)
    assistant.process("小象助手，孙悟空闪现了")
    assistant.process("小象助手，孙悟空放大了")

    timers = assistant.snapshot(at=200.0)
    assert {(row["hero"], row["skill_kind"]) for row in timers} == {
        ("孙悟空", "flash"),
        ("孙悟空", "ultimate"),
    }
    flash = next(row for row in timers if row["skill_kind"] == "flash")
    assert flash["cooldown_s"] == 120.0


def test_requires_wake_word_and_records_dynamic_cooldown_without_guessing():
    assistant = VoiceCooldownAssistant(now=lambda: 1.0)
    assert assistant.process("妲己放大了")["ok"] is False

    result = assistant.process("小象助手，朵莉亚放大了")
    assert result["ok"] is True
    assert result["timer"]["status"] == "unknown"
    assert result["timer"]["remaining_s"] is None


def test_parse_command_returns_route_payload_without_creating_state():
    payload = parse_command("小象助手，妲己放大了")

    assert payload == {
        "hero": "妲己",
        "skill_type": "ultimate",
        "skill_name": "女王崇拜",
        "cooldown": 15.0,
        "cooldown_s": 15.0,
        "slot": 3,
        "message": "已识别妲己女王崇拜，默认冷却15秒",
        "raw_text": "小象助手，妲己放大了",
    }


def test_whitespace_and_punctuation_are_ignored_and_four_skill_slot_is_preserved():
    result = parse_command("小象助手， 对面 大乔 刚刚 放大了！")

    assert result["hero"] == "大乔"
    assert result["slot"] == 4
    assert result["skill_name"] == "漩涡之门"


@pytest.mark.parametrize("text", [
    "小象助手，妲己大招还有多久",
    "小象助手，妲己大招好了",
    "小象助手，妲己没有放大了",
    "小象助手，妲己没带闪现",
    "小象助手，妲己还没用闪现",
    "小象助手，妲己和小乔放大了",
    "小象助手，妲己闪现了还是放大了",
    "小象助手，未知英雄闪现了",
    "小象助手，我方妲己放大了",
])
def test_non_cast_or_ambiguous_input_does_not_change_active_timers(text):
    assistant = VoiceCooldownAssistant(now=lambda: 100.0)
    assistant.process("小象助手，妲己放大了")
    before = assistant.snapshot()

    assert assistant.process(text)["ok"] is False
    assert assistant.snapshot() == before


def test_repeat_cast_replaces_only_same_hero_and_skill():
    assistant = VoiceCooldownAssistant(now=lambda: 100.0)
    first = assistant.process("小象助手，妲己放大了")["timer"]
    assistant.process("小象助手，妲己闪现了")
    replaced = assistant.process("小象助手，妲己放大了", recorded_at=102.0)["timer"]

    assert first["id"] != replaced["id"]
    assert replaced["ready_at"] == 114.0
    assert len(assistant.snapshot()) == 2
    assistant.clear(hero="妲己")
    assert assistant.snapshot() == []


def test_review_required_cooldown_is_unknown():
    result = parse_command("小象助手，鲁班大师放大了")
    assert result["cooldown_s"] is None


@pytest.mark.parametrize("text, kind", [
    ("妲己也放大招了", "ultimate"),
    ("妲己刚刚也开大了", "ultimate"),
    ("妲己也刚刚放大了", "ultimate"),
    ("妲己又放大招了", "ultimate"),
    ("妲己也交闪了", "flash"),
])
def test_conversational_cast_modifiers(text, kind):
    result = parse_command("小象助手，" + text)
    assert result is not None
    assert result["hero"] == "妲己"
    assert result["skill_type"] == kind


@pytest.mark.parametrize("text", [
    "妲己也没有放大招了", "妲己也放大招了吗", "妲己也放大招了？",
    "妲己也许放大招了", "妲己也想放大招了", "妲己和小乔也放大招了",
])
def test_modifiers_do_not_turn_uncertain_speech_into_casts(text):
    assert parse_command("小象助手，" + text) is None
