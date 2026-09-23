import numpy as np
from PIL import Image, ImageDraw
from pathlib import Path

from gameplan.skills.effect_actors import visible_actors


def field(color='red', resource=False):
    image=Image.new('RGB',(1280,576),(25,25,25))
    draw=ImageDraw.Draw(image)
    draw.rectangle((700,200,795,206),fill=color)
    if resource:draw.rectangle((700,211,795,215),fill='red')
    return image


def test_enemy_bar_is_localizable_without_needing_another_frame():
    readings=[{'text':'霸体','score':.98,'box':np.array([[700,175],[795,175],[795,189],[700,189]])}]
    actors=visible_actors(field(),readings,[],['孙策','敖隐'])
    assert len(actors)==1 and actors[0]['hero'] is None


def test_friendly_bar_and_red_resource_bar_are_not_enemy_actors():
    readings=[{'text':'霸体','score':.98,'box':np.array([[700,180],[795,180],[795,192],[700,192]])}]
    assert visible_actors(field('deepskyblue',True),readings,[],['敖隐'])==[]
    assert visible_actors(field('lime',True),readings,[],['敖隐'])==[]


def test_same_frame_bound_nickname_owns_actor_identity():
    readings=[{'text':'实际船长','score':.98,'box':np.array([[700,175],[795,175],[795,189],[700,189]])}]
    bindings=[{'hero':'孙策','nickname':'实际船长','side':'enemy_roster'}]
    assert visible_actors(field(),readings,bindings,['孙策','敖隐'])[0]['hero']=='孙策'
    assert visible_actors(field(),readings,bindings,['敖隐'])==[]


def test_neutral_monster_hp_is_not_a_hero_identity():
    readings=[{'text':'4811','score':.98,'box':np.array([[700,175],[795,175],[795,189],[700,189]])}]
    assert visible_actors(field(),readings,[],['敖隐'])==[]


def test_real_1655_friendly_frame_has_no_enemy_actor_for_a_hallucinated_cast():
    from gameplan.skills.combat_evidence import read_scene
    path=Path(__file__).parent/'fixtures/combat/attribution/allies-only-1015.png'
    scene=read_scene(path.read_bytes(),set())
    assert visible_actors(Image.open(path),scene['readings'],[],['敖隐','孙策','吕布','嬴政','瑶'])==[]


def test_real_single_frame_ship_has_one_enemy_actor_even_when_nickname_is_covered():
    from gameplan.skills.combat_evidence import read_scene
    path=Path(__file__).parent/'fixtures/combat/attribution/sunce-868.png'
    scene=read_scene(path.read_bytes(),set())
    actors=visible_actors(Image.open(path),scene['readings'],[],['孙策','敖隐'])
    assert len(actors)==1 and .64<actors[0]['x']<.72 and .39<actors[0]['y']<.47
