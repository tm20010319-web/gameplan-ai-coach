import json

import pytest
from fastapi.testclient import TestClient

from gameplan.knowledge import lol_knowledge
from gameplan.web.monitor_app import app as monitor
from gameplan.web.app import app as workbench
from scripts.update_lol_knowledge import validate


def test_snapshot_is_complete_and_builds_only_reference_available_items():
    data = lol_knowledge.catalog()
    validate(data)
    assert len(data["heroes"]) == data["expected_heroes"] == 173
    assert len(data["items"]) > 200
    for hero in data["heroes"]:
        assert hero["name"] and hero["title"] and hero["play_reference"]["text"]
        assert "非官方推荐" in hero["build_reference"]["kind"]
        assert hero["source_url"].startswith("https://ddragon.leagueoflegends.com/cdn/" + data["version"])
        for skill in hero["skills"]:
            assert "{{" not in skill["description"]
            if skill["cooldowns"] and all(value == 0 for value in skill["cooldowns"]):
                assert skill["special"] and "不代表无冷却" in skill["cooldown_text"]


@pytest.mark.parametrize("query,expected", [("安妮", "Annie"), ("黑暗之女", "Annie"), ("aNnIe", "Annie"), ("火女", "Annie"), ("提伯斯", "Annie"), ("无尽之刃", "Jinx"), ("130", "Annie")])
def test_search_covers_names_aliases_skills_cooldowns_and_items(query, expected):
    assert expected in {h["id"] for h in lol_knowledge.search(query)["heroes"]}


def test_filters_unknowns_and_games_are_isolated():
    assert all("法师" in row["roles"] for row in lol_knowledge.search("", "法师")["heroes"])
    assert lol_knowledge.search("安妮", "坦克")["heroes"] == []
    assert lol_knowledge.search("这个英雄不存在")["matched"] == 0
    assert lol_knowledge.get_hero("妲己") is None
    assert lol_knowledge.get_hero("../../.env") is None
    assert lol_knowledge.get_hero("Annie")["skills"][-1]["cooldowns"] == [130, 115, 100]
    assert lol_knowledge.get_hero("Ezreal")["build_reference"]["label"] == "技能命中与咒刃"
    assert lol_knowledge.get_hero("Kassadin")["build_reference"]["label"] == "法术爆发"
    assert all("复活" not in tip for tip in lol_knowledge.get_hero("Aatrox")["enemy_tips"])


@pytest.mark.parametrize("app", [monitor, workbench])
def test_both_servers_expose_page_search_and_detail_without_changing_existing_game(app):
    with TestClient(app) as client:
        response = client.get("/knowledge/lol")
        assert response.status_code == 200 and "英雄联盟" in response.text
        listing = client.get("/api/knowledge/lol/heroes", params={"q": "安妮"}).json()
        assert listing["heroes"][0]["id"] == "Annie"
        detail = client.get("/api/knowledge/lol/heroes/Annie").json()
        assert detail["game"] == "lol-pc" and detail["build_reference"]["items"]
        assert client.get("/api/knowledge/lol/heroes/not-a-hero").status_code == 404
        assert client.get("/api/knowledge/lol/heroes", params={"q": "x" * 201}).status_code == 422
        assert client.get("/api/knowledge/skills/妲己").json()["hero"] == "妲己"


def test_catalog_reload_after_snapshot_update(tmp_path, monkeypatch):
    data = lol_knowledge.catalog()
    path = tmp_path / "lol.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(lol_knowledge, "CATALOG_PATH", path)
    lol_knowledge._load.cache_clear()
    assert lol_knowledge.catalog()["version"] == data["version"]
    replacement = {**data, "version": "99.1.1"}
    path.write_text(json.dumps(replacement), encoding="utf-8")
    import os
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000_000))
    assert lol_knowledge.catalog()["version"] == "99.1.1"
    lol_knowledge._load.cache_clear()
