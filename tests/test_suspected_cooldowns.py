from gameplan.skills.auto_skill_monitor import EventTracker


def candidate(hero='铠',skill='大招',**extra):
    return {'hero':hero,'skill':skill,'frame_index':1,'confidence':.8,
            'evidence':'敌方角色出现大招变身起手动作','reason':'model_uncertain','event_type':'cast_start',**extra}


def ingest(tr,raw,now=101):
    tr.ingest_suspicions([raw],enemies=[raw['hero']],allies=[],frame_times=[now-1,now],now=now)


def test_suspected_cast_immediately_estimates_from_source_time_and_survives_banner():
    tr=EventTracker();ingest(tr,candidate())
    first=tr.estimate_snapshot(101)[0]
    assert first['status']=='estimated' and first['confirmed'] is False
    assert first['remaining_range_s'][1]>0
    later=tr.estimate_snapshot(116)[0]
    assert later['remaining_range_s'][1]==first['remaining_range_s'][1]-15
    assert tr.suspicion_snapshot(116)==[] and tr.events==[]


def test_repeated_candidate_does_not_restart_estimated_cooldown():
    tr=EventTracker();ingest(tr,candidate());before=tr.estimate_snapshot(101)[0]
    ingest(tr,candidate(),103);after=tr.estimate_snapshot(103)[0]
    assert after['id']==before['id'] and after['captured_at']==before['captured_at']


def test_clear_low_level_retracts_only_ultimate_estimate():
    tr=EventTracker();ingest(tr,candidate());ingest(tr,candidate(skill='闪现'))
    tr.update_levels([{'hero':'铠','level':1,'visible_text':'1','confidence':.99,'frame_index':0}],enemies=['铠'],allies=[],frame_times=[102])
    records=tr.estimate_snapshot(102)
    assert next(r for r in records if r['is_ultimate'])['status']=='retracted'
    assert next(r for r in records if not r['is_ultimate'])['status']=='estimated'


def test_ongoing_or_unbound_ultimate_starts_an_explicitly_tentative_countdown():
    for raw in [candidate(event_type='ongoing'),candidate(reason='visual_identity_unconfirmed')]:
        tr=EventTracker();ingest(tr,raw)
        record=tr.estimate_snapshot(101)[0]
        assert record['remaining_range_s'][1]==50
        assert record['confirmed'] is False and tr.events==[]
        if raw['event_type']=='ongoing':
            assert record['cast_window_start']==record['cast_window_end']==101
            assert record['timing_basis']=='first_visible_candidate'
        else:
            assert record['identity_confirmed'] is False


def test_known_level_one_cannot_start_suspected_ultimate_timer():
    tr=EventTracker();tr.update_levels([{'hero':'铠','level':1,'visible_text':'1','confidence':.99,'frame_index':0}],enemies=['铠'],allies=[],frame_times=[100])
    ingest(tr,candidate());assert tr.estimate_snapshot(101)==[]


def test_flash_estimate_uses_known_cooldown_and_delay_without_confirming_equipment():
    tr=EventTracker();ingest(tr,candidate(skill='闪现'))
    record=tr.estimate_snapshot(106)[0]
    assert record['remaining_range_s']==[114,115]
    assert tr.summoner_for('铠').get('skill') is None
