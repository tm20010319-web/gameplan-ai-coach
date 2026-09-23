"""Missing labels must affect one identity, not the complete match."""
import json
from copy import deepcopy
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from unittest.mock import patch

from gameplan.ai.advisor import KNOWN_HEROES
from gameplan.vision.loading_evidence import read_loading, loading_slots, LoadingMemory
from gameplan.skills.combat_tracks import advance
from gameplan.vision.hero_recognition import region_vote


FIXTURES = Path(__file__).parent / 'fixtures' / 'combat'


def test_loading_keeps_other_cards_when_an_entire_name_row_is_unreadable():
    picture = Image.open(FIXTURES / 'loading.jpg')
    readings = json.loads((FIXTURES / 'loading-ocr.json').read_text(encoding='utf8'))
    readings = [{**r, 'box': np.asarray(r['box'])} for r in readings
                if np.asarray(r['box'])[:, 1].mean() < 288]
    result = read_loading(picture, readings, KNOWN_HEROES)
    assert result is not None, 'One unreadable row discarded the whole loading screen'
    assert len(result['cards']) == 10


def loading_frame(hero='妲己', name='测试玩家', row=None):
    cards = [{**s, 'hero': hero if s['slot'] == 1 else None, 'nickname': name if s['slot'] == 1 else None,
              'hero_confidence': .91, 'nickname_confidence': .87, 'gold': 0.0} for s in loading_slots((1280, 576))]
    return {'phase': 'loading', 'cards': cards, 'own_row_candidate': row, 'player_slot': None}


def test_three_of_five_votes_survive_gaps_without_confirming_other_slots():
    memory = LoadingMemory()
    for stamp, hero in enumerate(['妲己', None, '妲己', None, '妲己']):
        result = memory.observe(loading_frame(hero, None), stamp)
        if stamp < 4:
            assert result['all_slots'] == [None]*10
    assert result['all_slots'] == ['妲己']+[None]*9
    assert result['bindings'] == result['enemy_roster'] == []
    assert result['side_status'] == 'unknown'
    for stamp in range(5, 11):
        result = memory.observe(loading_frame(None, None), stamp)
    assert result['all_slots'][0] == '妲己'


def test_duplicate_and_out_of_order_timestamps_do_not_supply_votes():
    memory = LoadingMemory()
    for stamp in [10, 10, 9, 10, 11]:
        assert memory.observe(loading_frame(), stamp)['all_slots'][0] is None
    assert memory.observe(loading_frame(), 12)['all_slots'][0] == '妲己'


def test_side_is_independent_and_can_resolve_after_loading_from_stable_ally_evidence():
    memory = LoadingMemory()
    for stamp in range(3):
        result = memory.observe(loading_frame(), stamp)
    assert result['bindings'][0]['side'] == 'unknown'
    for stamp in range(3, 6):
        result = memory.observe({'phase': 'in_game'}, stamp, allies=['妲己'])
        assert result['side_known'] == (stamp == 5)
    assert result['ally_roster'] == ['妲己']
    assert result['bindings'][0]['side'] == 'ally_roster'
    assert memory.observe(None, 6)['ally_roster'] == ['妲己']


def test_side_requires_three_consecutive_consistent_observations():
    memory = LoadingMemory()
    for stamp, row in enumerate([0, 1, 0, None, 0, 0]):
        assert not memory.observe(loading_frame(row=row), stamp)['side_known']
    assert memory.observe(loading_frame(row=0), 6)['side_known']


def test_high_score_single_variant_or_repeated_same_variant_is_not_confirmation():
    reading = {'text': '妲己', 'score': .999, 'variant': 0}
    assert region_vote([reading]*4, lambda s: s)[0] is None
    assert region_vote([reading, {**reading, 'variant': 1}], lambda s: s)[0] == '妲己'


BINDING = {'hero': '妲己', 'nickname': '测试玩家', 'side': 'enemy_roster'}


def combat_frame(x=700, second=None):
    picture = Image.new('RGB', (1280, 576), (25, 25, 25))
    draw = ImageDraw.Draw(picture)
    draw.rectangle((x, 200, x+95, 206), fill='red')
    if second:
        draw.rectangle((second, 225, second+95, 231), fill='red')
    target = {**BINDING, 'x': (x+48)/1280, 'y': 180/576, 'box': [x, 170, x+96, 190],
              'bar': [x, 200, 96, 7], 'source': 'ocr'}
    return picture, target


def step(memory, stamp, *, x=700, visible=False, second=None):
    picture, target = combat_frame(x, second)
    return advance(picture, {'panel': False, 'targets': [target] if visible else []}, stamp,
                   bindings=[BINDING], enemies=['妲己'], memory=memory)


def test_bar_track_keeps_identity_for_three_missing_frames_then_requires_exact_ocr():
    memory = {}
    assert step(memory, 100, visible=True)[0]['source'] == 'ocr'
    for index in range(1, 4):
        target = step(memory, 100+index*.3, x=700+index*15)[0]
        assert target['source'] == 'bar_track' and target['hero'] == '妲己'
        assert target['ocr_at'] == 100 and target['missing_frames'] == index
    assert step(memory, 101.2, x=760) == []
    assert step(memory, 101.3, x=775) == []
    assert step(memory, 101.4, x=790, visible=True)[0]['ocr_at'] == 101.4


def test_time_cap_is_independent_of_frame_count_and_flow_cannot_create_identity():
    memory = {}
    assert step(memory, 100) == []
    step(memory, 101, visible=True)
    assert step(memory, 102.6) == []


def test_overlapping_batches_reuse_history_without_renewing_anchor_or_spending_missing_frames():
    memory = {}
    step(memory, 100, visible=True)
    expected = step(memory, 100.3, x=715)
    assert step(memory, 100.3, x=715) == expected
    assert step(memory, 100.6, x=730)[0]['missing_frames'] == 2
    assert step(memory, 99) == []


def test_crossing_bars_or_changed_nickname_never_reassign_cached_identity():
    memory = {}
    step(memory, 100, visible=True)
    assert step(memory, 100.4, x=700, second=730) == []
    step(memory, 100.6, visible=True)
    picture, target = combat_frame()
    target['nickname'] = '测试玩冢'  # Similar OCR is not the original nickname.
    assert advance(picture, {'targets': [target]}, 101, bindings=[BINDING], enemies=['妲己'], memory=memory) == []


def test_blank_nickname_cannot_erase_hero_or_prevent_other_slot_confirmation():
    memory = LoadingMemory()
    for stamp in range(3):
        frame = loading_frame(name=None)
        frame['cards'][2].update(hero='吕布', nickname='另个玩家')
        result = memory.observe(frame, stamp)
    assert result['all_slots'][:3] == ['妲己', None, '吕布']
    assert [b['hero'] for b in result['bindings']] == ['吕布']
    assert memory.snapshot()['cards'][0]['nickname'] is None


def test_duplicate_hero_only_quarantines_affected_slots():
    memory = LoadingMemory()
    for stamp in range(3):
        frame = loading_frame()
        frame['cards'][2].update(hero='妲己')
        frame['cards'][4].update(hero='吕布')
        result = memory.observe(frame, stamp)
    assert result['all_slots'][:5] == [None, None, None, None, '吕布']


def test_nickname_plus_bar_needs_a_third_independent_cue():
    picture, target = combat_frame()
    target['evidence'] = {'bar': True, 'nickname': True, 'level_circle': False, 'continuity': False}
    observation = {'targets': [], 'name_candidates': [target]}
    memory = {}
    assert advance(picture, observation, 100, bindings=[BINDING], enemies=['妲己'], memory=memory) == []
    found = advance(picture, observation, 100.3, bindings=[BINDING], enemies=['妲己'], memory=memory)
    assert found[0]['evidence']['continuity']


def test_airdroid_chrome_is_removed_without_changing_the_game_content():
    from gameplan.vision.game_view import game_view
    game = Image.open(FIXTURES/'loading.jpg').convert('RGB')
    window = Image.new('RGB', (game.width+44, game.height+30), 'white')
    draw = ImageDraw.Draw(window)
    for x in range(8):
        shade = 190+x*6
        draw.line((x, 30, x, window.height-1), fill=(shade, shade, shade))
    window.paste(game, (44, 30))
    cropped = game_view(window)
    assert cropped.size == game.size
    assert np.array_equal(np.asarray(cropped), np.asarray(game))


def test_name_fifty_pixels_above_bar_is_found_but_disagreeing_crops_are_rejected():
    from gameplan.vision.health_nameplates import read_enemy_bars
    picture, _ = combat_frame()
    found = {'text': '测试玩家', 'score': .98,
             'box': np.array([[35, 10], [95, 10], [95, 24], [35, 24]])}
    good = [{'text': '测试玩家', 'score': .94, 'variant': i} for i in range(4)]
    wrong = [{'text': '测试玩冢', 'score': .99, 'variant': i} for i in range(4)]
    with patch('gameplan.vision.health_nameplates.level_circle', return_value=True), \
         patch('gameplan.vision.health_nameplates.bar_level', return_value=None), \
         patch('gameplan.vision.health_nameplates.read_text', return_value=[found]) as discover, \
         patch('gameplan.vision.health_nameplates.read_regions', return_value=[good, good]) as reread:
        result = read_enemy_bars(picture, [BINDING])
        assert result['targets'][0]['hero'] == '妲己'
        assert result['targets'][0]['box'][1] == 150
        assert discover.call_args.args[0].height == 58
        reread.return_value = [good, wrong]
        assert read_enemy_bars(picture, [BINDING])['targets'] == []


def test_api_saves_partial_unknown_side_roster_through_gameplay_and_clears_on_reset():
    import base64
    import time
    from fastapi.testclient import TestClient
    from gameplan.web.monitor_app import app
    from gameplan.monitoring.monitor_runtime import reset_match, trackers
    from gameplan.vision.loading_evidence import slot_scene
    match = 'partial-loading-persistence'
    reset_match(match)
    observed = loading_frame(name=None)
    observed.update(panel=False, targets=[], hero_levels=[], equipment=[], readings=[])
    observed = slot_scene(observed, observed['cards'], None)
    payload = {'match_id': match, 'focus': 'skills', 'input_kind': 'video',
               'image_base64': base64.b64encode((FIXTURES/'loading.jpg').read_bytes()).decode()}
    stamp = time.time()
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as client, \
         patch('gameplan.skills.combat_evidence.read_scene', return_value=observed) as reader, \
         patch('gameplan.skills.combat_evidence.read_scenes', side_effect=lambda *a, **k: [deepcopy(observed)]), \
         patch('gameplan.monitoring.monitor_runtime.vision', side_effect=AssertionError('No model identity guesses')):
        for index in range(3):
            response = client.post('/api/vision/observe', json={**payload, 'captured_at': stamp+index*.3})
            assert response.status_code == 200, response.text
        result = response.json()
        assert result['loading_slots'][0]['hero'] == '妲己'
        assert len(result['loading_slots']) == 10 and result['side_status'] == 'unknown'
        assert result['observation']['enemy_roster'] == []
        reader.return_value = {'panel': False, 'phase': 'in_game', 'bindings': [], 'targets': [],
                               'ally_roster': [], 'enemy_roster': [], 'hero_levels': [], 'readings': []}
        response = client.post('/api/vision/observe', json={**payload, 'captured_at': stamp+1})
        assert response.status_code == 200, response.text
        assert response.json()['loading_slots'][0]['hero'] == '妲己'
        assert trackers[match].loading_memory.cards[0]['hero'] == '妲己'
    reset_match(match)
    assert match not in trackers


def test_live_skill_detector_receives_cached_identity_on_missing_name_frames():
    import base64
    import io
    from gameplan.core.models import VisionRequest
    from gameplan.skills.keyframe_casts import detect
    memory = {}
    with patch('gameplan.ai.integrations.post_json', return_value={
            'done_reason': 'stop', 'message': {'content': '{"events":[]}'}}) as model:
        for index in range(5):
            picture, target = combat_frame(700+index*10)
            buffer = io.BytesIO(); picture.save(buffer, 'PNG')
            req = VisionRequest(image_base64=base64.b64encode(buffer.getvalue()).decode(),
                                captured_at=100+index*.3, focus='skills')
            scene = {'phase': 'in_game', 'panel': False, 'targets': [target] if index == 0 else [],
                     'hero_levels': [], 'readings': [], 'bindings': []}
            result = detect(req, b'', scene, bindings=[BINDING], enemies=['妲己'],
                            known_heroes=KNOWN_HEROES, track_memory=memory)
            assert result['visible_enemy_actors'] == (1 if index < 4 else 0)
            assert result['enemy_skill_events'] == []
        assert model.call_count == 8  # Context plus one bounded detail review per visible frame.
