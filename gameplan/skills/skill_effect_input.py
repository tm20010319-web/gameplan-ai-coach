"""Bounded battlefield detail and visual references for the local skill reader.

Descriptions help interpret pixels; they never establish identity or a cast.
Unlisted heroes retain their catalog mechanism rather than invented cues.
"""
from PIL import Image, ImageDraw
import io


VISUAL_CUES = {
    '小乔': '星华缭乱：以角色为中心持续展开范围领域，并有多次从空中落下的打击。皮肤可将流星变成星星、花朵或其他装饰，颜色不固定。只看到持续领域及部分落点、但被遮挡时可报告疑似；单个回旋扇、单个旋风、伤害数字或队友领域不算大招。',
    '后羿': '弓手释放大型火焰箭/鸟形远程弹道，体积和形状明显区别于普通连射；须能归属于后羿的发射位置或清晰角色，屏幕另一端孤立飞过的箭不能确定是谁释放。',
    '大司命': '跃起后从天落地并展开神巫力量，命中英雄可出现协同普攻的灵体；普通魂链、过墙路径、单独绿色光效和斩杀结果不足以证明开大。',
    '太乙真人': '自身及附近一名队友出现替身傀儡保护或清晰的原地复活过程，保护持续很短；须结合太乙真人施法与保护特效，普通炼炉爆炸、治疗绿光或复活装备不能确认。',
    '墨子': '人形机甲周围展开四边形/方形持续电能屏障，透视下呈菱形，有四面半透明墙和电弧，可罩住其他英雄；区别于贴身护盾、单枚炮弹爆炸和地面弹坑。',
    '吕布': '角色跃起后从天落地，周围形成大范围圆形杀戮场；单独圆形落点预告只算疑似，普通挥戟和队友圆形领域不能确认。',
    '孙策': '角色驾驶清楚可辨的船向前航行；普通奔跑、冲刺、水花或旁人的船不能确认。',
    '敖隐': '角色化为完整龙形躯体飞行或俯冲，颜色随皮肤变化；普通人形挥剑、剑气、光带和击退不能确认化龙。',
    '妲己': '角色向目标连续发射多团狐火；一颗爱心、单条冲击波、伤害数字或他人弹道不能确认。',
}

# A textual completeness check, not an independent pixel classifier. These
# distinctive forms are necessary evidence in the reviewed examples; a high
# model score with only ordinary actions must remain tentative.
EVIDENCE_MARKERS = {
    '墨子': ('方形', '四边', '菱形', '四面', '电能屏障', '高能屏障'),
    '吕布': ('圆形', '圆环', '圆圈', '杀戮场', '围栏'),
    '孙策': ('船', '舟'),
    '敖隐': ('龙身', '龙形', '长龙', '化龙', '真龙', '龙躯'),
    '妲己': ('多团', '多颗', '数团', '数颗', '连续', '五团', '5团', '五颗', '5颗'),
}


def supports_ultimate(hero, evidence):
    markers = EVIDENCE_MARKERS.get(hero)
    return markers is None or any(marker in evidence for marker in markers)


def visual_reference(reference):
    return {
        'hero': reference['hero'], 'ultimate': reference['ultimate'],
        'nickname': reference.get('nickname'),
        'visual': VISUAL_CUES.get(reference['hero'], reference['mechanic']),
        'other_skills': [item['name'] for item in reference['other_skills']],
        'summoners': reference['summoners'], 'ultimate_enabled': reference['ultimate_enabled'],
    }


def effect_tile(picture, actors, index, *, detail=False):
    """Keep every located enemy and its surrounding field, not just the body.

    Geometry is in original frame coordinates; IDs and frame indices are never
    renumbered when earlier frames have no visible enemy. Multiple enemies use
    a union crop so nearby effects remain attributable.
    """
    width, height = picture.size
    unit = height / 576
    radius, above = (190, 55) if detail else (300, 180)
    left = max(0, int(min(a['x'] * width for a in actors) - radius * unit))
    top = max(0, int(min(a['y'] * height for a in actors) - above * unit))
    right = min(width, round(max(a['x'] * width for a in actors) + radius * unit))
    bottom = min(height, round(max(a['y'] * height for a in actors) + 265 * unit))
    crop = picture.crop((left, top, right, bottom))
    scale = min(640 / crop.width, 540 / crop.height, 2.0)
    crop = crop.resize((max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
                       Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(crop)
    for actor in actors:
        x = (actor['x'] * width - left) * crop.width / (right - left)
        y = (actor['y'] * height - top) * crop.height / (bottom - top)
        draw.rectangle((x-40, y-5, x+40, y+8), outline=(255, 60, 180), width=2)
        draw.text((x-40, y-23), f'A{actor["actor_id"]}', fill=(255, 60, 180),
                  stroke_width=1, stroke_fill=(0, 0, 0))
    tile = Image.new('RGB', (crop.width, crop.height+24), (20, 20, 20))
    tile.paste(crop, (0, 24))
    ImageDraw.Draw(tile).text((8, 5), f'F{index}', fill='white')
    return tile


def response_schema(actors, references):
    """Constrain names and IDs while keeping an empty answer possible."""
    fields = {
        'actor_id': {'type': 'integer', 'enum': [a['actor_id'] for a in actors]},
        'frame_index': {'type': 'integer', 'enum': sorted({a['frame_index'] for a in actors})},
        'hero': {'type': 'string', 'enum': [r['hero'] for r in references]},
        'kind': {'type': 'string', 'enum': ['ultimate', 'summoner']},
        'skill': {'type': 'string', 'enum': sorted({name for r in references
                    for name in [r['ultimate'], *r['summoners']]})},
        'effect_visible': {'type': 'boolean'}, 'enemy_visible': {'type': 'boolean'},
        'identity_visible': {'type': 'boolean'},
        'confidence': {'type': 'number', 'minimum': 0, 'maximum': 1},
        'evidence': {'type': 'string', 'maxLength': 160},
    }
    review = {'type': 'object', 'properties': {
        kind: {'type': 'string', 'enum': ['event', 'not_seen', 'uncertain', 'not_visible']}
        for kind in ('ultimate', 'summoner')},
        'required': ['ultimate', 'summoner'], 'additionalProperties': False}
    return {'type': 'object', 'properties': {
        'description': {'type': 'string', 'maxLength': 160},
        'events': {'type': 'array', 'maxItems': 10, 'items': {
            'type': 'object', 'properties': fields, 'required': list(fields),
            'additionalProperties': False}},
        'reviews': {'type': 'object', 'properties': {r['hero']: review for r in references},
                    'required': [r['hero'] for r in references], 'additionalProperties': False},
    }, 'required': ['description', 'reviews', 'events'], 'additionalProperties': False}


def effect_sheet(tiles):
    """Encode a bounded frame batch, preserving each original A/F label."""
    rows = [tiles[offset:offset+2] for offset in range(0, len(tiles), 2)]
    heights = [max(tile.height for tile in row) for row in rows]
    sheet = Image.new('RGB', (max(sum(tile.width for tile in row) for row in rows), sum(heights)), (20, 20, 20))
    y = 0
    for row, height in zip(rows, heights):
        x = 0
        for tile in row:
            sheet.paste(tile, (x, y))
            x += tile.width
        y += height
    buffer = io.BytesIO()
    sheet.save(buffer, 'JPEG', quality=92)
    return buffer.getvalue()
