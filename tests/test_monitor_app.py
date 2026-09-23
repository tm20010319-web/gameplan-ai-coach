import base64
import io
import json
import random
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
import gameplan.web.monitor_app as monitor_app
import gameplan.monitoring.monitor_runtime as monitor_runtime


class StandaloneMonitorTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(monitor_app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))

    def tearDown(self):
        self.client.close()

    def test_exhaustive_video_sends_every_frame_to_qwen_even_with_local_portrait_result(self):
        frames = []
        for i in range(6):
            image = io.BytesIO()
            Image.new("RGB", (64, 64), (i * 40, 20, 60)).save(image, format="PNG")
            frames.append({"image_base64": base64.b64encode(image.getvalue()).decode(), "captured_at": 100 + i / 60})
        detected = {"phase": "not_game", "player_hero": None, "ally_roster": [], "enemy_roster": [], "note": "无游戏内容"}
        with patch('gameplan.skills.combat_evidence.read_scene', return_value=None), \
             patch('gameplan.monitoring.monitor_runtime.portrait_observation', return_value=(detected, None)), \
             patch('gameplan.monitoring.monitor_runtime.read_lane_banner', return_value=None), \
             patch('gameplan.ai.integrations.post_json', return_value={"message": {"content": json.dumps(detected)}}) as infer:
            response = self.client.post('/api/vision/observe', json={
                "match_id": "all60-transport", "input_kind": "video", "video_time_s": 5 / 60,
                "all_frames": True, "focus": "heroes", **frames[-1], "recent_frames": frames[:-1]})
        self.assertEqual(response.status_code, 200, response.text)
        infer.assert_called_once()
        images = infer.call_args.args[1]['messages'][0]['images']
        self.assertEqual(len(images), 6)
        self.assertEqual(len(set(images)), 6)
        self.assertEqual(response.json()['skill_scan']['qwen_frames_submitted'], 6)

    def test_window_source_metadata_never_exposes_native_handle(self):
        source={'id':'c'*16,'kind':'window','handle':123,'bbox':[0,0,1280,576],
                'label':'窗口 · 手机','width':1280,'height':576,'minimized':False}
        with patch('gameplan.monitoring.screen_capture.sources',return_value=[source]),patch('gameplan.monitoring.screen_capture.capture') as capture:
            response=self.client.get('/api/screen/sources')
        result=response.json()['sources'][0]
        self.assertEqual(result['kind'],'window')
        self.assertNotIn('handle',result);self.assertNotIn('bbox',result)
        capture.assert_not_called()

    def test_model_warmup_is_local_and_loads_without_sending_a_picture(self):
        with patch('gameplan.web.monitor_app.post_json',return_value={'done':True}) as load, \
             patch('gameplan.web.monitor_app.vision_status',return_value={'ready':True}):
            response=self.client.post('/api/vision/warmup?model=qwen3-vl:8b-instruct')
            self.assertEqual(response.status_code,200)
            self.assertEqual(load.call_args.args[1]['prompt'],'')
            self.assertEqual(load.call_args.args[1]['options']['num_ctx'],16384)
            load.reset_mock()
            denied=self.client.post('/api/vision/warmup',headers={'origin':'https://other.example'})
            self.assertEqual(denied.status_code,403);load.assert_not_called()

    def test_standalone_import_does_not_load_workbench_database_or_reports(self):
        result = subprocess.run([sys.executable, "-c", "import gameplan.web.monitor_app,sys,json; print(json.dumps([name for name in ('gameplan.web.app','gameplan.core.storage','gameplan.core.reporting','gameplan.skills.cooldowns') if name in sys.modules]))"], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), [])
        self.assertEqual(self.client.get("/api/monitor/status").json()["application"], "gameplan-desktop-monitor")
        for route in ("/", "/monitor"):
            page = self.client.get(route)
            self.assertEqual(page.status_code, 200)
            self.assertIn("/static/workspace.js", page.text)
            self.assertNotIn("/static/app.js", page.text)
        self.assertEqual(self.client.post("/api/matches").status_code, 404)
        self.assertIn("/static/workspace.js", self.client.get("/monitor?mode=screen").text)
        self.assertIn("/static/workspace.js", self.client.get("/bp").text)
        self.assertIn("/static/monitor.js", self.client.get("/monitor?mode=screen&embedded=true").text)
        self.assertIn("/static/picture.js", self.client.get("/monitor?embedded=true").text)
        self.assertIn("/static/bp.js", self.client.get("/bp?embedded=true").text)

    def test_observation_uses_shared_pipeline_and_keeps_only_valid_game_fields(self):
        image = io.BytesIO();Image.new("RGB", (64,64), "navy").save(image, format="PNG")
        detected = {"phase":"bp","player_hero":"后羿","ally_roster":["后羿","未收录的昵称"],"enemy_roster":["铠"]}
        with patch('gameplan.monitoring.monitor_runtime.vision', return_value=(detected,"qwen3-vl:8b")), patch.object(monitor_runtime.advisor,"remember") as remember:
            result = self.client.post("/api/vision/observe",json={"match_id":"independent-test","image_base64":base64.b64encode(image.getvalue()).decode(),"captured_at":time.time(),"input_kind":"live","focus":"heroes"})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()["observation"]["ally_roster"],[])
        self.assertEqual(result.json()["observation"]["enemy_roster"],[])
        self.assertEqual(result.json()["focus"],"heroes")
        remember.assert_called_once()
        self.assertNotIn("image_base64",str(remember.call_args))

    def test_remote_and_cross_origin_cannot_capture_or_request_model_analysis(self):
        payloads={"/api/screen/frame":{"source_id":"a"*16},"/api/vision/observe":{"match_id":"test","image_base64":"AAAA"},"/api/coach/analysis":{"match_id":"test","frame_id":"b"*32}}
        with patch('gameplan.monitoring.screen_capture.capture') as capture,patch('gameplan.web.monitor_app.observe') as observe,patch.object(monitor_app.advisor,"analyze") as analyze:
            with TestClient(monitor_app.app,base_url="http://127.0.0.1",client=("192.168.1.20",50000)) as remote:
                for route,payload in payloads.items():
                    self.assertEqual(remote.post(route,json=payload).status_code,403)
                    self.assertEqual(self.client.post(route,json=payload,headers={"origin":"https://unrelated.example"}).status_code,403)
            capture.assert_not_called();observe.assert_not_called();analyze.assert_not_called()

    def test_lossless_video_frame_remains_valid_when_reused_as_history(self):
        # Detailed 1280px video PNGs can exceed the old 2M base64 history limit.
        pixels = random.Random(42).randbytes(1280 * 576 * 3)
        image = io.BytesIO()
        Image.frombytes("RGB", (1280, 576), pixels).save(image, format="PNG")
        encoded = "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()
        self.assertGreater(len(encoded), 2000000)
        payload = {"match_id": "png-video", "input_kind": "video", "focus": "skills",
                   "captured_at": 100, "image_base64": encoded}
        with patch('gameplan.web.monitor_app.observe', return_value={"accepted": True}) as observe:
            first = self.client.post("/api/vision/observe", json=payload)
            self.assertEqual(first.status_code, 200)
            payload.update(captured_at=101, recent_frames=[{"captured_at": 100, "image_base64": encoded}])
            second = self.client.post("/api/vision/observe", json=payload)
            self.assertEqual(second.status_code, 200)
            request = observe.call_args.args[0]
            self.assertEqual(request.recent_frames[0].image_base64, request.image_base64)
            self.assertEqual(base64.b64decode(request.recent_frames[0].image_base64.split(",")[1]), image.getvalue())

    def test_history_still_rejects_oversized_images(self):
        from gameplan.core.models import MAX_VISION_IMAGE_BASE64, VisionRequest
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            VisionRequest(image_base64="AAAA", captured_at=100,
                          recent_frames=[{"captured_at": 99, "image_base64": "A" * (MAX_VISION_IMAGE_BASE64 + 1)}])

    def test_capture_failure_is_actionable_without_switching_screens(self):
        with patch('gameplan.monitoring.screen_capture.capture',side_effect=monitor_app.screen_capture.CaptureError("请解锁桌面")) as capture:
            result=self.client.post("/api/screen/frame",json={"source_id":"a"*16})
        self.assertEqual(result.status_code,503)
        self.assertIn("解锁",result.json()["detail"])
        capture.assert_called_once_with("a"*16,None)

    def test_capture_region_is_forwarded_to_desktop_capture(self):
        region={"x":0,"y":0,"width":0.5,"height":1}
        with patch('gameplan.monitoring.screen_capture.capture',return_value={"image_base64":"data:image/png;base64,AAAA"}) as capture:
            result=self.client.post("/api/screen/frame",json={"source_id":"a"*16,"roi":region})
        self.assertEqual(result.status_code,200,result.text)
        forwarded=capture.call_args.args[1]
        self.assertEqual(forwarded.model_dump(),region)

    def test_missing_external_key_keeps_local_advice_available(self):
        monitor_app.advisor.remember("c"*32,"independent-test",{"phase":"bp","ally_roster":["后羿"],"enemy_roster":["铠"],"player_hero":"后羿"},time.time(),"live")
        with patch('gameplan.ai.advisor.provider_config',return_value={"key":"","base":"https://api.deepseek.com","model":"deepseek-v4-flash"}):
            result=self.client.post("/api/coach/analysis",json={"match_id":"independent-test","frame_id":"c"*32})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()["source"],"local_rules")
        self.assertEqual(result.json()["status"],"ok")
        self.assertIn("选人",result.json()["summary"])

    def test_realtime_advice_does_not_wait_for_external_model(self):
        monitor_app.advisor.remember("d"*32,"local-realtime",{"phase":"loading","ally_roster":["后羿"],"enemy_roster":["铠"],"player_hero":None},time.time(),"live")
        with patch('gameplan.ai.advisor.post_json',side_effect=AssertionError("Unexpected network call")) as external:
            result=self.client.post("/api/coach/analysis",json={"match_id":"local-realtime","frame_id":"d"*32,"side":"a","player":"后羿","lane":"发育路"})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()["source"],"local_rules")
        self.assertIn("发育路",result.json()["summary"])
        external.assert_not_called()

    def test_selected_erin_receives_opponent_advice_instead_of_gongsunli_pick(self):
        frame_id = 'e' * 32
        monitor_app.advisor.remember(frame_id, 'erin-selected', {
            'phase': 'bp', 'ally_roster': ['艾琳', '牛魔'],
            'enemy_roster': ['瑶', '吕布', '嬴政'], 'player_hero': '艾琳',
        }, time.time(), 'live')
        result = self.client.post('/api/coach/analysis', json={
            'match_id': 'erin-selected', 'frame_id': frame_id,
        }).json()
        self.assertEqual(result['player'], '艾琳')
        self.assertIn('建议对象：艾琳', result['summary'])
        self.assertIn('敌方吕布', result['summary'])
        self.assertNotIn('公孙离', result['summary'])
        self.assertEqual(result['counter_items'], [])

    def test_unknown_player_is_not_inferred_from_a_counter_pick(self):
        frame_id = 'f' * 32
        monitor_app.advisor.remember(frame_id, 'erin-unknown', {
            'phase': 'bp', 'ally_roster': ['艾琳', '牛魔'],
            'enemy_roster': ['吕布'], 'player_hero': None,
        }, time.time(), 'live')
        payload = {'match_id': 'erin-unknown', 'frame_id': frame_id, 'side': 'a'}
        result = self.client.post('/api/coach/analysis', json=payload).json()
        self.assertIsNone(result['player'])
        self.assertIn('操控英雄尚未确认', result['summary'])
        self.assertNotIn('公孙离', result['summary'])
        corrected = self.client.post('/api/coach/analysis', json={**payload, 'player': '艾琳'}).json()
        self.assertEqual(corrected['player'], '艾琳')
        self.assertIn('建议对象：艾琳', corrected['summary'])
        self.assertNotIn('公孙离', corrected['summary'])
