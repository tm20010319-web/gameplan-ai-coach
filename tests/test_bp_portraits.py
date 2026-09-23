import asyncio
import base64
import io
import time
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageOps

import gameplan.vision.bp_portraits as bp_portraits
import gameplan.monitoring.monitor_runtime as monitor_runtime
from gameplan.core.models import VisionRequest

FIXTURES = Path(__file__).parent / 'fixtures' / 'bp'
ALLIES = ['艾琳', '廉颇', '云缨', '海月', '杨戬']
ENEMIES = ['瑶', '吕布', '敖隐', '孙策', '嬴政']


def test_official_portraits_recognize_locked_slots_at_two_resolutions():
    raw = (FIXTURES / 'locked.jpg').read_bytes()
    small = Image.open(io.BytesIO(raw)).resize((960, 432))
    out = io.BytesIO(); small.save(out, format='JPEG', quality=85)
    letterbox = io.BytesIO()
    ImageOps.expand(Image.open(io.BytesIO(raw)),border=(0,72),fill='black').save(letterbox,format='JPEG',quality=90)
    for data in (raw, out.getvalue(), letterbox.getvalue()):
        result = bp_portraits.match(data)
        assert result['left'] == ALLIES
        assert result['right'] == ENEMIES
        assert result['player_hero'] == '艾琳'


def test_hero_pool_and_ingame_hud_are_not_locked_team_slots():
    for name in ('unselected.jpg', 'in-game.jpg'):
        assert bp_portraits.match((FIXTURES / name).read_bytes()) is None


def test_temporal_confirmation_rejects_transients_repeated_timestamps_and_long_gaps():
    result = {'slots': [{'side': 'left', 'row': 0, 'hero': '艾琳'}]}
    previous, confirmed = bp_portraits.confirm(None, result, 100)
    assert not confirmed
    assert not bp_portraits.confirm(previous, result, 100)[1]
    assert not bp_portraits.confirm(previous, result, 131)[1]
    assert bp_portraits.confirm(previous, result, 101)[1][0]['hero'] == '艾琳'
    other = {'slots': [{'side': 'left', 'row': 0, 'hero': '后羿'}]}
    assert not bp_portraits.confirm(previous, other, 101)[1]
    assert not bp_portraits.confirm(previous, {'slots': [{'side':'left','row':0,'hero':None}]}, 101)[1]


def test_live_pipeline_confirms_then_carries_identity_without_cross_match_leakage():
    async def run():
        now = time.time()-5
        def req(name, tick, match='portrait-test'):
            return VisionRequest(match_id=match,input_kind='live',captured_at=now+tick,image_base64=base64.b64encode((FIXTURES/name).read_bytes()).decode())
        monitor_runtime.bp_sessions.clear()
        with patch('gameplan.monitoring.monitor_runtime.vision', side_effect=AssertionError('BP should not call Qwen')):
            first = await monitor_runtime.observe(req('locked.jpg',0))
            second = await monitor_runtime.observe(req('locked.jpg',1))
            third = await monitor_runtime.observe(req('lane.jpg',2))
        assert not first['observation']['ally_roster']
        assert second['observation']['ally_roster'] == ALLIES
        assert second['observation']['enemy_roster'] == ENEMIES
        assert third['bp_context']['lane'] == '发育路'
        with patch('gameplan.monitoring.monitor_runtime.vision',return_value=({'phase':'loading'},'qwen3-vl:8b')):
            loading = await monitor_runtime.observe(req('in-game.jpg',3))
            fresh = await monitor_runtime.observe(req('in-game.jpg',4,'another-match'))
        assert loading['observation']['ally_roster'] == ALLIES
        assert loading['bp_context']['player'] == '艾琳'
        assert not fresh['observation']['ally_roster']
        assert fresh['bp_context'] is None
        with patch('gameplan.monitoring.monitor_runtime.vision',side_effect=AssertionError('Portrait BP should stay local')):
            next_match = await monitor_runtime.observe(req('locked.jpg',5))
        assert not next_match['observation']['ally_roster']
        assert next_match['bp_context']['lane'] is None
    asyncio.run(run())


def test_sparse_enemy_selection_never_uses_hero_pool_fallback():
    raw=(FIXTURES/'sparse-enemy-preview.png').read_bytes()
    result=bp_portraits.match(raw,allow_sparse=True)
    assert result['left'] == []
    assert result['right'] == ['瑶']
    assert [s['row'] for s in result['slots'] if s['side']=='right' and s['hero']] == [0]
    async def run():
        runtime=monitor_runtime
        match='sparse-current-bp'
        runtime.bp_sessions.pop(match,None)
        with patch('gameplan.monitoring.monitor_runtime.vision',side_effect=AssertionError('No whole-frame roster guessing')):
            for _ in range(2):
                out=await runtime.observe(VisionRequest(match_id=match,image_base64=base64.b64encode(raw).decode(),captured_at=time.time(),input_kind='video'))
        assert out['observation']['enemy_roster'] == ['瑶']
        assert out['observation']['ally_roster'] == []
        right=[s for s in out['bp_slots'] if s['side']=='right']
        assert [s['hero'] for s in right] == ['瑶',None,None,None,None]
    asyncio.run(run())


def test_preselection_with_hidden_enemies_is_bp_without_invented_slots():
    raw = (FIXTURES / 'hidden-enemies.png').read_bytes()
    resized = io.BytesIO()
    Image.open(io.BytesIO(raw)).resize((960, 432)).save(resized, format='PNG')
    for pixels in (raw, resized.getvalue()):
        result = bp_portraits.match(pixels, allow_sparse=True)
        assert result is not None
        assert result['phase'] == 'bp'
        assert result['right'] == []
        assert all(slot['hero'] is None for slot in result['slots'] if slot['side'] == 'right')
    assert bp_portraits.match((FIXTURES / 'in-game.jpg').read_bytes(), allow_sparse=True) is None


def test_hidden_enemy_preselection_clears_guessed_match_identity_and_skips_whole_frame_model():
    from gameplan.skills.auto_skill_monitor import EventTracker
    raw = (FIXTURES / 'hidden-enemies.png').read_bytes()
    match_id = 'hidden-enemy-preselection'
    async def run():
        monitor_runtime.reset_match(match_id)
        previous = EventTracker()
        previous.phase = 'in_game'
        previous.enemies = ['程咬金', '李信', '兰陵王', '铠', '后羿']
        previous.allies = ['蔡文姬']
        previous.roster_verified = True
        previous.last_seen = time.time()
        monitor_runtime.trackers[match_id] = previous
        try:
            with patch('gameplan.monitoring.monitor_runtime.vision', side_effect=AssertionError('Never guess a roster from the hero pool')), \
                 patch('gameplan.skills.grounded_casts.detect', side_effect=AssertionError('BP has no combat skill releases')):
                for tick in range(2):
                    response = await monitor_runtime.observe(VisionRequest(
                        match_id=match_id, input_kind='video', focus='skills', captured_at=time.time()+tick,
                        image_base64=base64.b64encode(raw).decode()))
                    assert response['observation']['phase'] == 'bp'
                    assert response['observation']['enemy_roster'] == []
                    assert response['enemy_skill_timers'] == []
                    assert response['enemy_ultimate_states'] == []
                    assert response['enemy_summoner_states'] == []
                    assert len(response['bp_slots']) == 10
                    assert all(slot['hero'] is None for slot in response['bp_slots'] if slot['side'] == 'right')
            assert monitor_runtime.trackers[match_id] is not previous
            assert monitor_runtime.trackers[match_id].enemies == []
        finally:
            monitor_runtime.reset_match(match_id)
    asyncio.run(run())


def test_video_ready_transition_and_role_map_cannot_seed_or_retain_a_model_guessed_roster():
    from gameplan.skills.auto_skill_monitor import EventTracker
    match_id = 'video-before-selection'
    guessed = {'phase': 'in_game', 'ally_roster': ['蔡文姬'],
               'enemy_roster': ['兰陵王', '铠', '吕布', '赵云', '公孙离'],
               'player_hero': '蔡文姬', 'hero_levels': [], 'enemy_skill_events': []}
    async def run():
        monitor_runtime.reset_match(match_id)
        # Reproduce an earlier unverified roster already cached in this session.
        previous = EventTracker()
        previous.phase = 'in_game'
        previous.allies = guessed['ally_roster'][:]
        previous.enemies = guessed['enemy_roster'][:]
        previous.last_seen = time.time()
        monitor_runtime.trackers[match_id] = previous
        try:
            with patch('gameplan.monitoring.monitor_runtime.vision', return_value=(guessed, 'whole-frame-model')):
                for name, phase in [('match-ready.png', 'unknown'), ('match-transition.png', 'unknown'),
                                    ('jungle-assignment.png', 'bp'), ('hidden-enemies.png', 'bp')]:
                    guessed['phase'] = 'loading' if name == 'match-ready.png' else 'in_game'
                    response = await monitor_runtime.observe(VisionRequest(
                        match_id=match_id, input_kind='video', focus='skills', captured_at=time.time(),
                        image_base64=base64.b64encode((FIXTURES / name).read_bytes()).decode()))
                    assert response['observation']['phase'] == phase, name
                    assert response['observation']['enemy_roster'] == [], name
                    assert response['enemy_skill_timers'] == []
                    assert response['enemy_ultimate_states'] == []
                    assert response['enemy_summoner_states'] == []
                    assert monitor_runtime.trackers[match_id].enemies == []
                    if name == 'jungle-assignment.png':
                        assert response['lane_context']['lane'] == '打野'
        finally:
            monitor_runtime.reset_match(match_id)
    asyncio.run(run())


def test_missing_slot_model_preserves_local_matches_and_requires_two_frames():
    import copy, json
    raw=(FIXTURES/'locked.jpg').read_bytes()
    result=bp_portraits.match(raw)
    result['slots'][-1].update(hero=None,similarity=.75)
    original=copy.deepcopy(result)
    response={'message':{'content':json.dumps({'slots':[{'hero':'嬴政','confidence':.95}]})}}
    with patch('gameplan.ai.integrations.post_json',return_value=response) as call:
        filled=bp_portraits.supplement_missing(raw,result,'test')
    assert result==original
    assert filled['slots'][:-1]==result['slots'][:-1]
    assert filled['slots'][-1]['hero']=='嬴政'
    assert call.call_args[0][1]['model']=='test'
    pending,first=bp_portraits.confirm(None,filled,100)
    assert not first
    assert len(bp_portraits.confirm(pending,filled,101)[1])==10


def test_missing_slot_rejects_low_confidence_duplicates_and_provider_failure():
    import json
    raw=(FIXTURES/'locked.jpg').read_bytes()
    result=bp_portraits.match(raw)
    result['slots'][-1].update(hero=None,similarity=.75)
    for name,confidence in [('嬴政',.7),('艾琳',.99),('不存在',1)]:
        response={'message':{'content':json.dumps({'slots':[{'hero':name,'confidence':confidence}]})}}
        with patch('gameplan.ai.integrations.post_json',return_value=response):
            assert bp_portraits.supplement_missing(raw,result)['slots'][-1]['hero'] is None
    with patch('gameplan.ai.integrations.post_json',side_effect=TimeoutError):
        assert bp_portraits.supplement_missing(raw,result)==result


def test_complete_and_empty_bp_slots_never_call_supplement_model():
    with patch('gameplan.ai.integrations.post_json',side_effect=AssertionError('No model call')) as call:
        for name in ['locked.jpg','sparse-enemy-preview.png']:
            raw=(FIXTURES/name).read_bytes()
            result=bp_portraits.match(raw,allow_sparse=True)
            assert bp_portraits.supplement_missing(raw,result)==result
        call.assert_not_called()
