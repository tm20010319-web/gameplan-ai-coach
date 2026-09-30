"""Sample personal play without coupling reviews to the HoK skill detector."""
import asyncio
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
import io
import time
from uuid import uuid4

from fastapi import HTTPException
from PIL import Image

from gameplan.ai import match_review
from gameplan.ai.integrations import image_bytes
from gameplan.core import review_store as legacy_store
from gameplan.persistence.database import StoreProxy, stream_key, enabled

store = StoreProxy(legacy_store, "gameplan.persistence.reviews")

workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="match-review")


def run_job(match_id, token):
    try:
        match = store.read(match_id, include_frames=True)
        if enabled():
            from gameplan.persistence.profiles import read
            result = match_review.analyze(match, match["frames"], profile=read()["data"])
        else:
            result = match_review.analyze(match, match["frames"])
    except Exception:
        result = {"status": "failed", "source": "local", "points": [],
                  "message": "分析暂未完成，本局记录已保存，可以重试。"}
    store.finish(match_id, token, result)


def schedule(match_id, retry=False):
    token = store.claim(match_id, retry)
    if token:
        workers.submit(copy_context().run, run_job, match_id, token)


def recover():
    for match_id in store.pending():
        schedule(match_id)


def snapshot(stream_id):
    state = store.stream(stream_id)
    return store.read(state["match_id"]) if state.get("match_id") else None


def thumbnail(pixels):
    with Image.open(io.BytesIO(pixels)) as picture:
        picture = picture.convert("RGB")
        picture.thumbnail((1280, 720))
        output = io.BytesIO()
        picture.save(output, format="JPEG", quality=78)
        return output.getvalue()


def basic_response(req, observation, captured, started):
    now = time.time()
    hero = observation.get("player_hero")
    allies = observation.get("ally_roster", [])[:]
    if hero and hero not in allies:
        allies = ([hero] + allies)[:5]
    return {"observation": {"phase": observation["phase"], "player_hero": hero,
                "ally_roster": allies, "enemy_roster": observation.get("enemy_roster", []),
                "game_time_s": observation.get("game_time_s"), "self_skills": [],
                "note": "正在记录个人表现，结束后自动复盘。"},
            "frame_id": uuid4().hex, "model": req.model, "focus": req.focus,
            "game": observation["game"], "input_kind": "live", "video_time_s": None,
            "captured_at": captured, "processed_at": now, "elapsed_s": round(now-started, 2),
            "fresh": 0 <= now-captured <= 12, "age_s": max(0, now-captured),
            "hero_knowledge": {}, "enemy_skill_timers": [], "enemy_skill_updates": [],
            "enemy_skill_suspicions": [], "enemy_skill_estimates": [], "enemy_skill_activity": [],
            "enemy_ultimate_states": [], "enemy_summoner_states": [],
            "skill_scan": {"detector_status": "review_only", "coverage_status": "personal_review"}}


async def observe(req, legacy):
    from gameplan.monitoring.monitor_runtime import vision_gate, reset_versions, reset_match
    started = time.time()
    captured = req.captured_at or started
    if not 0 <= started-captured <= 1800:
        raise HTTPException(422, "复盘采样已过期，请重新采集游戏画面。")
    internal_key = stream_key(req.match_id)
    version = reset_versions.get(internal_key)
    state = await asyncio.to_thread(store.stream, req.match_id)
    last = state.get("last_observation", {})
    due = (not last or last.get("game") != "hok" or last.get("phase") == "result"
           or captured-state.get("last_at", 0) >= 12)
    response = None
    # Keep the existing HoK detector on the unsampled frames. It can request an
    # immediate ending check instead of waiting for the next periodic sample.
    if not due:
        response = await legacy(req.model_copy(update={"match_id": internal_key}))
        due = response.get("observation", {}).get("phase") == "result"
    error = None
    if due:
        if vision_gate.locked():
            raise HTTPException(429, "模型正在处理上一帧，请稍后重试。")
        try:
            async with vision_gate:
                _, pixels = await asyncio.to_thread(image_bytes, req)
                images = []
                for frame in req.recent_frames[-2:]:
                    _, prepared = await asyncio.to_thread(image_bytes, req.model_copy(update={"image_base64": frame.image_base64, "recent_frames": []}))
                    images.append(prepared)
                observation = await asyncio.to_thread(match_review.recognize, [*images, pixels], req.model)
                if reset_versions.get(internal_key) != version:
                    raise HTTPException(409, "本局已清空，已丢弃旧画面。")
                previous_match = state.get("match_id")
                state = await asyncio.to_thread(store.ingest, req.match_id, observation, captured, thumbnail(pixels))
                if previous_match and state.get("match_id") != previous_match:
                    reset_match(internal_key)
                last = observation
        except HTTPException:
            raise
        except Exception:
            error = "本次复盘记录未完成，下一帧会继续尝试。"
    if response is None:
        if last.get("game") == "hok" and not error:
            # The new sample provides an observation this round; the next round
            # runs the existing skill pipeline. No double inference per frame.
            response = basic_response(req, last, captured, started)
        else:
            response = basic_response(req, last if not error and last else {"game": "unknown", "phase": "unknown"}, captured, started)
    response["game"] = last.get("game", "unknown")
    match = await asyncio.to_thread(snapshot, req.match_id)
    if match and match["status"] == "pending":
        await asyncio.to_thread(schedule, match["id"])
        match = await asyncio.to_thread(store.read, match["id"])
    response["review"] = match
    response["review_status"] = error or ("已识别英雄联盟，正在记录个人表现。" if last.get("game") == "lol" else "正在自动识别游戏并记录本局。")
    response["note"] = "复盘采样与关键画面保存在本机；局后仅将游戏事实发送给 DeepSeek。"
    return response
