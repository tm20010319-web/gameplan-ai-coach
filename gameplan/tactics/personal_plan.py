"""Personal, lane-specific plans using roster-bound local mechanism entries."""
from gameplan.tactics.coach import HEROES
from gameplan.tactics.loading_plan import SUPPLEMENT
import json
import re

from pydantic import BaseModel, ConfigDict, Field


def text_model_config():
    import os
    from dotenv import dotenv_values
    from gameplan.tactics.coach import ROOT
    from gameplan.ai.advisor import provider_config
    values = dotenv_values(ROOT / '.env')
    def get(name, default=''):
        return values.get(name) or os.getenv(name) or default
    provider = get('COACH_PROVIDER', 'deepseek').lower()
    defaults = {
        'deepseek': ('https://api.deepseek.com', 'deepseek-chat', 'DEEPSEEK_API_KEY'),
        'qwen': ('https://dashscope.aliyuncs.com/compatible-mode/v1', 'qwen-plus', 'DASHSCOPE_API_KEY'),
        'doubao': ('https://ark.cn-beijing.volces.com/api/v3', '', 'ARK_API_KEY'),
    }
    if provider not in defaults:
        raise ValueError('Unsupported COACH_PROVIDER')
    base, model, key_name = defaults[provider]
    if provider == 'deepseek':
        previous = provider_config()
        base, model = previous['base'], previous['model']
    return {'provider': provider, 'base': get('COACH_BASE_URL', base),
            'model': get('COACH_MODEL', model),
            'key': get('COACH_API_KEY', get(key_name))}


class PlanSection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=10, max_length=500)


class HeroAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    hero: str = Field(min_length=1, max_length=20)
    text: str = Field(min_length=10, max_length=400)


class ExternalPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    summary: str = Field(min_length=20, max_length=500)
    sections: list[PlanSection] = Field(min_length=3, max_length=7)
    enemy_risks: list[HeroAction] = Field(min_length=1, max_length=5)
    ally_coordination: list[HeroAction] = Field(min_length=1, max_length=4)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


def analyze_personal_plan(allies, enemies, player, lane):
    from gameplan.ai.advisor import KNOWN_HEROES
    from gameplan.ai.integrations import post_json
    fallback = build_personal_plan(allies, enemies, player, lane)
    try:
        cfg = text_model_config()
    except ValueError:
        return {**fallback, 'fallback_reason': '外部模型供应商配置无效，当前显示本地规则建议。'}
    if not cfg['key'] or not cfg['model']:
        return {**fallback, 'fallback_reason': '外部接口未配置，当前显示本地规则建议。'}
    book = {**HEROES, **SUPPLEMENT}
    evidence = [
        {'id':'F1', 'status':'confirmed', 'text':f'本人英雄：{player}；用户确认分路：{lane}'},
        {'id':'F2', 'status':'confirmed', 'text':'己方阵容：'+'、'.join(allies)},
        {'id':'F3', 'status':'confirmed', 'text':'敌方阵容：'+'、'.join(enemies)},
        {'id':'F4', 'status':'confirmed', 'text':'加载阶段；敌方实际分路、实时技能、经济和位置未知'},
    ]
    # Only explicitly reviewed, versioned entries qualify as tactical evidence.
    qualified = {h: book[h] for h in allies + enemies if h in book
                 and book[h].get('review_status') == 'approved'
                 and book[h].get('game_version') and book[h].get('source_url')
                 and book[h].get('verified_at') and not book[h].get('expired', False)}
    for index, (hero, info) in enumerate(qualified.items(), 1):
        evidence.append({'id':f'K{index}', 'status':'reviewed', 'hero':hero,
                         'text':info.get('ally_plan',''), 'source':info['source_url'],
                         'game_version':info['game_version']})
    facts = {'player': player, 'lane': lane, 'allies': allies, 'enemies': enemies,
             'mechanisms': qualified, 'evidence':evidence,
             'knowledge_gaps':[h for h in allies+enemies if h not in qualified]}
    payload = {'model': cfg['model'], 'response_format': {'type': 'json_object'},
               'max_tokens': 2200, 'messages': [
        {'role': 'system', 'content': '你是王者荣耀教练，根据整套阵容为指定玩家生成中文开局预案。'
         '摘要必须说明本人英雄、本局具体敌方威胁和队友配合，禁止只套分路模板。'
         'sections覆盖开局、对线换血、支援、团战、资源转换和逆风，给出条件、行动与理由。每节不超过80字，摘要不超过120字。'
         'enemy_risks仅列敌方英雄；ally_coordination仅列本人以外的己方英雄。'
         '对方分路未知，不认定对线对象；没有实时位置、经济、技能状态，不编造已发生事件或版本数值。'
         '不得猜红开蓝开或默认存在辅助；提到具体对线英雄必须写明如果实际对线是该英雄。'
         '仅使用提供的机制资料支持技能效果，不自行增加技能编号、升级顺序或召唤师技能。英雄用官方全名，不用猴信等缩写。'
         'evidence_ids列出实际引用的依据编号。没有已审核机制时只给有条件的发育、支援、站位与资源预案，明确机制资料不足；不补写英雄技能效果、强弱或克制结论。'
         '机制资料是草案，缺失时保守表述；不承诺输赢，不添加阵容外英雄。'
         '只返回符合此JSON schema的对象：' + json.dumps(ExternalPlan.model_json_schema(), ensure_ascii=False)},
        {'role': 'user', 'content': json.dumps(facts, ensure_ascii=False)}]}
    if cfg.get('provider') == 'deepseek':
        payload['thinking'] = {'type': 'disabled'}
    elif cfg.get('provider') == 'qwen':
        payload['enable_thinking'] = False
    try:
        response = post_json(cfg['base'].rstrip('/') + '/chat/completions', payload, 75,
                             {'Authorization': 'Bearer ' + cfg['key']})
        choice = response['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise ValueError('incomplete output')
        result = ExternalPlan.model_validate_json(choice['message']['content']).model_dump()
        if not set(result['evidence_ids']) <= {item['id'] for item in evidence}:
            raise ValueError('unknown evidence reference')
        for field, allowed in [('enemy_risks', enemies), ('ally_coordination', [h for h in allies if h != player])]:
            names = [item['hero'] for item in result[field]]
            if len(names) != len(set(names)) or not set(names) <= set(allowed):
                raise ValueError('invalid team references')
        prose = result['summary'] + ''.join(s['text'] for s in result['sections'])
        all_text = json.dumps(result, ensure_ascii=False)
        if not qualified and re.search(r'[一二三四1234]\s*技能|[一二三四1234]级|到四|开大|大招|闪现|惩击|净化|护盾|韧性装备|红开|蓝开', all_text):
            raise ValueError('unreviewed mechanical detail')
        if any(h in all_text for h in KNOWN_HEROES - set(allies + enemies)):
            raise ValueError('unknown roster reference')
        if player not in prose or not any(h in result['summary'] for h in enemies):
            raise ValueError('not personalized')
        if any(s in all_text for s in ('必胜', '保证赢', '净化能解压制')):
            raise ValueError('unsupported claim')
        return {**fallback, **result, 'source': 'external_model', 'model': response.get('model') or cfg['model'],
                'evidence':[item for item in evidence if item['id'] in result['evidence_ids']],
                'knowledge_gaps':facts['knowledge_gaps'],
                'notes': ['基于已确认阵容的模型开局预案；敌方分路及实时位置、经济、技能状态未知。',
                          '当前未确认本局游戏版本；依据引用仅验证来源存在，不代表模型推论已获教练审核。',
                          '缺少合格机制资料：'+'、'.join(facts['knowledge_gaps']) if facts['knowledge_gaps'] else '机制依据附有资料版本，仍需核对本局版本。']}
    except Exception as exc:
        return {**fallback, 'fallback_reason': '外部分析未完成或结果未通过校验，当前显示本地规则建议。',
                'error_type': type(exc).__name__}

LANES = {
    '发育路': [
        ('开局发育', '先保证补刀与血量；敌方打野位置不明时保留撤退空间，不为一次消耗漏掉整波兵。'),
        ('打出对线优势', '对手补刀或关键控制交出时再消耗，打完回到安全距离。对方回城且支援位置明确时，先把兵线送进塔，让对手亏兵；回城前尽量处理好下一波线。'),
        ('支援时机', '先收安全兵线，再跟辅助或前排转线；河道没有视野时不要独自穿野区追人。'),
        ('团战怎么打', '站在保护位身后，先打最近且能安全攻击的目标。敌方切入位置不明时保留撤退路线，别为了追后排离开保护范围。'),
        ('优势换成资源', '逼退对手后先看兵线：有兵、有队友、有侧翼视野就推塔；没有兵线就补发育，再和队友一起争附近资源。'),
    ],
    '对抗路': [
        ('开局发育', '先观察双方清线与换血能力，争取补到兵并保持血量；无法安全抢线时让兵线靠近己方塔前。'),
        ('打出对线优势', '在对手补刀或关键技能交出后短换血，赚到血量就收手。能安全把兵线推进塔再回城或支援，避免追人让对手无代价补线。'),
        ('支援时机', '处理兵线后从有视野的一侧靠中；河道人数不足就回线。没有安全路线时用边线压力牵制，不强行绕后。'),
        ('团战怎么打', '先分配自己负责切入还是保护。切入要等队友能跟上，关键控制未交时不要单人冲进后排；没有进场窗口就先保护输出。'),
        ('优势换成资源', '敌人支援后先判断边塔能否安全推进；关键敌人消失就后撤，不把带线变成深入敌野追击。'),
    ],
    '中路': [
        ('开局发育', '优先处理兵线争取先行动权，同时保留应对突进的技能；无法安全清线时等队友掩护。'),
        ('打出对线优势', '利用对手补刀时的站位消耗；获得线权后先看哪一路有队友控制和人数优势，再决定游走，不盲目追求每波都抓人。'),
        ('支援时机', '清线后和辅助或打野同行，从已知安全路线支援；敌方中路先消失就发信号，不独自追进黑区。'),
        ('团战怎么打', '与前排保持能衔接技能的距离，等可靠控制或对方关键防守技能交出再集中输出；没视野时用技能探路，避免脸探草。'),
        ('优势换成资源', '抓到机会先帮队友推线，再转塔或河道资源；及时回中接下一波兵，避免一次游走亏掉持续发育。'),
    ],
    '打野': [
        ('开局发育', '先与队友确认野区资源归属，按队友保护与敌方入侵信息决定起手路线；守不住入口就撤退或在确认安全后换另一侧资源。'),
        ('打出节奏优势', '优先抓敌人压线、己方能先手且能跟进的一路。条件不足就继续刷野，不长时间蹲守丢掉下一轮资源。'),
        ('支援时机', '靠近有线权的队友再进河道；入侵前确认附近人数和退路，敌人位置不明时不单独深追。'),
        ('团战怎么打', '先观察对手控制和保护技能，队友能接战时再从侧翼进入；争资源时保留惩击并核对自身状态，不为追残血离开争夺区域。'),
        ('优势换成资源', '抓人成功后协助推线，把人数差转成塔或中立资源；队友无法跟进时先回收自己的野区，防止另一侧被反入侵。'),
    ],
    '辅助': [
        ('开局发育', '结合队友清线能力与入侵风险决定先协助谁，给核心创造安全补兵或刷野空间，不在无队友接应时独自深入。'),
        ('打出配合优势', '对手关键技能交出或己方人数占优时再尝试先手；核心容易被切时把控制留给反打，避免开得漂亮却没人能跟上。'),
        ('支援时机', '在核心兵线安全时游走，经过草丛先确认风险；发现多名敌人消失就回到需要保护的队友身边。'),
        ('团战怎么打', '开团前确认输出位距离。先手与保护不能同时兼顾时优先保住主要输出，技能交出后移动到能继续保护的位置。'),
        ('优势换成资源', '逼退对手后和队友控制推塔路线及资源入口，保护己方打野争夺；核心没到位时先占安全视野，不急着强开目标。'),
    ],
}


def build_personal_plan(allies, enemies, player, lane):
    if player not in allies or player in enemies:
        raise ValueError('请选择当前己方阵容中的英雄')
    if lane not in LANES:
        raise ValueError('请确认本局分路')
    book = {**HEROES, **SUPPLEMENT}
    info = book.get(player, {})
    mechanism = info.get('ally_plan', '该英雄专属机制资料尚未覆盖，以下先按你选择的分路安排发育和支援。')
    weakness = info.get('weakness')
    sections = [{'title':title, 'text':text} for title, text in LANES[lane]]
    sections.insert(0, {'title':f'{player}的发挥重点', 'text':mechanism + ('。自身短板：'+weakness if weakness else '')})
    sections.append({'title':'逆风如何止损', 'text':'优先守住安全兵线和可回收资源；人数不齐或关键状态不足时放弃硬接团，等队友到齐再找小范围反打。敌方深入且支援脱节时才尝试集火，不把落后变成连续单人送出机会。'})
    # Hero-specific facts are attached to explicit team labels. No language model
    # gets to reassign an ally as an enemy or invent a laning opponent.
    risks = [{'hero':h, 'text':f"敌方{h}：{book[h].get('teamfight', '先观察其位置与技能，再决定是否接近。')}"}
             for h in enemies if h in book]
    partners = [{'hero':h, 'text':f"己方{h}：{book[h].get('ally_plan', '清线后再一起行动。')}"}
                for h in allies if h != player and h in book]
    return {'source':'local_lane_rules', 'player':player, 'lane':lane,
            'allies':list(allies), 'enemies':list(enemies),
            'summary':f'{player} · {lane}：先建立安全发育与行动窗口，再把线权、人数或技能上的机会换成塔和资源。',
            'sections':sections, 'enemy_risks':risks, 'ally_coordination':partners,
            'notes':['分路采用你的选择；对方实际分路尚未确认，敌方列表不等于本路对线对象。',
                     '这是加载阶段的行动预案，进局后按实际兵线、位置、人数和技能调整。',
                     '英雄机制来自本地资料草案，未提供版本强度、胜率或固定装备数值。']}
