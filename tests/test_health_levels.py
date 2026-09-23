"""Real glyph crop plus explicitly synthetic hero binding; no hero inferred from it."""
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from gameplan.ai.advisor import KNOWN_HEROES
from gameplan.skills.auto_skill_monitor import EventTracker
from gameplan.skills.combat_evidence import read_scene
from gameplan.vision.health_levels import badge_number

FIXTURE=Path(__file__).parent/'fixtures/combat/level-11-crop.png'
NAME_BOX=np.array([[129.6,24.6],[161.3,24.6],[161.3,32.6],[129.6,32.6]])


def test_user_circled_eleven_is_read_without_full_frame_digit_detection():
    reading=badge_number(Image.open(FIXTURE),NAME_BOX)
    assert reading['level']==11 and reading['visible_text']=='11'
    assert .92<=reading['confidence']<1


def test_number_without_the_circular_badge_is_not_treated_as_a_hero_level():
    image=Image.open(FIXTURE).convert('L').convert('RGB')
    assert badge_number(image,NAME_BOX) is None


def test_two_possible_badges_or_conflicting_ocr_stay_unknown():
    image=Image.open(FIXTURE).convert('RGB')
    image.paste(image.crop((99,30,117,50)),(69,30))
    assert badge_number(image,NAME_BOX) is None
    answers=[([['1',.98]],None),([['11',.98]],None)]
    with patch('gameplan.vision.health_levels.ocr_engine') as engine:
        engine.return_value.side_effect=answers
        assert badge_number(Image.open(FIXTURE),NAME_BOX) is None


def synthetic_scene(bound=True):
    import io
    picture=Image.new('RGB',(1280,576),'navy')
    picture.paste(Image.open(FIXTURE),(400,200))
    buffer=io.BytesIO();picture.save(buffer,'PNG')
    def text(value,x,y,w=30,h=12):
        return {'text':value,'score':.99,'box':[[x,y],[x+w,y],[x+w,y+h],[x,y+h]]}
    readings=[text('03:10',850,10),text('回城',660,520),text('恢复',740,520),
              {'text':'测试敌方昵称','score':.99,'box':(NAME_BOX+[400,200]).tolist()}]
    bindings=[{'hero':'铠','nickname':'测试敌方昵称','side':'enemy_roster'}] if bound else []
    return read_scene(buffer.getvalue(),KNOWN_HEROES,bindings,readings=readings)


def test_badge_fallback_is_bound_to_one_enemy_and_unlocks_without_a_cast():
    scene=synthetic_scene()
    # This fixture is a GREEN allied bar with an invented OCR nickname. A level
    # badge alone must no longer authorize an enemy identity or enemy level.
    assert scene['targets'] == scene['hero_levels'] == []
    reading = badge_number(Image.open(FIXTURE), NAME_BOX)
    verified_levels = [{'hero': '铠', **reading, 'frame_index': 0}]
    tracker=EventTracker()
    added=tracker.ingest([],100,enemies=['铠','瑶'],allies=['艾琳'],frame_times=[100],hero_levels=verified_levels)
    assert added==[] and tracker.snapshot(100)==[]
    states={r['hero']:r for r in tracker.ultimate_states(['铠','瑶'],100)}
    assert states['铠']['status']=='unlocked' and states['铠']['level']==11
    assert states['瑶']['status']=='level_unknown'
    assert synthetic_scene(bound=False)['hero_levels']==[]


def test_same_number_on_an_ally_never_unlocks_an_enemy():
    tracker=EventTracker();levels=[{**r,'hero':'艾琳'} for r in synthetic_scene()['hero_levels']]
    tracker.ingest([],100,enemies=['铠'],allies=['艾琳'],frame_times=[100],hero_levels=levels)
    assert tracker.ultimate_states(['铠'],100)[0]['status']=='level_unknown'
