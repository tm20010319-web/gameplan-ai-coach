"""Standalone local monitor. Does not import the workbench, database or reports."""
import asyncio
import time
from typing import Literal
from pydantic import BaseModel, Field
from ipaddress import ip_address
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from gameplan.tactics.coach import ROOT
from gameplan.ai.advisor import advisor, provider_status
from gameplan.ai.integrations import vision_status, post_json
import os
from gameplan.tactics.loading_plan import build_plan
from gameplan.core.models import ScreenCaptureRequest, VisionRequest, CoachAnalysisRequest, LoadingPlanRequest, BPPlanRequest, MatchRequest
from gameplan.monitoring.monitor_runtime import observe, reset_match
from gameplan.knowledge.skill_knowledge import coverage as knowledge_coverage, get_hero as knowledge_hero
import gameplan.monitoring.screen_capture as screen_capture

app = FastAPI(title="GAMEPLAN 独立桌面监控", version="1.0.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


def monitor_status():
    return {"application": "gameplan-desktop-monitor", "version": "1.0.0", "page": "/monitor"}


def require_local(request: Request):
    try:
        local = request.client is not None and ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    if not local or request.url.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise HTTPException(403, "桌面监控仅允许在本机通过 localhost 或 127.0.0.1 使用。")
    origin = request.headers.get("origin")
    if origin and (urlparse(origin).scheme, urlparse(origin).netloc) != (request.url.scheme, request.url.netloc):
        raise HTTPException(403, "不允许其他网站请求本机屏幕或模型分析。")


@app.get("/")
@app.get("/monitor")
def page(mode: str = "picture", embedded: bool = False):
    filename = ('monitor.html' if mode == 'screen' else 'picture.html') if embedded else 'workspace.html'
    return FileResponse(ROOT / 'static' / filename)


@app.get('/monitor/panel')
def panel_page():
    return FileResponse(ROOT / 'static' / 'monitor.html')


class PanelRequest(BaseModel):
    source_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{16}$')
    region: Literal['full', 'left', 'right'] = 'full'


@app.post('/api/monitor/panel/open')
async def launch_panel(req: PanelRequest, request: Request):
    require_local(request)
    from gameplan.monitoring.panel_launcher import open_panel
    if req.source_id:
        found = await asyncio.to_thread(screen_capture.sources)
        if not any(source['id'] == req.source_id for source in found):
            raise HTTPException(409, '所选窗口已关闭，请刷新采集列表。')
    try:
        return await asyncio.to_thread(open_panel, request.url.port or 80, req.source_id, req.region)
    except (OSError, ValueError) as exc:
        raise HTTPException(503, '悬浮面板未能启动，请检查 work/monitor-logs/panel.log。') from exc


@app.get("/api/monitor/status")
def status():
    return monitor_status()


@app.get("/api/health")
def health():
    return {"ok": True, **monitor_status()}


@app.get('/api/knowledge/skills/status')
def knowledge_status():
    return knowledge_coverage()


@app.get('/api/monitor/skill-policy')
def skill_policy():
    from gameplan.skills.monitor_policy import TARGET_LATENCY_S, recognition_reference
    from gameplan.knowledge.skill_knowledge import _catalog
    from gameplan.skills.cooldown_estimates import SUMMONERS, SUMMONER_DATA
    references = [recognition_reference(record['hero']) for record in _catalog().get('heroes', [])]
    return {'target_latency_s': TARGET_LATENCY_S, 'alert_policy': 'single_frame_effect',
            'timing_basis': 'first_visible_effect', 'minimum_effect_frames': 1,
            'skills': ['ultimate', 'summoner'],
            'excluded_ultimates': [],
            'summoner_skills': list(SUMMONERS.values()),
            'summoner_source': SUMMONER_DATA['source_url'],
            'cooldown_source': 'resources/knowledge/skill_catalog.json',
            'heroes': [reference for reference in references if reference],
            'validation': 'pending_real_match_validation'}


@app.get('/api/knowledge/skills/{hero}')
def hero_knowledge(hero: str):
    result = knowledge_hero(hero)
    if result is None:
        raise HTTPException(404, '知识库中没有这个英雄')
    return result


@app.get("/api/vision/status")
async def model_status(model: str | None = Query(default=None, max_length=100)):
    return await asyncio.to_thread(vision_status, model)


@app.post("/api/vision/warmup")
async def warm_model(request: Request, model: str | None = Query(default=None, max_length=100)):
    require_local(request)
    from gameplan.monitoring.monitor_runtime import vision_gate
    if vision_gate.locked():
        raise HTTPException(429, "模型正在处理画面，稍后重试。")
    async with vision_gate:
        from gameplan.skills.combat_evidence import warm_scene_workers
        await asyncio.to_thread(warm_scene_workers)
        selected=model or os.getenv('OLLAMA_VISION_MODEL','qwen3-vl:8b-instruct')
        try:
            await asyncio.to_thread(post_json,os.getenv('OLLAMA_URL','http://127.0.0.1:11434').rstrip('/')+'/api/generate',
                                    {'model':selected,'prompt':'','stream':False,'keep_alive':'15m','options':{'num_ctx':16384}},60)
        except (OSError,ValueError) as exc:
            raise HTTPException(503,"模型预热未完成，请检查 Ollama 后重试。") from exc
    return await asyncio.to_thread(vision_status,selected)


@app.get("/api/screen/sources")
async def sources(request: Request):
    require_local(request)
    try:
        found = await asyncio.to_thread(screen_capture.sources)
        return {"sources": [{key: value for key, value in item.items() if key in ("id", "label", "kind", "width", "height", "primary", "minimized")} for item in found]}
    except screen_capture.CaptureError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/screen/frame")
async def frame(req: ScreenCaptureRequest, request: Request):
    require_local(request)
    try:
        return await asyncio.to_thread(screen_capture.capture, req.source_id, req.roi)
    except screen_capture.CaptureError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/vision/observe")
async def observation(req: VisionRequest, request: Request):
    require_local(request)
    from gameplan.monitoring.skill_diagnostics import begin, active_trace
    trace = begin(req)
    if trace is None:
        return await observe(req)
    token = active_trace.set(trace)
    try:
        response = await observe(req)
        response['note'] = response.get('note', '').replace('图片与本局释放记录不落盘。',
            '已开启本地漏检诊断，最近送检画面与判断结果按容量轮换保存。')
        trace_id = await asyncio.to_thread(trace.finish, response=response)
        response['skill_scan']['diagnostic_id'] = trace_id
        response['skill_scan']['diagnostic_status'] = 'saved' if trace_id else 'write_failed'
        return response
    except HTTPException as exc:
        if exc.status_code != 429:
            await asyncio.to_thread(trace.finish, error={'status_code': exc.status_code, 'detail': exc.detail})
        raise
    finally:
        active_trace.reset(token)


@app.post("/api/monitor/match/reset")
def clear_match(req: MatchRequest, request: Request):
    require_local(request)
    reset_match(req.match_id)
    return {"cleared": True, "match_id": req.match_id}


from gameplan.core.models import SummonerCorrectionRequest


@app.post("/api/monitor/summoner/correct")
def correct_summoner(req: SummonerCorrectionRequest, request: Request):
    require_local(request)
    from gameplan.monitoring.monitor_runtime import trackers
    tracker = trackers.get(req.match_id)
    if tracker is None:
        raise HTTPException(404, "本局记录已清空，请先识别当前阵容")
    try:
        tracker.correct_summoner(req.hero, req.skill)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    processed_at = time.time()
    return {"enemy_summoner_states": tracker.summoner_states(tracker.tracking_enemies),
            "enemy_skill_timers": tracker.snapshot(processed_at),
            "enemy_skill_estimates": tracker.estimate_snapshot(processed_at),
            "enemy_skill_suspicions": tracker.suspicion_snapshot(processed_at), "processed_at": processed_at}


@app.get("/api/coach/status")
def coach_status():
    return provider_status()


@app.post("/api/coach/analysis")
async def analyze(req: CoachAnalysisRequest, request: Request):
    require_local(request)
    try:
        return await asyncio.to_thread(advisor.analyze, req, local_only=True)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/loading/plan")
def quick_plan(req: LoadingPlanRequest):
    return build_plan(req.allies, req.enemies, req.player, req.source)


@app.post("/api/bp/plan")
def bp_quick_plan(req: BPPlanRequest):
    from gameplan.tactics.coach import bp_plan
    return bp_plan(req.enemies, req.allies)


from gameplan.web.picture_analysis import routes as picture_routes
app.include_router(picture_routes(require_local))

from gameplan.tactics.bp_assistant import routes as bp_routes
app.include_router(bp_routes(require_local))

@app.get('/bp')
def bp_page(embedded: bool = False):
    return FileResponse(ROOT / 'static' / ('bp.html' if embedded else 'workspace.html'))
