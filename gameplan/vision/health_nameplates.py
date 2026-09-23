"""Read bound enemy names/levels around red health bars, including screen edges."""
import re

import cv2
import numpy as np
from PIL import Image, ImageOps

from gameplan.vision.hero_recognition import OCR_LOCK, ocr_engine, read_text, read_regions, region_vote
from gameplan.vision.enemy_bars import enemy_bars


def bar_level(picture, x, y):
    # Fixed HUD scale (576 high): the level circle is directly left of the bar.
    # Read its interior so blue, black and gold rims do not become extra digits.
    if x < 25 or y < 2 or y+16 > picture.height:
        return None
    crop=picture.crop((x-25,y-2,x-5,y+16)).convert('L')
    pixels=np.asarray(crop)
    threshold,_=cv2.threshold(pixels,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    if pixels.max()-int(pixels.min()) < 30:
        return None
    readings=[]
    rim_merged = False
    with OCR_LOCK:
        for cut in (max(25,round(threshold*.85)),min(220,round(threshold*1.1))):
            glyph=crop.point(lambda value:255 if value>cut else 0)
            if not .06 < (np.asarray(glyph)>0).mean() < .65:
                return None
            glyph=ImageOps.expand(ImageOps.invert(glyph),border=8,fill=255)
            glyph=glyph.resize((190,190),Image.Resampling.NEAREST)
            result,_=ocr_engine()(np.asarray(glyph),use_det=False,use_cls=False)
            if not result or len(result)!=1:
                return None
            text,score=result[0]
            # The rim can add a trailing dot or opening parenthesis. A merged
            # parenthesis requires a clean reread below; never accept decimals,
            # arbitrary suffixes or extra digits as a hero level.
            match = re.fullmatch(r'[(（]?([1-9]|1[0-5])[.。]?', text)
            if not match or score < .65:
                return None
            rim_merged = rim_merged or text.startswith(('(', '（'))
            text = match.group(1)
            readings.append((text,float(score)))
    if readings[0][0]!=readings[1][0]:
        return None
    if rim_merged or min(score for _, score in readings) < .92:
        # A dim gold rim can merge with a white digit. Only corroborate an
        # already consistent numeric reading, using the smaller interior and
        # two agreeing masks; disagreement never falls through to a guess.
        interior = picture.crop((x-23, y-3, x-5, y+14)).convert('L')
        inner_threshold, _ = cv2.threshold(np.asarray(interior), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        confirmed = []
        with OCR_LOCK:
            for cuts in ((max(25, round(inner_threshold*.85)), min(220, round(inner_threshold*1.1))), (165, 180)):
                confirmed = []
                for cut in cuts:
                    glyph = interior.point(lambda value: 255 if value > cut else 0)
                    if not .06 < (np.asarray(glyph) > 0).mean() < .65:
                        break
                    glyph = ImageOps.expand(ImageOps.invert(glyph), border=8, fill=255)
                    result, _ = ocr_engine()(np.asarray(glyph.resize((190, 190), Image.Resampling.NEAREST)),
                                            use_det=False, use_cls=False)
                    if not result or len(result) != 1:
                        break
                    text, score = result[0]
                    if score >= .92 and text != readings[0][0]:
                        return None
                    if text != readings[0][0] or score < .92:
                        break
                    confirmed.append((text, float(score)))
                if len(confirmed) == 2:
                    break
        if len(confirmed) != 2:
            return None
        readings = confirmed
    return {'level':int(readings[0][0]),'visible_text':readings[0][0],
            'confidence':min(score for _,score in readings),'frame_index':0}


def read_enemy_bars(picture, bindings, targets=()):
    from gameplan.skills.combat_evidence import normalized_name
    result = {'targets': [], 'hero_levels': []}
    enemies = [b for b in bindings if b.get('side') == 'enemy_roster' and len(normalized_name(b.get('nickname'))) >= 2]
    if not enemies:
        return result
    picture = picture.resize((round(picture.width*576/picture.height), 576))
    names = {normalized_name(b['nickname']) for b in enemies}
    def exact(text):
        value = normalized_name(text)
        return value if value in names else None
    candidates = []
    for x, y, w, h in enemy_bars(picture):
        left, top, right, bottom = max(0, x-15), max(0, y-60), min(picture.width, x+w+130), y-2
        local = [t for t in targets if left <= t['x']*picture.width <= right and top <= t['y']*576 <= bottom]
        ring = level_circle(picture, x, y)
        if not ring and not local:
            continue
        verified = []
        for pass_index in range(2):
            regions = [(normalized_name(t['nickname']), t['box']) for t in local] if pass_index == 0 else []
            if pass_index == 1:
                # Discover the actual text height within the wide bar-anchored ROI.
                for r in read_text(picture.crop((left, top, right, bottom))):
                    name = exact(r['text'])
                    if name and r['score'] >= .7:
                        lo, hi = np.asarray(r['box']).min(axis=0), np.asarray(r['box']).max(axis=0)
                        regions.append((name, [lo[0]+left, lo[1]+top, hi[0]+left, hi[1]+top]))
            boxes, owners = [], []
            for name, box in regions:
                if name not in names:
                    continue
                a, b, c, d = box
                tight = [max(left, a), max(top, b), min(right, c), min(bottom, d)]
                wide = [max(left, a-4), max(top, b-1), min(right, c+4), min(bottom, d)]
                if tight[2] <= tight[0] or tight[3] <= tight[1]:
                    continue
                owners.append((name, tight)); boxes.extend([wide, tight])
            readings = read_regions(picture, boxes)
            for index, (name, tight) in enumerate(owners):
                votes = [region_vote(r, exact, minimum_score=.7) for r in readings[index*2:index*2+2]]
                if votes[0][0] == votes[1][0] == name:
                    hits = [b for b in bindings if normalized_name(b.get('nickname')) == name]
                    if len(hits) == 1 and hits[0] in enemies:
                        verified.append((hits[0], tight, min(v[1] for v in votes)))
            if verified:
                break
        verified = list({entry[0]['hero']: entry for entry in verified}.values())
        if len(verified) != 1:
            continue
        binding, box, confidence = verified[0]
        level = bar_level(picture, x, y)
        target = {'hero': binding['hero'], 'nickname': binding['nickname'], 'box': list(map(float, box)),
                  'x': (box[0]+box[2])/2/picture.width, 'y': (box[1]+box[3])/2/576,
                  'bar': [x, y, w, h], 'nickname_confidence': confidence, 'source': 'ocr',
                  'evidence': {'bar': True, 'nickname': True, 'level_circle': bool(level) or ring, 'continuity': False}}
        candidates.append((target, level))
    for target, level in candidates:
        if sum(t['hero'] == target['hero'] for t, _ in candidates) != 1:
            continue
        if target['evidence']['level_circle']:
            result['targets'].append(target)
        else:
            result.setdefault('name_candidates', []).append(target)
        if level:
            result['hero_levels'].append({'hero': target['hero'], **level})
    return result


def level_circle(picture, x, y):
    """Badge geometry is independent of whether its level digit is readable."""
    if x < 32:
        return False
    gray = np.asarray(picture.crop((x-32, max(0, y-12), x-2, min(picture.height, y+23))).convert('L'))
    circles = cv2.HoughCircles(cv2.GaussianBlur(gray, (3, 3), 0), cv2.HOUGH_GRADIENT,
                              1, 16, param1=70, param2=12, minRadius=5, maxRadius=14)
    return circles is not None and len(circles[0]) == 1


