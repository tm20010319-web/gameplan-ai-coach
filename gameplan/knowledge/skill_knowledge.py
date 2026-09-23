"""Structured local hero and skill knowledge lookup."""
import json
import sqlite3
import threading
from functools import lru_cache

from gameplan.tactics.coach import ROOT

CATALOG_PATH = ROOT / "resources" / "knowledge" / "skill_catalog.json"
DATABASE_PATH = ROOT / "data" / "hero_knowledge.sqlite3"
PROFILES_PATH = ROOT / "data" / "hero_coaching_profiles.json"
_lock = threading.RLock()


def sync_database():
    """Publish a source snapshot to an independently queryable SQLite database."""
    from gameplan.tactics.coach import HEROES
    stamp = str(CATALOG_PATH.stat().st_mtime_ns) + ':' + str(PROFILES_PATH.stat().st_mtime_ns if PROFILES_PATH.exists() else 0)
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock, sqlite3.connect(DATABASE_PATH) as db:
        db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS heroes (name TEXT PRIMARY KEY, data TEXT NOT NULL)")
        if db.execute("SELECT value FROM metadata WHERE key='catalog_stamp'").fetchone() == (stamp,):
            return
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        profiles = json.loads(PROFILES_PATH.read_text(encoding="utf-8")) if PROFILES_PATH.exists() else {}
        profile_index = {p['hero']: {**p, 'review_note': profiles.get('note', '')} for p in profiles.get('heroes', [])}
        _read_catalog.cache_clear()
        rows = []
        for hero in catalog["heroes"]:
            item = {**hero, "fetched_at": hero.get("fetched_at", catalog.get("fetched_at")),
                    "tactical_reference": HEROES.get(hero["hero"]),
                    "catalog_note": catalog.get("note", ""),
                    "coaching_profile": profile_index.get(hero['hero'])}
            rows.append((hero["hero"], json.dumps(item, ensure_ascii=False)))
        db.execute("DELETE FROM heroes")
        db.executemany("INSERT INTO heroes VALUES (?, ?)", rows)
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('catalog_stamp', ?)", (stamp,))


def _catalog():
    if not CATALOG_PATH.exists():
        return {"heroes": [], "note": "技能资料尚未导入", "fetched_at": None}
    return _read_catalog(str(CATALOG_PATH), CATALOG_PATH.stat().st_mtime_ns)


@lru_cache(maxsize=1)
def _read_catalog(path, stamp):
    # Another process can publish the SQLite snapshot before this process reads
    # coverage. Key by the source timestamp, independently of the database stamp.
    from pathlib import Path
    return json.loads(Path(path).read_text(encoding="utf-8"))


def reload_catalog():
    _read_catalog.cache_clear()
    return _catalog()


def _index():
    return {item.get("hero"): item for item in _catalog().get("heroes", []) if item.get("hero")}


def get_hero(hero):
    sync_database()
    with sqlite3.connect(DATABASE_PATH) as db:
        row = db.execute("SELECT data FROM heroes WHERE name=?", ((hero or "").strip(),)).fetchone()
    item = json.loads(row[0]) if row else None
    if not item:
        return None
    skills = []
    for skill in item.get("skills") or []:
        skills.append({
            "slot": skill.get("slot"),
            "name": skill.get("name", ""),
            "passive": bool(skill.get("passive")),
            "cooldown_s": skill.get("base_cooldowns_s") or [],
            "cooldown_text": skill.get("cooldown_text", "未知"),
            "description": skill.get("description", ""),
            "special_mechanic": bool(skill.get("special_mechanic")),
            "is_ultimate": skill.get("is_ultimate", skill.get("slot") == 3),
            "source_skill_id": skill.get("source_skill_id"),
            "tips": skill.get("tips", ""),
            "cooldown_kind": skill.get("cooldown_kind", "unknown"),
            "cooldown_formula": skill.get("cooldown_formula"),
            "cooldown_review_required": bool(skill.get("cooldown_review_required")),
            "cooldown_note": skill.get("cooldown_note", ""),
            "cooldown_alternatives": skill.get("cooldown_alternatives", []),
        })
    ultimate = next((skill for skill in skills if skill["is_ultimate"]), None)
    return {
        "hero": item["hero"], "hero_id": item.get("id"),
        "source_url": item.get("source_url"), "status": item.get("status", "unknown"),
        "skills": skills, "ultimate": ultimate, "has_skill_data": bool(skills),
        "has_ultimate_cooldown": bool(ultimate and (ultimate['cooldown_s'] or ultimate['cooldown_formula'])),
        "catalog_note": item.get("catalog_note", ""), "fetched_at": item.get("fetched_at"),
        "title": item.get("title", ""), "roles": item.get("roles", []),
        "tactical_reference": item.get("tactical_reference"),
        "coaching_profile": item.get("coaching_profile"),
        "cooldown_semantics": "基础冷却，不代表当前剩余冷却；估算须标注推测。",
    }


def lookup_many(heroes):
    result, seen = [], set()
    for hero in heroes or []:
        if hero in seen:
            continue
        seen.add(hero)
        record = get_hero(hero)
        if record:
            result.append(record)
    return result


def coverage():
    sync_database()
    records = list(_index().values())
    with_skills = sum(bool(item.get("skills")) for item in records)
    ultimates = {item['hero']: next((s for s in item.get('skills', []) if s.get('is_ultimate', s.get('slot') == 3)), None) for item in records}
    missing_ultimate = [hero for hero, skill in ultimates.items() if skill is None]
    missing_cooldown = [hero for hero, skill in ultimates.items() if not skill or not (skill.get('base_cooldowns_s') or skill.get('cooldown_formula'))]
    profiles = json.loads(PROFILES_PATH.read_text(encoding='utf-8')).get('heroes', []) if PROFILES_PATH.exists() else []
    coaching = []
    for profile in profiles:
        hero = next((r for r in records if r['hero'] == profile['hero']), {})
        rules = profile.get('rules', [])
        issues = []
        if not hero.get('skills'):
            issues.append('技能资料缺失')
        if len(profile.get('default_build', {}).get('items_in_order', [])) != 6:
            issues.append('默认六件装备不完整')
        if sum(r['count'] for r in profile.get('default_runes', {}).get('items', [])) != 30:
            issues.append('默认铭文数量不是30')
        if not profile.get('default_summoner_spell', {}).get('name'):
            issues.append('默认召唤师技能缺失')
        if not rules:
            issues.append('打法资料缺失')
        for rule in rules:
            if not rule.get('when') or not rule.get('action') or not (rule.get('source_url') or rule.get('source_file')):
                issues.append('打法条件或来源缺失')
        coaching.append({'hero': profile['hero'], 'practice_lane': profile['practice_lane'],
            'rules': len(rules), 'issues': issues, 'reference_ready': not issues,
            'current_patch_verified': profile.get('source_review', {}).get('current_patch_verified', False),
            'pending': profile.get('missing', []),
            'version_conflicts': profile.get('source_review', {}).get('version_conflicts', []),
            'excluded_scope': profile.get('scope_exclusions', [])})
    return {"storage": "sqlite", "heroes": len(records), "with_skill_data": with_skills,
            "skills": sum(len(item.get('skills', [])) for item in records),
            "with_ultimate": len(records) - len(missing_ultimate), "missing_ultimate": missing_ultimate,
            "with_ultimate_cooldown": len(records) - len(missing_cooldown), "missing_ultimate_cooldown": missing_cooldown,
            "dynamic_ultimate_cooldowns": [hero for hero, skill in ultimates.items() if skill and skill.get('cooldown_kind') == 'dynamic'],
            "ultimate_cooldown_review_required": [hero for hero, skill in ultimates.items() if skill and skill.get('cooldown_review_required')],
            "coaching_profiles": coaching, "coaching_heroes": len(coaching),
            "coaching_rules": sum(p['rules'] for p in coaching),
            "missing_heroes": [h["hero"] for h in records if not h.get("skills")],
            "missing_skill_data": len(records) - with_skills,
            "fetched_at": _catalog().get("fetched_at"), "note": _catalog().get("note", "")}
