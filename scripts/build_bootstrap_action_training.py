"""Build an explicitly weak, match-grouped bootstrap set from reviewed clips.

The generated windows are correlated views of a few events.  They are useful
for exercising the training/inference path, but are not independent examples
and must never be reported as real-match accuracy.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "work" / "video-material-review-20260918" / "samples.json"
OUT = ROOT / "work" / "ultimate-action-dataset" / "bootstrap-training.json"


WINDOWS = {
    "aoyin-ultimate": ("cast_start", [(375.8, 378.2), (376.0, 378.4),
                                       (376.2, 378.6), (376.4, 378.8)]),
    "gaojianli-ultimate": ("cast_start", [(422.0, 424.4), (422.2, 424.6),
                                           (422.4, 424.8), (422.6, 425.0)]),
    "sunce-ship": ("ongoing", [(864.0, 866.4), (865.0, 867.4),
                                 (866.0, 868.4), (867.0, 869.4)]),
    "gaojianli-ongoing": ("ongoing", [(423.6, 426.0), (423.8, 426.2),
                                        (424.0, 426.4), (424.4, 426.8)]),
    "aoyin-before": ("background", [(373.8, 375.8), (374.0, 376.0),
                                      (374.2, 376.2), (374.4, 376.4)]),
    "gaojianli-before": ("background", [(421.4, 422.6), (421.6, 422.8),
                                          (421.8, 423.0), (422.0, 423.2)]),
}


def main():
    source = json.loads(SAMPLES.read_text(encoding="utf-8"))["samples"]
    indexed = {item["id"]: item for item in source}
    # The ongoing portion comes from the same reviewed Gao Jianli event.
    indexed["gaojianli-ongoing"] = indexed["gaojianli-ultimate"]
    rows = []
    for event_id, (label, windows) in WINDOWS.items():
        sample = indexed[event_id]
        for number, (start, end) in enumerate(windows, 1):
            rows.append({
                "video_id": sample["source_video"].removesuffix(".mp4"),
                "video": sample["original_source"],
                "start_s": start,
                "end_s": end,
                "hero": sample.get("hero"),
                "label": label,
                "source": "assistant_visual_review_bootstrap",
                "human_verified": False,
                "training_eligible": True,
                "event_group": event_id,
                "correlated_window": number,
                "independent_event": False,
                "evidence": sample["evidence"],
            })
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(OUT), "windows": len(rows),
                      "event_groups": len(WINDOWS), "human_verified": False},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
