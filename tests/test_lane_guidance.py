import asyncio
import base64
import time
from pathlib import Path
from unittest.mock import patch
from gameplan.tactics.lane_guidance import read_lane_banner
from gameplan.core.models import VisionRequest, CoachAnalysisRequest
import gameplan.monitoring.monitor_runtime as runtime

RAW = (Path(__file__).parent / "fixtures/bp/personal-lane-banner.png").read_bytes()

def test_actual_user_banner():
    evidence = read_lane_banner(RAW)
    assert evidence["lane"] == "发育路"
    assert "本局您的分路" in evidence["evidence"]

def test_plain_or_conflicting_map_labels_do_not_assign_player_lane():
    for labels in (["发育路"], ["本局您的分路", "发育路", "中路"], ["敌方发育路孙尚香"]):
        with patch('gameplan.vision.hero_recognition.read_text', return_value=[{"text":t,"score":.99} for t in labels]):
            assert read_lane_banner(RAW) is None

def test_lane_survives_without_bp_but_does_not_leak_to_other_match():
    async def run():
        runtime.lane_sessions.clear()
        now = time.time()
        model = {"phase":"in_game", "player_hero":"孙尚香", "ally_roster":["孙尚香"], "enemy_roster":["铠"]}
        req = VisionRequest(match_id="lane-user", image_base64=base64.b64encode(RAW).decode(), input_kind="video", captured_at=now)
        with patch('gameplan.monitoring.monitor_runtime.vision', return_value=(model,"mock")), patch('gameplan.monitoring.monitor_runtime.bp_portraits.match',return_value=None):
            first = await runtime.observe(req)
            assert first["bp_context"] is None
            assert first["lane_context"]["lane"] == "发育路"
            assert first["observation"]["player_hero"] is None
            result = runtime.advisor.analyze(CoachAnalysisRequest(match_id=req.match_id, frame_id=first["frame_id"]), local_only=True)
            assert result["scope"] == "lane"
            assert "发育路" in result["summary"]
            assert "孙尚香" not in result["summary"]
            with patch('gameplan.monitoring.monitor_runtime.read_lane_banner',return_value=None):
                second = await runtime.observe(req)
                assert second["lane_context"]["lane"] == "发育路"
                assert not second["lane_context"]["from_current_frame"]
                other = await runtime.observe(req.model_copy(update={"match_id":"lane-other"}))
                assert other["lane_context"] is None
        assert runtime.update_lane_context(req.match_id,"result",None,now) is None
        assert req.match_id not in runtime.lane_sessions
    asyncio.run(run())

def test_lane_only_frame_needs_no_lineup():
    runtime.advisor.remember("e"*32,"lane-empty", {"phase":"in_game","player_lane":"发育路"},time.time(),"video")
    result=runtime.advisor.analyze(CoachAnalysisRequest(match_id="lane-empty",frame_id="e"*32),local_only=True)
    assert result["status"] == "ok"
    assert "发育路" in result["summary"]


def test_compressed_video_banner_does_not_require_tiny_heading():
    from PIL import Image
    import io
    p=Image.open(Path(__file__).parent / "fixtures/bp/compressed-lane-preview.png")
    p=p.resize((640, round(p.height*640/p.width)))
    b=io.BytesIO();p.save(b,format="JPEG",quality=75)
    result=read_lane_banner(b.getvalue())
    assert result["lane"] == "发育路"
    assert "金色分路横幅" in result["evidence"]

def test_plain_label_without_heading_or_gold_banner_remains_unknown():
    import io
    import numpy as np
    from PIL import Image
    p=Image.new("RGB",(640,300),"navy");b=io.BytesIO();p.save(b,format="PNG")
    item={"text":"发育路","score":.99,"box":np.array([[175,12],[235,12],[235,24],[175,24]])}
    with patch('gameplan.vision.hero_recognition.read_text',return_value=[item]):
        assert read_lane_banner(b.getvalue()) is None
