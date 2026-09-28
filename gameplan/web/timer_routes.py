"""Shared persisted timer routes for the workbench and desktop panel."""
import asyncio
import json
import re
import time
from collections import defaultdict
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from gameplan.core.models import SkillTimerRequest, VoiceCommandRequest, CommandTimerRequest
from gameplan.paths import ROOT
from gameplan.voice import VoiceCooldownAssistant
from gameplan.voice.cooldown_assistant import REPORT_DELAY_S


def routes(publish=None, catalog=None, match_locks=None):
    router = APIRouter()
    locks = match_locks if match_locks is not None else defaultdict(asyncio.Lock)

    @router.get("/api/knowledge/heroes")
    def knowledge_heroes():
        assistant = VoiceCooldownAssistant()
        return {"heroes": [{"hero": hero, "aliases": sorted({alias for alias, name in assistant._aliases if name == hero})}
                           for hero in assistant.known_heroes]}

    async def notify(match_id, kind, data):
        if publish is not None:
            await publish(match_id, kind, data)

    def get_skills():
        if catalog is not None:
            return catalog()
        return json.loads((ROOT / "resources/knowledge/skill_catalog.json").read_text(encoding="utf-8"))

    def validate_timer_match(match_id):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", match_id):
            raise HTTPException(400, "对局编号无效")


    @router.get("/api/matches/{match_id}/skill-timers")
    def get_skill_timers(match_id: str):
        from gameplan.core import storage
        import gameplan.skills.cooldowns as cooldowns
        validate_timer_match(match_id)
        return cooldowns.snapshot(match_id, storage.memory(match_id))


    @router.post("/api/matches/{match_id}/skill-timers")
    async def register_skill_timer(match_id: str, req: SkillTimerRequest):
        from gameplan.core import storage
        import gameplan.skills.cooldowns as cooldowns
        validate_timer_match(match_id)
        async with locks[match_id]:
            memory = storage.memory(match_id)
            try:
                item = cooldowns.register(memory, req, get_skills())
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            storage.save_memory(match_id, memory)
            storage.audit(match_id, {"kind": "skill_cast" if req.mode == "cast" else "skill_remaining", "timer": item})
            result = cooldowns.snapshot(match_id, memory)
            await notify(match_id, "skill_timers", result)
        return result


    def _voice_seconds(value):
        if value is None:
            return "未知"
        value = float(value)
        return f"{int(value)}" if value.is_integer() else f"{value:g}"


    @router.post("/api/matches/{match_id}/text-command")
    async def register_text_command(match_id: str, req: VoiceCommandRequest):
        text = req.text.strip()
        if not text.startswith("小象助手"):
            text = "小象助手，" + text
        return await register_command(match_id, VoiceCommandRequest(text=text), source="text")

    @router.post("/api/matches/{match_id}/voice-command")
    async def register_voice_command(match_id: str, req: VoiceCommandRequest):
        return await register_command(match_id, req, source="voice")

    async def register_command(match_id, req, *, source):
        from gameplan.core import storage
        import gameplan.skills.cooldowns as cooldowns
        """Turn one wake-word ASR result into a persisted enemy cooldown event."""
        validate_timer_match(match_id)
        resolution = VoiceCooldownAssistant().resolve_commands(req.text)
        if resolution["status"] == "needs_confirmation":
            message = ("这个称呼对应多个英雄，请选择本次要记录的英雄。" if resolution["candidates"]
                       else "候选组合较多，请使用英雄全名重新输入。")
            return {"ok": False, "status": "needs_confirmation", "message": message,
                    "candidates": resolution["candidates"]}
        commands = resolution["commands"]
        if not commands:
            raise HTTPException(422, "未识别到有效指令，请输入“妲己放大了”或“后裔小明都放大招了”；请确认每个英雄名称与释放动作。")

        async with locks[match_id]:
            memory = storage.memory(match_id)
            events, timers, messages = [], [], []
            for command in commands:
                recorded_at = time.time()
                cooldown_s = command["cooldown_s"]
                event = {
                    "id": "voice_" + uuid4().hex,
                    "hero": command["hero"],
                    "skill_kind": command["skill_type"],
                    "skill": command["skill_name"],
                    "source": source,
                    "recognized_text": command["raw_text"],
                    "recorded_epoch": recorded_at,
                    "cooldown_s": cooldown_s,
                    "status": "unknown" if cooldown_s is None else "cooling",
                }
                memory.setdefault("voice_events", []).append(event)
                memory["voice_events"] = memory["voice_events"][-100:]
    
                timer = None
                if cooldown_s is not None:
                    timer_request = CommandTimerRequest(
                        side="enemy", hero=command["hero"], slot=command["slot"],
                        mode="cast", seconds=cooldown_s, elapsed_s=min(REPORT_DELAY_S, cooldown_s), game_time_s=None,
                    )
                    try:
                        timer = cooldowns.register(
                            memory, timer_request, get_skills(),
                            skill_kind="summoner" if command["skill_type"] == "flash" else "hero",
                            skill_name=command["skill_name"], source=source,
                            metadata={"recognized_text": command["raw_text"], "wake_word": "小象助手",
                                      "report_delay_s": min(REPORT_DELAY_S, cooldown_s)},
                        )
                    except ValueError as exc:
                        raise HTTPException(422, str(exc)) from exc
                    event["timer_id"] = timer["id"]
                    event["ready_epoch"] = timer["ready_epoch"]
                else:
                    timer = {
                        "id": event["id"], "side": "enemy", "hero": command["hero"],
                        "slot": command["slot"], "skill": command["skill_name"],
                        "skill_kind": "hero", "source": source, "mode": "cast",
                        "seconds": None, "recorded_epoch": recorded_at,
                        "cast_epoch": recorded_at, "cast_game_time_s": None,
                        "ready_epoch": None, "ready_game_time_s": None,
                        "status": "unknown",
                    }
                    existing = memory.get("skill_timers", [])
                    replaced = {row["id"] for row in existing if cooldowns._timer_key(row) == cooldowns._timer_key(timer)}
                    if len(existing) - len(replaced) >= 40:
                        raise HTTPException(422, "最多同时记录 40 个技能，请先移除不需要的计时")
                    cooldowns.retire(memory, replaced, "superseded")
                    memory["skill_timers"].append(timer)
                    memory["skill_timer_history"] = (memory.get("skill_timer_history", []) + [dict(timer)])[-100:]
                    event["timer_id"] = timer["id"]
                events.append(event)
                timers.append(timer)
                label = "冷却时间未知" if cooldown_s is None else f"开始 {_voice_seconds(max(0.0, cooldown_s - REPORT_DELAY_S))} 秒预计倒计时（已补偿3秒报时延迟）"
                messages.append(f"已记录{command['hero']}{command['skill_name']}，{label}")
            storage.save_memory(match_id, memory)
            storage.audit(match_id, {"kind": source + "_command", "events": events, "commands": commands})
            result = cooldowns.snapshot(match_id, memory)
            await notify(match_id, "skill_timers", result)

        response = {"ok": True, "message": "；".join(messages), "commands": commands,
                    "events": events, "timers": timers, "skill_timers": result}
        if len(commands) == 1:
            response.update(command=commands[0], event=events[0], timer=timers[0])
        return response


    @router.post("/api/matches/{match_id}/skill-timers/clear")
    async def clear_skill_timers(match_id: str):
        from gameplan.core import storage
        import gameplan.skills.cooldowns as cooldowns
        validate_timer_match(match_id)
        async with locks[match_id]:
            memory = storage.memory(match_id)
            cooldowns.retire(memory, {item["id"] for item in memory.get("skill_timers", [])}, "cleared")
            storage.save_memory(match_id, memory)
            storage.audit(match_id, {"kind": "skill_timers_cleared"})
            result = cooldowns.snapshot(match_id, memory)
            await notify(match_id, "skill_timers", result)
        return result


    @router.post("/api/matches/{match_id}/skill-timers/{timer_id}/cancel")
    async def cancel_skill_timer(match_id: str, timer_id: str):
        from gameplan.core import storage
        import gameplan.skills.cooldowns as cooldowns
        validate_timer_match(match_id)
        async with locks[match_id]:
            memory = storage.memory(match_id)
            if not any(item["id"] == timer_id for item in memory.get("skill_timers", [])):
                raise HTTPException(404, "该技能计时已被替换或移除")
            cooldowns.retire(memory, {timer_id}, "cancelled")
            storage.save_memory(match_id, memory)
            storage.audit(match_id, {"kind": "skill_timer_cancelled", "timer_id": timer_id})
            result = cooldowns.snapshot(match_id, memory)
            await notify(match_id, "skill_timers", result)
        return result


    return router
