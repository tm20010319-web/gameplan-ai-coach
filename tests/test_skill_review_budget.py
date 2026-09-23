from types import SimpleNamespace
from unittest.mock import patch
from test_enemy_casts import client, request, observation
import gameplan.monitoring.monitor_runtime as runtime
import pytest
from test_enemy_casts import image_data


def test_slow_identity_ocr_still_leaves_time_for_actual_skill_review(client):
    clock=iter([100,109.7])
    scene={'panel':False,'bindings':[],'targets':[],'hero_levels':[],
           'ally_roster':[],'enemy_roster':[],'readings':[]}
    scan={'enemy_skill_events':[],'hero_levels':[],'activity':[], 'status':'no_visible_target'}
    with patch.object(runtime,'time',SimpleNamespace(time=lambda:next(clock,109.7))), \
         patch('gameplan.skills.combat_evidence.read_scene',return_value=scene), \
         patch('gameplan.skills.keyframe_casts.detect',return_value=scan) as detector:
        request(client,observation(),captured_at=100,recent_frames=[])
    assert detector.call_args.kwargs['budget_s'] >= 8


def test_retained_frame_age_is_reserved_inside_twenty_second_target(client):
    clock=iter([100,109.7])
    scene={'panel':False,'bindings':[],'targets':[],'hero_levels':[],
           'ally_roster':[],'enemy_roster':[],'readings':[]}
    scan={'enemy_skill_events':[],'hero_levels':[],'activity':[], 'status':'no_visible_target'}
    with patch.object(runtime,'time',SimpleNamespace(time=lambda:next(clock,109.7))), \
         patch('gameplan.skills.combat_evidence.read_scene',return_value=scene), \
         patch('gameplan.skills.combat_evidence.read_scenes',return_value=[scene,scene]), \
         patch('gameplan.skills.keyframe_casts.detect',return_value=scan) as detector:
        request(client,observation(),captured_at=100,recent_frames=[
            {'image_base64':image_data(),'captured_at':92.5}])
    assert detector.call_args.kwargs['budget_s'] == pytest.approx(2.8)
