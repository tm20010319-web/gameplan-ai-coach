"""Synthetic visual decisions exercise routing, not model detection accuracy."""
import json
from unittest.mock import patch

import numpy as np
import pytest

from gameplan.core.models import VisionRequest
from gameplan.skills.auto_skill_monitor import EventTracker
from gameplan.skills.effect_actors import visible_actors
from gameplan.skills.keyframe_casts import detect
from test_effect_actors import field
from test_enemy_casts import image_data, client, request, observation
from test_keyframe_casts import candidate, scene


def test_readable_level_and_red_bar_survive_missing_nickname_as_review_only():
    readings=[{'text':'9','score':.99,'box':np.array([[672,199],[690,199],[690,215],[672,215]])}]
    actors=visible_actors(field(),readings,[],['铠'],verified_targets=[])
    assert len(actors)==1
    assert actors[0]['hero'] is None and actors[0]['identity_review_only'] is True


def scan(items, actors=None, enemies=None):
    actors=actors or [{'hero':None,'x':.65,'y':.35,'bar':[780,200,100,8]}]
    req=VisionRequest(image_base64=image_data(),captured_at=100,focus='skills')
    with patch('gameplan.skills.keyframe_casts.visible_actors',return_value=actors), \
         patch('gameplan.ai.integrations.post_json',return_value={
             'done_reason':'stop','message':{'content':json.dumps({'events':items})}}):
        return detect(req,b'',scene(),bindings=[],enemies=enemies or ['铠'],known_heroes={'铠','孙策'})


def ingest_scan(result,enemies=('铠',),now=100):
    tracker=EventTracker()
    tracker.ingest_suspicions(result.get('suspicions',[]),enemies=list(enemies),allies=[],frame_times=[100],now=now)
    return tracker


@pytest.mark.parametrize('change',[
    {'identity_visible':False}, {'confidence':.75},
    {'evidence':'敌方角色出现魔铠轮廓，疑似铠的大招'},
])
def test_visible_tentative_ultimate_starts_estimate_without_confirming_identity(change):
    result=scan([candidate(**change)])
    tracker=ingest_scan(result)
    assert result['enemy_skill_events']==[] and tracker.events==[]
    first=tracker.estimate_snapshot(103)[0]
    assert first['remaining_range_s']==[37,47]
    assert first['timing_basis']=='first_visible_candidate' and first['confirmed'] is False
    assert tracker.estimate_snapshot(106)[0]['remaining_range_s']==[34,44]


def test_nameless_level_candidate_stays_tentative_even_if_model_claims_certainty():
    result=scan([candidate()],actors=[{'hero':None,'x':.65,'y':.35,'bar':[780,200,100,8],
                                    'identity_review_only':True}])
    assert result['enemy_skill_events']==[]
    assert result['suspicions'][0]['reason']=='visual_identity_unconfirmed'


def test_different_enemies_can_start_independent_tentative_timers_in_one_frame():
    actors=[{'hero':None,'x':x,'y':.35,'bar':[int(x*1280),200,100,8]} for x in (.4,.7)]
    result=scan([candidate(identity_visible=False),candidate(actor_id=1,hero='孙策',identity_visible=False,
                evidence='另一红血条角色驾驶船，疑似孙策')],actors=actors,enemies=['铠','孙策'])
    tracker=ingest_scan(result,('铠','孙策'))
    assert {r['hero'] for r in tracker.estimate_snapshot(100)}=={'铠','孙策'}
    assert result['enemy_skill_events']==[]


def test_one_unbound_actor_cannot_start_two_hero_estimates():
    result=scan([candidate(identity_visible=False),candidate(hero='孙策',identity_visible=False,
                evidence='同一角色疑似开船')],enemies=['铠','孙策'])
    assert result['enemy_skill_events']==[] and not result.get('suspicions')


@pytest.mark.parametrize('change',[{'effect_visible':False},{'enemy_visible':False},
                                  {'evidence':'敌方技能图标变暗'},{'confidence':.3}])
def test_no_enemy_effect_evidence_never_starts_even_a_tentative_clock(change):
    result=scan([candidate(identity_visible=False,**change)])
    assert not result.get('suspicions') and not result['enemy_skill_events']


def test_repeated_observation_does_not_restart_tentative_clock():
    result=scan([candidate(confidence=.75)])
    tracker=ingest_scan(result)
    first=tracker.estimate_snapshot(100)[0]
    tracker.ingest_suspicions(result['suspicions'],enemies=['铠'],allies=[],frame_times=[105],now=105)
    after=tracker.estimate_snapshot(105)[0]
    assert after['id']==first['id'] and after['remaining_range_s']==[35,45]


def test_api_exposes_unbound_candidate_as_estimate_without_confirmed_cast(client):
    with patch('gameplan.skills.combat_evidence.read_scene',return_value=scene()), \
         patch('gameplan.skills.keyframe_casts.visible_actors',return_value=[
             {'hero':None,'x':.65,'y':.35,'bar':[780,200,100,8]}]), \
         patch('gameplan.ai.integrations.post_json',return_value={'done_reason':'stop',
             'message':{'content':json.dumps({'events':[candidate(identity_visible=False)]})}}):
        response=request(client,observation(),recent_frames=[])
    assert response['enemy_skill_timers']==response['enemy_skill_updates']==[]
    assert response['enemy_skill_suspicions'][0]['reason']=='visual_identity_unconfirmed'
    estimate=response['enemy_skill_estimates'][0]
    assert estimate['confirmed'] is False and estimate['identity_confirmed'] is False
    assert 0 < estimate['remaining_range_s'][1] <= 50


def test_later_confirmation_replaces_estimate_without_duplicate_casts():
    tracker=ingest_scan(scan([candidate(identity_visible=False)]))
    assert tracker.estimate_snapshot(100)
    event={'hero':'铠','skill':'大招','used':True,'event_type':'keyframe','frame_index':0,
           'confidence':.96,'evidence':'敌方铠出现清晰魔铠变身特效'}
    assert len(tracker.ingest([event],102,enemies=['铠'],frame_times=[102]))==1
    assert tracker.estimate_snapshot(102)==[]
    assert not tracker.ingest([event],103,enemies=['铠'],frame_times=[103])


def test_conflicting_confirmed_and_tentative_hero_on_one_actor_are_both_rejected():
    result=scan([candidate(),candidate(hero='孙策',identity_visible=False,evidence='同一角色疑似开船')],
                enemies=['铠','孙策'])
    assert result['enemy_skill_events']==[] and not result.get('suspicions')


@pytest.mark.parametrize('value',[None,'false'])
def test_malformed_identity_field_is_not_an_explicit_tentative_decision(value):
    result=scan([candidate(identity_visible=value)])
    assert not result['enemy_skill_events'] and not result.get('suspicions')
