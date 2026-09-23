import os
from datetime import datetime
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

from gameplan.core.storage import DATA

LABELS = {"engage": "接团", "kite": "拉扯", "retreat": "撤退", "trade": "换资源", "push_lane": "推线", "convert_resource": "转资源", "unknown": "未确认"}


def review(result, records):
    verified = [record for record in records if record.get("execution") in ("matched", "different")]
    cause = "仅有结算或局部观察，不能确认整局胜负手；重点核对资源转化。"
    best = "缺少过程证据，暂不评定最佳行为；KDA不能代替角色完成度。"
    if result.get("towers"):
        best = f"登记推塔{result['towers']}座，已有推塔贡献；还需结合兵线和推进时机。"
    elif result.get("objectives"):
        best = f"登记中立目标{result['objectives']}次，已有资源贡献；不据此推断全部指挥正确。"
    issue = "尚无有效期内的高置信观察记录，不能判断是否执行建议。"
    if verified:
        record = verified[0]
        cause = f"局部记录：建议{LABELS[record['decision']]}，观察到{LABELS[record['observed_action']]}；不由单次结果判断整局。"
        issue = record["review"]
    training = "下局记录一次目标前推中线、到场和转资源的动作与结果。"
    sections = [
        {"title": "本局胜负手", "text": cause},
        {"title": "做得最好", "text": best},
        {"title": "最大问题 / 待验证", "text": issue},
        {"title": "下局训练目标", "text": training},
    ]
    return {
        "sections": sections, "report": "\n".join(f"【{section['title']}】{section['text']}" for section in sections),
        "confidence": "中" if verified else "低", "observations": len(verified),
        "evidence_note": "人工登记数据与可观察动作，不推断主观意图；无数据不编造MVP、伤害或胜负原因。",
    }


def font(size):
    candidates = [
        os.getenv("COACH_FONT", ""), "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    raise RuntimeError("缺少中文字体，请设置 COACH_FONT 为中文字体路径")


def wrap(draw, text, face, width):
    lines, line = [], ""
    for char in text:
        if char == "\n" or (draw.textlength(line + char, font=face) > width and char not in "。，；！？、：）】"):
            lines.append(line)
            line = "" if char == "\n" else char
        else:
            line += char
    if line:
        lines.append(line)
    return lines


def render(report):
    folder = DATA / "reports"
    folder.mkdir(exist_ok=True)
    body_font, title_font, small_font = font(28), font(46), font(22)
    picture = Image.new("RGB", (1000, 1650), "#0b1420")
    draw = ImageDraw.Draw(picture)
    draw.rectangle((0, 0, 1000, 14), fill="#65e6be")
    draw.text((60, 58), "GAMEPLAN / " + ("示例战报 · 非真实对局" if report.get("is_demo") else "赛后训练档案"), font=small_font, fill="#65e6be")
    result = report["result"]
    draw.text((60, 110), result["result"] + " · " + (result.get("hero") or "本局复盘"), font=title_font, fill="#f3f6fa")
    draw.text((60, 188), "K / D / A   " + result["kda"], font=body_font, fill="#d2dbe7")
    towers = result.get("towers")
    objectives = result.get("objectives")
    draw.text((60, 240), f"推塔 {towers if towers is not None else '未提供'}     目标 {objectives if objectives is not None else '未提供'}     置信度 {report['confidence']}", font=small_font, fill="#a6b6ca")
    position = 310
    for index, section in enumerate(report["sections"], 1):
        lines = wrap(draw, section["text"], body_font, 810)
        height = 85 + len(lines) * 42
        draw.rounded_rectangle((50, position, 950, position + height), radius=18, fill="#142236")
        draw.text((80, position + 18), f"0{index}  " + section["title"], font=small_font, fill="#65e6be")
        for line_index, line in enumerate(lines):
            draw.text((80, position + 62 + line_index * 42), line, font=body_font, fill="#e4ebf5")
        position += height + 20
    qr = qrcode.QRCode(box_size=7, border=4)
    qr.add_data(report["share_url"])
    qr.make(fit=True)
    qr_picture = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    qr_picture.save(folder / (report["id"] + "-qr.png"))
    qr_picture = qr_picture.resize((230, 230), Image.Resampling.NEAREST)
    picture.paste(qr_picture, (660, position + 20))
    draw.text((60, position + 30), "扫码查看 / 保存本局训练卡", font=body_font, fill="#65e6be")
    for index, line in enumerate(wrap(draw, "同一局域网访问；转发链接即分享此战报。", small_font, 530)):
        draw.text((60, position + 86 + index * 36), line, font=small_font, fill="#b5c4d7")
    draw.text((60, position + 180), datetime.now().strftime("%Y.%m.%d") + "  ·  无语音 / 不代操作", font=small_font, fill="#8396ad")
    final_height = position + 280
    picture.crop((0, 0, 1000, final_height)).save(folder / (report["id"] + ".png"))
