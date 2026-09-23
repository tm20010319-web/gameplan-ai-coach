"""Candidate admission before model review; synthetic replies do not prove accuracy."""
import base64
import io
import json
from unittest.mock import patch

from PIL import Image, ImageDraw

from gameplan.core.models import VisionRequest
from gameplan.skills.effect_actors import visible_actors
from gameplan.skills.keyframe_casts import detect
from gameplan.skills.auto_skill_monitor import EventTracker


def enemy_frame(color='red'):
    picture = Image.new('RGB', (1280, 576), (25, 25, 25))
    ImageDraw.Draw(picture).rectangle((700, 200, 795, 206), fill=color)
    readings = [
        {'text': '对面玩家甲', 'score': .98, 'box': [[700, 175], [795, 175], [795, 189], [700, 189]]},
        {'text': '5', 'score': .98, 'box': [[680, 195], [690, 195], [690, 212], [680, 212]]},
    ]
    return picture, readings


def encode(picture):
    out = io.BytesIO()
    picture.save(out, 'PNG')
    return base64.b64encode(out.getvalue()).decode()


def test_unknown_nickname_with_enemy_bar_and_level_enters_visual_identity_review():
    picture, readings = enemy_frame()
    actors = visible_actors(picture, readings, [], ['妲己'])
    assert len(actors) == 1
    assert actors[0]['hero'] is None  # The roster/nickname must not assign her identity.


def test_readable_unbound_enemy_daji_reaches_model_and_catalog_timer():
    picture, readings = enemy_frame()
    req = VisionRequest(image_base64=encode(picture), captured_at=100, focus='skills')
    scene = {'panel': False, 'readings': readings, 'targets': [], 'hero_levels': []}
    reply = {'events': [{'actor_id': 0, 'hero': '妲己', 'kind': 'ultimate', 'frame_index': 0,
                        'effect_visible': True, 'enemy_visible': True, 'identity_visible': True,
                        'confidence': .96, 'evidence': '红血条下的妲己向同一目标发射连续狐火弹道'}]}
    with patch('gameplan.ai.integrations.post_json', return_value={'done_reason': 'stop', 'message': {'content': json.dumps(reply)}}) as model:
        result = detect(req, b'', scene, bindings=[], enemies=['妲己'], known_heroes={'妲己'})
    assert model.call_count == 1
    tracker = EventTracker()
    added = tracker.ingest(result['enemy_skill_events'], 100, enemies=['妲己'], frame_times=[100])
    assert len(added) == 1 and added[0].hero == '妲己'
    assert tracker.snapshot(101)[0]['remaining_s'] > 0


def test_no_bound_identity_does_not_make_red_decorations_or_allies_cast():
    picture, readings = enemy_frame()
    with patch('gameplan.vision.health_nameplates.bar_level', return_value=None):
        assert visible_actors(picture, readings[:1], [], ['妲己']) == []
    assert visible_actors(*enemy_frame('deepskyblue'), [], ['妲己']) == []
    bindings = [{'hero': '妲己', 'nickname': '对面玩家甲', 'side': 'ally_roster'}]
    assert visible_actors(picture, readings, bindings, ['妲己']) == []


def test_prior_effect_frame_reads_its_own_text_when_cache_is_missing():
    picture, readings = enemy_frame()
    blank = encode(Image.new('RGB', picture.size, (25, 25, 25)))
    req = VisionRequest(image_base64=blank, captured_at=101, focus='skills',
                        recent_frames=[{'image_base64': encode(picture), 'captured_at': 100}])
    latest = {'panel': False, 'readings': [], 'targets': [], 'hero_levels': []}
    earlier = {**latest, 'readings': readings}
    with patch('gameplan.skills.combat_evidence.read_scene', return_value=earlier) as read, \
         patch('gameplan.ai.integrations.post_json', return_value={'done_reason': 'stop', 'message': {'content': '{"events":[]}'}}) as model:
        result = detect(req, b'', latest, bindings=[], enemies=['妲己'], known_heroes={'妲己'})
    assert read.call_count == 1
    assert model.call_count == 2 and result['visible_enemy_actors'] == 1
