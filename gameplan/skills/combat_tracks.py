"""Short optical-flow bridges between OCR anchors; never create a new identity."""
import cv2
import numpy as np
from collections import OrderedDict
from copy import deepcopy
import hashlib

MAX_IDENTITY_AGE_S = 1.5
MAX_MISSING_FRAMES = 3


def bridge(previous, current, target, *, gap_s, anchor_age_s):
    if not 0 < gap_s <= MAX_IDENTITY_AGE_S or not 0 <= anchor_age_s <= MAX_IDENTITY_AGE_S:
        return None
    height, width = previous.shape[:2]
    if current.shape != previous.shape:
        return None
    x, y = round(target["x"]*width), round(target["y"]*height)
    mask = np.zeros_like(previous)
    mask[max(0,y-10):min(height,y+75), max(0,x-42):min(width,x+42)] = 255
    points = cv2.goodFeaturesToTrack(previous, maxCorners=50, qualityLevel=.04, minDistance=4, mask=mask)
    if points is None or len(points) < 8:
        return None
    moved, status, _ = cv2.calcOpticalFlowPyrLK(previous, current, points, None, winSize=(21,21), maxLevel=2)
    if moved is None:
        return None
    backward, back_status, _ = cv2.calcOpticalFlowPyrLK(current, previous, moved, None, winSize=(21,21), maxLevel=2)
    if backward is None:
        return None
    keep = (status[:,0] == 1) & (back_status[:,0] == 1) & (np.linalg.norm(backward-points, axis=2)[:,0] < 1)
    if keep.sum() < 8 or keep.mean() < .7:
        return None
    displacements = (moved-points)[:,0][keep]
    shift = np.median(displacements,axis=0)
    if (np.linalg.norm(displacements-shift,axis=1) < 3).mean() < .75 or np.linalg.norm(shift) > width*.2:
        return None
    nx, ny = (x+float(shift[0]))/width, (y+float(shift[1]))/height
    if not .03 < nx < .97 or not .08 < ny < .88:
        return None
    result = {**target, "x": nx, "y": ny, "source": "short_flow"}
    dx, dy = map(float, shift)
    if target.get('box'):
        a, b, c, d = target['box']
        result['box'] = [a+dx, b+dy, c+dx, d+dy]
    if target.get('bar'):
        a, b, w, h = target['bar']
        result['bar'] = [a+dx, b+dy, w, h]
    return result


def advance(picture, observation, captured_at, *, bindings, enemies, memory):
    """One chronological identity path for both skill detectors.

    A recent OCR anchor may survive three missing-name frames and 1.5 seconds.
    Replayed/overlapping request frames reuse their past result, never a future
    identity. Uncertain crossings terminate continuity instead of swapping names.
    """
    from gameplan.vision.enemy_bars import enemy_bars
    from gameplan.skills.combat_evidence import normalized_name
    picture = picture.convert('RGB').resize((round(picture.width*576/picture.height), 576))
    gray = cv2.cvtColor(np.asarray(picture), cv2.COLOR_RGB2GRAY)
    signature = tuple(sorted((b.get('hero', ''), b.get('nickname', ''), b.get('side', '')) for b in bindings))
    if memory.get('signature') != signature:
        memory.clear()
        memory.update(signature=signature, frames=OrderedDict())
    key = (captured_at, hashlib.sha256(gray.tobytes()).digest())
    if key in memory['frames']:
        return deepcopy(memory['frames'][key])
    previous = memory.get('last')
    if previous and captured_at <= previous['time']:
        # Out-of-order data cannot update current tracks or renew an OCR anchor.
        return []
    valid_scene = observation and not observation.get('panel') and observation.get('phase') != 'loading'
    targets, pending = [], []
    bars = enemy_bars(picture) if valid_scene else []
    def anchor_position(bar):
        # Red fill width changes with HP; its left edge is the stable anchor.
        return np.array([bar[0], bar[1]], dtype=float)
    def compatible(target):
        matches = [b for b in bindings if b.get('hero') == target.get('hero') and b.get('nickname')]
        return target.get('hero') in enemies and (not matches or any(
            normalized_name(b['nickname']) == normalized_name(target.get('nickname')) and b.get('side') == 'enemy_roster'
            for b in matches))
    gap = captured_at-previous['time'] if previous else float('inf')
    if valid_scene:
        for target in observation.get('targets', []):
            if compatible(target) and target.get('source', 'ocr') == 'ocr':
                targets.append({**target, 'source': 'ocr', 'ocr_at': captured_at, 'missing_frames': 0})
        for target in observation.get('name_candidates', []):
            if not compatible(target):
                continue
            prior = [t for t in (previous or {}).get('pending', [])+(previous or {}).get('targets', [])
                     if t['hero'] == target['hero'] and normalized_name(t['nickname']) == normalized_name(target['nickname'])
                     and t.get('bar') and np.linalg.norm(anchor_position(t['bar'])-anchor_position(target['bar'])) < 60]
            if len(prior) == 1 and 0 < gap <= .8:
                targets.append({**target, 'source': 'ocr', 'ocr_at': captured_at, 'missing_frames': 0,
                                'evidence': {**target['evidence'], 'continuity': True}})
            else:
                pending.append(target)
    proposals = []
    if valid_scene and previous and previous['gray'].shape == gray.shape and 0 < gap <= MAX_IDENTITY_AGE_S:
        for target in previous['targets']:
            if not compatible(target) or any(t['hero'] == target['hero'] for t in targets):
                continue
            age = captured_at-target['ocr_at']
            missed = target.get('missing_frames', 0)+1
            if age > MAX_IDENTITY_AGE_S or missed > MAX_MISSING_FRAMES:
                continue
            flowed = bridge(previous['gray'], gray, target, gap_s=gap, anchor_age_s=age)
            moved, bar_index = None, None
            if target.get('bar'):
                expected = anchor_position(flowed['bar'] if flowed and flowed.get('bar') else target['bar'])
                choices = [(i, bar) for i, bar in enumerate(bars)
                           if np.linalg.norm(anchor_position(bar)-expected) <= max(18, min(90, gap*180))]
                if len(choices) > 1:
                    continue
                if choices:
                    bar_index, bar = choices[0]
                    # A currently readable different identity blocks old ownership.
                    nearby = observation.get('targets', [])+observation.get('name_candidates', [])
                    if any(t.get('bar') and np.linalg.norm(anchor_position(t['bar'])-anchor_position(bar)) < 12 and
                           (t['hero'] != target['hero'] or normalized_name(t.get('nickname')) != normalized_name(target.get('nickname')))
                           for t in nearby):
                        continue
                    contradicted = False
                    for reading in observation.get('readings', []):
                        box = np.asarray(reading.get('box', []))
                        if box.shape != (4, 2) or reading.get('score', 0) < .85:
                            continue
                        cx, cy = box.mean(axis=0)
                        if not (bar[0]-15 <= cx <= bar[0]+bar[2]+130 and bar[1]-60 <= cy < bar[1]):
                            continue
                        name = normalized_name(reading.get('text'))
                        if any(normalized_name(b.get('nickname')) == name and
                               (b.get('side') != 'enemy_roster' or b.get('hero') != target['hero']) for b in bindings):
                            contradicted = True
                    if contradicted:
                        continue
                    dx, dy = anchor_position(bar)-anchor_position(target['bar'])
                    moved = {**target, 'bar': bar, 'x': target['x']+dx/picture.width,
                             'y': target['y']+dy/576, 'source': 'bar_track'}
                    if target.get('box'):
                        a, b, c, d = target['box']; moved['box'] = [a+dx, b+dy, c+dx, d+dy]
                elif bars:
                    continue  # do not jump to an unrelated visible bar
            if moved is None:
                moved = flowed
            if moved:
                moved.update(missing_frames=missed, evidence={'bar': bar_index is not None,
                    'nickname': False, 'continuity': True, 'level_circle': False})
                proposals.append((bar_index, moved))
    for bar_index, moved in proposals:
        if bar_index is not None and (sum(i == bar_index for i, _ in proposals) != 1 or
                any(t.get('bar') == bars[bar_index] for t in targets)):
            continue
        targets.append(moved)
    memory['last'] = {'time': captured_at, 'gray': gray, 'targets': deepcopy(targets), 'pending': deepcopy(pending)}
    memory['frames'][key] = deepcopy(targets)
    while len(memory['frames']) > 32:
        memory['frames'].popitem(last=False)
    return targets
