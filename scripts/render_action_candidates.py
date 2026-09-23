"""Render candidate windows as labeled contact sheets for quick annotation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
from PIL import Image, ImageDraw


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--per-sheet", type=int, default=24)
    args = parser.parse_args()
    windows = json.loads((args.dataset / "candidate-windows.json").read_text(encoding="utf-8"))
    videos = {}
    for item in windows:
        videos.setdefault(item["video_id"], item["video"])
    out = args.dataset / "candidate-sheets"
    out.mkdir(exist_ok=True)
    for page, start in enumerate(range(0, len(windows), args.per_sheet), 1):
        subset = windows[start:start + args.per_sheet]
        tiles = []
        for offset, item in enumerate(subset):
            capture = cv2.VideoCapture(item["video"])
            capture.set(cv2.CAP_PROP_POS_MSEC, ((item["start_s"] + item["end_s"]) / 2) * 1000)
            ok, frame = capture.read(); capture.release()
            if not ok:
                continue
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(frame); image.thumbnail((320, 144))
            tile = Image.new("RGB", (320, 174), "#202020")
            tile.paste(image, (0, 28))
            ImageDraw.Draw(tile).text((5, 6), f"{start + offset:03d} {item['video_id']} {item['start_s']:.1f}-{item['end_s']:.1f}s", fill="white")
            tiles.append(tile)
        sheet = Image.new("RGB", (1280, ((len(tiles) + 3) // 4) * 174), "#111")
        for index, tile in enumerate(tiles):
            sheet.paste(tile, ((index % 4) * 320, (index // 4) * 174))
        sheet.save(out / f"page-{page:03d}.jpg", quality=88)
    (args.dataset / "annotations.json").write_text(json.dumps(windows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"sheets": (len(windows) + args.per_sheet - 1) // args.per_sheet,
                      "annotations": str(args.dataset / "annotations.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
