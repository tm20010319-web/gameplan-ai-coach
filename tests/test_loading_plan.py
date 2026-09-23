import os
import tempfile
import unittest

TEST_DATA = tempfile.TemporaryDirectory(prefix="gameplan-loading-tests-")
os.environ.setdefault("COACH_DATA_DIR", TEST_DATA.name)

from fastapi.testclient import TestClient
from gameplan.web.app import app
from gameplan.tactics.loading_plan import SAMPLE, build_plan


class LoadingPlanTests(unittest.TestCase):
    def test_sample_sides_have_different_actionable_plans(self):
        upper, lower = SAMPLE["group_a"], SAMPLE["group_b"]
        a, b = build_plan(upper, lower), build_plan(lower, upper)
        self.assertIn("鲁班", a["title"])
        self.assertIn("后羿", b["title"])
        self.assertIn("米莱狄", a["advantage"])
        self.assertIn("先约定", a["opening"])
        self.assertIn("先约定", b["opening"])
        self.assertIn("蔡文姬", str(a["cautions"]))
        self.assertIn("米莱狄", str(b["cautions"]))
        self.assertEqual(b["title"], build_plan(list(reversed(lower)), upper)["title"])

    def test_personal_advice_and_no_unselected_hero_instructions(self):
        plan = build_plan(SAMPLE["group_b"], SAMPLE["group_a"], "蔡文姬", "sample")
        self.assertEqual(plan["personal"]["hero"], "蔡文姬")
        self.assertIn("后羿", plan["personal"]["text"])
        short = build_plan(["后羿", "蔡文姬"], ["铠"])
        self.assertNotIn("妲己", short["teamfight"])
        self.assertTrue(any("阵容未齐" in n for n in short["notes"]))

    def test_unknown_hero_does_not_acquire_invented_mechanics(self):
        plan = build_plan(["未收录英雄"], ["另一个英雄"], "未收录英雄")
        self.assertIn("尚未覆盖", plan["personal"]["text"])
        self.assertEqual(plan["basis"], [])
        self.assertEqual(plan["cautions"], [])

    def test_api_validation_and_no_match_required(self):
        with TestClient(app) as client:
            sample = client.get("/api/loading/sample").json()
            payload = {"allies": sample["group_b"], "enemies": sample["group_a"], "source": "sample"}
            response = client.post("/api/loading/plan", json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["source"], "sample")
            for change in ({"allies": []}, {"allies": ["后羿"]*2}, {"allies": ["后羿"]*6}, {"allies": ["铠"]}, {"allies": [" "]}, {"player": "赵云"}):
                with self.subTest(change=change):
                    self.assertEqual(client.post("/api/loading/plan", json={**payload, **change}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
