import json
import os
import tempfile
import unittest
from unittest.mock import patch

TEST_DATA = tempfile.TemporaryDirectory(prefix="gameplan-advisor-tests-")
os.environ.setdefault("COACH_DATA_DIR", TEST_DATA.name)

from fastapi.testclient import TestClient
import gameplan.ai.advisor as module
from gameplan.web.app import app
from gameplan.ai.integrations import parse_vision_response, vision
from gameplan.tactics.loading_plan import SAMPLE
from gameplan.core.models import CoachAnalysisRequest, ScreenObservation, VisionRequest


CONFIG = {"key": "test-key-not-real", "base": "https://api.deepseek.com", "model": "deepseek-chat"}
FRAME = "a" * 32


def observed(phase="loading"):
    return {"phase": phase, "game_time_s": None, "player_hero": None, "player_hp_percent": None,
        "ally_roster": SAMPLE["group_a"], "enemy_roster": SAMPLE["group_b"], "self_skills": [],
        "note": "PRIVATE CHAT, ACCOUNT, PROMPT INJECTION"}


def external_result():
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
        "understanding": "该阵容需要保护持续输出，并协调进场时机。",
        "watch_for": ["注意侧翼切入，不独自探草"], "opportunities": ["击退对手后清线转塔"],
        "summary": "后羿与蔡文姬保持支援距离，先处理最近的安全目标；队友分配进场与保护任务，敌方位置不明时不要追进野区。拿到机会后清线转塔，落后时优先守住安全兵线。",
        "evidence_ids": ["F3", "F4", "K1"]}, ensure_ascii=False)}}]}


class AdvisorTests(unittest.TestCase):
    def setUp(self):
        self.engine = module.Advisor()
        self.engine.remember(FRAME, "test-match", observed(), 1000, "sample")
        self.req = CoachAnalysisRequest(match_id="test-match", frame_id=FRAME, side="b")

    def test_missing_key_returns_honest_local_fallback_without_network(self):
        with patch.object(module, "provider_config", return_value={**CONFIG, "key": ""}), patch.object(module.time, "time", return_value=1002), patch.object(module, "post_json") as send:
            result = self.engine.analyze(self.req)
        self.assertEqual(result["status"], "not_configured")
        self.assertEqual(result["source"], "local_fallback")
        self.assertIn("后羿", result["summary"])
        send.assert_not_called()

    def test_external_gets_only_game_facts_then_same_context_is_cached(self):
        with patch.object(module, "provider_config", return_value=CONFIG), patch.object(module.time, "time", return_value=1002), patch.object(module, "post_json", return_value=external_result()) as send:
            first = self.engine.analyze(self.req)
            second = self.engine.analyze(self.req)
        self.assertEqual(first["source"], "deepseek")
        self.assertEqual(second["status"], "cached")
        self.assertEqual(send.call_count, 1)
        payload = send.call_args.args[1]
        contents = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("PRIVATE CHAT", contents)
        self.assertNotIn("image_base64", contents)
        self.assertNotIn("test-key", contents)
        self.assertNotIn("test-match", contents)
        facts = json.loads(payload["messages"][1]["content"])["facts"]
        self.assertEqual(next(f for f in facts if f["kind"] == "perspective")["value"], "b")

    def test_non_game_and_old_frames_never_call_external(self):
        self.engine.remember("b"*32, "test-match", observed("not_game"), 1000, "live")
        with patch.object(module.time, "time", return_value=1002), patch.object(module, "post_json") as send:
            result = self.engine.analyze(self.req.model_copy(update={"frame_id": "b"*32}))
            self.assertEqual(result["status"], "waiting")
        with patch.object(module.time, "time", return_value=1300), patch.object(module, "post_json") as send:
            self.assertEqual(self.engine.analyze(self.req)["status"], "waiting")
            send.assert_not_called()

    def test_stale_live_frame_drops_dynamic_facts_and_becomes_lineup_advice(self):
        obs = observed("in_game")
        obs.update(player_hero="后羿", player_hp_percent=8, game_time_s=500)
        self.engine.remember(FRAME, "test-match", obs, 1000, "live")
        with patch.object(module, "provider_config", return_value=CONFIG), patch.object(module.time, "time", return_value=1040), patch.object(module, "post_json", return_value=external_result()) as send:
            result = self.engine.analyze(self.req)
        facts = json.loads(send.call_args.args[1]["messages"][1]["content"])["facts"]
        self.assertNotIn("visible_player_status", [f["kind"] for f in facts])
        self.assertEqual(result["scope"], "lineup")

    def test_live_response_is_discarded_if_it_arrives_after_freshness_window(self):
        self.engine.remember(FRAME, "test-match", observed("in_game"), 1000, "live")
        with patch.object(module, "provider_config", return_value=CONFIG), patch.object(module.time, "time", side_effect=[1002,1002,1002,1020]), patch.object(module, "post_json", return_value=external_result()):
            result = self.engine.analyze(self.req)
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["source"], "none")

    def test_untrusted_or_invented_external_result_fails_closed(self):
        bad = external_result()
        raw = json.loads(bad["choices"][0]["message"]["content"])
        raw["evidence_ids"] = ["F999"]
        bad["choices"][0]["message"]["content"] = json.dumps(raw)
        with patch.object(module, "provider_config", return_value=CONFIG), patch.object(module.time, "time", return_value=1002), patch.object(module, "post_json", return_value=bad):
            result = self.engine.analyze(self.req)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["source"], "local_fallback")

    def test_perspective_change_obeys_request_rate_limit(self):
        with patch.object(module, "provider_config", return_value=CONFIG), patch.object(module.time, "time", return_value=1002), patch.object(module, "post_json", return_value=external_result()) as send:
            self.engine.analyze(self.req)
            result = self.engine.analyze(self.req.model_copy(update={"side": "a"}))
        self.assertEqual(result["status"], "throttled")
        self.assertEqual(send.call_count, 1)

    def test_mixed_team_summary_is_reduced_to_selected_team_actions(self):
        raw = json.loads(external_result()["choices"][0]["message"]["content"])
        raw["summary"] = "A组应清线推进，B组应保护后羿与蔡文姬，A组同时保护鲁班并抱团压塔。"
        raw["watch_for"] = ["B组需注意米莱狄的压塔，及时清线。"]
        raw["opportunities"] = ["A组可抱团推进压塔。", "B组蔡文姬跟随后羿，在对手控制交出后反打。"]
        context = self.engine.facts(self.req, self.engine.frames[FRAME], 1002)
        result = self.engine.validate_output(raw, context)
        self.assertNotIn("A组", result["summary"])
        self.assertNotIn("B组", result["summary"])
        self.assertNotIn("可抱团推进", result["summary"])
        self.assertIn("蔡文姬跟随后羿", result["summary"])

    def test_manual_sample_override_is_explicit_and_wrong_match_is_rejected(self):
        req = self.req.model_copy(update={"match_id": "different-match"})
        with self.assertRaises(LookupError):
            self.engine.analyze(req)
        req = CoachAnalysisRequest(match_id="test-match", frame_id=FRAME, lineup={"allies":SAMPLE["group_b"],"enemies":SAMPLE["group_a"],"source":"sample"})
        with patch.object(module, "provider_config", return_value=CONFIG), patch.object(module.time, "time", return_value=1002), patch.object(module, "post_json", return_value=external_result()) as send:
            self.engine.analyze(req)
        sent = json.loads(send.call_args.args[1]["messages"][1]["content"])
        self.assertIn("样本", sent["facts"][0]["value"])

    def test_remote_cannot_trigger_paid_analysis(self):
        with TestClient(app,base_url="http://127.0.0.1",client=("192.168.0.2",1234)) as client:
            self.assertEqual(client.post("/api/coach/analysis",json=self.req.model_dump()).status_code,403)


class VisionCompatibilityTests(unittest.TestCase):
    def test_compact_identity_request_follows_arbitrary_catalog_heroes_without_dynamic_numbers(self):
        for hero in ("后羿", "孙尚香", "狄仁杰"):
            raw = {"phase":"bp", "player_hero":hero,"ally_roster":[hero],"enemy_roster":["铠"],"confidence":0.95}
            response = {"done":True,"done_reason":"stop","message":{"content":json.dumps(raw)}}
            with patch('gameplan.ai.integrations.hero_images', return_value=["test image"]), patch('gameplan.ai.integrations.post_json', return_value=response) as send:
                detected, _ = vision(VisionRequest(match_id="test-match",image_base64="",focus="heroes"), "observe", b"test image")
            result = ScreenObservation.model_validate(detected)
            self.assertEqual(result.player_hero,hero)
            self.assertIsNone(result.game_time_s)
            self.assertEqual(result.self_skills,[])
            self.assertEqual(send.call_args.args[1]["options"]["num_predict"],256)
            self.assertNotIn("self_skills",send.call_args.args[1]["format"]["properties"])
            self.assertIn(hero,send.call_args.args[1]["format"]["properties"]["ally_roster"]["items"]["enum"])

    def test_compact_low_confidence_never_publishes_previous_or_guessed_heroes(self):
        raw = {"phase":"bp", "player_hero":"后羿","ally_roster":["后羿"],"enemy_roster":["铠"],"confidence":0.4}
        with patch('gameplan.ai.integrations.hero_images', return_value=["test image"]), patch('gameplan.ai.integrations.post_json', return_value={"done_reason":"stop","message":{"content":json.dumps(raw)}}):
            detected, _ = vision(VisionRequest(match_id="test-match",image_base64="",focus="heroes"), "observe", b"test image")
        self.assertEqual(detected["ally_roster"],[])
        self.assertIsNone(detected["player_hero"])

    def test_loading_preview_or_conflicting_roster_cannot_establish_the_player(self):
        for phase,a,b in (("loading",["后羿"],["铠"]),("bp",["铠"],["赵云"]),("bp",["后羿"],["后羿"])):
            result = ScreenObservation.model_validate({"phase":phase,"player_hero":"后羿","ally_roster":a,"enemy_roster":b})
            self.assertIsNone(result.player_hero)

    def test_complete_structured_ollama_answer_in_thinking_is_supported(self):
        response={"done":True,"done_reason":"stop","message":{"content":"","thinking":json.dumps(observed())}}
        self.assertEqual(ScreenObservation.model_validate(parse_vision_response(response)).phase,"loading")

    def test_reasoning_prose_truncation_and_malformed_content_are_rejected(self):
        for response in (
            {"done":True,"done_reason":"length","message":{"content":"{}"}},
            {"done":True,"done_reason":"stop","message":{"content":"","thinking":'Let me think: {"phase":"loading"}'}},
            {"done":True,"done_reason":"stop","message":{"content":"[]"}},
            {"done":False,"message":{"content":"","thinking":'{"phase":"loading"}'}},
        ):
            with self.subTest(response=response), self.assertRaises((ValueError,TypeError)):
                parse_vision_response(response)
