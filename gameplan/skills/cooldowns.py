"""Manual skill observations, persisted with each match's existing memory."""

import time
from uuid import uuid4


def snapshot(match_id, memory):
    return {
        "match_id": match_id,
        "revision": memory.get("skill_timer_revision", 0),
        "server_epoch": time.time(),
        "timers": memory.get("skill_timers", []),
        "history": list(reversed(memory.get("skill_timer_history", []))),
    }


def retire(memory, timer_ids, reason):
    for item in memory.get("skill_timer_history", []):
        if item["id"] in timer_ids:
            item["ended_reason"] = reason
    memory["skill_timers"] = [item for item in memory.get("skill_timers", []) if item["id"] not in timer_ids]
    memory["skill_timer_revision"] = memory.get("skill_timer_revision", 0) + 1


def register(memory, req, catalog):
    hero = next((entry for entry in catalog["heroes"] if entry["hero"] == req.hero), None)
    if not hero:
        raise ValueError("请选择已收录的英雄")
    skill = next((entry for entry in hero["skills"] if entry["slot"] == req.slot), None)
    if skill and skill["passive"]:
        raise ValueError("被动技能不能登记施放倒计时")
    if hero["skills"] and not skill:
        raise ValueError("该英雄没有此技能槽位")
    # Heroes without official data can still use a manually confirmed slot and duration.
    timers = memory.get("skill_timers", [])
    replaced = {item["id"] for item in timers if (item["side"], item["hero"], item["slot"]) == (req.side, req.hero, req.slot)}
    if len(timers) - len(replaced) >= 40:
        raise ValueError("最多同时记录 40 个技能，请先移除不需要的计时")
    now = time.time()
    cast_epoch = now - req.elapsed_s if req.mode == "cast" else None
    item = {
        "id": uuid4().hex, "side": req.side, "hero": req.hero, "slot": req.slot,
        "skill": skill["name"] if skill else f"{req.slot} 技能",
        "mode": req.mode, "seconds": req.seconds, "source": "manual",
        "recorded_epoch": now, "cast_epoch": cast_epoch,
        "cast_game_time_s": req.game_time_s,
        "ready_epoch": (cast_epoch if cast_epoch is not None else now) + req.seconds,
        "ready_game_time_s": req.game_time_s + req.seconds if req.game_time_s is not None else None,
        "special_mechanic": bool(skill and skill.get("special_mechanic")),
    }
    retire(memory, replaced, "superseded")
    memory["skill_timers"].append(item)
    memory["skill_timer_history"] = (memory.get("skill_timer_history", []) + [dict(item)])[-100:]
    return item
