"""Build the compact knowledge base used by the Xiaoxiang Assistant voice timer."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "resources" / "knowledge" / "skill_catalog.json"
SUMMONER_SOURCE = ROOT / "resources" / "knowledge" / "summoner_skills.json"
TARGET = ROOT / "resources" / "knowledge" / "voice_assistant_knowledge.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build() -> dict:
    catalog = load_json(SOURCE)
    summoners = load_json(SUMMONER_SOURCE)
    flash = next(
        spell for spell in summoners["spells"] if spell["name"] == "闪现"
    )

    heroes = []
    for hero in catalog["heroes"]:
        ultimates = [skill for skill in hero["skills"] if skill["is_ultimate"]]
        if len(ultimates) != 1:
            raise ValueError(
                f"Expected exactly one ultimate for {hero['hero']}, found {len(ultimates)}"
            )
        ultimate = ultimates[0]
        cooldowns = [float(value) for value in (ultimate["base_cooldowns_s"] or [])]
        default_cooldown = cooldowns[0] if cooldowns else None
        heroes.append(
            {
                "id": hero["id"],
                "name": hero["hero"],
                "aliases": [hero["hero"]],
                "ultimate": {
                    "skill_id": ultimate["source_skill_id"],
                    "name": ultimate["name"],
                    "cooldown_by_level_s": cooldowns,
                    "default_level": 1,
                    "default_cooldown_s": default_cooldown,
                    "cooldown_min_s": min(cooldowns) if cooldowns else None,
                    "cooldown_max_s": max(cooldowns) if cooldowns else None,
                    "cooldown_text": ultimate["cooldown_text"],
                    "cooldown_kind": ultimate["cooldown_kind"],
                    "special_mechanic": bool(ultimate["special_mechanic"]),
                    "cooldown_review_required": bool(
                        ultimate["cooldown_review_required"]
                    ),
                    "cooldown_note": ultimate["cooldown_note"],
                    "description": ultimate["description"],
                },
                "source_url": hero["source_url"],
            }
        )

    return {
        "schema_version": 1,
        "assistant_name": "小象助手",
        "game": "王者荣耀",
        "game_mode": "正式服 5v5 排位/娱乐局",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "hero_catalog": str(SOURCE.relative_to(ROOT)).replace("\\", "/"),
            "summoner_catalog": str(SUMMONER_SOURCE.relative_to(ROOT)).replace(
                "\\", "/"
            ),
            "hero_source_url": catalog["source_url"],
            "hero_detail_source": catalog["detail_source"],
            "catalog_fetched_at": catalog["fetched_at"],
            "version_note": (
                "官网公开资料未标注正式服版本；本文件继承原始资料的采集时间，"
                "正式发布前应按当前版本抽样复核。"
            ),
        },
        "voice_defaults": {
            "wake_word": "小象助手",
            "ultimate_default_level": 1,
            "unknown_cooldown_policy": "record_without_countdown",
            "event_time_policy": "voice_recorded_at",
        },
        "summoner_skills": {
            "闪现": {
                "cooldown_s": float(flash["base_cooldown_s"]),
                "description": flash["description"],
                "source_url": summoners["source_url"],
                "version_note": (
                    "默认按召唤师技能基础冷却计时；未输入额外冷却影响因素。"
                ),
            }
        },
        "heroes": heroes,
    }


def main() -> None:
    result = build()
    TARGET.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"generated {TARGET} ({len(result['heroes'])} heroes)")


if __name__ == "__main__":
    main()
