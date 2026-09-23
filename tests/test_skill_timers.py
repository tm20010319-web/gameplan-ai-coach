import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

# Keep all regression data away from the user's matches and reports.
TEST_DATA = tempfile.TemporaryDirectory(prefix="gameplan-timers-")
os.environ["COACH_DATA_DIR"] = TEST_DATA.name

import gameplan.web.app as app
import gameplan.core.storage as storage


class SkillTimerTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app)
        self.match = self.client.post("/api/matches").json()["match_id"]
        self.path = f"/api/matches/{self.match}/skill-timers"

    def tearDown(self):
        self.client.close()

    def register(self, **changes):
        payload = {"hero": "王昭君", "slot": 3, "seconds": 30, **changes}
        response = self.client.post(self.path, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_backdated_cast_and_persistence(self):
        with patch('gameplan.skills.cooldowns.time.time', return_value=1000):
            data = self.register(elapsed_s=5, game_time_s=285)
        item = data["timers"][0]
        self.assertEqual(item["cast_epoch"], 995)
        self.assertEqual(item["recorded_epoch"], 1000)
        self.assertEqual(item["ready_epoch"], 1025)
        self.assertEqual(item["ready_game_time_s"], 315)
        self.assertEqual(item["ready_epoch"] - data["server_epoch"], 25)
        # Read through a fresh DB connection and a new client, as after a reload.
        self.assertEqual(storage.memory(self.match)["skill_timers"], data["timers"])
        with TestClient(app.app) as reloaded:
            snapshot = reloaded.get(f"/api/matches/{self.match}").json()["skill_timers"]
        self.assertEqual(snapshot["timers"], data["timers"])
        self.assertEqual(snapshot["history"], data["history"])

    def test_remaining_does_not_invent_a_cast(self):
        with patch('gameplan.skills.cooldowns.time.time', return_value=1000):
            item = self.register(mode="remaining", seconds=12)["timers"][0]
        self.assertIsNone(item["cast_epoch"])
        self.assertIsNone(item["cast_game_time_s"])
        self.assertIsNone(item["ready_game_time_s"])
        self.assertEqual(item["ready_epoch"], 1012)

    def test_recast_and_team_slot_isolation(self):
        first = self.register()["timers"][0]
        self.register(side="ally")
        self.register(slot=1)
        data = self.register(seconds=60)
        self.assertEqual(len(data["timers"]), 3)
        self.assertNotIn(first["id"], [item["id"] for item in data["timers"]])
        self.assertEqual(data["history"][-1]["ended_reason"], "superseded")
        self.assertEqual(len(data["history"]), 4)
        self.assertEqual(data["revision"], 4)

    def test_match_isolation_and_clear_preserves_history(self):
        self.register()
        other = self.client.post("/api/matches").json()["match_id"]
        self.assertEqual(self.client.get(f"/api/matches/{other}/skill-timers").json()["timers"], [])
        data = self.client.post(self.path + "/clear").json()
        self.assertEqual(data["timers"], [])
        self.assertEqual(data["history"][0]["ended_reason"], "cleared")
        self.assertEqual(len(data["history"]), 1)

    def test_cancel_only_one_timer(self):
        first = self.register()["timers"][0]
        self.register(slot=1)
        data = self.client.post(self.path + f"/{first['id']}/cancel").json()
        self.assertEqual(len(data["timers"]), 1)
        self.assertEqual(data["timers"][0]["slot"], 1)
        self.assertEqual(data["history"][-1]["ended_reason"], "cancelled")
        self.assertEqual(self.client.post(self.path + f"/{first['id']}/cancel").status_code, 404)

    def test_expired_backfill_keeps_original_deadline(self):
        with patch('gameplan.skills.cooldowns.time.time', return_value=1000):
            data = self.register(seconds=10, elapsed_s=20)
        self.assertEqual(data["timers"][0]["ready_epoch"], 990)
        self.assertLess(data["timers"][0]["ready_epoch"], data["server_epoch"])

    def test_invalid_input_never_creates_timers(self):
        for changes in [
            {"seconds": 0}, {"seconds": -1}, {"seconds": 3601}, {"seconds": "NaN"},
            {"elapsed_s": -1}, {"elapsed_s": 3601}, {"game_time_s": 14401},
            {"game_time_s": "Infinity"}, {"mode": "remaining", "elapsed_s": 1},
            {"mode": "remaining", "game_time_s": 0}, {"side": "unknown"},
            {"hero": "未收录英雄"}, {"slot": 0}, {"slot": 9},
        ]:
            with self.subTest(changes=changes):
                response = self.client.post(self.path, json={"hero": "王昭君", "slot": 3, "seconds": 30, **changes})
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get(self.path).json()["history"], [])

    def test_missing_official_data_still_allows_manual_confirmation(self):
        catalog = {"heroes": [{"hero": "廉颇", "skills": []}]}
        with patch('gameplan.web.app.get_skills', return_value=catalog):
            item = self.register(hero="廉颇")["timers"][0]
        self.assertEqual(item["skill"], "3 技能")
        self.assertEqual(item["seconds"], 30)

    def test_passive_and_nonexistent_slots_rejected(self):
        catalog = {"heroes": [{"hero": "王昭君", "skills": [{"slot": 1, "passive": True}]}]}
        with patch('gameplan.web.app.get_skills', return_value=catalog):
            for slot in (1, 3):
                self.assertEqual(self.client.post(self.path, json={"hero": "王昭君", "slot": slot, "seconds": 30}).status_code, 422)

    def test_websocket_snapshot_registration_and_cancel(self):
        with self.client.websocket_connect(f"/ws/{self.match}") as ws:
            self.assertEqual(ws.receive_json()["data"]["skill_timers"]["timers"], [])
            data = self.register()
            event = ws.receive_json()
            self.assertEqual(event["type"], "skill_timers")
            self.assertEqual(event["data"], data)
            item_id = data["timers"][0]["id"]
            self.client.post(self.path + f"/{item_id}/cancel")
            self.assertEqual(ws.receive_json()["data"]["timers"], [])

    def test_history_is_bounded(self):
        for _ in range(102):
            data = self.register()
        self.assertEqual(len(data["history"]), 100)
        self.assertEqual(len(data["timers"]), 1)

    def test_report_ends_timers_without_erasing_history(self):
        self.register()
        with patch('gameplan.web.app.render'), patch('gameplan.web.app.share_base', return_value="http://localhost"):
            response = self.client.post("/api/post_game/review", json={"match_id": self.match, "result": {"result": "未知"}})
        self.assertEqual(response.status_code, 200, response.text)
        data = self.client.get(self.path).json()
        self.assertEqual(data["timers"], [])
        self.assertEqual(data["history"][0]["ended_reason"], "match_ended")


if __name__ == "__main__":
    try:
        unittest.main()
    finally:
        TEST_DATA.cleanup()
