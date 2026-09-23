"""Render inspectable model replays and a local HTML report from saved scores."""
import argparse
import html
import json
from pathlib import Path
import subprocess


def video_path(folder, row):
    return Path(row.get('path') or folder/row['file'])


def ass_time(seconds):
    n = round(seconds*100)
    return f'{n//360000}:{n//6000%60:02}:{n//100%60:02}.{n%100:02}'


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=root/'work/three-hero-experiment')
    parser.add_argument('--folder', type=Path, default=Path(r'C:\Users\Administrator\Desktop\闪现'))
    parser.add_argument('--annotations', type=Path, default=root/'data/ultimate_examples/annotations.json')
    args = parser.parse_args()
    report = json.loads((args.out/'report.json').read_text(encoding='utf-8'))
    manifest = json.loads(args.annotations.read_text(encoding='utf-8'))
    cards, table = [], []
    header = '''[Script Info]
ScriptType: v4.00+
PlayResX: 960
PlayResY: 640
WrapStyle: 2
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,Microsoft YaHei,25,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,7,20,20,12,1
Style: Body,Microsoft YaHei,23,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,7,20,20,490,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    for i, (row, result) in enumerate(zip(manifest['videos'], report['videos']), 1):
        timeline = json.loads((args.out/(Path(row['file']).stem+'.timeline.json')).read_text(encoding='utf-8'))
        lines = [header, f'Dialogue: 0,0:00:00.00,0:10:00.00,Title,,0,0,0,,训练视频回放 · {Path(row["file"]).stem} · 非独立测试\n']
        for j, item in enumerate(timeline):
            t = item['time_s']
            end = timeline[j+1]['time_s'] if j+1 < len(timeline) else t+1
            label, score = max(item['probabilities'].items(), key=lambda p: p[1])
            seen = [e for e in result['events'] if e['confirmed_s'] <= t+.001]
            status = f'{label}大招（模型分数 {score:.3f}）' if label != 'background' and score >= report['threshold'] else '未确认大招'
            record = '尚无确认记录'
            if seen:
                event = seen[-1]
                record = f"已记录 {event['hero']}：首次检测 {event['first_seen_s']:.2f}s；基础冷却 {event['knowledge']['cooldown_text']}秒"
            text = f'录像 {t:.2f}s · {status}\\N{record}\\N真实释放时刻、敌我归属、剩余冷却未确认'
            lines.append(f'Dialogue: 0,{ass_time(t)},{ass_time(end)},Body,,0,0,0,,{text}\n')
        ass = args.out/f'replay-{i}.ass'
        ass.write_text(''.join(lines), encoding='utf-8-sig')
        # Crop removes browser chrome, subtitle text and skill-button evidence.
        roi = row['roi']
        x, y, right, bottom = [round(v*s) for v,s in zip(roi,(1918,1030,1918,1030))]
        vf = (f'crop={right-x}:{bottom-y}:{x}:{y},'
              'scale=960:420:force_original_aspect_ratio=decrease,'
              f'pad=960:640:(ow-iw)/2:60:black,subtitles={ass.name}')
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                        '-i', str(video_path(args.folder, row)), '-vf', vf,
                        '-an', '-c:v', 'libx264', '-preset', 'fast', '-crf', '24',
                        '-pix_fmt', 'yuv420p', '-movflags', '+faststart', f'replay-{i}.mp4'],
                       cwd=args.out, check=True)
        summary = []
        for event in result['events']:
            summary.append(f"{event['hero']}：首次 {event['first_seen_s']:.2f}s / 确认 {event['confirmed_s']:.2f}s；知识库基础冷却 {event['knowledge']['cooldown_text']} 秒")
        evaluation = result['evaluation']
        note = f"匹配 {len(evaluation['matched'])}，漏记 {len(evaluation['missed'])}，未匹配/重复记录 {len(evaluation['unmatched_or_duplicate_predictions'])}"
        cards.append(f'<article><h2>{html.escape(row["file"])}</h2><video controls preload="metadata" src="replay-{i}.mp4"></video><p>{html.escape("；".join(summary))}</p><p>{note}</p><small>{html.escape(row["notes"])}</small></article>')
        for event in result['events']:
            table.append(f"| {event['hero']} | {event['first_seen_s']:.2f} 秒 | {event['confirmed_s']:.2f} 秒 | {event['knowledge']['cooldown_text']} 秒 |")
        print(f'Rendered replay-{i}.mp4', flush=True)
    summary = f"{report['parameters']:,} 个参数 · {report['checkpoint_bytes']/1024:.1f} KiB · CPU 单次前向中位 {report['cpu_model_only_ms']['median']:.2f} ms"
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>英雄大招增量训练报告</title>
<style>body{margin:0;background:#101622;color:#e9effa;font:16px/1.7 system-ui,"Microsoft YaHei"}main{max-width:1000px;margin:auto;padding:36px 24px}h1{font-size:32px;margin-bottom:8px}h2{font-size:23px}p{margin:10px 0}.notice{border-left:4px solid #e9bb68;padding:14px 22px;background:#272634}article{margin:28px 0;padding:24px;background:#1b2535;border-radius:16px}video{display:block;width:100%;max-height:640px;background:black}small{color:#b2c0d6}a{color:#95bdff}</style><main><h1>三英雄大招 · 首轮训练报告</h1>'''
    page += f'<p>{summary}</p><div class="notice"><strong>{len(report["videos"])} 段训练视频回放，独立测试视频 0 段。</strong><p>按当前助手标注，{sum(len(v["events"]) for v in report["videos"])} 个目标事件各记录一次。结果只证明模型能拟合这批素材，不能证明新视频、不同皮肤或真实团战的准确率。固定裁剪区域，无敌我判定。</p><p>视频分数不是经过校准的准确率。貂蝉从开头就可见大招；录像含慢放/循环，记录首次检测时间，不伪造实际释放时刻或真实倒计时。</p></div>'
    page += ''.join(cards) + '<p><a href="report.json">完整机器报告</a> · <a href="inventory.json">素材清单</a></p></main></html>'
    (args.out/'report.html').write_text(page, encoding='utf-8')
    markdown = '# 英雄大招增量训练与回放结果\n\n' + summary + '\n\n'
    markdown += '**范围：训练视频回放，独立测试 0 段。不能视为实战准确率。**\n\n'
    markdown += '| 英雄 | 首次检测（录像时间） | 确认并记录 | 知识库基础冷却 |\n|---|---|---|---|\n'+'\n'.join(table)+'\n\n'
    annotated_frames = sum(sum(row) for row in report['confusion_matrix'])
    markdown += f'按助手标注，{sum(len(v["events"]) for v in report["videos"])} 个目标事件均记录一次，无未匹配/重复记录。已标注的 {annotated_frames} 个抽样帧与标签一致；这些帧参与了训练，100% 拟合不代表泛化。貂蝉无本英雄释放前负例，首帧已在大招内，起手时间未知。\n\n'
    markdown += '训练：连续 4 帧、目标采样 8 FPS、96×64 RGB、约 6 万参数的卷积模型，CPU 训练 100 轮。推理只使用当前及过去帧，不读取视频名/英雄标签/未来画面；裁剪区域人工指定，尚无自动目标跟踪。一次事件须连续两个采样确认，持续出现不重复记，消失至少 1 秒后可重新触发。\n\n'
    markdown += '冷却从外部知识库读取，未硬编码进模型。等级、冷却缩减、敌我和游戏时钟未确认，因此不输出实际剩余冷却。当前素材多为操控者视角，不能据此验收敌方视角检测。\n\n'
    markdown += '查看 [标注回放报告](report.html)、[完整指标](report.json)、[素材清单](inventory.json)。后续流程见 [使用说明](../../docs/three-hero-ultimate-prototype.md)。\n'
    (args.out/'report.md').write_text(markdown, encoding='utf-8')


if __name__ == '__main__':
    main()
