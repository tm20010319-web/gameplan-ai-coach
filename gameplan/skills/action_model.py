"""Optional lightweight temporal action model for enemy ultimate evidence.

The model is deliberately separate from hero identity. Qwen3-VL (or the local
scoreboard/OCR path) supplies the bound hero; this module only answers whether
the recent image sequence contains a cast-like temporal change. Loading is
opt-in through ``ULTIMATE_ACTION_MODEL_PATH`` so an untrained checkpoint can
never change the existing monitor behaviour.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable
from functools import lru_cache

import numpy as np

LABELS = ("background", "cast_start", "ongoing")


def _torch():
    import torch
    import torch.nn as nn
    return torch, nn


def _prepare(frames: Iterable[np.ndarray], *, clip_len: int = 8, size=(112, 64)):
    """Convert BGR images to a normalized ``1 x 1 x T x H x W`` tensor."""
    torch, _ = _torch()
    items = [np.asarray(frame) for frame in frames if frame is not None]
    if not items:
        raise ValueError("action model received no frames")
    chosen = np.linspace(0, len(items) - 1, clip_len).round().astype(int)
    out = []
    import cv2
    for index in chosen:
        frame = items[index]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        gray = cv2.resize(gray, size, interpolation=cv2.INTER_AREA).astype("float32") / 255.0
        out.append(gray)
    return torch.from_numpy(np.stack(out, axis=0)).unsqueeze(0).unsqueeze(0)


def build_network():
    torch, nn = _torch()

    class UltimateActionNet(nn.Module):
        def __init__(self):
            super().__init__()
            self.features = nn.Sequential(
                nn.Conv3d(1, 16, (3, 5, 5), padding=(1, 2, 2)), nn.BatchNorm3d(16), nn.ReLU(),
                nn.MaxPool3d((1, 2, 2)),
                nn.Conv3d(16, 32, (3, 3, 3), padding=1), nn.BatchNorm3d(32), nn.ReLU(),
                nn.MaxPool3d((2, 2, 2)),
                nn.Conv3d(32, 64, (3, 3, 3), padding=1), nn.ReLU(),
                nn.AdaptiveAvgPool3d((1, 1, 1)),
            )
            self.head = nn.Sequential(nn.Flatten(), nn.Dropout(0.15), nn.Linear(64, len(LABELS)))

        def forward(self, value):
            return self.head(self.features(value))

    return UltimateActionNet()


def load(path: str | os.PathLike | None = None, device: str | None = None):
    """Load a trained checkpoint, returning ``None`` when opt-in is absent."""
    path = path or os.getenv("ULTIMATE_ACTION_MODEL_PATH")
    if not path or not Path(path).is_file():
        return None
    torch, _ = _torch()
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    return _load_cached(str(Path(path).resolve()), device)


@lru_cache(maxsize=2)
def _load_cached(path: str, device: str):
    torch, _ = _torch()
    payload = torch.load(path, map_location=device, weights_only=False)
    # Bootstrap checkpoints trained from automatically generated labels are
    # intentionally opt-in.  This lets the one-video prototype run while
    # keeping unverified weights disabled by default in normal deployments.
    if (isinstance(payload, dict) and payload.get("deployment_eligible") is False
            and os.getenv("ULTIMATE_ACTION_ALLOW_EXPERIMENTAL", "0").lower() not in ("1", "true", "yes")):
        return None
    model = build_network().to(device)
    model.load_state_dict(payload["state_dict"] if "state_dict" in payload else payload)
    model.eval()
    model._action_device = device
    model._action_threshold = float(payload.get("threshold", 0.72)) if isinstance(payload, dict) else 0.72
    return model


def predict(model, frames: Iterable[np.ndarray]):
    if model is None:
        return {label: None for label in LABELS}
    torch, _ = _torch()
    device = getattr(model, "_action_device", "cpu")
    with torch.inference_mode():
        scores = torch.softmax(model(_prepare(frames).to(device)), dim=-1)[0].detach().cpu().numpy()
    return {label: float(scores[index]) for index, label in enumerate(LABELS)}


def review_sequence(model, frames: Iterable[np.ndarray]):
    """Return a serializable review payload for the monitor response."""
    if model is None:
        return {"enabled": False, "status": "unconfigured", "probabilities": {label: None for label in LABELS}}
    probabilities = predict(model, frames)
    best = max(probabilities, key=probabilities.get)
    return {"enabled": True, "status": "observed", "label": best,
            "probabilities": probabilities, "threshold": getattr(model, "_action_threshold", 0.72)}
