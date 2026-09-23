"""Native always-on-top host for the existing monitor and cooldown renderer."""
import argparse
import ctypes
from ctypes import wintypes
from pathlib import Path
import re
from urllib.parse import urlencode


def panel_url(port, source_id=None, region='full', auto=True):
    if not 1024 <= port <= 65535:
        raise ValueError('Invalid local port')
    if source_id and not re.fullmatch(r'[a-f0-9]{16}', source_id):
        raise ValueError('Invalid source')
    if region not in ('full', 'left', 'right'):
        raise ValueError('Invalid capture region')
    query = {'panel': '1', 'region': region}
    if source_id:
        query['source'] = source_id
    if not auto:
        query['monitor'] = 'off'
    return f'http://127.0.0.1:{port}/monitor/panel?{urlencode(query)}'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--source')
    parser.add_argument('--region', default='full')
    parser.add_argument('--no-auto', action='store_true')
    args = parser.parse_args()
    url = panel_url(args.port, args.source, args.region, not args.no_auto)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    mutex = kernel.CreateMutexW(None, False, f'Local\\GameplanSkillPanel{args.port}')
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:  # One sampler per panel/port.
        kernel.CloseHandle(mutex)
        return
    try:
        import webview
        profile = Path(__file__).resolve().parents[2] / 'work' / 'panel-profile'
        profile.mkdir(parents=True, exist_ok=True)
        webview.create_window('GAMEPLAN · 技能悬浮面板', url, width=460, height=640,
                              min_size=(380, 430), on_top=True, background_color='#101820')
        webview.start(gui='edgechromium', private_mode=False, storage_path=str(profile))
    finally:
        kernel.CloseHandle(mutex)


if __name__ == '__main__':
    main()
