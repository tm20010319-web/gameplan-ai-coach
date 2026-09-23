"""Evaluate saved weights on never-trained videos with one fixed screen ROI.

This is deliberately a video-level smoke test. Filename prefixes provide only
the expected hero class; they do not provide event counts or onset timestamps.
Consequently this report cannot claim per-cast recall or timing accuracy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import torch

from gameplan.skills.hero_ultimate_prototype import (
    EventTracker, HeroUltimateNet, causal_clips, sample_video,
)

ROOT = Path(__file__).resolve().parents[1]
# Excludes browser chrome and most touch controls. One policy for every test.
TEST_ROI = [0.04, 0.13, 0.78, 0.84]


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', type=Path, default=Path(r'C:\Users\Administrator\Desktop\闪现'))
    parser.add_argument('--model', type=Path, default=ROOT/'work/three-hero-experiment/hero_ultimate.pt')
    parser.add_argument('--annotations', type=Path, default=ROOT/'data/ultimate_examples/annotations.json')
    parser.add_argument('--out', type=Path, default=ROOT/'work/independent-ultimate-tests/report.json')
    args = parser.parse_args()
    torch.set_num_threads(4)
    payload = torch.load(args.model, map_location='cpu', weights_only=True)
    labels = payload['labels']
    model = HeroUltimateNet(len(labels))
    model.load_state_dict(payload['state_dict'])
    model.eval()
    trained_hashes = {row['sha256'] for row in json.loads(args.annotations.read_text(encoding='utf-8'))['videos']}
    rows = []
    for path in sorted(args.folder.glob('*测试*.mp4')):
        file_hash = sha256(path)
        if file_hash in trained_hashes:
            raise SystemExit(f'test data leakage: {path.name} is present in training annotations')
        expected = next((label for label in labels[1:] if path.name.startswith(label)), None)
        filename_hero = path.name.split('大招', 1)[0]
        times, frames = sample_video(path, TEST_ROI)
        clips = causal_clips(frames)
        with torch.inference_mode():
            probabilities = torch.cat([model(batch).softmax(1) for batch in clips.split(64)]).numpy()
        tracker = EventTracker(labels)
        events = []
        for timestamp, scores in zip(times, probabilities):
            event = tracker.update(float(timestamp), scores)
            if event:
                events.append(event)
        counts = {label: sum(event['hero'] == label for event in events) for label in labels[1:]}
        correct = counts.get(expected, 0) if expected else None
        wrong = sum(value for label, value in counts.items() if label != expected)
        rows.append({
            'file': path.name, 'sha256': file_hash, 'expected_hero_from_filename': filename_hero,
            'expected_class_supported': expected is not None, 'fixed_roi': TEST_ROI,
            'sampled_frames': len(times), 'event_counts': counts, 'events': events,
            'video_level_detected_expected_class': bool(correct) if expected else None,
            'wrong_class_events': wrong,
            'unsupported_video_false_positive': bool(events) if expected is None else None,
            'max_probabilities': dict(zip(labels, map(float, probabilities.max(axis=0)))),
        })
    supported = [row for row in rows if row['expected_class_supported']]
    unsupported = [row for row in rows if not row['expected_class_supported']]
    report = {
        'scope': 'independent_video_level_smoke_test',
        'model': str(args.model), 'model_training_scope': payload.get('evaluation_scope'),
        'test_files_in_training': 0, 'fixed_roi': TEST_ROI, 'threshold': .8,
        'supported_test_videos': len(supported),
        'supported_video_hits': sum(row['video_level_detected_expected_class'] for row in supported),
        'supported_videos_with_wrong_class_events': sum(row['wrong_class_events'] > 0 for row in supported),
        'unsupported_test_videos': len(unsupported),
        'unsupported_videos_with_false_positive': sum(bool(row['events']) for row in unsupported),
        'videos': rows,
        'not_measured': ['per_cast_recall', 'cast_onset_error', 'background_false_positive_rate',
                         'enemy_attribution', 'cooldown_start_time'],
        'interpretation': ('A supported-video hit only means at least one event of the filename class was emitted. '
                           'It does not prove every visible ultimate was detected.'),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
