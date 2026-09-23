"""Train a per-hero background/ultimate model from append-only annotations."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import torch
import torch.nn.functional as F

from gameplan.skills.hero_ultimate_prototype import (
    CLIP_LEN, HeroUltimateNet, causal_clips_at, sample_video, SAMPLE_FPS,
)


ROOT = Path(__file__).resolve().parents[1]


def row_heroes(row):
    if row.get("event_hero"):
        return {row["event_hero"]}
    return {event["hero"] for event in row.get("events", [])}


def event_intervals(row, hero):
    if row.get("event_onsets_s") is not None and row.get("event_hero") == hero:
        before, after = row.get("event_window_s", [-.35, .65])
        return [(float(t + before), float(t + after)) for t in row["event_onsets_s"]]
    return [tuple(map(float, event["visible_interval_s"]))
            for event in row.get("events", []) if event["hero"] == hero]


def background_intervals(row):
    if row.get("background_intervals_s") is not None:
        return [tuple(map(float, interval)) for interval in row["background_intervals_s"]]
    before, after = row.get("background_before_events_s", [-3, -1])
    return [(float(t + before), float(t + after)) for t in row.get("event_onsets_s", [])]


def targets_for(times, row, hero):
    targets = np.full(len(times), -1, dtype=np.int64)
    if row.get("background_sample_period_s"):
        start, end = row.get("background_sample_range_s", [float(times[0]), float(times[-1])])
        for timestamp in np.arange(start, min(end, float(times[-1])) + 1e-6,
                                   float(row["background_sample_period_s"])):
            targets[int(np.abs(times - timestamp).argmin())] = 0
    for start, end in background_intervals(row):
        targets[(times >= start) & (times <= end)] = 0
    for start, end in event_intervals(row, hero):
        targets[(times >= start) & (times <= end)] = 1
    return targets


def augment(x):
    """Clip-consistent location and color changes to reduce recording memorization."""
    batch, channels, height, width = x.shape
    padded = F.pad(x, (8, 8, 6, 6), mode="reflect")
    shifted = torch.empty_like(x)
    offsets_x = torch.randint(0, 17, (batch,))
    offsets_y = torch.randint(0, 13, (batch,))
    for i, (ox, oy) in enumerate(zip(offsets_x, offsets_y)):
        shifted[i] = padded[i, :, oy:oy + height, ox:ox + width]
    flip = torch.rand(batch) < .5
    shifted[flip] = shifted[flip].flip(-1)

    clips = shifted.view(batch, CLIP_LEN, 3, height, width)
    mean = clips.mean(dim=(1, 2, 3, 4), keepdim=True)
    contrast = torch.empty(batch, 1, 1, 1, 1).uniform_(.75, 1.25)
    brightness = torch.empty(batch, 1, 1, 1, 1).uniform_(.75, 1.25)
    channel_gain = torch.empty(batch, 1, 3, 1, 1).uniform_(.8, 1.2)
    clips = ((clips - mean) * contrast + mean) * brightness * channel_gain
    grayscale = torch.rand(batch) < .15
    if grayscale.any():
        gray = clips[grayscale].mean(2, keepdim=True)
        clips[grayscale] = gray.expand(-1, -1, 3, -1, -1)
    clips = clips + torch.randn_like(clips) * .015
    return clips.clamp(0, 1).view(batch, channels, height, width)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hero", required=True)
    parser.add_argument("--annotations", type=Path,
                        default=ROOT / "data/ultimate_examples/annotations.json")
    parser.add_argument("--folder", type=Path,
                        default=Path(r"C:\Users\Administrator\Desktop\闪现"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=100)
    args = parser.parse_args()

    torch.set_num_threads(4)
    torch.manual_seed(42)
    np.random.seed(42)
    manifest = json.loads(args.annotations.read_text(encoding="utf-8"))
    rows = [row for row in manifest["videos"]
            if row.get("split", "train") == "train" and args.hero in row_heroes(row)]
    if not rows:
        raise SystemExit(f"No annotations for hero {args.hero!r}")

    samples = []
    for row in rows:
        path = Path(row.get("path") or args.folder / row["file"])
        if row.get("sha256"):
            with path.open("rb") as stream:
                current_hash = hashlib.file_digest(stream, "sha256").hexdigest()
            if current_hash != row["sha256"]:
                raise SystemExit(f"Annotated video changed and needs review: {path}")
        times, frames = sample_video(path, row["roi"])
        targets = targets_for(times, row, args.hero)
        indices = np.flatnonzero(targets >= 0)
        samples.append((row, causal_clips_at(frames, indices), targets[indices]))
    xs = torch.cat([sample[1] for sample in samples])
    ys = torch.tensor(np.concatenate([sample[2] for sample in samples]), dtype=torch.long)
    counts = torch.bincount(ys, minlength=2)
    if (counts == 0).any():
        raise SystemExit(f"Both background and ultimate examples are required; counts={counts.tolist()}")

    model = HeroUltimateNet(2)
    optimizer = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.002)
    criterion = torch.nn.CrossEntropyLoss(weight=(1 / counts.float()).sqrt())
    started = time.perf_counter()
    for epoch in range(args.epochs):
        model.train()
        losses = []
        for ids in torch.randperm(len(xs)).split(32):
            xb, yb = augment(xs[ids].clone()), ys[ids]
            optimizer.zero_grad(set_to_none=True)
            loss = criterion(model(xb), yb)
            loss.backward()
            optimizer.step()
            losses.append(float(loss.detach()))
        if (epoch + 1) % 10 == 0:
            print(f"epoch {epoch + 1}/{args.epochs}, loss {np.mean(losses):.5f}", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "state_dict": model.state_dict(),
        "labels": ["background", args.hero],
        "known_hero_binary": True,
        "experimental": True,
        "deployment_eligible": False,
        "evaluation_scope": "training_video_only_until_new_independent_video",
        "sample_fps": SAMPLE_FPS,
        "training": {
            "seconds": time.perf_counter() - started,
            "epochs": args.epochs,
            "class_counts": {"background": int(counts[0]), args.hero: int(counts[1])},
            "seed": 42,
            "augmentation": "clip-consistent translation, horizontal flip, color/contrast, occasional grayscale, noise",
        },
        "manifest": {**manifest, "videos": rows},
    }
    torch.save(payload, args.out)
    print(json.dumps({
        "model": str(args.out),
        "hero": args.hero,
        "training_videos": len(rows),
        "class_counts": payload["training"]["class_counts"],
        "training_seconds": payload["training"]["seconds"],
    }, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
