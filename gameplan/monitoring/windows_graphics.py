"""Fresh, HWND-scoped Windows Graphics Capture frames, even behind other windows.

Each request owns its capture session. No cached frame can be mistaken for a
new skill cast after minimize/close, and idle pages leave no capture thread.
"""
import ctypes
from ctypes import wintypes
import threading
import time

from PIL import Image


class GraphicsCaptureError(RuntimeError):
    pass


_capture_lock = threading.Lock()


def _client_crop(picture, source):
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.IsWindow.argtypes = [wintypes.HWND]
    user.IsIconic.argtypes = [wintypes.HWND]
    user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    hwnd = source['handle']
    if not user.IsWindow(hwnd):
        raise GraphicsCaptureError('投屏窗口已关闭，请重新选择窗口。')
    if user.IsIconic(hwnd):
        raise GraphicsCaptureError('投屏窗口已最小化，恢复后自动重试；被其他窗口遮挡不影响采集。')
    client, outer, origin = wintypes.RECT(), wintypes.RECT(), wintypes.POINT()
    if not (user.GetClientRect(hwnd, ctypes.byref(client)) and
            user.GetWindowRect(hwnd, ctypes.byref(outer)) and
            user.ClientToScreen(hwnd, ctypes.byref(origin))):
        raise GraphicsCaptureError('窗口尺寸暂不可用，等待窗口稳定后重试。')
    size = (client.right, client.bottom)
    if size != (source['width'], source['height']):
        raise GraphicsCaptureError('投屏窗口尺寸正在变化，等待窗口稳定后重试。')
    if picture.size == size:
        return picture
    # WGC normally omits the invisible resize border (DWM bounds). Some
    # borderless apps use the outer rectangle. Accept only an exact match.
    dwm = ctypes.WinDLL('dwmapi')
    dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    bounds = wintypes.RECT()
    candidates = [outer]
    if dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(bounds), ctypes.sizeof(bounds)) == 0:
        candidates.insert(0, bounds)
    for rect in candidates:
        if picture.size != (rect.right - rect.left, rect.bottom - rect.top):
            continue
        x, y = origin.x - rect.left, origin.y - rect.top
        if x >= 0 and y >= 0 and x + size[0] <= picture.width and y + size[1] <= picture.height:
            return picture.crop((x, y, x + size[0], y + size[1]))
    raise GraphicsCaptureError('窗口尺寸正在变化，等待窗口稳定后重试。')


def capture_window(source, timeout=3.0):
    try:
        from windows_capture import WindowsCapture
    except ImportError as exc:
        raise GraphicsCaptureError('窗口采集组件未安装，请使用项目启动脚本并安装 requirements.txt。') from exc
    if not _capture_lock.acquire(timeout=timeout):
        raise GraphicsCaptureError('窗口采集正忙，稍后自动重试。')
    control = None
    try:
        ready = threading.Event()
        result = {}
        capture = WindowsCapture(cursor_capture=False, window_hwnd=source['handle'])

        @capture.event
        def on_frame_arrived(frame, internal_control):
            try:
                picture = Image.frombytes('RGB', (frame.width, frame.height),
                                          frame.frame_buffer.tobytes(), 'raw', 'BGRX')
                # Some drivers deliver one blank initialization frame.
                if all(high == 0 for low, high in picture.getextrema()):
                    return
                result.update(picture=picture, captured_at=time.time())
            except Exception as exc:
                result['error'] = exc
            ready.set()
            internal_control.stop()

        @capture.event
        def on_closed():
            ready.set()

        control = capture.start_free_threaded()
        if not ready.wait(timeout) or 'picture' not in result:
            raise GraphicsCaptureError('窗口没有提供有效画面（黑屏或已关闭）；请确认投屏已连接且窗口未最小化。')
        picture = _client_crop(result['picture'], source)
        picture.info['captured_at'] = result['captured_at']
        return picture
    except GraphicsCaptureError:
        raise
    except Exception as exc:
        raise GraphicsCaptureError('Windows 窗口采集失败，请检查投屏连接后重试。') from exc
    finally:
        try:
            if control is not None:
                control.stop()
        finally:
            _capture_lock.release()
