"""Local integration check: the AirDroid capture must survive the skill panel.

Run: .venv\Scripts\python.exe scripts/check_window_capture.py --output work/capture-diagnostics
Requires a connected, non-minimized cast and an overlapping native skill panel.
"""
import argparse
import base64
import ctypes
from ctypes import wintypes
import io
import json
from pathlib import Path
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageChops, ImageGrab, ImageStat
from gameplan.monitoring.screen_capture import windows, physical_pixels


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    available = windows()
    casts = [s for s in available if 'AirDroid Cast v' in s['label']]
    panels = [s for s in available if 'GAMEPLAN · 技能悬浮面板' in s['label']]
    assert len(casts) == 1, f'Expected one real cast window, got {len(casts)}'
    assert len(panels) == 1, f'Expected one native skill panel, got {len(panels)}'
    cast, panel = casts[0], panels[0]
    assert not cast['minimized'], 'Restore AirDroid before testing occlusion'
    with physical_pixels() as user:
        user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        panel_rect, origin = wintypes.RECT(), wintypes.POINT()
        assert user.GetWindowRect(panel['handle'], ctypes.byref(panel_rect))
        assert user.ClientToScreen(cast['handle'], ctypes.byref(origin))
        assert user.GetWindowLongPtrW(panel['handle'], -20) & 8, 'Panel is not topmost'
        overlap = (max(origin.x, panel_rect.left), max(origin.y, panel_rect.top),
                   min(origin.x + cast['width'], panel_rect.right),
                   min(origin.y + cast['height'], panel_rect.bottom))
        assert overlap[2] - overlap[0] > 100 and overlap[3] - overlap[1] > 100, 'Panel must overlap the cast'
        latencies, timestamps = [], []
        for _ in range(5):
            started = time.monotonic()
            request = urllib.request.Request(f'http://127.0.0.1:{args.port}/api/screen/frame',
                data=json.dumps({'source_id': cast['id']}).encode(), headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=12) as response:
                frame = json.load(response)
            latencies.append(round(time.monotonic() - started, 3))
            timestamps.append(frame['captured_at'])
            assert frame['occlusion_safe'] and frame['capture_backend'] == 'windows_graphics_capture'
            picture = Image.open(io.BytesIO(base64.b64decode(frame['image_base64'].split(',')[1]))).convert('RGB')
            assert max(ImageStat.Stat(picture).stddev) > 5, 'Black or empty cast frame'
        assert all(a < b for a, b in zip(timestamps, timestamps[1:])), 'Reused stale capture timestamp'
        desktop = ImageGrab.grab(bbox=(origin.x, origin.y, origin.x + cast['width'], origin.y + cast['height']), all_screens=True).convert('RGB')
        # Convert physical overlap to frame coordinates (large windows can be resized).
        scale_x, scale_y = picture.width / desktop.width, picture.height / desktop.height
        crop = (overlap[0]-origin.x, overlap[1]-origin.y, overlap[2]-origin.x, overlap[3]-origin.y)
        scaled = tuple(round(value * (scale_x if i % 2 == 0 else scale_y)) for i, value in enumerate(crop))
        captured_overlap = picture.crop(scaled).resize(desktop.crop(crop).size)
        difference = sum(ImageStat.Stat(ImageChops.difference(captured_overlap, desktop.crop(crop))).mean) / 3
        assert difference > 10, 'Captured overlay region looks like the desktop, inspect saved images'
    result = {'passed': True, 'backend': frame['capture_backend'], 'panel_topmost': True,
              'overlap': overlap, 'frame_size': picture.size, 'latency_s': latencies,
              'occluded_region_mean_difference': round(difference, 2), 'fresh_frames': len(timestamps)}
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        picture.save(args.output / 'occluded-game.png')
        desktop.save(args.output / 'desktop-with-panel.png')
        (args.output / 'occlusion-check.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
