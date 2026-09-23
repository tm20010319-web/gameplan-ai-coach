"""Bounded local evidence for live skill misses; never includes server settings."""
import base64
import io
import json
import logging
import os
import re
import time
import zipfile
from contextvars import ContextVar
from pathlib import Path
from threading import Lock
from uuid import uuid4

from PIL import Image

ROOT = Path(__file__).resolve().parents[2] / 'work' / 'skill-diagnostics'
MAX_BATCHES = 32
MAX_BYTES = 128 * 1024 * 1024
MAX_BATCH_BYTES = 24 * 1024 * 1024
MAX_AGE_S = 30 * 60
active_trace = ContextVar('skill_diagnostic_trace', default=None)
_lock = Lock()
_log = logging.getLogger(__name__)


def enabled():
    return os.getenv('GAMEPLAN_SKILL_DIAGNOSTICS', '0') == '1'


class Trace:
    def __init__(self, req):
        self.id = f'cast-{time.time_ns()}-{uuid4().hex[:8]}'
        self.req = req
        self.files = {}
        self.data = {'format_version': 1, 'created_at': time.time(),
                     'request': req.model_dump(exclude={'image_base64', 'recent_frames'}),
                     'frames': [{'frame_index': i, 'captured_at': sample.captured_at}
                                for i, sample in enumerate([*req.recent_frames, req])]}

    def finish(self, response=None, error=None):
        """Diagnostics must never turn a successful observation into a failure."""
        try:
            self.data.update(response=response, error=error, finished_at=time.time())
            total = sum(map(len, self.files.values()))
            for index, sample in enumerate([*self.req.recent_frames, self.req]):
                pixels = base64.b64decode(sample.image_base64.split(',')[-1], validate=True)
                if len(pixels) > 8_000_000 or total + len(pixels) > MAX_BATCH_BYTES - 2_000_000:
                    self.data['frames'][index]['omitted'] = 'size_limit'
                    continue
                with Image.open(io.BytesIO(pixels)) as picture:
                    extension = {'PNG': 'png', 'JPEG': 'jpg', 'WEBP': 'webp'}.get(picture.format)
                if not extension:
                    continue
                name = f'F{index}.{extension}'
                self.files[name] = pixels
                self.data['frames'][index]['file'] = name
                total += len(pixels)
            metadata = json.dumps(self.data, ensure_ascii=False, allow_nan=False,
                                  default=lambda value: value.tolist(), indent=2).encode('utf8')
            if total + len(metadata) > MAX_BATCH_BYTES:
                return None
            with _lock:
                ROOT.mkdir(parents=True, exist_ok=True)
                target = ROOT / (self.id + '.zip')
                pending = ROOT / (self.id + '.tmp')
                try:
                    with zipfile.ZipFile(pending, 'w', compression=zipfile.ZIP_STORED) as archive:
                        archive.writestr('trace.json', metadata)
                        for name, pixels in self.files.items():
                            archive.writestr(name, pixels)
                    pending.replace(target)
                    prune()
                finally:
                    pending.unlink(missing_ok=True)
            return self.id
        except Exception:
            _log.warning('Skill diagnostic write failed', exc_info=False)
            return None


def prune():
    """Delete only our regular archive files inside the configured directory."""
    root = ROOT.resolve()
    files = sorted((p for p in ROOT.glob('cast-*.zip')
                    if re.fullmatch(r'cast-\d+-[a-f0-9]{8}\.zip', p.name)
                    and not p.is_symlink() and p.is_file() and p.resolve().parent == root),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    total = 0
    now = time.time()
    for index, path in enumerate(files):
        size = path.stat().st_size
        if index >= MAX_BATCHES or total + size > MAX_BYTES or now-path.stat().st_mtime > MAX_AGE_S:
            path.unlink()
        else:
            total += size


def begin(req):
    if enabled() and req.input_kind == 'live' and req.focus == 'skills' and not req.all_frames:
        return Trace(req)
    return None


def record(name, value):
    trace = active_trace.get()
    if trace is not None:
        trace.data[name] = value


def record_image(name, pixels):
    trace = active_trace.get()
    if trace is not None and len(pixels) <= 8_000_000:
        trace.files[name] = pixels
