"""Local scoreboard identities and readable levels, scoped to one match.

Nicknames are matching keys, never hero names or instructions. No image hashes,
video timestamps, example lineups, or assumed hero levels are used.
"""
import io
import re
import hashlib
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import numpy as np
from PIL import Image, ImageOps

from gameplan.vision.hero_recognition import read_text, read_regions, region_vote


@lru_cache(maxsize=1)
def scene_workers():
    from gameplan.vision.hero_recognition import initialize_ocr_worker
    return ThreadPoolExecutor(max_workers=3, initializer=initialize_ocr_worker,
                              thread_name_prefix='scene-ocr')


@lru_cache(maxsize=1)
def warm_scene_workers():
    picture = Image.new('RGB', (1280, 576), (20, 20, 20))
    futures = [scene_workers().submit(read_text, picture) for _ in range(3)]
    for future in futures:
        future.result()


def read_scenes(pictures, known_heroes, bindings=(), *, cache):
    """Read independent anchors together, then update the match cache in order."""
    signature = tuple(sorted((b['hero'], b['nickname'], b['side']) for b in bindings))
    keys = [(hashlib.sha256(pixels).digest(), signature) for pixels in pictures]
    pending = {}
    for key, pixels in zip(keys, pictures):
        if key not in cache and key not in pending:
            readings = cached_readings(pixels, cache)
            pending[key] = scene_workers().submit(_read_scene, pixels, known_heroes, bindings,
                                                 **({'readings': readings} if readings is not None else {}))
    results = []
    for key in keys:
        result = cache.pop(key) if key in cache else pending[key].result()
        cache[key] = deepcopy(result)
        results.append(deepcopy(result))
    while len(cache) > 32:
        cache.popitem(last=False)
    return results


def cached_readings(pixels, cache):
    """OCR text is image evidence, independent of which hero bindings used it."""
    digest = hashlib.sha256(pixels).digest()
    for (image_digest, _), scene in (cache or {}).items():
        if image_digest == digest and scene is not None:
            return deepcopy(scene.get('readings'))
    return None


def normalized_name(value):
    return re.sub(r"[^\w\u4e00-\u9fff]", "", value or "")


def clock_label(value):
    # A HUD clock may acquire a trailing punctuation mark in OCR (11:27.).
    # Normalize formatting only, never turn arbitrary text/digits into a clock.
    text = value.strip().rstrip('.。').strip().replace('：', ':')
    return bool(re.fullmatch(r'\d{1,2}:[0-5]\d', text))


def scoreboard_rows(anchors, kda_rows, width):
    """Recover unreadable vertical tabs only from two aligned team tables."""
    sides = [[], []]
    for anchor in anchors:
        x, y = anchor['box'].mean(axis=0)
        if not (.12*width < x < .8*width and 100 < y < 480):
            continue
        side = int(x >= width*.5)
        if all(abs(y-prior[1]) > 25 for prior in sides[side]):
            sides[side].append((float(x), float(y), float(anchor['box'][:, 0].max())))
    if sum(map(len, sides)) < 6 or any(len(rows) < 2 for rows in sides):
        return False
    if any(max(r[1] for r in rows)-min(r[1] for r in rows) < 100
           or max(r[0] for r in rows)-min(r[0] for r in rows) > width*.06 for rows in sides):
        return False
    if sum(any(abs(left[1]-right[1]) < 12 for right in sides[1]) for left in sides[0]) < 2:
        return False
    # OCR can miss a slash or a hero independently. The opposite team still
    # anchors the same horizontal row; never use a number as a cooldown read.
    row_y = [row[1] for rows in sides for row in rows]
    for side, anchors_on_side in enumerate(sides):
        matched_y = []
        for row in kda_rows:
            x, y = row['box'].mean(axis=0)
            if (int(x >= width*.5) == side
                    and row['box'][:, 0].min() > max(a[2] for a in anchors_on_side)
                    and any(abs(y-anchor_y) < 12 for anchor_y in row_y)
                    and all(abs(y-prior) > 25 for prior in matched_y)):
                matched_y.append(float(y))
        if len(matched_y) < 2:
            return False
    return True


def scoreboard_hero_rows(anchors, width):
    """A hero-named nickname beside a label is still the same player row."""
    rows = []
    for anchor in sorted(anchors, key=lambda item: float(item['box'][:, 1].mean())):
        x = float(anchor['box'][:, 0].min())
        y = float(anchor['box'][:, 1].mean())
        if not (.12 * width < x < .8 * width and 100 < y < 480):
            continue
        side = int(x >= width * .5)
        row = next((r for r in rows if r['side'] == side and abs(r['y'] - y) < 18), None)
        if row is None:
            rows.append({'side': side, 'y': y, 'anchor': anchor})
        elif x < float(row['anchor']['box'][:, 0].min()):
            row['anchor'] = anchor
    return [row['anchor'] for row in rows]


def read_scene(pixels, known_heroes, bindings=(), *, readings=None, cache=None):
    if cache is None or readings is not None:
        return _read_scene(pixels,known_heroes,bindings,readings=readings)
    key=(hashlib.sha256(pixels).digest(),tuple(sorted((b['hero'],b['nickname'],b['side']) for b in bindings)))
    if key in cache:
        cached=cache.pop(key);cache[key]=cached
        return deepcopy(cached)
    result=_read_scene(pixels,known_heroes,bindings,readings=cached_readings(pixels,cache))
    cache[key]=deepcopy(result)
    while len(cache)>32:
        cache.popitem(last=False)
    return result


def _read_scene(pixels, known_heroes, bindings=(), *, readings=None):
    picture = Image.open(io.BytesIO(pixels)).convert("RGB")
    width, height = picture.size
    if width < 700 or not 1.7 <= width / height <= 2.5:
        return None
    picture = picture.resize((round(width * 576 / height), 576))
    width, height = picture.size
    items = readings if readings is not None else read_text(picture)
    items = [{**item, "box": np.asarray(item["box"])} for item in items
             if np.asarray(item.get("box")).shape == (4, 2)]
    text = {item["text"].strip() for item in items if item.get("score", 0) >= .9}
    anchors = [item for item in items if item.get("score", 0) >= .9 and item["text"] in known_heroes]
    panel_labels = "战绩" in text and len(text & {"属性", "英雄", "技能"}) >= 2
    kda_rows=[i for i in items if i.get('score',0)>=.8 and re.match(r'^\d+\s*/\s*\d+\s*/\s*\d+',i['text'])]
    # Partial rosters are useful. Require scoreboard structure independently,
    # instead of requiring six perfectly recognized hero names to accept any.
    panel = panel_labels and (len({i['text'] for i in anchors})>=6 or
              (len(anchors)>=2 and len(kda_rows)>=4 and
               max(float(i['box'][:,1].mean()) for i in anchors)-min(float(i['box'][:,1].mean()) for i in anchors)>20))
    if not panel and '战绩' in text and text & {'属性', '英雄', '技能'}:
        panel = scoreboard_rows(anchors, kda_rows, width)
    if panel:
        # A verified table row may merge the hero label and nickname into one
        # OCR word. A prefix only proposes a crop; independent variants must
        # read the entire official label before that row gains an identity.
        merged, regions = [], []
        for item in items:
            name = item['text'].strip()
            choices = [h for h in known_heroes if name.startswith(h) and name != h]
            if item['score'] < .9 or not choices:
                continue
            hero = max(choices, key=len)
            x, y = item['box'].min(axis=0); right, bottom = item['box'].max(axis=0)
            side = x >= width*.5
            row_x = [a['box'][:,0].min() for a in anchors if (a['box'][:,0].min() >= width*.5) == side]
            if not row_x or abs(x-float(np.median(row_x))) > width*.025 or not 100 < y < 480:
                continue
            end = min(right, x + len(hero)*min(18.5, (bottom-y)*.95))
            merged.append((item, hero, end))
            regions.append((x-2, y-1, end, bottom+1))
        for (item, hero, end), votes in zip(merged, read_regions(picture, regions)):
            name, score = region_vote(votes, lambda s: s.strip() if s.strip() == hero else None, minimum_score=.9)
            if name:
                box = item['box'].copy(); box[1:3,0] = end
                anchors.append({**item, 'text': hero, 'box': box, 'score': score, 'source': 'local_hero_reread'})
        for item in items:
            # Bot nicknames can repeat the hero label in one OCR box. Only
            # split an exact repetition, and independently reread the label
            # half; arbitrary nickname substrings never supply an identity.
            name = item['text'].strip()
            half = len(name) // 2
            hero = name[:half]
            if item['score'] < .9 or hero not in known_heroes or name != hero * 2:
                continue
            box = item['box']
            x, y = box.min(axis=0); right, bottom = box.max(axis=0)
            if not (.12 * width < x < .8 * width and 100 < y < 480):
                continue
            middle = (x + right) / 2
            crop = picture.crop((round(x-4), round(y-5), round(middle), round(bottom+5)))
            reread = read_text(ImageOps.autocontrast(crop.convert('L')).convert('RGB'))
            if not any(r['text'].strip() == hero and r['score'] >= .9 for r in reread):
                continue
            label_box = np.array([[x, y], [middle, y], [middle, bottom], [x, bottom]])
            anchors.append({**item, 'text': hero, 'box': label_box, 'source': 'local_hero_reread'})
        for item in items:
            name=item['text'].strip()
            if item['score']>=.9 or item['score']<.65 or not 2<=len(name)<=4:continue
            choices=[hero for hero in known_heroes if len(hero)==len(name) and sum(a!=b for a,b in zip(hero,name))<=1]
            if not choices:continue
            box=item['box'];x,y=box.min(axis=0);right,bottom=box.max(axis=0)
            if not (.12*width<x<.8*width and 100<y<480):continue
            crop=picture.crop((round(x-3),round(y-3),round(right+3),round(bottom+3)))
            reread=read_text(crop.resize((crop.width*4,crop.height*4)))
            exact={r['text'].strip() for r in reread if r['score']>=.7 and r['text'].strip() in choices}
            if not exact and name in known_heroes and item['score'] >= .7:
                # A faint exact label may disappear when its colored pixels
                # are enlarged. Recover it only inside a verified scoreboard
                # row, with two consistent high-confidence contrast readings.
                # Do not use this path to turn a fuzzy word into a roster hero.
                gray = ImageOps.autocontrast(crop.convert('L'))
                confirmations = []
                for variant in (gray, ImageOps.invert(gray)):
                    enhanced = read_text(variant.convert('RGB').resize((crop.width*4, crop.height*4)))
                    names = {r['text'].strip() for r in enhanced if r['score'] >= .85}
                    confirmations.append(names == {name})
                if all(confirmations):
                    exact = {name}
            if len(exact)==1:
                anchors.append({**item,'text':exact.pop(),'source':'local_hero_reread'})
        anchors = scoreboard_hero_rows(anchors, width)
        anchors.sort(key=lambda i:(round(float(i['box'][:,1].mean())/12),float(i['box'][:,0].mean())))
    clock = any(clock_label(i["text"]) and i["score"] >= .9
                and i["box"][:, 1].mean() < 60 for i in items)
    # The combat target name/health HUD can overlap the game clock. Require
    # independent, spatially constrained game controls plus performance HUD.
    bottom_controls={i['text'] for i in items if i['score']>=.95 and i['box'][:,1].mean()>height*.82}
    # Window capture includes a title bar. Keep the performance HUD in the
    # same narrow top band as the clock; OCR may split FPS and ping into two
    # adjacent words. Require their alignment instead of one merged string.
    top_hud = [i for i in items if i['score'] >= .9 and i['box'][:, 1].mean() < 60]
    performance = any(
        re.search(r'FPS\s*\d+', fps['text'], re.I)
        and re.search(r'\d+\s*ms', ping['text'], re.I)
        and abs(fps['box'][:, 1].mean() - ping['box'][:, 1].mean()) < 10
        and 0 <= ping['box'][:, 0].mean() - fps['box'][:, 0].mean() < width * .12
        for fps in top_hud for ping in top_hud)
    hud_without_clock=performance and {'回城','恢复','闪现'}<=bottom_controls
    gameplay_clock=clock or hud_without_clock
    if not panel and not gameplay_clock:
        from gameplan.vision.loading_evidence import read_loading
        loading = read_loading(picture, items, known_heroes)
        if loading:
            return loading
    bar_scene = None
    # An untargetable ultimate can replace the enemy nickname while cooldown
    # digits cover two control labels. Independent HUD evidence still proves
    # gameplay; enemy attribution remains gated on actual name/track evidence.
    independent_hud = clock and performance and bool(bottom_controls & {"回城", "恢复", "闪现"})
    if not panel and not (independent_hud or gameplay_clock and len(text & {"回城", "恢复", "闪现"}) >= 2):
        # A cooldown can cover the recovery/Flash label. A real, bound enemy
        # nameplate plus the clock and one remaining HUD label still identifies
        # gameplay; a clock or a random hero word alone does not.
        if not gameplay_clock or not (text & {"回城", "恢复", "闪现"}):
            return None
        from gameplan.vision.health_nameplates import read_enemy_bars
        # Reuse exact full-frame name readings as anchors for the independent
        # red-bar geometry check. Requiring two extra crop OCR passes here can
        # hide a clearly readable enemy when a cooldown obscures a HUD label.
        named_targets=[]
        for item in items:
            hits=[b for b in bindings if b.get('side')=='enemy_roster' and
                  normalized_name(b['nickname'])==normalized_name(item['text'])]
            if item['score']>=.9 and len(hits)==1 and len(normalized_name(item['text']))>=3:
                box=item['box']
                named_targets.append({'hero':hits[0]['hero'],'nickname':hits[0]['nickname'],
                    'box':[round(float(v)) for v in (*box.min(axis=0),*box.max(axis=0))],
                    'x':float(box[:,0].mean())/width,'y':float(box[:,1].mean())/height})
        bar_scene=read_enemy_bars(picture,bindings,named_targets)
        if not bar_scene['targets']:
            return None
    result = {"panel": panel, "bindings": [], "hero_levels": [], "targets": [],
              "ally_roster": [], "enemy_roster": [], "readings": items}
    if panel:
        result['hero_rows'] = anchors
        # Batch recognition on complete lower-left portrait labels. The old
        # narrow x-63..x-44 crop cut the leading 1 off visible levels 14/15.
        level_boxes = []
        for anchor in anchors:
            x = anchor['box'][:,0].min(); bottom = anchor['box'][:,1].max()
            level_boxes.append((x-72, bottom+18, x-44, bottom+38))
        level_readings = read_regions(picture, level_boxes)
        nickname_owners, nickname_boxes = [], []
        for anchor in anchors:
            x, y = anchor['box'].min(axis=0); end, bottom = anchor['box'].max(axis=0)
            candidates = []
            for item in items:
                a, b = item['box'].min(axis=0); c, d = item['box'].max(axis=0)
                if abs((b+d-y-bottom)/2) > 9 or item['score'] < .7 or '/' in item['text']:
                    continue
                if end+8 < a < x+180 and len(normalized_name(item['text'])) >= 3:
                    candidates.append((a,b,c,d))
                elif abs(a-x) < 4 and item['text'].startswith(anchor['text']) and c > end+12:
                    candidates.append((end+1,b,c,d))
            if len(candidates) == 1:
                a,b,c,d = candidates[0]
                nickname_owners.append(anchor['text'])
                nickname_boxes.extend([(a,b,c,d),(a-2,b-1,c+2,d+1)])
        nickname_votes = read_regions(picture, nickname_boxes)
        verified_names = {}
        def nickname(text):
            value = normalized_name(text)
            return value if 2 <= len(value) <= 24 and not value.isdecimal() else None
        for index, hero in enumerate(nickname_owners):
            votes = [region_vote(v, nickname, minimum_score=.85) for v in nickname_votes[index*2:index*2+2]]
            if votes[0][0] and votes[0][0] == votes[1][0]:
                verified_names[hero] = votes[0][0]
        for anchor, level_votes in zip(anchors, level_readings):
            hero = anchor["text"]
            box = anchor["box"]
            x, y = box.min(axis=0); bottom = box[:, 1].max()
            if not (.12 * width < x < .8 * width and 100 < y < 480):
                continue
            side = "ally_roster" if x < width * .5 else "enemy_roster"
            if hero not in result[side]:
                result[side].append(hero)
            names = [i for i in items if i["score"] >= .9 and
                     box[:, 0].max() + 8 < i["box"][:, 0].min() < x + 180 and
                     abs(i["box"][:, 1].mean() - box[:, 1].mean()) < 9 and
                     len(normalized_name(i["text"])) >= 3 and not normalized_name(i["text"]).isdigit() and "/" not in i["text"]]
            if hero in verified_names:
                result['bindings'].append({'hero':hero,'nickname':verified_names[hero],'side':side})
            elif len(names) == 1:
                result["bindings"].append({"hero": hero, "nickname": names[0]["text"], "side": side})
            # Read the portrait's lower-left level label, excluding the separate
            # summon/ultimate numbers to its right. Unsupported glyphs stay blank.
            level, score = region_vote(level_votes, lambda s: s.strip() if re.fullmatch(r'(?:[1-9]|1[0-5])', s.strip()) else None,
                                      minimum_score=.9)
            if not level:
                # A single dim digit can vanish in contrast variants. Require
                # a wider independent crop to corroborate an existing strong
                # variant; never manufacture a level from one OCR score.
                strong = {v['text'].strip() for v in level_votes if v['score'] >= .92
                          and re.fullmatch(r'(?:[1-9]|1[0-5])', v['text'].strip())}
                agreed, _ = region_vote(level_votes, lambda s: s.strip() if re.fullmatch(r'(?:[1-9]|1[0-5])', s.strip()) else None,
                                        minimum_score=.7)
                if not strong and agreed:
                    strong = {agreed}
                if len(strong) == 1:
                    for top, right in ((14, -42), (10, -38)):
                        crop = picture.crop((round(x-72), round(bottom+top), round(x+right), round(bottom+40)))
                        extra = [v for v in read_text(crop.resize((crop.width*4,crop.height*4)))
                                 if v['score'] >= .92 and re.fullmatch(r'(?:[1-9]|1[0-5])', v['text'].strip())]
                        if len(extra) == 1:
                            if extra[0]['text'].strip() in strong:
                                level, score = extra[0]['text'].strip(), extra[0]['score']
                            break  # A contradictory wider read must stay unknown.
            if level:
                result["hero_levels"].append({"hero": hero, "level": int(level),
                    "visible_text": level, "confidence": score, "frame_index": 0})
        return result
    from gameplan.vision.health_nameplates import read_enemy_bars
    hints = []
    for item in items:
        name = normalized_name(item['text'])
        hits = [b for b in bindings if b.get('side') == 'enemy_roster'
                and normalized_name(b.get('nickname')) == name and len(name) >= 2]
        if item['score'] >= .7 and len(hits) == 1:
            box = item['box']
            hints.append({'hero': hits[0]['hero'], 'nickname': hits[0]['nickname'],
                          'box': [float(v) for v in (*box.min(axis=0), *box.max(axis=0))],
                          'x': float(box[:, 0].mean())/width, 'y': float(box[:, 1].mean())/height})
    extra = bar_scene if bar_scene is not None else read_enemy_bars(picture, bindings, hints)
    result.update(extra)
    return result



def merge_bindings(previous, observed):
    result = {b["hero"]: b for b in previous}
    for item in observed:
        if not item.get('hero') or not item.get('nickname'):
            continue
        if item.get('side') == 'unknown' and result.get(item['hero'], {}).get('side') in ('ally_roster', 'enemy_roster'):
            continue
        result[item["hero"]] = item
    counts = {}
    for item in result.values():
        name = normalized_name(item["nickname"])
        counts[name] = counts.get(name, 0) + 1
    return [item for item in result.values() if counts[normalized_name(item["nickname"])] == 1][:10]
