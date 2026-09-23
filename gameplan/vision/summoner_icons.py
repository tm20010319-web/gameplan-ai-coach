"""Conservative scoreboard equipment reader; never infer spells from roles/casts."""
import io
import json
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ASSETS = Path(__file__).resolve().parents[2] / "data" / "summoner-icons"


@lru_cache(maxsize=1)
def templates():
    entries = json.loads((ASSETS / "manifest.json").read_text(encoding="utf-8"))
    return [(entry["skill"], cv2.imread(str(ASSETS / entry["file"]))) for entry in entries]


def match_icon(region):
    if region is None or region.size == 0:
        return None
    scores = []
    for skill, original in templates():
        if original is None:
            continue
        best = -1
        for size in range(18, 33, 2):
            icon = cv2.resize(original, (size, size), interpolation=cv2.INTER_AREA)
            edge = max(2, round(size * .15))
            icon = icon[edge:-edge, edge:-edge]
            if region.shape[0] < icon.shape[0] or region.shape[1] < icon.shape[1]:
                continue
            score = float(cv2.matchTemplate(region, icon, cv2.TM_CCOEFF_NORMED).max())
            # Smaller glyphs contain fewer discriminating pixels. Require
            # stronger agreement so disputed partial fragments stay unknown.
            if size >= 22 or score >= .96:
                best = max(best, score)
        scores.append((best, skill))
    scores.sort(reverse=True)
    # A small similar fragment is insufficient to confirm equipped spells.
    # In particular, the disputed replay matches near .81 must stay unknown.
    if len(scores) < 2 or scores[0][0] < .90 or scores[0][0] - scores[1][0] < .12:
        return None
    score, skill = scores[0]
    return {"skill": {"晕眩": "眩晕"}.get(skill, skill), "score": round(score, 3)}


def read_equipment(prepared, known_heroes, *, readings=None, panel_scene=None):
    from gameplan.vision.hero_recognition import read_text
    picture = Image.open(io.BytesIO(prepared)).convert("RGB")
    width, height = picture.size
    if not 1.7 <= width / height <= 2.5:
        return []
    picture = picture.resize((round(width * 576 / height), 576))
    items = readings if readings is not None else read_text(picture)
    # Ordinary HUD pictures must not be mistaken for a scoreboard by the LLM.
    text = {str(item["text"]).strip() for item in items if item.get("score", 0) >= .9}
    if panel_scene is not None:
        if not panel_scene.get('panel'):
            return []
        # Share independently verified rows, including locally reread labels.
        anchors = panel_scene.get('hero_rows', [])
    else:
        if "战绩" not in text or len(text & {"属性", "英雄", "技能"}) < 2:
            return []
        anchors = [item for item in items if item.get("score", 0) >= .9 and item["text"] in known_heroes]
        if len({item["text"] for item in anchors}) < 6:
            return []
    image = cv2.cvtColor(np.asarray(picture), cv2.COLOR_RGB2BGR)
    output = []
    for item in anchors:
        box = np.asarray(item["box"])
        if box.shape != (4, 2):
            continue
        x, y = box.min(axis=0)
        bottom = box[:, 1].max()
        # Supported two-column scoreboard: name above its own spell icon.
        if not (.12 * image.shape[1] < x < .8 * image.shape[1] and 100 < y < 480):
            continue
        region = image[max(0, round(bottom+5)):min(576, round(bottom+45)),
                       max(0, round(x-8)):min(image.shape[1], round(x+33))]
        matched = match_icon(region)
        if matched:
            output.append({"hero": item["text"], "skill": matched["skill"], "confidence": matched["score"],
                           "source": "scoreboard_icon", "frame_index": 0,
                           "evidence": f"本地战绩行名称与官网图标比对；相似度 {matched['score']}（非准确率）"})
    return output
