"""Locate enemy health bars in one frame; never infer a cast from a bar."""
import numpy as np

from gameplan.skills.combat_evidence import normalized_name
from gameplan.vision.enemy_bars import enemy_bars


def nickname_hint(labels, bindings, enemies):
    """One-character OCR disagreement suggests an identity, never verifies it."""
    hits = set()
    for binding in bindings:
        name = normalized_name(binding.get('nickname'))
        if len(name) < 3:
            continue
        if any(len(label) == len(name) and sum(a != b for a, b in zip(label, name)) <= 1
               for label in labels):
            hits.add((binding['hero'], binding.get('side')))
    if len(hits) == 1:
        hero, side = next(iter(hits))
        if side == 'enemy_roster' and hero in enemies:
            return hero
    return None


def visible_actors(picture, readings, bindings, enemies, *, verified_targets=None, anonymous_targets=()):
    picture = picture.convert('RGB')
    picture = picture.resize((round(picture.width*576/picture.height), 576))
    actors = []
    for x, y, w, h in enemy_bars(picture):
        labels = []
        for reading in readings or []:
            box = np.asarray(reading.get('box', []))
            if reading.get('score', 0) < .85 or box.shape != (4, 2):
                continue
            cx, cy = box.mean(axis=0)
            if x-15 <= cx <= x+w+130 and y-60 <= cy <= y-3:
                labels.append(normalized_name(reading.get('text', '')))
        matches = [b for b in bindings if normalized_name(b.get('nickname')) in labels
                   and len(normalized_name(b.get('nickname'))) >= 3]
        names = {b['hero'] for b in matches}
        if any(b.get('side') == 'ally_roster' for b in matches) or len(names) > 1 or (names and not names.issubset(enemies)):
            continue
        if verified_targets is not None:
            owned = [t for t in verified_targets if t.get('bar') and
                     abs(t['bar'][0]-x) < 5 and abs(t['bar'][1]-y) < 5 and t['hero'] in enemies]
            if len(owned) == 1:
                names = {owned[0]['hero']}
            else:
                names = set()  # single full-screen OCR is only a location hint
        status = any(word in label for label in labels for word in
                     ('霸体', '无法命中', '不可选中', '无法选中', '免控'))
        # A normal hero (e.g. Daji) has no special status while casting. Missing
        # a loading/scoreboard nickname binding must not suppress visual review.
        # A readable level beside the red bar can locate a review candidate
        # even when the nickname is covered. Without a name/status anchor it
        # remains tentative; this geometry must never verify hero identity.
        identity_review_only = False
        anonymous=any(abs(t['bar'][0]-x)<5 and abs(t['bar'][1]-y)<5 for t in anonymous_targets)
        if not names and not status and anonymous:
            identity_review_only = True
        elif not names and not status:
            nicknames = [label for label in labels if 2 <= len(label) <= 20 and not label.isdecimal()]
            level = None
            for reading in readings or []:
                text = reading.get('text', '').strip()
                box = np.asarray(reading.get('box', []))
                if (reading.get('score', 0) < .92 or box.shape != (4, 2) or
                        not text.isascii() or not text.isdecimal() or not 1 <= int(text) <= 15):
                    continue
                cx, cy = box.mean(axis=0)
                if x-32 <= cx <= x-3 and y-6 <= cy <= y+16:
                    level = int(text)
                    break
            if level is None and nicknames:
                from gameplan.vision.health_nameplates import bar_level
                level = bar_level(picture, x, y)
            # Single-frame candidates can use a level. The independent
            # anonymous continuity path above never requires a readable digit.
            if level is None:
                continue
            identity_review_only = not nicknames
        # Numeric monster HP labels are not enemy hero nameplates.
        if any(label.isdecimal() and int(label) > 15 for label in labels) and not names:
            continue
        hint = nickname_hint(labels, bindings, enemies) if not names else None
        actors.append({'hero': next(iter(names), None), 'x': (x+w/2)/picture.width,
                       'y': y/576, 'bar': [x, y, w, h],
                       'identity_review_only': identity_review_only or bool(hint),
                       **({'hero_hint': hint} if hint else {}),
                       **({'source':'anonymous_track'} if identity_review_only and anonymous else {})})
    return sorted(actors, key=lambda actor: (actor['y'], actor['x']))[:12]
