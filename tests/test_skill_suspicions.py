"""Behavior tests; they do not measure real-world model accuracy."""
import pytest

from gameplan.skills.auto_skill_monitor import EventTracker
from gameplan.skills.monitor_policy import FIRST_BATCH, recognition_reference
from test_enemy_casts import cast, feed, level
from test_realtime_evidence import sequence_response, scene, event


def suspicion(**changes):
    return {'hero':'铠', 'skill':'不灭魔躯', 'frame_index':1, 'confidence':.8,
            'evidence':'该敌方角色出现魔铠轮廓，连续变身过程被遮挡', 'reason':'model_uncertain', **changes}


def suspects(tracker, candidates=None, now=1000, times=None):
    tracker.ingest_suspicions(candidates if candidates is not None else [suspicion()],
        enemies=['铠'], allies=['艾琳'], frame_times=times or [now-.5,now], now=now)
    return tracker.suspicion_snapshot(now)


def test_suspected_cast_alerts_without_timer_and_cannot_block_later_confirmed_cast():
    tracker=EventTracker()
    first=suspects(tracker)[0]
    assert not tracker.snapshot(1000)
    assert first['status']=='suspected'
    confirmed=feed(tracker,captured=1002)
    assert len(confirmed)==1 and confirmed[0].cooldown_s==50
    assert not tracker.suspicion_snapshot(1002)


def test_repeated_suspicions_keep_id_but_expire_and_later_release_gets_new_id():
    tracker=EventTracker()
    first=suspects(tracker)[0]
    assert suspects(tracker,now=1001)[0]['id']==first['id']
    assert not tracker.suspicion_snapshot(1010)
    assert suspects(tracker,now=1011)[0]['id']!=first['id']


@pytest.mark.parametrize('change', [
    {'hero':'艾琳'}, {'skill':'极刃风暴'}, {'confidence':float('nan')}, {'confidence':.2},
    {'evidence':'看到图标亮起'}, {'frame_index':99}, {'frame_index':True},
])
def test_suspicions_require_enemy_identity_relevant_evidence_and_valid_time(change):
    assert not suspects(EventTracker(),[suspicion(**change)])


def test_unknown_level_can_alert_but_known_locked_level_cannot():
    tracker=EventTracker()
    assert suspects(tracker)
    tracker.update_levels([level(value=3)],enemies=['铠'],allies=[],frame_times=[1000])
    assert not suspects(tracker,now=1001)


def test_known_other_summoner_rejects_flash_and_correction_clears_suspected_flash():
    tracker=EventTracker();tracker.tracking_enemies=['铠']
    assert suspects(tracker,[suspicion(skill='闪现')])
    tracker.correct_summoner('铠','净化')
    assert not tracker.suspicion_snapshot(1000)
    assert not suspects(tracker,[suspicion(skill='闪现')],now=1001)


def test_unknown_equipment_flash_is_tentative_and_never_fabricates_a_timer():
    tracker=EventTracker()
    result=suspects(tracker,[suspicion(skill='闪现')])
    assert result[0]['skill']=='闪现'
    assert tracker.snapshot(1000)==[]


def test_countdown_bounds_are_knowledge_values_minus_elapsed_not_model_guesses():
    tracker=EventTracker()
    event=feed(tracker,{**cast(), 'hero':'高渐离', 'skill':'大招', 'cooldown_s':999}, enemies=['高渐离'])[0]
    assert event.cooldown_values_s==(45,40,35)
    result=tracker.snapshot(event.captured_at+5)[0]
    assert result['remaining_range_s']==[30,40]
    assert result['cooldown_source']=='resources/knowledge/skill_catalog.json'
    flash=feed(tracker,{**cast(),'hero':'高渐离','skill':'闪现','slot':5},enemies=['高渐离'],captured=1002)[0]
    assert flash.cooldown_s==120


def test_all_fifty_have_knowledge_but_are_not_claimed_as_validated_recognizers():
    assert len(FIRST_BATCH)==len(set(FIRST_BATCH))==50
    for hero in FIRST_BATCH:
        ref=recognition_reference(hero)
        assert ref['ultimate'] and ref['mechanic']
        assert ref['validation']=='pending_real_match_validation'


def test_medium_confidence_sequence_becomes_tentative_instead_of_disappearing():
    result,_=sequence_response(event(confidence=.8),[scene('孙策')]*3)
    assert not result['enemy_skill_events']
    assert result['suspicions'][0]['hero']=='孙策'
    assert result['suspicions'][0]['reason']=='model_uncertain'


def test_no_cast_answer_does_not_report_invalid_frame_or_create_suspicion():
    result,_=sequence_response({'state':'none','frame_index':'none','confidence':0},[scene('孙策')]*3)
    assert not result.get('rejected_candidates')
    assert not result.get('suspicions')


def test_onset_uncertainty_is_included_in_the_remaining_range():
    tracker=EventTracker()
    event=feed(tracker,{**cast(),'onset_start_index':0,'onset_end_index':2})[0]
    assert event.cast_window_start==999
    assert event.captured_at==event.cast_window_end==1000
    result=tracker.snapshot(1005)[0]
    # Kai's catalog values are 50/45/40; include the one-second onset interval.
    assert result['remaining_range_s']==[34,45]
    assert '1.0秒观察区间' in result['cooldown_basis']


def test_level_seen_only_after_interval_start_cannot_validate_the_whole_interval():
    tracker=EventTracker()
    assert feed(tracker,{**cast(),'onset_start_index':0,'onset_end_index':2},hero_levels=[level(index=2)])==[]
