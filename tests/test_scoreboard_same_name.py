"""Replay the live 2026-09-21 scoreboard that left the monitor at 0/5."""
import base64
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from gameplan.ai.advisor import KNOWN_HEROES
from gameplan.skills.combat_evidence import read_scene
from gameplan.web.monitor_app import app
from gameplan.monitoring.monitor_runtime import reset_match

FIXTURE = Path(__file__).parent / 'fixtures/combat/scoreboard-same-name'
ENEMIES = ['上官婉儿', '元歌', '露娜', '貂蝉', '朵莉亚']


def replay_scene(*args, **kwargs):
    return read_scene((FIXTURE / 'panel.png').read_bytes(), KNOWN_HEROES,
                      readings=json.loads((FIXTURE / 'ocr.json').read_text(encoding='utf-8')))


@pytest.fixture(scope='module')
def scene():
    return replay_scene()


def test_real_scoreboard_counts_each_enemy_row_once(scene):
    assert scene['panel']
    assert scene['enemy_roster'] == ENEMIES
    assert len(scene['ally_roster']) == len(set(scene['ally_roster'])) <= 5
    assert '伽罗' in scene['ally_roster'] and '伽罗' not in scene['enemy_roster']
    assert scene['targets'] == []


def test_live_observe_accepts_same_name_scoreboard_and_keeps_roster(scene):
    match = 'regression-scoreboard-same-name'
    reset_match(match)
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as client:
        with patch('gameplan.skills.combat_evidence.read_scene', return_value=scene), \
             patch('gameplan.vision.summoner_icons.read_equipment', return_value=[]):
            response = client.post('/api/vision/observe', json={
                'match_id': match, 'input_kind': 'live', 'focus': 'skills',
                'image_base64': base64.b64encode((FIXTURE / 'panel.png').read_bytes()).decode()})
        assert response.status_code == 200, response.text
        assert response.json()['observation']['enemy_roster'] == ENEMIES
        assert response.json()['enemy_skill_timers'] == []
        # A subsequent valid HUD without a scoreboard retains this identity.
        gameplay = {**scene, 'panel': False, 'ally_roster': [], 'enemy_roster': [],
                    'bindings': [], 'hero_levels': [], 'targets': [], 'readings': []}
        with patch('gameplan.skills.combat_evidence.read_scene', return_value=gameplay), \
             patch('gameplan.vision.summoner_icons.read_equipment', return_value=[]):
            following = client.post('/api/vision/observe', json={
                'match_id': match, 'input_kind': 'live', 'focus': 'skills',
                'image_base64': base64.b64encode((FIXTURE / 'panel.png').read_bytes()).decode()})
        assert following.status_code == 200, following.text
        assert following.json()['observation']['enemy_roster'] == ENEMIES
        assert following.json()['enemy_skill_timers'] == []
    reset_match(match)


def test_hero_named_nickname_does_not_add_another_enemy():
    readings = json.loads((FIXTURE / 'ocr.json').read_text(encoding='utf-8'))
    # The text in the nickname column is a different hero name, not a sixth player.
    for item in readings:
        if item['text'] == '元歌' and min(p[0] for p in item['box']) > 810:
            item['text'] = '妲己'
    result = read_scene((FIXTURE / 'panel.png').read_bytes(), KNOWN_HEROES, readings=readings)
    assert result['enemy_roster'] == ENEMIES
    assert '妲己' not in result['enemy_roster']


def test_later_user_screenshot_with_reordered_rows_recovers_both_teams():
    result = read_scene((FIXTURE / 'later-panel.png').read_bytes(), KNOWN_HEROES,
                        readings=json.loads((FIXTURE / 'later-panel-fresh-ocr.json').read_text(encoding='utf-8')))
    assert result['enemy_roster'] == ['貂蝉', '上官婉儿', '露娜', '朵莉亚', '元歌']
    assert result['ally_roster'] == ['伽罗', '廉颇', '孙权', '女娲', '戈娅']
    assert result['targets'] == []


def test_hero_substring_in_nickname_is_not_a_merged_hero_label():
    readings = json.loads((FIXTURE / 'ocr.json').read_text(encoding='utf-8'))
    for item in readings:
        if item['text'] == '上官婉儿上官婉儿':
            item['text'] = '最爱上官婉儿玩家'
    result = read_scene((FIXTURE / 'panel.png').read_bytes(), KNOWN_HEROES, readings=readings)
    assert result['enemy_roster'] == ['元歌', '露娜', '貂蝉', '朵莉亚']
