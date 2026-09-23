"""Targeted local vision. Only final answers with locally bound heroes count."""
import base64
import io
import json
import os
import re
import time

from PIL import Image, ImageChops, ImageStat

from gameplan.skills.combat_evidence import read_scene
from gameplan.knowledge.skill_knowledge import _catalog
from gameplan.skills.monitor_policy import recognition_reference, suspect


def final_json(response):
    if response.get("done_reason") != "stop":
        raise ValueError("识别未产生完整最终回答")
    content = (response.get("message") or {}).get("content", "").strip()
    if content.startswith("```json\n") and content.endswith("```"):
        content = content[8:-3].strip()
    elif content.startswith("```\n") and content.endswith("```"):
        content = content[4:-3].strip()
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("最终回答必须为对象")
    return parsed


def frame_index(value, count):
    """Accept the F labels printed on model images without coercing guesses."""
    if isinstance(value, str) and re.fullmatch(r"(?:F)?[0-7]", value.strip()):
        value = int(value.strip().removeprefix('F'))
    return value if type(value) is int and 0 <= value < count else None


def detect(req, prepared, scene, *, bindings, enemies, known_heroes, equipped=None, after_check=None, budget_s=6, scene_cache=None, track_memory=None):
    if len(req.recent_frames) >= 2:
        from gameplan.skills.temporal_casts import detect_sequence
        return detect_sequence(req,scene,bindings=bindings,enemies=enemies,known_heroes=known_heroes,equipped=equipped,budget_s=budget_s,scene_cache=scene_cache,track_memory=track_memory)
    scan_started = time.monotonic()
    from gameplan.ai.integrations import image_bytes, post_json
    result = {"enemy_skill_events": [], "activity": [], "hero_levels": [], "status": "no_visible_target" if bindings else "waiting_identity"}
    if scene["panel"]:
        result["status"] = "scoreboard"
        return result
    if not req.recent_frames:
        result["status"] = "need_sequence"
        return result
    # Use the preceding submitted image and the latest, with original indices
    # retained. Never turn index-zero ongoing effects into a new cast.
    first = req.recent_frames[-1]
    if first.image_base64.split(",")[-1] == req.image_base64.split(",")[-1]:
        result["status"] = "unchanged"
        return result
    raw_previous, previous = image_bytes(req.model_copy(update={"image_base64": first.image_base64, "recent_frames": []}))
    small = [Image.open(io.BytesIO(p)).convert("L").resize((320, 180)) for p in (previous, prepared)]
    if ImageStat.Stat(ImageChops.difference(*small)).mean[0] <= .1:
        result["status"] = "unchanged"
        return result
    earlier = read_scene(previous if req.roi else raw_previous, known_heroes, bindings, cache=scene_cache)
    observations = [(len(req.recent_frames)-1, earlier), (len(req.recent_frames), scene)]
    targets = {}
    for index, observation in observations:
        if not observation or observation["panel"]:
            continue
        for level in observation["hero_levels"]:
            result["hero_levels"].append({**level, "frame_index": index})
        for target in observation["targets"]:
            if target["hero"] in enemies:
                targets.setdefault(target["hero"], []).append({**target, "frame_index": 0 if index == observations[0][0] else 1})
    catalog = {h["hero"]: h for h in _catalog().get("heroes", [])}
    references = []
    for hero, sightings in targets.items():
        skill = next((s for s in catalog.get(hero, {}).get("skills", []) if s.get("is_ultimate")), None)
        if skill:
            references.append({"hero": hero, "skill": skill["name"], "slot": skill["slot"],
                               "mechanic": skill.get("description", "")[:140], "sightings": sightings})
    if not references:
        return result
    pictures = []
    # Canonicalize both submitted frames once. Re-encoding an already prepared
    # frame adds different artifacts in API calls versus offline replay checks.
    model_pixels = (previous, prepared) if req.roi else (
        raw_previous, base64.b64decode(req.image_base64.split(",")[-1], validate=True))
    for pixels in model_pixels:
        picture = Image.open(io.BytesIO(pixels)).convert("RGB"); picture.thumbnail((1280, 720))
        output = io.BytesIO(); picture.save(output, "JPEG", quality=88)
        pictures.append(base64.b64encode(output.getvalue()).decode())
    first_index, last_index = observations[0][0], observations[1][0]
    candidates = []
    incomplete = False
    checks = []
    for reference in references:
        checks.append(reference)
        if (equipped or {}).get(reference["hero"]) == "闪现":
            checks.append({**reference, "skill": "闪现", "mechanic": "闪现造成瞬间短距离位移，须区分英雄自身位移技能"})
    # Bound inference work per sample and rotate unfinished checks across samples.
    # Do not let a failed hero request discard other heroes' accepted evidence.
    keys = [(r["hero"], r["skill"]) for r in checks]
    if after_check in keys:
        start = keys.index(after_check) + 1
        checks = checks[start:] + checks[:start]
    deadline = scan_started + budget_s
    result.update(checks_total=len(checks), checks_attempted=0, checks_completed=0)
    for reference in checks:
        remaining = deadline - time.monotonic()
        if remaining < 1:
            break
        sighting = reference["sightings"][0]
        # Reviewed mechanism descriptions, independent of any replay timestamp,
        # hero lineup, skin color, or expected result in a test sample.
        action = {"敖隐": "化为长龙腾空", "孙策": "驾驶船航行、撞船击飞"}.get(reference["hero"])
        if reference["skill"] == "闪现":
            action = "瞬间短距离位移的闪现，须区分英雄自身位移技能"
        elif not action:
            action = re.sub(r"[（(].*?[）)]", "", reference["mechanic"]).split("。")[0][:65]
        position = "右侧" if sighting["x"] > .66 else "左侧" if sighting["x"] < .33 else "中间"
        prompt = (
            f"比较两张按顺序的王者荣耀截图，检查图{sighting['frame_index']}{position}昵称“{sighting['nickname']}”的敌方{reference['hero']}"
            f"是否开始释放{action}的技能。身份已由战绩面板姓名对应。"
            "只看战场中该角色的形态变化，不能依据自己的技能按钮、野怪或其他角色。"
            "输出JSON字段：evidence（具体可见变化）、"
            "state（none/uncertain/ongoing/cast_start）、frame_index（0或1）、confidence（0至1）。"
            "第一帧已存在的效果只能ongoing，没有明确变化不要猜。")
        knowledge = recognition_reference(reference['hero']) or {}
        prompt += ('知识库大招机制：'+knowledge.get('mechanic', reference['mechanic'])+
                   '。其他技能对照：'+json.dumps(knowledge.get('other_skills', []), ensure_ascii=False)+
                   '。大招与其他技能无法区分时填uncertain，不推测冷却数值。')
        result["checks_attempted"] += 1
        result["last_check"] = (reference["hero"], reference["skill"])
        try:
            response = post_json(os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/") + "/api/chat",
                {"model": req.model or os.getenv("OLLAMA_VISION_MODEL", "qwen3-vl:8b-instruct"), "stream": False,
                 "think": False, "keep_alive": "15m", "options": {"temperature": 0, "num_ctx": 16384, "num_predict": 800},
                 "messages": [{"role": "user", "content": prompt, "images": pictures}]}, min(12, remaining))
            parsed = final_json(response)
            if parsed.get("hero", reference["hero"]) != reference["hero"]:
                incomplete = True
                continue
            result["checks_completed"] += 1
            candidates.append({**parsed, "hero": reference["hero"], "skill": reference["skill"]})
        except (ValueError, TypeError, OSError) as exc:
            incomplete = True
            result['failure_reason']='model_timeout' if isinstance(exc,TimeoutError) else 'model_unavailable' if isinstance(exc,OSError) else 'invalid_model_answer'
    result["status"] = "observed"
    if incomplete:
        result["status"] = "incomplete_answer"
    if result["checks_attempted"] < result["checks_total"]:
        result["status"] = "partial"
    for candidate in candidates[:10]:
        if not isinstance(candidate, dict) or candidate.get("hero") not in targets:
            continue
        if candidate.get('state') not in ('cast_start', 'ongoing', 'uncertain'):
            continue
        reference = next(r for r in references if r["hero"] == candidate["hero"])
        if candidate.get("skill") not in (reference["skill"], "大招", "闪现"):
            continue
        try:
            confidence = float(candidate.get("confidence", 0))
        except (ValueError, TypeError):
            continue
        if not .65 <= confidence <= 1 or not str(candidate.get("evidence", "")).strip():
            continue
        candidate['frame_index'] = frame_index(candidate.get('frame_index'), 2)
        if candidate['frame_index'] is None:
            result.setdefault('rejected_candidates',{})['invalid_frame_index']=1
            continue
        def tentative(reason):
            suspect(result, hero=candidate['hero'], skill=candidate['skill'],
                    index=observations[candidate['frame_index']][0], confidence=confidence,
                    evidence=str(candidate.get('evidence', '')), reason=reason,event_type=candidate.get('state','uncertain'))
        if confidence < .9 or candidate.get('state') == 'uncertain':
            tentative('model_uncertain')
            continue
        if candidate['hero']=='高渐离' and candidate.get('skill')!='闪现':
            from gameplan.skills.field_effects import large_enemy_field
            target=next((t for t in scene['targets'] if t['hero']=='高渐离'),None)
            if not target or not large_enemy_field(Image.open(io.BytesIO(model_pixels[-1])),target):
                result.setdefault('rejected_candidates',{})['large_field_unconfirmed']=1
                tentative('large_field_unconfirmed')
                continue
        if candidate.get("state") == "cast_start" and candidate.get("frame_index") == 1:
            if not any(s["frame_index"] == 0 for s in targets[candidate["hero"]]):
                tentative('onset_identity_unconfirmed')
                continue  # An identity first seen afterwards cannot establish onset.
            if candidate["hero"] == "敖隐" and candidate["skill"] != "闪现" and any(
                    s["frame_index"] == 1 for s in targets[candidate["hero"]]):
                # The dragon form is untargetable; a readable normal nameplate
                # on the same hero contradicts a claim of completed transformation.
                continue
            if candidate['skill']=='闪现':
                from gameplan.skills.flash_motion import displaced
                import cv2
                import numpy as np
                before=next((t for t in earlier['targets'] if t['hero']==candidate['hero']),None) if earlier else None
                after=next((t for t in scene['targets'] if t['hero']==candidate['hero']),None)
                gray=[]
                for pixels in model_pixels:
                    picture=Image.open(io.BytesIO(pixels)).convert('RGB')
                    picture=picture.resize((round(picture.width*576/picture.height),576))
                    gray.append(cv2.cvtColor(np.asarray(picture),cv2.COLOR_RGB2GRAY))
                if not before or not after or req.captured_at-first.captured_at>.8 or not displaced(*gray,before,after):
                    result.setdefault('rejected_candidates',{})['flash_motion_unconfirmed']=1
                    if before and after:tentative('flash_motion_unconfirmed')
                    continue
            result["enemy_skill_events"].append({**candidate, "frame_index": last_index,
                'onset_start_index':first_index, 'onset_end_index':last_index, "used": True, "event_type": "cast_start"})
        elif candidate.get("state") == "ongoing" and candidate.get("skill") != "闪现":
            # Ongoing observation has no trustworthy cast-start timestamp.
            # It can be shown tentatively, never used to start a cooldown.
            result["activity"].append({"hero": candidate["hero"], "skill": reference["skill"],
                                      "status": "possible_ongoing"})
            tentative('ongoing_onset_unknown')
    return result
