"use strict";
const byId = id => document.getElementById(id);
const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const appState = {matchId:crypto.randomUUID(),screenSource:null,stream:null};
let toastTimer;
function toast(message,error=false){clearTimeout(toastTimer);byId("toast").textContent=message;byId("toast").classList.toggle("error",error);byId("toast").hidden=false;toastTimer=setTimeout(()=>byId("toast").hidden=true,6000);}
async function api(path,payload,signal){
  const timeout=AbortSignal.timeout(path.includes("/vision/observe")?135000:path.includes("/vision/warmup")?65000:35000);
  const response=await fetch(path,{method:payload===undefined?"GET":"POST",headers:payload===undefined?{}:{"Content-Type":"application/json"},body:payload===undefined?undefined:JSON.stringify(payload),signal:signal?AbortSignal.any([signal,timeout]):timeout});
  let data;
  try{data=await response.json();}catch{throw Object.assign(new Error(`服务返回了无法读取的响应（HTTP ${response.status}），请稍后重试。`),{status:response.ok?502:response.status});}
  if(!response.ok){
    const detail=data?.detail;
    const message=typeof detail==="string"?detail:Array.isArray(detail)?detail.slice(0,3).map(item=>`${(item.loc||[]).filter(part=>part!=="body").join(".")||"请求参数"}：${item.msg||"校验失败"}`).join("；"):"服务暂时无法完成请求，请稍后重试。";
    throw Object.assign(new Error(`${message}（HTTP ${response.status}）`),{status:response.status});
  }
  return data;
}
function action(id,handler){byId(id).addEventListener("click",()=>Promise.resolve(handler()).catch(error=>toast(error.message,true)));}

(() => {
  const state={active:false,busy:false,generation:0,controller:null,timer:null,sources:[],latest:null,expiredFrame:null,count:0,errors:0,started:0,planController:null,planKey:null,planGeneration:0,videoJob:null,captureTimer:null,captureController:null,latestFrame:null,video:null,frameHistory:[],lastSubmittedAt:0,videoCaptureTimer:null,frameLimit:8,reconnectTimer:null};
  const phases={bp:"选人",loading:"加载",in_game:"对局中",result:"结算",not_game:"非游戏画面",unknown:"阶段未知"};
  const captureRegions={full:null,left:{x:0,y:0,width:.5,height:1},right:{x:.5,y:0,width:.5,height:1}};
  const launch = new URLSearchParams(location.search);
  if (/^[a-f0-9]{16}$/.test(launch.get('source')||'')) localStorage.setItem('gameplan-screen-source', launch.get('source'));
  if (Object.hasOwn(captureRegions, launch.get('region'))) localStorage.setItem('gameplan-monitor-region', launch.get('region'));
  const channel=typeof BroadcastChannel==="function"?new BroadcastChannel("gameplan-monitor-session"):null;
  const owner=crypto.randomUUID();
  function loadBpContext(){try{const raw=localStorage.getItem("gameplan-bp-context");if(!raw)return null;const value=JSON.parse(raw);if(!value||value.version!==1||Date.now()-Number(value.created_at)>21600000)return null;return value;}catch{return null;}}
  let bpContext=loadBpContext();
  function clearBpContext(){bpContext=null;localStorage.removeItem("gameplan-bp-context");byId("bp-context").hidden=true;}
  function renderBpContext(){const box=byId("bp-context");if(!box||!bpContext)return;box.innerHTML=`BP 已确认 · <b>我的分路：${escapeHtml(bpContext.lane||"待确认")}</b> · 己方：${escapeHtml((bpContext.allies||[]).join("、")||"待识别")} · 敌方：${escapeHtml((bpContext.enemies||[]).join("、")||"待识别")}`;box.hidden=false;byId("monitor-side").value="auto";byId("roster-a").textContent=(bpContext.allies||[]).join("、")||"尚未识别";byId("roster-b").textContent=(bpContext.enemies||[]).join("、")||"尚未识别";}
  const status=text=>{byId("monitor-status").textContent=text;if(!byId("video-status").hidden)byId("video-status").textContent=text;};
  const captureRoi=()=>appState.screenSource?.kind==='window'?null:captureRegions[byId("capture-region").value]??null;
  function regionControls(){
    const isWindow=state.sources.find(s=>s.id===byId('screen-source').value)?.kind==='window';
    byId('capture-region').disabled=isWindow;
    if(isWindow)byId('capture-region').value='full';
    byId('capture-region').title=isWindow?'独立窗口采集完整窗口，可被小面板遮挡':'屏幕采集包含遮挡它的其他窗口';
  }
  function controls(){byId("monitor-start").disabled=state.active||(!state.videoJob&&!byId("screen-source").value);byId("monitor-start").textContent=state.videoJob?"继续视频分析":byId("preview-paused").hidden?"开始监控":"继续监控";byId("monitor-pause").disabled=!state.active;byId("monitor-stop").disabled=!state.active&&!state.videoJob&&!state.reconnectTimer&&byId("frame-image").hidden;}
  function clearPlan(){state.planGeneration++;state.planController?.abort();state.planController=null;state.planKey=null;byId("quick-plan").hidden=true;byId("quick-text").textContent="";}
  function clearObservation(message,keepEnemies=false){
    state.latest=null;if(!keepEnemies)window.gameplanEnemies?.reset();renderLane();clearPlan();byId("current-hero").textContent="待确认";byId("phase").textContent="等待画面";
    byId("roster-a").textContent=byId("roster-b").textContent="尚未识别";byId("observation-note").textContent=message;
    byId("observation-json").textContent="当前没有有效识别";byId("skill-readings").textContent="暂无有效技能读数";
    byId("monitor-hero").innerHTML='<option value="">自动跟随 / 全队配合</option>';
    window.gameplanAdvisor?.reset(message);
    renderEnemyTimers();
  }
  function stop(message="已停止监控",keepPreview=false){
    if(state.video&&!['complete','all60'].includes(state.videoJob?.mode))window.gameplanEnemies?.setPlaybackTime(state.video.currentTime);
    clearTimeout(state.reconnectTimer);state.reconnectTimer=null;
    state.frameHistory=[];state.panelFrame=null;state.lastSubmittedAt=0;clearInterval(state.videoCaptureTimer);state.videoCaptureTimer=null;
    state.active=false;state.generation++;clearTimeout(state.captureTimer);state.captureController?.abort();state.captureController=null;state.latestFrame=null;if(state.video){if(state.videoJob)state.videoJob.next=state.video.currentTime;state.video.pause();state.video.remove();state.video=null;byId("frame-image").hidden=false;}state.controller?.abort();state.controller=null;clearTimeout(state.timer);state.timer=null;
    if(!keepPreview)state.videoJob=null;
    appState.screenSource=null;
    // Keep the last valid observation and advice visible while paused.
    if(!keepPreview) clearObservation(message);
    else { byId("observation-note").textContent=message+"；保留最近一次建议"; window.gameplanAdvisor?.hold("已暂停 · 保留上次分析"); window.gameplanEnemies?.hold(); }
    status(message);byId("frame-time").textContent="已暂停观察";
    byId("preview-paused").hidden=!keepPreview||byId("frame-image").hidden;
    if(!keepPreview){byId("frame-image").hidden=true;byId("frame-image").removeAttribute("src");byId("preview-placeholder").hidden=false;byId("capture-status").textContent="尚未采集";}
    controls();
  }
  function context(){
    const o=state.latest?.observation;if(!o)return {side:"neutral",player:null,lineup:null};
    const a=o.ally_roster||[];
    const selected=byId("monitor-hero").value;
    // BP 阶段只确认阵容和分路，不把缓存中的候选英雄当成玩家已选英雄。
    const detected=["bp","in_game"].includes(o.phase)?o.player_hero:null;
    let player=selected||detected;
    // The observation already names own/enemy teams. A guessed player or a
    // cached BP screen position must never reinterpret those canonical lists.
    const verified=state.latest?.team_context?.side==="a" ||
      (o.phase==="loading" && state.latest?.loading_context?.side==="a"
       && state.latest.loading_context.source==="local_name_highlight");
    if(!a.includes(player)||(o.enemy_roster||[]).includes(player))player=null;
    const side=verified||player?"a":"neutral";
    return {side,player,lineup:null,lane:state.latest?.lane_context?.lane||state.latest?.bp_context?.lane||"unknown"};
  }
  function perspectiveControls(){
    const o=state.latest?.observation,previous=byId("monitor-hero").value;
    const names=o?.ally_roster||[];
    byId("monitor-hero").innerHTML='<option value="">自动跟随 / 全队配合</option>'+names.map(hero=>`<option value="${escapeHtml(hero)}">${escapeHtml(hero)}</option>`).join("");
    if(names.includes(previous))byId("monitor-hero").value=previous;
  }
  function laneAdvice(lane, phase){
    const prefix = phase === "bp" ? "选人阶段" : phase === "loading" ? "加载阶段" : "局中观察";
    const advice = {"发育路":"优先补刀和保血，前几分钟不要单独探草；等辅助和打野露出位置后再压线，河道和侧翼不明时保留闪现与退路。","对抗路":"先稳住线权和血量，观察对方打野位置；有兵线再换血或支援，支援前先确认队友能跟进，不为残血越过河道。","中路":"快速处理兵线后优先报点，支援前确认边路和河道视野；技能不全或敌方刺客位置不明时不要单独压深。","打野":"先明确首轮路线和资源分配，靠近目标前确认线权；没有可靠信息时不强行入侵，击杀或逼退后优先换塔、龙或反野。","辅助":"开局先保护核心并帮助取得线权，探草和游走要让队友能跟进；不要脱离队伍太久。"};
    return `${prefix} · ${lane || "分路待确认"}：${advice[lane] || "先确认自己的分路、核心职责和队友跟进距离；未确认英雄前不生成个人英雄打法。"}`;
  }
  async function quickPlan(){
    const o=state.latest?.observation,c=context();
    if(!o||!["bp","loading","in_game"].includes(o.phase)){clearPlan();return;}
    const allies=o.ally_roster||[],enemies=o.enemy_roster||[];
    if(o.phase!=="bp"&&!c.player){clearPlan();byId("quick-text").textContent=laneAdvice(c.lane !== "unknown" ? c.lane : null,o.phase);byId("quick-plan").hidden=false;return;}
    if(!allies.length&&!enemies.length){clearPlan();return;}
    const key=JSON.stringify([o.phase,allies,enemies,c.player,c.side,c.lane]);if(state.planKey===key)return;
    clearPlan();state.planKey=key;const generation=state.planGeneration,controller=new AbortController();state.planController=controller;
    try{
      if(o.phase==="bp"){
        const plan=await api("/api/bp/plan",{allies:c.side==="b"?enemies:allies,enemies:c.side==="b"?allies:enemies},controller.signal);if(generation!==state.planGeneration)return;
        const text=plan.items?.length?plan.items.slice(0,3).map(item=>`敌方${item.hero}：${item.advice}`).join("；"):"等待识别到对手英雄后显示应对参考。";
        byId("quick-text").textContent=`选人阶段 · ${c.player?"建议对象："+c.player:"操控英雄待确认"} · ${text}`;byId("quick-plan").hidden=false;return;
      }
      const own=c.side==="a"?allies:c.side==="b"?enemies:allies,other=c.side==="a"?enemies:c.side==="b"?allies:enemies;
      const plan=await api("/api/loading/plan",{allies:own,enemies:other,player:c.player,source:"vision_draft"},controller.signal);if(generation!==state.planGeneration)return;
      byId("quick-text").textContent=plan.personal?`${plan.personal.hero}：${plan.personal.text}`:plan.summary;byId("quick-plan").hidden=false;
    }catch(error){if(generation===state.planGeneration){state.planKey=null;byId("quick-plan").hidden=true;}}
  }
  function renderLane(){
    const lane=state.latest?.lane_context;
    byId("current-lane").textContent=lane?.lane||"待确认";
    byId("lane-evidence").textContent=lane ? (lane.from_current_frame?"当前画面分路提示":"本局已确认分路")+" · "+lane.evidence : "等待读取个人分路提示，不根据英雄定位猜测。";
    byId("lane-plan").hidden=!lane;
    byId("lane-plan-title").textContent=lane?lane.lane+" · 分路打法":"分路打法";
    byId("lane-plan-text").textContent=lane?.advice||"";
  }
  async function render(response,frameImage=null){
    const incomplete=['incomplete_answer','partial'].includes(response.skill_scan?.detector_status);
    state.latest=response;state.expiredFrame=null;if(!incomplete)state.count++;state.errors=0;const o=response.observation;
    byId("frame-count").textContent=`${state.count} 帧`;byId("frame-time").textContent=`最近识别 ${response.elapsed_s} 秒`;
    byId("phase").textContent=phases[o.phase];byId("current-hero").textContent=o.player_hero||"待确认";
    byId("observation-note").textContent=o.note||"英雄身份来自视觉识别，请核对。";
    byId("roster-a").textContent=o.ally_roster.join("、")||"尚未识别";byId("roster-b").textContent=o.enemy_roster.join("、")||"尚未识别";
    byId("observation-json").textContent=JSON.stringify(o,null,2);perspectiveControls();renderLane();
    const valid=(Number.isFinite(response.video_time_s)||Date.now()/1000-response.captured_at>=0&&Date.now()/1000-response.captured_at<=1800)&&["bp","loading","in_game","result"].includes(o.phase);
    if(!valid){
      // Leaving the cast can expose a desktop/non-game frame. Keep the last
      // enemy result until fresh game data arrives; ambiguous frames alone
      // should still allow an active cooldown to tick.
      clearObservation(o.phase==="not_game"?"当前不是游戏画面，继续观察…":"等待新的有效游戏画面",true);
      window.gameplanEnemies?.retain("当前无有效游戏画面 · 保留上次识别结果，等待新数据");
      byId("phase").textContent=phases[o.phase];
      status("等待有效游戏画面 · 保留上次识别结果");
    }
    else{window.gameplanEnemies?.observe(response,context());void Promise.all([quickPlan(),window.gameplanAdvisor?.observe(response)]).catch(error=>toast(error.message,true));}
    if(state.latest!==response)return;
    status(incomplete?"本轮技能判断未完成 · 已保留有效结果":"持续观察中");renderEnemyTimers();renderSkills();
  }
  function renderEnemyTimers(){
    window.gameplanEnemies?.tick();
  }
  function renderSkills(){
    const r=state.latest;if(!r)return;
    const age=Date.now()/1000-r.captured_at;
    byId("skill-readings").textContent=r.focus!=="full"?"当前模式优先读取英雄或敌方技能，不读取自身技能数字。":age<0||age>12?"画面读数已过期，等待新帧。":(r.observation.self_skills||[]).map(s=>`${s.slot} 技能：${s.remaining_s===null?"未知":s.remaining_s-age>0?"约 "+Math.ceil(s.remaining_s-age)+" 秒":"计时结束，待确认"}`).join("；")||"未读到有效的自身技能数字。";
  }
  function rememberFrame(frame){
    // This color cue reserves a sample only; OCR still verifies the panel.
    if(frame.panel_candidate)state.panelFrame=frame;
    if(state.panelFrame&&frame.captured_at-state.panelFrame.captured_at>15)state.panelFrame=null;
    const previous=state.frameHistory.at(-1);
    if(!previous||frame.captured_at-previous.captured_at>=.18)state.frameHistory.push(frame);
    state.frameHistory=state.frameHistory.filter(item=>frame.captured_at-item.captured_at<=7.5).slice(-40);
  }
  function skillFrames(frame){
    if(byId("observe-focus").value==="heroes")return [];
    // Keep evidence collected during the previous inference. Restricting this
    // to 1.8 seconds silently erased casts near the start of a 5-second call.
    // The bounded buffer/model cap still prevents an unbounded live backlog.
    const history=state.frameHistory.filter(item=>item.captured_at<frame.captured_at&&frame.captured_at-item.captured_at<=7.5);
    const before=history.filter(item=>item.captured_at<=state.lastSubmittedAt).at(-1);
    let pending=history.filter(item=>item.captured_at>state.lastSubmittedAt);
    if(before)pending.unshift(before);
    const panelAgeLimit=byId("observe-focus").value==="skills"?15:7.5;
    const panel=state.panelFrame&&state.panelFrame.captured_at<frame.captured_at&&frame.captured_at-state.panelFrame.captured_at<=panelAgeLimit?state.panelFrame:null;
    if(panel)pending=pending.filter(item=>item.captured_at!==panel.captured_at);
    const limit=state.frameLimit-1-(panel?1:0);
    if(pending.length>limit)pending=limit===1?[pending.at(-1)]:Array.from({length:limit},(_,i)=>pending[Math.round(i*(pending.length-1)/(limit-1))]);
    if(panel)pending.push(panel);
    pending.sort((a,b)=>a.captured_at-b.captured_at);
    state.lastSubmittedAt=frame.captured_at;
    return pending.map(item=>({image_base64:item.image_base64,captured_at:item.captured_at,...(item===panel?{panel_candidate:true}:{})}));
  }
  async function captureLatest(generation){
    if(!state.active||generation!==state.generation)return;
    const controller=new AbortController();state.captureController=controller;
    const started=performance.now();
    try{
      const frame=await api("/api/screen/frame",{source_id:appState.screenSource.id,roi:captureRoi()},controller.signal);
      if(generation!==state.generation)return;
      state.latestFrame=frame;
      rememberFrame(frame);
      byId("frame-image").src=frame.image_base64;byId("frame-image").hidden=false;byId("preview-placeholder").hidden=true;
      byId("capture-status").textContent=`${frame.occlusion_safe?'窗口采集 · 可遮挡':'屏幕采集 · 请勿遮挡游戏'} · ${new Date(frame.captured_at*1000).toLocaleTimeString("zh-CN",{hour12:false})}`;
    }catch(error){if(generation===state.generation){
      if(error.status===503&&byId("monitor-auto").checked)waitForSource(error.message);
      else {stop(error.message,true);toast(error.message,true);}
    }}
    finally{if(state.captureController===controller)state.captureController=null;}
    if(state.active&&generation===state.generation)state.captureTimer=setTimeout(()=>captureLatest(generation),Math.max(0,100-(performance.now()-started)));
  }
  async function tick(generation){
    if(!state.active||generation!==state.generation)return;
    if(state.busy){state.timer=setTimeout(()=>tick(generation),250);return;}
    state.busy=true;state.started=performance.now();const controller=new AbortController();state.controller=controller;
    let delay=Number(byId("observe-interval").value)*1000;
    try{
      const frame=state.latestFrame;
      if(!frame){state.timer=setTimeout(()=>tick(generation),50);return;}
      state.latestFrame=null;
      status("正在识别");window.gameplanAdvisor?.reading();
      const recent=skillFrames(frame);
      const response=await api("/api/vision/observe",{match_id:appState.matchId,image_base64:frame.image_base64,captured_at:frame.captured_at,recent_frames:recent,perspective_side:context().side,input_kind:"live",focus:byId("observe-focus").value,model:byId("vision-model").value},controller.signal);
      if(generation===state.generation){
        if(state.panelFrame&&[...recent,frame].some(item=>item.captured_at===state.panelFrame.captured_at))state.panelFrame=null;
        if(["bp","loading","result"].includes(response.observation.phase))state.panelFrame=null;
        await render(response,frame.image_base64);
      }
    }catch(error){
      if(generation!==state.generation)return;
      if(error.captureFailure||error.status===422){stop(error.message,true);toast(error.message,true);return;}
      if(error.status!==429)state.errors++;
      if(state.errors>=3){stop("连续识别失败，已暂停",true);toast(error.message,true);return;}
      clearObservation("本帧未能识别，等待最新画面",true);delay=error.status===429?2000:state.errors*3000;status(error.status===429?"模型正在处理上一帧，稍后重试":"识别暂不可用，正在重试");
    }finally{state.busy=false;if(state.controller===controller)state.controller=null;}
    if(state.active&&generation===state.generation)state.timer=setTimeout(()=>tick(generation),delay);
  }
  function waitForMedia(video,event,signal,begin){
    return new Promise((resolve,reject)=>{
      let timer;
      const finish=error=>{clearTimeout(timer);video.removeEventListener(event,ready);video.removeEventListener("error",failed);signal.removeEventListener("abort",aborted);error?reject(error):resolve();};
      const ready=()=>finish(),failed=()=>finish(new Error("无法解码此视频，请选择浏览器支持的 MP4 或 WebM 文件。")),aborted=()=>finish(new DOMException("已停止视频分析","AbortError"));
      if(signal.aborted){aborted();return;}
      video.addEventListener(event,ready,{once:true});video.addEventListener("error",failed,{once:true});signal.addEventListener("abort",aborted,{once:true});
      timer=setTimeout(()=>finish(new Error("读取视频超时，请重新选择视频。")),30000);
      try{begin();}catch(error){finish(error);}
    });
  }
  const videoTime=seconds=>`${Math.floor(seconds/60).toString().padStart(2,"0")}:${Math.floor(seconds%60).toString().padStart(2,"0")}`;
  function retryDelay(delay,signal){
    return new Promise((resolve,reject)=>{
      const abort=()=>{clearTimeout(timer);signal.removeEventListener("abort",abort);reject(new DOMException("已停止视频分析","AbortError"));};
      const timer=setTimeout(()=>{signal.removeEventListener("abort",abort);resolve();},delay);
      signal.addEventListener("abort",abort,{once:true});if(signal.aborted)abort();
    });
  }
  async function analyzeVideo(file,resume=false){
    if(!file || !file.size) throw new Error("视频文件为空，请重新选择文件。");
    if(file.size>4*1024*1024*1024) throw new Error("视频不能超过 4GB，请先压缩或截取需要分析的片段。");
    if(['complete','all60'].includes(resume?state.videoJob?.mode:byId('video-mode')?.value))return analyzeCompleteVideo(file,resume);
    const job=resume?state.videoJob:{file,mode:byId("video-mode").value,next:0,epoch:Date.now()/1000};
    const smart=job.mode==="smart60",stride=job.mode==="stride10",candidates=smart?new window.VideoCandidates():null;
    const frameStride=stride?new window.FrameStride():null,replayWindow=stride?new window.ReplayWindow():null;
    let strideBoundary=job.next,lastStrideTime=job.next,uncoveredSeconds=0;
    const panelBuffer=stride?new window.PanelFrameBuffer():null;
    stop("正在读取视频",true);state.videoJob=job;state.active=true;state.errors=0;
    if(!resume){clearServerMatch();clearBpContext();clearObservation("正在读取新视频");state.count=0;byId("frame-count").textContent="0 帧";appState.matchId=crypto.randomUUID();byId("match-label").textContent="# "+appState.matchId.slice(0,8);}
    byId("video-status").hidden=false;byId("preview-paused").hidden=true;status(`正在读取视频：${file.name}`);controls();
    const generation=state.generation,controller=new AbortController();state.controller=controller;
    const video=document.createElement("video");video.preload="auto";video.muted=true;video.playsInline=true;
    let url,frameCallback=null;
    try{
      url=URL.createObjectURL(file);
      await waitForMedia(video,"loadeddata",controller.signal,()=>{video.src=url;video.load();});
      if(!Number.isFinite(video.duration)||video.duration<=0||!video.videoWidth||!video.videoHeight)throw new Error("视频没有可读取的画面或时长。");
      channel?.postMessage({owner});
      await prepareModel(controller.signal);if(generation!==state.generation)return;
      const canvas=document.createElement("canvas"),ctx=canvas.getContext("2d");if(!ctx)throw new Error("浏览器无法创建视频画布。");
      canvas.width=Math.min(smart?960:1280,video.videoWidth);canvas.height=Math.round(video.videoHeight*canvas.width/video.videoWidth);
      if(job.next>0)await waitForMedia(video,"seeked",controller.signal,()=>{video.currentTime=Math.min(job.next,video.duration);});
      if(generation!==state.generation)return;
      state.video=video;video.style.cssText="width:100%;max-height:100%;object-fit:contain";
      byId("frame-image").hidden=true;byId("preview-placeholder").hidden=true;
      byId("frame-image").parentElement.append(video);
      await video.play();
      let sampledTime=-1,pendingTime=null,completedTime=null,lastError=null;
      let lastDispatch=-Infinity,lastIdentity=-Infinity,lastFocus=null,previousPixels=null;
      const probe=document.createElement("canvas");probe.width=64;probe.height=36;
      const probeCtx=probe.getContext("2d");
      const motion=()=>{
        if(!probeCtx?.getImageData)return 0;
        probeCtx.drawImage(video,0,0,64,36);
        const pixels=probeCtx.getImageData(0,0,64,36).data;
        let sum=0,count=0;
        // Central playfield excludes most fixed HUD, but camera motion can still trigger.
        if(previousPixels)for(let y=7;y<30;y++)for(let x=12;x<55;x++){
          const i=(y*64+x)*4;sum+=Math.abs(pixels[i]-previousPixels[i])+Math.abs(pixels[i+1]-previousPixels[i+1])+Math.abs(pixels[i+2]-previousPixels[i+2]);count+=3;
        }
        previousPixels=pixels;return count?sum/count:0;
      };
      const videoProgress=()=>{
        if(generation!==state.generation)return;
        const current=video.currentTime;
        window.gameplanEnemies?.setPlaybackTime(current);
        const parts=[`播放 ${videoTime(current)} / ${videoTime(video.duration)}`];
        if(pendingTime!==null)parts.push(`正在识别 ${videoTime(pendingTime)} · 落后 ${Math.max(0,current-pendingTime).toFixed(1)} 秒`);
        if(completedTime!==null)parts.push(`最近完成 ${videoTime(completedTime)}`);
        parts.push(stride?`每隔 10 帧取 1 帧 · 最近一秒 ${replayWindow.frames.filter(f=>current-f.t<1).length} 帧 · 最多 6 张覆盖最近 4 秒 · 更新目标 3 秒 · 本段未覆盖 ${uncoveredSeconds.toFixed(1)} 秒 · 回调跨过取样点 ${frameStride.missed} 次`:smart?`智能筛选 · 采集目标 60 帧/秒 · 最近一秒 ${candidates.frames.filter(f=>current-f.t>=0&&current-f.t<1).length} 帧 · 缓存 ${candidates.frames.length}/180 帧 · 合并 ${candidates.merged} · 过期未分析 ${candidates.expired} · 跳过内容不代表未释放`:"跟随播放位置 · 不补查积压片段 · 冷却随播放时间递减");
        byId("capture-status").textContent=parts.join(" · ");
      };
      const sampleVideo=(mediaTime=video.currentTime,fromCallback=false)=>{
        if(stride&&!fromCallback)return;
        if(!smart)videoProgress();
        if(generation!==state.generation||sampledTime===mediaTime||smart&&mediaTime-sampledTime<1/60-.001)return;
        sampledTime=mediaTime;ctx.drawImage(video,0,0,canvas.width,canvas.height);
        const frame={image_base64:canvas.toDataURL(smart?"image/jpeg":"image/png",.8),captured_at:job.epoch+sampledTime};
        if(stride){
          replayWindow.add({...frame,t:sampledTime});
          if(probeCtx?.getImageData){probeCtx.drawImage(video,0,0,64,36);panelBuffer.add({...frame,t:sampledTime},window.PanelFrameBuffer.score(probeCtx.getImageData(0,0,64,36).data));}
        }
        else if(smart)candidates.add({...frame,t:sampledTime},motion());else rememberFrame(frame);
      };
      if(stride&&typeof video.requestVideoFrameCallback!=="function")throw new Error("当前浏览器不支持按视频帧计数，请使用新版 Edge 或 Chrome。");
      if((smart||stride)&&typeof video.requestVideoFrameCallback==="function"){
        const onFrame=(_,meta)=>{if(generation!==state.generation)return;if(!stride||frameStride.accept(meta.presentedFrames))sampleVideo(meta.mediaTime,true);frameCallback=video.requestVideoFrameCallback(onFrame);};
        frameCallback=video.requestVideoFrameCallback(onFrame);
        state.videoCaptureTimer=setInterval(videoProgress,200);
      }else state.videoCaptureTimer=setInterval(()=>sampleVideo(),smart?1000/60:200);
      let lastTime=-1;
      while(generation===state.generation){
        if(stride){
          replayWindow.prune(video.currentTime);
          if((!replayWindow.frames.length&&!panelBuffer.frame)||(!video.ended&&video.currentTime-strideBoundary<1)){
            if(video.ended)break;
            await retryDelay(30,controller.signal);continue;
          }
        }
        const panelFrame=stride?panelBuffer.take(video.currentTime,lastTime):null;
        const strideFrames=stride&&!panelFrame?replayWindow.select(video.currentTime,lastStrideTime):[];
        const t=panelFrame?panelFrame.t:stride?strideFrames.at(-1)?.t:video.currentTime;
        if(t===undefined){if(video.ended)break;await retryDelay(30,controller.signal);continue;}
        if(t===lastTime){
          if(video.ended)break;
          await new Promise(resolve=>setTimeout(resolve,30));continue;
        }
        if(smart)sampleVideo();
        const o=state.latest?.observation;
        const identityNeeded=smart&&(!o||o.phase!=="in_game"||(o.enemy_roster||[]).length<5||(o.ally_roster||[]).length<5);
        const identify=identityNeeded&&lastFocus!=="heroes"&&(t-lastIdentity>=2);
        if(smart){
          candidates.prune(t);
          if(!identify&&t-lastDispatch<2&&candidates.pending===null&&!video.ended){await retryDelay(50,controller.signal);continue;}
          if(!identify&&t-lastDispatch<.35&&!video.ended){await retryDelay(50,controller.signal);continue;}
          if(!identify)candidates.take(t);lastDispatch=t;if(identify)lastIdentity=t;
        }
        lastTime=t;job.next=t;if(stride){strideBoundary=video.currentTime;if(!panelFrame)uncoveredSeconds=Math.max(0,strideFrames[0].t-lastStrideTime);}
        if(!stride)ctx.drawImage(video,0,0,canvas.width,canvas.height);const img=panelFrame?panelFrame.image_base64:stride?strideFrames.at(-1).image_base64:canvas.toDataURL("image/png");
        const captured=job.epoch+t;
        byId("frame-image").src=img;
        pendingTime=t;videoProgress();
        status(`${panelFrame?"优先核验短暂战绩面板":"视频实时识别"} · ${file.name}`);window.gameplanAdvisor?.reading();
        sampleVideo();
        const focus=smart?(identify?"heroes":"skills"):byId("observe-focus").value;
        lastFocus=focus;
        const recent=panelFrame?[]:stride?strideFrames.slice(0,-1).map(({image_base64,captured_at})=>({image_base64,captured_at})):smart?(identify?[]:candidates.select(t,7).filter(f=>f.captured_at<captured).map(({image_base64,captured_at})=>({image_base64,captured_at}))):skillFrames({captured_at:captured});
        let res;
        try{
          res=await api("/api/vision/observe",{match_id:appState.matchId,image_base64:img,captured_at:captured,video_time_s:t,recent_frames:recent,perspective_side:context().side,input_kind:"video",focus,model:byId("vision-model").value},controller.signal);
        }catch(error){
          if(controller.signal.aborted||generation!==state.generation)return;
          if(error.status&&error.status<500&&![408,429].includes(error.status))throw error;
          state.errors++;pendingTime=null;
          lastError=error.name==="TimeoutError"?"识别请求超时":error.name==="TypeError"?"无法连接本地分析服务":error.message;
          const message=state.errors>=3?`连续 ${state.errors} 次分析未完成：${lastError}；视频继续播放，将自动重试`:`本帧暂未识别：${lastError}；视频继续播放，正在重试`;
          status(message);byId("observation-note").textContent=message;
          window.gameplanAdvisor?.hold("识别暂不可用 · 保留上次分析");videoProgress();
          if(state.errors===3)toast(message,true);
          if(video.ended)break;
          // Retry a fresh playback frame; never queue stale frames behind the model.
          await retryDelay(error.status===429?1000:Math.min(10000,1000*2**Math.min(state.errors-1,4)),controller.signal);
          continue;
        }
        if(generation!==state.generation)return;
        if(stride&&!panelFrame)lastStrideTime=t;
        await render(res,img);
        lastError=null;
        completedTime=t;pendingTime=null;videoProgress();
        if(video.ended)break;
      }
      if(generation!==state.generation)return;
      state.active=false;state.videoJob=null;window.gameplanEnemies?.hold();status(lastError?`视频播放完成 · ${state.count} 帧已识别 · 最近画面未能识别：${lastError}`:`视频播放与识别完成 · ${state.count} 帧 · ${file.name}`);controls();
    }catch(error){
      if(generation===state.generation){stop(`视频分析失败：${error.message}`,true);toast(error.message,true);}
    }finally{
      if(generation===state.generation){clearInterval(state.videoCaptureTimer);state.videoCaptureTimer=null;}
      if(frameCallback!==null)video.cancelVideoFrameCallback?.(frameCallback);
      if(state.video===video){state.video=null;byId("frame-image").hidden=false;}video.remove();
      video.pause();video.removeAttribute("src");video.load();if(url)URL.revokeObjectURL(url);
      if(state.controller===controller)state.controller=null;
    }
  }
  async function reobserve(){
    if(state.busy)throw new Error("上一帧仍在处理，请稍后重试。");
    const image=byId("frame-image");
    if(image.hidden||!image.getAttribute("src"))throw new Error("请先选择视频或采集一张画面。");
    const data=image.src,inputKind=state.latest?.input_kind==="video"||state.videoJob?"video":"image";
    stop("正在重新识别当前画面",true);
    clearObservation("正在重新读取画面中的分路和英雄");
    const generation=state.generation,controller=new AbortController();state.controller=controller;
    state.busy=true;byId("coach-retry").disabled=true;
    try{
      const response=await api("/api/vision/observe",{match_id:appState.matchId,image_base64:data,captured_at:Date.now()/1000,input_kind:inputKind,focus:byId("observe-focus").value,model:byId("vision-model").value},controller.signal);
      if(generation!==state.generation)return;
      await render(response,data);
      if(generation===state.generation){window.gameplanEnemies?.hold();status("当前画面已重新识别 · 已暂停");byId("preview-paused").hidden=false;}
    }catch(error){if(generation===state.generation){clearObservation("重新识别失败，请重试");status("重新识别失败");throw error;}}
    finally{state.busy=false;if(state.controller===controller)state.controller=null;byId("coach-retry").disabled=false;controls();}
  }
  async function checkModel(signal){const h=await api("/api/vision/status?model="+encodeURIComponent(byId("vision-model").value),undefined,signal);state.frameLimit=h.inference_device==="cpu"?4:8;byId("model-status").textContent=`${h.model} · ${h.message}`;byId("skill-hardware-status").textContent=h.inference_device==="cpu"?h.message:"";byId("skill-hardware-status").hidden=h.inference_device!=="cpu";return h;}
  async function prepareModel(signal){
    const h=await checkModel(signal);signal.throwIfAborted();
    if(!h.ready)throw new Error(h.message);
    const path="/api/vision/warmup?model="+encodeURIComponent(byId("vision-model").value);
    status("正在预热视觉模型，完成后自动开始");byId("frame-time").textContent="等待模型就绪";
    for(;;){
      signal.throwIfAborted();
      try{await api(path,{},signal);signal.throwIfAborted();return h;}
      catch(error){
        if(signal.aborted||error.status!==429)throw error;
        // Cancelling a browser request leaves its model inference running until it finishes.
        status("模型正在处理上一帧，等待模型空闲后自动开始；可点击暂停取消等待");
        byId("frame-time").textContent="等待模型就绪";
        await retryDelay(1000,signal);
      }
    }
  }
  async function analyzeCompleteVideo(file,resume=false){
    const job=resume?state.videoJob:{file,mode:byId('video-mode').value,next:0,index:0,epoch:Date.now()/1000,completed:null};
    stop('正在读取视频',true);state.videoJob=job;state.active=true;state.errors=0;
    window.gameplanEnemies?.setPlaybackTime(null);
    if(!resume){clearServerMatch();clearBpContext();clearObservation('正在读取新视频');state.count=0;byId('frame-count').textContent='0 帧';appState.matchId=crypto.randomUUID();byId('match-label').textContent='# '+appState.matchId.slice(0,8);}
    const all60=job.mode==='all60',batchSize=all60?6:state.frameLimit;
    const generation=state.generation,controller=new AbortController();state.controller=controller;
    const video=document.createElement('video'),decoder=document.createElement('video');
    for(const media of [video,decoder]){media.preload='auto';media.muted=true;media.playsInline=true;}
    let url,pending=null;
    byId('video-status').hidden=false;byId('preview-paused').hidden=true;controls();
    const progress=()=>{
      if(generation!==state.generation)return;
      const parts=[`播放 ${videoTime(video.currentTime)} / ${videoTime(video.duration)}`];
      if(pending!==null)parts.push(`正在识别 ${videoTime(pending)} · 待追赶 ${Math.max(0,video.currentTime-pending).toFixed(1)} 秒`);
      if(job.completed!==null)parts.push(`最近完成 ${videoTime(job.completed)}`);
      parts.push(all60?'60 帧/秒完整识别 · 每批 6 帧 · 冷却按已分析的录像时间计算':'连续片段分析 · 冷却按已分析的录像时间计算');
      byId('capture-status').textContent=parts.join(' · ');
    };
    try{
      url=URL.createObjectURL(file);
      for(const media of [video,decoder])await waitForMedia(media,'loadeddata',controller.signal,()=>{media.src=url;media.load();});
      if(!Number.isFinite(video.duration)||video.duration<=0||!video.videoWidth||!video.videoHeight)throw new Error('视频没有可读取的画面或时长。');
      channel?.postMessage({owner});await prepareModel(controller.signal);if(generation!==state.generation)return;
      if(job.next>0)await waitForMedia(video,'seeked',controller.signal,()=>{video.currentTime=Math.min(job.next,video.duration);});
      state.video=video;video.style.cssText='width:100%;max-height:100%;object-fit:contain';
      byId('frame-image').hidden=true;byId('preview-placeholder').hidden=true;byId('frame-image').parentElement.append(video);
      await video.play();state.videoCaptureTimer=setInterval(progress,200);
      const canvas=document.createElement('canvas'),ctx=canvas.getContext('2d');if(!ctx)throw new Error('浏览器无法创建视频画布。');
      canvas.width=Math.min(1280,video.videoWidth);canvas.height=Math.round(video.videoHeight*canvas.width/video.videoWidth);
      const step=all60?1/60:.2,end=Math.max(0,Math.ceil(video.duration/step)-1);
      // Keep the cheap 200ms sweep for the whole recording, but promote a
      // sudden visual change to an event replay. The replay is deliberately
      // dense and overlaps the trigger on both sides so short flashes and
      // ultimate wind-ups cannot fall between ordinary samples.
      // 31 frames is the API burst limit (1.5s at 20 FPS); the normal sweep
      // still supplies the wider context before and after this dense window.
      const replayStep=.05,replayBefore=.75,replayAfter=.75;
      let previousProbe=null,lastReplayAt=-Infinity;
      const probeCanvas=document.createElement('canvas');probeCanvas.width=64;probeCanvas.height=36;
      const probeCtx=probeCanvas.getContext('2d');
      const motionScore=()=>{
        // Some embedded/browser test canvases do not expose pixel reads. In
        // that case retain the baseline sweep and simply skip promotion.
        if(!probeCtx||typeof probeCtx.getImageData!=='function')return 0;
        probeCtx.drawImage(canvas,0,0,64,36);
        const data=probeCtx.getImageData(0,0,64,36).data;
        if(!previousProbe){previousProbe=data;return 0;}
        let total=0,count=0;
        for(let i=0;i<data.length;i+=16){total+=Math.abs(data[i]-previousProbe[i])+Math.abs(data[i+1]-previousProbe[i+1])+Math.abs(data[i+2]-previousProbe[i+2]);count++;}
        previousProbe=data;return total/(count*3);
      };
      const denseReplay=async(trigger)=>{
        const start=Math.max(0,trigger-replayBefore),finish=Math.min(video.duration,trigger+replayAfter);
        const frames=[];const first=Math.ceil(start/replayStep),last=Math.floor(finish/replayStep);
        for(let index=first;index<=last;index++){
          const t=Math.min(video.duration,index*replayStep);
          if(Math.abs(decoder.currentTime-t)>.0001)await waitForMedia(decoder,'seeked',controller.signal,()=>{decoder.currentTime=t;});
          if(generation!==state.generation)return;
          ctx.drawImage(decoder,0,0,canvas.width,canvas.height);
          frames.push({image_base64:canvas.toDataURL('image/jpeg',.82),captured_at:job.epoch+t});
        }
        if(frames.length<2)return;
        const latest=frames.at(-1), replayTime=trigger;
        byId('frame-image').src=latest.image_base64;
        status(`事件回溯高密度识别 · ${file.name} · ${videoTime(replayTime)}`);
        const result=await api('/api/vision/observe',{match_id:appState.matchId,...latest,recent_frames:frames.slice(0,-1),video_time_s:replayTime,perspective_side:context().side,input_kind:'video',focus:'skills',model:byId('vision-model').value},controller.signal);
        if(generation!==state.generation)return;
        await render(result,latest.image_base64);
      };
      while(generation===state.generation){
        // Independent decoder: unprocessed source frames survive slow inference,
        // tab throttling and retries without retaining a whole video in memory.
        const first=job.index,last=Math.min(end,first+batchSize-1),frames=[];
        for(let index=first;index<=last;index++){
          const t=index*step;
          if(Math.abs(decoder.currentTime-t)>.0001)await waitForMedia(decoder,'seeked',controller.signal,()=>{decoder.currentTime=t;});
          if(generation!==state.generation)return;
          ctx.drawImage(decoder,0,0,canvas.width,canvas.height);
          frames.push({image_base64:canvas.toDataURL('image/png'),captured_at:job.epoch+t});
        }
        pending=last*step;progress();
        const latest=frames.at(-1);byId('frame-image').src=latest.image_base64;
        const probe=motionScore();
        // Cooldown prevents one sustained effect from spawning a replay on
        // every low-frequency tick. A new replay can start before the normal
        // loop advances, and its window is intentionally allowed to overlap.
        if(!all60 && probe>=18 && pending-lastReplayAt>=1.0){
          lastReplayAt=pending;
          try{await denseReplay(pending);}catch(error){
            if(controller.signal.aborted||generation!==state.generation)return;
            if(!(error.status&&error.status<500&&![408,429].includes(error.status)))
              status(`事件回溯暂未完成 · ${error.message}`);
          }
        }
        let accepted=false;
        while(!accepted&&generation===state.generation){
          status(`${all60?"视频 60 帧/秒完整识别":"视频连续片段分析"} · ${file.name}`);
          try{
            const result=await api('/api/vision/observe',{match_id:appState.matchId,...latest,recent_frames:frames.slice(0,-1),video_time_s:pending,all_frames:all60,perspective_side:context().side,input_kind:'video',focus:byId('observe-focus').value,model:byId('vision-model').value},controller.signal);
            if(generation!==state.generation)return;
            await render(result,latest.image_base64);
            if(['incomplete_answer','partial'].includes(result.skill_scan?.detector_status))throw new Error('本片段的技能判断未完成');
            accepted=true;state.errors=0;
          }catch(error){
            if(controller.signal.aborted||generation!==state.generation)return;
            if(error.status&&error.status<500&&![408,429].includes(error.status))throw error;
            state.errors++;
            status(`片段 ${videoTime(pending)} 等待重试：${error.message}；视频继续播放，保留本段画面`);
            await retryDelay(error.status===429?1000:Math.min(10000,1000*2**Math.min(state.errors-1,4)),controller.signal);
          }
        }
        job.completed=pending;pending=null;progress();
        if(last===end)break;
        job.index=all60?last+1:last; // overlap one frame so a boundary cast keeps its predecessor
        // Do not analyze unseen future playback. Decoding still covers missed
        // intervals after an ended/paused/background player catches up.
        while(generation===state.generation&&!video.ended&&video.currentTime<(job.index+batchSize-1)*step)await retryDelay(100,controller.signal);
      }
      if(generation!==state.generation)return;
      state.active=false;state.videoJob=null;window.gameplanEnemies?.hold();
      status(`${all60?"视频 60 帧/秒完整识别完成":"视频连续片段分析完成"} · ${state.count} 段 · ${file.name}`);controls();
    }catch(error){
      if(generation===state.generation)stop(`视频分析失败：${error.message}`,true);
    }finally{
      if(generation===state.generation){clearInterval(state.videoCaptureTimer);state.videoCaptureTimer=null;}
      if(state.video===video){state.video=null;byId('frame-image').hidden=false;}
      for(const media of [video,decoder]){media.pause();media.remove();media.removeAttribute('src');media.load();}
      if(url)URL.revokeObjectURL(url);if(state.controller===controller)state.controller=null;
    }
  }
  function waitForSource(message="等待已选投屏窗口连接，连接后自动监控"){
    stop(message,true);const generation=state.generation;
    const retry=async()=>{
      if(generation!==state.generation||!byId("monitor-auto").checked)return;
      try{await refresh();if(generation!==state.generation)return;if(byId("screen-source").value&&!state.sources.find(s=>s.id===byId('screen-source').value)?.minimized){await start();return;}}
      catch(error){status(error.message);}
      if(generation===state.generation&&byId("monitor-auto").checked)state.reconnectTimer=setTimeout(retry,2000);
    };
    state.reconnectTimer=setTimeout(retry,2000);
    controls();
  }
  async function start(){
    if(state.active)return;
    if(state.videoJob)return analyzeVideo(state.videoJob.file,true);
    byId("video-status").hidden=true;
    const source=state.sources.find(s=>s.id===byId("screen-source").value);if(!source)throw new Error("首次使用请选择游戏投屏窗口或屏幕，之后自动沿用。");
    if(source.minimized){waitForSource('投屏窗口已最小化，恢复后自动继续；被其他窗口遮挡不影响采集。');return;}
    stop("正在检查模型",true);state.active=true;state.errors=0;state.count=0;byId("frame-count").textContent="0 帧";byId("preview-paused").hidden=true;
    appState.screenSource=source;localStorage.setItem("gameplan-screen-source",source.id);controls();
    const generation=state.generation,controller=new AbortController();state.controller=controller;
    channel?.postMessage({owner});
    try{await prepareModel(controller.signal);if(generation!==state.generation)return;void captureLatest(generation).then(()=>{if(generation===state.generation)tick(generation);});}
    catch(error){if(generation===state.generation){stop(error.message,true);throw error;}}
  }
  async function refresh(){
    const selected=byId("screen-source").value||localStorage.getItem("gameplan-screen-source");
    const data=await api("/api/screen/sources");state.sources=data.sources;
    byId("screen-source").innerHTML='<option value="">请选择游戏投屏窗口或屏幕</option>'+state.sources.map(s=>`<option value="${s.id}">${escapeHtml(s.label)}</option>`).join("");
    if(state.sources.some(s=>s.id===selected))byId("screen-source").value=selected;
    regionControls();
    if(state.active&&appState.screenSource&&!state.sources.some(s=>s.id===appState.screenSource.id))waitForSource();
    controls();
  }
  byId("video-mode")?.addEventListener("change",()=>{
    if(!state.videoJob)return;
    const job=state.videoJob,wasActive=state.active;
    stop("视频分析方式已变更",true);
    job.mode=byId("video-mode").value;
    job.index=Math.floor(job.next/(job.mode==="all60"?1/60:.2));
    if(wasActive)analyzeVideo(job.file,true).catch(error=>toast(error.message,true));
  });
  byId("video-file")?.addEventListener("change",e=>{const f=e.target.files[0];e.target.value="";if(f)analyzeVideo(f).catch(err=>toast(err.message,true));});
  byId("video-mode").addEventListener("change",()=>{
    const job=state.videoJob,mode=byId("video-mode").value;
    if(!job||job.mode===mode)return;
    const active=state.active;
    // Preserve playback time and the match clock, but abandon the old analysis queue.
    stop("视频分析方式已切换",true);
    job.mode=mode;job.index=Math.floor(job.next*(mode==="all60"?60:5));job.completed=null;
    if(active)analyzeVideo(job.file,true).catch(error=>toast(error.message,true));
    else status("视频分析方式已切换，点击继续视频分析");
  });
  action("monitor-start",start);action("monitor-pause",()=>stop(state.videoJob?"已暂停视频分析，当前建议保留":"已暂停监控，当前建议保留",true));action("monitor-stop",()=>stop());action("screen-refresh",refresh);action("monitor-check",()=>checkModel());
  function clearServerMatch(){void api("/api/monitor/match/reset",{match_id:appState.matchId}).catch(()=>{});}
  action("monitor-new",()=>{stop("已新建对局，点击开始监控");clearServerMatch();clearBpContext();appState.matchId=crypto.randomUUID();byId("monitor-side").value="auto";byId("match-label").textContent="# "+appState.matchId.slice(0,8);});
  byId("screen-source").addEventListener("change",()=>{stop("采集窗口已更新");regionControls();clearServerMatch();clearBpContext();appState.matchId=crypto.randomUUID();byId("match-label").textContent="# "+appState.matchId.slice(0,8);localStorage.setItem("gameplan-screen-source",byId("screen-source").value);if(byId("screen-source").value&&byId("monitor-auto").checked)start().catch(error=>toast(error.message,true));});
  const savedRegion=localStorage.getItem("gameplan-monitor-region");
  if(Object.hasOwn(captureRegions,savedRegion))byId("capture-region").value=savedRegion;
  byId("capture-region").addEventListener("change",()=>{localStorage.setItem("gameplan-monitor-region",byId("capture-region").value);stop("采集区域已更新");if(byId("screen-source").value&&byId("monitor-auto").checked)start().catch(error=>toast(error.message,true));});
  for(const id of ["observe-focus","observe-interval","vision-model"])byId(id).addEventListener("change",()=>{localStorage.setItem("monitor-"+id,byId(id).value);if(id!=="observe-interval")stop("设置已变更，点击开始监控");});
  for(const id of ["monitor-side","monitor-hero"])byId(id).addEventListener("change",()=>{perspectiveControls();quickPlan();if(state.latest)window.gameplanEnemies?.observe(state.latest,context());window.gameplanEnemies?.setPlayer(context().player);window.gameplanAdvisor?.contextChanged();});
  byId("monitor-auto").checked=localStorage.getItem("gameplan-monitor-autostart")!=="false";
  byId("monitor-auto").addEventListener("change",()=>{localStorage.setItem("gameplan-monitor-autostart",String(byId("monitor-auto").checked));if(!byId("monitor-auto").checked&&state.reconnectTimer)stop("已取消自动重连",true);});
  if(channel)channel.onmessage=event=>{if(event.data?.owner!==owner&&(state.active||state.reconnectTimer))stop("监控已切换到另一个窗口");};
  window.gameplanLoading={analysisContext:context};
  window.gameplanMonitor={start,stop,reobserve};
  window.addEventListener("pagehide",()=>{stop("窗口已关闭");void fetch("/api/monitor/match/reset",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({match_id:appState.matchId}),keepalive:true}).catch(()=>{});channel?.close();});
  setInterval(()=>{if(state.active&&state.busy)byId("frame-time").textContent=`正在识别 · 等待 ${Math.floor((performance.now()-state.started)/1000)} 秒`;renderEnemyTimers();renderSkills();if(state.latest&&!Number.isFinite(state.latest.video_time_s)&&Date.now()/1000-state.latest.captured_at>180&&state.expiredFrame!==state.latest){state.expiredFrame=state.latest;window.gameplanAdvisor?.hold("画面已暂停或过期 · 保留上次分析，等待新画面");}},500);
  window.addEventListener("DOMContentLoaded",async()=>{
    byId("match-label").textContent="# "+appState.matchId.slice(0,8);
    for(const id of ["observe-focus","observe-interval","vision-model"]){const value=localStorage.getItem("monitor-"+id);if(value!==null){byId(id).value=value;if(!byId(id).value)byId(id).value=id==="observe-focus"?"full":id==="observe-interval"?"0":"qwen3-vl:8b-instruct";}}
    if(!localStorage.getItem("monitor-instruct-v1")){
      if(byId("vision-model").value==="qwen3-vl:8b"){byId("vision-model").value="qwen3-vl:8b-instruct";localStorage.setItem("monitor-vision-model","qwen3-vl:8b-instruct");}
      localStorage.setItem("monitor-instruct-v1","true");
    }
    if(!localStorage.getItem("monitor-realtime-v1")){byId("observe-interval").value="0";localStorage.setItem("monitor-observe-interval","0");localStorage.setItem("monitor-realtime-v1","true");}
    if(!localStorage.getItem("monitor-cast-subtitles-v1")){byId("observe-focus").value="skills";byId("observe-interval").value="0";localStorage.setItem("monitor-observe-focus","skills");localStorage.setItem("monitor-observe-interval","0");localStorage.setItem("monitor-cast-subtitles-v1","true");}
    renderBpContext();
    try{await refresh();if(new URLSearchParams(location.search).get("monitor")!=="off"&&byId("monitor-auto").checked){
      if(byId("screen-source").value)await start();
      else if(localStorage.getItem("gameplan-screen-source"))waitForSource();
      else status("首次使用请选择游戏投屏窗口，之后打开页面自动监控");
    }else status("等待开始");}
    catch(error){stop(error.message);toast(error.message,true);}
  },{once:true});
})();








