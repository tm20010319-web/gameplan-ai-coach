"""Merge completed video runs and provenance-preserving AI visual reviews."""
import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def finalize(root):
    expected=read(root/'annotations.json')
    expected_keys=[hashlib.sha256(json.dumps(item,sort_keys=True).encode()).hexdigest()[:16] for item in expected]
    rows=[];identities={}
    for folder in sorted(root.glob('auto-run-*')):
        if not folder.is_dir():continue
        rows.extend(read(folder/'auto-annotations.json'))
        identities.update(read(folder/'auto-identities.json'))
    indexed={row['key']:row for row in rows}
    if len(indexed)!=len(rows) or set(indexed)!=set(expected_keys):
        raise ValueError('The completed runs do not exactly cover the input windows')
    rows=[indexed[key] for key in expected_keys]
    write(root/'qwen-automatic-results.json',rows)
    review_path=root/'assistant-visual-reviews.json'
    reviews=read(review_path) if review_path.exists() else []
    for review in reviews:
        row=indexed[review['key']]
        if row['video_id']!=review['video_id'] or abs(row['start_s']-review['start_s'])>.01:
            raise ValueError('Visual review no longer matches its source interval')
        row.setdefault('automatic_result',{'label':row['label'],'hero':row['hero'],
                    'events':copy.deepcopy(row['events']),'status':row['status']})
        stamp=row['start_s']+(row['end_s']-row['start_s'])*review['frame_index']/5
        previous=stamp-(row['end_s']-row['start_s'])/5
        event={'hero':review['hero'],'label':review['label'],'evidence':review['evidence'],
               'source':'assistant_visual_review','human_verified':False,
               'reason':'AI_image_review_not_human_ground_truth','training_eligible':True,
               'event_time_s':stamp if review['label']=='cast_start' else None,
               'observed_at_s':stamp,
               'onset_interval_s':[previous,stamp] if review['label']=='cast_start' else None}
        row['events']=[e for e in row['events'] if e['hero']!=review['hero']]+[event]
        row.update(label=review['clip_label'],hero=review['hero'],source='assistant_visual_review',
                   status='assistant_reviewed',human_verified=False,
                   reason='AI_visual_review_overrides_local_model; see automatic_result')
    # Systematic false negatives were observed in Qwen's backgrounds. Retain its
    # proposals for audit, but don't feed unchecked negative labels to training.
    for row in rows:
        reviewed=row.get('source')=='assistant_visual_review'
        row['training_eligible']=reviewed and row['label'] in ('cast_start','ongoing','background')
        if not reviewed and row['label']=='background':
            row['training_exclusion']='local_model_false_negatives_detected; not independently_reviewed'
        for event in row['events']:
            event['training_eligible']=event.get('source')=='assistant_visual_review' and event['label']!='unknown'
    write(root/'auto-annotations.json',rows)
    write(root/'auto-identities.json',identities)
    training=[r for r in rows if r['training_eligible']]
    write(root/'auto-training.json',training)
    heroes=sorted({h for identity in identities.values() for h in identity['enemy_roster']})
    by_hero={h:dict(Counter(e['label'] for r in rows for e in r['events'] if e['hero']==h)) for h in heroes}
    positive=[{**e,'video_id':r['video_id'],'window_s':[r['start_s'],r['end_s']],
               'evidence_image':r['evidence_image']} for r in rows for e in r['events'] if e['label'] in ('cast_start','ongoing')]
    write(root/'auto-events.json',positive)
    report={'windows':len(rows),'unique_enemy_heroes':len(heroes),'enemies':heroes,
            'status_counts':dict(Counter(r['status'] for r in rows)),
            'clip_label_counts':dict(Counter(r['label'] for r in rows)),
            'training_counts':dict(Counter(r['label'] for r in training)),
            'positive_hero_windows':len(positive),'hero_event_counts':by_hero,
            'visual_review_count':len(reviews),'human_verified':False,'real_match_accuracy':None,
            'coverage':'202 motion candidates; not all frames or all casts',
            'model_status':'data_readiness_pending; no production model enabled'}
    write(root/'auto-report.json',report)
    lines=['# 全自动标注结果','',f'已处理 {len(rows)} / {len(expected)} 个候选窗口，两局合计 {len(heroes)} 种敌方英雄。',
           '', '这些窗口来自约 38 分钟录像的运动变化筛选，不是整场逐帧标注；不能计算整局召回率。',
           '',f'窗口标签：`{report["clip_label_counts"]}`。',
           f'AI 图像复核补充/更正 {len(reviews)} 个英雄片段；正向英雄片段共 {len(positive)} 条，邻近片段可能属于同一次释放。',
           '', 'Qwen 对敖隐化龙和高渐离领域出现明显假阴性，原始模型标签保存在 qwen-automatic-results.json。',
           '已复核结论包含在 auto-annotations.json 的事件字段，原始提议仍保留；未知不会当负样本。',
           '未经进一步图像复核的 Qwen 背景标签不进入训练集。所有标签均为 AI 弱标签，没有人工真值验证。',
           '', '| 英雄 | 起手片段 | 持续片段 | 背景提议 | 未知 |', '| --- | ---: | ---: | ---: | ---: |']
    for hero in heroes:
        counts=by_hero[hero];lines.append('| '+hero+' | '+' | '.join(str(counts.get(label,0)) for label in ('cast_start','ongoing','background','unknown'))+' |')
    lines+=['','起手区间记录的是图像中可见动作变化，不是精确按键时刻；持续状态不能反推出起手，也不会触发新的冷却计时。',
            '', 'auto-training.json 仅为实验训练输入；训练检查结果见 training-readiness.json。',
            '现有模型骨架是整屏三分类，不等于已完成逐英雄大招识别。当前没有将实验标签接入实战计时。']
    (root/'自动标注结果.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('dataset',type=Path)
    finalize(parser.parse_args().dataset)
