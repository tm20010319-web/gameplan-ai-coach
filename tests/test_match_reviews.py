import asyncio
import base64
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from PIL import Image

from gameplan.ai import match_review as ai
from gameplan.core import review_store as store
from gameplan.core.models import VisionRequest
from gameplan.monitoring import review_runtime as runtime
from gameplan.web.monitor_app import app


def observation(game="hok", hero="后羿", **extra):
    return ai.normalize({"game": game, "phase": "in_game", "confidence": .97,
        "player_hero": hero, "player_confidence": .97, "viewpoint": "player", "game_time_s": 300,
        "facts": [{"kind": "position", "text": "操控英雄残血站在敌方塔前，附近未见队友", "confidence": .96}], **extra})


def pixels():
    result = io.BytesIO(); Image.new("RGB", (128, 72), "navy").save(result, format="JPEG")
    return result.getvalue()


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(store, "DATABASE_PATH", Path(self.temp.name) / "reviews.sqlite3")
        self.patch.start()
        self.image = pixels()
        self.t = 1000

    def tearDown(self):
        self.patch.stop(); self.temp.cleanup()

    def feed(self, item=None, stream="test", step=15):
        self.t += step
        return store.ingest(stream, item or observation(), self.t, self.image)

    def playing(self, game="hok", hero="后羿"):
        item = observation(game, hero)
        for _ in range(4):
            state = self.feed(item)
        return state["match_id"]

    def end(self, game="hok"):
        terminal = observation(game, phase="result", outcome="win", end_text="VICTORY", viewpoint="unknown")
        self.feed(terminal)
        return self.feed(terminal)

    def test_two_games_have_separate_identity_catalogs_and_uncertain_facts_are_removed(self):
        self.assertEqual(observation("lol", "寒冰射手")["player_hero"], "艾希")
        self.assertIsNone(observation("hok", "艾希")["player_hero"])
        self.assertEqual(observation(player_confidence=.5)["facts"], [])
        self.assertEqual(observation(viewpoint="spectator")["facts"], [])
        self.assertEqual(observation(confidence=.6)["game"], "unknown")

    def test_scoreboard_death_or_non_game_does_not_end_match(self):
        mid = self.playing()
        for item in [observation(outcome="win", end_text="胜利"), observation(phase="not_game"),
                     observation(phase="result", outcome="win", end_text="ACE"), observation(confidence=.5)]:
            self.feed(item); self.feed(item)
            self.assertEqual(store.read(mid)["status"], "recording")

    def test_ending_needs_two_consistent_observations_and_only_finalizes_once(self):
        mid = self.playing()
        ending = observation(phase="result", outcome="win", end_text="胜利")
        self.feed(ending)
        self.assertEqual(store.read(mid)["status"], "recording")
        self.feed(observation(phase="unknown"))
        self.feed(ending)
        self.assertEqual(store.read(mid)["status"], "recording")
        state = self.feed(ending)
        self.assertEqual(store.read(mid)["status"], "pending")
        count = store.read(mid)["samples"]
        self.feed(ending); self.feed(ending)
        self.assertEqual(store.history()["total"], 1)
        self.assertEqual(store.read(mid)["samples"], count)
        self.assertEqual(state["match_id"], mid)

    def test_terminal_without_personal_gameplay_never_creates_review(self):
        self.end(); self.end()
        self.assertEqual(store.history()["total"], 0)
        for _ in range(4): self.feed(observation(viewpoint="spectator"))
        self.end()
        self.assertEqual(store.history()["total"], 0)

    def test_duplicate_out_of_order_or_too_close_terminal_does_not_confirm(self):
        mid = self.playing()
        ending = observation(phase="result", outcome="win", end_text="胜利")
        self.feed(ending)
        store.ingest("test", ending, self.t, self.image)
        store.ingest("test", ending, self.t-30, self.image)
        self.feed(ending, step=.1)
        self.assertEqual(store.read(mid)["status"], "recording")
        self.feed(ending)
        self.assertEqual(store.read(mid)["status"], "pending")

    def test_new_match_same_hero_and_game_gets_its_own_id(self):
        first = self.playing(); self.end()
        second = self.playing()
        self.assertNotEqual(first, second)
        self.assertEqual(store.read(first)["status"], "pending")
        self.assertEqual(store.read(second)["status"], "recording")

    def test_clock_reset_after_missed_ending_is_confirmed_and_previous_is_preserved(self):
        first = self.playing()
        self.feed(observation(game_time_s=1600))
        self.feed(observation(game_time_s=300))
        self.assertEqual(store.stream("test")["match_id"], first)
        self.feed(observation(game_time_s=314))
        second = store.stream("test")["match_id"]
        self.assertNotEqual(first, second)
        self.assertEqual(store.read(first)["status"], "incomplete")

    def test_switching_games_does_not_mix_samples_or_rebind_hero(self):
        first = self.playing()
        self.feed(observation(hero="蔡文姬")); self.feed(observation(hero="蔡文姬"))
        self.assertEqual(store.read(first)["hero"], "后羿")
        second = self.playing("lol", "艾希")
        self.assertNotEqual(first, second)
        self.assertEqual(store.read(second)["hero"], "艾希")
        self.assertEqual({f["observation"]["game"] for f in store.read(first, True)["frames"]}, {"hok"})
        self.assertEqual({f["observation"]["game"] for f in store.read(second, True)["frames"]}, {"lol"})

    def test_history_frames_and_stream_survive_new_database_connections(self):
        mid = self.playing(); self.end()
        row = store.read(mid, True)
        self.assertEqual(store.stream("test")["match_id"], mid)
        self.assertEqual(store.history(1, 0)["items"][0]["id"], mid)
        self.assertEqual(store.history(1, 1)["items"], [])
        self.assertEqual(store.image(row["frames"][0]["id"]), self.image)

    def test_bounded_frame_retention_and_personal_identity_missing(self):
        with patch.object(store, "MAX_FRAMES", 3):
            mid = self.playing()
            for _ in range(8): self.feed()
            self.assertEqual(len(store.read(mid, True)["frames"]), 3)
            self.assertGreater(store.read(mid)["samples"], 3)
        with patch.object(ai, "post_json") as provider:
            result = ai.analyze({"hero": None}, [])
        self.assertEqual(result["status"], "insufficient"); provider.assert_not_called()

    def test_parallel_job_claim_is_exactly_once_and_old_completion_cannot_overwrite(self):
        mid = self.playing(); self.end()
        with ThreadPoolExecutor(max_workers=4) as pool:
            tokens = list(pool.map(lambda _: store.claim(mid), range(8)))
        self.assertEqual(sum(t is not None for t in tokens), 1)
        token = next(t for t in tokens if t)
        store.finish(mid, "wrong-token", {"status": "ready"})
        self.assertEqual(store.read(mid)["status"], "analyzing")
        store.finish(mid, token, {"status": "failed", "points": []})
        self.assertIsNone(store.claim(mid))
        self.assertIsNotNone(store.claim(mid, retry=True))

    def test_abandoned_job_can_be_recovered_and_completed_jobs_not_resubmitted(self):
        mid = self.playing(); self.end()
        old = store.claim(mid)
        with store.database() as db:
            db.execute("UPDATE review_matches SET updated=? WHERE id=?", (time.time()-150, mid))
        self.assertIn(mid, store.pending())
        new = store.claim(mid)
        self.assertNotEqual(old, new)
        store.finish(mid, new, {"status": "ready", "points": []})
        self.assertIsNone(store.claim(mid, retry=True))

    def test_deepseek_only_receives_personal_structured_facts_and_bounded_response(self):
        mid = self.playing(); self.end()
        match = store.read(mid, True); eid = match["frames"][0]["id"]
        answer = {"points": [{"issue": "这次画面中残血压塔，退路有限。", "advice": "下局残血先回安全位置，再等队友一起推进。", "evidence_ids": [eid]}]}
        with patch.object(ai, "provider_config", return_value={"key": "secret", "base": "https://example.test", "model": "deepseek-test"}), \
             patch.object(ai, "post_json", return_value={"choices": [{"message": {"content": json.dumps(answer)}}]}) as provider:
            result = ai.analyze(match, match["frames"])
        self.assertEqual(result["status"], "ready")
        payload = provider.call_args.args[1]
        text = json.dumps(payload)
        self.assertNotIn("image_base64", text); self.assertNotIn("secret", text)
        self.assertEqual(len(result["points"]), 1)
        facts = json.loads(payload["messages"][1]["content"])
        self.assertEqual(facts["hero"], "后羿")
        self.assertTrue(all(e["facts"] for e in facts["evidence"]))

    def test_unknown_citation_model_failure_and_missing_key_are_explicit(self):
        mid = self.playing(); match = store.read(mid, True)
        for returned in [{"points": [{"issue": "这次压线过深", "advice": "先确认队友位置", "evidence_ids": ["invented"]}]},
                         {"points": [{"issue": "错"*46, "advice": "先确认队友位置", "evidence_ids": [match["frames"][0]["id"]]}]}]:
            with patch.object(ai, "provider_config", return_value={"key": "secret", "base": "https://example.test", "model": "deepseek-test"}), \
                 patch.object(ai, "post_json", return_value={"choices": [{"message": {"content": json.dumps(returned)}}]}):
                self.assertEqual(ai.analyze(match, match["frames"])["status"], "failed")
        with patch.object(ai, "provider_config", return_value={"key": ""}), patch.object(ai, "post_json") as provider:
            self.assertEqual(ai.analyze(match, match["frames"])["status"], "failed")
            provider.assert_not_called()

    def test_frame_and_history_endpoints_are_local_only(self):
        mid = self.playing(); frame = store.read(mid, True)["frames"][0]["id"]
        with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
            self.assertEqual(client.get("/api/reviews").json()["total"], 1)
            self.assertEqual(client.get(f"/api/reviews/{mid}").status_code, 200)
            self.assertEqual(client.get(f"/api/reviews/frames/{frame}").content, self.image)
            self.assertEqual(client.get("/api/reviews/missing").status_code, 404)
            self.assertEqual(client.get("/api/reviews", headers={"origin": "https://elsewhere.test"}).status_code, 403)
        with TestClient(app, base_url="http://127.0.0.1", client=("192.168.1.20", 50000)) as client:
            self.assertEqual(client.get(f"/api/reviews/frames/{frame}").status_code, 403)

    def test_manual_new_stream_preserves_incomplete_history(self):
        mid = self.playing()
        store.close_stream("test")
        self.assertEqual(store.read(mid)["status"], "incomplete")
        self.assertGreater(len(store.read(mid, True)["frames"]), 0)

    def test_auto_review_transport_routes_lol_without_using_hok_detector(self):
        payload = {"match_id": "live-lol", "auto_review": True, "input_kind": "live", "captured_at": time.time(),
                   "image_base64": base64.b64encode(self.image).decode()}
        with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client, \
             patch.object(ai, "recognize", return_value=observation("lol", "艾希")), \
             patch("gameplan.web.monitor_app.observe", new_callable=AsyncMock) as legacy:
            response = client.post("/api/vision/observe", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["game"], "lol")
        legacy.assert_not_called()

    def test_hok_uses_original_detector_between_review_samples(self):
        req = VisionRequest(match_id="runtime-hok", input_kind="live", auto_review=True, captured_at=time.time(), image_base64=base64.b64encode(self.image).decode())
        legacy = AsyncMock(return_value={"observation": {"phase": "in_game"}})
        with patch.object(ai, "recognize", return_value=observation()) as vision:
            asyncio.run(runtime.observe(req, legacy))
            asyncio.run(runtime.observe(req.model_copy(update={"captured_at": time.time()}), legacy))
        self.assertEqual(vision.call_count, 1)
        legacy.assert_awaited_once()

    def test_reset_during_visual_inference_discards_late_frame(self):
        from gameplan.monitoring.monitor_runtime import reset_match
        req = VisionRequest(match_id="late-reset", input_kind="live", auto_review=True, captured_at=time.time(), image_base64=base64.b64encode(self.image).decode())
        def infer(*args):
            reset_match("late-reset")
            return observation()
        with patch.object(ai, "recognize", side_effect=infer):
            with self.assertRaises(Exception) as error:
                asyncio.run(runtime.observe(req, AsyncMock()))
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(store.history()["total"], 0)

    def test_automatic_end_to_card_and_history_generates_once_for_both_games(self):
        for game, hero in (("hok", "后羿"), ("lol", "艾希")):
            play = observation(game, hero)
            end = observation(game, hero, phase="result", outcome="loss", end_text="失败", viewpoint="unknown")
            payload = {"match_id": "e2e-"+game, "auto_review": True, "input_kind": "live",
                       "image_base64": base64.b64encode(self.image).decode()}
            result = {"status": "ready", "source": "deepseek", "model": "test", "points": []}
            with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client, \
                 patch.object(ai, "recognize", side_effect=[play]*4+[end]*4), \
                 patch.object(ai, "analyze", return_value=result) as analyze:
                started = time.time()-180
                for index in range(8):
                    payload["captured_at"] = started + index*15
                    response = client.post("/api/vision/observe", json=payload)
                    self.assertEqual(response.status_code, 200, response.text)
                mid = response.json()["review"]["id"]
                deadline = time.monotonic()+3
                while store.read(mid)["status"] == "analyzing" and time.monotonic()<deadline:
                    time.sleep(.01)
                card = client.get("/api/reviews/streams/"+payload["match_id"]).json()["match"]
                self.assertEqual(card["status"], "ready")
                self.assertEqual(card["hero"], hero)
                self.assertEqual(card["outcome"], "loss")
                analyze.assert_called_once()
                self.assertIn(mid, [m["id"] for m in client.get("/api/reviews").json()["items"]])


if __name__ == "__main__":
    unittest.main()
