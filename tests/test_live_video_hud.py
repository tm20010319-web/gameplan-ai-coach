"""Real recordings: window chrome and badge OCR must not suppress review."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from gameplan.ai.advisor import KNOWN_HEROES
from gameplan.skills.combat_evidence import read_scene
from gameplan.vision.health_nameplates import bar_level

FIXTURES = Path(__file__).parent / 'fixtures/combat/live-window'


@pytest.mark.parametrize('name', ['艾琳-357', '艾琳-361', '吕布-210'])
def test_real_window_hud_survives_covered_bottom_labels(name):
    readings = json.loads((FIXTURES / f'{name}-ocr.json').read_text(encoding='utf-8'))
    scene = read_scene((FIXTURES / f'{name}.png').read_bytes(), KNOWN_HEROES, readings=readings)
    assert scene is not None and not scene['panel']
    assert scene['targets'] == []  # A valid HUD alone cannot assign an enemy.


def test_window_hud_still_requires_performance_evidence_and_bottom_control():
    pixels = (FIXTURES / '艾琳-357.png').read_bytes()
    readings = json.loads((FIXTURES / '艾琳-357-ocr.json').read_text(encoding='utf-8'))
    for excluded in ('FPS', '回城'):
        assert read_scene(pixels, KNOWN_HEROES,
                          readings=[r for r in readings if not r['text'].startswith(excluded)]) is None


def test_separate_ping_must_align_with_fps_in_top_hud():
    name = '艾琳-361'
    pixels = (FIXTURES / f'{name}.png').read_bytes()
    readings = json.loads((FIXTURES / f'{name}-ocr.json').read_text(encoding='utf-8'))
    for item in readings:
        if item['text'] == '22ms':
            item['box'] = [[x, y + 120] for x, y in item['box']]
    assert read_scene(pixels, KNOWN_HEROES, readings=readings) is None


def test_real_scoreboard_recovers_faint_daji_without_assigning_her_to_our_lubu():
    pixels = (FIXTURES / '吕布-390.png').read_bytes()
    readings = json.loads((FIXTURES / '吕布-390-ocr.json').read_text(encoding='utf-8'))
    scene = read_scene(pixels, KNOWN_HEROES, readings=readings)
    assert scene['panel']
    assert set(scene['enemy_roster']) == {'赵云', '铠', '伽罗', '妲己', '关羽'}
    assert '吕布' in scene['ally_roster'] and '吕布' not in scene['enemy_roster']
    assert scene['targets'] == []  # Scoreboard identity is never cast evidence.


@pytest.mark.parametrize('name', ['艾琳-354', '艾琳-152'])
def test_real_level_badge_rim_does_not_hide_enemy_candidate(name):
    picture = Image.open(FIXTURES / f'{name}.png')
    picture = picture.resize((round(picture.width * 576 / picture.height), 576))
    # Geometry of the red enemy bar in the saved frame, not a hero assignment.
    from gameplan.skills.effect_actors import visible_actors
    readings = json.loads((FIXTURES / f'{name}-ocr.json').read_text(encoding='utf-8'))
    actors = visible_actors(picture, readings, [], ['吕布', '墨子'])
    assert len(actors) == 1 and actors[0]['hero'] is None


@pytest.mark.parametrize('text', ['9.5', '19', '9级', '9..'])
def test_level_does_not_strip_arbitrary_nonnumeric_text(text):
    picture = Image.open(FIXTURES.parent / 'healthbar-edge/lixin-10.png')
    with patch('gameplan.vision.health_nameplates.ocr_engine') as engine:
        engine.return_value.return_value = ([[text, .99]], None)
        assert bar_level(picture, 730, 36) is None
