import json
from pathlib import Path
from scripts.update_skill_catalog import strategy_reference
from gameplan.knowledge.skill_knowledge import get_hero, coverage

ROOT = Path(__file__).resolve().parents[1]


def test_all_supported_heroes_have_sourced_strategy_and_skill_data():
    catalog = json.loads((ROOT / "resources/knowledge/skill_catalog.json").read_text(encoding="utf-8"))
    for hero in catalog["heroes"]:
        record = get_hero(hero["hero"])
        strategy = record["strategy_reference"]
        assert strategy["source_url"] == hero["source_url"]
        assert strategy["current_patch_verified"] is False
        assert strategy["review_required"] is True
        assert all(strategy[key] for key in ("usage", "counterplay", "teamfight", "builds", "runes_note"))
        assert record["skills"] and record["ultimate"]
        for build in strategy["builds"]:
            assert [item["id"] for item in build["items"]] == build["item_ids"]
            assert all(item["name"] or item["status"] == "unresolved" for item in build["items"])
    assert coverage()["strategy_heroes"] == len(catalog["heroes"])


def test_missing_source_fields_are_not_fabricated():
    strategy = strategy_reference({"ename":105,"usetips":"<p>保留来源说明</p>"})
    assert strategy["usage"] == "保留来源说明"
    assert strategy["teamfight"] == ""
    assert strategy["builds"] == []
    assert "团战" in strategy["missing"]
    assert strategy["review_required"] is True


def test_existing_personal_profiles_are_preserved():
    assert get_hero("李元芳")["coaching_profile"]
    assert get_hero("朵莉亚")["ultimate"]["cooldown_kind"] == "dynamic"
    assert get_hero("鲁班大师")["ultimate"]["cooldown_review_required"] is True
