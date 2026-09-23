"""Source-backed base estimates, separate from current-version certainty."""
import json
from pathlib import Path

from gameplan.skills.monitor_policy import cooldown_values

ROOT = Path(__file__).resolve().parents[2] / 'resources' / 'knowledge'
SUMMONER_DATA = json.loads((ROOT / 'summoner_skills.json').read_text(encoding='utf-8'))
SUMMONERS = {row['name']: row for row in SUMMONER_DATA['spells']}
REFERENCES = json.loads((ROOT / 'cooldown_references.json').read_text(encoding='utf-8'))


def canonical_summoner(name):
    if not isinstance(name, str):
        return None
    name = name.removeprefix('召唤师技能·')
    name = {'惩戒': '惩击', '晕眩': '眩晕'}.get(name, name)
    return name if name in SUMMONERS else None


def ultimate_values(hero, item):
    if hero == '朵莉亚' or not item or not item.get('is_ultimate'):
        return []
    if hero in REFERENCES:
        return cooldown_values(REFERENCES[hero])
    if item.get('cooldown_review_required') or item.get('cooldown_kind') == 'dynamic':
        return []
    return cooldown_values(item)
