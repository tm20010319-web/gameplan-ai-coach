import json
from pathlib import Path
import pytest
from gameplan.voice import VoiceCooldownAssistant

ROOT = Path(__file__).resolve().parents[1]
ALIASES = json.loads((ROOT / "resources/knowledge/hero_voice_aliases.json").read_text(encoding="utf-8"))["heroes"]
ASSISTANT = VoiceCooldownAssistant()


def test_alias_catalog_covers_exactly_current_heroes():
    assert set(ALIASES) == set(ASSISTANT.known_heroes)
    assert len(ALIASES) == 132


@pytest.mark.parametrize('hero', sorted(ALIASES))
def test_every_official_hero_accepts_short_unavailable_report(hero):
    result = ASSISTANT.resolve_commands(f'小象助手，{hero}没大')
    assert result['status'] == 'ready'
    assert [c['hero'] for c in result['commands']] == [hero]


@pytest.mark.parametrize('name', ['牛魔', '老牛', '牛牛', '牛魔王'])
@pytest.mark.parametrize('action', ['没大', '没大了', '没大招', '没有大招', '放大', '开大', '交大了'])
def test_cow_names_and_colloquial_casts_start_an_ultimate(name, action):
    result = ASSISTANT.process(f'小象助手，{name}{action}', recorded_at=1000)
    assert result['ok']
    assert result['timer']['hero'] == '牛魔'
    assert result['timer']['skill_kind'] == 'ultimate'
    assert result['timer']['ready_at'] > 1000


@pytest.mark.parametrize('text', ['牛魔没大吗', '牛魔没大？', '牛魔可能没大', '牛魔没放大', '牛魔没有放大',
    '牛魔没学大', '牛魔不放大', '牛魔没到四级', '不知道牛魔没大', '牛牛没大招吗'])
def test_short_reports_do_not_turn_uncertainty_or_negation_into_a_timer(text):
    assert ASSISTANT.resolve_commands('小象助手，'+text)['status'] == 'invalid'


@pytest.mark.parametrize('action', ['没闪', '没闪了', '没闪现', '没闪现了', '没有闪现', '闪现没了'])
def test_flash_unavailability_reports_start_the_same_cooldown(action):
    assistant = VoiceCooldownAssistant(now=lambda: 1000)
    result = assistant.process('小象助手，牛牛'+action)
    assert result['ok']
    assert result['timer']['hero'] == '牛魔'
    assert result['timer']['skill_kind'] == 'flash'
    assert result['timer']['ready_at'] == 1117


@pytest.mark.parametrize("hero", sorted(ALIASES))
def test_every_official_name_remains_unambiguous(hero):
    result = ASSISTANT.resolve_commands(f"小象助手，{hero}放大了")
    assert result["status"] == "ready"
    assert [c["hero"] for c in result["commands"]] == [hero]


@pytest.mark.parametrize('action', ['闪现了', '没大'])
@pytest.mark.parametrize("hero, alias", [
    (hero, alias) for hero, entry in ALIASES.items()
    for alias in entry["aliases"] + entry["asr_variants"]
])
def test_each_curated_alias_resolves_or_offers_its_hero(hero, alias, action):
    result = ASSISTANT.resolve_commands(f"小象助手，{alias}{action}")
    if result["status"] == "ready":
        assert [c["hero"] for c in result["commands"]] == [hero]
    else:
        assert result["status"] == "needs_confirmation"
        assert hero in {name for choice in result["candidates"] for name in choice["heroes"]}
        assert result["commands"] == []


@pytest.mark.parametrize("alias, expected", [
    ("鲁班", {"鲁班七号", "鲁班大师"}),
    ("百里", {"百里守约", "百里玄策"}),
    ("鸟人", {"后羿", "云中君"}),
    ("老头", {"老夫子", "姜子牙"}),
])
def test_shared_alias_never_silently_selects_first_hero(alias, expected):
    result = ASSISTANT.resolve_commands(f"小象助手，{alias}放大了")
    assert result["status"] == "needs_confirmation"
    assert {c["heroes"][0] for c in result["candidates"]} == expected
    assert ASSISTANT.parse_command(f"小象助手，{alias}放大了") is None
    for choice in result["candidates"]:
        confirmed = ASSISTANT.resolve_commands(choice["text"])
        assert confirmed["status"] == "ready"
        assert [c["hero"] for c in confirmed["commands"]] == choice["heroes"]


def test_ambiguous_group_preserves_every_hero_and_limits_combinations():
    result = ASSISTANT.resolve_commands("小象助手，鲁班小明都放大招了")
    assert len(result["candidates"]) == 2
    assert all("明世隐" in c["heroes"] for c in result["candidates"])
    many = ASSISTANT.resolve_commands("小象助手，元流百里鲁班都放大招了")
    assert many["status"] == "needs_confirmation"
    assert many["candidates"] == []
    assert many["commands"] == []


@pytest.mark.parametrize("text", ["鲁班没放大了", "鲁班放大了吗", "鲁班放大了？", "鲁班谁放大了", "小明陌生英雄都放大了"])
def test_unknown_and_negative_input_never_offers_confirmation(text):
    assert ASSISTANT.resolve_commands("小象助手，" + text)["status"] == "invalid"
