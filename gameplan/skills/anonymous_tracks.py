"""Track unnamed bar/body candidates; never attach a hero name or infer a cast."""
from collections import OrderedDict
from copy import deepcopy
import hashlib
import math

import cv2
import numpy as np

from gameplan.skills.combat_evidence import normalized_name
from gameplan.vision.enemy_bars import enemy_bars


def _candidates(picture, readings, bindings, enemies):
    pixels=np.asarray(picture)
    dark=pixels.max(axis=2)<80
    result=[]
    for x,y,w,h in enemy_bars(picture):
        # Anonymous evidence needs an actual bordered, bright health strip.
        # Clothes and skill icons also contain horizontal red contours.
        if not (45<=w<=145 and 3<=h<=12 and w>=6*h and 50<=y<450):
            continue
        top=float(dark[y-3:y,x:x+w].mean())
        bottom=float(dark[y+h:y+h+3,x:x+w].mean())
        if min(top,bottom)<.25 or max(top,bottom)<.4:
            continue
        if (pixels[y:y+h,x:x+w].max(axis=2)>110).mean()<.65:
            continue
        # A damaged tower has a short red fill inside a much longer empty
        # health container. Follow its contiguous dark tail, not fill width.
        interior=pixels[y+1:y+h-1,x:min(picture.width,x+240)]
        empty=(interior.max(axis=2)<80).mean(axis=0)>=.65
        tail_end=w
        for start in range(w,min(w+17,len(empty)-4)):
            if empty[start:start+5].all():
                tail_end=start+5
                while tail_end<len(empty) and empty[tail_end]:
                    tail_end+=1
                break
        if tail_end>145:
            continue
        contradicted=False
        for reading in readings or []:
            box=np.asarray(reading.get('box',[]))
            if reading.get('score',0)<.85 or box.shape!=(4,2):
                continue
            cx,cy=box.mean(axis=0)
            if not (x-15<=cx<=x+w+130 and y-60<=cy<y):
                continue
            name=normalized_name(reading.get('text',''))
            if ((name.isdecimal() and int(name)>15) or any(
                    b.get('nickname') and normalized_name(b['nickname'])==name and
                    (b.get('side')=='ally_roster' or b.get('hero') not in enemies) for b in bindings)):
                contradicted=True
        if contradicted:
            continue
        body=pixels[y+h+8:min(576,y+125),max(0,x-8):min(picture.width,x+115)]
        gray=cv2.cvtColor(body,cv2.COLOR_RGB2GRAY)
        if gray.std()<8:
            continue
        spatial=cv2.resize(gray,(20,20),interpolation=cv2.INTER_AREA).astype(float).ravel()
        spatial-=spatial.mean()
        spatial/=max(float(np.linalg.norm(spatial)),1e-6)
        hsv=cv2.cvtColor(body,cv2.COLOR_RGB2HSV)
        colors=cv2.calcHist([hsv],[0,1],None,[12,4],[0,180,0,256]).ravel()
        colors/=max(float(colors.sum()),1)
        result.append({'bar':[x,y,w,h],'spatial':spatial,'colors':colors})
    return result


def advance(picture, observation, captured_at, *, bindings, enemies, memory):
    """Three distinct source frames support tentative review, including F0.

    History stores positions only. A stale frame, gap, ambiguity or scene change
    cannot manufacture/renew a named identity. Matching is one-to-one.
    """
    picture=picture.convert('RGB').resize((round(picture.width*576/picture.height),576))
    digest=hashlib.sha256(picture.tobytes()).digest()
    key=(captured_at,digest)
    empty={'key':key,'supported':[]}
    signature=(tuple(sorted(enemies)),tuple(sorted(
        (b.get('hero',''),b.get('nickname',''),b.get('side','')) for b in bindings)))
    if memory.get('signature')!=signature:
        memory.clear();memory.update(signature=signature,frames=OrderedDict())
    if key in memory['frames']:
        return deepcopy(memory['frames'][key])
    if not isinstance(captured_at,(int,float)) or not math.isfinite(captured_at) or captured_at<=0:
        return empty
    previous=memory.get('last')
    if previous and captured_at<=previous['time']:
        return empty
    valid=(observation and not observation.get('panel') and
           observation.get('phase') not in ('loading','bp','result','not_game','unknown'))
    # A repeated frozen image is not an independent temporal observation.
    if valid and previous and digest==previous['digest']:
        return empty
    current=_candidates(picture,observation.get('readings',[]),bindings,enemies) if valid else []
    gap=captured_at-previous['time'] if previous else float('inf')
    prior=previous['targets'] if previous and previous['size']==picture.size and 0<gap<=1.5 else []
    links=[]
    for i,target in enumerate(current):
        for j,old in enumerate(prior):
            distance=np.linalg.norm(np.array(target['bar'][:2])-old['bar'][:2])
            if (distance<=max(18,min(90,gap*180)) and abs(target['bar'][3]-old['bar'][3])<=3
                    and target['bar'][2]<=old['bar'][2]*1.25
                    and np.minimum(target['colors'],old['colors']).sum()>=.65
                    and np.dot(target['spatial'],old['spatial'])>=.2):
                links.append((i,j))
    supported=[]
    for i,target in enumerate(current):
        matches=[j for ci,j in links if ci==i]
        history=[]
        if len(matches)==1 and sum(j==matches[0] for _,j in links)==1:
            history=prior[matches[0]]['history'][-2:]
        target['history']=[*history,{'key':key,'bar':target['bar']}]
        if len(target['history'])==3:
            supported.extend(deepcopy(target['history']))
    result={'key':key,'supported':supported}
    memory['last']={'time':captured_at,'digest':digest,'size':picture.size,'targets':current}
    memory['frames'][key]=deepcopy(result)
    while len(memory['frames'])>32:
        memory['frames'].popitem(last=False)
    return result
