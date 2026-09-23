"""Generate a local report without downloading or updating any source."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gameplan.knowledge.skill_knowledge import coverage, _catalog


def main():
    report = coverage()
    output = ROOT / 'docs' / 'knowledge-audit.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = ['# 知识库完整性报告', '',
             f"基础英雄 {report['heroes']} 位，已收录技能 {report['skills']} 条，技能覆盖 {report['with_skill_data']} 位，大招及冷却资料覆盖 {report['with_ultimate_cooldown']} 位；详细打法 {report['coaching_heroes']} 位，共 {report['coaching_rules']} 条。",
             '', '动态大招冷却：' + ('、'.join(report['dynamic_ultimate_cooldowns']) or '无') + '。',
             '官网冷却字段待核对：' + ('、'.join(report['ultimate_cooldown_review_required']) or '无') + '。',
             '', '“可用”表示资料结构与默认配置齐全，不表示当前版本或实战效果已核验。', '',
             '| 英雄 | 练习分路 | 打法条数 | 资料结构 | 当前版本 |', '|---|---|---:|---|---|']
    for item in report['coaching_profiles']:
        lines.append(f"| {item['hero']} | {item['practice_lane']} | {item['rules']} | {'可用' if item['reference_ready'] else '、'.join(item['issues'])} | {'已核验' if item['current_patch_verified'] else '待核验'} |")
    lines += ['', '## 待补资料', '', '基础技能缺失：' + ('、'.join(report['missing_heroes']) or '无') + '。']
    for item in report['coaching_profiles']:
        lines.append(f"- {item['hero']}：" + '；'.join(item['pending'] + item['version_conflicts']))
    lines += ['', '## 使用边界', '',
              '- 默认出装、铭文、分路是用户预设，不是本局画面确认结果。',
              '- 来源分为官网、用户文字和第三方攻略；条件未知时只展示条件参考。',
              '- 基础冷却不是剩余冷却；估算必须标注“推测”。',
              '- 小件购买顺序、逆风或针对阵容换装不在本次范围。',
              '- 抖音视频与未读取的贴吧帖子继续待整理；未审核原文不进入建议。',
              '- 详细打法仅覆盖上述五位，其他英雄不冒充已有完整攻略。']
    (ROOT / 'docs' / 'knowledge-audit.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    index = ['# 全英雄技能与大招冷却索引', '',
             f"来源：腾讯官网制胜宝典；目录包含 {report['heroes']} 个英雄条目（含分支/命格），共 {report['skills']} 条被动、主动和附加技能。",
             f"采集批次：{report['fetched_at']}。完整技能介绍、原式、来源及采集时间存于 `resources/knowledge/skill_catalog.json` 和 `data/hero_knowledge.sqlite3`。", '',
             '表中为各技能等级的基础冷却，单位秒，不能视为本局剩余冷却。`Lv` 原式按1级基值和每次升级递减量展开。官网没有提供明确的当前版本号。', '',
             '朵莉亚按「10 / 9 / 8 + 1.2 × 目标技能当前冷却」保存动态公式；鲁班大师的官网技能字段存在冲突，保存原值及历史来源，禁止直接用于倒计时。', '',
             '| 英雄 | 大招 | 基础冷却 / 原式 | 已收录技能 |', '|---|---|---|---|']
    for hero in _catalog()['heroes']:
        skills = hero.get('skills', [])
        ultimate = next((s for s in skills if s.get('is_ultimate', s['slot'] == 3)), None)
        if ultimate:
            values = ultimate.get('base_cooldowns_s', [])
            cd = ' / '.join(f'{v:g}' for v in values) if values else ultimate['cooldown_text']
            if ultimate.get('cooldown_review_required'):
                cd = '待核对（官网原值：' + ultimate['cooldown_text'] + '）'
            name = ultimate['name']
        else:
            cd, name = '待补', '待补'
        index.append(f"| [{hero['hero']}]({hero['source_url']}) | {name} | {cd} | " + '、'.join(s['name'].replace('|', '\\|') for s in skills) + ' |')
    (ROOT / 'docs' / '英雄技能与大招冷却索引.md').write_text('\n'.join(index) + '\n', encoding='utf-8')
    print(json.dumps({'heroes': report['heroes'], 'skills': report['with_skill_data'],
                      'profiles': report['coaching_heroes'], 'rules': report['coaching_rules'],
                      'issues': [p for p in report['coaching_profiles'] if p['issues']]}, ensure_ascii=False))
    return int(any(p['issues'] for p in report['coaching_profiles']))


if __name__ == '__main__':
    raise SystemExit(main())
