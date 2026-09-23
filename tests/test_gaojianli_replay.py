"""Regression for real nameplates; mocked cast responses test parsing only."""
import json
from pathlib import Path
import pytest
from gameplan.ai.advisor import KNOWN_HEROES
from gameplan.skills.combat_evidence import read_scene
from gameplan.skills.grounded_casts import frame_index
from test_realtime_evidence import sequence_response, scene, event

ROOT=Path(__file__).parent/'fixtures/combat/gaojianli-onset'
BINDING={'hero':'高渐离','nickname':'欲盖弥彰、','side':'enemy_roster'}

@pytest.mark.parametrize('stamp',['422.80','423.00'])
def test_real_precast_identity_survives_obscured_recovery_and_full_red_bar(stamp):
    pixels=(ROOT/f'{stamp}.png').read_bytes()
    readings=json.loads((ROOT/f'{stamp}-ocr.json').read_text(encoding='utf8'))
    assert '恢复' not in [r['text'] for r in readings]
    result=read_scene(pixels,KNOWN_HEROES,[BINDING],readings=readings)
    assert any(t['hero']=='高渐离' for t in result['targets'])
    unbound = read_scene(pixels,KNOWN_HEROES,[],readings=readings)
    assert not unbound or not unbound['targets']  # HUD can prove gameplay without proving identity.

@pytest.mark.parametrize('value,expected',[('F2',2),('2',2),(2,2),('F0',0),('F9',None),(-1,None),(True,None),(2.5,None),('frame 2',None)])
def test_printed_frame_labels_are_normalized_without_guessing(value,expected):
    assert frame_index(value,8)==expected

def test_labeled_frame_generates_same_cast_as_integer_and_keeps_identity_gate():
    for index in (1,'F1','1'):
        result,_=sequence_response(event(index=index),[scene('孙策')]*3)
        assert len(result['enemy_skill_events'])==1
        assert result['enemy_skill_events'][0]['frame_index']==1
    result,_=sequence_response(event(index='F1'),[scene(),scene('孙策'),scene('孙策')])
    assert not result['enemy_skill_events']

def test_first_frame_effect_cannot_become_new_cast_or_cooldown():
    result,_=sequence_response(event(index='F0'),[scene('孙策')]*3)
    assert not result['enemy_skill_events']
    assert result['activity'][0]['status']=='possible_ongoing'
    assert result['rejected_candidates']['onset_before_sequence']==1


def test_large_area_rejects_enemy_foot_ring_and_requires_the_real_field():
    from PIL import Image,ImageDraw
    from gameplan.skills.field_effects import large_enemy_field
    target={'x':.5,'y':.25}
    image=Image.new('RGB',(1280,576),'black');draw=ImageDraw.Draw(image)
    draw.ellipse((610,220,670,265),outline='red',width=3)
    assert not large_enemy_field(image,target)
    draw.ellipse((480,150,800,355),outline='red',width=3)
    assert large_enemy_field(image,target)
    actual=Image.open(ROOT/'423.40.png')
    # This real frame has a small enemy foot ring, not the large red boundary
    # required by this geometric predicate. It must stay a tentative candidate;
    # the previous positive expectation already failed before these changes.
    assert not large_enemy_field(actual,{'x':.546,'y':.116})
