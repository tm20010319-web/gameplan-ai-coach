"""OCR-free candidate tracking contracts; synthetic effects do not test model accuracy."""
import base64
import io
import json
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image, ImageDraw

from gameplan.core.models import VisionRequest
from gameplan.skills.keyframe_casts import detect
from gameplan.skills.auto_skill_monitor import EventTracker
from test_keyframe_casts import scene, candidate


def picture(x=700, *, color='red', other=None, bare=False):
    image=Image.new('RGB',(1280,576),(90,100,80))
    draw=ImageDraw.Draw(image)
    for bx in [x]+([] if other is None else [other]):
        if not bare:draw.rectangle((bx-2,196,bx+100,210),fill=(15,15,15))
        draw.rectangle((bx,200,bx+95,206),fill=color)
        # A reproducible textured body region, moving with the bar.
        rng=np.random.default_rng(5)
        body=Image.fromarray(rng.integers(20,230,(88,70,3),dtype=np.uint8))
        image.paste(body,(bx+12,220))
    return image


def encode(image):
    out=io.BytesIO();image.save(out,format='PNG')
    return base64.b64encode(out.getvalue()).decode()


def run_frames(pictures, *, times=None, answer=None, observations=None, memory=None, bindings=()):
    times=times or [100+i*.3 for i in range(len(pictures))]
    samples=[{'image_base64':encode(p),'captured_at':t} for p,t in zip(pictures,times)]
    req=VisionRequest(**samples[-1],recent_frames=samples[:-1],focus='skills')
    observations=observations or [scene() for _ in pictures]
    reply={'events':[candidate()] if answer is None else answer}
    with patch('gameplan.skills.combat_evidence.read_scene',side_effect=observations[:-1]), \
         patch('gameplan.ai.integrations.post_json',return_value={
             'done_reason':'stop','message':{'content':json.dumps(reply)}}) as post:
        result=detect(req,b'',observations[-1],bindings=bindings,enemies=['铠'],
                      known_heroes={'铠'},track_memory=memory)
    return result,post


def test_three_ocr_empty_frames_review_first_effect_frame_and_start_tentative_timer():
    result,post=run_frames([picture(700),picture(712),picture(724)])
    assert post.call_count==1
    assert result['frames_checked']==3  # Earlier effect must not be sampled away.
    assert result['enemy_skill_events']==[]
    event=result['suspicions'][0]
    assert event['frame_index']==0 and event['reason']=='visual_identity_unconfirmed'
    tracker=EventTracker()
    tracker.ingest_suspicions([event],enemies=['铠'],allies=[],frame_times=[100,100.3,100.6],now=100.6)
    assert tracker.estimate_snapshot(101)[0]['remaining_range_s']==[39,49]
    assert tracker.events==[] and tracker.levels=={}


@pytest.mark.parametrize('frames',[
    [picture(700)], [picture(700),picture(712)], [picture(700)]*3,
    [picture(700),picture(710,bare=True),picture(720)],
    [picture(700,color='deepskyblue'),picture(712,color='deepskyblue'),picture(724,color='deepskyblue')],
])
def test_one_frame_duplicates_missing_borders_and_friendly_bars_do_not_create_tracks(frames):
    result,post=run_frames(frames)
    assert not post.called and not result.get('suspicions')


def test_position_continuity_alone_never_creates_cast_when_model_sees_no_effect():
    result,post=run_frames([picture(700),picture(712),picture(724)],answer=[])
    assert post.called
    assert not result['enemy_skill_events'] and not result.get('suspicions')


def test_crossing_ambiguous_targets_and_large_time_gaps_break_continuity():
    frames=[picture(700),picture(712,other=745),picture(724)]
    assert not run_frames(frames)[1].called
    assert not run_frames([picture(700),picture(712),picture(724)],times=[100,102,104])[1].called


def test_panel_or_unverified_scene_interrupts_anonymous_continuity():
    for middle in [None,{**scene(),'panel':True}]:
        result,post=run_frames([picture(700),picture(712),picture(724)],observations=[scene(),middle,scene()])
        assert not post.called and not result.get('suspicions')


def test_real_friendly_picture_stays_negative_even_with_all_name_and_level_ocr_removed():
    from pathlib import Path
    source=Image.open(Path(__file__).parent/'fixtures/combat/attribution/allies-only-1015.png')
    frames=[source.transform(source.size,Image.Transform.AFFINE,(1,0,-i*2,0,1,0)) for i in range(3)]
    assert not run_frames(frames)[1].called


def test_real_enemy_bar_survives_removed_ocr_across_translated_frames():
    from pathlib import Path
    source=Image.open(Path(__file__).parent/'fixtures/combat/attribution/sunce-868.png')
    frames=[source.transform(source.size,Image.Transform.AFFINE,(1,0,-i*2,0,1,0)) for i in range(3)]
    # Translation validates geometry/appearance only, not actual temporal recall.
    result,post=run_frames(frames,answer=[])
    assert post.called and result['frames_checked']==3


def test_anonymous_continuity_survives_requests_without_creating_named_anchors():
    memory={}
    for i in range(3):
        result,post=run_frames([picture(700+i*12)],times=[100+i*.3],memory=memory)
        assert post.called is (i==2)
        assert memory['last']['targets']==[]  # Named OCR/flow history is untouched.
    assert result['suspicions'][0]['reason']=='visual_identity_unconfirmed'


def test_replayed_frames_do_not_count_again_or_renew_anonymous_history():
    memory={}
    first=[picture(700),picture(712)]
    assert not run_frames(first,memory=memory)[1].called
    assert not run_frames(first,memory=memory)[1].called
    assert not run_frames([picture(710)],times=[99],memory=memory)[1].called
    assert run_frames([*first,picture(724)],memory=memory)[1].called


def test_body_appearance_change_and_known_ally_name_break_candidate_sequence():
    changed=picture(712)
    ImageDraw.Draw(changed).rectangle((715,218,815,319),fill='white')
    assert not run_frames([picture(700),changed,picture(724)])[1].called
    readings=[{'text':'友方昵称','score':.99,'box':np.array([[700,165],[796,165],[796,190],[700,190]])}]
    binding={'hero':'艾琳','nickname':'友方昵称','side':'ally_roster'}
    frames=[picture(700),picture(712),picture(724)]
    assert not run_frames(frames,observations=[{**scene(),'readings':readings}]*3,bindings=[binding])[1].called


def test_multiple_anonymous_targets_are_all_sent_for_review():
    frames=[picture(500+i*10,other=900+i*10) for i in range(3)]
    result,post=run_frames(frames,answer=[])
    assert post.called and result['visible_enemy_actors']==6


def test_ocr_level_reader_is_not_needed_for_anonymous_review():
    with patch('gameplan.vision.health_nameplates.bar_level',side_effect=AssertionError('No level OCR needed')):
        result,post=run_frames([picture(700),picture(712),picture(724)])
    assert post.called and result.get('suspicions')


def test_real_damaged_structure_does_not_become_an_unnamed_hero():
    from pathlib import Path
    root=Path(__file__).parent/'fixtures/combat/anonymous-structure'
    frames=[Image.open(root/f'F{i}.png') for i in (5,6,7)]
    result,post=run_frames(frames)
    assert not post.called and not result.get('suspicions')
