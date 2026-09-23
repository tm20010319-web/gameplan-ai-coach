"""Scan new examples, train an experimental model, replay and report honestly.

No random adjacent-frame validation: current results are training-video replay.
New files remain pending until their visible event intervals/ROI are reviewed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import torch
from gameplan.skills.hero_ultimate_prototype import (
    HeroUltimateNet, EventTracker, sample_video, causal_clips_at, SAMPLE_FPS,
)

ROOT = Path(__file__).resolve().parents[1]


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def digest(path):
    with open(path, 'rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def video_path(folder, row):
    return Path(row.get('path') or folder/row['file'])


def inventory(folder, manifest):
    known = {r['file']: r for r in manifest['videos']}
    rows = [{'file': p.name, 'sha256': digest(p),
             'status': ('annotated' if known[p.name].get('sha256') == digest(p) else 'changed_needs_review')
             if p.name in known else 'new_needs_roi_and_event_annotation'}
            for p in sorted(folder.glob('*.mp4'))]
    present = {row['file'] for row in rows}
    for row in manifest['videos']:
        if row['file'] in present or not row.get('path'):
            continue
        path = video_path(folder, row)
        current = digest(path) if path.is_file() else None
        rows.append({'file': row['file'], 'path': str(path), 'sha256': current,
                     'status': ('annotated' if current == row.get('sha256') else
                                'changed_needs_review' if current else 'missing')})
    return rows


def expanded_events(row):
    if row.get('events') is not None:
        return row['events']
    before, after = row.get('event_window_s', [-.35, .65])
    uncertainty = row.get('onset_uncertainty_s', .25)
    return [{'hero': row['event_hero'],
             'visible_interval_s': [round(t + before, 3), round(t + after, 3)],
             'onset_s': round(t + before, 3), 'onset_uncertainty_s': uncertainty}
            for t in row.get('event_onsets_s', [])]


def expanded_backgrounds(row):
    if row.get('background_intervals_s') is not None:
        return row['background_intervals_s']
    before, after = row.get('background_before_events_s', [-3, -1])
    return [[round(t + before, 3), round(t + after, 3)]
            for t in row.get('event_onsets_s', [])]


def labels_for(times, row, labels):
    # Unannotated/transition intervals remain excluded, not invented negatives.
    targets = np.full(len(times), -1, dtype=int)
    if row.get('background_sample_period_s'):
        start, end = row.get('background_sample_range_s', [float(times[0]), float(times[-1])])
        for timestamp in np.arange(start, min(end, float(times[-1])) + 1e-6,
                                   float(row['background_sample_period_s'])):
            targets[int(np.abs(times - timestamp).argmin())] = 0
    for a, b in expanded_backgrounds(row):
        targets[(times >= a) & (times <= b)] = 0
    for event in expanded_events(row):
        a, b = event['visible_interval_s']
        targets[(times >= a) & (times <= b)] = labels.index(event['hero'])
    return targets


def evaluate_events(predictions, references, tolerance=.3):
    unmatched = set(range(len(references)))
    matches, false_alarms = [], []
    for prediction in predictions:
        candidates = [j for j in sorted(unmatched) if references[j]['hero'] == prediction['hero']
                      and references[j]['visible_interval_s'][0] - tolerance <= prediction['first_seen_s']
                      <= references[j]['visible_interval_s'][1] + tolerance]
        if not candidates:
            false_alarms.append(prediction)
            continue
        j = candidates[0]
        unmatched.remove(j)
        ref = references[j]
        onset = ref.get('onset_s')
        matches.append({'hero': prediction['hero'], 'first_seen_s': prediction['first_seen_s'],
                        'reference_onset_s': onset,
                        'delay_s': None if onset is None else prediction['first_seen_s'] - onset,
                        'onset_uncertainty_s': ref.get('onset_uncertainty_s')})
    return {'matched': matches, 'missed': [references[j] for j in sorted(unmatched)],
            'unmatched_or_duplicate_predictions': false_alarms}


def knowledge_record(hero, catalog):
    row = next((h for h in catalog['heroes'] if h['hero'] == hero), None)
    skill = next((s for s in row['skills'] if s.get('is_ultimate', s.get('slot') == 3)), None) if row else None
    return {'skill_name': skill['name'] if skill else None,
            'base_cooldowns_s': skill.get('base_cooldowns_s', []) if skill else [],
            'cooldown_text': skill.get('cooldown_text') if skill else None,
            'remaining_cooldown_s': None, 'ready_at_s': None,
            'reason': '等级、冷却缩减、游戏时间与真实起算时刻未确认；只记录知识库基础值',
            'source': 'resources/knowledge/skill_catalog.json',
            'source_url': row.get('source_url') if row else None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['scan', 'train', 'replay'])
    parser.add_argument('--folder', type=Path, default=Path(r'C:\Users\Administrator\Desktop\闪现'))
    parser.add_argument('--annotations', type=Path, default=ROOT/'data/ultimate_examples/annotations.json')
    parser.add_argument('--out', type=Path, default=ROOT/'work/three-hero-experiment')
    parser.add_argument('--epochs', type=int, default=100)
    args = parser.parse_args()
    torch.set_num_threads(4)
    torch.manual_seed(42)
    np.random.seed(42)
    manifest = json.loads(args.annotations.read_text(encoding='utf-8'))
    available = inventory(args.folder, manifest)
    dump(args.out/'inventory.json', available)
    if args.command == 'scan':
        print(json.dumps(available, ensure_ascii=True, indent=2))
        return
    status = {r['file']: r['status'] for r in available}
    rows = [r for r in manifest['videos'] if status.get(r['file']) == 'annotated']
    if len(rows) != len(manifest['videos']):
        raise SystemExit('Annotated video changed or is missing; inspect inventory.json before training/replay')
    labels = ['background'] + sorted({e['hero'] for r in rows for e in expanded_events(r)})
    samples = []
    for row in rows:
        ts, frames = sample_video(video_path(args.folder, row), row['roi'])
        y = labels_for(ts, row, labels)
        samples.append((row, ts, frames, y))
    checkpoint = args.out/'hero_ultimate.pt'
    if args.command == 'train':
        xs = torch.cat([causal_clips_at(frames, np.flatnonzero(y >= 0))
                        for _, _, frames, y in samples])
        ys = torch.tensor(np.concatenate([y[y >= 0] for _, _, _, y in samples]), dtype=torch.long)
        counts = torch.bincount(ys, minlength=len(labels))
        if (counts == 0).any():
            raise SystemExit('Each class requires annotated examples')
        model = HeroUltimateNet(len(labels))
        opt = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.001)
        criterion = torch.nn.CrossEntropyLoss(weight=(1/counts.float()).sqrt())
        started = time.perf_counter()
        for epoch in range(args.epochs):
            model.train()
            losses = []
            for ids in torch.randperm(len(xs)).split(32):
                xb, yb = xs[ids].clone(), ys[ids]
                # Same photometric adjustment across all four frames.
                xb = (xb * (torch.rand(len(ids), 1, 1, 1)*.2+.9)).clamp(0, 1)
                opt.zero_grad(set_to_none=True)
                loss = criterion(model(xb), yb)
                loss.backward()
                opt.step()
                losses.append(float(loss.detach()))
            if (epoch + 1) % 10 == 0:
                print(f'epoch {epoch+1}/{args.epochs}, loss {np.mean(losses):.5f}', flush=True)
        training = {'seconds': time.perf_counter()-started, 'epochs': args.epochs,
                    'class_counts': dict(zip(labels, map(int, counts))), 'seed': 42}
        torch.save({'state_dict': model.state_dict(), 'labels': labels, 'experimental': True,
                    'deployment_eligible': False, 'evaluation_scope': 'training_video_resubstitution',
                    'training': training, 'manifest': manifest, 'sample_fps': SAMPLE_FPS}, checkpoint)
    else:
        payload = torch.load(checkpoint, map_location='cpu', weights_only=True)
        if payload['labels'] != labels or payload['manifest'] != manifest:
            raise SystemExit('Model/annotation mismatch; retrain or restore matching annotations')
        model = HeroUltimateNet(len(labels))
        model.load_state_dict(payload['state_dict'])
        training = payload['training']
    model.eval()
    catalog = json.loads((ROOT/'resources/knowledge/skill_catalog.json').read_text(encoding='utf-8'))
    report = {'scope': 'training_video_resubstitution', 'independent_test_videos': 0,
              'generalization_accuracy': None, 'deployment_eligible': False,
              'parameters': sum(p.numel() for p in model.parameters()),
              'checkpoint_bytes': checkpoint.stat().st_size, 'training': training,
              'labels': labels, 'threshold': .8, 'videos': [],
              'limitations': ['每位英雄仅一个来源；回放训练视频不证明实战准确率',
                             'ROI固定、无目标跟踪、无敌我识别；支持清单外动作未经测试',
                             '人工观察是助手标注，未由独立标注者复核',
                             '录像含慢放/循环，录像秒不等于游戏秒；不输出真实剩余冷却']}
    confusion = np.zeros((len(labels), len(labels)), dtype=int)
    for row, ts, frames, ys in samples:
        with torch.inference_mode():
            score_batches = []
            for indices in np.array_split(np.arange(len(frames)), max(1, int(np.ceil(len(frames)/64)))):
                score_batches.append(model(causal_clips_at(frames, indices)).softmax(1))
            scores = torch.cat(score_batches).numpy()
        tracker = EventTracker(labels)
        predictions = []
        for t, probs in zip(ts, scores):
            event = tracker.update(float(t), probs)
            if event:
                event['knowledge'] = knowledge_record(event['hero'], catalog)
                predictions.append(event)
        for y, p in zip(ys, scores.argmax(1)):
            if y >= 0:
                confusion[y, p] += 1
        timeline = [{'time_s': float(t), 'annotation': None if y < 0 else labels[y],
                     'probabilities': dict(zip(labels, map(float, p)))} for t, y, p in zip(ts, ys, scores)]
        dump(args.out/(Path(row['file']).stem+'.timeline.json'), timeline)
        result = {'file': row['file'], 'sampled_frames': len(ts), 'last_sample_s': float(ts[-1]),
                  'events': predictions, 'evaluation': evaluate_events(predictions, expanded_events(row)),
                  'labeled_frame_agreement': float((scores.argmax(1)[ys>=0] == ys[ys>=0]).mean())}
        report['videos'].append(result)
    # Batch-one CPU inference benchmark, not an end-to-end capture FPS claim.
    x = causal_clips_at(samples[0][2], [0])
    timing = []
    with torch.inference_mode():
        for i in range(110):
            start = time.perf_counter(); model(x)
            if i >= 10:
                timing.append((time.perf_counter()-start)*1000)
    report['cpu_model_only_ms'] = {'median': float(np.median(timing)), 'p95': float(np.percentile(timing, 95))}
    report['confusion_matrix'] = confusion.tolist()
    report['labeled_frame_agreement'] = float(confusion.trace()/confusion.sum())
    dump(args.out/'report.json', report)
    print(json.dumps(report, ensure_ascii=True, indent=2), flush=True)


if __name__ == '__main__':
    main()
