"""Capture a selected local Windows display on demand, without writing images to disk."""

import base64
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import hashlib
import io
import sys
import time
from collections import Counter

from PIL import ImageGrab


class CaptureError(RuntimeError):
    pass


@contextmanager
def physical_pixels():
    if sys.platform != "win32":
        raise CaptureError("本机整屏采集目前支持 Windows；其他系统可使用浏览器共享。")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    switch = user32.SetThreadDpiAwarenessContext
    switch.argtypes = [ctypes.c_void_p]
    switch.restype = ctypes.c_void_p
    previous = switch(ctypes.c_void_p(-4))
    try:
        yield user32
    finally:
        if previous:
            switch(previous)


def displays():
    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                    ("work", wintypes.RECT), ("flags", wintypes.DWORD), ("device", wintypes.WCHAR * 32)]

    result = []
    with physical_pixels() as user32:
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                                          ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), callback_type, wintypes.LPARAM]
        user32.EnumDisplayMonitors.restype = wintypes.BOOL

        @callback_type
        def collect(handle, _dc, _rect, _data):
            info = MonitorInfo()
            info.size = ctypes.sizeof(info)
            if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
                rect = info.monitor
                bbox = [rect.left, rect.top, rect.right, rect.bottom]
                identity = f"{info.device}:{bbox}"
                result.append({"id": hashlib.sha256(identity.encode()).hexdigest()[:16],
                               "width": rect.right - rect.left, "height": rect.bottom - rect.top,
                               "primary": bool(info.flags & 1), "bbox": bbox})
            return True

        if not user32.EnumDisplayMonitors(None, None, collect, 0):
            raise CaptureError("无法枚举显示器，请确认 Windows 桌面会话处于登录状态。")
    result.sort(key=lambda item: (not item["primary"], item["bbox"][0], item["bbox"][1]))
    for number, item in enumerate(result, 1):
        item["label"] = f"屏幕 {number}{'（主屏）' if item['primary'] else ''} · {item['width']} × {item['height']}"
    return result


def windows():
    """Exclude tool shadows; retain named sources across cast-window recreation.

    Genuine same-title windows get instance identities instead of disappearing.
    """
    class Placement(ctypes.Structure):
        _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("show", wintypes.UINT),
                    ("minimum", wintypes.POINT), ("maximum", wintypes.POINT), ("normal", wintypes.RECT)]

    found = []
    with physical_pixels() as user32:
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(Placement)]

        @callback_type
        def collect(handle, _data):
            if not user32.IsWindowVisible(handle):
                return True
            # AirDroid's same-title shadow is a layered tool window. It renders
            # black and must never replace the actual video window.
            if user32.GetWindowLongPtrW(handle, -20) & 0x80:  # WS_EX_TOOLWINDOW
                return True
            length = user32.GetWindowTextLengthW(handle)
            if not length:
                return True
            title = ctypes.create_unicode_buffer(length + 1)
            kind = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(handle, title, length + 1)
            user32.GetClassNameW(handle, kind, 256)
            rect = wintypes.RECT()
            if not user32.GetClientRect(handle, ctypes.byref(rect)):
                return True
            width, height = rect.right, rect.bottom
            minimized = bool(user32.IsIconic(handle))
            if minimized:
                placement = Placement()
                placement.length = ctypes.sizeof(placement)
                if user32.GetWindowPlacement(handle, ctypes.byref(placement)):
                    width = placement.normal.right - placement.normal.left
                    height = placement.normal.bottom - placement.normal.top
            if width < 320 or height < 160 or kind.value in ("Shell_TrayWnd", "Progman", "WorkerW"):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            identity = hashlib.sha256(f"window:{kind.value}:{title.value}".encode()).hexdigest()[:16]
            found.append({"id": identity, "kind": "window", "handle": handle, "width": width, "height": height,
                          "process_id": pid.value, "minimized": minimized, "label": f"窗口 · {title.value[:100]}"})
            return True
        if not user32.EnumWindows(collect, 0):
            raise CaptureError("无法读取窗口列表，请确认桌面已登录。")
    counts = Counter(item['id'] for item in found)
    for item in found:
        if counts[item['id']] > 1:
            item['id'] = hashlib.sha256(f"{item['id']}:{item['process_id']}:{item['handle']}".encode()).hexdigest()[:16]
            item['label'] += f" · 窗口 {item['handle']}"
    return found


def sources():
    return [*windows(), *[{**item, "kind": "display"} for item in displays()]]


def window_image(source):
    if source.get("minimized"):
        raise CaptureError("投屏窗口已最小化，等待恢复窗口后继续采集。")
    return graphics_window_image(source)


def graphics_window_image(source):
    from gameplan.monitoring.windows_graphics import capture_window, GraphicsCaptureError
    try:
        with physical_pixels():
            return capture_window(source)
    except GraphicsCaptureError as exc:
        raise CaptureError(str(exc)) from exc


def panel_candidate(picture):
    """Cheap sampling priority, never proof of a scoreboard or hero identity."""
    pixels = picture.convert('RGB').resize((64, 36)).load()
    blue = sum(b > 35 and b > r * 1.3 and g > r * 1.1
               for y in range(6, 29) for x in range(12, 53)
               for r, g, b in [pixels[x, y]])
    return blue / (23 * 41) >= .6


def capture(source_id, roi=None):
    source = next((item for item in displays() if item["id"] == source_id), None)
    if source is None:
        source = next((item for item in windows() if item["id"] == source_id), None)
    if source is None:
        raise CaptureError("所选窗口已关闭或屏幕已断开，请刷新采集列表并重新选择。")
    captured_at = time.time()
    try:
        if source.get("kind") == "window":
            picture = window_image(source)
            captured_at = picture.info.get("captured_at", captured_at)
        else:
            with physical_pixels():
                picture = ImageGrab.grab(bbox=tuple(source["bbox"]), all_screens=True).convert("RGB")
        if picture.size != (source["width"], source["height"]):
            raise CaptureError("屏幕尺寸发生变化，请重新选择屏幕。")
        if all(high == 0 for _low, high in picture.getextrema()):
            raise CaptureError("采集到黑屏；请解锁桌面，并检查远程桌面或游戏全屏模式。")
        if roi is not None:
            # Crop in physical pixels before reducing the image; small labels matter.
            width, height = picture.size
            box = (round(roi.x * width), round(roi.y * height),
                   min(width, round((roi.x + roi.width) * width)),
                   min(height, round((roi.y + roi.height) * height)))
            if box[2] - box[0] < 32 or box[3] - box[1] < 32:
                raise CaptureError("框选区域太小，请至少选择 32 × 32 像素。")
            picture = picture.crop(box)
        elif source.get('kind') == 'window' and 'airdroid' in source.get('label', '').lower():
            from gameplan.vision.game_view import game_view
            picture = game_view(picture)
        picture.thumbnail((1920, 1080) if roi is not None or source.get("kind") == "window" else (1280, 720))
        buffer = io.BytesIO()
        # Preserve small icon/name pixels for local matching. Model preparation
        # can compress its own copy; capture must not discard this evidence.
        picture.save(buffer, format="PNG")
        return {"image_base64": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(),
                "captured_at": captured_at, "source_id": source_id, "source_label": source["label"],
                "capture_backend": "windows_graphics_capture" if source.get("kind") == "window" else "desktop",
                "occlusion_safe": source.get("kind") == "window",
                "panel_candidate": panel_candidate(picture),
                "width": picture.width, "height": picture.height}
    except CaptureError:
        raise
    except Exception as exc:
        raise CaptureError("无法采集当前桌面；请确认电脑已登录并解锁，远程桌面仍保持连接，然后重新开始监控。") from exc
