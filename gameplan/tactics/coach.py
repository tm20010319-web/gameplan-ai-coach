import json
import re
from gameplan.paths import ROOT

CATALOG = json.loads((ROOT / "resources" / "knowledge" / "hero_relations.json").read_text(encoding="utf-8"))
HEROES = {item["hero"]: item for item in CATALOG["heroes"]}
PHASES = ["BP", "loading", "mid_game", "team_fight"]
LANE_PLANS = {
    "对抗路": ["控安全兵线，留意敌方打野位置", "清边后协防，别为追人丢塔", "边线与主目标同步施压，保留退路"],
    "中路": ["先清中线，有视野再支援", "推线后与辅助一起占入口", "关键控制与核心输出同步，不独自探草"],
    "发育路": ["保证补兵和血线，未知侧草不单探", "跟随保护位转安全线，把优势换塔", "站在可撤位置输出，不为追残血走出保护"],
    "打野": ["结合线权规划野区路线，不无条件入侵", "抓人后先看兵线与资源，减少无效蹲人", "争目标前确认人数、技能与惩击条件"],
    "辅助": ["帮助中路先动，和队友一起确认入口", "围绕输出核心与主目标安排保护", "先确认核心位置，再决定开团或反保"],
}
COUNTERS = {
    "关羽": [("金蝉", "限制移动，压缩冲锋空间"), ("老夫子", "限制活动范围，需要队友跟进")],
    "马超": [("金蝉", "限制移动空间"), ("东皇太一", "压制近身进场者，需保证队友可跟进")],
    "镜": [("东皇太一", "保留压制处理有效进场"), ("张飞", "护盾与反开保护低机动核心")],
    "公孙离": [("东皇太一", "以压制限制近身换位"), ("金蝉", "缩小机动输出空间")],
    "貂蝉": [("东皇太一", "压制限制持续穿梭，需集火配合")],
    "露娜": [("东皇太一", "保留压制打断穿梭节奏"), ("金蝉", "限制位移空间")],
    "马可波罗": [("张飞", "保留反开处理进场，注意免控状态")],
    "吕布": [("公孙离", "用位移拉开附魔后的近身作战距离")],
    "后羿": [("兰陵王", "侧翼逼位压缩无位移射手的安全输出区")],
    "伽罗": [("兰陵王", "隐匿侧切压缩远程输出空间")],
}


def hero_entry(hero):
    return {
        **hero, "id": "hero_" + hero["hero"], "type": "hero_tactic",
        "tags": [hero["lane"], hero["role"], *hero["threat"].split("与")],
        "trigger": [f"敌方锁定{hero['hero']}", f"{hero['hero']}消失"],
        "phase": PHASES, "recommendation": hero["teamfight"],
        "content": f"常见弱点：{hero['weakness']}。对线：{hero['laning']}。团战：{hero['teamfight']}。",
        "tempo": dict(zip(["early", "mid", "late"], LANE_PLANS[hero["lane"]])),
        "source_version": CATALOG["source_version"], "reviewed_at": None, "reviewer": "待人工教练复核",
    }


KB = [hero_entry(hero) for hero in HEROES.values()]
KB.extend(json.loads((ROOT / "resources" / "knowledge" / "knowledge_base.json").read_text(encoding="utf-8")))


def search(query, phase=None, limit=5):
    query = query.strip().lower()
    if not query:
        return []
    phase = {"bp": "BP", "in_game": "mid_game"}.get(phase, phase)
    if query in HEROES:
        return [hero_entry(HEROES[query])]
    terms = re.findall(r"[\w]+", query)
    scored = []
    for item in KB:
        if phase and phase not in item.get("phase", []):
            continue
        haystack = " ".join([
            item.get("hero", ""), item.get("title", ""), item.get("content", ""),
            item.get("laning", ""), item.get("teamfight", ""), item.get("ally_plan", ""),
            *item.get("tags", []), *item.get("trigger", []),
        ]).lower()
        score = sum(4 for term in terms if term in haystack)
        hero = item.get("hero", "")
        if hero and hero in query:
            score += 20
        for tag in item.get("tags", []):
            if len(tag) > 1 and tag in query:
                score += 2
        if score:
            scored.append((score, item))
    return [item for score, item in sorted(scored, key=lambda row: row[0], reverse=True)[:limit]]


def bp_plan(enemies, allies):
    unknown = [hero for hero in enemies + allies if hero not in HEROES]
    cards = []
    for name in enemies:
        if name not in HEROES:
            continue
        hero = HEROES[name]
        counters = [
            {"hero": candidate, "reason": reason, "selected": candidate in allies}
            for candidate, reason in COUNTERS.get(name, []) if candidate not in enemies
        ]
        cards.append({
            "hero": name, "threat": hero["threat"], "advice": hero["teamfight"],
            "content": hero["weakness"], "laning": hero["laning"], "counters": counters,
            "source_id": "hero_" + name,
        })
    known_allies = [HEROES[name] for name in allies if name in HEROES]
    warnings = []
    if len(allies) < 5 or len(enemies) < 5:
        warnings.append("阵容未齐：仅分析已确认英雄，不预测胜率。")
    if known_allies and not any("坦克" in hero["role"] for hero in known_allies):
        warnings.append("未见明确坦克位：优先反打与视野，不默认有人能正面承伤。")
    if known_allies and not any(hero["lane"] == "辅助" for hero in known_allies):
        warnings.append("未见常用辅助位：确认谁负责探视野和保护核心。")
    if unknown:
        warnings.append("暂未收录：" + "、".join(unknown) + "；不补猜克制关系。")
    if "成吉思汗" in unknown:
        warnings.append("可检索「苍」的新条目；历史名称不直接套用旧技能机制。")
    priorities = ["开局先守野区入口；中线推完再一起支援，避免盲探河道。"]
    priorities.extend(f"{hero['hero']}：{hero['ally_plan']}。" for hero in known_allies[:2])
    if len(priorities) == 1:
        priorities.append("团战先确认核心位置与关键技能，击杀后优先转塔或主目标。")
    loading = "先推中线再布控资源入口；" + (
        f"重点防{cards[0]['hero']}：{cards[0]['advice']}；" if cards else "未知敌方位置时不接无视野团；"
    ) + "己方核心到位、关键技能齐再争目标，无法先到就换另一侧资源。"
    return {
        "items": cards, "ally_items": [hero_entry(hero) for hero in known_allies],
        "message": cards[0]["advice"] if cards else "先确认敌方英雄；信息不足时清线、看视野、不盲接团。",
        "loading_plan": loading[:120], "priorities": priorities[:3], "warnings": warnings,
        "confidence": "中" if cards else "低", "unknown_heroes": unknown,
        "version_note": CATALOG["note"],
    }


def evaluate(state):
    heroes, arena, combat = state["heroes"], state["map"], state["combat_signal"]
    confidence = min(heroes["confidence"], arena["confidence"])
    result = {
        "decision": "kite", "urgency": "watch", "message": "", "backup": "先清安全线，等待信息。",
        "reason_codes": [], "confidence": confidence, "expires_in_sec": 12, "source": "rules",
    }
    if state["phase"] != "in_game":
        return {**result, "reason_codes": ["not_in_game"]}
    if confidence < 0.8 or arena["ally_count_near_mid"] is None or arena["enemy_count_near_mid"] is None:
        return {**result, "message": "局面识别中，信息不足；先清安全线，不盲探河道。", "reason_codes": ["low_confidence"]}
    ally, enemy = arena["ally_count_near_mid"], arena["enemy_count_near_mid"]
    low = arena["ally_low_hp_count"]
    missing = heroes["enemy_missing"]
    eta = arena["objective_eta_sec"]
    objective = arena["next_objective"]
    target_soon = objective is not None and eta is not None and 0 <= eta <= 40
    danger = enemy > ally or (low is not None and low >= 2) or arena["core_present"] is False
    if danger:
        reasons = []
        if enemy > ally:
            reasons.append("enemy_number_advantage")
        if low is not None and low >= 2:
            reasons.append("low_hp_risk")
        if arena["core_present"] is False:
            reasons.append("core_absent")
        if target_soon and arena["safe_trade_available"] is True and not combat["damage_exchange"]:
            return {**result, "decision": "trade", "urgency": "soon", "message": "当前接目标风险高，换另一侧安全兵线或塔，保留撤退路线。", "reason_codes": reasons + ["safe_resource_trade"]}
        return {**result, "decision": "retreat", "urgency": "now", "message": "人数、血线或核心到场不利，先退塔前清线，暂不接河道架。", "reason_codes": reasons}
    if missing and (arena["overextended"] or combat["heroes_closing_distance"] or target_soon):
        return {**result, "decision": "retreat", "urgency": "now", "message": f"{'、'.join(missing[:2])}消失，先收线看侧翼，控制和位移别提前交。", "reason_codes": ["key_hero_missing"]}
    if target_soon:
        ready = (
            ally > 0 and low == 0 and arena["key_skills_ready"] is True
            and arena["core_present"] is True and arena["position_safe"] is True
            and arena["lane_state"] == "mid_pushed_ally"
        )
        if ready:
            return {**result, "decision": "engage", "urgency": "soon", "message": f"{objective}{eta}秒刷新，人数与线权具备；先占入口，确认敌方位置再接团。", "backup": "敌方增援或关键技能交出，立即退回清线。", "reason_codes": ["objective_soon", "mid_priority", "skills_ready"]}
        return {**result, "message": f"{objective}{eta}秒刷新，先推中线补状态，等核心和关键技能到齐再争。", "reason_codes": ["objective_soon", "readiness_incomplete"]}
    if combat["damage_exchange"] or combat["heroes_closing_distance"] or combat["skills_or_ults_visible"]:
        return {**result, "message": "先拉扯逼技能，保持可撤距离；仅凭双方靠近不直接强开。", "reason_codes": ["combat_candidate"]}
    return result


def fingerprint(state, advice):
    arena = state["map"]
    return json.dumps([
        advice["decision"], advice["reason_codes"], arena["ally_count_near_mid"],
        arena["enemy_count_near_mid"], arena["ally_low_hp_count"], arena["next_objective"],
        arena["lane_state"], arena["key_skills_ready"], arena["core_present"],
        arena["position_safe"], sorted(state["heroes"]["enemy_missing"]),
    ], ensure_ascii=False)
