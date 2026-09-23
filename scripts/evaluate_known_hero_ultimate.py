"""Evaluate one known hero's ultimate detector on an independent full match.

The saved network is multiclass, but hero identity is supplied by the caller.
Only the requested hero probability is thresholded. Reference timestamps are
kept outside the training annotation manifest and are used only for scoring.
"""
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

from gameplan.skills.hero_ultimate_prototype import (
    EventTracker, HeroUltimateNet, causal_clips_at, sample_video,
)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def match_events(predictions, references, before_s: float, after_s: float):
    unmatched = set(range(len(references)))
    matches = []
    extra = []
    for prediction in predictions:
        t = prediction["first_seen_s"]
        candidates = [
            i for i in unmatched
            if references[i] - before_s <= t <= references[i] + after_s
        ]
        if not candidates:
            extra.append(prediction)
            continue
        i = min(candidates, key=lambda j: abs(t - references[j]))
        unmatched.remove(i)
        matches.append({
            "reference_s": references[i],
            "first_seen_s": t,
            "confirmed_s": prediction["confirmed_s"],
            "delay_s": round(t - references[i], 3),
            "score_at_confirmation": prediction["score"],
        })
    return matches, [references[i] for i in sorted(unmatched)], extra


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--hero", required=True)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--roi", nargs=4, type=float, required=True)
    parser.add_argument("--threshold", type=float, default=.8)
    parser.add_argument("--match-before-s", type=float, default=.5)
    parser.add_argument("--match-after-s", type=float, default=1.0)
    parser.add_argument("--scope", default="independent_video_known_hero_event_evaluation")
    args = parser.parse_args()

    torch.set_num_threads(4)
    started = time.perf_counter()
    payload = torch.load(args.model, map_location="cpu", weights_only=True)
    labels = payload["labels"]
    if args.hero not in labels:
        raise SystemExit(f"Hero {args.hero!r} is not in model labels: {labels}")
    hero_i = labels.index(args.hero)
    references = sorted(map(float, json.loads(args.references.read_text(encoding="utf-8"))))
    digest = sha256(args.video)
    training_hashes = {row.get("sha256") for row in payload.get("manifest", {}).get("videos", [])}
    if digest in training_hashes:
        raise SystemExit("Refusing independent evaluation: video hash occurs in training manifest")

    times, frames = sample_video(args.video, args.roi)
    model = HeroUltimateNet(len(labels))
    model.load_state_dict(payload["state_dict"])
    model.eval()
    hero_scores = []
    with torch.inference_mode():
        for indices in np.array_split(np.arange(len(frames)), max(1, int(np.ceil(len(frames) / 64)))):
            scores = model(causal_clips_at(frames, indices)).softmax(1)[:, hero_i]
            hero_scores.extend(map(float, scores))

    # Reuse the production confirmation/rearm policy with a conditioned
    # two-class vector. The supplied hero can trigger without winning the old
    # multiclass argmax.
    tracker = EventTracker(["background", args.hero], threshold=args.threshold, release_s=1.0)
    predictions = []
    for t, score in zip(times, hero_scores):
        event = tracker.update(float(t), np.asarray([1.0 - score, score]))
        if event:
            predictions.append(event)

    matches, missed, extra = match_events(
        predictions, references, args.match_before_s, args.match_after_s,
    )
    report = {
        "scope": args.scope,
        "video": str(args.video),
        "video_sha256": digest,
        "video_hash_in_training_manifest": False,
        "known_hero": args.hero,
        "model": str(args.model),
        "model_is_experimental": bool(payload.get("experimental", True)),
        "deployment_eligible": False,
        "conditioning": "caller_supplied_hero_identity; threshold_only_known_hero_softmax_probability",
        "roi": args.roi,
        "threshold": args.threshold,
        "confirmation_frames": 2,
        "release_s": 1.0,
        "reference_source": str(args.references),
        "reference_count": len(references),
        "reference_semantics": "first 4-fps frame showing ultimate-button cooldown transition, visually checked for Daji multi-orb effect",
        "match_window_s": [-args.match_before_s, args.match_after_s],
        "sampled_frames": len(times),
        "last_sample_s": float(times[-1]),
        "prediction_count": len(predictions),
        "matched_count": len(matches),
        "missed_count": len(missed),
        "extra_or_duplicate_count": len(extra),
        "recall": len(matches) / len(references) if references else None,
        "precision": len(matches) / len(predictions) if predictions else None,
        "matches": matches,
        "missed_reference_times_s": missed,
        "extra_or_duplicate_predictions": extra,
        "predictions": predictions,
        "elapsed_s": time.perf_counter() - started,
        "limitations": [
            "Reference timestamps were visually reviewed by the assistant, not an independent second annotator.",
            "The network was originally trained as a multiclass classifier; this is score conditioning, not a separately trained binary model.",
            "One independent match cannot establish deployment accuracy across skins, devices, resolutions, or effects settings.",
        ],
    }
    dump(args.out, report)
    print(json.dumps({k: report[k] for k in (
        "reference_count", "prediction_count", "matched_count", "missed_count",
        "extra_or_duplicate_count", "recall", "precision", "elapsed_s",
    )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
