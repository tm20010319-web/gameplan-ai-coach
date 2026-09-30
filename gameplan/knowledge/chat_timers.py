"""Ground explicit cast reports in the local catalog; no model inference."""
import math
import re
import time
from uuid import uuid4

from gameplan.knowledge import chat_knowledge as kb, skill_knowledge, lol_knowledge

SKILL = r"大招|[一二三四1234]技能|[QWERqwer](?:技能)?|闪现"
CAST = re.compile(rf"(?P<subject>.+?)(?:刚刚|刚才|刚|又)?(?:放了|用了|交了|释放了|使用了|开了)(?P<before>{SKILL})(?:了)?$|(?P<subject_after>.+?)(?:刚刚|刚才|刚|又)?(?P<after>{SKILL})(?:用了|放了|交了|释放了|使用了|开了)$|(?P<subject_verb>.+?)(?:刚刚|刚才|刚|又)?(?:用|放|开|交|使用|释放)(?P<verb>{SKILL})了$|(?P<subject_short>.+?)(?:刚刚|刚才|刚|又)?(?P<short>放大了|开大了|闪现了)$")
CANCEL = re.compile(rf"取消(?P<subject>.+?)(?P<skill>{SKILL})计时")
# In this chat, an unavailable-ultimate report explicitly means start now.
# Full matching keeps questions and "还没用大招" out of the cast path.
UNAVAILABLE = re.compile(
    r"(?P<subject>.+?)(?:已经|现在|刚刚|刚才|刚|又)?"
    r"(?:(?:没|没有)(?P<absent>大招|大|闪现|闪)(?:了)?|(?P<absent_after>大招|闪现)(?:没了|没有了|用掉了|交掉了|还在冷却|在冷却))"
)
COMPOUND_SKILL = r"(?:大招|大|[Rr](?:技能)?|闪现|闪)"
SKILL_LIST = rf"{COMPOUND_SKILL}(?:(?:和|与|以及|、)?{COMPOUND_SKILL})+"
REPORT_ATOM = rf"(?:(?:没|没有){COMPOUND_SKILL}(?:了)?|{COMPOUND_SKILL}(?:也|都)?(?:没了|没有了|用了|交了|用掉了|交掉了))"
COMPOUND_REPORTS = [re.compile(pattern) for pattern in (
    rf"(?P<subject>.+?)(?P<actions>{REPORT_ATOM}(?:(?:也|还|和|与|、)?{REPORT_ATOM})+)",
    rf"(?P<subject>.+?)(?P<actions>{SKILL_LIST})(?:都|也)?(?:用了|交了|用掉了|交掉了|没了|没有了)",
    rf"(?P<subject>.+?)(?:没|没有|用了|交了)(?P<actions>{SKILL_LIST})(?:了)?",
)]


def clean(text):
    return re.sub(r"[\s，,。.!！:：;；]+", "", text).removeprefix("小象助手")


def clear_request(text, context):
    if clean(text) not in {"清空计时器", "清空所有计时", "取消所有计时"}:
        return None
    context = {**context, "pending": None}
    context.pop("timer_queue", None)
    return reply("已清空当前对话的技能计时。", context, [{"action": "clear"}])


def reply(text, context, events=None):
    return {"answer": text, "status": "timer", "sources": [], "choices": [],
            "external_status": "not_needed", "context": context, "timer_events": events or []}


def parse_report(text):
    text = clean(text)
    match = CANCEL.fullmatch(text)
    cancel = match is not None
    if not match:
        match = CAST.fullmatch(text)
    unavailable = False
    if not match:
        match = UNAVAILABLE.fullmatch(text)
        unavailable = match is not None
    if not match:
        return None
    values = match.groupdict()
    subject = next((values.get(k) for k in ("subject", "subject_after", "subject_verb", "subject_short") if values.get(k)), "")
    skill_label = next((values.get(k) for k in ("skill", "before", "after", "verb", "short") if values.get(k)), "")
    if unavailable:
        skill_label = "闪现" if (values.get("absent") or values.get("absent_after")).startswith("闪") else "大招"
    elif skill_label in {"开大了", "放大了"} or skill_label.upper() in {"R", "R技能"}:
        skill_label = "大招"
    elif skill_label == "闪现了":
        skill_label = "闪现"
    subject = re.sub(r"(?:(?:也|都|一起|已经|现在|刚刚|刚才|刚|又))+$", "", subject)
    return subject, skill_label, cancel


def parse_compound_report(text):
    for pattern in COMPOUND_REPORTS:
        match = pattern.fullmatch(clean(text))
        if match:
            subject = re.sub(r"(?:(?:也|都|一起|已经|现在|刚刚|刚才|刚|又))+$", "", match["subject"])
            skills = list(dict.fromkeys("闪现" if s.startswith("闪") else "大招"
                                        for s in re.findall(COMPOUND_SKILL, match["actions"])))
            return subject, skills
    return None


def answer(resolved, context, received_at=None):
    parsed = parse_report(resolved["question"])
    if parsed is None:
        return None
    subject, skill_label, cancel = parsed
    heroes = resolved["heroes"]
    if len(heroes) != 1:
        return reply("请一次报一位英雄的技能，例如“小明大招用了”。", context)
    hero = heroes[0]
    row = next(row for row in kb.directory() if row["game"] == hero["game"] and row["id"] == hero["id"])
    subject = re.sub(r"^(?:王者荣耀|王者|英雄联盟|联盟|lol)(?:的)?", "", subject, flags=re.I)
    subject = re.sub(r"^(?:敌方|对面|对方)", "", subject).removesuffix("的")
    if subject.casefold() not in {clean(alias).casefold() for alias in row["aliases"]} and subject not in {"他", "她", "它", "这个英雄", ""}:
        return None
    game = hero["game"]
    new_context = {"game": game, "heroes": [hero], "pending": None}
    if game == "lol":
        if skill_label not in {"大招", "闪现"}:
            return reply("英雄联盟目前只计大招和闪现，Q、W、E不启动计时。", new_context)
        if skill_label == "闪现":
            spell = lol_knowledge.summoner_spell("SummonerFlash")
            duration, slot, name = spell["cooldown_s"], 5, spell["name"]
        else:
            record = lol_knowledge.get_hero(hero["id"])
            skill = next((s for s in record["skills"] if s["slot"] == "R"), None)
            slot, name = "R", skill["name"] if skill else "大招"
            values = skill.get("cooldowns", []) if skill else []
            duration = values[0] if values and all(math.isfinite(v) and v > 0 for v in values) else None
            if skill and (skill.get("cooldown_review_required") or skill.get("cooldown_kind") in {"dynamic", "unknown"}):
                duration = None
    elif skill_label == "闪现":
        # Use the same catalog-backed default as the existing text timer.
        from gameplan.voice import VoiceCooldownAssistant
        command = VoiceCooldownAssistant().parse_command(f"小象助手，{hero['name']}闪现了")
        duration = command["cooldown_s"]
        slot, name = 5, "闪现"
    else:
        if skill_label not in {"大招", *[str(n) + "技能" for n in range(1, 5)], *[n + "技能" for n in "一二三四"]}:
            return reply("王者荣耀请用大招、一至四技能或闪现报时。", new_context)
        record = skill_knowledge.get_hero(hero["id"])
        slot = None if skill_label == "大招" else int(skill_label[0]) if skill_label[0].isdigit() else "一二三四".index(skill_label[0]) + 1
        skill = record["ultimate"] if slot is None else next((s for s in record["skills"] if s["slot"] == slot and not s["passive"]), None)
        if not skill:
            return reply(f"{hero['name']}没有收录这个主动技能，暂不能计时。", new_context)
        slot, name = skill["slot"], skill["name"]
        duration = skill["cooldown_s"][0] if skill["cooldown_s"] else None
        if skill["cooldown_kind"] == "dynamic" or skill["cooldown_review_required"]:
            duration = None
    if cancel:
        return reply(f"已取消{hero['name']}{skill_label}计时。", new_context,
                     [{"action": "cancel", "game": game, "hero": hero["name"], "hero_id": hero["id"], "slot": slot}])
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        duration = None
    started = time.time() if received_at is None else received_at
    timer = {"id": uuid4().hex, "game": game, "hero": hero["name"], "hero_id": hero["id"], "slot": slot,
             "skill": skill_label, "skill_name": name, "cooldown_s": duration,
             "started_at": started, "ready_at": started + duration if duration is not None else None}
    if duration is None:
        message = f"已记录{hero['name']}{skill_label}使用。该技能冷却存在特殊机制或资料待核验，暂不显示倒计时。"
    else:
        modifiers = "技能急速、符文、装备及技能刷新" if game == "lol" else "冷却缩减和技能刷新"
        message = f"已记录{hero['name']}{skill_label}使用，从现在开始倒计时{duration:g}秒，到期会提示预计已好。按{'基础冷却' if slot == 5 else '一级基础冷却'}估算，未计{modifiers}。"
    return reply(message, new_context, [{"action": "start", "timer": timer}])


def _reports(text):
    """Keep clause boundaries: never concatenate two cast reports into a name."""
    reports, unrecognized = [], []
    waiting_names = []
    aliases = {clean(alias).casefold() for row in kb.directory() for alias in row["aliases"] if alias}

    def known_name(subject):
        unscoped = re.sub(r"^(?:王者荣耀|王者|英雄联盟|联盟|lol)(?:的)?", "", subject, flags=re.I)
        unscoped = re.sub(r"^(?:敌方|对面|对方)", "", unscoped).removesuffix("的")
        return unscoped.casefold() in aliases

    def split_names(subject):
        # Do not split a conjunction inside an official title such as 和平守望.
        return [subject] if known_name(subject) else re.split(r"[、]|和|与|跟", subject)

    # The wake word can be followed by a comma without becoming a report itself.
    text = re.sub(r"^\s*小象助手[，,\s]*", "", text)
    for clause in re.split(r"[，,；;。\n]+", text):
        if not clause.strip():
            continue
        compound = parse_compound_report(clause)
        parsed = (compound[0], compound[1], False) if compound else parse_report(clause)
        if parsed is None:
            names = split_names(clean(clause))
            if names and all(name and known_name(name) for name in names):
                waiting_names.extend(names)
            else:
                unrecognized.extend([*waiting_names, clause.strip()])
                waiting_names = []
            continue
        subject, skill, cancel = parsed
        skills = skill if compound else [skill]
        subjects = split_names(subject)
        if cancel:
            unrecognized.extend(waiting_names)
        elif subjects and all(known_name(name) or name.removesuffix("的") in {"他", "她", "它", "这个英雄"} for name in subjects):
            subjects = [*waiting_names, *subjects]
        else:
            unrecognized.extend(waiting_names)
        waiting_names = []
        for name in subjects:
            if name:
                for skill in skills:
                    reports.append((f"取消{name}{skill}计时" if cancel else f"{name}{skill}用了", skill, cancel))
    unrecognized.extend(waiting_names)
    return list(dict.fromkeys(reports)), unrecognized


def queue_report(queue, report, choices):
    """One hero confirmation should cover all skills reported for that name."""
    subject = parse_report(report)[0]
    for pending in queue:
        parsed = parse_report(pending["question"])
        if parsed and parsed[0] == subject and pending["choices"] == choices:
            questions = pending.setdefault("questions", [pending["question"]])
            if report not in questions:
                questions.append(report)
            return
    queue.append({"question": report, "choices": choices})


def _batch_reply(messages, events, context, queue):
    context = {**context, "pending": None}
    context.pop("timer_queue", None)
    result = reply("\n".join(messages), context, events)
    if queue:
        current, *rest = queue
        context["pending"] = current
        context["timer_queue"] = rest
        choices = current["choices"]
        questions = "、".join(current.get("questions", [current["question"]]))
        prompt = f"“{questions}”还未计时，请确认英雄：" if choices else f"“{questions}”的英雄未识别，请回复英雄全名。"
        result["answer"] = "\n".join([*messages, prompt])
        result["choices"] = [{"label": kb.GAMES[h["game"]] + " · " + h["name"],
                              "text": kb.GAMES[h["game"]] + "的" + h["name"]} for h in choices]
        if not events:
            result["status"] = "clarification"
    return result


def batch_answer(text, context, received_at=None):
    """Apply clear reports now, queue only unresolved reports for later turns."""
    reports, unrecognized = _reports(text)
    pending_batch = "timer_queue" in context and context.get("pending") is not None
    if pending_batch and not reports:
        pending = context["pending"]
        if clean(text) == "取消确认":
            return _batch_reply(["已取消尚未确认的计时，已开始的计时继续。"], [], context, [])
        resolved = kb.resolve(text, context)
        if not pending["choices"] and "clarification" not in resolved and len(resolved["heroes"]) == 1:
            hero = resolved["heroes"][0]
            parsed = parse_report(pending["question"])
            report = f"取消{hero['name']}{parsed[1]}计时" if parsed[2] else f"{hero['name']}{parsed[1]}用了"
            resolved = {"question": report, "heroes": [hero], "game": hero["game"]}
        if "clarification" not in resolved and len(resolved["heroes"]) == 1 and (resolved["question"] == pending["question"] or not pending["choices"]):
            timestamp = time.time() if received_at is None else received_at
            hero = resolved["heroes"][0]
            messages, events = [], []
            for question in pending.get("questions", [pending["question"]]):
                parsed = parse_report(question)
                canonical = f"取消{hero['name']}{parsed[1]}计时" if parsed[2] else f"{hero['name']}{parsed[1]}用了"
                result = answer({**resolved, "question": canonical}, context, timestamp)
                if result is not None and result.get("timer_events"):
                    messages.append(result["answer"])
                    events.extend(result["timer_events"])
            if events:
                return _batch_reply(messages, events, {"game": hero["game"], "heroes": [hero], "pending": None}, context["timer_queue"])
        return _batch_reply(["未确认英雄；已经开始的计时继续。"], [], context, [pending, *context["timer_queue"]])
    if len(reports) < 2 and not (reports and (pending_batch or unrecognized)):
        return None
    timestamp = time.time() if received_at is None else received_at
    queue = []
    if pending_batch:
        new_reports = {r[0] for r in reports}
        for pending in [context["pending"], *context["timer_queue"]]:
            expanded = [r[0] for q in pending.get("questions", [pending["question"]]) for r in _reports(q)[0]]
            # A fresh, complete report replaces the same pending report, including old malformed parses.
            if not expanded or not set(expanded) <= new_reports:
                queue.append({**pending, **({"questions": list(pending["questions"])} if "questions" in pending else {})})
    scope = {**context, "pending": None}
    scope.pop("timer_queue", None)
    explicit = kb.explicit_game(text)
    if explicit:
        scope["game"] = explicit
    messages, events, heroes, seen = [], [], [], set()
    for report, skill, cancel in reports:
        resolved = kb.resolve(report, scope)
        if "clarification" in resolved:
            queue_report(queue, report, (resolved.get("pending") or {}).get("choices", []))
            continue
        result = answer(resolved, scope, timestamp)
        if result is None or not resolved["heroes"]:
            queue_report(queue, report, [])
            continue
        for event in result.get("timer_events", []):
            item = event.get("timer", event)
            key = (event["action"], item["game"], item.get("hero_id", item["hero"]), item["slot"])
            if key not in seen:
                seen.add(key)
                events.append(event)
                if event["action"] == "start" and item["cooldown_s"] is not None:
                    messages.append(f"{item['hero']}{item['skill']}：已记录，开始{item['cooldown_s']:g}秒预计倒计时。")
                else:
                    messages.append(result["answer"])
        if not result.get("timer_events"):
            messages.append(result["answer"])
        heroes.extend(h for h in resolved["heroes"] if h not in heroes)
        scope = result["context"]
    if unrecognized:
        messages.append("以下内容未作为计时指令处理：" + "；".join(unrecognized))
    if any(e["action"] == "start" and e["timer"]["cooldown_s"] is not None for e in events):
        messages.append("均从现在开始，按默认基础冷却估算；到期分别提示预计已好。")
    if heroes:
        scope = {**scope, "heroes": heroes}
    return _batch_reply(messages, events, scope, queue)
