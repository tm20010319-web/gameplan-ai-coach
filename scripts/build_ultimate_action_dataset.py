"""Build a compact, reviewable temporal dataset from screen recordings.

This intentionally does not invent cast timestamps. It samples low-resolution
frames, scores adjacent motion, and writes candidate windows plus an annotation
template. A human or a later Qwen review fills ``label`` and ``hero`` before
training.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np


def video_info(path: Path):
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open video: {path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    capture.release()
    if fps <= 0 or count <= 0:
        raise RuntimeError(f"video has no readable timing: {path}")
    return fps, count, width, height


def sample_video(path: Path, out: Path, sample_fps: float, quality: int):
    fps, count, width, height = video_info(path)
    video_id = path.stem
    frame_dir = out / "frames" / video_id
    frame_dir.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(path))
    step = max(1, round(fps / sample_fps))
    rows = []
    previous = None
    frame_index = 0
    sample_index = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if frame_index % step:
            frame_index += 1
            continue
        small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (160, 72), interpolation=cv2.INTER_AREA)
        motion = float(cv2.absdiff(small, previous).mean()) if previous is not None else 0.0
        previous = small
        thumb = cv2.resize(frame, (320, 144), interpolation=cv2.INTER_AREA)
        relative = f"frames/{video_id}/{sample_index:06d}.jpg"
        cv2.imwrite(str(out / relative), thumb, [cv2.IMWRITE_JPEG_QUALITY, quality])
        rows.append({"id": f"{video_id}:{sample_index}", "video": str(path), "video_id": video_id,
                     "path": relative, "frame_index": frame_index, "timestamp_s": round(frame_index / fps, 3),
                     "motion_score": round(motion, 4), "label": "unknown", "hero": ""})
        sample_index += 1
        frame_index += 1
    capture.release()
    return rows, {"video": str(path), "video_id": video_id, "fps": fps, "frames": count,
                  "width": width, "height": height, "duration_s": count / fps,
                  "sample_fps": fps / step, "samples": len(rows)}


def windows(rows, *, quantile: float, radius_s: float, max_windows: int):
    if not rows:
        return []
    threshold = float(np.quantile([row["motion_score"] for row in rows], quantile))
    candidates = [row for row in rows if row["motion_score"] >= threshold]
    candidates.sort(key=lambda row: row["motion_score"], reverse=True)
    selected = []
    for row in candidates:
        start = max(0.0, row["timestamp_s"] - radius_s)
        end = row["timestamp_s"] + radius_s
        if any(max(start, item["start_s"]) <= min(end, item["end_s"]) for item in selected):
            continue
        selected.append({"video_id": row["video_id"], "video": row["video"], "start_s": round(start, 3),
                         "end_s": round(end, 3), "peak_motion": row["motion_score"], "hero": "", "label": "unknown"})
        if len(selected) >= max_windows:
            break
    return sorted(selected, key=lambda item: (item["video_id"], item["start_s"]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("videos", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, default=Path("work/ultimate-action-dataset"))
    parser.add_argument("--sample-fps", type=float, default=4)
    parser.add_argument("--motion-quantile", type=float, default=.90)
    parser.add_argument("--window-radius", type=float, default=1.5)
    parser.add_argument("--max-windows-per-video", type=int, default=160)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    all_rows, infos, all_windows = [], [], []
    for video in args.videos:
        rows, info = sample_video(video, args.out, args.sample_fps, quality=72)
        all_rows.extend(rows); infos.append(info)
        all_windows.extend(windows(rows, quantile=args.motion_quantile, radius_s=args.window_radius,
                                   max_windows=args.max_windows_per_video))
    with (args.out / "frames.jsonl").open("w", encoding="utf-8") as handle:
        for row in all_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (args.out / "candidate-windows.json").write_text(json.dumps(all_windows, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "videos.json").write_text(json.dumps(infos, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "ANNOTATE.md").write_text(
        "# 大招动作片段标注\n\n"
        "`candidate-windows.json` 只按画面变化筛选候选，不代表这些片段就是大招。\n"
        "请为每个窗口填写 `hero` 和 `label`：`cast_start`、`ongoing` 或 `background`。\n"
        "若窗口包含多个英雄动作，拆成更短窗口；英雄身份以 Qwen/加载槽位确认结果为准。\n"
        "相邻帧只能属于同一段视频的训练或测试一侧，训练脚本会按 video_id 分组切分。\n",
        encoding="utf-8")
    print(json.dumps({"out": str(args.out), "videos": infos, "samples": len(all_rows),
                      "candidate_windows": len(all_windows)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
