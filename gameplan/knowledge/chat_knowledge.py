"""Ground conversation references in the two existing local knowledge bases."""
from functools import lru_cache
from html import unescape
import json
import re

from gameplan.paths import ROOT
from gameplan.knowledge import lol_knowledge, skill_knowledge

GAMES = {"hok": "王者荣耀", "lol": "英雄联盟"}
ALIASES = ROOT / "resources/knowledge/hero_voice_aliases.json"
ITEMS = ROOT / "resources/knowledge/equipment_catalog.json"


def plain(value):
    text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]*>", "", str(value or "")))).strip()
    return re.sub(r"[。；]{2,}", "。", text)


def short(value, limit=150):
    text = plain(value)
    if len(text) <= limit:
        return text
    sentences = re.split(r"(?<=[。！？；])", text)
    result = ""
    for sentence in sentences:
        if len(result + sentence) > limit:
            break
        result += sentence
    return result or text[:limit] + "…"


def directory():
    stamp = tuple(path.stat().st_mtime_ns for path in (ALIASES, skill_knowledge.CATALOG_PATH, lol_knowledge.CATALOG_PATH))
    return _directory(stamp)


@lru_cache(maxsize=1)
def _directory(stamp):
    aliases = json.loads(ALIASES.read_text(encoding="utf-8"))["heroes"]
    rows = []
    for h in skill_knowledge._catalog()["heroes"]:
        alias = aliases.get(h["hero"], {})
        rows.append({"game": "hok", "id": h["hero"], "name": h["hero"],
                     "aliases": {h["hero"], h.get("title", ""), *alias.get("aliases", []), *alias.get("asr_variants", [])},
                     "skills": [s["name"] for s in h["skills"]]})
    for h in lol_knowledge.catalog()["heroes"]:
        rows.append({"game": "lol", "id": h["id"], "name": h["name"],
                     "aliases": {h["id"], h["name"], h["title"], *h["aliases"]},
                     "skills": [s["name"] for s in h["skills"]]})
    return rows


def identity(row):
    return {key: row[key] for key in ("game", "id", "name")}


def explicit_game(text):
    games = []
    if re.search(r"英雄联盟|\blol\b|联盟", text, re.I):
        games.append("lol")
    if re.search(r"王者荣耀|(?:^|玩|问|说|是|切换|换到|在|那)王者(?!独尊)", text):
        games.append("hok")
    return games[0] if len(games) == 1 else None


def equipment_documents(text, game=None):
    documents = []
    if game in (None, "lol"):
        catalog = lol_knowledge.catalog()
        for item in catalog["items"].values():
            if item["name"] in text:
                documents.append({"id": "lol:item:" + item["id"], "kind": "equipment", "game": "lol", "hero": "", "title": item["name"],
                                  "text": item["name"] + f"，总价{item['gold']}金币。" + item["description"],
                                  "source_url": catalog["sources"]["items"], "page_url": "/knowledge/lol", "version": catalog["version"], "note": "召唤师峡谷装备资料快照。"})
    if game in (None, "hok"):
        catalog = json.loads(ITEMS.read_text(encoding="utf-8"))
        for item in catalog["items"]:
            if item["item_name"] in text:
                documents.append({"id": "hok:item:" + str(item["item_id"]), "kind": "equipment", "game": "hok", "hero": "", "title": item["item_name"],
                                  "text": item["item_name"] + f"，总价{item['total_price']}金币。" + plain(item.get("des1")) + " " + plain(item.get("des2")),
                                  "source_url": catalog["source_url"], "page_url": "/static/knowledge.html", "version": "官网版本待核验", "note": "官网装备参考。"})
    # Prefer the complete item name over a shorter component appearing inside it.
    return [d for d in documents if not any(d["title"] != other["title"] and d["title"] in other["title"] for other in documents)]


def mentions(text, game=None, skills=False):
    text = text.casefold()
    hits = []
    for row in directory():
        if game and row["game"] != game:
            continue
        for alias in row["skills"] if skills else row["aliases"]:
            alias = alias.casefold().strip()
            if not alias:
                continue
            if re.fullmatch(r"[a-z0-9]+", alias):
                pattern = r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])"
            elif len(alias) == 1:
                pattern = r"(?:^|[，。！？\s]|问|玩|用|的|对面|敌方|对方|取消)" + re.escape(alias) + r"(?=的|怎么|大招|技能|闪现|[一二三四1234]技能|放|开|用|交|刚|没|已经|现在|$)"
            else:
                pattern = re.escape(alias)
            for match in re.finditer(pattern, text):
                hits.append((match.start(), match.end(), row))
    # A full name wins over aliases contained inside it; equal spans retain ambiguity.
    hits = [hit for hit in hits if not any(a <= hit[0] and b >= hit[1] and b-a > hit[1]-hit[0] for a, b, _ in hits)]
    unique = {}
    for start, end, row in sorted(hits, key=lambda hit: hit[0]):
        unique[(row["game"], row["id"])] = identity(row)
    return list(unique.values()), hits


def resolve(text, context):
    """Return resolved scope or a clarification, never guess between shared aliases."""
    game = explicit_game(text)
    pending = context.get("pending")
    options = pending.get("choices", []) if pending else []
    if pending:
        matches, _ = mentions(text, game)
        chosen = [row for row in options if row in matches or (game and row["game"] == game and not matches)]
        if len(chosen) == 1:
            return {"question": pending["question"], "heroes": chosen, "game": chosen[0]["game"]}
    found, hits = mentions(text, game)
    if not found:
        found, hits = mentions(text, game, skills=True)
    ambiguous = any(a == c and b == d and (r["game"], r["id"]) != (s["game"], s["id"])
                    for a, b, r in hits for c, d, s in hits)
    if ambiguous and not game and context.get("game"):
        scoped = [row for row in found if row["game"] == context["game"]]
        if len(scoped) == 1:
            found, ambiguous = scoped, False
    if ambiguous:
        return {"clarification": "你说的是哪一位？" + "、".join(f"{GAMES[row['game']]}的{row['name']}" for row in found[:6]) + "。",
                "pending": {"question": text, "choices": found[:6]}}
    if len(found) > 3:
        return {"clarification": "这次先讨论哪一到三位英雄？", "pending": None}
    if found:
        return {"question": text, "heroes": found, "game": found[0]["game"] if len({r['game'] for r in found}) == 1 else None}
    equipment = equipment_documents(text, game)
    item_games = {d["game"] for d in equipment}
    if len(item_games) == 1:
        return {"question": text, "heroes": [], "game": next(iter(item_games))}
    if len(item_games) > 1 and not context.get("game"):
        return {"clarification": "这个装备名称在两个游戏里都有，你想查王者荣耀还是英雄联盟？", "pending": {"question": text, "choices": []}}
    scope = game or context.get("game")
    followup = re.search(r"他|她|它|这个英雄|刚才|上述|前面|继续|再说|再讲|详细|那.*呢", text)
    bare = re.fullmatch(r"[那再请问呢的，。？！\s]*(大招|[qwerQWER]|[一二三四1234]技能|技能|出装|装备|打法|符文|铭文|冷却|怎么出装|怎么玩|怎么打|怎么连招).{0,18}", text)
    if (followup or bare) and context.get("heroes") and (not game or game == context.get("game")):
        if len(context["heroes"]) > 1 and followup:
            return {"clarification": "你指的是哪位英雄？", "pending": {"question": text, "choices": context["heroes"]}}
        return {"question": text, "heroes": context["heroes"], "game": scope}
    if not scope:
        return {"clarification": "你想问王者荣耀还是英雄联盟？也可以直接告诉我英雄名字。",
                "pending": {"question": text, "choices": []}}
    if pending and not options and game and text.strip(" ，。！？") in ("王者", "王者荣耀", "英雄联盟", "联盟", "lol", "LOL"):
        return {"question": pending["question"], "heroes": [], "game": game}
    return {"question": text, "heroes": [], "game": scope}


def is_direct_question(question, heroes):
    """Only narrow fact queries bypass semantic coverage assessment."""
    remainder = question
    refs = {(h["game"], h["id"]) for h in heroes}
    names = {name for row in directory() if (row["game"], row["id"]) in refs for name in row["aliases"] | set(row["skills"]) if len(name) >= 2}
    for name in sorted(names, key=len, reverse=True):
        remainder = re.sub(re.escape(name), "", remainder, flags=re.I)
    remainder = re.sub(r"王者荣耀|英雄联盟|\blol\b|小象助手|请问|帮我|查询|查一下|查下|告诉我|介绍一下|一下|各等级|基础|一技能|二技能|三技能|四技能|[1234]技能|怎么出装|怎么打|怎么玩|有哪些|是什么|多少钱|多长时间|冷却时间|冷却|多久|多少秒|几秒|出装|装备|购买|大招|被动|技能|介绍|效果|作用|打法|对线|团战|思路|技巧|符文|铭文|加点|推荐|[qwercd]+", "", remainder, flags=re.I)
    return not remainder.strip(" 他她它的那这个呢吗啊呀和以及、与还再也要都了是多少，。？！；：,?! \n")


def hero_documents(ref):
    """Map the existing structured documents to small, independently cited passages."""
    game, hero_id = ref["game"], ref["id"]
    documents = []
    if game == "lol":
        h = lol_knowledge.get_hero(hero_id)
        url, page, version = h["source_url"], "/knowledge/lol?hero=" + hero_id, h["version"]
        note = "基础冷却未计技能急速；出装是参考装备池，官方技巧版本待复核。"
    else:
        h = skill_knowledge.get_hero(hero_id)
        url, page, version = h["source_url"], "/static/knowledge.html", "官网未标明正式服版本"
        note = h["catalog_note"]

    def add(kind, suffix, title, text, **extra):
        if plain(text):
            documents.append({"id": f"{game}:{hero_id}:{suffix}", "kind": kind, "game": game, "hero": ref["name"],
                              "title": title, "text": plain(text), "source_url": url, "page_url": page,
                              "version": version, "note": note, **extra})

    for skill in h["skills"]:
        slot = str(skill["slot"])
        ultimate = slot == "R" if game == "lol" else skill["is_ultimate"]
        passive = slot == "P" if game == "lol" else skill["passive"]
        label = "大招" if ultimate else "被动" if passive else slot + ("技能" if game == "hok" else "")
        add("skill", "skill-" + slot, ref["name"] + label + "·" + skill["name"], skill["description"], slot=slot, ultimate=ultimate, passive=passive)
        cd = skill.get("cooldowns", skill.get("cooldown_s", []))
        uncertain = skill.get("cooldown_review_required") or skill.get("cooldown_kind") in ("dynamic", "unknown") or not cd or any(v <= 0 for v in cd)
        if not passive:
            rendered = "、".join(f"{v:g}" for v in cd) + "秒" if cd and not uncertain else skill["cooldown_text"]
            prefix = "资料有特殊机制或待核对：" if uncertain else "各等级基础冷却依次为"
            if cd and not uncertain and len(set(cd)) == 1:
                prefix, rendered = "各等级基础冷却均为", f"{cd[0]:g}秒"
            add("cooldown", "cd-" + slot, ref["name"] + label, prefix + rendered + "。基础值不等于实战剩余冷却。", slot=slot, ultimate=ultimate, uncertain=bool(uncertain))
    if game == "lol":
        add("play", "play", "使用技巧", "。".join(h["ally_tips"][:3]) or h["play_reference"]["text"])
        add("counter", "counter", "对抗技巧", "。".join(h["enemy_tips"][:3]))
        add("teamfight", "teamfight", "团战思路（类型参考）", h["play_reference"]["text"])
        build = h["build_reference"]
        add("build", "build", "可选装备池", "可考虑" + "、".join(item["name"] for item in build["items"][:5]) + "。" + build["note"] + "这不是固定购买顺序或版本最优出装。")
        add("lore", "lore", "英雄背景", h.get("description"))
    else:
        strategy = h.get("strategy_reference") or {}
        for kind, key, label in [("play", "usage", "使用技巧"), ("counter", "counterplay", "对抗技巧"), ("teamfight", "teamfight", "团战思路"), ("runes", "runes_note", "铭文参考"), ("order", "skill_order", "加点参考")]:
            add(kind, key, label, strategy.get(key))
        for i, build in enumerate(strategy.get("builds", [])[:1]):
            items = [item["name"] for item in build.get("items", []) if item.get("name")]
            if items:
                add("build", "build", "官网出装参考", "可参考" + "、".join(items) + "。" + build.get("note", "") + "官网攻略版本待复核，请按对局调整。")
        profile = h.get("coaching_profile") or {}
        for i, rule in enumerate(profile.get("rules", [])[:12]):
            text = str(rule.get("when", "")) + "：" + str(rule.get("action", ""))
            add("coaching", f"coach-{i}", rule.get("title", "条件打法"), text)
    return documents


def topics(question):
    result = []
    for kind, pattern in [
        ("cooldown", r"冷却|[cC][dD]|多久|几秒"), ("build", r"出装|装备|出什么|买什么"),
        ("runes", r"符文|铭文"), ("order", r"加点|主升|主[一二三qweQWE]|技能顺序"),
        ("teamfight", r"团战"), ("counter", r"怎么打败|怎么克制|如何克制|怎么对付|对抗技巧"),
        ("play", r"怎么玩|怎么打|如何玩|打法|思路|技巧|对线|连招"),
    ]:
        if re.search(pattern, question):
            result.append(kind)
    if (not result or re.search(r"效果|作用|介绍|技能.*(和|及).*冷却", question)) and re.search(r"技能|大招|被动|[qwerQWER]|介绍|效果", question):
        result.append("skill")
    return list(dict.fromkeys(result))


def selected_skill(question, docs):
    if re.search(r"大招|终极|(?<![a-zA-Z])[rR](?![a-zA-Z])", question):
        return [d for d in docs if d.get("ultimate")]
    match = re.search(r"([一二三四1234])技能|(?<![a-zA-Z])([qweQWE])(?![a-zA-Z])", question)
    if match:
        slot = str("一二三四".index(match[1]) + 1) if match[1] and match[1] in "一二三四" else match[1] if match[1] else match[2].upper()
        return [d for d in docs if d.get("slot") == slot]
    if "被动" in question:
        return [d for d in docs if d.get("passive")]
    named = [d for d in docs if "·" in d["title"] and d["title"].split("·", 1)[1] in question]
    if named:
        slots = {d["slot"] for d in named}
        return [d for d in docs if d.get("slot") in slots]
    return docs


def retrieve(question, resolved):
    docs = [doc for hero in resolved["heroes"] for doc in hero_documents(hero)]
    equipment = equipment_documents(question, resolved["game"])
    docs.extend(equipment)
    wanted = topics(question)
    advanced = bool(re.search(r"连招|胜率|最强|最新|这个版本|当前版本|版本答案|能不能|为什么|能否|计算|多少伤害|克制谁|对比|比较", question)) or len(resolved["heroes"]) > 1 or not is_direct_question(question, resolved["heroes"])
    if not wanted:
        advanced = True
    chosen, missing = [], []
    for hero in resolved["heroes"]:
        own = [d for d in docs if d["game"] == hero["game"] and d["hero"] == hero["name"]]
        for kind in wanted:
            pool = selected_skill(question, own) if kind in ("skill", "cooldown") else own
            rows = [d for d in pool if d["kind"] == kind]
            if rows:
                chosen.extend(rows[:5] if kind in ("skill", "cooldown") else rows[:1])
                if any(d.get("uncertain") for d in rows):
                    missing.append(hero["name"] + "的精确冷却与特殊机制说明")
            else:
                missing.append(hero["name"] + "的" + {"runes": "符文/铭文", "counter": "对抗技巧", "order": "加点", "cooldown": "冷却", "skill": "指定技能"}.get(kind, kind))
    if not resolved["heroes"] and resolved["game"] == "hok":
        book = json.loads((ROOT / "resources/knowledge/knowledge_base.json").read_text(encoding="utf-8"))
        for entry in book:
            if any(tag in question for tag in entry["tags"]):
                chosen.append({"id": "hok:macro:" + entry["id"], "kind": "macro", "game": "hok", "hero": "",
                               "title": entry["title"], "text": entry["content"], "page_url": "/static/knowledge.html",
                               "source_url": "", "version": entry["source_version"], "note": "本地教练原则，待人工复核。"})
    if equipment and not resolved["heroes"]:
        chosen = equipment
        # Item-specific effects can require interpretation: assess coverage locally.
        advanced = True
    # Complex questions are assessed against passages by the local model before fallback.
    evidence = chosen if not advanced and chosen else docs
    if not evidence:
        evidence = chosen
    if not evidence:
        missing.append("知识库未收录能回答该问题的资料")
    return {"documents": evidence[:20], "direct": not advanced and bool(chosen), "missing": missing,
            "topics": wanted, "question": question}


def direct_answer(result):
    docs = result["documents"]
    parts, ids = [], []
    for doc in docs[:5]:
        allowance = max(35, 220 // max(1, len(docs)))
        prefix = (doc["title"] + "：") if doc["kind"] in ("cooldown", "skill") else (doc["hero"] + "：") if doc["hero"] else ""
        parts.append(prefix + short(doc["text"], allowance))
        ids.append(doc["id"])
    text = "根据知识库，" + "；".join(part.rstrip("。；") for part in parts)
    if any(d["kind"] != "cooldown" for d in docs):
        text += "。资料按收录版本参考。"
    return text, ids
