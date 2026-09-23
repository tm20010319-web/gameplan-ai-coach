"""Fixed loading slots with independent hero, nickname and team evidence."""
import re
from collections import Counter, deque
from copy import deepcopy

import cv2
import numpy as np

from gameplan.vision.hero_recognition import read_regions, region_vote


def loading_slots(size):
    """Two centered five-card rows. Card pitch scales with game height."""
    width, height = size
    slots = []
    for row in range(2):
        for column in range(5):
            x = width/2 + (column-2)*height*(160/576)
            y = height*((181 if row == 0 else 496)/576)
            half = height*71/576
            def box(top, bottom):
                return [max(0, round(x-half)), max(0, round(top)), min(width, round(x+half)), min(height, round(bottom))]
            slots.append({'slot': row*5+column+1, 'row': row, 'column': column+1,
                          'x': x, 'y': y+height*23/576,
                          'label_box': box(y-height*10/576, y+height*10/576),
                          'nickname_box': box(y+height*14/576, y+height*33/576)})
    return slots


def is_loading_layout(picture, slots):
    """Check long card borders, independently of readable heroes/nicknames."""
    gray = cv2.cvtColor(np.asarray(picture.convert('RGB')), cv2.COLOR_RGB2GRAY)
    edge = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)) > 55
    height = picture.height
    rows = [0, 0]
    for slot in slots:
        top, bottom = ((8, 248) if slot['row'] == 0 else (325, 565))
        top, bottom = round(top*height/576), round(bottom*height/576)
        hits = 0
        for x in (slot['label_box'][0], slot['label_box'][2]):
            band = edge[top:bottom, max(0, x-round(height*.008)):min(picture.width, x+round(height*.008)+1)]
            if band.size and band.mean(axis=0).max() > .55:
                hits += 1
        if hits == 2:
            rows[slot['row']] += 1
    return min(rows) >= 3


def read_loading(picture, items, known_heroes):
    slots = loading_slots(picture.size)
    if not is_loading_layout(picture, slots):
        return None
    # Text boxes may tighten a line *inside* a fixed slot. They never locate,
    # add, remove or move cards, and a missing detector box uses the fixed ROI.
    for slot in slots:
        slot['fixed_label_box'] = slot['label_box'][:]
        left, top, right, bottom = slot['label_box']
        local = [r for r in items if top <= r['box'][:, 1].mean() <= bottom
                 and left <= r['box'][:, 0].min() < r['box'][:, 0].max() <= right]
        if len(local) == 1:
            lo, hi = local[0]['box'].min(axis=0), local[0]['box'].max(axis=0)
            slot['label_box'] = [max(left, round(lo[0]-2)), max(top, round(lo[1]-2)),
                                 min(right, round(hi[0]+2)), min(bottom, round(hi[1]+2))]
    aliases = {'赢政': '嬴政', '熬隐': '敖隐'}
    def hero_label(text):
        value = re.sub(r'[^\w\u4e00-\u9fff]', '', text)
        for wrong, correct in aliases.items():
            if value.endswith(wrong):
                value = value[:-len(wrong)]+correct
        return next((h for h in sorted(known_heroes, key=len, reverse=True) if value.endswith(h)), None)
    def nickname(text):
        value = re.sub(r'[^\w\u4e00-\u9fff]', '', text)
        return value if 2 <= len(value) <= 24 and not value.isdecimal() else None
    readings = read_regions(picture, [s['label_box'] for s in slots]+[s['nickname_box'] for s in slots]
                            +[s['fixed_label_box'] for s in slots])
    hsv = cv2.cvtColor(np.asarray(picture.convert('RGB')), cv2.COLOR_RGB2HSV)
    cards = []
    for i, slot in enumerate(slots):
        hero, hero_confidence = region_vote(readings[i]+readings[i+20], hero_label)
        name, name_confidence = region_vote(readings[i+10], nickname)
        local = [r for r in items if r.get('score', 0) >= .85 and nickname(r['text'])
                 and abs(float(r['box'][:, 0].mean())-slot['x']) < picture.height*.09
                 and abs(float(r['box'][:, 1].mean())-slot['y']) < picture.height*.012]
        if len(local) == 1:
            # A tight crop can exclude a level badge; it cannot supply a hero.
            b = local[0]['box']; lo, hi = b.min(axis=0), b.max(axis=0)
            tight = [max(0, lo[0]-1), max(0, lo[1]-1), min(picture.width, hi[0]+1), min(picture.height, hi[1]+1)]
            verified, score = region_vote(read_regions(picture, [tight])[0], nickname)
            if verified == nickname(local[0]['text']):
                name, name_confidence = verified, score
        x, y = slot['x'], slot['y']
        region = hsv[max(0, round(y-8)):round(y+8), max(0, round(x-picture.width*.04)):round(x+picture.width*.04)]
        gold = float(((region[:, :, 0] >= 18) & (region[:, :, 0] <= 38) &
                      (region[:, :, 1] > 110) & (region[:, :, 2] > 65)).mean()) if region.size else 0
        cards.append({**slot, 'hero': hero, 'hero_confidence': hero_confidence,
                      'nickname': name, 'nickname_confidence': name_confidence, 'gold': gold,
                      'side': 'unknown', 'side_confidence': 0.0})
    highlights = sorted(cards, key=lambda card: card['gold'], reverse=True)
    player = highlights[0] if highlights[0]['gold'] > .45 and highlights[0]['gold']-highlights[1]['gold'] > .2 else None
    row = player['row'] if player else None
    scene = {'phase': 'loading', 'panel': False, 'targets': [], 'hero_levels': [],
             'equipment': [], 'cards': cards, 'readings': items,
             'own_row_candidate': row, 'player_slot': player['slot'] if player else None}
    return slot_scene(scene, cards, row, player['slot'] if player else None)


def slot_scene(scene, cards, own_row, player_slot=None):
    cards = deepcopy(cards)
    for card in cards:
        card['side'] = ('ally_roster' if card['row'] == own_row else 'enemy_roster') if own_row is not None else 'unknown'
        # This is the fraction of supporting side votes, not an OCR probability.
        card['side_confidence'] = 1.0 if own_row is not None else 0.0
    counts = Counter(c['hero'] for c in cards if c['hero'])
    for card in cards:
        if counts[card['hero']] > 1:
            card.update(hero=None, hero_confidence=0.0)
    return {**scene, 'cards': cards, 'all_slots': [c['hero'] for c in cards],
            'side_known': own_row is not None, 'side_status': 'confirmed' if own_row is not None else 'unknown',
            'player_hero': next((c['hero'] for c in cards if c['slot'] == player_slot), None),
            'bindings': [{'hero': c['hero'], 'nickname': c['nickname'], 'side': c['side'], 'slot': c['slot']}
                         for c in cards if c['hero'] and c['nickname']],
            **{side: [c['hero'] for c in cards if c['side'] == side and c['hero']]
               for side in ('ally_roster', 'enemy_roster')}}


class LoadingMemory:
    """Per-match 3-of-5 votes. Missing fields never erase confirmed fields."""
    def __init__(self):
        self.frames = deque(maxlen=5)
        self.cards = []
        self.last_at = None
        self.own_row = None
        self.player_slot = None
        self.side_votes = deque(maxlen=3)

    def observe(self, scene, captured_at, *, allies=(), enemies=(), player=None):
        if self.last_at is not None and captured_at <= self.last_at:
            return self.snapshot(scene)
        self.last_at = captured_at
        cards = scene.get('cards', []) if scene and scene.get('phase') == 'loading' else []
        self.frames.append(deepcopy(cards))
        if cards and not self.cards:
            self.cards = [{**c, 'hero': None, 'nickname': None, 'hero_confidence': 0.0,
                           'nickname_confidence': 0.0} for c in cards]
        for card in self.cards:
            seen = [c for frame in self.frames for c in frame if c['slot'] == card['slot']]
            if seen:
                for key in ('x', 'y', 'label_box', 'nickname_box', 'fixed_label_box'):
                    if key in seen[-1]:
                        card[key] = deepcopy(seen[-1][key])
            for field in ('hero', 'nickname'):
                eligible = [c for c in seen if field == 'hero' or c.get('hero') in (None, card['hero'])]
                candidates = Counter(c[field] for c in eligible if c.get(field))
                value, count = candidates.most_common(1)[0] if candidates else (None, 0)
                card[field+'_votes'] = count
                if count >= 3:
                    if field == 'hero' and card['hero'] not in (None, value):
                        card.update(nickname=None, nickname_confidence=0.0)
                    card[field] = value
                    card[field+'_confidence'] = min(c.get(field+'_confidence', 0) for c in eligible if c.get(field) == value)
        row = scene.get('own_row_candidate') if scene else None
        evidence = set()
        for c in self.cards:
            if not c['hero']:
                continue
            if c['hero'] in allies or c['hero'] == player:
                evidence.add(c['row'])
            if c['hero'] in enemies:
                evidence.add(1-c['row'])
        if row is None and len(evidence) == 1:
            row = next(iter(evidence))
        if len(evidence) > 1 or (evidence and row not in evidence):
            row = None
        self.side_votes.append(row)
        if row is not None and len(self.side_votes) == 3 and all(v == row for v in self.side_votes):
            self.own_row = row
            self.player_slot = scene.get('player_slot') or self.player_slot
        return self.snapshot(scene)

    def snapshot(self, scene=None):
        return slot_scene(scene or {}, self.cards, self.own_row, self.player_slot)
