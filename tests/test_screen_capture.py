import os
import tempfile
import unittest
from unittest.mock import patch
from contextlib import nullcontext
from io import BytesIO
from pathlib import Path
import base64

TEST_DATA = tempfile.TemporaryDirectory(prefix="gameplan-screen-tests-")
os.environ.setdefault("COACH_DATA_DIR", TEST_DATA.name)

from PIL import Image
from fastapi.testclient import TestClient
import gameplan.web.app as app
import gameplan.monitoring.screen_capture as screen_capture


DISPLAY = {"id": "a" * 16, "label": "屏幕 2", "width": 1920, "height": 1080,
           "primary": False, "bbox": [-1920, 0, 0, 1080]}


def test_panel_sampling_cue_accepts_real_scoreboards_and_rejects_plain_frames():
    fixtures = Path(__file__).parent / 'fixtures'
    for relative in ('summoner/scoreboard.jpg', 'combat/scoreboard-partial-tabs/panel.png',
                     'combat/scoreboard-same-name/panel.png', 'combat/stream-scoreboard/panel.png'):
        with Image.open(fixtures / relative) as picture:
            assert screen_capture.panel_candidate(picture), relative
    for color in ('black', 'white', 'green'):
        assert not screen_capture.panel_candidate(Image.new('RGB', (1280, 576), color))
    for relative in ('combat/untargetable/aoyin.png', 'combat/sunce-ongoing.jpg', 'combat/sunce-warning/268.png'):
        with Image.open(fixtures / relative) as picture:
            assert not screen_capture.panel_candidate(picture), relative


class ScreenCaptureTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))

    def tearDown(self):
        self.client.close()

    def test_source_listing_does_not_capture(self):
        with patch('gameplan.monitoring.screen_capture.displays', return_value=[DISPLAY]), patch('gameplan.monitoring.screen_capture.ImageGrab.grab') as grab:
            response = self.client.get("/api/screen/sources")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["sources"][0]["id"], DISPLAY["id"])
            self.assertNotIn("bbox", response.json()["sources"][0])
            grab.assert_not_called()

    def test_only_selected_window_is_captured(self):
        window={"id":"c"*16,"label":"窗口 · 投屏","kind":"window","handle":101,"width":1600,"height":720}
        with patch('gameplan.monitoring.screen_capture.displays',return_value=[DISPLAY]),patch('gameplan.monitoring.screen_capture.windows',return_value=[window]), \
             patch('gameplan.monitoring.screen_capture.window_image',return_value=Image.new("RGB",(1600,720),"navy")) as selected, \
             patch('gameplan.monitoring.screen_capture.ImageGrab.grab') as screen:
            result=screen_capture.capture(window['id'])
        selected.assert_called_once_with(window);screen.assert_not_called()
        self.assertEqual((result['width'],result['height']),(1600,720))

    def test_minimized_window_never_captures_a_different_window_or_display(self):
        with patch('gameplan.monitoring.screen_capture.ImageGrab.grab') as capture:
            with self.assertRaisesRegex(screen_capture.CaptureError,"最小化"):
                screen_capture.window_image({'minimized':True,'handle':101})
            capture.assert_not_called()

    def test_remote_and_other_origin_cannot_read_desktop(self):
        with patch('gameplan.monitoring.screen_capture.capture') as capture:
            with TestClient(app.app, base_url="http://127.0.0.1", client=("192.168.0.2", 50000)) as remote:
                self.assertEqual(remote.post("/api/screen/frame", json={"source_id": DISPLAY["id"]}).status_code, 403)
            for headers in ({"origin": "http://other.example"}, {"host": "other.example"}, {"origin": "null"}):
                self.assertEqual(self.client.post("/api/screen/frame", json={"source_id": DISPLAY["id"]}, headers=headers).status_code, 403)
            capture.assert_not_called()

    def test_only_selected_display_is_returned_with_capture_timestamp(self):
        with patch('gameplan.monitoring.screen_capture.displays', return_value=[DISPLAY]), patch('gameplan.monitoring.screen_capture.physical_pixels', return_value=nullcontext()), \
             patch('gameplan.monitoring.screen_capture.ImageGrab.grab', return_value=Image.new("RGB", (1920, 1080), "navy")) as grab:
            response = self.client.post("/api/screen/frame", json={"source_id": DISPLAY["id"]}, headers={"origin": "http://127.0.0.1"})
        self.assertEqual(response.status_code, 200, response.text)
        grab.assert_called_once_with(bbox=(-1920, 0, 0, 1080), all_screens=True)
        data = response.json()
        self.assertGreater(data["captured_at"], 0)
        self.assertEqual((data["width"], data["height"]), (1280, 720))
        with Image.open(BytesIO(base64.b64decode(data["image_base64"].split(",")[1]))) as image:
            self.assertEqual(image.size, (1280, 720))
            self.assertEqual(image.format, 'PNG')

    def test_removed_display_never_falls_back_to_another(self):
        with patch('gameplan.monitoring.screen_capture.displays', return_value=[]), patch('gameplan.monitoring.screen_capture.ImageGrab.grab') as grab:
            response = self.client.post("/api/screen/frame", json={"source_id": DISPLAY["id"]})
        self.assertEqual(response.status_code, 503)
        self.assertIn("重新选择", response.json()["detail"])
        grab.assert_not_called()

    def test_black_frame_and_changed_dimensions_fail(self):
        for picture in (Image.new("RGB", (1920, 1080)), Image.new("RGB", (10, 10), "navy")):
            with self.subTest(size=picture.size), patch('gameplan.monitoring.screen_capture.displays', return_value=[DISPLAY]), \
                 patch('gameplan.monitoring.screen_capture.physical_pixels', return_value=nullcontext()), patch('gameplan.monitoring.screen_capture.ImageGrab.grab', return_value=picture):
                self.assertEqual(self.client.post("/api/screen/frame", json={"source_id": DISPLAY["id"]}).status_code, 503)

    def test_bad_source_id_is_rejected(self):
        with patch('gameplan.monitoring.screen_capture.capture') as capture:
            for value in ("", "all", "../desktop", "a" * 200):
                self.assertEqual(self.client.post("/api/screen/frame", json={"source_id": value}).status_code, 422)
            capture.assert_not_called()

    def test_preflight_checks_the_selected_model(self):
        with patch('gameplan.web.app.vision_status', return_value={"ready": False, "model": "missing"}) as health:
            self.assertEqual(self.client.get("/api/vision/status?model=missing").json()["ready"], False)
            health.assert_called_once_with("missing")


if __name__ == "__main__":
    unittest.main()
