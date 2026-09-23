"""Bootstrap the ultimate-action prototype from one untrimmed replay.

This is deliberately a prototype path: it creates pseudo labels from temporal
motion plus purple ultimate-effect pixels, trains the existing tiny 3-D CNN on
temporal splits, and marks the checkpoint experimental.  It is useful for
testing the end-to-end monitor wiring, not for claiming hero-general accuracy.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gameplan.skills.action_model import LABELS, build_network


def probe(path: Path):
    raw = subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate",
        "-of", "json", str(path)], text=True)
    stream = json.loads(raw)["streams"][0]
    num, den = (int(x) for x in stream["r_frame_rate"].split("/"))
    return int(stream["width"]), int(stream["height"]), num / den


def frames(path: Path, sample_fps: float = 8):
    width, height, fps = probe(path)
    command = ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo",
               "-pix_fmt", "bgr24", "pipe:1"]
    proc = subprocess.Popen(command, stdout=subprocess.PIPE)
    size = width * height * 3
    step = max(1, round(fps / sample_fps))
    result = []
    index = 0
    while True:
        payload = proc.stdout.read(size)
        if len(payload) != size:
            break
        if index % step == 0:
            result.append((index / fps, np.frombuffer(payload, np.uint8).reshape(height, width, 3).copy()))
        index += 1
    proc.wait()
    if not result:
        raise RuntimeError("ffmpeg could not decode any frames")
    return result, fps


def make_samples(video: Path, hero: str, clip_len=8):
    sampled, fps = frames(video)
    purple, motion = [], []
    previous = None
    for timestamp, frame in sampled:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (115, 35, 45), (179, 255, 255))
        purple.append(float((mask > 0).mean()))
        small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (112, 64))
        motion.append(0.0 if previous is None else float(cv2.absdiff(small, previous).mean()))
        previous = small
    # Purple field effects and motion together are more stable than either one.
    effect_threshold = max(float(np.quantile(purple, .65)), .012)
    motion_threshold = float(np.quantile(motion, .55))
    active = np.asarray([(p >= effect_threshold and m >= motion_threshold * .45)
                         for p, m in zip(purple, motion)])
    if not active.any():
        active = np.asarray([p >= effect_threshold for p in purple])
    first = int(np.argmax(active)) if active.any() else len(active) // 3
    last = len(active) - 1 - int(np.argmax(active[::-1])) if active.any() else min(len(active) - 1, first + 12)
    # A replay that starts after the visual effect is already visible has no
    # true negative or onset frame.  Reserve an initial context slice as
    # background and a short transition slice as cast_start so the prototype
    # still exercises all three classifier outputs; this is recorded as
    # pseudo-label data and remains experimental.
    if first <= 1 or last >= len(active) - 2:
        first = max(3, len(active) // 5)
        last = max(first + 3, int(len(active) * .9))
    start = sampled[first][0]
    end = sampled[last][0]
    duration = sampled[-1][0]
    windows = []
    # Generate temporally separated background/ongoing/cast-start examples.
    for i in range(0, len(sampled) - clip_len + 1, max(1, clip_len // 2)):
        t0, t1 = sampled[i][0], sampled[i + clip_len - 1][0]
        overlap = max(0.0, min(t1, end) - max(t0, start))
        if t0 <= start <= t1:
            label = "cast_start"
        elif overlap > max(.25, (t1 - t0) * .25):
            label = "ongoing"
        else:
            label = "background"
        windows.append({"video": str(video), "video_id": video.stem, "start_s": round(t0, 3),
                        "end_s": round(t1, 3), "label": label, "hero": hero,
                        "source": "purple_motion_pseudo", "training_eligible": True,
                        "event_group": "diaochan_single_replay", "correlated_window": True,
                        "independent_event": False})
    return windows, {"video": str(video), "fps": fps, "duration_s": duration,
                     "pseudo_cast_start_s": start, "pseudo_cast_end_s": end,
                     "effect_threshold": effect_threshold, "motion_threshold": motion_threshold,
                     "samples": len(sampled)}


def read_clip(item, clip_len=8, size=(112, 64)):
    sampled, _ = frames(Path(item["video"]), sample_fps=8)
    chosen = [frame for timestamp, frame in sampled if item["start_s"] <= timestamp <= item["end_s"]]
    if not chosen:
        chosen = [sampled[min(len(sampled) - 1, int(item["start_s"] * 8))][1]]
    chosen = [chosen[min(len(chosen) - 1, round(i * (len(chosen) - 1) / max(1, clip_len - 1)))] for i in range(clip_len)]
    gray = [cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), size).astype("float32") / 255 for frame in chosen]
    import torch
    return torch.from_numpy(np.stack(gray)).unsqueeze(0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("videos", type=Path, nargs="+")
    parser.add_argument("--heroes", nargs="+", help="与 videos 一一对应的英雄名称")
    parser.add_argument("--out", type=Path, default=Path("data/models/ultimate_action_prototype.pt"))
    parser.add_argument("--annotations", type=Path, default=Path("work/ultimate-action-prototype/annotations.json"))
    parser.add_argument("--epochs", type=int, default=8)
    args = parser.parse_args()
    heroes = args.heroes or []
    if heroes and len(heroes) != len(args.videos):
        raise SystemExit("--heroes 必须与视频数量一致")
    if not heroes:
        heroes = ["\u8c82\u8749" if "diaochan" in video.stem.lower() else "\u5415\u5e03" if "lubu" in video.stem.lower() else video.stem
                  for video in args.videos]
    items, infos = [], []
    for video, hero in zip(args.videos, heroes):
        rows, info = make_samples(video, hero)
        items.extend(rows); infos.append({**info, "hero": hero})
    args.annotations.parent.mkdir(parents=True, exist_ok=True)
    args.annotations.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    import torch
    torch.manual_seed(42)
    xs = torch.stack([read_clip(item) for item in items])
    ys = torch.tensor([LABELS.index(item["label"]) for item in items])
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = build_network().to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=1e-4)
    loss_fn = torch.nn.CrossEntropyLoss()
    for _ in range(args.epochs):
        model.train(); optimizer.zero_grad(set_to_none=True)
        loss = loss_fn(model(xs.to(device)), ys.to(device)); loss.backward(); optimizer.step()
    model.eval()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    summary = {"videos": infos, "model": str(args.out), "annotations": str(args.annotations), "device": device,
               "label_counts": {label: sum(item["label"] == label for item in items) for label in LABELS},
               "heroes": sorted({item["hero"] for item in items}), "deployment_eligible": False}
    torch.save({"state_dict": model.state_dict(), "labels": LABELS, "threshold": .72,
                "deployment_eligible": False, "experimental": True, "human_verified": False,
                "pseudo_label_info": summary}, args.out)
    info = summary
    print(json.dumps(info, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
