import json
import sqlite3

import gameplan.knowledge.skill_knowledge as kb
from scripts.update_skill_catalog import parse_mobile_skills
from tests import test_advisor as advisor_tests
from unittest.mock import patch


def test_mobile_skill_ids_preserve_ultimate_with_four_active_skills():
    page = ''.join(f'<span class="plus-name" data-skillid="519{s}0">技能{s}</span>'
                   '<span class="plus-value">(冷却值：80/75/70 消耗：120)</span>'
                   '<p class="plus-int">官方技能描述</p>' for s in [0, 1, 2, 4, 3])
    skills = parse_mobile_skills(page)
    assert [s['slot'] for s in skills] == [0, 1, 2, 4, 3]
    assert skills[-1]['base_cooldowns_s'] == [80, 75, 70]


def test_database_uses_exact_names_and_includes_previously_missing_heroes():
    assert kb.get_hero('廉颇')['has_skill_data']
    assert kb.get_hero('敖隐')['ultimate']['name'] == '穷乎玄间'
    assert kb.get_hero('不存在的英雄') is None
    assert kb.get_hero('卢雅那')['ultimate']['cooldown_s'] == [50, 45, 40]
    assert kb.get_hero('心魔六耳')['ultimate']['name'] == '十方俱焚'
    with sqlite3.connect(kb.DATABASE_PATH) as db:
        assert db.execute('SELECT count(*) FROM heroes').fetchone()[0] == kb.coverage()['heroes']


def test_advice_payload_contains_retrieved_skills():
    case = advisor_tests.AdvisorTests(); case.setUp()
    module = advisor_tests.module
    with patch.object(module, 'provider_config', return_value=advisor_tests.CONFIG), patch.object(module.time, 'time', return_value=1002), patch.object(module, 'post_json', return_value=advisor_tests.external_result()) as send:
        case.engine.analyze(case.req)
    payload = json.loads(send.call_args.args[1]['messages'][1]['content'])
    records = [m for m in payload['mechanisms'] if 'skills' in m]
    assert records and all(m['source_url'].startswith('https://pvp.qq.com/') for m in records)
    assert all(m['hero'] in payload['facts'][2]['heroes'] + payload['facts'][3]['heroes'] for m in records)


def test_four_profiles_supply_conditions_without_claiming_observed_lane():
    from gameplan.core.models import CoachAnalysisRequest
    engine = advisor_tests.module.Advisor()
    frame = advisor_tests.observed()
    names = ['伽罗', '艾琳', '小乔', '吕布']
    frame.update(ally_roster=names, enemy_roster=['赵云'], player_hero=None)
    frame_id = 'b' * 32
    engine.remember(frame_id, 'profile-match', frame, 1000, 'sample')
    req = CoachAnalysisRequest(match_id='profile-match', frame_id=frame_id, side='a')
    context = engine.facts(req, engine.frames[frame_id], 1002)
    profiles = [m['conditional_coaching'] for m in context['mechanisms'] if 'conditional_coaching' in m]
    assert {p['hero'] for p in profiles} == set(names)
    assert context['player'] is None
    for p in profiles:
        assert p['status'] in {'official_reference_conditional', 'mixed_source_conditional'}
        assert all(r['when'] and r['source_quote'] and r['source_url'] for r in p['rules'])
        for rule in p['rules']:
            if rule['basis_type'] == 'official_tip_with_application_conditions':
                assert rule['source_url'] == p['source_url']
            else:
                assert rule['basis_type'] == 'third_party_guide_with_application_conditions'
                assert rule['source_author'] and rule['published_at']
        assert p['missing']


def test_local_only_reads_each_personal_profile_without_network():
    from gameplan.core.models import CoachAnalysisRequest
    for name in ['李元芳', '伽罗', '艾琳', '小乔', '吕布']:
        engine = advisor_tests.module.Advisor()
        frame = advisor_tests.observed('in_game')
        frame.update(ally_roster=[name], enemy_roster=['赵云'], player_hero=name)
        engine.remember('c' * 32, 'kb-local', frame, 1000, 'live')
        req = CoachAnalysisRequest(match_id='kb-local', frame_id='c' * 32)
        with patch.object(advisor_tests.module.time, 'time', return_value=1002), patch.object(advisor_tests.module, 'post_json') as send:
            result = engine.analyze(req, local_only=True)
        assert result['knowledge_references']
        assert '条件参考' in result['summary']
        assert all(r['hero'] == name and not r['conditions_confirmed'] for r in result['knowledge_references'])
        assert all(not r.get('lane_required') for r in result['knowledge_references'])
        send.assert_not_called()


def test_reference_selection_respects_unknown_identity_lane_and_visible_cooldown():
    from gameplan.knowledge.knowledge_advice import select_references
    context = {'player': '李元芳', 'scope': 'live', 'group_a': ['李元芳'], 'group_b': ['赵云'],
               'skill_knowledge': kb.lookup_many(['李元芳'])}
    visible = {'phase': 'in_game', 'player_hero': '李元芳', 'self_skills': [{'slot': 2, 'remaining_s': 5}]}
    rules = select_references(context, visible, None, advisor_tests.module.KNOWN_HEROES, 100)
    assert all(2 not in r['basis_slots'] and not r['lane_required'] for r in rules)
    assert all('阿轲' not in r['when'] and '白起' not in r['when'] for r in rules)
    assert not select_references({**context, 'player': None}, visible, '打野', advisor_tests.module.KNOWN_HEROES)


def test_missing_model_key_keeps_sourced_knowledge_fallback():
    from gameplan.core.models import CoachAnalysisRequest
    engine = advisor_tests.module.Advisor()
    frame = advisor_tests.observed('in_game')
    frame.update(ally_roster=['小乔'], enemy_roster=['赵云'], player_hero='小乔')
    engine.remember('d' * 32, 'kb-fallback', frame, 1000, 'live')
    with patch.object(advisor_tests.module.time, 'time', return_value=1002), patch.object(advisor_tests.module, 'provider_config', return_value={**advisor_tests.CONFIG, 'key': ''}):
        result = engine.analyze(CoachAnalysisRequest(match_id='kb-fallback', frame_id='d' * 32))
    assert result['source'] == 'local_fallback'
    assert result['knowledge_references'] and '条件参考' in result['summary']


def test_five_profiles_have_complete_configuration_and_honest_version_status():
    report = kb.coverage()
    assert report['coaching_heroes'] == 5
    assert all(p['reference_ready'] and not p['current_patch_verified'] for p in report['coaching_profiles'])
