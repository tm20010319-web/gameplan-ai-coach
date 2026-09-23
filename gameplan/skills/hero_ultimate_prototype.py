"""Small causal clip classifier for explicitly annotated ultimate examples.

This experimental model is separate from the live enemy-cast detector. It does
not infer team, cooldown modifiers, or the true cast time of an ongoing effect.
"""
from collections import deque

import cv2
import numpy as np
import torch
from torch import nn

SIZE = (96, 64)
CLIP_LEN = 4
SAMPLE_FPS = 8


class HeroUltimateNet(nn.Module):
    def __init__(self, classes):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Conv2d(CLIP_LEN * 3, 16, 5, stride=2, padding=2), nn.ReLU(),
            nn.Conv2d(16, 32, 3, stride=2, padding=1), nn.ReLU(),
            nn.Conv2d(32, 48, 3, stride=2, padding=1), nn.ReLU(),
            nn.AdaptiveAvgPool2d((3, 4)), nn.Flatten(),
            nn.Linear(48 * 3 * 4, 64), nn.ReLU(), nn.Linear(64, classes),
        )

    def forward(self, x):
        return self.layers(x)


def prepare_frame(frame, roi):
    """Normalized ROI supplied by the caller; no filename or hero input."""
    h, w = frame.shape[:2]
    if len(roi) != 4 or not (0 <= roi[0] < roi[2] <= 1 and 0 <= roi[1] < roi[3] <= 1):
        raise ValueError('ROI must be normalized x1,y1,x2,y2 with positive area')
    x1, y1, x2, y2 = [int(v * s) for v, s in zip(roi, (w, h, w, h))]
    crop = frame[y1:y2, x1:x2]
    if not crop.size:
        raise ValueError('empty ROI')
    return cv2.cvtColor(cv2.resize(crop, SIZE), cv2.COLOR_BGR2RGB)


def sample_video(path, roi):
    """Decode sequentially using presentation timestamps (supports VFR clips)."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f'Cannot open video: {path}')
    frames, times = [], []
    deadline = 0.0
    previous = -1.0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
            if t < previous:
                raise ValueError('Video timestamps moved backwards')
            previous = t
            if t + 1e-6 >= deadline:
                frames.append(prepare_frame(frame, roi))
                times.append(t)
                deadline = t + 1 / SAMPLE_FPS
    finally:
        cap.release()
    if len(frames) < CLIP_LEN or times[-1] <= 0:
        raise ValueError('Video too short or missing presentation timestamps')
    return np.asarray(times), np.asarray(frames)


def causal_clips(frames):
    return causal_clips_at(frames, np.arange(len(frames)))


def causal_clips_at(frames, indices):
    """Build causal clips only at requested indices for long recordings."""
    result = []
    for i in indices:
        frame_indices = np.maximum(0, np.arange(i - CLIP_LEN + 1, i + 1))
        result.append(frames[frame_indices].transpose(0, 3, 1, 2).reshape(CLIP_LEN * 3, SIZE[1], SIZE[0]))
    if not result:
        return torch.empty((0, CLIP_LEN * 3, SIZE[1], SIZE[0]), dtype=torch.float32)
    return torch.from_numpy(np.stack(result)).float() / 255


class EventTracker:
    """Two-frame confirmation; rearm only after a sustained absence.

    Tracks observation times, never silently upgrades them to cast times.
    """
    def __init__(self, labels, threshold=.8, release_s=1.0):
        self.labels = labels
        self.threshold = threshold
        self.release_s = release_s
        self.pending = None
        self.active = None
        self.last_active = None
        self.last_t = None

    def update(self, t, probabilities):
        if self.last_t is not None and t <= self.last_t:
            raise ValueError('Reset tracker before replay seek/restart')
        if self.last_t is not None and t - self.last_t > .5:
            self.pending = None
        self.last_t = t
        k = int(np.argmax(probabilities))
        label = self.labels[k] if k and probabilities[k] >= self.threshold else None
        if self.active is not None:
            if label == self.active:
                self.last_active = t
            if t - self.last_active < self.release_s:
                return None
            self.active = None
        if label is None:
            self.pending = None
            return None
        if self.pending is None or self.pending['hero'] != label:
            self.pending = {'hero': label, 'first_seen_s': float(t), 'count': 1}
            return None
        self.pending['count'] += 1
        if self.pending['count'] < 2:
            return None
        event = {**self.pending, 'confirmed_s': float(t), 'score': float(probabilities[k]),
                 'cast_time_s': None, 'team': 'unknown',
                 'time_semantics': 'first_visual_detection_not_verified_cast_time'}
        del event['count']
        self.active, self.last_active, self.pending = label, t, None
        return event


class StreamingClassifier:
    """Reusable frame-in/scores-out interface; caller owns capture and ROI."""
    def __init__(self, checkpoint, roi):
        data = torch.load(checkpoint, map_location='cpu', weights_only=True)
        self.labels, self.roi = data['labels'], roi
        self.model = HeroUltimateNet(len(self.labels))
        self.model.load_state_dict(data['state_dict'])
        self.model.eval()
        self.reset()

    def reset(self):
        self.frames = deque(maxlen=CLIP_LEN)
        self.tracker = EventTracker(self.labels)
        self.last_t = None

    def update(self, frame, timestamp_s):
        if self.last_t is not None:
            if timestamp_s <= self.last_t:
                raise ValueError('Reset classifier before replay seek/restart')
            if timestamp_s - self.last_t < 1 / SAMPLE_FPS:
                return None
            if timestamp_s - self.last_t > .5:
                self.frames.clear()
        self.last_t = timestamp_s
        self.frames.append(prepare_frame(frame, self.roi))
        with torch.inference_mode():
            scores = self.model(causal_clips(np.asarray(self.frames))[-1:]).softmax(1)[0].numpy()
        return {'scores': dict(zip(self.labels, map(float, scores))),
                'event': self.tracker.update(timestamp_s, scores)}
