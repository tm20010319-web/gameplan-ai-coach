"""Geometry-constrained loading icons, confirmed across three observations."""
import cv2
import numpy as np
import io
from PIL import Image
from gameplan.vision.summoner_icons import templates


def rank_icon(region, *, smooth=False):
    if smooth:
        # Apply the same mild low-pass filter to both sides to reduce aliasing
        # of compressed 14-pixel glyphs. The unfiltered match is still required.
        region=cv2.GaussianBlur(region,(3,3),.8)
    scores=[]
    for skill,original in templates():
        if original is None:continue
        best=(-1,0)
        for size in range(12,25):
            icon=cv2.resize(original,(size,size),interpolation=cv2.INTER_AREA)
            if smooth:icon=cv2.GaussianBlur(icon,(3,3),.8)
            mask=np.zeros((size,size),np.uint8);cv2.circle(mask,(size//2,size//2),round(size*.4),255,-1)
            if min(region.shape[:2])<size:continue
            values=cv2.matchTemplate(region,icon,cv2.TM_CCOEFF_NORMED,mask=mask)
            yy,xx=np.indices(values.shape)
            centered=(xx+size/2-region.shape[1]/2)**2+(yy+size/2-region.shape[0]/2)**2<=9
            values=np.where(centered & np.isfinite(values),values,-1)
            _,score,_,loc=cv2.minMaxLoc(values)
            if score>best[0]:
                patch=region[loc[1]:loc[1]+size,loc[0]:loc[0]+size]
                a=np.mean(patch[mask>0],axis=0);b=np.mean(icon[mask>0],axis=0)
                color=float(np.dot(a,b)/max(1,np.linalg.norm(a)*np.linalg.norm(b)))
                best=(score,color)
        scores.append((best[0],skill,best[1]))
    scores.sort(reverse=True)
    return {'skill':scores[0][1],'score':scores[0][0],'color':scores[0][2],'margin':scores[0][0]-scores[1][0]}


def accepted_scores(score, margin, color, smoothed_score=None, smoothed_margin=None):
    if color is None or color < .98:
        return False
    if score >= .85 and (margin or 0) >= .12:
        return True
    return (score >= .80 and (margin or 0) >= .18 and
            (smoothed_score or 0) >= .88 and (smoothed_margin or 0) >= .18)


def verify(picture,cards):
    rgb=cv2.cvtColor(np.asarray(picture),cv2.COLOR_RGB2BGR)
    result=[]
    for card in cards:
        if not card.get('hero'):
            continue
        # Center the search on the icon, not its lower rim; the old .05 offset
        # put real glyphs at the edge of the gate and nickname OCR jitter lost them.
        cx,cy=card['x']+picture.width*.042,card['y']+picture.height*.046
        box=(max(0,round(cx-16)),max(0,round(cy-16)),round(cx+16),round(cy+16))
        region=rgb[box[1]:box[3],box[0]:box[2]]
        if region.shape[:2]!=(32,32):continue
        ranked=rank_icon(region)
        extra={}
        if not accepted_scores(ranked['score'],ranked['margin'],ranked['color']):
            if ranked['score']<.80 or ranked['margin']<.18 or ranked['color']<.98:continue
            smoothed=rank_icon(region,smooth=True)
            if smoothed['skill']!=ranked['skill'] or smoothed['color']<.98:continue
            extra={'smoothed_score':smoothed['score'],'smoothed_margin':smoothed['margin']}
            if not accepted_scores(ranked['score'],ranked['margin'],ranked['color'],**extra):continue
        result.append({'hero':card['hero'],'skill':{'晕眩':'眩晕'}.get(ranked['skill'],ranked['skill']),
                       'confidence':ranked['score'],'template_margin':ranked['margin'],'color_score':ranked['color'],
                       **extra,'source':'loading_icon','frame_index':0,
                       'evidence':'加载卡片中心图标形状与颜色比对'+('；原图与平滑图匹配一致' if extra else '')+'，须连续三次一致；分数并非准确率'})
    return result


def verify_sequence(req, picture, cards):
    """Check buffered loading frames only where the same hero label is visible.

    Card identity comes from the current OCR. Earlier labels must closely match
    those exact pixels before reusing that identity; BP/gameplay/different-card
    frames and unreadable labels cannot build the confirmation streak.
    """
    from gameplan.ai.integrations import image_bytes
    from gameplan.vision.hero_recognition import read_text, name_in_label
    current = np.asarray(picture)
    result = []
    for index, frame in enumerate(req.recent_frames):
        try:
            raw, prepared = image_bytes(req.model_copy(update={"image_base64": frame.image_base64, "recent_frames": []}))
            previous = Image.open(io.BytesIO(prepared if req.roi else raw)).convert('RGB')
            previous = previous.resize((round(previous.width*576/previous.height),576))
            if previous.size != picture.size:
                continue
            pixels = np.asarray(previous)
            stable = []
            for card in cards:
                box = card.get('label_box')
                if not box:
                    continue
                left, top, right, bottom = [round(v) for v in box]
                expected = current[top:bottom,left:right]
                observed = pixels[top:bottom,left:right]
                if expected.size == 0 or expected.std() < 10:
                    continue
                similarity = float(cv2.matchTemplate(observed,expected,cv2.TM_CCOEFF_NORMED)[0,0])
                difference = float(np.abs(observed.astype(float)-expected).mean())
                matched = np.isfinite(similarity) and similarity >= .96 and difference <= 12
                # Skin animation can alter the background behind a name line.
                # In that case require a fresh reading of this same card's hero
                # label instead of relaxing pixel identity matching.
                if not matched and np.isfinite(similarity) and similarity >= .7 and difference <= 30:
                    readings = read_text(previous.crop((max(0,left-2),max(0,top-2),right+2,bottom+2)))
                    matched = any(r['score'] >= .9 and name_in_label(r['text'],[card['hero']]) == card['hero'] for r in readings)
                if matched:
                    stable.append(card)
            result.extend({**r,'frame_index':index} for r in verify(previous,stable))
        except (OSError, ValueError):
            continue
    result.extend({**r,'frame_index':len(req.recent_frames)} for r in verify(picture,cards))
    return result
