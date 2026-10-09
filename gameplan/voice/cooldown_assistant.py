"""Small closed loop for the voice driven enemy skill timer.

The module deliberately starts after speech-to-text.  A microphone adapter can
feed recognised text into :meth:`VoiceCooldownAssistant.process`, while a UI
can render :meth:`VoiceCooldownAssistant.snapshot` without knowing anything
about the knowledge file or parsing rules.

Example::

    assistant = VoiceCooldownAssistant()
    assistant.process("小象助手，妲己放大了")
    assistant.process("小象助手，孙悟空闪现了")
    timers = assistant.snapshot()

The default cooldown is an estimate from the voice knowledge file.  Entries
whose cooldown is dynamic or marked for review are still recorded, but expose
``status=\"unknown\"`` and no fabricated countdown.
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Literal
from uuid import uuid4

from gameplan.paths import ROOT

SkillKind = Literal["ultimate", "flash"]
REPORT_DELAY_S = 3.0


@dataclass(frozen=True, slots=True)
class ParsedVoiceCommand:
    """A recognised release command after the wake word was removed."""

    hero: str
    skill_kind: SkillKind
    raw_text: str
    wake_word: str


@dataclass(slots=True)
class CooldownEvent:
    """One active timer and its source metadata."""

    id: str
    hero: str
    skill_kind: SkillKind
    skill_name: str
    cooldown_s: float | None
    recorded_at: float
    ready_at: float | None
    raw_text: str


class VoiceCooldownAssistant:
    """Parse voice commands and maintain active ultimate/flash timers.

    ``now`` is injectable so callers and tests can use a monotonic game clock
    or a deterministic timestamp.  The default uses Unix time, which also
    makes the event payload convenient to persist or send to a UI.
    """

    _FLASH_PHRASES = (
        "没闪",
        "没闪了",
        "没闪现",
        "没闪现了",
        "没有闪现",
        "没有闪现了",
        "闪现没了",
        "交了闪现",
        "交闪了",
        "使用闪现",
        "用了闪现",
        "用闪现",
        "闪现了",
    )
    _ULTIMATE_PHRASES = (
        "没大",
        "没大了",
        "没大招",
        "没大招了",
        "没有大",
        "没有大了",
        "没有大招",
        "没有大招了",
        "大没了",
        "大招没了",
        "大招用了",
        "大招交了",
        "交大",
        "交大了",
        "交了大招",
        "用了大招",
        "放大",
        "开大",
        "放了大招",
        "放大招了",
        "放大招",
        "放大了",
        "开了大招",
        "开大招了",
        "开大招",
        "开大了",
    )

    def __init__(
        self,
        knowledge_path: str | Path | None = None,
        *,
        wake_word: str | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.knowledge_path = Path(knowledge_path or ROOT / "resources" / "knowledge" / "voice_assistant_knowledge.json")
        payload = json.loads(self.knowledge_path.read_text(encoding="utf-8"))
        defaults = payload.get("voice_defaults") or {}
        self.wake_word = (wake_word or defaults.get("wake_word") or "小象助手").strip()
        if not self.wake_word:
            raise ValueError("wake_word 不能为空")
        self._now = now or time.time
        self._heroes: dict[str, dict] = {}
        self._aliases: list[tuple[str, str]] = []
        for item in payload.get("heroes") or []:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            self._heroes[name] = item
            aliases = item.get("aliases") or [name]
            for alias in {name, *(str(value).strip() for value in aliases if str(value).strip())}:
                self._aliases.append((alias, name))
        alias_path = self.knowledge_path.with_name("hero_voice_aliases.json")
        if alias_path.is_file():
            alias_data = json.loads(alias_path.read_text(encoding="utf-8"))
            for hero, entry in alias_data["heroes"].items():
                if hero in self._heroes:
                    for alias in entry["aliases"] + entry["asr_variants"]:
                        # An official name always identifies its own hero.
                        if alias not in self._heroes or alias == hero:
                            self._aliases.append((alias, hero))
        self._aliases = sorted(set(self._aliases), key=lambda pair: (-len(pair[0]), pair))
        self._ultimate_slots = self._read_ultimate_slots()
        self._flash_cooldown_s = self._read_flash_cooldown(payload)
        self._events: dict[tuple[str, SkillKind], CooldownEvent] = {}

    @staticmethod
    def _read_flash_cooldown(payload: dict) -> float | None:
        value = (payload.get("summoner_skills") or {}).get("闪现", {}).get("cooldown_s")
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) and value > 0 else None

    def _read_ultimate_slots(self) -> dict[str, int]:
        # Older generated voice knowledge does not yet include a slot.  The
        # source catalog is kept beside it and identifies four-skill heroes.
        catalog_path = self.knowledge_path.with_name("skill_catalog.json")
        if not catalog_path.is_file():
            return {}
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        return {
            hero["hero"]: skill["slot"]
            for hero in catalog.get("heroes", [])
            for skill in hero.get("skills", [])
            if skill.get("is_ultimate") and skill.get("slot") in (1, 2, 3, 4)
        }

    @staticmethod
    def _clean(text: str) -> str:
        # ASR often emits Chinese punctuation and spaces around names.
        return re.sub(r"[\s，,。.!！?？、:：;；]+", "", str(text or "")).strip()

    def parse(self, text: str) -> ParsedVoiceCommand | None:
        """Parse one ASR result; return ``None`` when it is not a command.

        A command must contain the configured wake word.  Query-style phrases
        are intentionally ignored in this first loop because they do not
        represent a new release event.
        """

        groups, kind = self._candidate_groups(text)
        if len(groups) != 1 or len(groups[0]) != 1:
            return None
        return ParsedVoiceCommand(hero=groups[0][0], skill_kind=kind,
                                  raw_text=str(text).strip(), wake_word=self.wake_word)

    def parse_command(self, text: str) -> dict | None:
        """Return the small payload expected by an HTTP/UI command endpoint.

        ``slot`` uses the source catalog's actual ultimate slot, and 5 for a
        summoner spell (flash).  The payload intentionally does
        not create state; call :meth:`process` when the event should start a
        timer.
        """

        command = self.parse(text)
        if command is None:
            return None
        return self._command_payload(command)

    def _command_payload(self, command: ParsedVoiceCommand) -> dict:
        skill_name, cooldown_s = self._cooldown_for(command)
        if cooldown_s is None:
            message = f"已识别{command.hero}{skill_name}，冷却时间未知"
        else:
            message = f"已识别{command.hero}{skill_name}，默认冷却{_format_seconds(cooldown_s)}"
        return {
            "hero": command.hero,
            "skill_type": command.skill_kind,
            "skill_name": skill_name,
            "cooldown": cooldown_s,
            "cooldown_s": cooldown_s,
            "slot": self._ultimate_slots.get(command.hero, 3) if command.skill_kind == "ultimate" else 5,
            "message": message,
            "raw_text": command.raw_text,
        }

    def _candidate_groups(self, text: str) -> tuple[list[tuple[str, ...]], SkillKind | None]:
        raw = str(text or "").strip()
        if self.wake_word not in raw or re.search(r"[?？]", raw):
            return [], None
        body = self._clean(raw.split(self.wake_word, 1)[1])
        actions = sorted((*self._FLASH_PHRASES, *self._ULTIMATE_PHRASES, "大招"), key=len, reverse=True)
        modifier = r"(?:(?:也|又)(?:已经|现在|刚刚|刚才|刚)?|(?:已经|现在|刚刚|刚才|刚)(?:也|又)?)?"
        pattern = r"(?:敌方|对面|对方)?(.+?)" + modifier + "(" + "|".join(map(re.escape, actions)) + ")"
        match = re.fullmatch(pattern, body)
        if not match:
            return [], None
        names, action = match.groups()
        kind = "flash" if action in self._FLASH_PHRASES else "ultimate"
        group = re.fullmatch(r"(.+?)(?:都|一起)", names)
        if not group:
            if names in self._heroes:
                return [(names,)], kind
            return [(hero,) for hero in sorted({hero for alias, hero in self._aliases if alias == names})], kind
        names = group.group(1)
        solutions = set()
        # Enumerate whole-name matches, never substrings from an uncertain sentence.
        # Nine results suffice to detect the UI limit of eight without exponential work.
        def split_names(rest, heroes):
            if not rest:
                unique = tuple(dict.fromkeys(heroes))
                if len(unique) >= 2:
                    solutions.add(unique)
                return
            if len(heroes) >= 5 or len(solutions) >= 9:
                return
            official = [name for name in self._heroes if rest.startswith(name)]
            choices = [(max(official, key=len), max(official, key=len))] if official else self._aliases
            for alias, hero in choices:
                if rest.startswith(alias):
                    tail = rest[len(alias):]
                    if tail.startswith(("和", "与", "跟")):
                        tail = tail[1:]
                        if not tail:
                            continue
                    split_names(tail, heroes + [hero])
                    if len(solutions) >= 9:
                        break
        split_names(names, [])
        return sorted(solutions), kind

    def resolve_commands(self, text: str) -> dict:
        """Resolve names without recording; ambiguous groups require user selection."""
        groups, kind = self._candidate_groups(text)
        if not groups:
            return {"status": "invalid", "commands": [], "candidates": []}
        if len(groups) > 1:
            candidates = []
            if len(groups) <= 8:
                action = "闪现了" if kind == "flash" else "放大招了"
                for heroes in groups:
                    names = "、".join(heroes)
                    command_text = f"{self.wake_word}，{names}{'都' if len(heroes) > 1 else ''}{action}"
                    candidates.append({"heroes": list(heroes), "text": command_text,
                                       "label": names + (" · 闪现" if kind == "flash" else " · 大招")})
            return {"status": "needs_confirmation", "commands": [], "candidates": candidates}
        commands = [self._command_payload(ParsedVoiceCommand(
            hero=hero, skill_kind=kind, raw_text=str(text).strip(), wake_word=self.wake_word,
        )) for hero in groups[0]]
        return {"status": "ready", "commands": commands, "candidates": []}

    def parse_commands(self, text: str) -> list[dict]:
        return self.resolve_commands(text)["commands"]

    def _cooldown_for(self, command: ParsedVoiceCommand) -> tuple[str, float | None]:
        if command.skill_kind == "flash":
            return "闪现", self._flash_cooldown_s
        ultimate = (self._heroes.get(command.hero) or {}).get("ultimate") or {}
        skill_name = str(ultimate.get("name") or "大招")
        value = ultimate.get("default_cooldown_s")
        # A dynamic or review-required value is recorded without pretending
        # that its duration is known.
        if ultimate.get("cooldown_kind") == "dynamic" or ultimate.get("cooldown_review_required"):
            return skill_name, None
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = None
        return skill_name, value if value is not None and math.isfinite(value) and value > 0 else None

    def record(self, command: ParsedVoiceCommand, *, recorded_at: float | None = None) -> CooldownEvent:
        """Record or replace the timer for a hero and skill kind."""

        timestamp = self._now() if recorded_at is None else float(recorded_at)
        if command.hero not in self._heroes or command.skill_kind not in ("ultimate", "flash"):
            raise ValueError("未知英雄或不支持的技能类型")
        if not math.isfinite(timestamp):
            raise ValueError("记录时间必须是有效数字")
        skill_name, cooldown_s = self._cooldown_for(command)
        ready_at = timestamp + max(0.0, cooldown_s - REPORT_DELAY_S) if cooldown_s is not None else None
        event = CooldownEvent(
            id=uuid4().hex,
            hero=command.hero,
            skill_kind=command.skill_kind,
            skill_name=skill_name,
            cooldown_s=cooldown_s,
            recorded_at=timestamp,
            ready_at=ready_at,
            raw_text=command.raw_text,
        )
        self._events[(command.hero, command.skill_kind)] = event
        return event

    def process(self, text: str, *, recorded_at: float | None = None) -> dict:
        """Parse and record a text command into a JSON-friendly response."""

        command = self.parse(text)
        if command is None:
            return {"ok": False, "message": "未识别到有效指令", "timers": self.snapshot()}
        event = self.record(command, recorded_at=recorded_at)
        if event.cooldown_s is None:
            message = f"已记录{event.hero}{event.skill_name}，冷却时间未知"
        else:
            message = f"已记录{event.hero}{event.skill_name}，开始{_format_seconds(max(0.0, event.cooldown_s - REPORT_DELAY_S))}预计倒计时（已补偿3秒报时延迟）"
        return {
            "ok": True,
            "message": message,
            "command": asdict(command),
            "timer": self._event_snapshot(event),
            "timers": self.snapshot(),
        }

    def _event_snapshot(self, event: CooldownEvent, *, at: float | None = None) -> dict:
        timestamp = self._now() if at is None else float(at)
        if event.ready_at is None:
            remaining = None
            status = "unknown"
        else:
            remaining = max(0.0, event.ready_at - timestamp)
            status = "ready" if remaining <= 0 else "cooling"
        return {
            **asdict(event),
            "remaining_s": remaining,
            "status": status,
        }

    def snapshot(self, at: float | None = None) -> list[dict]:
        """Return active events with current remaining seconds for the overlay."""

        timestamp = self._now() if at is None else float(at)
        rows = [self._event_snapshot(event, at=timestamp) for event in self._events.values()]
        return sorted(rows, key=lambda row: (row["status"] == "unknown", row["remaining_s"] is None, row["remaining_s"] or 0))

    def clear(self, hero: str | None = None, skill_kind: SkillKind | None = None) -> None:
        """Clear all events, or one hero/skill pair."""

        if hero is None and skill_kind is None:
            self._events.clear()
            return
        for key in list(self._events):
            if (hero is None or key[0] == hero) and (skill_kind is None or key[1] == skill_kind):
                del self._events[key]

    @property
    def known_heroes(self) -> tuple[str, ...]:
        """Canonical hero names available to the parser."""

        return tuple(self._heroes)


def parse_command(text: str, knowledge_path: str | Path | None = None) -> dict | None:
    """Stateless convenience wrapper for an API route or a quick prototype."""

    return VoiceCooldownAssistant(knowledge_path=knowledge_path).parse_command(text)


def _format_seconds(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value)}秒"
    return f"{value:g}秒"
