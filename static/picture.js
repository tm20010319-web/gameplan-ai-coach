"use strict";
(() => {
  const $=id=>document.getElementById(id);
  const phases={bp:"选人",loading:"加载截图",in_game:"局中截图",result:"结算",not_game:"非游戏图片",unknown:"待确认"};
  const state={session:crypto.randomUUID(),revision:0,generation:0,running:false,watching:false,busy:false,
    sources:[],roi:null,pending:null,result:null,timer:null,captureController:null,inferController:null,
    stable:0,requested:-1,retryAt:0,manual:null,started:0,planVersion:0,planBusy:false,rosterCandidate:null};
  let laneSelect=$('lane');
  if(!laneSelect){
    laneSelect=document.createElement('select'); laneSelect.id='lane';
    [['unknown','待确认'],['打野','打野'],['对抗路','对抗路'],['中路','中路'],['发育路','发育路'],['辅助','辅助']].forEach(([v,t])=>laneSelect.add(new Option(t,v)));
    const laneLabel=document.createElement('label'); laneLabel.textContent='你的分路'; laneLabel.append(laneSelect);
    document.querySelector('.perspective')?.append(laneLabel);
  }
  const channel=typeof BroadcastChannel==="function"?new BroadcastChannel("gameplan-monitor-session"):null;
  let toastTimer,drag=null,draft=null;
  const status=text=>$('status').textContent=text;
  function toast(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,6000);}
  async function api(path,payload,signal){
    const timeout=AbortSignal.timeout(/\/(analysis|recognize|personal-plan)$/.test(path)?210000:20000);
    const response=await fetch(path,{method:payload===undefined?'GET':'POST',headers:payload===undefined?{}:{'Content-Type':'application/json'},body:payload===undefined?undefined:JSON.stringify(payload),signal:signal?AbortSignal.any([signal,timeout]):timeout});
    const data=await response.json();
    if(!response.ok)throw Object.assign(new Error(typeof data.detail==='string'?data.detail:'输入内容无法校验，请核对图片或英雄名字。'),{status:response.status});
    return data;
  }
  function on(id,event,fn){$(id).addEventListener(event,()=>Promise.resolve(fn()).catch(e=>toast(e.message)));}
  function source(){return state.sources.find(s=>s.id===$('screen-source').value);}
  function controls(){
    $('start').disabled=state.watching||!source();$('pause').disabled=!state.running;
    $('stop').disabled=!state.running&&$('frame-image').hidden;
    $('retry').disabled=!state.running||!state.pending;
    $('correct').disabled=!state.running||!state.result;
    $('plan-generate').disabled=state.planBusy||!state.running||!state.result?.complete||!state.result?.identity_id||!$('hero').value||$('lane').value==='unknown'||$('side').value==='neutral';
  }
  function bump(){
    state.revision++;state.requested=-1;state.retryAt=0;
    // This also prevents an obsolete Qwen pass from starting a paid image request.
    fetch('/api/picture/revision',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({match_id:state.session,revision:state.revision}),keepalive:true}).catch(()=>{});
  }
  function clearAdvice(message,rosters=true){
    state.planVersion++;$('personal-plan').replaceChildren();
    $('coach-summary').textContent=message;$('coach-source').textContent='等待新分析';$('coach-meta').textContent='';$('advice-detail').hidden=true;
    $('coach-title').textContent='你的对战思路';
    $('watch-for').replaceChildren();$('opportunities').replaceChildren();
    if(rosters){
      $('lineup-title').textContent='当前阵容 · 请核对';
      state.result=null;state.manual=null;$('phase').textContent='等待画面';$('roster-a').textContent=$('roster-b').textContent='尚未识别';
      $('uncertainty').textContent='只分析当前图片，不沿用上一张的英雄。';$('correct-a').value=$('correct-b').value='';$('local-result').textContent='等待本地识别';
      $('hero').replaceChildren(new Option('请选择英雄',''));
      $('lane').value='unknown';
    }
    controls();
  }
  function stop(message='已停止监控',keep=false){
    state.rosterCandidate=null;
    state.running=false;state.watching=false;state.generation++;bump();clearTimeout(state.timer);
    state.captureController?.abort();state.inferController?.abort();state.pending=null;state.stable=0;
    clearAdvice(message);status(message);$('pipeline').textContent='已停止采样与后续分析';$('elapsed').textContent='已停止';
    $('detection-status').textContent='已停止检查画面';
    $('preview-paused').hidden=!keep||$('frame-image').hidden;
    if(!keep){$('frame-image').hidden=true;$('frame-image').removeAttribute('src');$('placeholder').hidden=false;$('capture-status').textContent='等待选择';}
    controls();
  }
  function different(a,b){
    if(!a||!b||a.length!==b.length)return true;
    const left=atob(a),right=atob(b);let sum=0,changed=0;
    for(let i=0;i<left.length;i++){const delta=Math.abs(left.charCodeAt(i)-right.charCodeAt(i));sum+=delta;if(delta>18)changed++;}
    return sum/left.length>1.1||changed/left.length>0.008;
  }
  function acceptFrame(frame,immediate=false){
    if(!state.running)return;
    $('frame-image').src=frame.image_base64;$('frame-image').hidden=false;$('placeholder').hidden=true;$('preview-paused').hidden=true;
    $('capture-status').textContent=`${state.watching?'区域采样':'图片载入'} ${new Date(frame.captured_at*1000).toLocaleTimeString('zh-CN',{hour12:false})}`;
    if(state.watching){
      state.pending=frame;
      if(!state.busy&&Date.now()>=state.retryAt){
        bump();state.stable=2;
        $('detection-status').textContent=state.rosterCandidate?'正在核对下一帧阵容':'监控中 · 寻找加载界面';
        maybeAnalyze();
      }
      return;
    }
    if(!state.pending||different(state.pending.signature,frame.signature)){
      bump();state.pending=frame;state.stable=immediate?2:1;clearAdvice('画面已更新，正在逐个读取英雄名字…');
      $('detection-status').textContent=state.watching?'发现变化 · 等待稳定':'静态图片 · 再次选择可换图';
      $('pipeline').textContent=immediate?'准备读取图片':'检测到变化，等待图片稳定';status('发现新图片');
    }else{
      state.stable++;
      $('detection-status').textContent=state.result?'画面未变化 · 已复用当前方案':'区域监控中 · 图片已稳定';
    }
    controls();maybeAnalyze();
  }
  let heroCatalog=[];
  function heroOptions(){
    const v=state.result?.verdict,old=$('hero').value,side=$('side').value;
    const names=heroCatalog.length?heroCatalog:(!v?[]:side==='a'?v.top_heroes:side==='b'?v.bottom_heroes:[]);
    $('hero').replaceChildren(new Option('请选择英雄',''),...names.map(h=>new Option(h,h)));
    if(names.includes(old))$('hero').value=old;
  }
  function render(result){
    state.result=result;const v=result.verdict;
    if(result.scope==='hero_identity'){
      const slots=result.slots||[];
      $('lineup-title').textContent=`英雄识别 · ${slots.filter(s=>s.hero).length}/10`;
      $('phase').textContent=phases[v.phase];
      for(const [id,row] of [['roster-a',0],['roster-b',1]]){
        const members=slots.filter(s=>s.row===row);
        $(id).textContent=members.length?members.map(s=>`${s.column}. ${s.hero||'待确认'}`).join('　'):'未能定位英雄名字';
      }
      $('uncertainty').textContent=v.uncertainty.join('\\n');
      $('correct-a').value=v.top_heroes.join('、');$('correct-b').value=v.bottom_heroes.join('、');
      $('local-result').textContent=JSON.stringify(slots,null,2);
      $('coach-title').textContent='你的对战思路';$('coach-source').textContent='本地文字识别';
      $('coach-summary').textContent=v.summary;
      $('coach-meta').textContent=`${result.model} · ${result.cached?'复用本图名字':`${result.elapsed_s} 秒`} · 不调用外部打法模型`;
      $('pipeline').textContent='定位加载卡片 → 放大英雄名字 → OCR 与本地模型核对';
      $('advice-detail').hidden=true;$('watch-for').replaceChildren();$('opportunities').replaceChildren();
      $('elapsed').textContent=result.cached?'已复用本图名字':`识别耗时 ${result.elapsed_s} 秒`;
      status(result.complete?'英雄已识别 · 请选择自己的英雄和分路':'部分名字待确认');heroOptions();controls();return;
    }
    $('lineup-title').textContent=result.lineup_source==='manual'?'当前阵容 · 已核对':'当前阵容 · 请核对';
    $('phase').textContent=phases[v.phase];$('roster-a').textContent=v.top_heroes.join('、')||'未能确认';$('roster-b').textContent=v.bottom_heroes.join('、')||'未能确认';
    $('uncertainty').textContent=v.uncertainty.join('\\n')||(result.lineup_source==='manual'?'当前按核对阵容分析，换图后恢复自动识别。':'阵容由模型从本图识别，采用具体英雄建议前请核对。');
    $('correct-a').value=v.top_heroes.join('、');$('correct-b').value=v.bottom_heroes.join('、');
    $('local-result').textContent=result.qwen_observation?JSON.stringify(result.qwen_observation,null,2):'本次 Qwen 本地识别未完成，由 DeepSeek 独立看图分析。';
    const selectedHero=$('hero').value, selectedLane=$('lane')?.value||'unknown';
    $('coach-title').textContent=selectedHero&&selectedLane!=='unknown'?`${selectedHero} · ${selectedLane}前期个人打法`:'这张图，该怎么打';
    $('coach-summary').textContent=v.summary;$('coach-source').textContent=result.requires_review?'DeepSeek · 阵容待核对':'DeepSeek 看图';
    $('coach-meta').textContent=`实际返回 ${result.model} · ${result.cached?'复用本图结果':`本次 ${result.elapsed_s} 秒`} · 静态图片阵容方案`;
    if(result.summary_condensed)$('coach-meta').textContent+=' · 按模型行动要点整理';
    $('pipeline').textContent=result.qwen_model?`${result.qwen_model} → DeepSeek 图片分析`:'DeepSeek 图片分析（本地识别未完成）';
    if(result.requires_review)$('pipeline').textContent='图片清晰度或识别一致性不足：先展示通用打法，核对阵容后生成具体方案';
    for(const [id,items]of [['watch-for',v.watch_for],['opportunities',v.opportunities]])$(id).replaceChildren(...items.map(text=>{const li=document.createElement('li');li.textContent=text;return li;}));
    $('advice-detail').hidden=!v.watch_for.length&&!v.opportunities.length;
    $('elapsed').textContent=result.cached?'已复用本图方案':`分析耗时 ${result.elapsed_s} 秒`;
    status(state.watching?'监控中 · 等待换图':'图片分析完成');heroOptions();controls();
  }
  async function maybeAnalyze(){
    if(!state.running||!state.pending||state.stable<2||state.busy||state.requested===state.revision||Date.now()<state.retryAt)return;
    const revision=state.revision,generation=state.generation,frame=state.pending;
    const controller=new AbortController();state.inferController=controller;state.busy=true;state.requested=revision;state.started=performance.now();
    status('正在识别英雄');$('pipeline').textContent='先识别英雄，完成后选择自己的英雄和分路';
    try{
      const result=await api('/api/picture/recognize',{match_id:state.session,revision,image_base64:frame.image_base64,captured_at:frame.captured_at,input_kind:state.watching?'live':'image',focus:'heroes'},controller.signal);
      if(state.running&&generation===state.generation&&revision===state.revision){
        if(state.watching){
          const key=result.complete?JSON.stringify((result.slots||[]).map(s=>[s.slot,s.hero])):null;
          if(key&&key===state.rosterCandidate){
            state.watching=false;clearTimeout(state.timer);state.captureController?.abort();
            render(result);
            $('preview-title').textContent='阵容已锁定 · 开始监控可识别下一局';
            $('detection-status').textContent='连续两帧阵容一致 · 已暂停扫描';
            status('阵容已锁定 · 确认英雄和分路后生成建议');
          }else{
            state.rosterCandidate=key;state.retryAt=Date.now()+1500;
            status(key?'已识别阵容 · 等待第二帧核对':'监控中 · 等待清晰加载画面');
          }
        }else render(result);
      }
    }catch(error){
      if(!state.running||generation!==state.generation||revision!==state.revision)return;
      if(error.status===429){state.requested=-1;state.retryAt=Date.now()+1800;status('等待上一张处理结束');}
      else{status('本图分析未完成');clearAdvice(error.message,false);$('coach-source').textContent='接口未完成';$('pipeline').textContent='可重试，或更换图片；当前没有有效英雄识别结果';}
    }finally{
      state.busy=false;if(state.inferController===controller)state.inferController=null;
      if(state.running&&state.requested!==state.revision)setTimeout(maybeAnalyze,Math.max(0,state.retryAt-Date.now()));
    }
  }
  async function tick(generation){
    if(!state.watching||generation!==state.generation)return;
    const controller=new AbortController();state.captureController=controller;
    try{
      const frame=await api('/api/picture/frame',{source_id:source().id,roi:state.roi},controller.signal);
      if(state.watching&&generation===state.generation)acceptFrame(frame);
    }catch(error){if(generation===state.generation&&state.watching){stop(error.message);toast(error.message);}}
    finally{if(state.captureController===controller)state.captureController=null;}
    if(state.watching&&generation===state.generation)state.timer=setTimeout(()=>tick(generation),900);
  }
  function loadRegion(){
    state.roi=null;const s=source();if(!s)return;
    try{const saved=JSON.parse(localStorage.getItem('gameplan-picture-region-'+s.id));if(saved&&saved.width===s.width&&saved.height===s.height)state.roi=saved.roi;}catch{}
    $('region-label').textContent=state.roi?`固定区域 ${Math.round(state.roi.width*s.width)} × ${Math.round(state.roi.height*s.height)} 像素 · 换图保持位置即可，移动窗口后请重选`:'首次框选图片所在位置，之后在这个位置换图即可。';
  }
  async function refresh(){
    const previous=$('screen-source').value||localStorage.getItem('gameplan-screen-source');
    const data=await api('/api/screen/sources');state.sources=data.sources;
    $('screen-source').replaceChildren(...data.sources.map(s=>new Option(s.label,s.id)));
    if(data.sources.some(s=>s.id===previous))$('screen-source').value=previous;
    if(state.watching&&(!data.sources.some(s=>s.id===previous))){stop('显示器配置已变化，请重新框选');}
    loadRegion();controls();
  }
  async function start(){
    if(!source())throw new Error('请选择可用的屏幕。');
    if(!state.roi)return selectRegion();
    stop('正在开始区域监控');state.running=true;state.watching=true;
    localStorage.setItem('gameplan-screen-source',source().id);$('preview-title').textContent='固定区域 · 当前图片';channel?.postMessage({owner:state.session});controls();
    tick(state.generation);
  }
  async function selectRegion(){
    if(!source())throw new Error('请选择可用的屏幕。');
    stop('正在获取框选预览');const generation=state.generation;
    const frame=await api('/api/screen/frame',{source_id:source().id});
    if(generation!==state.generation)return;
    draft=null;$('region-box').hidden=true;$('region-save').disabled=true;$('region-size').textContent='在预览中拖动鼠标框选';
    $('region-image').src=frame.image_base64;await $('region-image').decode();
    if(generation===state.generation){$('region-dialog').showModal();status('请选择固定图片区域');}
  }
  function point(event){const r=$('region-image').getBoundingClientRect();return{x:Math.max(0,Math.min(1,(event.clientX-r.left)/r.width)),y:Math.max(0,Math.min(1,(event.clientY-r.top)/r.height))};}
  function drawRegion(){
    const box=$('region-box');box.hidden=false;Object.assign(box.style,{left:draft.x*100+'%',top:draft.y*100+'%',width:draft.width*100+'%',height:draft.height*100+'%'});
    const s=source(),w=Math.round(draft.width*s.width),h=Math.round(draft.height*s.height);$('region-size').textContent=`选中 ${w} × ${h} 像素`;$('region-save').disabled=w<32||h<32;
  }
  $('region-stage').addEventListener('pointerdown',e=>{if(e.button!==0)return;drag=point(e);$('region-stage').setPointerCapture(e.pointerId);e.preventDefault();});
  $('region-stage').addEventListener('pointermove',e=>{if(!drag)return;const end=point(e);draft={x:Math.min(drag.x,end.x),y:Math.min(drag.y,end.y),width:Math.abs(end.x-drag.x),height:Math.abs(end.y-drag.y)};drawRegion();});
  $('region-stage').addEventListener('pointerup',()=>{drag=null;});$('region-stage').addEventListener('pointercancel',()=>{drag=null;});
  on('region-save','click',()=>{state.roi=draft;const s=source();localStorage.setItem('gameplan-picture-region-'+s.id,JSON.stringify({width:s.width,height:s.height,roi:draft}));loadRegion();$('region-dialog').close();$('region-image').removeAttribute('src');return start();});
  function closeRegion(){drag=null;$('region-dialog').close();$('region-image').removeAttribute('src');status('框选已取消');}
  on('region-cancel','click',closeRegion);$('region-dialog').addEventListener('cancel',closeRegion);
  async function imageFrame(data){
    const img=new Image();img.src=data;await img.decode();
    if(img.naturalWidth*img.naturalHeight>20000000)throw new Error('图片最多支持 2000 万像素。');
    const canvas=document.createElement('canvas');canvas.width=96;canvas.height=54;const ctx=canvas.getContext('2d');ctx.drawImage(img,0,0,96,54);
    const rgba=ctx.getImageData(0,0,96,54).data,rgb=[];for(let i=0;i<rgba.length;i+=4)rgb.push(rgba[i],rgba[i+1],rgba[i+2]);
    return {image_base64:data,signature:btoa(String.fromCharCode(...rgb)),captured_at:Date.now()/1000};
  }
  async function useImage(file){
    if(file.size>8000000)throw new Error('图片必须小于 8MB。');
    stop('正在载入图片');const generation=state.generation;
    const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error('无法读取图片'));reader.readAsDataURL(file);});
    const frame=await imageFrame(data);if(generation!==state.generation)return;
    state.running=true;channel?.postMessage({owner:state.session});$('preview-title').textContent='所选图片 · 可继续选择另一张';acceptFrame(frame,true);
  }
  on('image-file','change',async()=>{const file=$('image-file').files[0];$('image-file').value='';if(file)await useImage(file);});
  // Keep the current preview intact. This action must never load the bundled
  // sample image; users use the file picker above to replace the screenshot.
  on('reference','click',reanalyze);
  on('select-region','click',selectRegion);on('start','click',start);on('pause','click',()=>stop('已暂停区域监控',true));on('stop','click',()=>stop());on('refresh','click',refresh);
  on('screen-source','change',()=>{stop('屏幕已更换，请框选图片区域');localStorage.setItem('gameplan-screen-source',$('screen-source').value);loadRegion();});
  function reanalyze(){if(!state.running||!state.pending)return;bump();clearAdvice('正在重新读取这张图片的英雄名字…',false);state.stable=2;maybeAnalyze();}
  function resetPersonal(){
    state.planVersion++;$('personal-plan').replaceChildren();$('advice-detail').hidden=true;
    $('coach-title').textContent='你的对战思路';$('coach-summary').textContent='选择所在阵营、自己的英雄和本局分路，查看个人行动方案。';
    $('coach-meta').textContent='';controls();
  }
  async function personalPlan(){
    if(state.planBusy)return;
    resetPersonal();
    if($('plan-generate').disabled)return;
    state.planBusy=true;controls();
    const version=state.planVersion,revision=state.revision,identity=state.result.identity_id;
    $('coach-summary').textContent='正在整理本局个人行动方案…';
    try{
      const result=await api('/api/picture/personal-plan',{match_id:state.session,identity_id:identity,revision,side:$('side').value,player:$('hero').value,lane:$('lane').value});
      if(version!==state.planVersion||revision!==state.revision||identity!==state.result?.identity_id)return;
      $('coach-title').textContent=`${result.player} · ${result.lane}怎么打`;
      $('coach-source').textContent=result.source==='external_model'?`模型分析 · ${result.model}`:'本地规则建议';$('coach-summary').textContent=result.summary;
      $('coach-meta').textContent=`己方：${result.allies.join('、')}；敌方：${result.enemies.join('、')}`;
      const parts=[...result.sections,
        {title:'本局敌方需要防什么',text:result.enemy_risks.map(i=>i.text).join('\\n')||'敌方机制资料尚未覆盖，先观察关键技能和进场距离。'},
        {title:'和队友如何配合',text:result.ally_coordination.map(i=>i.text).join('\\n')||'先处理自己的兵线或野区，再与队友结伴行动。'}];
      for(const item of parts){const section=document.createElement('section');section.className='personal-step';const h=document.createElement('h3'),p=document.createElement('p');h.textContent=item.title;p.textContent=item.text;section.append(h,p);$('personal-plan').append(section);}
      const note=document.createElement('p');note.className='muted';note.textContent=[result.fallback_reason,...result.notes].filter(Boolean).join(' ');$('personal-plan').append(note);
      if(result.evidence?.length){
        const details=document.createElement('details'),summary=document.createElement('summary');
        summary.textContent='建议依据';details.append(summary);
        for(const item of result.evidence){const p=document.createElement('p');p.textContent=`${item.id} · ${item.text}${item.game_version?` · 资料版本 ${item.game_version}`:''}`;details.append(p);}
        $('personal-plan').append(details);
      }
      $('pipeline').textContent='使用当前识别阵容和你选择的分路；不重新猜英雄或对线对象';status('个人对战方案已生成');
    }catch(error){if(version===state.planVersion){$('coach-summary').textContent=error.message;status('个人方案未完成');}}
    finally{state.planBusy=false;controls();}
  }
  on('side','change',()=>{heroOptions();resetPersonal();});on('hero','change',resetPersonal);on('lane','change',resetPersonal);on('plan-generate','click',personalPlan);
  on('correct','click',()=>{
    const split=value=>value.trim().split(/[、，,\s]+/).filter(Boolean),a=split($('correct-a').value),b=split($('correct-b').value),all=[...a,...b];
    if(!a.length||!b.length||a.length>5||b.length>5||new Set(all).size!==all.length)throw new Error('每组填写 1～5 位英雄，双方不能重复。');
    state.manual={allies:a,enemies:b,source:'manual'};state.result.verdict.top_heroes=a;state.result.verdict.bottom_heroes=b;heroOptions();reanalyze();
  });
  async function checkProvider(){const cfg=await api('/api/vision/status');$('provider').textContent=`本地英雄识别 · ${cfg.ready?'模型已就绪':cfg.message}。识别完成后，选择自己的英雄和分路，生成个人对战思路。`;}
  on('retry','click',async()=>{await checkProvider();reanalyze();});
  $('auto').checked=localStorage.getItem('gameplan-picture-auto')!=='false';on('auto','change',()=>localStorage.setItem('gameplan-picture-auto',String($('auto').checked)));
  if(channel)channel.onmessage=e=>{if(e.data?.owner!==state.session&&state.running)stop('监控已切换到另一个窗口');};
  window.addEventListener('pagehide',()=>{stop('窗口已关闭');channel?.close();});
  setInterval(()=>{if(state.running&&state.busy)$('elapsed').textContent=`模型处理中 ${Math.floor((performance.now()-state.started)/1000)} 秒 · 换图检测独立运行`;},500);
  window.gameplanPicture={start,stop,different};
  window.addEventListener('DOMContentLoaded',async()=>{
    try{const catalog=await api('/api/bp-assistant/heroes');heroCatalog=catalog;await refresh();await checkProvider();if(state.roi&&$('auto').checked&&new URLSearchParams(location.search).get('monitor')!=='off')await start();else status(state.roi?'等待开始':'先框选区域或选择图片');}
    catch(e){status('初始化未完成');toast(e.message);}
  },{once:true});
})();



