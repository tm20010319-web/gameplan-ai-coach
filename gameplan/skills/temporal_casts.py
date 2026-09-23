"""One grounded Qwen request for all targets over the complete submitted sequence."""
import base64
import io
import json
import os
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw

from gameplan.skills.combat_evidence import read_scene
from gameplan.knowledge.skill_knowledge import _catalog
from gameplan.skills.monitor_policy import recognition_reference, suspect


def detect_sequence(req, scene, *, bindings, enemies, known_heroes, equipped=None, budget_s=6, scene_cache=None, track_memory=None):
    from gameplan.skills.grounded_casts import final_json, frame_index
    from gameplan.ai.integrations import image_bytes, post_json
    result = {"enemy_skill_events": [], "activity": [], "hero_levels": [], "status": "waiting_identity"}
    if not bindings:
        return result
    started = time.monotonic()
    samples = [*req.recent_frames, req]
    frames = []
    for index, sample in enumerate(samples):
        raw, prepared = image_bytes(req.model_copy(update={"image_base64": sample.image_base64, "recent_frames": []}))
        pixels = prepared if req.roi else raw
        if frames and pixels == frames[-1]["pixels"]:
            frames[-1]["original_index"] = index
            frames[-1]["time"] = sample.captured_at
            continue
        picture = Image.open(io.BytesIO(pixels)).convert("RGB")
        normalized = picture.resize((round(picture.width*576/picture.height),576))
        frames.append({"pixels": pixels, "picture": picture, "gray": cv2.cvtColor(np.asarray(normalized),cv2.COLOR_RGB2GRAY),
                       "original_index": index, "time": sample.captured_at, "targets": []})
    if len(frames) < 2:
        result["status"] = "unchanged"
        return result
    def read(pixels):
        return read_scene(pixels,known_heroes,bindings,**({'cache':scene_cache} if scene_cache is not None else {}))
    from gameplan.skills.combat_tracks import advance
    identity_memory = track_memory if track_memory is not None else {}
    for index, frame in enumerate(frames):
        observation = scene if index == len(frames)-1 else read(frame['pixels'])
        if observation and not observation.get('panel') and observation.get('phase') != 'loading':
            result['hero_levels'].extend({**level, 'frame_index': frame['original_index']}
                                        for level in observation.get('hero_levels', []))
        frame['targets'] = advance(frame['picture'], observation, frame['time'],
            bindings=bindings, enemies=enemies, memory=identity_memory)
    heroes = list(dict.fromkeys(t["hero"] for f in frames for t in f["targets"]))
    catalog = {h["hero"]:h for h in _catalog().get("heroes",[])}
    references = []
    for hero in heroes:
        ultimate = next((s for s in catalog.get(hero,{}).get("skills",[]) if s.get("is_ultimate")),None)
        if ultimate:
            knowledge = recognition_reference(hero) or {}
            references.append({"target_id":len(references),"hero":hero,"ultimate":ultimate["name"],
                               "mechanic":knowledge.get('mechanic', ultimate.get("description", "")[:150]),
                               "other_skills":knowledge.get('other_skills', []),
                               "flash_equipped":(equipped or {}).get(hero)=="闪现"})
    if not references:
        result["status"] = "no_visible_target"
        return result
    ids = {r["hero"]:r["target_id"] for r in references}
    pictures = []
    tiles = []
    for number,frame in enumerate(frames):
        picture = frame["picture"].copy()
        left=top=0;scale_x=picture.width;scale_y=picture.height
        if len(references)==1:
            # Center the bound actor so an ally's larger effect elsewhere in the
            # screen cannot dominate this hero's classification. Nearby known
            # positions may frame an occluded actor, but do not establish identity.
            hero=references[0]['hero']
            sightings=[(abs(f['time']-frame['time']),t) for f in frames for t in f['targets']
                       if t['hero']==hero and abs(f['time']-frame['time'])<=.8]
            if sightings:
                target=min(sightings,key=lambda item:item[0])[1]
                x,y=target['x']*picture.width,target['y']*picture.height
                unit=picture.height/576
                left,top=max(0,round(x-165*unit)),max(0,round(y-40*unit))
                right,bottom=min(picture.width,round(x+165*unit)),min(picture.height,round(y+250*unit))
                picture=picture.crop((left,top,right,bottom))
        original_width,original_height=picture.size
        picture.thumbnail((960,540))
        draw = ImageDraw.Draw(picture)
        for target in frame["targets"]:
            if target["hero"] not in ids:
                continue
            x=round((target['x']*scale_x-left)*picture.width/original_width)
            y=round((target['y']*scale_y-top)*picture.height/original_height)
            draw.rectangle((x-40,y-10,x+40,y+8),outline=(255,60,180),width=2)
            draw.text((x-39,y-24),f'T{ids[target["hero"]]}',fill=(255,60,180),stroke_width=1,stroke_fill=(0,0,0))
        tiles.append(picture)
    # Qwen's vision encoder expands even small images to ~1024 tokens. Pack
    # neighboring frames in pairs for long sequences instead of silently dropping
    # half the timeline or overflowing the time/context budget with eight images.
    grid = not req.all_frames and req.input_kind in ('live', 'video') and 2 <= len(tiles) <= 6
    packed = not grid and len(tiles) > 4
    group_size = len(tiles) if grid else 2 if packed else 1
    for index in range(0,len(tiles),group_size):
        group=tiles[index:index+group_size]
        if grid:
            # Keep each tile's original detail, but pack each row separately.
            # One unbound full-screen frame must not expand every cropped tile
            # to full-screen dimensions with expensive empty image tokens.
            rows=[group[offset:offset+2] for offset in range(0,len(group),2)]
            width=max(sum(tile.width for tile in row) for row in rows)
            heights=[max(tile.height for tile in row)+24 for row in rows]
            picture=Image.new('RGB',(width,sum(heights)),(20,20,20))
            draw=ImageDraw.Draw(picture)
            y=0
            for row_number,(row,height) in enumerate(zip(rows,heights)):
                x=0
                for column,tile in enumerate(row):
                    offset=row_number*2+column
                    draw.text((x+8,y+4),f'F{index+offset} t={frames[index+offset]["time"]-frames[0]["time"]:.2f}s',fill='white')
                    picture.paste(tile,(x,y+24));x+=tile.width
                y+=height
        elif packed:
            width=max(p.width for p in group);height=sum(p.height+24 for p in group)
            picture=Image.new('RGB',(width,height),(20,20,20));draw=ImageDraw.Draw(picture);top=0
            for offset,tile in enumerate(group):
                draw.text((8,top+4),f'F{index+offset}   t={frames[index+offset]["time"]-frames[0]["time"]:.2f}s',fill='white')
                picture.paste(tile,(0,top+24));top+=tile.height+24
        else:
            picture=group[0]
        buffer=io.BytesIO();picture.save(buffer,'JPEG',quality=90)
        pictures.append(base64.b64encode(buffer.getvalue()).decode())
    prompt = (
        "比较按时间顺序排列的王者荣耀画面。粉色T编号框仅标出本地已核对身份的角色名字位置，不是释放证据。"
        "只判断这些目标的敌方大招或闪现，不把玩家、队友、野怪或其他英雄的特效归给目标。"
        "跟随框下方角色的连续动作，不能仅凭相似特效猜英雄。"
        "只输出JSON {\"events\":[{\"target_id\":0,\"kind\":\"ultimate或flash\",\"state\":\"cast_start或ongoing或uncertain\","
        "\"frame_index\":首次出现动作的图片索引,\"confidence\":0到1,\"evidence\":\"具体前后变化\"}]}。"
        "索引从0开始，第一帧已有的效果只能ongoing。只有明确从未释放到开始释放才cast_start。"
        "有相关但不充分的释放证据填uncertain，没有相关变化则events为空；不能凭技能按钮、字幕或单独位移判断。"
        "flash_equipped为false的目标禁止报告flash。"
        "目标表："+json.dumps(references,ensure_ascii=False))
    if len(references) == 1:
        reference = references[0]
        sighting = next(t for f in frames for t in f['targets'] if t['hero']==reference['hero'])
        action = {'敖隐':'化为长龙腾空','孙策':'驾驶船航行、撞船击飞'}.get(reference['hero'],reference['mechanic'][:80])
        prompt = (f"比较{len(frames)}张按顺序的王者荣耀截图，检查昵称“{sighting['nickname']}”的敌方{reference['hero']}"
            f"是否开始释放{action}的技能。身份已由英雄名字对应。粉色框只辅助定位。"
            "只看战场中该角色的形态变化，不能依据自己的技能按钮、野怪或其他角色。"
            "输出JSON字段：evidence（20字以内的具体可见变化）、state（none/uncertain/ongoing/cast_start）、"
            f"frame_index（0到{len(frames)-1}，首次起手所在图片索引）、confidence（0至1）。"
            "第一帧已存在的效果只能ongoing，没有明确变化不要猜。")
        if reference['flash_equipped']:
            prompt += ("该目标另已确认携带闪现，需同时判断闪现：只有该角色出现闪现特效和瞬间短距位移，且可排除英雄自身位移与镜头移动才确认。"
                       "最终JSON须有ultimate和flash两个对象，每个对象各填写上述evidence/state/frame_index/confidence四个字段，没有释放也填写none。")
        prompt += ('用户知识库大招机制：'+reference['mechanic'][:140]+'。易混淆的其他技能：'+
                   json.dumps([{'name':s['name'],'mechanic':s['mechanic'][:45]} for s in reference['other_skills']], ensure_ascii=False)+
                   '。只出现普通攻击或上述其他技能不能确认大招；相关但不充分的变化填写uncertain并描述可见证据。')
    if packed:
        prompt += (f"注意：共{len(frames)}帧合并在{len(pictures)}张图片里，每张图片上帧在先、下帧在后。"
                   "帧标题F0、F1等是时间顺序。frame_index必须填F后的帧编号，不能填合并图片的编号。")
    if grid:
        prompt += (f'注意：{len(frames)}帧排在一张图中，按从左到右、从上到下顺序阅读F0至F{len(frames)-1}。'
                   'frame_index填F后的帧编号；不能把拼图的左右位置当成角色发生位移。')
    result.update(checks_total=len(references),checks_attempted=len(references),checks_completed=0,frames_checked=len(frames))
    remaining = budget_s-(time.monotonic()-started)
    if remaining < .5:
        result["status"] = "partial"
        return result
    incomplete = False
    try:
        response = post_json(os.getenv("OLLAMA_URL","http://127.0.0.1:11434").rstrip('/')+'/api/chat',
            {"model":req.model or os.getenv("OLLAMA_VISION_MODEL","qwen3-vl:8b-instruct"),"stream":False,"think":False,
             "keep_alive":"15m","options":{"temperature":0,"num_ctx":16384,"num_predict":700},
             "messages":[{"role":"user","content":prompt,"images":pictures}]},remaining)
        parsed=final_json(response)
        candidates=parsed.get('events')
        if len(references)==1 and not references[0]['flash_equipped'] and 'state' in parsed:
            candidates=[{**parsed,'target_id':0,'kind':'ultimate'}]
        if len(references)==1 and references[0]['flash_equipped']:
            # A complete JSON reply may omit one check. Preserve the other
            # answer through all evidence gates; omission is not a negative.
            answered = [kind for kind in ('ultimate','flash') if isinstance(parsed.get(kind),dict)
                        and parsed[kind].get('state') in ('none','uncertain','ongoing','cast_start')]
            if not answered:
                raise ValueError('Missing ultimate and flash answers')
            incomplete = len(answered) != 2
            candidates=[{**parsed[kind],'target_id':0,'kind':kind} for kind in answered]
        if not isinstance(candidates,list):
            raise ValueError('Missing events')
    except (ValueError,TypeError,OSError) as exc:
        result['status']='incomplete_answer'
        result['failure_reason']='model_timeout' if isinstance(exc,TimeoutError) else 'model_unavailable' if isinstance(exc,OSError) else 'invalid_model_answer'
        return result
    result.update(status='partial' if incomplete else 'observed',checks_completed=0 if incomplete else len(references))
    if incomplete:
        result['failure_reason']='missing_skill_answer'
    for candidate in candidates[:10]:
        if not isinstance(candidate,dict):continue
        if candidate.get('state') not in ('uncertain', 'ongoing', 'cast_start'):
            continue
        tid,index=candidate.get('target_id'),frame_index(candidate.get('frame_index'),len(frames))
        if type(tid) is not int or not 0<=tid<len(references):continue
        if index is None:
            result.setdefault('rejected_candidates',{})['invalid_frame_index']=result.get('rejected_candidates',{}).get('invalid_frame_index',0)+1
            continue
        reference=references[tid];hero=reference['hero'];kind=candidate.get('kind')
        if candidate.get('hero',hero)!=hero or kind not in ('ultimate','flash') or (kind=='flash' and not reference['flash_equipped']):continue
        try:confidence=float(candidate.get('confidence',0))
        except (TypeError,ValueError):continue
        evidence=candidate.get('evidence','')
        if not .65<=confidence<=1 or not isinstance(evidence,str) or not evidence.strip():continue
        skill = '闪现' if kind == 'flash' else reference['ultimate']
        def tentative(reason):
            suspect(result, hero=hero, skill=skill, index=frames[index]['original_index'],
                    confidence=confidence, evidence=evidence, reason=reason,event_type=candidate.get("state","uncertain"))
        if confidence < .9 or candidate.get('state') == 'uncertain':
            tentative('model_uncertain')
            continue
        if hero=='高渐离' and kind=='ultimate':
            from gameplan.skills.field_effects import large_enemy_field
            visible=[]
            for number in range(max(0,index-1),len(frames)):
                target=next((t for t in frames[number]['targets'] if t['hero']==hero),None)
                if target and large_enemy_field(frames[number]['picture'],target):
                    visible.append(number)
            supported=next((a for a,b in zip(visible,visible[1:]) if b==a+1 and frames[b]['time']-frames[a]['time']<=.8),None)
            if supported is None:
                result.setdefault('rejected_candidates',{})['large_field_unconfirmed']=result.get('rejected_candidates',{}).get('large_field_unconfirmed',0)+1
                tentative('large_field_unconfirmed')
                continue
            # The first locally corroborated area frame bounds onset; do not
            # attach an earlier generic walking animation to this later effect.
            if candidate.get('state')=='cast_start':
                index=max(index,supported)
                evidence='目标周围出现并持续的大范围敌方领域；'+evidence
        if candidate.get('state')=='cast_start' and index==0:
            # The model found an effect already present in the first frame.
            # This cannot establish onset, but must not silently disappear.
            result.setdefault('rejected_candidates',{})['onset_before_sequence']=result.get('rejected_candidates',{}).get('onset_before_sequence',0)+1
            if kind=='ultimate' and any(t['hero']==hero for t in frames[0]['targets']):
                result['activity'].append({'hero':hero,'skill':reference['ultimate'],'status':'possible_ongoing'})
                tentative('onset_before_sequence')
            continue
        if candidate.get('state')=='ongoing' and kind=='ultimate':
            result['activity'].append({'hero':hero,'skill':reference['ultimate'],'status':'possible_ongoing'})
            tentative('ongoing_onset_unknown')
        elif candidate.get('state')=='cast_start' and index>0:
            # The preceding identity must belong to this target, within a short
            # interval. A target first seen afterwards cannot establish onset.
            prior=frames[index-1]
            if frames[index]['time']-prior['time']>1.5 or not any(t['hero']==hero for t in prior['targets']):
                result.setdefault('rejected_candidates',{})['onset_identity_unconfirmed']=result.get('rejected_candidates',{}).get('onset_identity_unconfirmed',0)+1
                # A currently bound actor may already be using the ultimate.
                # Expose that uncertainty without inventing an onset or timer.
                if kind=='ultimate' and any(t['hero']==hero and t['source']=='ocr' for t in frames[index]['targets']):
                    result['activity'].append({'hero':hero,'skill':reference['ultimate'],'status':'possible_ongoing'})
                tentative('onset_identity_unconfirmed')
                continue
            if kind=='flash':
                # A real post-blink identity and camera-compensated displacement
                # are required. Transformation/disappearance alone never qualifies.
                before=next((t for t in prior['targets'] if t['hero']==hero),None)
                after=next((t for t in frames[index]['targets'] if t['hero']==hero and t['source']=='ocr'),None)
                from gameplan.skills.flash_motion import displaced
                if not before or not after or frames[index]['time']-prior['time']>.8 or not displaced(prior['gray'],frames[index]['gray'],before,after):
                    result.setdefault('rejected_candidates',{})['flash_motion_unconfirmed']=result.get('rejected_candidates',{}).get('flash_motion_unconfirmed',0)+1
                    # Disappearance/dragon transformation is not even a useful
                    # Flash alert; require a bound actor at both endpoints.
                    if before and after:
                        tentative('flash_motion_unconfirmed')
                    continue
            if hero=='敖隐' and kind=='ultimate':
                normal_form = any(t['hero']==hero and t['source']=='ocr' for t in frames[index]['targets'])
                if normal_form or not any(term in evidence for term in ('龙','腾空')):
                    result.setdefault('rejected_candidates',{})['transformation_unconfirmed']=result.get('rejected_candidates',{}).get('transformation_unconfirmed',0)+1
                    # A plainly visible normal-form identity contradicts a
                    # claim of completed dragon form, rather than supporting an
                    # actionable warning. Other ambiguity remains tentative.
                    if not normal_form:tentative('transformation_unconfirmed')
                    continue
            result['enemy_skill_events'].append({'hero':hero,'skill':'闪现' if kind=='flash' else reference['ultimate'],
                'used':True,'event_type':'cast_start','frame_index':frames[index]['original_index'],
                'onset_start_index':prior['original_index'], 'onset_end_index':frames[index]['original_index'],
                'confidence':confidence,'evidence':evidence})
    # The overview may miss a brief change in a packed tile. Use remaining time
    # to inspect one strong local change at full frame resolution. Selection is
    # based on bound actors and pixels, never replay timestamps or expected heroes.
    remaining=budget_s-(time.monotonic()-started)
    if req.input_kind != 'live' and packed and not result['enemy_skill_events'] and not result['activity'] and not result.get('suspicions') and remaining>=2:
        pairs=[]
        for left in range(len(frames)-1):
            before,after=frames[left:left+2]
            if not 0<after['time']-before['time']<=.8:continue
            for target in before['targets']:
                if target['source']!='ocr':continue
                height,width=before['gray'].shape
                x,y=round(target['x']*width),round(target['y']*height)
                box=(max(0,x-90),max(0,y-10),min(width,x+90),min(height,y+160))
                a=before['gray'][box[1]:box[3],box[0]:box[2]];b=after['gray'][box[1]:box[3],box[0]:box[2]]
                change=float(cv2.absdiff(a,b).mean()) if a.size else 0
                if change>=18:pairs.append((change,left,target['hero']))
        if pairs:
            _,left,hero=max(pairs)
            pair=frames[left:left+2];after_scene=read(pair[1]['pixels'])
            if after_scene and not after_scene['panel']:
                samples=[{'image_base64':base64.b64encode(f['pixels']).decode(),'captured_at':f['time']} for f in pair]
                # model_copy does not revalidate nested dicts, so construct the
                # request once through the schema before entering the pair path.
                subreq=type(req).model_validate({**req.model_dump(),**samples[1],'recent_frames':[samples[0]],'roi':None})
                from gameplan.skills.grounded_casts import detect
                remaining=budget_s-(time.monotonic()-started)
                detail=detect(subreq,pair[1]['pixels'],after_scene,bindings=bindings,enemies=[hero],known_heroes=known_heroes,
                              equipped=equipped,budget_s=max(0,remaining),scene_cache=scene_cache)
                for item in detail['enemy_skill_events']:
                    # The two-frame fallback caused a real false dragon onset.
                    # Keep its evidence tentative until the sequence confirms it.
                    suspect(result, hero=item['hero'], skill=item['skill'],
                            index=pair[item['frame_index']]['original_index'],
                            confidence=item.get('confidence', .9), evidence=item.get('evidence', '局部复核候选，等待连续画面确认'),
                            reason='pair_recheck_unconfirmed')
                for item in detail.get('suspicions', []):
                    result.setdefault('suspicions', []).append({**item,'frame_index':pair[item['frame_index']]['original_index']})
                for item in detail['hero_levels']:
                    result['hero_levels'].append({**item,'frame_index':pair[item['frame_index']]['original_index']})
                result['activity'].extend(detail['activity'])
                result['pair_rechecks']=1
                if detail['status'] in ('partial','incomplete_answer'):
                    result['status']='partial'
    return result
