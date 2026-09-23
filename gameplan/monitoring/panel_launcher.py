"""Launch a bounded local panel process; never accept arbitrary commands/URLs."""
import subprocess
import sys
import threading
from pathlib import Path

_lock = threading.Lock()
_process = None


def open_panel(port, source_id=None, region='full'):
    from gameplan.monitoring.overlay import panel_url
    panel_url(port, source_id, region)  # Validate also for non-HTTP callers.
    global _process
    with _lock:
        if _process is not None and _process.poll() is None:
            return {'opened': False, 'running': True}
        root = Path(__file__).resolve().parents[2]
        log_dir = root / 'work' / 'monitor-logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        args = [sys.executable, '-m', 'gameplan.monitoring.overlay', '--port', str(port), '--region', region]
        if source_id:
            args += ['--source', source_id]
        with (log_dir / 'panel.log').open('a', encoding='utf-8') as log:
            _process = subprocess.Popen(args, cwd=root, stdout=log, stderr=log,
                                        creationflags=subprocess.CREATE_NO_WINDOW)
        return {'opened': True, 'running': True}
