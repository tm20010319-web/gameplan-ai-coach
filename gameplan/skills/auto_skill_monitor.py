"""In-memory, evidence-gated enemy ultimate / summoner estimates for one match."""
import json
import math
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4
from collections import OrderedDict

from gameplan.core.models import EnemyCastObservation, HeroLevelObservation, SummonerObservation
from gameplan.skills.monitor_policy import cooldown_values
from gameplan.skills.cooldown_estimates import SUMMONERS, SUMMONER_DATA, REFERENCES, canonical_summoner, ultimate_values


@dataclass
class EnemySkillEvent:
    hero: str
    skill: str
    slot: int
    captured_at: float
    confidence: float
    evidence: str
    cooldown_s: float | None
    id: str
    is_ultimate: bool
    cooldown_basis: str = "基础冷却估算，未计装备减冷却或刷新"
    cooldown_values_s: tuple = ()
    cooldown_source: str = "resources/knowledge/skill_catalog.json"
    cast_window_start: float | None = None
    cast_window_end: float | None = None
    timing_basis: str = 'cast_start'
    special_base_estimate: bool = False
    charge_recovery_estimate: bool = False
    reference_estimate: bool = False


class EventTracker:
    def __init__(self, cooldowns=None, dedupe_window=8):
        self.match_epoch = uuid4().hex
        self.dedupe_window = dedupe_window
        self.events = []
        self.suspicions = {}
        from gameplan.skills.suspected_cooldowns import SuspectedCooldowns
        self.estimates = SuspectedCooldowns()
        self.last_seen = time.time()
        self.phase = None
        self.allies = []
        self.enemies = []
        self.levels = {}
        self.summoners = {}
        self.pending_summoners = {}
        self.identities = []
        self.scene_cache = OrderedDict()
        self.track_memory = {}
        from gameplan.vision.loading_evidence import LoadingMemory
        self.loading_memory = LoadingMemory()
        self.summoner_overrides = {}
        self.tracking_enemies = []
        self.player = None
        self.player_verified = False
        self.roster_verified = False
        self.roster_source = None
        self.last_skill_check = None
        self.rejections = {}
        data = json.loads((Path(__file__).resolve().parents[2] / "resources" / "knowledge" / "skill_catalog.json").read_text(encoding="utf-8"))
        self.catalog = {h["hero"]: h.get("skills", []) for h in data.get("heroes", [])}

    def reject(self, reason):
        self.rejections[reason] = self.rejections.get(reason, 0) + 1

    def ingest_suspicions(self, observations, *, enemies, allies, frame_times, now):
        """Unconfirmed evidence expires; it never reserves the confirmed recast guard."""
        for raw in observations or []:
            if not isinstance(raw, dict):
                continue
            hero, skill, index = raw.get('hero'), raw.get('skill'), raw.get('frame_index')
            confidence, evidence = raw.get('confidence'), raw.get('evidence')
            if (hero not in enemies or hero in allies or type(index) is not int or not 0 <= index < len(frame_times)
                    or type(confidence) not in (int, float) or not math.isfinite(confidence)
                    or not .65 <= confidence <= 1 or not isinstance(evidence, str) or not evidence.strip()
                    or any(word in evidence for word in ('图标', '按钮', '技能栏'))):
                continue
            spell = self.summoner_name(hero, skill, raw.get('slot'))
            is_summoner = bool(spell)
            item = None if is_summoner else self.skill_record(hero, skill)
            if not is_summoner and (not item or not item.get('is_ultimate')):
                continue
            skill = spell if is_summoner else item['name']
            stamp = frame_times[index]
            # Vision inference may arrive several sampling intervals late.
            # Keep the tentative cast window long enough to cover the user's
            # accepted delay while still rejecting stale historical frames.
            if now - stamp > 18 or stamp > now:
                continue
            equipped = self.summoner_for(hero)
            # Unknown equipment may be shown as tentative, but a known different
            # summoner is affirmative evidence against a summoner candidate.
            if is_summoner and equipped.get('skill') not in (None, skill):
                continue
            reading = self.level_at(hero, stamp)
            if not is_summoner and reading and reading['level'] < 4:
                continue
            key = (hero, skill)
            confirmed = next((event for event in self.events if (event.hero, event.skill) == key), None)
            if confirmed and abs(stamp-confirmed.captured_at) < self.dedupe_window:
                self.suspicions.pop(key, None)
                continue
            prior = self.suspicions.get(key)
            if prior and prior['last_seen_at'] >= stamp:
                continue
            # Keep one id over a burst of supporting frames to avoid repeated
            # alerts; a negative scan does not itself disprove earlier evidence.
            same = prior and stamp-prior['last_seen_at'] <= 3
            self.suspicions[key] = {
                'id': prior['id'] if same else uuid4().hex, 'hero': hero, 'skill': skill,
                'is_ultimate': not is_summoner, 'status': 'suspected',
                'captured_at': prior['captured_at'] if same else stamp,
                'last_seen_at': stamp, 'expires_at': stamp+8, 'confidence': confidence,
                'evidence': evidence.strip()[:240], 'reason': str(raw.get('reason', 'awaiting_confirmation'))[:80],
            }
            self.estimates.observe(raw, self.suspicions[key], frame_times, item,
                                   self.resolve_cooldown(hero, skill, 5 if is_summoner else item['slot']),
                                   values=self.estimate_values(hero, skill, 5 if is_summoner else item['slot']))
        self.suspicions = {key: value for key, value in self.suspicions.items()
                           if value['expires_at'] > now and key[0] in enemies}

    def estimate_snapshot(self, now=None):
        return self.estimates.snapshot(time.time() if now is None else now, self.events)

    def suspicion_snapshot(self, now=None):
        now = time.time() if now is None else now
        return [dict(value) for value in self.suspicions.values() if value['expires_at'] > now]

    def update_summoners(self, observations, *, enemies, allies, frame_times):
        grouped = {}
        parsed = []
        for raw in observations or []:
            try:
                record = raw if isinstance(raw, SummonerObservation) else SummonerObservation.model_validate(raw)
            except (ValueError, TypeError):
                continue
            if record.hero not in enemies or record.hero in allies or not record.evidence.strip() or record.frame_index >= len(frame_times):
                continue
            record = record.model_copy(update={"skill": {"惩戒": "惩击", "晕眩": "眩晕"}.get(record.skill, record.skill)})
            parsed.append(record)
        if len(frame_times) > 1:
            # Missing/conflicting intermediate images must break a loading
            # streak too; a batch is not three votes from one screenshot.
            for index, timestamp in enumerate(frame_times):
                self.update_summoners([r.model_copy(update={'frame_index':0}) for r in parsed if r.frame_index == index],
                                      enemies=enemies, allies=allies, frame_times=[timestamp])
            return
        # Conflicting labels from one image cannot build a confirmation streak.
        labels = {}
        for record in parsed:
            key = (record.hero, frame_times[record.frame_index])
            labels.setdefault(key, set()).add(record.skill)
        accepted_loading = set()
        for record in sorted(parsed, key=lambda r: frame_times[r.frame_index]):
            timestamp = frame_times[record.frame_index]
            if len(labels[(record.hero, timestamp)]) > 1:
                self.pending_summoners.pop(record.hero, None)
                if record.source == 'loading_icon':
                    continue
            if record.source == 'loading_icon' and record.color_score is not None:
                from gameplan.vision.loading_spells import accepted_scores
                if not accepted_scores(record.confidence, record.template_margin, record.color_score,
                                       record.smoothed_score, record.smoothed_margin):
                    continue
                accepted_loading.add(record.hero)
                pending=self.pending_summoners.get(record.hero)
                if pending and timestamp<=pending['captured_at']:
                    continue
                count=pending.get('count',1)+1 if pending and pending['skill']==record.skill and .1<=timestamp-pending['captured_at']<=15 else 1
                self.pending_summoners[record.hero]={'skill':record.skill,'captured_at':timestamp,'count':count}
                if count<3:
                    continue
            elif record.confidence < .9:
                continue
            skill = {"惩戒": "惩击", "晕眩": "眩晕"}.get(record.skill, record.skill)
            grouped.setdefault(record.hero, []).append({"hero": record.hero, "skill": skill,
                "captured_at": frame_times[record.frame_index], "evidence": record.evidence, "source": record.source})
        for hero, pending in list(self.pending_summoners.items()):
            if hero not in accepted_loading and frame_times and max(frame_times) > pending['captured_at']:
                self.pending_summoners.pop(hero, None)
        for hero, records in grouped.items():
            if hero in self.summoner_overrides:
                continue
            latest = max(records, key=lambda record: record["captured_at"])
            previous = self.summoners.get(hero)
            if previous and latest["captured_at"] < previous["captured_at"]:
                continue
            if len({r["skill"] for r in records}) > 1:
                latest = {**latest, "skill": None}
            elif previous and previous["captured_at"] == latest["captured_at"] and previous["skill"] != latest["skill"]:
                latest = {**latest, "skill": None}
            self.summoners[hero] = latest
            if latest['skill']:
                self.invalidate_summoner(hero, latest['skill'])

    def summoner_states(self, enemies):
        return [{"hero": hero, "skill": self.summoner_for(hero).get("skill"),
                 "status": "confirmed" if self.summoner_for(hero).get("skill") else "confirming" if hero in self.pending_summoners else "unknown",
                 **({"confirmation_count": min(3,self.pending_summoners[hero]['count'])}
                    if hero in self.pending_summoners and not self.summoner_for(hero).get("skill") else {}),
                 **({"source": "manual"} if hero in self.summoner_overrides else {})}
                for hero in enemies]

    def summoner_for(self, hero):
        return self.summoner_overrides.get(hero, self.summoners.get(hero, {}))

    def correct_summoner(self, hero, skill):
        if hero not in self.tracking_enemies:
            raise ValueError("只能更正当前已识别的敌方英雄")
        self.summoners.pop(hero, None)
        self.pending_summoners.pop(hero, None)
        self.summoner_overrides.pop(hero, None)
        if skill:
            self.summoner_overrides[hero] = {"hero": hero, "skill": skill, "captured_at": time.time()}
        self.invalidate_summoner(hero, skill)

    def invalidate_summoner(self, hero, skill):
        def keep(value):
            return value['hero'] != hero or value['is_ultimate'] or value['skill'] == skill
        self.events = [e for e in self.events if keep(asdict(e))]
        self.suspicions = {k: v for k, v in self.suspicions.items() if keep(v)}
        self.estimates.records = {k: v for k, v in self.estimates.records.items() if keep(v)}

    def summoner_name(self, hero, skill, slot=None):
        # Explicit slot/prefix resolves collisions such as 牛魔's ordinary 狂暴.
        if slot is not None and slot != 5:
            return None
        name = canonical_summoner(skill)
        if name and (slot == 5 or skill.startswith('召唤师技能·') or not self.skill_record(hero, skill)):
            return name
        return None

    def recast_guard(self, hero, skill, slot):
        spell = self.summoner_name(hero, skill, slot)
        if spell:
            return float(SUMMONERS[spell]['base_cooldown_s'])
        item = self.skill_record(hero, skill, slot) or {}
        values = [v for v in item.get("base_cooldowns_s", []) if isinstance(v, (int, float)) and math.isfinite(v) and v > 0]
        # Use the shortest rank and 40% CDR as a conservative recast boundary,
        # rather than blocking the full longest-rank estimated timer.
        return max(self.dedupe_window, min(values) * .6 if values else 20)

    def skill_record(self, hero, skill, slot=None):
        skills = self.catalog.get(hero, [])
        exact = next((item for item in skills if item.get("name") == skill), None)
        if exact:
            return exact
        if skill in ("大招", "终极技能", "终极技能·大招"):
            return next((item for item in skills if item.get("is_ultimate")), None)
        return None

    def charge_recovery_values(self, hero, item):
        """Read the explicitly requested Ma Chao recharge estimate from the catalog.

        Its zero cast cooldown is not its per-charge recovery interval. Keep
        the source intact and do not infer remaining charges from a cast.
        """
        if hero != '马超' or not item or not item.get('is_ultimate') or item.get('cooldown_review_required'):
            return []
        match = re.search(r'每(\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?){2})秒可准备1次', item.get('description', ''))
        if not match:
            return []
        values = [float(value) for value in match[1].split('/')]
        return values if all(0 < value <= 3600 for value in values) else []

    def estimate_values(self, hero, skill, slot):
        spell = self.summoner_name(hero, skill, slot)
        if spell:
            return [float(SUMMONERS[spell]['base_cooldown_s'])]
        item = self.skill_record(hero, skill, slot)
        return self.charge_recovery_values(hero, item) or ultimate_values(hero, item)

    def resolve_cooldown(self, hero, skill, slot):
        values = self.estimate_values(hero, skill, slot)
        return max(values) if values else None

    def update_levels(self, observations, *, enemies, allies, frame_times):
        for raw in observations or []:
            try:
                record = raw if isinstance(raw, HeroLevelObservation) else HeroLevelObservation.model_validate(raw)
            except (ValueError, TypeError):
                continue
            if record.hero not in enemies or record.hero in allies or record.confidence < .9 or record.frame_index >= len(frame_times):
                continue
            stamp = frame_times[record.frame_index]
            readings = self.levels.setdefault(record.hero, [])
            # Keep temporal evidence, so reaching level four in a later frame
            # cannot validate an alleged cast in an earlier frame.
            if not any(r["captured_at"] == stamp and r["level"] == record.level for r in readings):
                readings.append({"level": record.level, "captured_at": stamp})
                readings.sort(key=lambda item: item["captured_at"])
                self.levels[record.hero] = readings[-24:]
        # A clear low-level reading invalidates a contradictory earlier timer.
        for hero, readings in self.levels.items():
            if readings[-1]["level"] < 4 and frame_times and frame_times[-1]-readings[-1]['captured_at'] <= 10:
                self.estimates.retract(hero,"新等级证据表明大招未解锁，已撤销估算",readings[-1]["captured_at"],ultimate_only=True)
                self.events = [e for e in self.events if e.hero != hero or not e.is_ultimate]
                self.suspicions = {k: v for k, v in self.suspicions.items() if k[0] != hero or not v['is_ultimate']}

    def level_at(self, hero, at):
        readings = [r for r in self.levels.get(hero, []) if r["captured_at"] <= at]
        if not readings:
            return None
        latest = readings[-1]
        same_time = {r["level"] for r in readings if r["captured_at"] == latest["captured_at"]}
        if len(same_time) != 1:
            return None
        # Unlocked is persistent within a match. A low-level sighting expires:
        # an unseen enemy may have levelled up since that image was captured.
        if latest["level"] < 4 and at - latest["captured_at"] > 10:
            return None
        return latest

    def ultimate_states(self, enemies, now=None):
        now = time.time() if now is None else now
        result = []
        for hero in enemies:
            reading = self.level_at(hero, now)
            result.append({"hero": hero, "level": reading["level"] if reading else None,
                           "level_captured_at": reading["captured_at"] if reading else None,
                           "unlock_level": 4,
                           "status": "level_unknown" if not reading else "locked" if reading["level"] < 4 else "unlocked"})
        return result

    def ingest(self, observations, captured_at=None, *, enemies=(), allies=(), frame_times=None, hero_levels=(), summoner_skills=()):
        now = time.time() if captured_at is None else captured_at
        times = frame_times or [now]
        self.rejections = {}
        self.update_summoners(summoner_skills, enemies=enemies, allies=allies, frame_times=times)
        self.update_levels(hero_levels, enemies=enemies, allies=allies, frame_times=times)
        added = []
        for raw in observations or []:
            try:
                record = raw if isinstance(raw, EnemyCastObservation) else EnemyCastObservation.model_validate(raw)
            except (ValueError, TypeError):
                continue
            keyframe = record.event_type == 'keyframe'
            if not record.used or record.confidence < .9 or record.event_type not in ("cast_start", "keyframe") or not record.evidence.strip():
                self.reject("uncertain_cast")
                continue
            if any(word in record.evidence for word in ("图标", "按钮", "技能栏")):
                self.reject("icon_is_not_cast_evidence")
                continue
            hero = record.hero.strip()
            if hero not in enemies or hero in allies or hero not in self.catalog:
                self.reject("hero_unconfirmed")
                continue
            spell = self.summoner_name(hero, record.skill, record.slot)
            is_summoner = bool(spell)
            item = None if is_summoner else self.skill_record(hero, record.skill, record.slot)
            if not is_summoner and (not item or not item.get("is_ultimate")):
                self.reject("not_ultimate_or_summoner")
                continue
            skill, slot = (spell, 5) if is_summoner else (item["name"], item["slot"])
            index = record.frame_index
            # A keyframe estimate starts at the observed effect, including F0.
            # Legacy cast-start evidence still needs a preceding source frame.
            if index is None or not (0 <= index < len(times) if keyframe else 0 < index < len(times)):
                self.reject("missing_before_frame")
                continue
            cast_at = times[index]
            window_start = window_end = None
            if record.onset_start_index is not None:
                if record.onset_end_index >= len(times):
                    self.reject('invalid_onset_window')
                    continue
                window_start, window_end = times[record.onset_start_index], times[record.onset_end_index]
                # Frame labels from the VLM are approximate. The confirmed
                # sequence bounds its onset; use the late bound for the point
                # estimate, and expose the full interval in remaining_range_s.
                cast_at = window_end
            if is_summoner:
                equipped = self.summoner_for(hero)
                if ((keyframe and equipped.get('skill') not in (None, skill)) or
                    (not keyframe and (not equipped or equipped["skill"] != skill or equipped["captured_at"] > (window_start if window_start is not None else cast_at)))):
                    self.reject("flash_not_equipped_or_unknown" if skill == '闪现' else "summoner_not_equipped_or_unknown")
                    continue
            if not is_summoner:
                reading = self.level_at(hero, window_start if window_start is not None else cast_at)
                current = self.level_at(hero, now)
                if ((keyframe and ((reading and reading['level'] < 4) or (current and current['level'] < 4))) or
                    (not keyframe and (not reading or reading["level"] < 4 or not current or current["level"] < 4))):
                    self.reject("ultimate_level_unconfirmed_or_locked")
                    continue
            previous = next((e for e in reversed(self.events) if e.hero == hero and e.skill == skill), None)
            cooldown = self.resolve_cooldown(hero, skill, slot)
            guard = self.recast_guard(hero, skill, slot)
            if keyframe or (previous and previous.timing_basis == 'first_visible_effect'):
                guard = cooldown or max(cooldown_values(item or {}), default=20)
            if previous and cast_at - previous.captured_at < guard:
                self.reject("duplicate_or_early_recast")
                continue
            event = EnemySkillEvent(hero, skill, slot, cast_at, record.confidence,
                                    record.evidence.strip(), cooldown, uuid4().hex, not is_summoner)
            event.cast_window_start, event.cast_window_end = window_start, window_end
            event.timing_basis = 'first_visible_effect' if keyframe else 'cast_start'
            event.special_base_estimate = not is_summoner and bool(item.get('special_mechanic')) and cooldown is not None
            event.reference_estimate = not is_summoner and hero in REFERENCES and cooldown is not None
            recovery = self.charge_recovery_values(hero, item) if not is_summoner else []
            event.charge_recovery_estimate = bool(recovery) and cooldown is not None
            values = self.estimate_values(hero, skill, slot)
            event.cooldown_values_s = tuple(values)
            event.cooldown_source = SUMMONER_DATA['source_url'] if is_summoner else REFERENCES[hero]['source_url'] if event.reference_estimate else 'resources/knowledge/skill_catalog.json'
            if is_summoner:
                event.cooldown_basis = f'{skill}基础冷却{cooldown:g}秒估算，未计召唤师技能冷却调整、装备升级或条件返还'
            elif cooldown is None:
                event.cooldown_basis = '用户知识库：特殊机制、动态冷却或资料待核对，剩余冷却未知'
            elif len(set(values)) > 1:
                event.cooldown_basis = '用户知识库：大招等级未确认，显示各等级基础冷却范围；未计减冷却或刷新'
            else:
                event.cooldown_basis = '用户知识库基础冷却估算，未计减冷却或刷新'
            if window_start is not None and window_end > window_start:
                event.cooldown_basis += f'；释放时刻在{window_end-window_start:.1f}秒观察区间内'
            if keyframe:
                event.cooldown_basis += '；按首次识别到特效的画面时间起算，可能晚于实际释放'
            if event.special_base_estimate:
                event.cooldown_basis += ('；仅基础估算，未计附身持续时间、脱离返还及装备减冷却' if hero == '瑶'
                                         else '；特殊机制仅基础估算，未计持续时间、刷新、返还或存储次数')
            if event.reference_estimate:
                event.cooldown_basis += '；' + REFERENCES[hero]['note']
            if event.charge_recovery_estimate:
                ranks = '/'.join(f'{value:g}' for value in recovery)
                event.cooldown_basis = (f'知识库技能说明：每{ranks}秒恢复一次；按首次识别估算充能恢复，'
                                        '未计被动及装备减冷却；剩余次数未知，可能仍可释放')
            self.events = [e for e in self.events if (e.hero, e.skill) != (hero, skill)]
            self.events.append(event)
            self.suspicions.pop((hero, skill), None)
            added.append(event)
        self.events = self.events[-10:]
        return added

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        result = []
        for event in self.events:
            remaining = None if event.cooldown_s is None else max(0, event.cooldown_s - max(0, now - event.captured_at))
            elapsed = max(0, now-event.captured_at)
            values = event.cooldown_values_s
            earliest_elapsed=max(0,now-(event.cast_window_start if event.cast_window_start is not None else event.captured_at))
            bounds = ([max(0, min(values)-earliest_elapsed), max(0, max(values)-elapsed)]
                      if remaining is not None and values else None)
            result.append({**asdict(event), "remaining_s": remaining,
                           "remaining_range_s": bounds,
                           "status": "cooldown_unknown" if remaining is None else "ready" if remaining == 0 else "cooling"})
        return result
