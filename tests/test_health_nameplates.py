"""Real top-edge enemy name/level regression, separate from cast accuracy."""
import base64
import json
import time
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from fastapi.testclient import TestClient

from gameplan.ai.advisor import KNOWN_HEROES
from gameplan.skills.auto_skill_monitor import EventTracker
from gameplan.skills.combat_evidence import read_scene
from gameplan.vision.health_nameplates import read_enemy_bars, bar_level
import gameplan.web.monitor_app as monitor_app
import gameplan.monitoring.monitor_runtime as monitor_runtime

ROOT=Path(__file__).parent/'fixtures/combat/healthbar-edge'
BINDING={'hero':'李信','nickname':'改名解毒','side':'enemy_roster'}


def scene(bindings=None, missing_recovery=False):
    readings=json.loads((ROOT/'ocr.json').read_text(encoding='utf8'))
    if missing_recovery:
        readings=[r for r in readings if r['text']!='恢复']
    return read_scene((ROOT/'lixin-10.png').read_bytes(),KNOWN_HEROES,
                      [BINDING] if bindings is None else bindings,readings=readings)


def test_real_blue_circle_and_top_edge_name_recover_the_bound_enemy_at_level_ten():
    result=scene()
    assert [(t['hero'],t['nickname']) for t in result['targets']]==[('李信','改名解毒')]
    assert result['targets'][0]['y']<65/576  # old unconditional exclusion
    assert [(r['hero'],r['level']) for r in result['hero_levels']]==[('李信',10)]
    assert .92<=result['hero_levels'][0]['confidence']<1


def test_same_pixels_without_binding_or_with_ally_identity_cannot_unlock_an_enemy():
    for bindings in ([],[{**BINDING,'side':'ally_roster'}],
                     [BINDING,{**BINDING,'hero':'瑶'}]):
        result=scene(bindings)
        assert not result['targets'] and not result['hero_levels']


def test_missing_recovery_label_accepts_independent_hud_but_never_invents_identity():
    assert scene(missing_recovery=True)['hero_levels'][0]['level']==10
    result=scene([],missing_recovery=True)
    assert result['targets']==result['hero_levels']==[]
    readings=json.loads((ROOT/'ocr.json').read_text(encoding='utf8'))
    readings=[r for r in readings if r['text']!='恢复' and not r['text'].startswith('FPS')]
    assert read_scene((ROOT/'lixin-10.png').read_bytes(),KNOWN_HEROES,[],readings=readings) is None


def test_blank_or_disagreeing_level_glyph_remains_unknown():
    assert bar_level(Image.new('RGB',(1280,576),'navy'),730,36) is None
    with patch('gameplan.vision.health_nameplates.ocr_engine') as engine:
        engine.return_value.side_effect=[([['10',.99]],None),([['1',.99]],None)]
        assert bar_level(Image.open(ROOT/'lixin-10.png'),730,36) is None


def test_chat_name_without_its_health_bar_never_becomes_a_target():
    picture=Image.open(ROOT/'lixin-10.png').convert('L').convert('RGB')
    assert read_enemy_bars(picture,[BINDING])=={'targets':[],'hero_levels':[]}


def test_level_unlock_persists_without_inventing_casts_and_allows_later_grounded_candidates():
    tracker=EventTracker();levels=scene()['hero_levels']
    assert tracker.ingest([],100,enemies=['李信','瑶'],allies=[],frame_times=[100],hero_levels=levels)==[]
    assert tracker.ultimate_states(['李信'],130)[0]['level']==10
    assert tracker.ultimate_states(['瑶'],130)[0]['status']=='level_unknown'
    # Explicitly synthetic cast evidence checks gating only, not real release detection.
    event={'hero':'李信','skill':'大招','used':True,'event_type':'cast_start','frame_index':1,
           'confidence':.96,'evidence':'测试提供的前后形态变化'}
    assert len(tracker.ingest([event],102,enemies=['李信'],allies=[],frame_times=[101,102]))==1


def test_loading_to_real_gameplay_api_updates_only_the_visible_enemy_level():
    match='healthbar-edge-api';monitor_runtime.reset_match(match);now=time.time()
    with TestClient(monitor_app.app,base_url='http://127.0.0.1',client=('127.0.0.1',50000)) as client, \
         patch('gameplan.ai.integrations.post_json',return_value={'done_reason':'stop','message':{'content':'{"events":[]}'}}), \
         patch('gameplan.monitoring.monitor_runtime.vision',side_effect=AssertionError('Local evidence must not need model guesses')):
        for path,stamp in [(ROOT.parent/'loading-new/206.5.png',now-3),
                           (ROOT.parent/'loading-new/207.png',now-2.5),
                           (ROOT.parent/'loading-new/207.5.png',now-2),(ROOT/'lixin-10.png',now)]:
            response=client.post('/api/vision/observe',json={'match_id':match,'focus':'skills','input_kind':'video',
                'image_base64':base64.b64encode(path.read_bytes()).decode(),'captured_at':stamp})
            assert response.status_code==200,response.text
        result=response.json()
    states={r['hero']:r for r in result['enemy_ultimate_states']}
    assert states['李信']['status']=='unlocked' and states['李信']['level']==10
    assert all(s['status']=='level_unknown' for hero,s in states.items() if hero!='李信')
    assert result['enemy_skill_timers']==result['enemy_skill_updates']==[]
    assert result['skill_scan']['target_latency_s']==20
    monitor_runtime.reset_match(match)
