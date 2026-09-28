"""Read-only League of Legends PC knowledge, separate from Honor of Kings."""
from functools import lru_cache
import json

from gameplan.paths import ROOT

CATALOG_PATH = ROOT / "resources/knowledge/lol_catalog.json"


@lru_cache(maxsize=1)
def _load(mtime_ns):
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def catalog():
    return _load(CATALOG_PATH.stat().st_mtime_ns)


def metadata(data):
    return {key: data[key] for key in ("game", "version", "locale", "fetched_at", "sources", "note")}


def search(query="", role=""):
    data = catalog()
    words = query.casefold().split()
    heroes = []
    for hero in data["heroes"]:
        if role and role not in hero["roles"]:
            continue
        items = [data["items"][key] for key in hero["build_reference"]["item_ids"]]
        fields = [hero["id"], hero["name"], hero["title"], *hero["aliases"], *hero["roles"],
                  *hero["ally_tips"], *hero["enemy_tips"], hero["play_reference"]["text"],
                  hero["build_reference"]["note"], *[item["name"] for item in items]]
        fields.extend(text for skill in hero["skills"] for text in (skill["name"], skill["description"], skill["cooldown_text"]))
        haystack = " ".join(fields).casefold()
        if all(word in haystack for word in words):
            heroes.append({key: hero[key] for key in ("id", "name", "title", "roles")})
    normalized = query.strip().casefold()
    heroes.sort(key=lambda h: (normalized not in (h["id"].casefold(), h["name"].casefold(), h["title"].casefold()), h["id"]))
    return {**metadata(data), "heroes": heroes, "total": len(data["heroes"]), "matched": len(heroes),
            "roles": sorted({role for hero in data["heroes"] for role in hero["roles"]})}


def get_hero(hero_id):
    data = catalog()
    hero = next((hero for hero in data["heroes"] if hero["id"].casefold() == hero_id.casefold()), None)
    if hero is None:
        return None
    return {**metadata(data), **hero,
            "build_reference": {**hero["build_reference"], "items": [data["items"][key] for key in hero["build_reference"]["item_ids"]]}}
