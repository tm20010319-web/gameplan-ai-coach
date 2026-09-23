"""Countdown contracts use synthetic recognition, not real-video accuracy claims."""
import json
from unittest.mock import patch

import pytest

from gameplan.skills.auto_skill_monitor import EventTracker
from gameplan.skills.cooldown_estimates import SUMMONERS
from test_keyframe_casts import candidate, detect_reply, model_contract_actor_locator, scene
from test_enemy_casts import client, request, observation


def cast(hero='铠', skill='大招', **changes):
    return dict(hero=hero, skill=skill, used=True, confidence=.96, event_type='keyframe',
                frame_index=0, evidence='敌方角色释放可归属其身份的专属战场特效', **changes)


@pytest.mark.parametrize('spell,seconds', [('惩击',30),('终结',60),('狂暴',75),('疾跑',75),
    ('治疗术',120),('干扰',90),('眩晕',90),('净化',120),('弱化',75),('闪现',120),('传送',75)])
def test_all_summoners_flow_from_effect_to_countdown_and_do_not_restart(spell, seconds):
    result, post = detect_reply([candidate(kind='summoner', skill=spell)], equipped={'铠':spell})
    assert len(result['enemy_skill_events']) == 1
    tracker = EventTracker()
    tracker.summoners['铠'] = {'skill':spell, 'captured_at':90}
    tracker.levels['铠'] = [{'level':1, 'captured_at':100}]
    event = tracker.ingest(result['enemy_skill_events'],100,enemies=['铠'])[0]
    assert event.slot == 5 and not event.is_ultimate and event.cooldown_s == seconds
    assert tracker.snapshot(105)[0]['remaining_range_s'] == [seconds-5,seconds-5]
    assert tracker.ingest(result['enemy_skill_events'],105,enemies=['铠']) == []
    assert tracker.snapshot(100+seconds)[0]['remaining_s'] == 0
    assert not EventTracker().snapshot(100)
    # Unknown loadout can still use direct effects, never merely equipment.
    assert EventTracker().ingest(result['enemy_skill_events'],100,enemies=['铠'])
    assert not detect_reply([candidate(kind='summoner',skill=spell,effect_visible=False)])[0]['enemy_skill_events']
    wrong = '净化' if spell != '净化' else '疾跑'
    assert not detect_reply([candidate(kind='summoner',skill=spell)],equipped={'铠':wrong})[0]['enemy_skill_events']
    tracker.summoners['铠']['skill'] = wrong
    assert not tracker.ingest(result['enemy_skill_events'],300,enemies=['铠'])


@pytest.mark.parametrize('alias,canonical',[('惩戒','惩击'),('晕眩','眩晕'),('召唤师技能·疾跑','疾跑')])
def test_summoner_aliases(alias, canonical):
    event = EventTracker().ingest([cast(skill=alias,slot=5)],100,enemies=['铠'])[0]
    assert event.skill == canonical


@pytest.mark.parametrize('hero', list(EventTracker().catalog))
def test_every_catalog_ultimate_has_bounded_estimate_except_dolia(hero):
    tracker = EventTracker()
    event = tracker.ingest([cast(hero=hero)],100,enemies=[hero])[0]
    if hero == '朵莉亚':
        assert event.cooldown_s is None and event.cooldown_values_s == ()
    else:
        assert 0 < event.cooldown_s <= 180
        assert all(0 < value <= 180 for value in event.cooldown_values_s)
        assert tracker.ingest([cast(hero=hero)],101,enemies=[hero]) == []


@pytest.mark.parametrize('hero,values',[('鲁班大师',(40,35,30)),('司马懿',(35,30,25))])
def test_conflicting_data_has_explicit_reference_provenance(hero, values):
    event = EventTracker().ingest([cast(hero=hero)],100,enemies=[hero])[0]
    assert event.cooldown_values_s == values and event.reference_estimate
    assert event.cooldown_source.startswith('https://pvp.qq.com/')
    assert '当前版本待核对' in event.cooldown_basis


def test_hero_skill_name_collision_needs_explicit_summoner_slot():
    tracker = EventTracker()
    assert not tracker.ingest([cast(hero='牛魔',skill='狂暴',slot=1)],100,enemies=['牛魔'])
    event = tracker.ingest([cast(hero='牛魔',skill='狂暴',slot=5)],100,enemies=['牛魔'])[0]
    assert event.cooldown_s == 75 and not event.is_ultimate


@pytest.mark.parametrize('manual',[True,False])
def test_corrected_equipment_retracts_any_mismatched_spell_but_keeps_ultimate(manual):
    tracker = EventTracker()
    tracker.tracking_enemies = ['铠']
    tracker.ingest([cast(),cast(skill='疾跑',slot=5)],100,enemies=['铠'])
    tracker.ingest_suspicions([dict(cast(skill='疾跑',slot=5),confidence=.8,frame_index=1,
        event_type='cast_start',reason='model_uncertain')],enemies=['铠'],allies=[],frame_times=[119,120],now=120)
    assert tracker.estimate_snapshot(120)[0]['cooldown_range_s'] == [75,75]
    if manual:
        tracker.correct_summoner('铠','净化')
    else:
        tracker.update_summoners([{'hero':'铠','skill':'净化','frame_index':0,'confidence':.99,
            'evidence':'已核对携带','source':'scoreboard_icon'}],enemies=['铠'],allies=[],frame_times=[121])
    assert len(tracker.events) == 1 and tracker.events[0].is_ultimate
    assert not tracker.suspicions and not tracker.estimate_snapshot(121)


def test_low_confidence_summoner_stays_tentative_with_slot():
    result, _ = detect_reply([candidate(kind='summoner',skill='疾跑',confidence=.8)])
    assert not result['enemy_skill_events']
    assert result['suspicions'][0]['slot'] == 5


def test_actual_api_routes_nonflash_effect_and_preserves_timer_without_new_effect(client):
    with patch('gameplan.skills.combat_evidence.read_scene',return_value=scene()), \
         patch('gameplan.ai.integrations.post_json',return_value={'done_reason':'stop','message':{'content':json.dumps({
             'events':[candidate(kind='summoner',skill='疾跑')]})}}) as post:
        first=request(client,{**observation(),'hero_levels':[]},recent_frames=[])
        post.return_value={'done_reason':'stop','message':{'content':'{"events":[]}'}}
        second=request(client,{**observation(),'hero_levels':[]},recent_frames=[])
    assert first['enemy_skill_timers'][0]['skill'] == '疾跑'
    assert first['enemy_skill_timers'][0]['cooldown_s'] == 75
    assert second['enemy_skill_timers'][0]['id'] == first['enemy_skill_timers'][0]['id']
    assert not second['enemy_skill_updates']
