"""Input geometry and response attribution, not a claim of model accuracy."""
import base64
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from gameplan.ai.advisor import KNOWN_HEROES
from gameplan.core.models import VisionRequest
from gameplan.skills.combat_evidence import read_scene
from gameplan.skills.keyframe_casts import detect

FIXTURE = Path(__file__).parent / 'fixtures/combat/live-window'


def real_input():
    pixels = (FIXTURE / '艾琳-152.png').read_bytes()
    readings = json.loads((FIXTURE / '艾琳-152-ocr.json').read_text(encoding='utf8'))
    scene = read_scene(pixels, KNOWN_HEROES, readings=readings)
    req = VisionRequest(image_base64=base64.b64encode(pixels).decode(), captured_at=100, focus='skills')
    return req, pixels, scene


def test_real_enemy_detail_keeps_field_context_and_constrains_model_output():
    req, pixels, scene = real_input()
    with patch('gameplan.ai.integrations.post_json', return_value={
            'done_reason': 'stop', 'message': {'content': '{"events":[]}'}}) as post:
        detect(req, pixels, scene, bindings=[], enemies=['吕布', '澜', '妲己', '敖隐', '墨子'], known_heroes=KNOWN_HEROES)
    payload = post.call_args.args[1]
    schema = payload['format']
    assert isinstance(schema, dict)
    event = schema['properties']['events']['items']['properties']
    assert event['actor_id']['enum'] == [0]
    assert event['frame_index']['enum'] == [0]
    assert '墨子' in event['hero']['enum'] and '朵莉亚' not in event['hero']['enum']
    picture = Image.open(io.BytesIO(base64.b64decode(payload['messages'][0]['images'][0])))
    assert picture.width <= 960  # Local enemy and surrounding field, no full-width HUD.
    assert picture.height > 400
    assert '四边形' in payload['messages'][0]['content']


@pytest.mark.parametrize('evidence', ['疑似墨子或类似英雄正在释放技能', '无法确定是否为大招'])
def test_high_score_does_not_override_explicitly_uncertain_evidence(evidence):
    req, pixels, scene = real_input()
    answer = {'events': [{'actor_id': 0, 'hero': '墨子', 'kind': 'ultimate', 'frame_index': 0,
        'confidence': .99, 'effect_visible': True, 'identity_visible': True, 'enemy_visible': True,
        'evidence': evidence}]}
    with patch('gameplan.ai.integrations.post_json', return_value={
            'done_reason': 'stop', 'message': {'content': json.dumps(answer)}}):
        result = detect(req, pixels, scene, bindings=[], enemies=['墨子'], known_heroes=KNOWN_HEROES)
    assert not result['enemy_skill_events']
    assert result['suspicions'][0]['reason'] == 'uncertain_evidence'


def test_detail_crop_keeps_original_frame_number_after_empty_target_frame():
    req, pixels, scene = real_input()
    req = req.model_copy(update={'recent_frames': [req.model_copy(update={'captured_at': 99})]})
    actor = {'hero': '墨子', 'x': .55, 'y': .30, 'bar': [660, 175, 73, 9]}
    with patch('gameplan.skills.combat_evidence.read_scene', return_value=scene), \
         patch('gameplan.skills.keyframe_casts.visible_actors', side_effect=[[], [actor]]), \
         patch('gameplan.ai.integrations.post_json', return_value={
             'done_reason': 'stop', 'message': {'content': '{"events":[]}'}}) as post:
        detect(req, pixels, scene, bindings=[], enemies=['墨子'], known_heroes=KNOWN_HEROES)
    event = post.call_args.args[1]['format']['properties']['events']['items']['properties']
    assert event['frame_index']['enum'] == [1]


def test_recorded_high_confidence_normal_swing_cannot_confirm_lubu_ultimate():
    req, pixels, scene = real_input()
    # Real 8B response on the 117/118/119 s recording, not a calibrated score.
    answer = {'events': [{'actor_id': 0, 'hero': '吕布', 'kind': 'ultimate', 'frame_index': 0,
        'confidence': .95, 'effect_visible': True, 'identity_visible': True, 'enemy_visible': True,
        'evidence': '角色呈人形，手持方天画戟，正在挥舞武器并释放带有绿色轨迹的技能，技能范围呈扇形扩散，'
                    '同时有红色伤害数字320和27显示在角色周围，表明正在造成伤害。角色血条为红色。'}]}
    with patch('gameplan.ai.integrations.post_json', return_value={
            'done_reason': 'stop', 'message': {'content': json.dumps(answer)}}):
        result = detect(req, pixels, scene, bindings=[], enemies=['吕布'], known_heroes=KNOWN_HEROES)
    assert not result['enemy_skill_events']
    assert result['suspicions'][0]['reason'] == 'incomplete_visual_evidence'
