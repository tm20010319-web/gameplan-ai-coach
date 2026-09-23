import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import gameplan.web.monitor_app as monitor_app
import gameplan.knowledge.skill_knowledge as kb
from gameplan.skills.auto_skill_monitor import EventTracker
from scripts.update_skill_catalog import cooldown_fields, hero_entry, parse_official_skills

FIXTURES = json.loads((Path(__file__).parent / 'fixtures/official_skill_details.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('hero_id,name,slot', [
    (547, '焚火灼目', 3), (549, '十方俱焚', 3), (188, '乾坤以同', 3),
    (581, '倾注一击', 3), (191, '漩涡之门', 4), (182, '剑来', 4),
    (179, '神晖：诛灭', 4), (125, '秘术·散', 4), (519, '穷乎玄间', 4),
    (525, '强力收纳', 3),
])
def test_official_ids_do_not_override_button_or_ultimate_identity(hero_id, name, slot):
    skills = parse_official_skills(FIXTURES[str(hero_id)])
    ultimate = [skill for skill in skills if skill['is_ultimate']]
    assert len(ultimate) == 1
    assert (ultimate[0]['name'], ultimate[0]['slot']) == (name, slot)
    assert len({skill['slot'] for skill in skills}) == len(skills)
    assert all(skill['description'] for skill in skills)
    assert sum(skill['passive'] for skill in skills) == 1


@pytest.mark.parametrize('raw,ranks,expected', [
    ('50(-5/Lv)', 3, [50, 45, 40]),
    ('12.5(-0.5/Lv）', 6, [12.5, 12, 11.5, 11, 10.5, 10]),
    ('24-1/LV', 3, [24, 23, 22]),
    ('10/9.6/9.2/8.8/8.4/8秒', 6, [10, 9.6, 9.2, 8.8, 8.4, 8]),
])
def test_cooldown_notation_is_normalized_without_losing_the_source(raw, ranks, expected):
    result = cooldown_fields(raw, ranks)
    assert result['base_cooldowns_s'] == expected
    assert result['cooldown_text'] == raw


def test_dynamic_cooldown_needs_target_remaining_time_and_cannot_start_a_static_timer():
    skill = next(s for s in parse_official_skills(FIXTURES['159']) if s['is_ultimate'])
    assert skill['cooldown_kind'] == 'dynamic'
    assert skill['base_cooldowns_s'] == []
    assert skill['cooldown_formula']['base_component_s'] == [10, 9, 8]
    assert skill['cooldown_formula']['target_remaining_multiplier'] == 1.2
    assert EventTracker().resolve_cooldown('朵莉亚', '天籁', 3) is None


def test_conflicting_official_ranks_are_preserved_but_separate_reference_estimate_is_available():
    record = kb.get_hero('鲁班大师')
    assert record['ultimate']['cooldown_review_required']
    assert record['ultimate']['cooldown_alternatives'][0]['base_cooldowns_s'] == [40, 35, 30]
    assert EventTracker().estimate_values('鲁班大师', '强力收纳', 3) == [40, 35, 30]


def test_missing_unknown_and_zero_cooldowns_are_distinct():
    assert cooldown_fields('', 3)['cooldown_kind'] == 'unknown'
    assert cooldown_fields('依效果决定', 3)['base_cooldowns_s'] == []
    assert cooldown_fields('0', 3)['cooldown_kind'] == 'no_fixed'


def test_named_ultimate_takes_precedence_over_generic_third_slot():
    assert EventTracker().resolve_cooldown('大乔', '漩涡之门', 3) == 80


def test_wrong_hero_or_incomplete_source_is_rejected():
    with patch('scripts.update_skill_catalog.fetch', return_value=json.dumps(FIXTURES['547'])):
        with pytest.raises(ValueError, match='identity mismatch'):
            hero_entry({'ename': 193, 'cname': '铠'})
    broken = copy.deepcopy(FIXTURES['547'])
    broken['skill4_des'] = ''
    with pytest.raises(ValueError, match='Missing description'):
        parse_official_skills(broken)


def test_entire_official_roster_is_queryable_with_skills_and_ultimate_cooldown():
    catalog = json.loads(kb.CATALOG_PATH.read_text(encoding='utf-8'))
    with TestClient(monitor_app.app) as client:
        status = client.get('/api/knowledge/skills/status').json()
        assert status['heroes'] == status['with_skill_data'] == status['with_ultimate'] == status['with_ultimate_cooldown'] == len(catalog['heroes'])
        assert status['skills'] == sum(len(hero['skills']) for hero in catalog['heroes'])
        assert status['missing_heroes'] == status['missing_ultimate_cooldown'] == []
        assert status['dynamic_ultimate_cooldowns'] == ['朵莉亚']
        for hero in catalog['heroes']:
            response = client.get('/api/knowledge/skills/' + hero['hero'])
            assert response.status_code == 200
            record = response.json()
            assert record['hero'] == hero['hero']
            assert record['ultimate']['is_ultimate']
            assert record['has_ultimate_cooldown']
            assert all(skill['description'] for skill in record['skills'])


def test_catalog_cache_refreshes_after_another_process_has_synced_database(tmp_path):
    catalog_path = tmp_path / 'catalog.json'
    catalog_path.write_text(json.dumps({'heroes': [{'hero': '旧数据'}]}), encoding='utf-8')
    with patch.object(kb, 'CATALOG_PATH', catalog_path):
        assert kb._catalog()['heroes'][0]['hero'] == '旧数据'
        catalog_path.write_text(json.dumps({'heroes': [{'hero': '新资料已导入'}]}), encoding='utf-8')
        import os
        stamp = catalog_path.stat().st_mtime_ns + 1_000_000
        os.utime(catalog_path, ns=(stamp, stamp))
        assert kb._catalog()['heroes'][0]['hero'] == '新资料已导入'
