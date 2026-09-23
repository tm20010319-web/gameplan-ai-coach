"""Window enumeration and compositor capture regressions (no desktop required)."""
from contextlib import nullcontext
from unittest.mock import Mock, patch
import ctypes

import pytest
from PIL import Image

from gameplan.monitoring import screen_capture as sc


def fake_desktop():
    user = Mock()
    user.IsWindowVisible.return_value = True
    user.IsIconic.return_value = False
    user.GetWindowTextLengthW.return_value = 12
    user.GetWindowTextW.side_effect = lambda h, buf, n: setattr(buf, 'value', 'AirDroid Cast')
    user.GetClassNameW.side_effect = lambda h, buf, n: setattr(buf, 'value', 'CastWindow')
    user.GetWindowLongPtrW.side_effect = lambda h, index: 0x80 if h == 102 else 0
    def rect(h, ptr):
        ptr._obj.right, ptr._obj.bottom = 1200, 600
        return True
    user.GetClientRect.side_effect = rect
    user.GetWindowThreadProcessId.side_effect = lambda h, ptr: setattr(ptr._obj, 'value', 77)
    def enumerate_windows(callback, _):
        for handle in (101, 102, 103):
            callback(handle, 0)
        return True
    user.EnumWindows.side_effect = enumerate_windows
    return user


def test_same_title_windows_are_selectable_but_tool_shadow_is_excluded():
    with patch.object(sc, 'physical_pixels', return_value=nullcontext(fake_desktop())):
        sources = sc.windows()
    assert {s['handle'] for s in sources} == {101, 103}
    assert len({s['id'] for s in sources}) == 2


def test_unambiguous_cast_source_survives_window_recreation():
    user = fake_desktop()
    user.EnumWindows.side_effect = lambda callback, _: callback(101, 0)
    with patch.object(sc, 'physical_pixels', return_value=nullcontext(user)):
        first = sc.windows()
        user.EnumWindows.side_effect = lambda callback, _: callback(201, 0)
        second = sc.windows()
    assert first[0]['handle'] != second[0]['handle']
    assert [s['id'] for s in first] == [s['id'] for s in second]


def test_selected_window_uses_compositor_without_desktop_fallback():
    source = {'handle': 101, 'width': 1200, 'height': 600, 'minimized': False}
    picture = Image.new('RGB', (1200, 600), 'navy')
    with patch.object(sc, 'graphics_window_image', return_value=picture) as graphics, \
         patch.object(sc.ImageGrab, 'grab', side_effect=AssertionError('Desktop/PrintWindow fallback')):
        assert sc.window_image(source) is picture
    graphics.assert_called_once_with(source)


def test_failed_window_capture_never_reads_the_covering_desktop():
    with patch.object(sc, 'graphics_window_image', side_effect=sc.CaptureError('窗口没有提供画面')), \
         patch.object(sc.ImageGrab, 'grab') as grab:
        with pytest.raises(sc.CaptureError, match='没有提供'):
            sc.window_image({'handle': 101})
    grab.assert_not_called()
