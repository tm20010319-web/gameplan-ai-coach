"""A scoreboard following combat must not discard the preceding effect frames."""
import base64
import io
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from gameplan.skills.combat_evidence import read_scene
from gameplan.skills.keyframe_casts import detect
from gameplan.skills.auto_skill_monitor import EventTracker
from gameplan.ai.advisor import KNOWN_HEROES
from gameplan.core.models import VisionRequest
import gameplan.monitoring.monitor_runtime as runtime
from test_enemy_casts import client

ROOT = Path(__file__).parent / 'fixtures/combat/scoreboard-partial-tabs'


def test_user_scoreboard_is_gameplay_even_when_two_vertical_tabs_are_unreadable():
    pixels = (ROOT / 'panel.png').read_bytes()
    readings = json.loads((ROOT / 'ocr.json').read_text(encoding='utf8'))
    result = read_scene(pixels, KNOWN_HEROES, readings=readings)
    assert result is not None and result['panel']
    assert {'吕布', '澜', '墨子'} <= set(result['enemy_roster'])


def test_random_hero_words_and_kda_without_row_structure_are_not_a_scoreboard():
    pixels = (ROOT / 'panel.png').read_bytes()
    readings = json.loads((ROOT / 'ocr.json').read_text(encoding='utf8'))
    # Retain readable words/counts but move each KDA away from all hero rows.
    for item in readings:
        if '/' in item['text']:
            item['box'] = [[x, 20+y*0] for x,y in item['box']]
    result = read_scene(pixels, KNOWN_HEROES, readings=readings)
    assert result is None or not result['panel']


def encoded(color):
    out = io.BytesIO()
    Image.new('RGB', (1280,576), color).save(out, 'PNG')
    return base64.b64encode(out.getvalue()).decode()


def scene(panel=False):
    return {'panel':panel,'ally_roster':['艾琳'],'enemy_roster':['妲己'],
            'bindings':[],'targets':[],'hero_levels':[],'readings':[]}


def reply():
    return {'done_reason':'stop','message':{'content':json.dumps({'events':[
        {'actor_id':0,'hero':'妲己','kind':'ultimate','frame_index':0,'effect_visible':True,
         'enemy_visible':True,'identity_visible':True,'confidence':.96,
         'evidence':'敌方红血条下妲己发出连续狐火弹道'}]})}}


def test_latest_scoreboard_still_reviews_earlier_combat_at_original_frame_index():
    req=VisionRequest(image_base64=encoded('navy'),captured_at=101,focus='skills',
                      recent_frames=[{'image_base64':encoded('maroon'),'captured_at':100}])
    with patch('gameplan.skills.combat_evidence.read_scene',return_value=scene()), \
         patch('gameplan.skills.keyframe_casts.visible_actors',return_value=[{'hero':'妲己','x':.6,'y':.4}]) as locate, \
         patch('gameplan.ai.integrations.post_json',return_value=reply()) as model:
        result=detect(req,b'',scene(True),bindings=[],enemies=['妲己'],known_heroes=KNOWN_HEROES)
    assert locate.call_count==1  # No portrait/icon on the scoreboard becomes an actor.
    assert model.call_count==1
    assert result['enemy_skill_events'][0]['frame_index']==0


def test_scoreboard_only_history_never_turns_portraits_into_skill_casts():
    req=VisionRequest(image_base64=encoded('navy'),captured_at=101,focus='skills',
                      recent_frames=[{'image_base64':encoded('maroon'),'captured_at':100}])
    with patch('gameplan.skills.combat_evidence.read_scene',return_value=scene(True)), \
         patch('gameplan.skills.keyframe_casts.visible_actors') as locate, \
         patch('gameplan.ai.integrations.post_json') as model:
        result=detect(req,b'',scene(True),bindings=[],enemies=['妲己'],known_heroes=KNOWN_HEROES)
    assert not result['enemy_skill_events']
    locate.assert_not_called();model.assert_not_called()


def test_real_observe_route_does_not_discard_recent_cast_when_panel_opens(client):
    now=time.time()
    tracker=EventTracker();tracker.phase='in_game';tracker.roster_verified=True
    tracker.allies=['艾琳'];tracker.enemies=['妲己'];tracker.last_seen=now
    runtime.trackers['panel-following-cast']=tracker
    with patch('gameplan.skills.combat_evidence.read_scenes',return_value=[scene(),scene(True)]), \
         patch('gameplan.skills.combat_evidence.read_scene',return_value=scene(True)), \
         patch('gameplan.vision.summoner_icons.read_equipment',return_value=[]), \
         patch('gameplan.skills.keyframe_casts.detect',return_value={
             'enemy_skill_events':[{'hero':'妲己','skill':'大招','used':True,'event_type':'keyframe',
                 'frame_index':0,'confidence':.96,'evidence':'敌方妲己向对手发出狐火特效'}],
             'hero_levels':[],'activity':[],'status':'observed','detection_policy':'single_frame_effect'}) as check:
        response=client.post('/api/vision/observe',json={'match_id':'panel-following-cast',
            'input_kind':'live','focus':'skills','image_base64':encoded('navy'),'captured_at':now,
            'recent_frames':[{'image_base64':encoded('maroon'),'captured_at':now-.5}]})
    assert response.status_code==200,response.text
    check.assert_called_once()
    data=response.json()
    assert data['enemy_skill_timers'][0]['hero']=='妲己'
    assert data['enemy_skill_timers'][0]['captured_at']==now-.5
