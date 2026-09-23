"""New replay's real mixed equipment; no role-based labels or cast inference."""
import base64
import io
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from gameplan.ai.advisor import KNOWN_HEROES
from gameplan.skills.auto_skill_monitor import EventTracker
from gameplan.skills.combat_evidence import read_scene
from gameplan.vision.loading_spells import verify, verify_sequence
from gameplan.core.models import VisionRequest
import gameplan.web.monitor_app as monitor_app
import gameplan.monitoring.monitor_runtime as monitor_runtime

ROOT = Path(__file__).parent/'fixtures/combat/loading-new'
EXPECTED = {'李信':'传送', '百里玄策':'惩击', '高渐离':'狂暴', '虞姬':'闪现', '瑶':'治疗术'}


def scene():
    return read_scene((ROOT/'207.png').read_bytes(),KNOWN_HEROES,
                      readings=json.loads((ROOT/'207-ocr.json').read_text(encoding='utf8')))


def encoded(picture):
    buffer=io.BytesIO();picture.save(buffer,format='PNG')
    return base64.b64encode(buffer.getvalue()).decode()


def test_missing_nickname_keeps_the_readable_hero_card_and_its_own_equipment():
    result=scene()
    assert set(result['enemy_roster']) == set(EXPECTED)
    card=next(c for c in result['cards'] if c['hero']=='百里玄策')
    assert card['nickname'] is None
    assert not any(b['hero']=='百里玄策' for b in result['bindings'])
    spells=verify(Image.open(ROOT/'207.png'),result['cards'])
    assert {s['hero']:s['skill'] for s in spells if s['hero'] in EXPECTED} == EXPECTED
    for hero in ('李信','高渐离'):
        record=next(s for s in spells if s['hero']==hero)
        assert .80 <= record['confidence'] < .85  # preserve raw measured score
        assert record['smoothed_score'] >= .88


def test_weak_or_disagreeing_smoothed_match_cannot_promote_the_raw_candidate():
    card=scene()['cards'][:1]
    first={'skill':'传送','score':.81,'margin':.3,'color':.99}
    with patch('gameplan.vision.loading_spells.rank_icon',side_effect=[first,{'skill':'惩击','score':.99,'margin':.4,'color':.99}]):
        assert verify(Image.open(ROOT/'207.png'),card) == []
    tracker=EventTracker()
    for stamp in (100,101,102):
        tracker.update_summoners([{'hero':'李信','skill':'传送','confidence':.81,'template_margin':.3,
            'color_score':.99,'source':'loading_icon','frame_index':0,'evidence':'raw candidate only'}],
            enemies=['李信'],allies=[],frame_times=[stamp])
    assert tracker.summoner_states(['李信'])[0]['status']=='unknown'


def test_buffered_frames_cannot_borrow_a_different_cards_hero_identity():
    current=Image.open(ROOT/'207.png').convert('RGB');data=scene()
    card=next(c for c in data['cards'] if c['hero']=='虞姬')
    previous=current.copy()
    left,top,right,bottom=map(round,card['label_box'])
    # Swap the current target's hero text with another actual card's label.
    previous.paste(current.crop((left-160,top,right-160,bottom)),(left,top))
    req=VisionRequest(image_base64=encoded(current),captured_at=102,focus='skills',recent_frames=[
        {'image_base64':encoded(previous),'captured_at':100},
        {'image_base64':encoded(Image.new('RGB',current.size)),'captured_at':101}])
    observations=verify_sequence(req,current,[card])
    assert observations and {o['frame_index'] for o in observations}=={2}
    tracker=EventTracker();tracker.update_summoners(observations,enemies=['虞姬'],allies=[],frame_times=[100,101,102])
    assert tracker.summoner_states(['虞姬'])[0]['status']=='confirming'


def test_missing_intermediate_image_breaks_a_batch_confirmation_streak():
    record=next(r for r in verify(Image.open(ROOT/'207.png'),scene()['cards']) if r['hero']=='虞姬')
    tracker=EventTracker()
    tracker.update_summoners([{**record,'frame_index':i} for i in (0,2,3)],
        enemies=['虞姬'],allies=[],frame_times=[100,100.5,101,101.5])
    assert tracker.summoner_states(['虞姬'])==[
        {'hero':'虞姬','skill':None,'status':'confirming','confirmation_count':2}]


@pytest.mark.parametrize('focus',['skills','full','heroes'])
def test_real_loading_sequence_fills_all_five_equipment_states_through_api(focus):
    match='loading-spells-'+focus
    monitor_runtime.reset_match(match)
    now=time.time()
    frames=[]
    for i,s in enumerate(('206.5','207','207.5')):
        # Capture now uses PNG, preserving the small glyphs for local matching.
        buffer=io.BytesIO();Image.open(ROOT/f'{s}.png').save(buffer,format='PNG')
        frames.append({'image_base64':base64.b64encode(buffer.getvalue()).decode(),'captured_at':now-1+i*.5})
    with TestClient(monitor_app.app,base_url='http://127.0.0.1',client=('127.0.0.1',50000)) as client, \
         patch('gameplan.monitoring.monitor_runtime.vision',side_effect=AssertionError('Local loading evidence must not need model guesses')):
        response=client.post('/api/vision/observe',json={'match_id':match,'focus':focus,'input_kind':'video',
            **frames[-1],'recent_frames':frames[:-1]})
    assert response.status_code==200,response.text
    result=response.json()
    assert {s['hero']:s['skill'] for s in result['enemy_summoner_states']}==EXPECTED
    assert all(s['status']=='confirmed' for s in result['enemy_summoner_states'])
    assert result['observation']['phase']=='loading'
    assert result['loading_context']=={'side':'a','source':'local_name_highlight'}
    assert result['enemy_skill_updates']==result['enemy_skill_timers']==[]
    assert not monitor_runtime.trackers[match].events
    monitor_runtime.reset_match(match)
