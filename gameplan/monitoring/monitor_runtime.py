"""Shared, non-persistent observation pipeline used by both local applications."""
import asyncio
import time
import os
from uuid import uuid4
from collections import OrderedDict
import io
import re
import base64
import numpy as np
from PIL import Image
import gameplan.vision.bp_portraits as bp_portraits
from gameplan.skills.auto_skill_monitor import EventTracker
from gameplan.skills.monitor_policy import TARGET_LATENCY_S

trackers = OrderedDict()
reset_versions = OrderedDict()


def retain_roster(previous, current):
    """Keep match identities/order through partial reads; accept complete corrections."""
    if len(current) >= 5 and set(current) != set(previous):
        return current[:5]
    return list(dict.fromkeys([*previous, *current]))[:5]


def reset_match(match_id):
    """Also invalidate inference already running when the user clears a match."""
    trackers.pop(match_id, None)
    bp_sessions.pop(match_id, None)
    lane_sessions.pop(match_id, None)
    reset_versions[match_id] = uuid4().hex
    reset_versions.move_to_end(match_id)
    while len(reset_versions) > 256:
        reset_versions.popitem(last=False)

from fastapi import HTTPException
from gameplan.ai.advisor import advisor, KNOWN_HEROES
from gameplan.ai.integrations import image_bytes, vision
from gameplan.core.models import ScreenObservation

vision_gate = asyncio.Lock()
bp_sessions = OrderedDict()
lane_sessions = OrderedDict()
from gameplan.tactics.lane_guidance import read_lane_banner, LANE_ADVICE
from gameplan.knowledge.skill_knowledge import lookup_many as lookup_skill_heroes



def detect_lane_from_frame(prepared):
    result = read_lane_banner(prepared)
    return result["lane"] if result else None


def update_lane_context(match_id, phase, evidence, captured):
    previous = lane_sessions.get(match_id)
    if previous and (not 0 <= captured - previous["last_seen"] <= 180 or
                     (phase == "bp" and previous["phase"] != "bp")):
        lane_sessions.pop(match_id, None)
        previous = None
    if phase == "result":
        lane_sessions.pop(match_id, None)
        return None
    if phase not in ("bp", "loading", "in_game"):
        return None
    if evidence:
        previous = {**evidence, "captured_at": captured}
    if previous:
        previous.update(last_seen=captured, phase=phase)
        lane_sessions[match_id] = previous
        lane_sessions.move_to_end(match_id)
        while len(lane_sessions) > 64:
            lane_sessions.popitem(last=False)
        return {**previous, "advice": LANE_ADVICE[previous["lane"]], "from_current_frame": bool(evidence)}
    return None


def portrait_observation(req, prepared):
    result = bp_portraits.match(prepared, allow_sparse=True)
    captured = req.captured_at or time.time()
    previous = bp_sessions.get(req.match_id)
    if previous and not 0 <= captured - previous['last_seen'] <= 180:
        bp_sessions.pop(req.match_id, None)
        previous = None
    if not result:
        if previous:
            previous['pending'] = None
        return None, previous
    if previous and previous.get('phase') != 'bp':
        previous = None
    result = bp_portraits.supplement_missing(prepared, result, req.model)
    pending, slots = bp_portraits.confirm(previous.get('pending') if previous else None, result, captured)
    allies = [s['hero'] for s in slots if s['side'] == 'left']
    enemies = [s['hero'] for s in slots if s['side'] == 'right']
    player = result['player_hero'] if result['player_hero'] in allies else None
    lane = previous.get('lane') if previous else None
    if not lane:
        lane = detect_lane_from_frame(prepared)
    confirmed = {(s['side'],s['row']):s['hero'] for s in slots}
    slot_evidence = [{"side":s["side"],"row":s["row"],
                      "hero":confirmed.get((s["side"],s["row"])),
                      "status":"confirmed" if (s["side"],s["row"]) in confirmed else "unconfirmed"}
                     for s in result["slots"]]
    session = {'slots':slot_evidence, 'pending': pending, 'last_seen': captured, 'phase': 'bp', 'allies': allies, 'enemies': enemies,
               'player': player, 'lane': lane, 'source': result['source']}
    bp_sessions[req.match_id] = session
    bp_sessions.move_to_end(req.match_id)
    while len(bp_sessions) > 64:
        bp_sessions.popitem(last=False)
    note = f"头像匹配：{len(result['left'])+len(result['right'])}/10 个候选；连续两帧一致：{len(slots)}/10。"
    if lane:
        note += f"我的分路：{lane}。"
    detected = {'phase': 'bp', 'ally_roster': allies, 'enemy_roster': enemies, 'player_hero': player, 'note': note}
    return detected, session


async def observe(req):
    if vision_gate.locked():
        raise HTTPException(429, "模型正在处理上一帧，请等待后发送最新画面。")
    started = time.time()
    scene = None
    buffered_scenes = []
    anchors = []
    local_bindings = None
    detector_status = "legacy"
    activity = []
    scan = {}
    suspicion_candidates = []
    version = reset_versions.get(req.match_id)
    for match_id, tracker in list(trackers.items()):
        if started - tracker.last_seen > 1800:
            trackers.pop(match_id, None)
    try:
        raw, prepared = await asyncio.to_thread(image_bytes, req)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if vision_gate.locked():
        raise HTTPException(429, "模型正在处理上一帧，请等待后发送最新画面。")
    try:
        async with vision_gate:
            # Explicit exhaustive replay always sends every frame to Qwen,
            # even when local roster matching could otherwise skip inference.
            full_review = None
            if req.all_frames:
                full_review = await asyncio.to_thread(vision, req.model_copy(update={"focus": "skills"}), "observe", prepared)
            previous = trackers.get(req.match_id)
            scene_cache = previous.scene_cache if previous else OrderedDict()
            from gameplan.skills.combat_evidence import read_scene, merge_bindings
            if (previous and (previous.identities or previous.enemies) and req.focus == 'skills'
                    and 1 <= len(req.recent_frames) <= 7):
                # Every submitted frame needs its own actor identity. A name in
                # a neighboring frame cannot authorize a single-frame cast.
                from gameplan.skills.combat_evidence import read_scenes
                samples = [*req.recent_frames, req]
                anchors = []
                for index in range(len(samples)):
                    sample = samples[index]
                    if sample is req:
                        anchors.append(prepared if req.roi else raw)
                    else:
                        earlier_raw, earlier_prepared = image_bytes(req.model_copy(update={
                            'image_base64': sample.image_base64, 'recent_frames': []}))
                        anchors.append(earlier_prepared if req.roi else earlier_raw)
                buffered_scenes = await asyncio.to_thread(read_scenes, anchors, KNOWN_HEROES, previous.identities,
                                                         cache=scene_cache)
            scene = await asyncio.to_thread(read_scene, prepared if req.roi else raw, KNOWN_HEROES,
                                            previous.identities if previous else [],cache=scene_cache)
            from gameplan.vision.loading_evidence import LoadingMemory
            loading_memory = previous.loading_memory if previous else LoadingMemory()
            if scene and scene.get('phase') == 'loading' and any('slot' in c for c in scene.get('cards', [])):
                if previous and previous.phase == 'in_game':
                    loading_memory = LoadingMemory()
                    previous = None  # A new loading layout starts a new match even before side confirmation.
                from gameplan.skills.combat_evidence import read_scenes
                samples = [*req.recent_frames[-4:], req]
                pixels = []
                for sample in samples:
                    a, b = image_bytes(req.model_copy(update={'image_base64': sample.image_base64, 'recent_frames': []}))
                    pixels.append(b if req.roi else a)
                observations = await asyncio.to_thread(read_scenes, pixels, KNOWN_HEROES,
                    previous.identities if previous else [], cache=scene_cache)
                bp_known = bp_sessions.get(req.match_id) or {}
                for sample, observed in zip(samples, observations):
                    scene_confirmed = loading_memory.observe(observed, sample.captured_at or started,
                        allies=previous.allies if previous and previous.roster_verified else bp_known.get('allies', []),
                        enemies=previous.enemies if previous and previous.roster_verified else bp_known.get('enemies', []),
                        player=previous.player if previous and previous.player_verified else bp_known.get('player'))
                scene = {**scene, **scene_confirmed}
            elif scene and loading_memory.cards:
                resolved = loading_memory.observe(scene, req.captured_at or started,
                    allies=scene.get('ally_roster') or (previous.allies if previous and previous.roster_verified else []),
                    enemies=scene.get('enemy_roster') or (previous.enemies if previous and previous.roster_verified else []),
                    player=previous.player if previous and previous.player_verified else None)
                if resolved['side_known']:
                    scene = {**scene, 'bindings': merge_bindings(resolved['bindings'], scene['bindings']),
                             'side_known': True,
                             **{side: list(dict.fromkeys(resolved[side]+scene.get(side, [])))[:5]
                                for side in ('ally_roster', 'enemy_roster')}}
            from gameplan.monitoring.skill_diagnostics import record
            record('latest_scene', scene)
            # Loading equipment is useful in every observation mode. Keep full
            # gameplay/self-skill inference unchanged outside skills mode.
            if req.focus != "skills" and scene and scene.get('phase') != 'loading':
                scene = None
            # Template matching benefits from the original PNG/JPEG pixels;
            # keep the normalized JPEG for OCR and vision-model requests.
            if scene:
                # A locally verified match HUD/scoreboard needs no BP portrait scan.
                detected, bp = None, bp_sessions.get(req.match_id)
                if bp and not 0 <= (req.captured_at or started) - bp["last_seen"] <= 180:
                    bp_sessions.pop(req.match_id, None)
                    bp = None
            else:
                detected, bp = await asyncio.to_thread(portrait_observation, req, raw)
            # Local readers have already converted screen positions to own/enemy
            # teams. A stale client perspective must not swap them a second time.
            canonical_teams = bool((previous and previous.roster_verified) or bp or
                                   (scene and (scene["panel"] or scene.get("side_known"))))
            perspective_side = "a" if canonical_teams else req.perspective_side
            if detected is not None:
                model = 'official-portrait-matcher'
            else:
                roster_context = {"ally_roster": bp['allies'], "enemy_roster": bp['enemies']} if bp else (
                    {"ally_roster": previous.allies, "enemy_roster": previous.enemies} if previous else None)
                if scene and (scene["panel"] or scene.get("phase") == "loading" or roster_context):
                    local_bindings = merge_bindings(previous.identities if previous else [], scene["bindings"])
                    roster_frame = scene["panel"] or scene.get("phase") == "loading"
                    detected = {"phase": scene.get("phase", "in_game"), "player_hero": previous.player if previous and scene.get("phase") != "loading" else None, "enemy_skill_events": [],
                                "ally_roster": scene["ally_roster"] if roster_frame else roster_context["ally_roster"],
                                "enemy_roster": scene["enemy_roster"] if roster_frame else roster_context["enemy_roster"],
                                "hero_levels": [{**level, "frame_index": len(req.recent_frames)} for level in scene["hero_levels"]]}
                    if previous and previous.roster_verified and scene.get('phase') != 'loading':
                        detected['ally_roster'] = retain_roster(previous.allies, detected['ally_roster'])
                        detected['enemy_roster'] = retain_roster(previous.enemies, detected['enemy_roster'])
                    model = "local-scoreboard-ocr"
                    detector_status = "scoreboard" if scene["panel"] else "waiting_identity"
                    if scene.get("phase") == "loading":
                        detector_status = "loading_identity"
                    elif not scene["panel"] or req.recent_frames:
                        from gameplan.skills.keyframe_casts import detect
                        enemies = detected["ally_roster"] if perspective_side == "b" else detected["enemy_roster"]
                        if (scene['panel'] and previous and previous.roster_verified
                                and set(enemies).issubset(previous.enemies)):
                            enemies = previous.enemies[:]
                        # Reserve the user's end-to-end target for both the
                        # retained source interval and OCR, not just inference.
                        recent_times = [s.captured_at for s in [*req.recent_frames, req]
                                        if s.captured_at and req.captured_at
                                        and 0 <= req.captured_at-s.captured_at <= 8]
                        source_span = req.captured_at-min(recent_times) if recent_times else 0
                        review_budget = max(.1, min(10, TARGET_LATENCY_S-source_span-(time.time()-started)))
                        scan = await asyncio.to_thread(detect, req, prepared, scene, bindings=local_bindings,
                                                       enemies=enemies, known_heroes=KNOWN_HEROES,
                                                       equipped={hero: previous.summoner_for(hero).get("skill") for hero in enemies} if previous else {},
                                                       after_check=previous.last_skill_check if previous else None,
                                                       scene_cache=scene_cache,
                                                       track_memory=previous.track_memory if previous else None,
                                                       budget_s=review_budget)
                        record('skill_detector', scan)
                        detected["enemy_skill_events"] = scan["enemy_skill_events"]
                        detected["hero_levels"].extend(scan["hero_levels"])
                        suspicion_candidates = scan.get('suspicions', [])
                        # Experimental screen-wide classifier is diagnostic only.
                        # It cannot attribute a cast to a particular enemy actor.
                        try:
                            from gameplan.skills.action_model import load as load_action_model, review_sequence
                            action_model = load_action_model()
                            if action_model:
                                frames = []
                                for sample in [*req.recent_frames, req]:
                                    try:
                                        frames.append(np.asarray(Image.open(io.BytesIO(base64.b64decode(sample.image_base64))).convert('RGB'))[:, :, ::-1].copy())
                                    except Exception:
                                        continue
                                scan['action_model'] = review_sequence(action_model, frames) if len(frames) >= 2 else {
                                    'enabled': True, 'status': 'insufficient_frames'}
                        except Exception as exc:
                            scan['action_model'] = {'enabled': False, 'status': 'error', 'error': type(exc).__name__}
                        activity = scan["activity"]
                        detector_status = scan["status"]
                        model = req.model or os.getenv("OLLAMA_VISION_MODEL", "qwen3-vl:8b-instruct")
                else:
                    # With no local cast identity, only discover the current
                    # phase/roster. Sending eight images for unusable unbound
                    # skill guesses wastes the next capture interval.
                    fallback = req.model_copy(update={"focus": "heroes", "recent_frames": []}) if req.focus == "skills" else req
                    if req.focus == 'skills':
                        detector_status = 'waiting_gameplay_evidence'
                    detected, model = full_review if full_review is not None else await asyncio.to_thread(vision, fallback, "observe", prepared, roster_context=roster_context)
                detected = dict(detected)
                if detected.get('phase') == 'bp':
                    # Whole-frame models can mistake the hero pool/bans for team selections.
                    # Unsupported layouts remain unconfirmed until slot evidence is available.
                    detected.update(ally_roster=[], enemy_roster=[], player_hero=None,
                                    note='已识别选人画面，但玩家槽位未能可靠确认；不采用英雄池或禁用栏名单。')
                if detected.get('phase') in ('loading', 'in_game') and bp and bp['allies'] and bp['enemies'] and not (previous and previous.roster_verified) and not (scene and (scene["panel"] or scene.get("side_known"))):
                    # Preserve an established match identity, not arbitrary screen-order rows.
                    detected.update(ally_roster=bp['allies'], enemy_roster=bp['enemies'])
                    detected['player_hero'] = bp['player'] if detected['phase'] == 'in_game' else None
                    bp['last_seen'] = req.captured_at or started
                    bp['phase'] = detected['phase']
                elif detected.get('phase') in ('not_game', 'unknown', 'result', 'bp'):
                    bp_sessions.pop(req.match_id, None)
                    bp = None
            if (scan.get('detection_policy') != 'single_frame_effect' and req.focus == 'skills' and req.recent_frames and scene and not scene['panel']
                    and scene.get('phase') != 'loading' and local_bindings
                    and detector_status in ('waiting_identity', 'no_visible_target')):
                # OCR near screen edges can suggest, but cannot verify, an actor.
                # Review its visible form only for an explicitly tentative alert.
                from gameplan.skills.visual_suspicions import detect as review_unbound
                review_enemies=detected['ally_roster'] if perspective_side=='b' else detected['enemy_roster']
                review_bindings=[{**b,'side':('enemy_roster' if b['side']=='ally_roster' else 'ally_roster')}
                                 for b in local_bindings] if perspective_side=='b' else local_bindings
                review=await asyncio.to_thread(review_unbound,req,review_enemies,bindings=review_bindings,
                                               scene_cache=scene_cache)
                suspicion_candidates.extend(review.get('suspicions',[]))
                scan['visual_review_status']=review['status']
                if review.get('suspicions'):
                    detector_status='visual_suspected'
        # This banner also appears after entering the match, before the hero HUD.
        # Read it independently of the model's loading/in_game classification and BP history.
        evidence = None
        if scene is None:
            try:
                evidence = await asyncio.to_thread(read_lane_banner, prepared)
            except Exception:
                pass  # OCR failure must not discard otherwise valid observations.
        if evidence and detected.get("phase") in ("bp", "loading", "in_game"):
            detected["player_hero"] = None
            detected["self_skills"] = []
            detected["enemy_skill_events"] = []
        # A model's confidence is not evidence of a selected team. Match-ready
        # animations and role maps can otherwise invent a roster before BP,
        # which the tracker would then carry forward as this match's identity.
        unsupported_identity = False
        local_roster = scene and (scene["panel"] or scene.get("phase") == "loading")
        if model != 'official-portrait-matcher' and not local_roster and detected.get('phase') in ('loading', 'in_game'):
            trusted_match = previous and previous.roster_verified
            trusted_bp = bp and (bp.get('allies') or bp.get('enemies'))
            if trusted_match:
                detected.update(ally_roster=previous.allies[:], enemy_roster=previous.enemies[:])
            elif trusted_bp:
                detected.update(ally_roster=bp['allies'][:], enemy_roster=bp['enemies'][:])
            else:
                unsupported_identity = True
                detected.update(ally_roster=[], enemy_roster=[], player_hero=None,
                                enemy_skill_events=[], hero_levels=[], summoner_skills=[], self_skills=[])
            if not scene and not trusted_match and (detected.get('phase') == 'in_game' or not trusted_bp):
                detected['phase'] = 'bp' if evidence or bp or (previous and previous.phase == 'bp') else 'unknown'
                detected.update(enemy_skill_events=[], hero_levels=[], self_skills=[], player_hero=None,
                                note='当前阶段尚待确认；等待选人槽位、加载卡片或清晰对局画面，不采用整屏猜测的阵容。')
        # One malformed candidate must not hide valid heroes or other casts.
        from gameplan.core.models import EnemyCastObservation, HeroLevelObservation, SummonerObservation
        summoners = []
        # Ignore model-guessed equipment. Only independently matched scoreboard
        # rows and official skill icons can establish which spell is equipped.
        verified_equipment = []
        if scene and scene.get("phase") == "loading":
            verified_equipment = scene.get("equipment", [])
            if scene.get('side_known') and scene.get('cards'):
                from gameplan.vision.loading_spells import verify_sequence
                picture=Image.open(io.BytesIO(prepared if req.roi else raw)).convert('RGB')
                picture=picture.resize((round(picture.width*576/picture.height),576))
                verified_equipment=await asyncio.to_thread(verify_sequence,req,picture,scene['cards'])
        elif detected.get("phase") == "in_game":
            try:
                from gameplan.vision.summoner_icons import read_equipment
                # Consume short-lived panels already read in the same sequence.
                # Only same-side subsets of this confirmed match are eligible.
                if previous and previous.roster_verified:
                    for index, panel in reversed(list(enumerate(buffered_scenes[:-1]))):
                        if not panel or not panel.get('panel'):
                            continue
                        sides = (('ally_roster', previous.allies), ('enemy_roster', previous.enemies))
                        if not any(panel.get(side) for side, _ in sides) or any(
                                not set(panel.get(side, [])).issubset(set(known) & set(detected.get(side, [])))
                                for side, known in sides):
                            continue
                        local_bindings = merge_bindings(local_bindings or previous.identities, panel.get('bindings', []))
                        detected.setdefault('hero_levels', []).extend(
                            {**level, 'frame_index': index} for level in panel.get('hero_levels', []))
                        verified_equipment.extend({**item, 'frame_index': index} for item in
                            await asyncio.to_thread(read_equipment, anchors[index], KNOWN_HEROES,
                                                    readings=panel['readings'], panel_scene=panel))
                if scene:
                    current_equipment = await asyncio.to_thread(read_equipment, prepared if req.roi else raw,
                        KNOWN_HEROES, readings=scene["readings"], panel_scene=scene)
                else:
                    current_equipment = await asyncio.to_thread(read_equipment, prepared if req.roi else raw, KNOWN_HEROES)
                verified_equipment.extend({**item, 'frame_index': len(req.recent_frames)} for item in current_equipment)
            except Exception:
                pass
        for candidate in verified_equipment:
            try:
                summoners.append(SummonerObservation.model_validate(candidate))
            except (ValueError, TypeError):
                pass
        detected["summoner_skills"] = summoners[:80]
        levels = []
        for candidate in detected.get("hero_levels", []) or []:
            try:
                levels.append(HeroLevelObservation.model_validate(candidate))
            except (ValueError, TypeError):
                pass
        detected["hero_levels"] = levels[:20]
        candidates = []
        for candidate in detected.get("enemy_skill_events", []) or []:
            try:
                candidates.append(EnemyCastObservation.model_validate(candidate))
            except (ValueError, TypeError):
                pass
        detected["enemy_skill_events"] = candidates[:10]
        state = ScreenObservation.model_validate(detected)
        finished = time.time()
        if reset_versions.get(req.match_id) != version:
            # Portrait/OCR work can also have completed after reset.
            trackers.pop(req.match_id, None)
            bp_sessions.pop(req.match_id, None)
            lane_sessions.pop(req.match_id, None)
            raise HTTPException(409, "本局已清空，已丢弃旧画面。")
        captured = req.captured_at or started
        lane_context = update_lane_context(req.match_id, state.phase, evidence, captured)
        # Keep visually recognized names even when the hero is not yet in the
        # reviewed tactical catalog. Downstream advice falls back to generic,
        # evidence-based play guidance for those heroes.
        state.ally_roster = list(dict.fromkeys(name for name in state.ally_roster if name in KNOWN_HEROES))
        state.enemy_roster = list(dict.fromkeys(name for name in state.enemy_roster if name in KNOWN_HEROES))
        tracker = trackers.get(req.match_id)
        if unsupported_identity and tracker and not tracker.roster_verified and not tracker.loading_memory.cards:
            # Discard old whole-frame guesses, including pre-fix cached records.
            trackers.pop(req.match_id, None)
            tracker = None
        new_match_scene = ((state.phase == "loading" and scene) or
                           (state.phase == "bp" and model == "official-portrait-matcher"))
        if tracker and (state.phase == "result" or (new_match_scene and tracker.phase == "in_game")):
            trackers.pop(req.match_id, None)
            tracker = None
        if tracker is None:
            tracker = EventTracker()
            trackers[req.match_id] = tracker
        if state.phase in ('loading', 'in_game'):
            tracker.loading_memory = loading_memory
        tracker.scene_cache = scene_cache
        if local_bindings is not None and state.phase in ("in_game", "loading"):
            tracker.identities = local_bindings
            if scene and (scene["panel"] or scene.get("side_known")):
                tracker.roster_verified = True
                tracker.roster_source = "local_name_highlight" if scene.get("side_known") else "local_scoreboard"
            if scene and scene.get("player_hero"):
                tracker.player = scene["player_hero"]
                tracker.player_verified = bool(scene.get("side_known"))
            if scan.get("last_check"):
                tracker.last_skill_check = scan["last_check"]
        if state.phase in ("bp", "loading", "in_game"):
            # A momentarily unreadable roster doesn't erase this match's timers.
            if state.phase in ("loading", "in_game") and tracker.phase in ("loading", "in_game"):
                if state.phase == "in_game" and tracker.roster_verified and not (scene and scene["panel"]):
                    state.ally_roster, state.enemy_roster = tracker.allies[:], tracker.enemies[:]
                state.ally_roster = retain_roster(tracker.allies, state.ally_roster)
                state.enemy_roster = retain_roster(tracker.enemies, state.enemy_roster)
            tracker.allies, tracker.enemies = state.ally_roster[:], state.enemy_roster[:]
            tracker.phase = state.phase
        tracker.last_seen = finished
        trackers.move_to_end(req.match_id)
        while len(trackers) > 64:
            trackers.popitem(last=False)
        frame_times = [frame.captured_at for frame in req.recent_frames] + [captured]
        allies, enemies = state.ally_roster, state.enemy_roster
        if perspective_side == "b":
            allies, enemies = enemies, allies
        tracker.tracking_enemies = list(enemies)
        if state.phase in ("bp", "loading", "in_game"):
            tracker.update_summoners(state.summoner_skills, enemies=enemies, allies=allies, frame_times=frame_times)
        added = tracker.ingest(state.enemy_skill_events, captured, enemies=enemies, allies=allies,
                               frame_times=frame_times, hero_levels=state.hero_levels) if state.phase == "in_game" and req.focus != "heroes" else []
        if state.phase == 'in_game' and req.focus != 'heroes':
            # Level/equipment uncertainty must not silently erase a grounded
            # model observation. Confirmed timers keep their stricter contract.
            for candidate in state.enemy_skill_events:
                if candidate.confidence < .9 or not candidate.used:
                    continue
                if any(e.hero == candidate.hero and e.skill == candidate.skill for e in tracker.events):
                    continue
                suspicion_candidates.append({**candidate.model_dump(), 'reason':'awaiting_level_or_equipment_confirmation'})
            tracker.ingest_suspicions(suspicion_candidates, enemies=enemies, allies=allies,
                                     frame_times=frame_times, now=captured)
        # Sequential file analysis advances on the replay clock, even when
        # inference takes longer than playback. Wall time must not consume CD.
        timeline_now = captured if req.video_time_s is not None else finished
        suspicions = tracker.suspicion_snapshot(captured) if state.phase == 'in_game' else []
        for candidate in suspicions:
            candidate['display_expires_at'] = finished + max(0, candidate['expires_at']-captured)
            if req.video_time_s is not None:
                candidate['video_time_s'] = max(0, req.video_time_s-(captured-candidate['captured_at']))
        estimated_timers = tracker.estimate_snapshot(timeline_now) if state.phase == "in_game" else []
        enemy_skill_timers = tracker.snapshot(timeline_now) if state.phase == "in_game" else []
        if req.video_time_s is not None:
            for timer in enemy_skill_timers:
                timer['video_time_s'] = max(0,req.video_time_s-(captured-timer['captured_at']))
        ultimate_states = tracker.ultimate_states(enemies, captured) if state.phase == "in_game" else []
        if state.player_hero not in state.ally_roster + state.enemy_roster:
            state.player_hero = None
        if state.phase == "in_game" and canonical_teams:
            # A locally bound player survives missed HUD frames and model guesses.
            # Enemy heroes can never become the controlled hero in this match.
            if tracker.player_verified or state.player_hero not in state.ally_roster:
                state.player_hero = tracker.player if tracker.player in state.ally_roster else None
        if state.phase == "in_game" and state.player_hero:
            tracker.player = state.player_hero
        age = finished - captured
        frame_id = uuid4().hex
        advisor.remember(frame_id, req.match_id, {**state.model_dump(), "player_lane": lane_context["lane"] if lane_context else None}, captured, req.input_kind)
        knowledge = lookup_skill_heroes([state.player_hero, *state.ally_roster, *state.enemy_roster])
        return {"observation": state.model_dump(), "model": model, "frame_id": frame_id,
                "match_epoch": tracker.match_epoch,
                "lane_context": lane_context,
                "bp_slots": bp.get("slots") if bp and state.phase == "bp" else None,
                "bp_context": {k: bp[k] for k in ('player', 'lane', 'source')} if bp else None,
                "loading_context": {"side":"a", "source":"local_name_highlight"}
                    if scene and scene.get('phase')=='loading' and scene.get('side_known') else None,
                "loading_slots": tracker.loading_memory.snapshot()['cards'] if state.phase in ('loading', 'in_game') else [],
                "side_status": tracker.loading_memory.snapshot()['side_status'] if tracker.loading_memory.cards else
                    ('confirmed' if tracker.roster_verified else 'unknown'),
                "team_context": {"side":"a", "source":tracker.roster_source or "local_bp_slots"}
                    if canonical_teams and state.phase in ("bp", "loading", "in_game") else None,
                "input_kind": req.input_kind, "video_time_s": req.video_time_s, "focus": req.focus, "enemy_skill_timers": enemy_skill_timers,
                "enemy_skill_updates": [e.id for e in added],
                "enemy_skill_suspicions": suspicions,
                "enemy_skill_estimates": estimated_timers,
                "enemy_skill_activity": [item for item in activity if item["hero"] in enemies and
                    (tracker.level_at(item["hero"], captured) or {}).get("level", 0) >= 4],
                "enemy_ultimate_states": ultimate_states,
                "enemy_summoner_states": tracker.summoner_states(enemies) if state.phase in ("bp", "loading", "in_game") else [],
                "skill_scan": {"frames": len(frame_times),
                               "qwen_frames_submitted": len(frame_times) if req.all_frames else None,
                               "full_frame_review": full_review[0].get("note", "") if full_review else None, "target_latency_s": TARGET_LATENCY_S,
                               "detector_status": detector_status,
                               "detection_policy": scan.get('detection_policy', 'onset'),
                               **{k: scan[k] for k in ("checks_total", "checks_attempted", "checks_completed", "frames_checked", "skipped_frames", "visible_enemy_actors", "pair_rechecks", "failure_reason", "visual_review_status", "reviewed_heroes", "pending_heroes", "multi_target_rechecks") if k in scan},
                               **({"action_model": scan["action_model"]} if "action_model" in scan else {}),
                               "max_frame_gap_s": round(max((b-a for a, b in zip(frame_times, frame_times[1:])), default=0), 2),
                               "rejected_candidates": {**tracker.rejections,**scan.get('rejected_candidates',{})} if state.phase == "in_game" else {},
                               "latency_s": round(max(0, finished - frame_times[0]), 2),
                               "over_target": finished - frame_times[0] > TARGET_LATENCY_S,
                               "processing_s": round(finished-started, 2),
                               "suspected_count": len(suspicions),
                               "coverage_status": "knowledge_configured_real_match_validation_pending"},
                "captured_at": captured, "processed_at": finished, "elapsed_s": round(finished-started, 2),
                "age_s": round(age, 2), "fresh": 0 <= age <= 12,
                "hero_knowledge": knowledge,
                "note": ("单帧明确特效触发，按首次识别画面时间计时；冷却为基础值估算。图片与本局释放记录不落盘。"
                         if scan.get('detection_policy') == 'single_frame_effect' else
                         "连续画面辅助确认释放；冷却为基础值估算。图片与本局释放记录不落盘。")}
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(502, "模型返回的画面状态无法校验，本帧已丢弃。") from exc
    except Exception as exc:
        raise HTTPException(503, "视觉推理失败，请检查 Ollama 状态；可降低画面范围后重试。") from exc





