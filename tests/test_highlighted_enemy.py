"""Real selected-target frame: segmentation must reach visual review."""
import json
from pathlib import Path

from PIL import Image, ImageDraw

from gameplan.skills.effect_actors import visible_actors, nickname_hint
from gameplan.vision.enemy_bars import enemy_bars

ROOT = Path(__file__).parent / 'fixtures/combat/highlighted-enemy'


def test_real_highlighted_enemy_reaches_review_without_inventing_identity():
    evidence = json.loads((ROOT/'evidence.json').read_text(encoding='utf8'))
    actors = visible_actors(Image.open(ROOT/'xiaoqiao-299.png'), evidence['readings'],
                            evidence['bindings'], ['马超', '桑启', '卢雅那', '小乔', '孙悟空'],
                            verified_targets=[])
    target = [a for a in actors if .70 < a['x'] < .79 and .67 < a['y'] < .74]
    assert len(target) == 1
    assert target[0]['hero'] is None


def test_thick_red_strip_without_a_level_circle_is_not_a_selected_hero():
    picture = Image.new('RGB', (1280, 576), (25, 25, 25))
    ImageDraw.Draw(picture).rectangle((700, 200, 795, 216), fill='red')
    assert enemy_bars(picture) == []


def test_nickname_hint_is_unique_enemy_only_and_does_not_assign_confirmed_identity():
    bindings = [{'hero': '小乔', 'nickname': '泡芙桃冰', 'side': 'enemy_roster'}]
    assert nickname_hint(['泡芙楼冰'], bindings, ['小乔']) == '小乔'
    assert nickname_hint(['泡芙楼冰'], bindings, ['马超']) is None
    assert nickname_hint(['泡芙楼冰'], bindings + [
        {'hero': '李元芳', 'nickname': '泡芙楼冰', 'side': 'ally_roster'}], ['小乔']) is None
    assert nickname_hint(['泡芙'], bindings, ['小乔']) is None
