import asyncio
import os
import re
import socket
import time
import json
from collections import defaultdict
from datetime import datetime, timezone
from uuid import uuid4
from ipaddress import ip_address
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from gameplan.tactics.coach import CATALOG, HEROES, ROOT, bp_plan, evaluate, fingerprint, hero_entry, search
from gameplan.ai.integrations import image_bytes, polish, vision, vision_status
from gameplan.core.models import BPRequest, GameState, ObservationRequest, ResultData, ReviewRequest, StateRequest, VisionRequest, ScreenObservation, SkillTimerRequest, ScreenCaptureRequest
from gameplan.core.reporting import LABELS, render, review
import gameplan.core.storage as storage
import gameplan.skills.cooldowns as cooldowns
import gameplan.monitoring.screen_capture as screen_capture
from gameplan.tactics.loading_plan import SAMPLE, build_plan
from gameplan.core.models import LoadingPlanRequest
from gameplan.core.models import CoachAnalysisRequest
from gameplan.ai.advisor import advisor, provider_status
from gameplan.monitoring.monitor_runtime import observe as monitor_observe, vision_gate
from gameplan.web.monitor_app import monitor_status, require_local as monitor_require_local
from gameplan.web.picture_analysis import routes as picture_routes
from gameplan.knowledge.skill_knowledge import coverage as skill_coverage, get_hero as get_skill_hero, lookup_many as lookup_skill_heroes

app = FastAPI(title="王者荣耀 AI 助教", version="2.0.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.include_router(picture_routes(monitor_require_local))
connections = defaultdict(set)
locks = defaultdict(asyncio.Lock)


def share_base(request):
    configured = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        from urllib.parse import urlparse
        parsed = urlparse(configured)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.query or parsed.fragment:
            raise HTTPException(503, "PUBLIC_BASE_URL 必须是完整 HTTP(S) 站点地址，不含查询或片段")
        return configured
    host = request.url.hostname
    if host in ("localhost", "127.0.0.1", "::1"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(("192.0.2.1", 80))
                host = probe.getsockname()[0]
        except OSError:
            host = "127.0.0.1"
    if ":" in host:
        host = "[" + host + "]"
    return f"{request.url.scheme}://{host}" + (f":{request.url.port}" if request.url.port else "")


async def publish(match_id, kind, data):
    payload = {"type": kind, "data": data}
    clients = list(connections.get(match_id, set()))
    if clients:
        results = await asyncio.gather(*(client.send_json(payload) for client in clients), return_exceptions=True)
        for client, result in zip(clients, results):
            if isinstance(result, Exception):
                connections[match_id].discard(client)


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/monitor")
def monitor_page(mode: str = "picture"):
    return FileResponse(ROOT / "static" / ("monitor.html" if mode == "screen" else "picture.html"))


@app.get("/api/monitor/status")
def standalone_status():
    return monitor_status()


@app.get("/api/health")
def health():
    return {"ok": True, "version": app.version, "hero_count": len(HEROES), "camera_required": False}


@app.get("/api/config")
def config(request: Request):
    return {
        "hero_count": len(HEROES), "vision_model": os.getenv("OLLAMA_VISION_MODEL", "qwen3-vl:8b"),
        "deepseek_configured": bool(os.getenv("DEEPSEEK_API_KEY")), "share_base_url": share_base(request),
        "mode": "手动 / 演示可离线运行", "knowledge_note": CATALOG["note"],
    }


@app.get("/api/vision/status")
async def get_vision_status(model: str | None = Query(default=None, max_length=100)):
    return await asyncio.to_thread(vision_status, model)


@app.get("/api/skills")
def get_skills():
    path = ROOT / "resources" / "knowledge" / "skill_catalog.json"
    if not path.exists():
        return {"heroes": [], "note": "技能资料尚未导入", "fetched_at": None}
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/knowledge/skills/status")
def skill_knowledge_status():
    return skill_coverage()


@app.get("/api/knowledge/skills/{hero}")
def skill_knowledge_hero(hero: str):
    record = get_skill_hero(hero)
    if not record:
        raise HTTPException(404, "知识库中没有这个英雄的技能资料")
    return record


@app.post("/api/knowledge/skills/lookup")
def skill_knowledge_lookup(payload: dict):
    heroes = payload.get("heroes", []) if isinstance(payload, dict) else []
    if not isinstance(heroes, list) or len(heroes) > 10 or any(not isinstance(hero, str) for hero in heroes):
        raise HTTPException(422, "heroes 必须是最多 10 个英雄名称的数组")
    return {"items": lookup_skill_heroes(heroes), "coverage": skill_coverage()}


@app.get("/api/coach/status")
def coach_status():
    return provider_status()


@app.post("/api/coach/analysis")
async def coach_analysis(req: CoachAnalysisRequest, request: Request):
    require_local_screen_request(request)
    try:
        return await asyncio.to_thread(advisor.analyze, req)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def require_local_screen_request(request):
    try:
        local = request.client is not None and ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    if not local or request.url.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise HTTPException(403, "整屏采集只允许在运行本程序的电脑上，通过 localhost 或 127.0.0.1 使用。")
    origin = request.headers.get("origin")
    if origin and (urlparse(origin).scheme, urlparse(origin).netloc) != (request.url.scheme, request.url.netloc):
        raise HTTPException(403, "不允许其他网站请求本机屏幕。")


@app.get("/api/screen/sources")
async def screen_sources(request: Request):
    require_local_screen_request(request)
    try:
        sources = await asyncio.to_thread(screen_capture.displays)
        return {"sources": [{key: value for key, value in item.items() if key != "bbox"} for item in sources]}
    except screen_capture.CaptureError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/screen/frame")
async def screen_frame(req: ScreenCaptureRequest, request: Request):
    require_local_screen_request(request)
    try:
        return await asyncio.to_thread(screen_capture.capture, req.source_id, req.roi)
    except screen_capture.CaptureError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/heroes")
def heroes(lane: str | None = None):
    return {"items": [hero_entry(hero) for hero in HEROES.values() if not lane or hero["lane"] == lane], "total": len(HEROES)}


@app.get("/api/loading/sample")
def loading_sample():
    return SAMPLE


@app.post("/api/loading/plan")
def loading_recommendation(req: LoadingPlanRequest):
    return build_plan(req.allies, req.enemies, req.player, req.source)


@app.get("/api/knowledge/search")
def knowledge_search(q: str = Query("", max_length=200), phase: str | None = None, limit: int = Query(5, ge=1, le=60)):
    return {"items": search(q, phase, limit)}


@app.post("/api/matches")
def create_match():
    match_id = uuid4().hex
    storage.memory(match_id)
    return {"match_id": match_id}


@app.get("/api/matches/{match_id}")
def get_match(match_id: str):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", match_id):
        raise HTTPException(400, "对局编号无效")
    memory = storage.memory(match_id)
    advice = memory.get("advice")
    if advice and advice["expires_epoch"] <= time.time():
        advice = None
    return {"match_id": match_id, "phase": memory.get("phase", "bp"), "bp": memory.get("bp"), "advice": advice, "records": storage.records(match_id), "last_report": memory.get("last_report"), "skill_timers": cooldowns.snapshot(match_id, memory)}


def validate_timer_match(match_id):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", match_id):
        raise HTTPException(400, "对局编号无效")


@app.get("/api/matches/{match_id}/skill-timers")
def get_skill_timers(match_id: str):
    validate_timer_match(match_id)
    return cooldowns.snapshot(match_id, storage.memory(match_id))


@app.post("/api/matches/{match_id}/skill-timers")
async def register_skill_timer(match_id: str, req: SkillTimerRequest):
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
        await publish(match_id, "skill_timers", result)
    return result


@app.post("/api/matches/{match_id}/skill-timers/clear")
async def clear_skill_timers(match_id: str):
    validate_timer_match(match_id)
    async with locks[match_id]:
        memory = storage.memory(match_id)
        cooldowns.retire(memory, {item["id"] for item in memory.get("skill_timers", [])}, "cleared")
        storage.save_memory(match_id, memory)
        storage.audit(match_id, {"kind": "skill_timers_cleared"})
        result = cooldowns.snapshot(match_id, memory)
        await publish(match_id, "skill_timers", result)
    return result


@app.post("/api/matches/{match_id}/skill-timers/{timer_id}/cancel")
async def cancel_skill_timer(match_id: str, timer_id: str):
    validate_timer_match(match_id)
    async with locks[match_id]:
        memory = storage.memory(match_id)
        if not any(item["id"] == timer_id for item in memory.get("skill_timers", [])):
            raise HTTPException(404, "该技能计时已被替换或移除")
        cooldowns.retire(memory, {timer_id}, "cancelled")
        storage.save_memory(match_id, memory)
        storage.audit(match_id, {"kind": "skill_timer_cancelled", "timer_id": timer_id})
        result = cooldowns.snapshot(match_id, memory)
        await publish(match_id, "skill_timers", result)
    return result


@app.websocket("/ws/{match_id}")
async def websocket(websocket: WebSocket, match_id: str):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", match_id):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    connections[match_id].add(websocket)
    try:
        await websocket.send_json({"type": "snapshot", "data": get_match(match_id)})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        connections[match_id].discard(websocket)
        if not connections[match_id]:
            connections.pop(match_id, None)


@app.post("/api/bp/advice")
async def bp_advice(req: BPRequest):
    result = bp_plan(req.enemy_heroes, req.ally_heroes)
    async with locks[req.match_id]:
        memory = storage.memory(req.match_id)
        memory["bp"] = {**result, "enemy_heroes": req.enemy_heroes, "ally_heroes": req.ally_heroes}
        memory["advice"] = None
        memory["phase"] = "bp"
        storage.save_memory(req.match_id, memory)
        storage.audit(req.match_id, {"kind": "bp", "input": req.model_dump(), "output": result})
    await publish(req.match_id, "bp", memory["bp"])
    return result


@app.post("/api/in_game/advice")
async def in_game_advice(req: StateRequest):
    state = req.state_json.model_dump()
    now = time.time()
    advice = evaluate(state)
    signature = fingerprint(state, advice)
    async with locks[req.match_id]:
        memory = storage.memory(req.match_id)
        memory["phase"] = state["phase"]
        previous = memory.get("candidate", {})
        old_advice = memory.get("advice")
        memory["candidate"] = {
            "signature": signature, "since": previous.get("since", now) if previous.get("signature") == signature else now,
            "at": now, "decision": advice["decision"],
        }
        status = "ready"
        if not advice["message"]:
            status = "quiet"
        elif "low_confidence" in advice["reason_codes"]:
            status = "low_confidence"
        elif req.source == "vision":
            same = previous.get("signature") == signature and now - previous.get("at", 0) <= 3.5
            opposite = {previous.get("decision"), advice["decision"]} == {"engage", "retreat"}
            if opposite and now - previous.get("at", 0) <= 2:
                status = "conflict"
            elif not same or now - previous.get("since", now) < 1:
                status = "confirming"
        if status == "ready" and old_advice and memory.get("signature") == signature and old_advice["expires_epoch"] > now:
            response = {**old_advice, "triggered": False, "status": "duplicate", "advice": old_advice}
        elif status != "ready":
            memory["advice"] = None
            response = {"triggered": False, "status": status, "advice": None, "message": advice["message"] if status == "low_confidence" else "局面识别中" if status in ("conflict", "confirming") else "安静观察 · 暂无高价值事件"}
        else:
            names = " ".join(state["heroes"]["enemy_visible"] + state["heroes"]["enemy_missing"])
            snippets = search(names, "mid_game", 3) + search("目标 线权", "mid_game", 2)
            advice, model_log = await asyncio.to_thread(polish, advice, state, snippets, old_advice)
            issued = time.time()
            record = {
                **advice, "advice_id": "adv_" + uuid4().hex, "issued_epoch": issued,
                "issued_at": datetime.fromtimestamp(issued, timezone.utc).isoformat(),
                "expires_epoch": issued + advice["expires_in_sec"], "game_time": state["time_sec"],
                "input_source": req.source, "knowledge_ids": [item["id"] for item in snippets],
                "execution": "unobserved",
            }
            memory["signature"], memory["advice"] = signature, record
            storage.save_record(req.match_id, record)
            storage.audit(req.match_id, {"kind": "decision", "state": state, "knowledge": snippets, "model": model_log, "output": record})
            response = {**record, "triggered": True, "status": "issued", "advice": record}
        storage.save_memory(req.match_id, memory)
        storage.audit(req.match_id, {"kind": "state", "source": req.source, "state": state, "status": response["status"]})
    await publish(req.match_id, "advice", response)
    return response


@app.post("/api/matches/{match_id}/records/{advice_id}/observation")
async def observation(match_id: str, advice_id: str, req: ObservationRequest):
    async with locks[match_id]:
        record = next((item for item in storage.records(match_id) if item["advice_id"] == advice_id), None)
        if not record:
            raise HTTPException(404, "找不到该对局建议")
        start = record.get("game_time")
        within = start is not None and start <= req.observed_at <= start + record["expires_in_sec"]
        certain = within and req.action_confidence >= 0.8 and req.observed_action != "unknown"
        execution = "uncertain"
        message = "观察超出建议有效期、缺少局内时间或置信度不足，不能判定执行情况。"
        if certain:
            execution = "matched" if req.observed_action == record["decision"] else "different"
            message = f"人工观察：建议{LABELS[record['decision']]}，记录到{LABELS[req.observed_action]}；不据此推断意图或建议正确性。"
        record.update(req.model_dump())
        record.update({"execution": execution, "review": message, "observation_source": "manual"})
        storage.save_record(match_id, record)
        storage.audit(match_id, {"kind": "observation", "record": record})
    await publish(match_id, "observation", record)
    return record


@app.post("/api/vision/{kind}")
async def vision_analyze(kind: str, req: VisionRequest):
    if kind == "observe":
        return await monitor_observe(req)
    if kind not in ("bp", "analyze", "result", "crop", "observe"):
        raise HTTPException(404, "不支持的识别阶段")
    if kind != "crop" and vision_gate.locked():
        raise HTTPException(429, "模型正在处理上一帧，请等待后发送最新画面。")
    started = time.time()
    try:
        raw, prepared = await asyncio.to_thread(image_bytes, req)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if kind == "crop":
        import base64
        return {"image_base64": "data:image/jpeg;base64," + base64.b64encode(prepared).decode()}
    # Image preparation yields to other requests. Recheck before the lock so a
    # second tab cannot enqueue an already aging frame during preparation.
    if vision_gate.locked():
        raise HTTPException(429, "模型正在处理上一帧，请等待后发送最新画面。")
    frame_id = uuid4().hex
    folder = storage.DATA / "frames"
    folder.mkdir(exist_ok=True)
    (folder / (frame_id + ".bin")).write_bytes(raw)
    (folder / (frame_id + ".jpg")).write_bytes(prepared)
    try:
        async with vision_gate:
            detected, model = await asyncio.to_thread(vision, req, kind, prepared)
        if kind == "analyze":
            state = GameState.model_validate(detected)
            result = {"state": state.model_dump(), "needs_confirmation": True}
        else:
            confidence = detected.get("confidence", 0)
            if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
                raise ValueError("视觉置信度无效")
            if kind == "bp":
                roster = BPRequest(enemy_heroes=detected.get("enemy_locked") or [], ally_heroes=detected.get("ally_locked") or [])
                result = {"detection": detected, "advice": bp_plan(roster.enemy_heroes, roster.ally_heroes) if confidence >= 0.8 and detected.get("phase") in ("bp", "loading") else None, "needs_confirmation": True}
            else:
                result_data = ResultData.model_validate(detected.get("result") or {})
                result = {"detection": {**detected, "result": result_data.model_dump()}, "needs_confirmation": True}
        storage.audit(req.match_id, {"kind": "vision", "frame_id": frame_id, "model": model, "raw_response": detected, "output": result})
        return {**result, "model": model, "frame_id": frame_id}
    except (ValueError, ValidationError, KeyError, TypeError) as exc:
        storage.audit(req.match_id, {"kind": "vision_failed", "frame_id": frame_id, "error_type": type(exc).__name__})
        raise HTTPException(502, "视觉结果不符合结构；请手动确认输入，不自动生成战术。") from exc
    except Exception as exc:
        storage.audit(req.match_id, {"kind": "vision_failed", "frame_id": frame_id, "error_type": type(exc).__name__})
        raise HTTPException(503, "本地视觉模型不可用；请启动 Ollama 并安装模型，或继续手动录入。") from exc


@app.post("/api/post_game/review")
async def post_game(req: ReviewRequest, request: Request):
    async with locks[req.match_id]:
        records = storage.records(req.match_id)
        report_id = uuid4().hex
        report = {
            **review(req.result.model_dump(), records), "id": report_id, "match_id": req.match_id,
            "is_demo": any(record.get("input_source") == "replay" for record in records),
            "result": req.result.model_dump(), "share_url": share_base(request) + "/r/" + report_id,
            "poster_url": "/api/reports/" + report_id + "/poster.png",
            "qr_url": "/api/reports/" + report_id + "/qr.png",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if req.advice_records:
            report["import_note"] = "为避免伪造执行证据，仅使用本对局已持久化记录；外部 advice_records 未用于归因。"
        try:
            await asyncio.to_thread(render, report)
        except Exception as exc:
            raise HTTPException(503, "海报生成失败；请检查中文字体 COACH_FONT 与数据目录权限。") from exc
        storage.save_report(report)
        memory = storage.memory(req.match_id)
        memory["last_report"], memory["advice"] = report_id, None
        memory["phase"] = "result"
        cooldowns.retire(memory, {item["id"] for item in memory.get("skill_timers", [])}, "match_ended")
        storage.save_memory(req.match_id, memory)
        storage.audit(req.match_id, {"kind": "report", "report_id": report_id, "input": req.result.model_dump()})
    public_report = {key: value for key, value in report.items() if key != "match_id"}
    await publish(req.match_id, "report", public_report)
    await publish(req.match_id, "skill_timers", cooldowns.snapshot(req.match_id, memory))
    return public_report


def require_report(report_id):
    if not re.fullmatch(r"[a-f0-9]{32}", report_id):
        raise HTTPException(404, "战报不存在")
    report = storage.get_report(report_id)
    if not report:
        raise HTTPException(404, "战报不存在")
    return report


@app.get("/api/reports/{report_id}")
def report_data(report_id: str):
    return {key: value for key, value in require_report(report_id).items() if key != "match_id"}


@app.get("/api/reports/{report_id}/{asset}")
def report_asset(report_id: str, asset: str):
    require_report(report_id)
    suffix = {"poster.png": ".png", "qr.png": "-qr.png"}.get(asset)
    if not suffix:
        raise HTTPException(404, "资源不存在")
    path = storage.DATA / "reports" / (report_id + suffix)
    if not path.is_file():
        raise HTTPException(404, "海报文件不存在")
    return FileResponse(path, media_type="image/png")


@app.get("/r/{report_id}")
def report_page(report_id: str):
    require_report(report_id)
    return FileResponse(ROOT / "static" / "report.html")
