"""Reviewed scope and knowledge-backed descriptions; never a trained model claim."""
import math
import re

from gameplan.knowledge.skill_knowledge import _catalog


TARGET_LATENCY_S = 20
FIRST_BATCH = (
    '吕布', '孙策', '铠', '狂铁', '夏侯惇', '白起', '廉颇', '项羽', '蒙恬', '亚连',
    '赵云', '李白', '韩信', '露娜', '娜可露露', '澜', '镜', '宫本武藏', '云缨', '大司命',
    '高渐离', '王昭君', '安琪拉', '甄姬', '小乔', '貂蝉', '嬴政', '武则天', '张良', '上官婉儿',
    '敖隐', '马可波罗', '后羿', '鲁班七号', '黄忠', '蒙犽', '虞姬', '狄仁杰', '公孙离', '伽罗',
    '张飞', '牛魔', '鬼谷子', '蔡文姬', '孙膑', '苏烈', '东皇太一', '瑶', '大乔', '刘禅',
)


def cooldown_values(skill):
    return [float(value) for value in skill.get('base_cooldowns_s', [])
            if type(value) in (int, float) and math.isfinite(value) and 0 < value <= 3600]


def mechanism(text, limit=240):
    """Keep gameplay mechanics, while reducing coefficient clutter in prompts."""
    return re.sub(r'\s+', ' ', re.sub(r'[（(][^）)]*[）)]', '', text or '')).strip()[:limit]


def recognition_reference(hero):
    record = next((item for item in _catalog().get('heroes', []) if item.get('hero') == hero), {})
    ultimate = next((s for s in record.get('skills', []) if s.get('is_ultimate')), None)
    if not ultimate:
        return None
    return {
        'hero': hero, 'first_batch': hero in FIRST_BATCH,
        'ultimate': ultimate['name'], 'slot': ultimate.get('slot'),
        'mechanic': mechanism(ultimate.get('description')),
        'other_skills': [{'name': s.get('name'), 'mechanic': mechanism(s.get('description'), 90)}
                         for s in record.get('skills', []) if not s.get('is_ultimate') and not s.get('passive')],
        'cooldown_values_s': cooldown_values(ultimate),
        'source': 'resources/knowledge/skill_catalog.json',
        'validation': 'pending_real_match_validation',
    }


def suspect(result, *, hero, skill, index, confidence, evidence, reason, event_type="uncertain"):
    """A candidate may start a provisional estimate, never a confirmed cast."""
    if type(index) is not int or index < 0 or not .65 <= confidence <= 1 or not evidence.strip():
        return
    if any(word in evidence for word in ('图标', '按钮', '技能栏')):
        return
    result.setdefault('suspicions', []).append({
        'hero': hero, 'skill': skill, 'frame_index': index,
        'confidence': confidence, 'evidence': evidence[:240], 'reason': reason, 'event_type': event_type,
    })
