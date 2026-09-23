"""Behavioral contracts; synthetic evidence is not a vision accuracy test."""
import base64
import io
import time
from unittest.mock import patch

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from gameplan.skills.auto_skill_monitor import EventTracker
from gameplan.core.models import VisionRequest
import gameplan.web.monitor_app as monitor_app
import gameplan.monitoring.monitor_runtime as runtime
import gameplan.ai.integrations as integrations


def cast(**changes):
    return dict(hero="铠", skill="不灭魔躯", slot=3, used=True, confidence=.96,
                evidence="前帧铠未变身，后帧铠开始召唤魔铠，位置和敌方血条一致",
                event_type="cast_start", frame_index=1, **changes)


def level(hero="铠", value=4, index=0, **changes):
    return {"hero": hero, "level": value, "visible_text": str(value), "confidence": .96, "frame_index": index, **changes}


def summoner(hero="铠", skill="闪现", **changes):
    return {"hero": hero, "skill": skill, "confidence": .96, "evidence": "战绩面板该英雄同行的召唤师技能图标",
            "source": "scoreboard_icon", "frame_index": 0, **changes}


def feed(tracker, event=None, captured=1000, enemies=("铠",), allies=("艾琳",), hero_levels=None, summoner_skills=None):
    return tracker.ingest([event or cast()], captured, enemies=enemies, allies=allies,
                          frame_times=[captured-1, captured-.5, captured],
                          hero_levels=[level(hero) for hero in enemies] if hero_levels is None else hero_levels,
                          summoner_skills=[summoner(hero) for hero in enemies] if summoner_skills is None else summoner_skills)


def test_cast_uses_evidence_time_and_catalog_not_invented_model_cooldown():
    tracker = EventTracker()
    event = feed(tracker, {**cast(), "cooldown_s": 3})[0]
    assert event.cooldown_s == 50
    assert event.captured_at == 999.5
    assert tracker.snapshot(1004.5)[0]["remaining_s"] == 45
    assert tracker.snapshot(1050)[0]["status"] == "ready"


@pytest.mark.parametrize("change", [
    {"used": False}, {"used": "false"}, {"confidence": .89}, {"confidence": "NaN"},
    {"confidence": 1.5}, {"evidence": " "}, {"event_type": "ongoing"},
    {"event_type": "uncertain"}, {"frame_index": None}, {"frame_index": 0},
    {"frame_index": 7}, {"skill": "极刃风暴", "slot": 3},
    {"skill": "瞎编的大招", "slot": 3}, {"hero": "艾琳"}, {"hero": "玩家昵称"},
])
def test_uncertain_allied_ordinary_or_invalid_casts_never_start_timers(change):
    tracker = EventTracker()
    assert feed(tracker, {**cast(), **change}) == []
    assert tracker.snapshot() == []


def test_single_still_and_unknown_roster_never_start_timer():
    tracker = EventTracker()
    assert tracker.ingest([cast()], 1000, enemies=["铠"]) == []
    assert feed(tracker, enemies=()) == []
    assert feed(tracker, allies=("铠",)) == []


def test_repeated_frames_and_aliases_do_not_restart_countdown_but_later_recast_does():
    tracker = EventTracker()
    first = feed(tracker)[0]
    assert feed(tracker, {**cast(), "skill": "大招"}, captured=1001) == []
    assert feed(tracker, captured=1008) == []
    assert feed(tracker, captured=999) == []
    second = feed(tracker, captured=1060)[0]
    assert second.id != first.id
    assert len(tracker.events) == 1


def test_all_five_enemies_and_flash_are_independent():
    tracker = EventTracker()
    heroes = ["铠", "孙策", "敖隐", "吕布", "瑶"]
    for hero in heroes:
        feed(tracker, {**cast(), "hero": hero, "skill": "大招"}, enemies=heroes)
        feed(tracker, {**cast(), "hero": hero, "skill": "闪现", "slot": 5}, enemies=heroes)
    assert len(tracker.events) == 10
    assert all(e.cooldown_s == 120 for e in tracker.events if e.skill == "闪现")
    assert {e.slot for e in tracker.events if e.hero == "敖隐"} == {4, 5}


def test_special_and_conflicting_cooldowns_record_usage_without_inventing_total():
    tracker = EventTracker()
    for hero in ("朵莉亚",):
        event = feed(tracker, {**cast(), "hero": hero, "skill": "大招", "cooldown_s": 30}, enemies=[hero])[0]
        assert event.cooldown_s is None
        assert event.is_ultimate


def image_data():
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "navy").save(buffer, "PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def test_sequence_validation():
    for times in ([100, 100], [101, 100], [90, 99]):
        with pytest.raises(ValueError):
            VisionRequest(image_base64="AA==", captured_at=100,
                          recent_frames=[{"image_base64": "AA==", "captured_at": t} for t in times])


def test_extended_history_allows_only_one_bounded_panel_candidate():
    def frame(stamp, panel=True):
        return {'image_base64': 'AA==', 'captured_at': stamp, 'panel_candidate': panel}
    valid = VisionRequest(image_base64='AA==', captured_at=100, focus='skills', recent_frames=[frame(85), frame(99, False)])
    assert valid.recent_frames[0].captured_at == 85
    for frames in ([frame(84.9)], [frame(90, False)], [frame(89), frame(90)]):
        with pytest.raises(ValueError):
            VisionRequest(image_base64='AA==', captured_at=100, focus='skills', recent_frames=frames)


@pytest.fixture
def client():
    runtime.trackers.clear()
    runtime.bp_sessions.clear()
    with TestClient(monitor_app.app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        yield client
    runtime.trackers.clear()
    runtime.bp_sessions.clear()


def request(client, detected, *, verified_equipment=None, **changes):
    now = time.time()
    payload = dict(match_id="cast-test", input_kind="live", focus="skills", captured_at=now,
                   image_base64=image_data(), recent_frames=[{"image_base64": image_data(), "captured_at": now-.5}])
    payload.update(changes)
    # These tests exercise casts/equipment in an already confirmed match.
    # Blank synthetic pixels must not serve as evidence of a selected roster.
    prior = runtime.trackers.get(payload['match_id'])
    if prior is None or not prior.roster_verified:
        prior = EventTracker()
        prior.phase = 'in_game'
        prior.roster_verified = True
        prior.allies, prior.enemies = ['艾琳'], ['铠']
        prior.player = '艾琳'
        runtime.trackers[payload['match_id']] = prior
    with patch.object(runtime, "portrait_observation", return_value=(None, None)), \
         patch.object(runtime, "read_lane_banner", return_value=None), \
         patch('gameplan.vision.summoner_icons.read_equipment', return_value=detected.get("summoner_skills", []) if verified_equipment is None else verified_equipment), \
         patch.object(runtime, "vision", return_value=(detected, "test-model")):
        result = client.post("/api/vision/observe", json=payload)
    assert result.status_code == 200, result.text
    return result.json()


def observation(**changes):
    return dict(phase="in_game", ally_roster=["艾琳"], enemy_roster=["铠"],
                player_hero="艾琳", enemy_skill_events=[cast()], hero_levels=[level()], **changes)


def test_runtime_only_emits_new_ids_and_retains_record_during_unreadable_frame(client):
    first = request(client, observation())
    assert len(first["enemy_skill_updates"]) == 1
    second = request(client, {**observation(), "enemy_roster": [], "enemy_skill_events": []})
    assert second["observation"]["enemy_roster"] == ["铠"]
    assert second["enemy_skill_updates"] == []
    assert second["enemy_skill_timers"][0]["id"] == first["enemy_skill_timers"][0]["id"]
    assert second["enemy_skill_timers"][0]["remaining_s"] <= first["enemy_skill_timers"][0]["remaining_s"]


def test_bad_candidate_does_not_discard_good_event_and_wrong_team_is_filtered(client):
    data = request(client, {**observation(), "enemy_skill_events": [{**cast(), "confidence": "bad"}, cast(), {**cast(), "hero": "艾琳"}]})
    assert len(data["enemy_skill_timers"]) == 1
    assert data["enemy_skill_timers"][0]["hero"] == "铠"


def test_partial_scoreboard_cannot_shrink_or_reorder_confirmed_five(client):
    request(client, observation())
    tracker = runtime.trackers['cast-test']
    names = ['铠', '韩信', '金蝉', '程咬金', '桑启']
    tracker.enemies = names[:]
    for reading in (names[:3] + ['吕布'], names[::-1]):
        scene = {'phase': 'in_game', 'panel': True, 'side_known': True,
                 'ally_roster': ['艾琳'], 'enemy_roster': reading, 'bindings': [],
                 'targets': [], 'hero_levels': [], 'equipment': [], 'readings': []}
        with patch('gameplan.skills.combat_evidence.read_scene', return_value=scene):
            result = request(client, observation(), recent_frames=[])
        assert result['observation']['enemy_roster'] == names
        assert tracker.tracking_enemies == names
        assert result['enemy_skill_timers'][0]['hero'] == '铠'


@pytest.mark.parametrize('age', [1.5, 10])
@pytest.mark.parametrize('roster', ['same', 'other_match', 'opposite_side'])
def test_brief_buffered_scoreboard_updates_equipment_and_level_after_it_closes(client, age, roster):
    request(client, observation(), captured_at=time.time()-20, recent_frames=[])
    gameplay = {'phase': 'in_game', 'panel': False, 'bindings': [], 'targets': [],
                'ally_roster': [], 'enemy_roster': [], 'hero_levels': [], 'readings': []}
    panel = {**gameplay, 'panel': True, 'ally_roster': ['艾琳'], 'enemy_roster': ['铠'],
             'hero_levels': [level(value=13, frame_index=0)], 'readings': [{'marker': 'panel'}]}
    if roster == 'other_match':
        panel['enemy_roster'] = ['铠', '妲己']
    elif roster == 'opposite_side':
        panel.update(ally_roster=['铠'], enemy_roster=['艾琳'])
    now = time.time()
    with patch('gameplan.skills.combat_evidence.read_scenes', return_value=[panel, gameplay]), \
         patch('gameplan.skills.combat_evidence.read_scene', return_value=gameplay), \
         patch('gameplan.skills.keyframe_casts.detect', return_value={
             'enemy_skill_events': [], 'hero_levels': [], 'activity': [], 'status': 'no_visible_target'}), \
         patch.object(runtime, 'portrait_observation', return_value=(None, None)), \
         patch('gameplan.vision.summoner_icons.read_equipment', side_effect=lambda *a, **kw:
               [summoner()] if kw.get('readings') == panel['readings'] else []):
        response = client.post('/api/vision/observe', json={
            'match_id': 'cast-test', 'focus': 'skills', 'image_base64': image_data(), 'captured_at': now,
            'recent_frames': [{'image_base64': image_data(), 'captured_at': now-age, 'panel_candidate': True}]})
    assert response.status_code == 200, response.text
    data = response.json()
    if roster != 'same':
        assert data['enemy_summoner_states'][0]['skill'] is None
        assert data['enemy_ultimate_states'][0]['level'] == 4
        return
    assert data['enemy_summoner_states'][0]['skill'] == '闪现'
    assert data['enemy_ultimate_states'][0]['level'] == 13
    assert data['enemy_ultimate_states'][0]['level_captured_at'] == now-age
    assert next(r for r in data['observation']['summoner_skills'] if r['hero'] == '铠')['frame_index'] == 0


@pytest.mark.parametrize("phase", ["bp", "loading", "result", "not_game", "unknown"])
def test_non_playing_phase_cannot_create_release(client, phase):
    data = request(client, {**observation(), "phase": phase})
    assert data["enemy_skill_timers"] == data["enemy_skill_updates"] == []


def test_result_new_loading_and_explicit_reset_discard_match_records(client):
    for boundary in ("result", "loading"):
        before = request(client, observation())
        local_loading={'phase':'loading','panel':False,'side_known':True,'player_hero':'艾琳',
                       'ally_roster':['艾琳'],'enemy_roster':['铠'],'bindings':[],
                       'targets':[],'hero_levels':[],'equipment':[],'readings':[]}
        with patch('gameplan.skills.combat_evidence.read_scene',return_value=local_loading if boundary=='loading' else None):
            request(client, {**observation(), "phase": boundary, "enemy_skill_events": []})
        result = request(client, {**observation(), "enemy_skill_events": []})
        assert result["enemy_skill_timers"] == []
        assert result['match_epoch'] != before['match_epoch']
    request(client, observation())
    assert client.post("/api/monitor/match/reset", json={"match_id": "cast-test"}).json()["cleared"]
    assert "cast-test" not in runtime.trackers
    assert request(client, {**observation(), "enemy_skill_events": []})["enemy_skill_timers"] == []


def test_unverified_model_loading_guess_cannot_erase_a_confirmed_match(client):
    first=request(client,observation())
    request(client,{**observation(),'phase':'loading','enemy_skill_events':[]})
    resumed=request(client,{**observation(),'enemy_skill_events':[]})
    assert resumed['enemy_skill_timers'][0]['id']==first['enemy_skill_timers'][0]['id']


def test_reset_during_inference_cannot_restore_old_match(client):
    def reset_during_vision(*args, **kwargs):
        runtime.reset_match("cast-test")
        return observation(), "test-model"
    with patch.object(runtime, "portrait_observation", return_value=(None, None)), \
         patch.object(runtime, "read_lane_banner", return_value=None), \
         patch.object(runtime, "vision", side_effect=reset_during_vision):
        response = client.post("/api/vision/observe", json={"match_id": "cast-test", "image_base64": image_data()})
    assert response.status_code == 409
    assert "cast-test" not in runtime.trackers


def test_model_receives_temporal_sequence_and_strict_event_schema():
    req = VisionRequest(image_base64=image_data(), captured_at=100, focus="skills",
                        recent_frames=[{"image_base64": image_data(), "captured_at": 99}])
    _, prepared = integrations.image_bytes(req)
    response = {"message": {"content": '{"phase":"in_game","enemy_skill_events":[]}'}}
    with patch.object(integrations, "post_json", return_value=response) as post:
        integrations.vision(req, "observe", prepared, roster_context={"enemy_roster": ["铠"]})
    payload = post.call_args.args[1]
    assert len(payload["messages"][0]["images"]) == 2
    assert "第一次看到起手" in payload["messages"][0]["content"]
    assert "cast_start" in str(payload["format"])
    assert "cooldown_s" not in str(payload["format"])


@pytest.mark.parametrize("value", [1, 2, 3])
def test_locked_ultimate_is_rejected_even_when_model_claims_certain_cast(value):
    tracker = EventTracker()
    assert feed(tracker, {**cast(), "confidence": 1}, hero_levels=[level(value=value)]) == []
    assert tracker.ultimate_states(["铠"], 1000)[0]["status"] == "locked"
    assert tracker.snapshot(1000) == []


def test_unknown_level_never_implies_ultimate_ready_or_used_but_flash_is_independent():
    tracker = EventTracker()
    assert feed(tracker, hero_levels=[]) == []
    assert tracker.ultimate_states(["铠"], 1000)[0]["status"] == "level_unknown"
    flash = {**cast(), "skill": "闪现", "slot": 5}
    assert len(feed(tracker, flash, hero_levels=[level(value=1)])) == 1
    assert tracker.snapshot(1000)[0]["skill"] == "闪现"


def test_reaching_four_only_unlocks_tracking_and_does_not_create_a_cast():
    tracker = EventTracker()
    tracker.ingest([], 1000, enemies=["铠"], frame_times=[999, 1000], hero_levels=[level()])
    assert tracker.ultimate_states(["铠"], 1000)[0]["status"] == "unlocked"
    assert tracker.snapshot(1000) == []
    assert len(feed(tracker, captured=1001, hero_levels=[])) == 1


def test_future_level_four_cannot_validate_earlier_level_three_cast():
    tracker = EventTracker()
    levels = [level(value=3, index=0), level(value=4, index=2)]
    assert feed(tracker, hero_levels=levels) == []
    assert len(feed(tracker, {**cast(), "frame_index": 2}, hero_levels=levels)) == 1


@pytest.mark.parametrize("readings", [
    [level(visible_text="3")], [level(confidence=.89)], [level(hero="艾琳")],
    [level(index=7)], [level(value=3), level(value=4)],
])
def test_invalid_uncertain_allied_or_conflicting_level_cannot_unlock(readings):
    assert feed(EventTracker(), hero_levels=readings) == []


def test_low_level_evidence_expires_but_confirmed_unlock_persists():
    tracker = EventTracker()
    feed(tracker, hero_levels=[level(value=3)])
    assert tracker.ultimate_states(["铠"], 1000)[0]["status"] == "locked"
    assert tracker.ultimate_states(["铠"], 1010)[0]["status"] == "level_unknown"
    feed(tracker, captured=1011, hero_levels=[level()])
    assert tracker.ultimate_states(["铠"], 1050)[0]["status"] == "unlocked"


def test_new_low_level_evidence_removes_contradictory_ultimate_without_removing_flash():
    tracker = EventTracker()
    feed(tracker)
    feed(tracker, {**cast(), "skill": "闪现", "slot": 5})
    tracker.ingest([], 1002, enemies=["铠"], frame_times=[1001, 1002], hero_levels=[level(value=3)])
    assert [event.skill for event in tracker.events] == ["闪现"]


def test_runtime_returns_level_states_and_clears_unlock_on_new_match(client):
    low = request(client, {**observation(), "hero_levels": [level(value=3)]})
    assert low["enemy_ultimate_states"][0]["status"] == "locked"
    assert low["enemy_skill_timers"] == low["enemy_skill_updates"] == []
    high = request(client, {**observation(), "enemy_skill_events": []})
    assert high["enemy_ultimate_states"][0]["status"] == "unlocked"
    assert high["enemy_skill_updates"] == []
    client.post("/api/monitor/match/reset", json={"match_id": "cast-test"})
    unknown = request(client, {**observation(), "hero_levels": []})
    assert unknown["enemy_ultimate_states"][0]["status"] == "level_unknown"
    assert unknown["enemy_skill_updates"] == []


@pytest.mark.parametrize("skill", ["惩击", "惩戒", "净化", "疾跑", "狂暴", "治疗术", "眩晕", "终结", "弱化", "干扰"])
def test_non_flash_carriers_cannot_create_a_flash_timer(skill):
    tracker = EventTracker()
    assert feed(tracker, {**cast(), "skill": "闪现", "slot": 5}, summoner_skills=[summoner(skill=skill)]) == []
    assert tracker.summoner_states(["铠"])[0]["skill"] != "闪现"
    assert tracker.rejections == {"flash_not_equipped_or_unknown": 1}


def test_unknown_equipment_does_not_default_to_flash_or_infer_from_cast():
    tracker = EventTracker()
    assert feed(tracker, {**cast(), "skill": "闪现", "slot": 5}, summoner_skills=[]) == []
    assert tracker.summoner_states(["铠"])[0] == {"hero": "铠", "skill": None, "status": "unknown"}


@pytest.mark.parametrize("changes", [{"source": "jungle_role"}, {"source": "self_hud"}, {"confidence": .5}, {"evidence": " "}, {"hero": "艾琳"}, {"frame_index": 7}])
def test_equipment_needs_clear_enemy_icon_evidence(changes):
    tracker = EventTracker()
    assert feed(tracker, {**cast(), "skill": "闪现", "slot": 5}, summoner_skills=[summoner(**changes)]) == []


def test_equipment_is_kept_when_temporarily_hidden_and_correction_removes_false_flash():
    tracker = EventTracker()
    event = {**cast(), "skill": "闪现", "slot": 5}
    first = feed(tracker, event)[0]
    assert feed(tracker, event, captured=1040, summoner_skills=[]) == []
    assert tracker.events[0].id == first.id
    assert tracker.summoner_states(["铠"])[0]["skill"] == "闪现"
    assert feed(tracker, event, captured=1121, summoner_skills=[summoner(skill="惩击")]) == []
    assert tracker.events == []
    assert tracker.summoner_states(["铠"])[0]["skill"] == "惩击"


def test_conflicting_equipment_is_unknown_and_older_frames_cannot_overwrite_correction():
    tracker = EventTracker()
    feed(tracker, summoner_skills=[summoner(), summoner(skill="惩击")])
    assert tracker.summoner_states(["铠"])[0]["status"] == "unknown"
    feed(tracker, captured=1002, summoner_skills=[summoner(skill="惩击")])
    feed(tracker, captured=1001, summoner_skills=[summoner()])
    assert tracker.summoner_states(["铠"])[0]["skill"] == "惩击"


def test_cooldown_guard_blocks_repeated_effects_but_allows_plausible_shorter_rank_cdr_recast():
    tracker = EventTracker()
    first = feed(tracker)[0]
    # Old implementation allowed this same apparent effect to retrigger at 16s.
    assert feed(tracker, captured=1016) == []
    assert tracker.events[0].id == first.id
    assert tracker.rejections == {"duplicate_or_early_recast": 1}
    # Kai's shortest base is 40s; 40% CDR permits 24s, earlier than the 50s estimate.
    assert feed(tracker, captured=1025)[0].id != first.id


def test_frozen_images_cannot_confirm_a_cast_even_if_model_claims_it():
    req = VisionRequest(image_base64=image_data(), captured_at=100, focus="skills",
        recent_frames=[{"image_base64": image_data(), "captured_at": 99}])
    _, prepared = integrations.image_bytes(req)
    import json
    response = {"message": {"content": json.dumps(observation())}}
    with patch.object(integrations, "post_json", return_value=response):
        result, _ = integrations.vision(req, "observe", prepared)
    assert result["enemy_skill_events"] == []


def test_runtime_keeps_each_heroes_equipment_and_reset_discards_it(client):
    data = request(client, {**observation(), "summoner_skills": [summoner(skill="惩戒")]})
    assert data["enemy_summoner_states"] == [{"hero": "铠", "skill": "惩击", "status": "confirmed"}]
    hidden = request(client, {**observation(), "summoner_skills": []})
    assert hidden["enemy_summoner_states"] == data["enemy_summoner_states"]
    client.post("/api/monitor/match/reset", json={"match_id": "cast-test"})
    assert request(client, observation())["enemy_summoner_states"][0]["status"] == "unknown"


def test_model_equipment_claim_cannot_override_empty_local_verification(client):
    detected = {**observation(), "summoner_skills": [summoner()],
                "enemy_skill_events": [{**cast(), "skill": "闪现", "slot": 5}]}
    data = request(client, detected, verified_equipment=[])
    assert data["enemy_summoner_states"] == [{"hero": "铠", "skill": None, "status": "unknown"}]
    assert data["enemy_skill_updates"] == data["enemy_skill_timers"] == []


def test_manual_equipment_correction_is_match_scoped_and_auto_observation_cannot_overwrite_it(client):
    request(client, observation())
    payload = {"match_id": "cast-test", "hero": "铠", "skill": "惩击"}
    result = client.post("/api/monitor/summoner/correct", json=payload)
    assert result.status_code == 200
    assert result.json()["enemy_summoner_states"][0] == {"hero": "铠", "skill": "惩击", "status": "confirmed", "source": "manual"}
    automatic = request(client, {**observation(), "summoner_skills": [summoner()]})
    assert automatic["enemy_summoner_states"][0]["skill"] == "惩击"
    assert client.post("/api/monitor/summoner/correct", json={**payload, "hero": "艾琳"}).status_code == 422
    assert client.post("/api/monitor/summoner/correct", json={**payload, "skill": "瞎编技能"}).status_code == 422
    cleared = client.post("/api/monitor/summoner/correct", json={**payload, "skill": None}).json()
    assert cleared["enemy_summoner_states"][0]["status"] == "unknown"
    client.post("/api/monitor/match/reset", json={"match_id": "cast-test"})
    assert client.post("/api/monitor/summoner/correct", json=payload).status_code == 404


def test_cross_origin_cannot_correct_local_equipment(client):
    response = client.post("/api/monitor/summoner/correct", json={"match_id": "cast-test", "hero": "铠", "skill": "闪现"},
                           headers={"origin": "https://unrelated.example"})
    assert response.status_code == 403


@pytest.mark.parametrize("correction", [None, "惩击"])
def test_correction_removes_false_flash_timer_and_dates_remaining_ultimate_time(client, correction):
    detected = {**observation(), "summoner_skills": [summoner()],
                "enemy_skill_events": [cast(), {**cast(), "skill": "闪现", "slot": 5}]}
    initial = request(client, detected)
    assert len(initial["enemy_skill_timers"]) == 2
    now = time.time() + 5
    with patch('gameplan.web.monitor_app.time.time', return_value=now):
        response = client.post("/api/monitor/summoner/correct",
                               json={"match_id": "cast-test", "hero": "铠", "skill": correction})
    assert response.status_code == 200
    result = response.json()
    assert result["processed_at"] == now
    assert [t["skill"] for t in result["enemy_skill_timers"]] == ["不灭魔躯"]
    timer = result["enemy_skill_timers"][0]
    assert timer["remaining_s"] == pytest.approx(timer["cooldown_s"] - (now - timer["captured_at"]))


@pytest.mark.parametrize("reset_during_scan", [False, True])
def test_local_scoreboard_context_reaches_targeted_pipeline_and_reset_discards_inflight_result(client, reset_during_scan):
    binding = {"hero": "铠", "nickname": "测试玩家名", "side": "enemy_roster"}
    panel = {"panel": True, "ally_roster": ["艾琳"], "enemy_roster": ["铠"],
             "bindings": [binding], "hero_levels": [level()], "targets": [], "readings": []}
    request(client, {**observation(), "enemy_skill_events": []})
    with patch('gameplan.skills.combat_evidence.read_scene', return_value=panel):
        scoreboard = request(client, observation())
    assert scoreboard["enemy_skill_updates"] == []
    assert scoreboard["observation"]["player_hero"] == "艾琳"
    assert runtime.trackers["cast-test"].identities == [binding]
    scene = {**panel, "panel": False, "bindings": [], "hero_levels": []}

    def detect(*args, **kwargs):
        assert kwargs["bindings"] == [binding]
        assert kwargs["enemies"] == ["铠"]
        if reset_during_scan:
            runtime.reset_match("cast-test")
        return {"enemy_skill_events": [cast()], "hero_levels": [], "activity": [], "status": "observed",
                "last_check": ("铠", "不灭魔躯")}

    now = time.time()
    with patch('gameplan.skills.combat_evidence.read_scene', return_value=scene), patch('gameplan.skills.keyframe_casts.detect', side_effect=detect), \
         patch.object(runtime, "portrait_observation", return_value=(None, None)), \
         patch('gameplan.vision.summoner_icons.read_equipment', return_value=[]), \
         patch.object(runtime, "vision", side_effect=AssertionError("Whole-screen fallback must not run")):
        response = client.post("/api/vision/observe", json={"match_id": "cast-test", "image_base64": image_data(),
            "captured_at": now, "focus": "skills", "recent_frames": [{"image_base64": image_data(), "captured_at": now-.5}]})
    if reset_during_scan:
        assert response.status_code == 409
        assert "cast-test" not in runtime.trackers
    else:
        assert response.status_code == 200
        assert len(response.json()["enemy_skill_updates"]) == 1
        assert response.json()["observation"]["player_hero"] == "艾琳"
        assert runtime.trackers["cast-test"].last_skill_check == ("铠", "不灭魔躯")
        assert "测试玩家名" not in response.text


def test_partial_loading_frame_does_not_erase_verified_enemy_or_replace_erin(client):
    roster=['吕布','孙策','嬴政','敖隐','瑶']
    load={'phase':'loading','panel':False,'side_known':True,'player_hero':'艾琳','ally_roster':['艾琳'],
          'enemy_roster':roster,'bindings':[{'hero':h,'nickname':h+'玩家','side':'enemy_roster'} for h in roster],
          'targets':[],'hero_levels':[],'equipment':[],'readings':[]}
    with patch('gameplan.skills.combat_evidence.read_scene',return_value=load):
        first=request(client,observation())
    assert first['observation']['enemy_roster']==roster
    partial={**load,'enemy_roster':roster[1:],'bindings':load['bindings'][1:]}
    with patch('gameplan.skills.combat_evidence.read_scene',return_value=partial):
        second=request(client,observation())
    assert second['observation']['enemy_roster']==roster
    battlefield={**load,'phase':'in_game','side_known':False,'player_hero':None,'enemy_roster':[],
                 'ally_roster':[],'bindings':[]}
    scan={'enemy_skill_events':[],'activity':[],'hero_levels':[],'status':'no_visible_target'}
    with patch('gameplan.skills.combat_evidence.read_scene',return_value=battlefield),patch('gameplan.skills.keyframe_casts.detect',return_value=scan):
        third=request(client,observation())
    assert third['observation']['enemy_roster']==roster
    assert third['observation']['player_hero']=='艾琳'


@pytest.mark.parametrize('guess', ['铠', '廉颇', None])
def test_verified_local_player_survives_model_guess_and_stale_opposite_perspective(client, guess):
    load={'phase':'loading','panel':False,'side_known':True,'player_hero':'艾琳',
          'ally_roster':['艾琳','廉颇'],'enemy_roster':['铠'],'bindings':[],
          'targets':[],'hero_levels':[],'equipment':[],'readings':[]}
    with patch('gameplan.skills.combat_evidence.read_scene',return_value=load):
        first=request(client,observation(),perspective_side='b')
    assert first['team_context']['side']=='a'
    with patch('gameplan.skills.combat_evidence.read_scene',return_value=None):
        result=request(client,{**observation(),'player_hero':guess,
            'enemy_skill_events':[cast(),{**cast(),'hero':'艾琳'}]},perspective_side='b')
    assert result['observation']['player_hero']=='艾琳'
    assert result['observation']['ally_roster']==['艾琳','廉颇']
    assert result['observation']['enemy_roster']==['铠']
    assert result['team_context']=={'side':'a','source':'local_name_highlight'}
    assert [t['hero'] for t in result['enemy_skill_timers']]==['铠']
    assert runtime.trackers['cast-test'].tracking_enemies==['铠']


def test_stale_opposite_perspective_never_retargets_locally_verified_cast_detector(client):
    request(client,observation())
    scene={'panel':False,'ally_roster':[],'enemy_roster':[],'bindings':[],
           'targets':[],'hero_levels':[],'readings':[]}
    scan={'enemy_skill_events':[],'hero_levels':[],'activity':[],'status':'observed'}
    with patch('gameplan.skills.combat_evidence.read_scene',return_value=scene), \
         patch('gameplan.skills.keyframe_casts.detect',return_value=scan) as detect:
        request(client,observation(),perspective_side='b')
    assert detect.call_args.kwargs['enemies']==['铠']


@pytest.mark.parametrize("evidence", ["战绩图标由亮变暗", "技能按钮消失", "技能栏出现大招特效"])
def test_icon_changes_are_not_release_evidence(evidence):
    tracker = EventTracker()
    assert feed(tracker, {**cast(), "evidence": evidence}) == []
    assert tracker.rejections == {"icon_is_not_cast_evidence": 1}
