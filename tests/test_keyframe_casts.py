"""Single-effect policy contracts. Model replies here are synthetic, not accuracy results."""
import json
import time
from unittest.mock import patch

import pytest

from gameplan.core.models import VisionRequest
from gameplan.skills.auto_skill_monitor import EventTracker
from gameplan.skills.keyframe_casts import detect, effect_locations
from test_enemy_casts import image_data, client, request, observation, level


def candidate(**changes):
    return dict({'actor_id':0,'hero':'铠','kind':'ultimate','frame_index':0,'effect_visible':True,
        'enemy_visible':True,'identity_visible':True,'confidence':.96,
        'evidence':'敌方红血条下的铠覆盖魔铠，周围持续灼烧特效'}, **changes)


@pytest.fixture(autouse=True)
def model_contract_actor_locator():
    # These tests provide synthetic model replies/actors. Real pixel geometry
    # and the user's ally-only frame are covered in test_effect_actors.py.
    with patch('gameplan.skills.combat_evidence.read_scene', side_effect=lambda *args, **kwargs: scene()), \
         patch('gameplan.skills.keyframe_casts.visible_actors',side_effect=lambda *args, **kwargs:
        [{'hero':None,'x':.65,'y':.35,'bar':[780,200,100,8]}]):
        yield


def scene():
    return {'panel':False,'targets':[],'bindings':[],'hero_levels':[],
            'ally_roster':[],'enemy_roster':[]}


def detect_reply(items, *, recent=None, equipped=None):
    req=VisionRequest(image_base64=image_data(),captured_at=100,focus='skills',recent_frames=recent or [])
    with patch('gameplan.ai.integrations.post_json',return_value={
            'done_reason':'stop','message':{'content':json.dumps({'events':items})}}) as post:
        result=detect(req,b'',scene(),bindings=[],enemies=['铠'],known_heroes={'铠'},equipped=equipped)
    return result,post


def test_old_panel_sampling_flag_cannot_authorize_a_historical_cast():
    recent = [{'image_base64': image_data(), 'captured_at': 90, 'panel_candidate': True}]
    # Even when a false sampling cue contains actors, an old frame is OCR-only.
    result, post = detect_reply([candidate(frame_index=0)], recent=recent)
    assert result['enemy_skill_events'] == []
    assert {'frame_index': 0, 'reason': 'historical_panel_candidate'} in result['skipped_frames']


def test_completed_streamed_event_survives_truncated_second_event_without_inventing_review():
    req=VisionRequest(image_base64=image_data(),captured_at=100,focus='skills')
    prefix='{"events":['+json.dumps(candidate())+',{"hero":"后'
    with patch('gameplan.ai.integrations.post_json',return_value={
            'partial':True,'done_reason':'timeout','message':{'content':prefix}}):
        result=detect(req,b'',scene(),bindings=[],enemies=['铠'],known_heroes={'铠'})
    assert len(result['enemy_skill_events'])==1
    assert result['enemy_skill_events'][0]['hero']=='铠'
    assert result['status']=='partial' and result['checks_completed']==0


@pytest.mark.parametrize('kind,cooldown',[('ultimate',50),('flash',120)])
def test_one_effect_frame_starts_timer_without_before_frame_level_or_equipment(kind,cooldown):
    result,post=detect_reply([candidate(kind=kind)])
    assert post.call_count==1
    assert result['detection_policy']=='single_frame_effect'
    tracker=EventTracker()
    added=tracker.ingest(result['enemy_skill_events'],100,enemies=['铠'],frame_times=[100])
    assert len(added)==1
    assert added[0].captured_at==100 and added[0].cooldown_s==cooldown
    assert added[0].timing_basis=='first_visible_effect'
    assert added[0].cast_window_start is None
    assert '首次识别' in added[0].cooldown_basis
    assert tracker.snapshot(103)[0]['remaining_s']==cooldown-3


def test_machao_charge_recovery_estimate_uses_description_and_keeps_first_effect_time():
    tracker = EventTracker()
    cast = {'hero': '马超', 'skill': '大招', 'slot': 3, 'used': True,
            'confidence': .96, 'event_type': 'keyframe', 'frame_index': 0,
            'evidence': '敌方马超召回冷晖枪，出现专属收枪特效'}
    event = tracker.ingest([cast], 100, enemies=['马超'], frame_times=[100])[0]
    assert event.cooldown_s == 15
    assert event.cooldown_values_s == (15, 13.5, 12)
    assert event.charge_recovery_estimate is True
    assert '剩余次数未知' in event.cooldown_basis
    assert tracker.snapshot(105)[0]['remaining_range_s'] == [7, 10]
    assert tracker.ingest([cast], 105, enemies=['马超'], frame_times=[105]) == []
    assert tracker.events[0].captured_at == 100
    assert tracker.snapshot(115)[0]['remaining_s'] == 0
    # Never invent a recharge time when the source no longer states it.
    tracker.skill_record('马超', '大招')['description'] = '未提供充能时间'
    assert tracker.resolve_cooldown('马超', '大招', 3) is None


def test_batch_uses_first_effect_frame_even_when_already_ongoing_at_index_zero():
    recent=[{'image_base64':image_data(),'captured_at':99}]
    result,_=detect_reply([candidate(frame_index=1),candidate(frame_index=0)],recent=recent)
    assert len(result['enemy_skill_events'])==1
    assert result['enemy_skill_events'][0]['frame_index']==0
    added=EventTracker().ingest(result['enemy_skill_events'],100,enemies=['铠'],frame_times=[99,100])
    assert added[0].captured_at==99


def test_yao_base_estimate_counts_down_and_repeated_effect_does_not_restart():
    tracker = EventTracker()
    cast = {'hero': '瑶', 'skill': '大招', 'slot': 3, 'used': True,
            'confidence': .96, 'event_type': 'keyframe', 'frame_index': 0,
            'evidence': '敌方瑶附身友方英雄，出现专属护盾特效'}
    event = tracker.ingest([cast], 100, enemies=['瑶'], frame_times=[100])[0]
    assert event.cooldown_s == 15
    assert event.special_base_estimate is True
    assert '附身' in event.cooldown_basis
    assert tracker.snapshot(105)[0]['remaining_s'] == 10
    assert tracker.ingest([cast], 105, enemies=['瑶'], frame_times=[105]) == []
    assert tracker.snapshot(115)[0]['remaining_s'] == 0
    assert tracker.snapshot(115)[0]['status'] == 'ready'


@pytest.mark.parametrize('kind,duration',[('ultimate',50),('flash',120)])
def test_repeated_effect_does_not_restart_anywhere_in_the_base_cooldown(kind,duration):
    result,_=detect_reply([candidate(kind=kind)]);tracker=EventTracker()
    first=tracker.ingest(result['enemy_skill_events'],100,enemies=['铠'])[0]
    for now in (101,110,100+duration-1,99):
        assert tracker.ingest(result['enemy_skill_events'],now,enemies=['铠'])==[]
        assert tracker.events[0].id==first.id and tracker.events[0].captured_at==100
    assert len(tracker.ingest(result['enemy_skill_events'],100+duration,enemies=['铠']))==1


@pytest.mark.parametrize('change',[
    {'hero':'艾琳'}, {'hero':'不存在'}, {'hero':{}}, {'kind':'ordinary'}, {'effect_visible':False},
    {'identity_visible':False}, {'enemy_visible':False}, {'confidence':float('nan')},
    {'confidence':True}, {'frame_index':True}, {'frame_index':7}, {'evidence':'技能按钮变暗'},
])
def test_single_frame_rule_still_requires_attributable_enemy_effect(change):
    result,_=detect_reply([candidate(**change)])
    assert result['enemy_skill_events']==[]


def test_uncertain_effect_is_not_promoted_to_a_keyframe_timer():
    result,_=detect_reply([candidate(confidence=.75)])
    assert result['enemy_skill_events']==[] and result['suspicions'][0]['reason']=='model_uncertain'


def test_known_other_summoner_and_fresh_locked_level_remain_contradictions():
    assert detect_reply([candidate(kind='flash')],equipped={'铠':'净化'})[0]['enemy_skill_events']==[]
    result,_=detect_reply([candidate()]);tracker=EventTracker()
    assert tracker.ingest(result['enemy_skill_events'],100,enemies=['铠'],hero_levels=[level(value=3)],frame_times=[100])==[]
    # A stale level-three observation must not erase a later keyframe timer.
    assert len(tracker.ingest(result['enemy_skill_events'],112,enemies=['铠']))==1
    tracker.ingest([],113,enemies=['铠'])
    assert len(tracker.events)==1


def test_timeout_never_creates_keyframe_events():
    req=VisionRequest(image_base64=image_data(),captured_at=100,focus='skills')
    with patch('gameplan.ai.integrations.post_json',side_effect=TimeoutError):
        result=detect(req,b'',scene(),bindings=[],enemies=['铠'],known_heroes={'铠'})
    assert result['enemy_skill_events']==[] and result['failure_reason']=='model_timeout'


@pytest.mark.parametrize('bindings,accepted',[
    ([{'hero':'铠','nickname':'测试玩家'}],True),
    ([{'hero':'艾琳','nickname':'测试玩家'}],False),
    ([{'hero':'铠','nickname':'测试玩家'},{'hero':'艾琳','nickname':'测试玩家'}],False),
])
def test_model_nickname_is_resolved_only_through_unique_verified_enemy_binding(bindings,accepted):
    req=VisionRequest(image_base64=image_data(),captured_at=100,focus='skills')
    with patch('gameplan.ai.integrations.post_json',return_value={'done_reason':'stop',
        'message':{'content':json.dumps({'events':[candidate(hero='测试玩家')]})}}):
        result=detect(req,b'',scene(),bindings=bindings,enemies=['铠'],known_heroes={'铠','艾琳'})
    assert bool(result['enemy_skill_events']) is accepted
    if accepted:assert result['enemy_skill_events'][0]['hero']=='铠'


def test_same_frame_status_coordinates_guide_attention_without_creating_an_event():
    readings=[{'text':'无法命中','score':.95,'box':[[950,120],[1050,120],[1050,140],[950,140]]},
              {'text':'霸体','score':.95,'box':[[0,0],[100,0],[100,20],[0,20]]},
              {'text':'不可选中','score':.7,'box':[[950,120],[1050,120],[1050,140],[950,140]]}]
    assert effect_locations(readings,(2560,1152),0)==[
        {'frame_index':0,'status':'无法命中','x_percent':78,'y_percent':23}]
    req=VisionRequest(image_base64=image_data(),captured_at=100,focus='skills')
    with patch('gameplan.ai.integrations.post_json',return_value={'done_reason':'stop',
        'message':{'content':'{"events":[]}'}}):
        result=detect(req,b'',{**scene(),'readings':readings},bindings=[],enemies=['铠'],known_heroes={'铠'})
    assert not result['enemy_skill_events']


@pytest.mark.parametrize('normal_name_visible',[True,False])
def test_aoyin_same_frame_normal_identity_contradicts_a_claim_of_dragon_form(normal_name_visible):
    req=VisionRequest(image_base64=image_data(),captured_at=100,focus='skills')
    current=scene()
    if normal_name_visible:current['targets']=[{'hero':'敖隐','nickname':'测试角色'}]
    with patch('gameplan.ai.integrations.post_json',return_value={'done_reason':'stop',
        'message':{'content':json.dumps({'events':[candidate(hero='敖隐',evidence='红血条角色出现完整盘旋的长龙身体')]})}}):
        result=detect(req,b'',current,bindings=[],enemies=['敖隐'],known_heroes={'敖隐'})
    assert bool(result['enemy_skill_events']) is not normal_name_visible


def test_keyframe_event_policy_applies_to_every_catalog_ultimate_not_only_first_batch():
    tracker=EventTracker()
    for hero,skills in tracker.catalog.items():
        if not any(s.get('is_ultimate') for s in skills):continue
        added=tracker.ingest([{'hero':hero,'skill':'大招','used':True,'confidence':.96,
            'evidence':'测试提供的敌方专属技能特效','event_type':'keyframe','frame_index':0}],100,enemies=[hero])
        assert len(added)==1 and added[0].timing_basis=='first_visible_effect', hero


def test_actual_api_routes_single_frame_effects_to_both_timer_types(client):
    with patch('gameplan.skills.combat_evidence.read_scene',return_value=scene()), \
         patch('gameplan.ai.integrations.post_json',return_value={'done_reason':'stop',
            'message':{'content':json.dumps({'events':[candidate(),candidate(kind='flash')]})}}):
        first=request(client,{**observation(),'hero_levels':[]},recent_frames=[])
        second=request(client,{**observation(),'hero_levels':[]},recent_frames=[])
    assert first['skill_scan']['detection_policy']=='single_frame_effect'
    assert {t['skill'] for t in first['enemy_skill_timers']}=={'不灭魔躯','闪现'}
    assert len(first['enemy_skill_updates'])==2 and second['enemy_skill_updates']==[]
    assert all(t['timing_basis']=='first_visible_effect' for t in first['enemy_skill_timers'])


def test_public_policy_reports_single_frame_flash_and_all_catalog_ultimates(client):
    policy=client.get('/api/monitor/skill-policy').json()
    assert policy['alert_policy']=='single_frame_effect'
    assert policy['timing_basis']=='first_visible_effect' and policy['minimum_effect_frames']==1
    assert policy['skills']==['ultimate','summoner']
    assert policy['excluded_ultimates']==[]
    heroes={h for h,skills in EventTracker().catalog.items() if any(s.get('is_ultimate') for s in skills)}
    assert {r['hero'] for r in policy['heroes']}==heroes


def test_seven_second_source_latency_is_within_the_ten_second_target(client):
    with patch('gameplan.skills.combat_evidence.read_scene', return_value=scene()), \
         patch('gameplan.ai.integrations.post_json', return_value={'done_reason':'stop',
             'message':{'content':'{"events":[]}'}}):
        result = request(client, observation(), captured_at=time.time()-7, recent_frames=[])
    scan = result['skill_scan']
    assert 5 < scan['latency_s'] < 10
    assert scan['target_latency_s'] == 20 and scan['over_target'] is False


@pytest.mark.parametrize('changes',[{'actor_id':None},{'actor_id':True},{'actor_id':8},
    {'hero':'敖隐'},{'evidence':'队友昵称身旁的光效属于敌方铠'}])
def test_model_cannot_override_locally_bound_actor_or_borrow_an_ally_effect(changes):
    req=VisionRequest(image_base64=image_data(),captured_at=100,focus='skills')
    with patch('gameplan.skills.keyframe_casts.visible_actors',return_value=[
        {'hero':'铠','x':.65,'y':.35,'bar':[780,200,100,8]}]), \
         patch('gameplan.ai.integrations.post_json',return_value={'done_reason':'stop',
            'message':{'content':json.dumps({'events':[candidate(**changes)]})}}):
        result=detect(req,b'',scene(),bindings=[{'hero':'艾琳','nickname':'队友昵称'}],
                      enemies=['铠','敖隐'],known_heroes={'铠','敖隐','艾琳'})
    assert result['enemy_skill_events']==[]
    assert result['rejected_candidates']['actor_identity_mismatch']==1


def test_high_confidence_reply_cannot_create_a_cast_without_a_local_enemy_actor():
    req=VisionRequest(image_base64=image_data(),captured_at=100,focus='skills')
    with patch('gameplan.skills.keyframe_casts.visible_actors',return_value=[]), \
         patch('gameplan.ai.integrations.post_json') as post:
        result=detect(req,b'',scene(),bindings=[],enemies=['敖隐'],known_heroes={'敖隐'})
    assert result['enemy_skill_events']==[] and result['visible_enemy_actors']==0
    post.assert_not_called()


def test_actor_identity_cannot_be_borrowed_from_a_different_frame():
    result,_=detect_reply([candidate(frame_index=1,actor_id=0)],
        recent=[{'image_base64':image_data(),'captured_at':99}])
    assert not result['enemy_skill_events']
    assert result['rejected_candidates']['actor_identity_mismatch']==1


def multi_actors():
    return [{'hero': hero, 'x': .25 + i*.2, 'y': .35, 'bar': [300+i*240, 200, 80, 8]}
            for i, hero in enumerate(['铠', '韩信', '金蝉'])]


def multi_answer(heroes):
    names = ['铠', '韩信', '金蝉']
    return {'done_reason': 'stop', 'message': {'content': json.dumps({
        'reviews': {hero: {'ultimate': 'event', 'summoner': 'event'} for hero in heroes},
        'events': [candidate(hero=hero, actor_id=names.index(hero), kind=kind,
                             evidence=f'敌方{hero}出现可归属的专属技能特效')
                   for hero in heroes for kind in ('ultimate', 'flash')]})}}


def test_multi_cast_omitted_heroes_are_rechecked_without_losing_first_hero():
    req = VisionRequest(image_base64=image_data(), captured_at=100, focus='skills')
    names = ['铠', '韩信', '金蝉']
    with patch('gameplan.skills.keyframe_casts.visible_actors', return_value=multi_actors()), \
         patch('gameplan.ai.integrations.post_json', side_effect=[multi_answer(['铠']), multi_answer(names[1:])]) as post:
        result = detect(req, b'', scene(), bindings=[], enemies=names, known_heroes=set(names))
    tracker = EventTracker()
    added = tracker.ingest(result['enemy_skill_events'], 100, enemies=names, frame_times=[100])
    assert {(e.hero, e.is_ultimate) for e in added} == {(h, u) for h in names for u in (True, False)}
    assert post.call_count == 2
    assert set(post.call_args.args[1]['format']['properties']['reviews']['required']) == set(names[1:])
    assert result['reviewed_heroes'] == names
    assert result['pending_heroes'] == []
    assert len({e.id for e in added}) == 6
    assert not tracker.ingest(result['enemy_skill_events'], 101, enemies=names, frame_times=[101])


def test_multi_cast_recheck_failure_keeps_valid_events_and_reports_pending_targets():
    req = VisionRequest(image_base64=image_data(), captured_at=100, focus='skills')
    names = ['铠', '韩信', '金蝉']
    with patch('gameplan.skills.keyframe_casts.visible_actors', return_value=multi_actors()), \
         patch('gameplan.ai.integrations.post_json', side_effect=[multi_answer(['铠']), TimeoutError]):
        result = detect(req, b'', scene(), bindings=[], enemies=names, known_heroes=set(names))
    assert {e['hero'] for e in result['enemy_skill_events']} == {'铠'}
    assert result['status'] == 'partial'
    assert result['pending_heroes'] == names[1:]
    assert result['checks_completed'] < result['checks_total']


@pytest.mark.parametrize('count', [2, 3, 4, 5])
def test_every_simultaneous_hero_and_skill_survives_a_complete_model_reply(count):
    names = ['铠', '韩信', '金蝉', '程咬金', '桑启'][:count]
    actors = [{'hero': hero, 'x': .15+i*.16, 'y': .35, 'bar': [180+i*190, 200, 80, 8]}
              for i, hero in enumerate(names)]
    events = [candidate(hero=hero, actor_id=i, kind=kind,
                        evidence=f'敌方{hero}可见清楚的专属释放特效')
              for i, hero in enumerate(names) for kind in ('ultimate', 'flash')]
    answer = {'events': events, 'reviews': {h: {'ultimate': 'event', 'summoner': 'event'} for h in names}}
    req = VisionRequest(image_base64=image_data(), captured_at=100, focus='skills')
    with patch('gameplan.skills.keyframe_casts.visible_actors', return_value=actors), \
         patch('gameplan.ai.integrations.post_json', return_value={
             'done_reason': 'stop', 'message': {'content': json.dumps(answer)}}) as post:
        result = detect(req, b'', scene(), bindings=[], enemies=names, known_heroes=set(names))
    tracker = EventTracker()
    added = tracker.ingest(result['enemy_skill_events'], 100, enemies=names, frame_times=[100])
    assert len(added) == count*2
    assert result['pending_heroes'] == [] and result['status'] == 'observed'
    assert post.call_count == 1
    assert post.call_args.args[1]['options']['num_predict'] > 900
    assert 0 < post.call_args.args[2] <= 10


def test_missing_reviews_do_not_trigger_recheck_after_shared_budget_is_spent():
    names = ['铠', '韩信', '金蝉']
    req = VisionRequest(image_base64=image_data(), captured_at=100, focus='skills')
    clock = [0]
    def answer(*args):
        clock[0] = 9
        return multi_answer(['铠'])
    with patch('gameplan.skills.keyframe_casts.visible_actors', return_value=multi_actors()), \
         patch('gameplan.skills.keyframe_casts.time.monotonic', side_effect=lambda: clock[0]), \
         patch('gameplan.ai.integrations.post_json', side_effect=answer) as post:
        result = detect(req, b'', scene(), bindings=[], enemies=names, known_heroes=set(names), budget_s=10)
    assert post.call_count == 1
    assert result['pending_heroes'] == names[1:]
    assert {e['hero'] for e in result['enemy_skill_events']} == {'铠'}


def test_negative_or_uncertain_reviews_never_create_multi_cast_timers():
    names = ['铠', '韩信', '金蝉']
    answer = {'events': [], 'reviews': {
        hero: {'ultimate': state, 'summoner': state}
        for hero, state in zip(names, ['not_seen', 'uncertain', 'not_visible'])}}
    req = VisionRequest(image_base64=image_data(), captured_at=100, focus='skills')
    with patch('gameplan.skills.keyframe_casts.visible_actors', return_value=multi_actors()), \
         patch('gameplan.ai.integrations.post_json', return_value={
             'done_reason': 'stop', 'message': {'content': json.dumps(answer)}}) as post:
        result = detect(req, b'', scene(), bindings=[], enemies=names, known_heroes=set(names))
    assert result['enemy_skill_events'] == []
    assert result['reviewed_heroes'] == names and result['pending_heroes'] == []
    assert post.call_count == 2  # One bounded visual recheck; neither review creates a cast.


def test_review_claim_without_corresponding_event_is_rechecked():
    names = ['铠', '韩信', '金蝉']
    first = multi_answer(names)
    parsed = json.loads(first['message']['content'])
    parsed['events'] = parsed['events'][:2]
    first['message']['content'] = json.dumps(parsed)
    req = VisionRequest(image_base64=image_data(), captured_at=100, focus='skills')
    with patch('gameplan.skills.keyframe_casts.visible_actors', return_value=multi_actors()), \
         patch('gameplan.ai.integrations.post_json', side_effect=[first, multi_answer(names[1:])]) as post:
        result = detect(req, b'', scene(), bindings=[], enemies=names, known_heroes=set(names))
    assert len(result['enemy_skill_events']) == 6
    assert post.call_count == 2


def test_one_unknown_actor_cannot_supply_two_simultaneous_hero_identities():
    req = VisionRequest(image_base64=image_data(), captured_at=100, focus='skills')
    answer = multi_answer(['铠', '韩信'])
    parsed = json.loads(answer['message']['content'])
    for item in parsed['events']:
        item['actor_id'] = 0
    answer['message']['content'] = json.dumps(parsed)
    with patch('gameplan.ai.integrations.post_json', return_value=answer):
        result = detect(req, b'', scene(), bindings=[], enemies=['铠', '韩信'], known_heroes={'铠', '韩信'})
    assert result['enemy_skill_events'] == []
    assert result['rejected_candidates']['conflicting_actor_identity'] == 4


def test_api_returns_every_simultaneous_cast_and_per_hero_review_status(client):
    from gameplan.monitoring import monitor_runtime as runtime
    names = ['铠', '韩信', '金蝉']
    tracker = EventTracker()
    tracker.phase, tracker.roster_verified = 'in_game', True
    tracker.allies, tracker.enemies = ['艾琳'], names
    runtime.trackers['cast-test'] = tracker
    with patch('gameplan.skills.combat_evidence.read_scene', return_value=scene()), \
         patch('gameplan.skills.keyframe_casts.visible_actors', return_value=multi_actors()), \
         patch('gameplan.ai.integrations.post_json', return_value=multi_answer(names)):
        response = request(client, observation(), recent_frames=[])
    assert len(response['enemy_skill_timers']) == len(response['enemy_skill_updates']) == 6
    assert response['skill_scan']['reviewed_heroes'] == names
    assert response['skill_scan']['pending_heroes'] == []
