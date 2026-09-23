"""Train the optional lightweight temporal cast classifier.

The input is the edited ``candidate-windows.json`` produced by
``build_ultimate_action_dataset.py``. Training refuses incomplete labels and
requires at least two videos so adjacent frames from one replay cannot make a
misleading validation score.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np

from gameplan.skills.action_model import LABELS, build_network


def read_clip(item, clip_len=8, size=(112, 64)):
    capture = cv2.VideoCapture(item["video"])
    if not capture.isOpened():
        raise RuntimeError(f"cannot open {item['video']}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
    if fps <= 0:
        raise RuntimeError(f"missing fps for {item['video']}")
    times = np.linspace(float(item["start_s"]), float(item["end_s"]), clip_len)
    frames = []
    for timestamp in times:
        capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
        ok, frame = capture.read()
        if not ok:
            capture.release()
            raise RuntimeError(f"cannot read {item['video']} at {timestamp:.2f}s")
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        frames.append(cv2.resize(gray, size, interpolation=cv2.INTER_AREA).astype("float32") / 255.0)
    capture.release()
    import torch
    return torch.from_numpy(np.stack(frames, axis=0)).unsqueeze(0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("annotations", type=Path)
    parser.add_argument("--out", type=Path, default=Path("data/models/ultimate_action.pt"))
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    items = json.loads(args.annotations.read_text(encoding="utf-8"))
    if not isinstance(items,list) or any(not isinstance(item,dict) for item in items):
        raise SystemExit('annotations must be an array of JSON objects')
    report_path = args.report or args.out.with_suffix('.report.json')
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {'annotations':str(args.annotations), 'status':'checking_data',
              'metric_scope':'agreement_with_annotation_labels_not_real_match_accuracy',
              'annotation_sources':dict(Counter(item.get('source','unspecified') for item in items))}
    def write_report():
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    def stop(reason):
        report.update(status='insufficient_training_data',reason=reason)
        write_report()
        raise SystemExit(reason)
    valid = [item for item in items if item.get("label") in LABELS and item.get("video")
             and item.get("start_s") is not None and item.get('training_eligible',True)]
    videos = sorted({item.get("video_id") or item.get("video") for item in valid})
    missing = len(items) - len(valid)
    if missing:
        stop(f"{missing} candidate windows have no valid label; unknowns must be excluded from training")
    counts = {label: sum(item["label"] == label for item in valid) for label in LABELS}
    report['counts']=counts
    report['videos']=videos
    groups = sorted({item.get('event_group') for item in valid if item.get('event_group')})
    report['event_groups'] = groups
    report['independent_events'] = sum(item.get('independent_event') is True for item in valid)
    report['bootstrap_only'] = any(item.get('correlated_window') for item in valid)
    if len(videos) < 2:
        stop("at least two videos are required for a video-level holdout")
    if min(counts.values()) < 4:
        stop(f"need at least 4 labeled clips per class; got {counts}")
    import torch
    torch.manual_seed(42)
    torch.set_num_threads(4)
    from torch.utils.data import DataLoader, TensorDataset
    device = args.device if args.device == "cuda" and torch.cuda.is_available() else "cpu"
    holdout_video = videos[-1]
    train_items = [item for item in valid if (item.get("video_id") or item.get("video")) != holdout_video]
    test_items = [item for item in valid if (item.get("video_id") or item.get("video")) == holdout_video]
    split_counts={name:{label:sum(item['label']==label for item in rows) for label in LABELS}
                  for name,rows in [('train',train_items),('holdout',test_items)]}
    report.update(split_counts=split_counts,holdout_video=holdout_video)
    if any(count==0 for split in split_counts.values() for count in split.values()):
        stop('Each class must occur in both the training video and the held-out video')
    if not train_items or not test_items:
        raise SystemExit("video split produced an empty train or validation set")
    xs = torch.stack([read_clip(item) for item in train_items])
    ys = torch.tensor([LABELS.index(item["label"]) for item in train_items], dtype=torch.long)
    tx = torch.stack([read_clip(item) for item in test_items])
    ty = torch.tensor([LABELS.index(item["label"]) for item in test_items], dtype=torch.long)
    model = build_network().to(device)
    loader = DataLoader(TensorDataset(xs, ys), batch_size=min(8, len(xs)), shuffle=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=1e-4)
    criterion = torch.nn.CrossEntropyLoss()
    for _ in range(args.epochs):
        model.train()
        for xb, yb in loader:
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(xb.to(device)), yb.to(device))
            loss.backward(); optimizer.step()
    model.eval()
    with torch.inference_mode():
        prediction = torch.cat([model(batch.to(device)).argmax(dim=1).cpu() for batch in tx.split(8)])
    accuracy = float((prediction == ty).float().mean())
    confusion=[[int(((ty==i)&(prediction==j)).sum()) for j in range(len(LABELS))] for i in range(len(LABELS))]
    deployment_eligible = accuracy >= .75 and not report['bootstrap_only']
    rejection_reason = (None if deployment_eligible else
                        'correlated bootstrap windows are not independent validation' if report['bootstrap_only'] else
                        'holdout label agreement is below 0.75')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": model.state_dict(), "labels": LABELS, "threshold": .72,
                "train_videos": videos[:-1], "holdout_video": holdout_video,
                "counts": counts, "holdout_label_agreement": accuracy,
                "event_groups": groups, "bootstrap_only": report['bootstrap_only'],
                "deployment_eligible": deployment_eligible,
                "experimental":True,"human_verified":False}, args.out)
    report.update(status='experimental_model_trained' if deployment_eligible else 'experimental_model_rejected',
                  model=str(args.out),device=device, deployment_eligible=deployment_eligible,
                  rejection_reason=rejection_reason,
                  holdout_label_agreement=accuracy,confusion_matrix=confusion,labels=list(LABELS),
                  real_match_accuracy=None)
    write_report()
    print(json.dumps({"model": str(args.out), "device": device, "counts": counts,
                      "holdout_video": holdout_video, "holdout_label_agreement": accuracy}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
