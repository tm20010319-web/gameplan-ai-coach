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


@pytest.mark.parametrize("hero", sorted(ALIASES))
def test_every_official_name_remains_unambiguous(hero):
    result = ASSISTANT.resolve_commands(f"小象助手，{hero}放大了")
    assert result["status"] == "ready"
    assert [c["hero"] for c in result["commands"]] == [hero]


@pytest.mark.parametrize("hero, alias", [
    (hero, alias) for hero, entry in ALIASES.items()
    for alias in entry["aliases"] + entry["asr_variants"]
])
def test_each_curated_alias_resolves_or_offers_its_hero(hero, alias):
    result = ASSISTANT.resolve_commands(f"小象助手，{alias}闪现了")
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
