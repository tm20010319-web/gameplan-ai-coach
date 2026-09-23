"""Deterministic plans from a supplied lineup; no live combat inference."""

from gameplan.tactics.coach import HEROES


SAMPLE = {
    "image_url": "/static/samples/loading-lineup.png",
    "source_note": "网上截图示例 · 阵容经助手核对，非本地模型识别结果",
    "group_a": ["铠", "典韦", "鲁班七号", "赵云", "米莱狄"],
    "group_b": ["妲己", "后羿", "蔡文姬", "孙悟空", "韩信"],
}

# Supplemental qualitative mechanics, without patch-specific cooldown or item numbers.
SUPPLEMENT = {
    "米莱狄": {"ally_plan": "有兵线且敌方支援位置明确时用机械仆从压塔，先保留撤离路线", "teamfight": "先处理兵线和机械仆从，别让防御塔在追人时被持续消耗", "threat": "机械仆从与推进压力"},
    "妲己": {"ally_plan": "与队友同行，留控制限制切入者；抓到落单目标再衔接爆发", "teamfight": "脆皮不单探草，确认其控制交出或位置暴露后再接近", "threat": "单体控制与爆发"},
}


def build_plan(allies, enemies, player=None, source="manual"):
    own, enemy = set(allies), set(enemies)
    book = {**HEROES, **SUPPLEMENT}
    core = next((name for name in ("后羿", "鲁班七号", "伽罗", "黄忠", "孙尚香", "狄仁杰", "马可波罗", "公孙离") if name in own), None)
    threats = [name for name in ("韩信", "孙悟空", "赵云", "铠", "典韦", "兰陵王", "镜", "露娜") if name in enemy]
    junglers = [name for name in allies if HEROES.get(name, {}).get("lane") == "打野"]
    opening = "先确认分路与野区分工；各线保住血量和补兵，中线处理后结伴看河道，不盲目入侵。"
    if len(junglers) > 1:
        opening = f"{ '、'.join(junglers) }都有常用打野定位，开局先约定谁打野、谁走线，避免抢同一轮资源；实际分路以队伍安排为准。"
    if {"后羿", "蔡文姬"} <= own:
        title = "护住后羿，控制接反打"
        summary = "蔡文姬跟住后羿维持血线，后羿先打最近的安全目标；队友限制切入者后，再追击或转塔。"
        teamfight = "后羿与蔡文姬保持支援距离，避免一起吃到范围控制；妲己若在场，优先留控制保护后羿。侧翼输出等敌方位置与控制交代清楚再进场。" if "妲己" in own else "后羿与蔡文姬保持支援距离，避免一起吃到范围控制；其他队友先限制切入者，再衔接输出。"
    elif "鲁班七号" in own:
        title = "鲁班稳输出，突进分批进场"
        summary = "让鲁班在有人照看的位置持续输出；进场队友与保护位分工，避免所有人一起追后排。"
        divers = [name for name in ("赵云", "铠", "典韦") if name in own]
        teamfight = (f"{'、'.join(divers)}先分配进场和保护任务，一人探明机会后其余人再衔接；" if divers else "队友先确认侧翼，保留控制应对切入；") + "鲁班不抢先脸探草，不为追残血离开保护范围。"
    elif core:
        title = f"围绕{core}，先保安全输出"
        summary = f"{core}跟随队友处理安全目标，先确认侧翼和跟进距离，再决定追击。"
        teamfight = "开团前确认核心到场，进场与保护分工；对手威胁位置不明时，先清线并留退路。"
    else:
        title = "先清线抱团，抓可靠机会"
        summary = "确定队伍主要输出与保护分工；控制命中且队友能跟进时再接战，不从阵容推断一定能赢。"
        teamfight = "不同进场者分批衔接，给后续输出留空间；对手位置、己方状态或技能未确认时先拉开。"

    cautions = []
    if threats:
        cautions.append({"title": "先防侧翼切入", "text": f"重点留意{'、'.join(threats)}的接近路线。{core or '主要输出'}不独自穿野区；至少留一段控制或保护应对贴脸，不把敌方暂时不见当作安全。", "heroes": threats})
    if "蔡文姬" in enemy:
        cautions.append({"title": "别和回复阵容无效消耗", "text": "对方有蔡文姬，零散伤害可能被回复抵消。先分割其与核心的距离，再集中处理可接近的目标；减回复效果由合适位置结合当前版本、经济和装备路线承担。", "heroes": ["蔡文姬"]})
    if "米莱狄" in enemy:
        cautions.append({"title": "守塔先处理推进物", "text": "对方有米莱狄，回防时先看兵线与机械仆从。别为了追击离塔太远，把塔交给推进物持续消耗。", "heroes": ["米莱狄"]})
    if "妲己" in enemy:
        cautions.append({"title": "脆皮别单探草", "text": "妲己有单体控制与爆发。用兵线和队友同行降低被先手风险；切入前留意其位置及控制是否已经交出。", "heroes": ["妲己"]})
    if "鲁班七号" in enemy:
        cautions.append({"title": "压缩射手输出空间", "text": "避免站在鲁班扫射方向硬扛；有侧翼视野和队友跟进时再逼位，不把切后排变成单人冲进保护圈。", "heroes": ["鲁班七号"]})
    if not cautions:
        for name in enemies:
            if name in book:
                cautions.append({"title": f"注意{name}", "text": book[name]["teamfight"], "heroes": [name]})
                if len(cautions) == 2:
                    break

    advantage = "击杀或逼退对手后先看兵线，能安全推塔就转塔；争主目标前再核对人数、状态、技能与惩击，不原地追人。"
    if "米莱狄" in own:
        advantage = "有兵线、敌方支援位置明确时，让米莱狄与队友把机会换成塔；鲁班若在场可安全跟进输出，别越塔追人让推进脱节。" if "鲁班七号" in own else "有兵线、敌方支援位置明确时，让米莱狄与队友把机会换成塔，留意侧翼与回防。"
    fallback = "落后或人数不齐时先守塔清线，等对方阵型分散再接；看不清河道就不硬争，另一侧资源也要确认安全才能交换。"
    personal = None
    if player:
        info = book.get(player)
        personal = {"hero": player, "text": info["ally_plan"] if info else "该英雄的打法资料尚未覆盖，先与队友确认分路和职责。"}
        if player == "蔡文姬" and "后羿" in own:
            personal["text"] = "团战先跟住后羿，移动时与他保持支援距离；别独自走到最前方探草。敌方突进靠近时再用控制配合队友保护。"
        elif player == "后羿" and "蔡文姬" in own:
            personal["text"] = "以补兵和存活保证持续输出，团战先打最近的安全目标；与蔡文姬保持支援距离，远程开团前确认队友跟得上。"
        elif player in ("韩信", "孙悟空"):
            personal["text"] += "。若队内有其他常用打野英雄，先约定资源分配；绕后也要确认己方核心不会被无人照看。"

    unknown = [name for name in allies + enemies if name not in book]
    notes = ["按阵容提供的赛前方案；进入对局后，按实际分路、兵线、位置、状态和技能调整。"]
    if len(allies) < 5 or len(enemies) < 5:
        notes.append("阵容未齐，仅分析已填英雄。")
    if unknown:
        notes.append("未覆盖的英雄：" + "、".join(dict.fromkeys(unknown)) + "；对应机制不补猜。")
    if len(junglers) > 1:
        notes.append("常用位置不是本局分路；需先分配打野和走线职责。")
    return {"title": title, "summary": summary, "opening": opening, "teamfight": teamfight,
            "advantage": advantage, "fallback": fallback, "cautions": cautions, "personal": personal,
            "allies": allies, "enemies": enemies, "source": source, "notes": notes,
            "basis": [{"hero": name, "source": "hero_relations.json" if name in HEROES else "loading_plan.py 补充机制草案"} for name in dict.fromkeys(allies + enemies) if name in book],
            "version_note": "机制草案，待当前版本教练复核；不预测胜率、不提供未经核验的数值或固定出装。"}
