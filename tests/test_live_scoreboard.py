"""User-captured pixels, including merged labels and two-digit levels."""
from pathlib import Path
from collections import OrderedDict
from unittest.mock import patch
import pytest
from gameplan.ai.advisor import KNOWN_HEROES
from gameplan.skills.combat_evidence import read_scene
from gameplan.vision.summoner_icons import read_equipment

PIXELS = (Path(__file__).parent/'fixtures/combat/live-scoreboard-20260922/panel.png').read_bytes()


@pytest.fixture(scope='module')
def panel():
    return read_scene(PIXELS, KNOWN_HEROES)


def test_visible_base_hero_and_merged_nicknames_do_not_erase_roster(panel):
    assert set(panel['enemy_roster']) == {'元流之子','后羿','墨子','大司命','太乙真人'}
    assert '百里守约' in panel['ally_roster']


def test_portrait_levels_keep_both_digits_and_exclude_red_death_countdown(panel):
    readings = {r['hero']:r['level'] for r in panel['hero_levels']}
    assert {h:readings.get(h) for h in ['后羿','墨子','大司命','太乙真人']} == {
        '后羿':15,'墨子':15,'大司命':14,'太乙真人':14}


def test_actual_small_scoreboard_flash_icon_is_read_without_relaxing_confidence(panel):
    readings = read_equipment(PIXELS, KNOWN_HEROES, readings=panel['readings'], panel_scene=panel)
    assert any(r['hero']=='后羿' and r['skill']=='闪现' and r['confidence']>=.9 for r in readings)


def test_original_user_screenshot_reads_all_five_heroes_and_levels():
    path=Path(__file__).parent/'fixtures/combat/live-scoreboard-20260922/early-panel.png'
    scene=read_scene(path.read_bytes(),KNOWN_HEROES)
    assert set(scene['enemy_roster']) == {'元流之子','后羿','墨子','大司命','太乙真人'}
    levels={r['hero']:r['level'] for r in scene['hero_levels']}
    assert {h:levels.get(h) for h in scene['enemy_roster']} == {
        '元流之子':10,'后羿':9,'墨子':8,'大司命':8,'太乙真人':7}


def test_same_pixels_with_new_bindings_reuse_ocr_but_recompute_identity():
    cache=OrderedDict()
    first={'panel':False,'readings':[{'text':'unchanged'}],'targets':[]}
    second={'panel':False,'readings':first['readings'],'targets':[{'hero':'后羿'}]}
    with patch('gameplan.skills.combat_evidence._read_scene',side_effect=[first,second]) as reader:
        read_scene(PIXELS,KNOWN_HEROES,cache=cache)
        result=read_scene(PIXELS,KNOWN_HEROES,[{'hero':'后羿','nickname':'名字','side':'enemy_roster'}],cache=cache)
    assert result['targets']==second['targets']
    assert reader.call_args.kwargs.get('readings')==first['readings']
