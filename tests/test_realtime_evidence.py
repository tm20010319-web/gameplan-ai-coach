"""Identity/temporal safeguards. These checks do not measure live model accuracy."""
import base64
import json
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image
import pytest

from gameplan.ai.advisor import KNOWN_HEROES
from gameplan.skills.auto_skill_monitor import EventTracker
from gameplan.skills.combat_evidence import read_scene
from gameplan.skills.combat_tracks import bridge
from gameplan.skills.flash_motion import displaced
from gameplan.vision.loading_evidence import read_loading
from gameplan.vision.loading_spells import verify
from gameplan.core.models import VisionRequest
from gameplan.skills.temporal_casts import detect_sequence

FIXTURES = Path(__file__).parent / 'fixtures' / 'combat'


def test_untargetable_cast_and_cooldown_labels_do_not_disable_gameplay_detection():
    pixels = (FIXTURES/'untargetable/aoyin.png').read_bytes()
    readings = json.loads((FIXTURES/'untargetable/aoyin-ocr.json').read_text(encoding='utf8'))
    scene = read_scene(pixels, KNOWN_HEROES, readings=readings)
    assert scene is not None and not scene['panel']
    assert scene['targets'] == []  # HUD proves gameplay, never an enemy's identity.
    without_performance = [r for r in readings if not r['text'].startswith('FPS')]
    assert read_scene(pixels, KNOWN_HEROES, readings=without_performance) is None


def loading():
    pixels=(FIXTURES/'loading.jpg').read_bytes()
    readings=json.loads((FIXTURES/'loading-ocr.json').read_text(encoding='utf8'))
    return read_scene(pixels,KNOWN_HEROES,readings=readings)


def test_loading_enemy_side_comes_from_unique_local_highlight():
    scene=loading()
    assert scene['side_known'] and scene['player_hero']=='艾琳'
    assert scene['enemy_roster']==['吕布','孙策','嬴政','敖隐','瑶']
    assert next(b for b in scene['bindings'] if b['hero']=='孙策')['nickname']=='无阙焕'
    blank=Image.open(FIXTURES/'loading.jpg').convert('L').convert('RGB')
    unknown=read_loading(blank,scene['readings'],KNOWN_HEROES)
    assert not unknown['side_known'] and not unknown['enemy_roster']
    assert all(b['side']=='unknown' for b in unknown['bindings'])


def test_loading_layout_survives_missing_row_text_but_random_words_are_not_a_layout():
    scene=loading()
    one_row=[r for r in scene['readings'] if r['box'][:,1].mean()<288]
    assert len(read_loading(Image.open(FIXTURES/'loading.jpg'),one_row,KNOWN_HEROES)['cards']) == 10
    assert read_loading(Image.new('RGB',(1280,576)),one_row,KNOWN_HEROES) is None


def test_sunce_short_nickname_does_not_hide_readable_level_13():
    readings=json.loads((FIXTURES/'sunce-ongoing-ocr.json').read_text(encoding='utf8'))
    with patch('gameplan.skills.combat_evidence.read_text',return_value=[{'text':'无阙焕','score':.91}]):
        result=read_scene((FIXTURES/'sunce-ongoing.jpg').read_bytes(),KNOWN_HEROES,loading()['bindings'],readings=readings)
    assert {v['hero']:v['level'] for v in result['hero_levels']}=={'孙策':13}
    assert [t['hero'] for t in result['targets']]==['孙策']


def equipment(skill='闪现',**changes):
    return {'hero':'敖隐','skill':skill,'confidence':.87,'template_margin':.3,'color_score':.99,
            'source':'loading_icon','frame_index':0,'evidence':'中心图标形状与颜色比对',**changes}


def observe_equipment(tracker,stamp,items):
    tracker.update_summoners(items,enemies=['敖隐'],allies=['艾琳'],frame_times=[stamp])


def test_loading_spell_requires_three_distinct_consistent_observations():
    tracker=EventTracker()
    for stamp in (100,100,101):
        observe_equipment(tracker,stamp,[equipment()])
        assert not tracker.summoner_for('敖隐')
    observe_equipment(tracker,102,[equipment()])
    assert tracker.summoner_for('敖隐')['skill']=='闪现'
    assert equipment()['confidence']==.87  # no score-to-probability promotion
    observe_equipment(tracker,103,[])
    assert tracker.summoner_for('敖隐')['skill']=='闪现'


@pytest.mark.parametrize('interruption', [[],[equipment(confidence=.84)],[equipment(),equipment('惩击')]])
def test_weak_missing_or_conflicting_frame_breaks_pending_loading_streak(interruption):
    tracker=EventTracker()
    observe_equipment(tracker,100,[equipment()])
    observe_equipment(tracker,101,interruption)
    observe_equipment(tracker,102,[equipment()])
    observe_equipment(tracker,103,[equipment()])
    assert not tracker.summoner_for('敖隐')


def test_manual_clear_also_discards_pending_loading_evidence():
    tracker=EventTracker();tracker.tracking_enemies=['敖隐']
    observe_equipment(tracker,100,[equipment()]);observe_equipment(tracker,101,[equipment()])
    tracker.correct_summoner('敖隐',None)
    observe_equipment(tracker,102,[equipment()])
    assert not tracker.summoner_for('敖隐')


def test_real_loading_icon_scores_are_preserved_and_decorated_icon_stays_unknown():
    scene=loading()
    result=verify(Image.open(FIXTURES/'loading.jpg'),scene['cards'])
    by_hero={r['hero']:r for r in result}
    assert '吕布' not in by_hero
    assert by_hero['孙策']['skill']=='惩击'
    assert .85<=by_hero['孙策']['confidence']<.9
    assert by_hero['敖隐']['skill']=='闪现'


def textured():
    rng=np.random.default_rng(8)
    return cv2.GaussianBlur(rng.integers(0,256,(576,1280),dtype=np.uint8),(3,3),0)


def test_camera_pan_alone_is_not_flash_but_relative_displacement_passes_motion_gate():
    pixels=textured();matrix=np.float32([[1,0,12],[0,1,3]])
    moved=cv2.warpAffine(pixels,matrix,(1280,576))
    before={'x':.42,'y':.4};camera_only={'x':.42+12/1280,'y':.4+3/576}
    assert not displaced(pixels,moved,before,camera_only)
    assert displaced(pixels,moved,before,{**camera_only,'x':camera_only['x']+.06})
    assert not displaced(np.zeros_like(pixels),np.zeros_like(pixels),before,{'x':.8,'y':.4})


def test_short_flow_keeps_bound_identity_and_expires_without_ocr():
    pixels=textured();moved=cv2.warpAffine(pixels,np.float32([[1,0,3],[0,1,2]]),(1280,576))
    target={'hero':'孙策','nickname':'无阙焕','x':.5,'y':.4}
    tracked=bridge(pixels,moved,target,gap_s=.2,anchor_age_s=.2)
    assert tracked['hero']=='孙策' and tracked['source']=='short_flow'
    assert tracked['x']==pytest.approx(.5+3/1280,abs=.002)
    assert bridge(pixels,moved,target,gap_s=.2,anchor_age_s=1.1) is not None
    assert bridge(pixels,moved,target,gap_s=.2,anchor_age_s=1.51) is None


def scene(*heroes):
    return {'panel':False,'targets':[{'hero':hero,'nickname':hero+'玩家','x':.4,'y':.4} for hero in heroes],
            'hero_levels':[],'bindings':[]}


def sequence_response(answer,scenes,equipped=None,*,input_kind='image'):
    import io
    pixels=[]
    for i in range(len(scenes)):
        buffer=io.BytesIO();Image.new('RGB',(960,540),(20*i,30,40)).save(buffer,'PNG');pixels.append(buffer.getvalue())
    frames=[{'image_base64':base64.b64encode(p).decode(),'captured_at':100+i*.5} for i,p in enumerate(pixels)]
    req=VisionRequest(**frames[-1],recent_frames=frames[:-1],focus='skills',input_kind=input_kind)
    responses=dict(zip(pixels,scenes))
    with patch('gameplan.skills.temporal_casts.read_scene',side_effect=lambda p,*args:responses[p]), \
         patch('gameplan.skills.combat_tracks.bridge',return_value=None), \
         patch('gameplan.ai.integrations.post_json',return_value={'done_reason':'stop','message':{'content':json.dumps(answer)}}) as post:
        result=detect_sequence(req,scenes[-1],bindings=[{'hero':'孙策'}],enemies=['孙策','嬴政','敖隐'],
                               known_heroes=KNOWN_HEROES,equipped=equipped)
    return result,post


def event(tid=0,kind='ultimate',index=1,**extra):
    return {'target_id':tid,'kind':kind,'frame_index':index,'state':'cast_start','confidence':.96,'evidence':'目标形态变化',**extra}


def test_middle_hero_is_checked_even_when_another_hero_is_visible_at_both_ends():
    result,post=sequence_response({'events':[event(tid=1)]},[scene('嬴政'),scene('孙策'),scene('嬴政')])
    prompt=post.call_args.args[1]['messages'][0]['content']
    assert '孙策' in prompt and result['checks_total']==2
    assert not result['enemy_skill_events']
    assert result['activity'][0]['hero']=='孙策'


def test_model_cannot_replace_the_locally_bound_hero_with_yingzheng():
    result,_=sequence_response(event(hero='嬴政'),[scene('孙策'),scene('孙策'),scene('孙策')])
    assert not result['enemy_skill_events'] and not result['activity']


def test_missing_flash_answer_is_incomplete_instead_of_a_successful_empty_scan():
    result,_=sequence_response({'ultimate':event()},[scene('敖隐'),scene('敖隐'),scene()],{'敖隐':'闪现'})
    assert result['status']=='partial'
    assert result['failure_reason']=='missing_skill_answer'


def test_missing_flash_does_not_discard_a_valid_ultimate_or_fabricate_flash():
    result,_=sequence_response({'ultimate':event(index=2,evidence='人形变成长龙腾空')},
                              [scene('敖隐'),scene('敖隐'),scene()],{'敖隐':'闪现'})
    assert result['status']=='partial'
    assert result['checks_completed']==0
    assert [e['skill'] for e in result['enemy_skill_events']]==['穷乎玄间']


def test_late_onset_uses_its_preceding_frame_not_unrelated_start_of_batch():
    frames=[scene('孙策') for _ in range(5)]
    frames[2]['hero_levels']=[{'hero':'孙策','level':4,'visible_text':'4','confidence':.99}]
    result,_=sequence_response(event(index=3),frames)
    cast=result['enemy_skill_events'][0]
    assert (cast['onset_start_index'],cast['onset_end_index'])==(2,3)
    tracker=EventTracker()
    added=tracker.ingest(result['enemy_skill_events'],102,enemies=['孙策'],frame_times=[100,100.5,101,101.5,102],
                         hero_levels=result['hero_levels'])
    assert len(added)==1
    assert added[0].captured_at==101.5
    assert tracker.snapshot(102)[0]['remaining_s']==39.5


def test_level_read_only_after_onset_still_cannot_validate_cast():
    frames=[scene('孙策') for _ in range(5)]
    frames[4]['hero_levels']=[{'hero':'孙策','level':4,'visible_text':'4','confidence':.99}]
    result,_=sequence_response(event(index=3),frames)
    tracker=EventTracker()
    assert tracker.ingest(result['enemy_skill_events'],102,enemies=['孙策'],frame_times=[100,100.5,101,101.5,102],
                          hero_levels=result['hero_levels'])==[]


def test_aoyin_transformation_cannot_also_create_a_flash_event_without_post_identity():
    result,_=sequence_response({'ultimate':event(index=2,evidence='人形变成长龙腾空'),'flash':event(kind='flash',index=2)},
                              [scene('敖隐'),scene('敖隐'),scene()],{'敖隐':'闪现'})
    assert [e['skill'] for e in result['enemy_skill_events']]==['穷乎玄间']
    assert result['rejected_candidates']['flash_motion_unconfirmed']==1


def test_short_lived_name_between_three_anchors_is_still_discovered():
    result,post=sequence_response(event(index=2),[scene(),scene('孙策'),scene(),scene(),scene()])
    assert post.call_count==1 and result['checks_total']==1


def test_eight_frame_prompt_and_short_prompt_use_same_sufficient_context():
    _,short=sequence_response({'state':'none'},[scene('孙策')]*3)
    _,long=sequence_response({'state':'none'},[scene('孙策')]*8)
    context=short.call_args.args[1]['options']['num_ctx']
    assert context==long.call_args.args[1]['options']['num_ctx']
    assert context>8*1024+700  # preserve prompt and final answer after image tokens


def test_short_video_batch_keeps_all_six_frames_in_one_ordered_model_image():
    result, post = sequence_response({'state': 'none'}, [scene('孙策')]*6, input_kind='video')
    message = post.call_args.args[1]['messages'][0]
    assert result['frames_checked'] == 6
    assert len(message['images']) == 1
    assert 'F0至F5' in message['content']
    assert '不能把拼图的左右位置当成角色发生位移' in message['content']


def test_mixed_full_frame_and_actor_crops_do_not_inflate_the_whole_grid():
    import io
    # Untargetable final frame has no anchor within .8s, so it stays full size.
    _, post = sequence_response({'state': 'none'},
        [scene('敖隐'), scene(), scene(), scene('敖隐'), scene(), scene()], input_kind='video')
    encoded = post.call_args.args[1]['messages'][0]['images'][0]
    sheet = Image.open(io.BytesIO(base64.b64decode(encoded)))
    assert sheet.width*sheet.height < 1_600_000, 'Empty padding made a six-frame request exceed its model budget'


def test_disappearing_name_rechecks_preceding_identity_before_building_model_context():
    import io
    _, post = sequence_response({'state': 'none'},
        [scene('敖隐'), scene(), scene(), scene(), scene('敖隐'), scene()], input_kind='video')
    encoded = post.call_args.args[1]['messages'][0]['images'][0]
    sheet = Image.open(io.BytesIO(base64.b64decode(encoded)))
    assert sheet.width < 1600, 'The unexamined penultimate name left the transformation outside the actor context'


def test_ocr_cache_reuses_identical_evidence_but_not_a_changed_hero_binding():
    from collections import OrderedDict
    cache=OrderedDict();bindings=[{'hero':'孙策','nickname':'甲乙丙','side':'enemy_roster'}]
    with patch('gameplan.skills.combat_evidence._read_scene',return_value=scene('孙策')) as read:
        first=read_scene(b'pixels',KNOWN_HEROES,bindings,cache=cache)
        first['targets'].clear()
        again=read_scene(b'pixels',KNOWN_HEROES,bindings,cache=cache)
        assert len(again['targets'])==1 and read.call_count==1
        read_scene(b'pixels',KNOWN_HEROES,[{**bindings[0],'hero':'嬴政'}],cache=cache)
        assert read.call_count==2


def test_parallel_scene_anchors_preserve_order_and_binding_scoped_cache():
    from collections import OrderedDict
    from concurrent.futures import ThreadPoolExecutor
    from gameplan.skills.combat_evidence import read_scenes
    cache = OrderedDict()
    bindings = [{'hero': '孙策', 'nickname': '甲乙丙', 'side': 'enemy_roster'}]
    def read(pixels, *args):
        return {'pixels': pixels, 'targets': [{'hero': args[-1][0]['hero']}]}
    with ThreadPoolExecutor(max_workers=3) as pool, \
         patch('gameplan.skills.combat_evidence.scene_workers', return_value=pool), \
         patch('gameplan.skills.combat_evidence._read_scene', side_effect=read) as reader:
        result = read_scenes([b'a', b'b', b'a'], KNOWN_HEROES, bindings, cache=cache)
        assert [r['pixels'] for r in result] == [b'a', b'b', b'a']
        assert reader.call_count == 2
        result[0]['targets'].clear()
        assert len(read_scene(b'a', KNOWN_HEROES, bindings, cache=cache)['targets']) == 1
        read_scenes([b'a'], KNOWN_HEROES, [{**bindings[0], 'hero': '嬴政'}], cache=cache)
        assert reader.call_count == 3


def test_packed_overview_rechecks_one_changed_actor_and_preserves_original_frame_index():
    detail={'enemy_skill_events':[{'hero':'孙策','skill':'长帆破浪','frame_index':1}],
            'activity':[],'hero_levels':[],'status':'observed'}
    with patch('gameplan.skills.grounded_casts.detect',return_value=detail) as second, \
         patch('gameplan.skills.temporal_casts.cv2.absdiff',side_effect=lambda a,b:np.full_like(a,50)):
        result,_=sequence_response({'state':'none'},[scene('孙策')]*8)
    assert second.call_count==1
    assert len(second.call_args.args[0].recent_frames)==1
    # Each submitted frame now shares the identity reader; the latest eligible
    # pair is 6 -> 7 and must preserve the source frame index.
    assert not result['enemy_skill_events']
    assert result['suspicions'][0]['frame_index']==7
    assert result['suspicions'][0]['reason']=='pair_recheck_unconfirmed'
    assert result['pair_rechecks']==1
