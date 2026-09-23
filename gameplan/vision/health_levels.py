"""Magnify a bound hero's circular level badge when full-frame OCR misses it.

This reads only a number. The caller must already know which hero owns the
nameplate; a level on a random unit must never unlock all enemy ultimates.
"""
import re

import cv2
import numpy as np
from PIL import Image, ImageOps

from gameplan.vision.hero_recognition import OCR_LOCK, ocr_engine


def badge_number(picture, name_box):
    box=np.asarray(name_box)
    cx=float(box[:,0].mean());bottom=float(box[:,1].max())
    left=max(0,round(cx-98));top=max(0,round(bottom-7))
    right=min(picture.width,round(cx-20));lower=min(picture.height,round(bottom+36))
    if right-left<12 or lower-top<12:
        return None
    region=np.asarray(picture.convert('RGB'))[top:lower,left:right]
    hsv=cv2.cvtColor(region,cv2.COLOR_RGB2HSV)
    # The level number is inside a gold/cream circular rim. Red annotations,
    # damage text and horizontal health-bar edges do not form this shape.
    rim=cv2.inRange(hsv,np.array([15,30,80]),np.array([45,255,255]))
    contours,_=cv2.findContours(rim,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    badges=[]
    for contour in contours:
        x,y,w,h=cv2.boundingRect(contour)
        if not (10<=w<=38 and 10<=h<=38 and .75<=w/h<=1.35 and cv2.contourArea(contour)>=10):
            continue
        bx,by=left+x+w/2,top+y+h/2
        if not (24<=cx-bx<=90 and 0<=by-bottom<=30):
            continue
        radius=min(w,h)/2
        # Exclude the rim; otherwise OCR reads the surrounding circle as a zero.
        half=round(radius*2/3);px,py=round(bx),round(by)
        crop=picture.crop((px-half,py-half,px+half,py+half)).convert('L')
        if min(crop.size)<6:
            continue
        readings=[]
        with OCR_LOCK:
            for threshold in (100,125):
                glyph=crop.point(lambda value:255 if value>threshold else 0)
                ink=np.asarray(glyph)>0
                if not .06<ink.mean()<.65:
                    break
                glyph=ImageOps.expand(ImageOps.invert(glyph),border=max(4,crop.width//3),fill=255)
                glyph=glyph.resize((190,190),Image.Resampling.NEAREST)
                result,_=ocr_engine()(np.asarray(glyph),use_det=False,use_cls=False)
                if not result or len(result)!=1:
                    break
                text,score=result[0]
                if not re.fullmatch(r'(?:[1-9]|1[0-5])',text) or score<.92:
                    break
                readings.append((text,float(score)))
        if len(readings)==2 and readings[0][0]==readings[1][0]:
            badges.append({'visible_text':readings[0][0],'level':int(readings[0][0]),
                           'confidence':min(score for _,score in readings)})
    return badges[0] if len(badges)==1 else None
