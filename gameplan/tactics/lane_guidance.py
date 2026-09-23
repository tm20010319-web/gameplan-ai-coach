"""Explicit personal lane banners; no inference from hero roles or map icons."""
import io
import re
from PIL import Image

LANE_ADVICE = {
    "发育路": "优先补刀和保血，不单独探草；敌方打野和侧翼位置不明时不要压深，保留闪现与退路。团战从安全距离输出，不追出队友保护范围。",
    "对抗路": "先稳住线权和血量，观察对方打野位置；支援前处理兵线并确认队友能跟进，不为追残血冒进。",
    "中路": "处理兵线后报点，和队友一起支援；没有视野时不要独自探河道或进野区。",
    "打野": "先明确首轮路线和资源分配，靠近目标前确认线权；无可靠信息不强行入侵，取得优势后优先转塔或资源。",
    "辅助": "帮助队友取得线权，探草和游走时保持跟进距离；留意核心安全，确认队友到位后再开团或反打。",
}

def highlighted_lane(picture, item, crop_left):
    """Accept the large lane label on the centered gold assignment banner.

    Small heading text may disappear under video compression. A plain map
    label is insufficient: require the banner's position and gold surround.
    """
    import numpy as np
    box = np.asarray(item.get("box", []))
    if box.shape != (4, 2) or item.get("score", 0) < .95:
        return False
    x0, y0 = box.min(axis=0)
    x1, y1 = box.max(axis=0)
    x0 += crop_left
    x1 += crop_left
    w, h = picture.size
    if not (.4*w <= (x0+x1)/2 <= .6*w and 0 <= y0 < y1 <= .22*h):
        return False
    tw, th = x1-x0, y1-y0
    if not (.035*w <= tw <= .25*w and .015*h <= th <= .1*h):
        return False
    region = picture.crop((max(0,int(x0-tw)), max(0,int(y0-2*th)),
                           min(w,int(x1+tw)), min(h,int(y1+th))))
    rgb = np.asarray(region).astype(float)
    red, green, blue = rgb[:,:,0], rgb[:,:,1], rgb[:,:,2]
    gold = (red > 110) & (green > 75) & (red > blue*1.4) & (green > blue*1.15) & (red < green*2)
    # The glow must extend on both sides of the text, not just its letters.
    third = max(1, gold.shape[1]//3)
    return bool(gold[:,:third].mean() > .08 and gold[:,-third:].mean() > .08)


def read_lane_banner(prepared):
    from gameplan.vision.hero_recognition import read_text
    picture = Image.open(io.BytesIO(prepared)).convert("RGB")
    w, h = picture.size
    left = int(w*.18)
    top = picture.crop((left, 0, int(w*.82), int(h*.3)))
    readings = read_text(top)
    items = [re.sub(r"\s+", "", item["text"]) for item in readings if item.get("score", 0) >= .8]
    heading = any(re.search(r"本局.{0,4}分路|准备前往|我的分路|您的分路|你的分路", t) for t in items)
    found = set()
    for item in readings:
        if item.get("score", 0) < .8:
            continue
        text = re.sub(r"\s+", "", item["text"])
        match = re.fullmatch(r"(?:(?:准备前往|我的分路|您的分路|你的分路|本局.{0,4}分路)[:：]?)?(发育路|对抗路|中路|打野|游走|辅助)", text)
        if match:
            found.add("辅助" if match[1] == "游走" else match[1])
    if len(found) != 1:
        return None
    lane = next(iter(found))
    highlighted = any(re.sub(r"\s+", "", item["text"]) in (lane, "游走" if lane == "辅助" else lane)
                      and highlighted_lane(picture, item, left) for item in readings)
    if not heading and not highlighted:
        return None
    evidence = " / ".join(items)[:120] if heading else f"顶部金色分路横幅：{lane}"
    return {"lane": lane, "source": "personal_lane_banner", "evidence": evidence}
