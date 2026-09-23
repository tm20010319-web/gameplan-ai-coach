import base64
import json
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pytest
from gameplan.core.models import VisionRequest
from gameplan.skills.visual_suspicions import detect
from gameplan.skills.auto_skill_monitor import EventTracker

ROOT=Path(__file__).parent/'fixtures/combat/sunce-warning'
BINDINGS=[{'hero':'孙策','nickname':'无阙焕','side':'enemy_roster'}]

def request():
    image=base64.b64encode((ROOT/'268.png').read_bytes()).decode()
    return VisionRequest(match_id='visual-warning',input_kind='video',focus='skills',video_time_s=268,
                         captured_at=100,image_base64=image,recent_frames=[{'captured_at':99.8,'image_base64':image}])

def readings():
    return [{**i,'box':np.asarray(i['box'])} for i in json.loads((ROOT/'268-ocr.json').read_text(encoding='utf-8'))]

@pytest.mark.parametrize('overrides,accepted',[
    ({},True),({'hero':'吕布'},False),({'enemy_visible':False},False),
    ({'state':'none'},False),({'confidence':.8},False),({'frame_index':12},False),
    ({'evidence':'技能按钮发亮'},False),
])
def test_nickname_hint_can_only_emit_tentative_visible_enemy_warning(overrides,accepted):
    event={'hero':'孙策','state':'ongoing','enemy_visible':True,'frame_index':1,'confidence':.9,
           'evidence':'敌方红色血条下方角色驾驶红白战船',**overrides}
    with patch('gameplan.vision.hero_recognition.read_text',return_value=readings()), patch(
        'gameplan.ai.integrations.post_json',return_value={'done_reason':'stop','message':{'content':json.dumps({'events':[event]})}}):
        result=detect(request(),['孙策','吕布'],bindings=BINDINGS)
    assert bool(result['suspicions']) is accepted
    assert not result.get('enemy_skill_events')
    tracker=EventTracker()
    tracker.ingest_suspicions(result['suspicions'],enemies=['孙策'],allies=[],frame_times=[99.8,100],now=100)
    assert bool(tracker.suspicion_snapshot(100)) is accepted
    assert tracker.events==[]

def test_no_identity_or_ambiguous_nickname_does_not_guess_a_roster_hero():
    for bindings in ([],BINDINGS+[{'hero':'吕布','nickname':'无闯焕','side':'enemy_roster'}]):
        with patch('gameplan.vision.hero_recognition.read_text',return_value=readings()), patch('gameplan.ai.integrations.post_json') as post:
            result=detect(request(),['孙策','吕布'],bindings=bindings)
        assert not result['suspicions']
        post.assert_not_called()


def test_visual_review_reuses_exact_image_ocr_without_repeating_text_recognition():
    import hashlib
    pixels = (ROOT/'268.png').read_bytes()
    cache = {(hashlib.sha256(pixels).digest(), ()): {'readings': readings()}}
    event = {'hero': '孙策', 'state': 'ongoing', 'enemy_visible': True, 'frame_index': 1,
             'confidence': .9, 'evidence': '敌方红色血条下方角色驾驶红白战船'}
    with patch('gameplan.vision.hero_recognition.read_text', side_effect=AssertionError('duplicate OCR')), \
         patch('gameplan.ai.integrations.post_json', return_value={
             'done_reason': 'stop', 'message': {'content': json.dumps({'events': [event]})}}):
        result = detect(request(), ['孙策', '吕布'], bindings=BINDINGS, scene_cache=cache)
    assert result['suspicions'][0]['hero'] == '孙策'
    assert not result.get('enemy_skill_events')
