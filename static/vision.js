"use strict";

(() => {
  const state = { active:false, busy:false, generation:0, timer:null, sample:null, prior:null, catalog:[], lastVideoTime:null, controller:null, count:0, errors:0, startedAt:0 };
  const phases = {bp:"选人", loading:"加载", in_game:"对局中", result:"结算", not_game:"非游戏画面", unknown:"阶段未知"};
  const status = (text) => { byId("live-status").textContent = text; };
  const monitorChannel = typeof BroadcastChannel === "function" ? new BroadcastChannel("gameplan-monitor-session") : null;
  const monitorOwner = crypto.randomUUID();
  if (monitorChannel) monitorChannel.onmessage = event => {
    if (event.data?.owner !== monitorOwner && state.active) stop("监控已切换到独立窗口或其他工作台");
  };

  function stop(message="观察已暂停", preserve=false) {
    if (!preserve) window.gameplanAdvisor?.reset(message);
    window.gameplanLoading?.pause(preserve);
    const hadObservation = state.active || state.sample;
    state.active=false; state.generation++; clearTimeout(state.timer);
    state.controller?.abort();
    state.controller=null;
    state.sample=null; state.prior=null; state.lastVideoTime=null;
    byId("live-run").textContent="开始连续观察";
    byId("live-run").setAttribute("aria-pressed","false");
    byId("self-skill-timers").innerHTML='<span class="micro">动态技能读数已清除</span>';
    renderHeroKnowledge([], null);
    byId("live-summary").textContent=message;
    byId("live-json").textContent="当前没有有效观察";
    byId("live-note").textContent=message;
    byId("hero-follow-status").textContent=message;
    byId("live-metrics").textContent=`已识别 ${state.count} 帧 · 已暂停`;
    window.gameplanScreen?.onPaused(message);
    if(hadObservation && !preserve) resetHud("观察已暂停，等待新画面。");
    status(message);
  }

  function heroData(name){ return state.catalog.find(item=>item.hero===name); }
  function referenceCooldown(skill){
    if(skill.cooldown_review_required)return "官网冷却字段待核对";
    if(skill.cooldown_kind==="dynamic")return "动态冷却："+skill.cooldown_text;
    const values=skill.cooldown_s||skill.base_cooldowns_s||[];
    return values.length?`基础冷却 ${values.join(" / ")} 秒`:"冷却："+skill.cooldown_text;
  }
  function skillName(hero, slot){return heroData(hero)?.skills.find(skill=>skill.slot===slot)?.name || `${slot} 技能`;}
  function skillLabel(sample, skill){
    const age=(Date.now()/1000)-sample.captured_at;
    if(!sample.fresh || age<0 || age>12) return "读数过期";
    if(skill.remaining_s===null) return "无可读倒计时";
    const remaining=skill.remaining_s-age;
    return remaining>0 ? `约 ${Math.ceil(remaining)} 秒` : "计时结束 · 待确认";
  }
  function renderTimers(){
    if(state.sample){
      const sample=state.sample, observed=sample.observation;
      const skills=observed.self_skills;
      byId("self-skill-timers").innerHTML=skills.length ? skills.map(skill=>`<div class="skill-clock"><span>${escapeHtml(skillName(observed.player_hero,skill.slot))}</span><b>${escapeHtml(skillLabel(sample,skill))}</b><small>画面读数 ${escapeHtml(skill.visible_text || "未知")} · 自身技能</small></div>`).join("") : '<span class="micro">未读到自身技能倒计时；不推断技能已经可用。</span>';
    }
  }
  function renderHeroKnowledge(records, player){
    const target = byId("hero-knowledge");
    if (!records?.length) { target.innerHTML='<span class="micro">识别到英雄后自动读取技能资料</span>'; return; }
    const ordered = [...records].sort((a,b) => (a.hero === player ? -1 : 1) - (b.hero === player ? -1 : 1));
    target.innerHTML = ordered.slice(0, 5).map(record => {
      const ultimate = record.ultimate;
      const status = record.has_skill_data ? "本地资料" : "资料待补";
      return `<article class="hero-knowledge-card"><div class="hero-knowledge-head"><b>${escapeHtml(record.hero)}</b><small>${status}</small></div>`+
        (ultimate ? `<p><strong>大招 · ${escapeHtml(ultimate.name)}</strong><span>${escapeHtml(referenceCooldown(ultimate))}</span></p><p class="micro">${escapeHtml(ultimate.description || "暂无技能介绍")}</p>` : `<p class="micro">暂无完整技能资料，不能推断冷却时间。</p>`) +
        `<details><summary>查看技能介绍</summary>${(record.skills || []).map(skill => `<p><strong>${skill.passive ? "被动" : skill.is_ultimate ? "大招" : skill.slot + "技能"} · ${escapeHtml(skill.name)}</strong><span>${escapeHtml(referenceCooldown(skill))}</span><br><small>${escapeHtml(skill.description || "暂无介绍")} ${escapeHtml(skill.cooldown_note||"")}</small></p>`).join("")}</details></article>`;
    }).join("");
  }

  function applyObservation(response){
    const previous=state.prior;
    state.sample=response; state.prior=response;
    const observed=response.observation;
    const hero = observed.player_hero;
    byId("hero-follow-status").textContent = `${hero ? "最近识别的操控英雄：" + hero : "操控英雄待确认"} · ${phases[observed.phase]} · 采样 ${new Date(response.captured_at * 1000).toLocaleTimeString("zh-CN", {hour12:false})} · 本帧 ${response.elapsed_s} 秒${previous?.observation.player_hero && hero && previous.observation.player_hero !== hero ? " · 已识别到换英雄" : ""}`;
    state.count++;
    state.errors=0;
    byId("live-metrics").textContent=`已识别 ${state.count} 帧 · 最近耗时 ${response.elapsed_s} 秒 · 采样 ${new Date(response.captured_at * 1000).toLocaleTimeString("zh-CN", {hour12:false})}`;
    byId("live-note").textContent=observed.note || (observed.phase==="not_game" ? "当前是非游戏画面；屏幕监控仍在继续。" : "模型未补充画面说明。");
    const clock=observed.game_time_s===null ? "时间未知" : `${Math.floor(observed.game_time_s/60)}:${String(observed.game_time_s%60).padStart(2,"0")}`;
    byId("live-summary").textContent=`${phases[observed.phase]} · ${observed.player_hero || "操控英雄待确认"} · ${clock} · 本次 ${response.elapsed_s} 秒${response.fresh ? "" : " · 画面已过期，仅供检查"}`;
    byId("live-json").textContent=JSON.stringify(observed,null,2);
    renderTimers();
    renderHeroKnowledge(response.hero_knowledge, observed.player_hero);
    const lowHealth=response.fresh && previous?.fresh && observed.phase==="in_game" && previous.observation.phase==="in_game" &&
      observed.player_hero && previous.observation.player_hero===observed.player_hero &&
      observed.player_hp_percent!==null && observed.player_hp_percent<=25 &&
      previous.observation.player_hp_percent!==null && previous.observation.player_hp_percent<=25 &&
      observed.game_time_s!==null && previous.observation.game_time_s!==null && observed.game_time_s>=previous.observation.game_time_s &&
      response.captured_at>previous.captured_at && response.captured_at-previous.captured_at<=15;
    resetHud(response.fresh ? "持续读取可见信息，关键条件未确认时保持观察。" : "识别画面已过期，本次不生成实时建议。");
    if(lowHealth){
      byId("hud-tag").textContent="VISION / 连续画面提醒";
      byId("hud-title").textContent="状态偏低，先减少暴露";
      byId("hud-message").textContent="连续两次画面显示你的血量较低；先确认附近威胁，再选择补给时机。";
      byId("hud-backup").textContent="附近敌人、撤离路线和队友技能仍需确认。";
      byId("hud-meta").textContent="血量为视觉估读；画面过期即撤下。";
    }
    status(state.active ? "持续观察中" : "单次识别完成");
    window.gameplanLoading?.observe(response);
    window.gameplanAdvisor?.observe(response);
  }

  async function observe(generation){
    if(state.busy) throw new Error("上一帧仍在识别，请等待完成。");
    state.busy=true; status("正在识别最新画面");
    window.gameplanAdvisor?.reading();
    state.startedAt=performance.now();
    const controller=new AbortController();
    state.controller=controller;
    try{
      const payload=await visionPayload(controller.signal);
      if(generation!==state.generation || controller.signal.aborted)return;
      const response=await api("/api/vision/observe",payload,controller.signal);
      if(generation!==state.generation)return;
      applyObservation(response);
    }finally{state.busy=false;if(state.controller===controller)state.controller=null;}
  }

  async function tick(generation){
    if(!state.active || generation!==state.generation)return;
    const video=byId("frame-video");
    if(!appState.screenSource){
      if(video.hidden || video.paused || video.ended || (appState.stream && appState.stream.getVideoTracks().some(track=>track.readyState!=="live" || track.muted))){stop("画面暂停或共享中断，动态读数已清除");return;}
      if(state.lastVideoTime===video.currentTime){stop("未收到新的视频帧，请检查共享窗口");return;}
      state.lastVideoTime=video.currentTime;
    }
    try{await observe(generation);}
    catch(error){
      if(generation!==state.generation)return;
      state.errors++;
      if(error.captureFailure || state.errors>=3 || error.status===422){stop("观察已暂停："+error.message);toast(error.message,true);return;}
      state.sample=null;state.prior=null;
      window.gameplanAdvisor?.reset("本帧识别失败，等待新的有效画面");
      window.gameplanLoading?.pause();
      byId("hero-follow-status").textContent="本帧识别失败，英雄身份待确认";
      byId("self-skill-timers").innerHTML='<span class="micro">本次识别失败，旧读数已撤下</span>';
      byId("live-note").textContent=error.message;
      resetHud("识别暂不可用，等待下一帧。");
      status(`识别暂不可用，${state.errors * 3} 秒后重试（${state.errors}/3）`);
    }
    if(state.active && generation===state.generation)state.timer=setTimeout(()=>tick(generation),state.errors ? state.errors*3000 : Number(byId("observe-interval").value)*1000);
  }

  async function once(){
    if(state.busy)throw new Error("上一帧仍在识别，请等待完成。");
    stop("准备单次识别");
    const generation=state.generation;
    try{await observe(generation);}catch(error){if(generation===state.generation)stop("识别失败："+error.message);throw error;}
  }

  async function start(){
    if(!appState.screenSource && byId("frame-video").hidden)throw new Error("先选择本机屏幕并点击开始监控，或共享窗口 / 播放录像。");
    // Uploaded videos are the active source; resume playback before sampling.
    // A paused video otherwise makes the first tick treat the source as gone.
    const video = byId("frame-video");
    if(!appState.screenSource && !video.hidden && video.paused){
      await video.play();
    }
    stop("正在检查本地模型");
    const generation=state.generation;
    state.count=0;state.errors=0;
    state.active=true;
    byId("live-run").textContent="暂停观察";
    byId("live-run").setAttribute("aria-pressed","true");
    byId("vision-kind").value="observe";
    const controller=new AbortController();state.controller=controller;
    try{
      const health=await api("/api/vision/status?model="+encodeURIComponent(byId("vision-model").value),undefined,controller.signal);
      if(generation!==state.generation)return;
      if(!health.ready)throw new Error(health.message);
      monitorChannel?.postMessage({owner:monitorOwner});
      byId("model-status").textContent=`${health.model} · ${health.message}`;
      tick(generation);
    }catch(error){if(generation!==state.generation)return;stop(error.message);throw error;}
  }
  byId("live-run").addEventListener("click",async()=>{
    if(state.active){stop();return;}
    try{await start();}catch(error){toast(error.message,true);}
  });

  function renderReference(){
    const hero=heroData(byId("skill-hero").value);
    const skill=hero?.skills.find(item=>String(item.slot)===byId("skill-slot").value);
    byId("timer-seconds").value="";
    byId("timer-add").disabled=!hero || Boolean(skill?.passive);
    if(!hero || !skill){byId("skill-reference").textContent="该英雄的官网技能页暂未取得完整资料，冷却时间保留未知。";return;}
    byId("skill-reference").innerHTML=`<div class="skill-reference-card"><b>${escapeHtml(hero.hero)} · ${escapeHtml(skill.name)}</b><p>${skill.passive ? "被动技能" : "各技能等级基础冷却"}：${escapeHtml(skill.cooldown_text)}${skill.base_cooldowns_s.length ? " 秒" : ""}</p>${skill.special_mechanic ? '<p class="micro">含刷新、充能或特殊机制描述，请核对完整技能说明。</p>' : ''}<details><summary>技能说明与来源</summary><p>${escapeHtml(skill.description)}</p><a href="${escapeHtml(hero.source_url)}" target="_blank" rel="noopener">王者荣耀官网技能页 ↗</a><p class="micro">官网未标注当前对局版本，仅作基础资料参考。</p></details></div>`;
  }
  function selectHero(){
    const hero=heroData(byId("skill-hero").value);
    byId("skill-slot").innerHTML=(hero?.skills || []).map(skill=>`<option value="${skill.slot}">${skill.passive ? "被动" : skill.slot+" 技能"} · ${escapeHtml(skill.name)}</option>`).join("");
    if(hero && !hero.skills.length)byId("skill-slot").innerHTML=[1,2,3,4].map(slot=>`<option value="${slot}">${slot} 技能 · 请人工核对</option>`).join("");
    if(hero?.skills.some(skill=>skill.slot===1))byId("skill-slot").value="1";
    renderReference();
  }
  byId("skill-hero").addEventListener("change",selectHero);
  byId("skill-slot").addEventListener("change",renderReference);
  for(const id of ["roi-x","roi-y","roi-width","roi-height","vision-model","observe-focus"]){byId(id).addEventListener("change",()=>stop("识别范围、内容或模型改变，请重新开始观察"));}
  byId("frame-video").addEventListener("pause",()=>{if(state.active)stop("视频已暂停，保留当前识别建议。", true);});
  window.addEventListener("pagehide",()=>{stop("页面已关闭");monitorChannel?.close();});
  window.gameplanVision={stop,once,start};

  setInterval(()=>{
    if(state.busy && state.active)byId("live-metrics").textContent=`已识别 ${state.count} 帧 · 正在处理第 ${state.count+1} 帧 · 已等待 ${Math.floor((performance.now()-state.startedAt)/1000)} 秒`;
    renderTimers();
    if(state.sample && ((Date.now()/1000)-state.sample.captured_at>12 || (Date.now()/1000)<state.sample.captured_at)){
      resetHud("当前画面读数已过期，等待新画面。");
    }
  },500);

  api("/api/skills").then(catalog=>{
    state.catalog=catalog.heroes;
    byId("skill-count").textContent=`${state.catalog.filter(hero=>hero.skills.length).length} / ${state.catalog.length} 个条目有资料`;
    byId("skill-hero").innerHTML=state.catalog.map(hero=>`<option value="${escapeHtml(hero.hero)}">${escapeHtml(hero.hero)}${hero.skills.length ? "" : " · 资料待补"}</option>`).join("");
    if(heroData("王昭君"))byId("skill-hero").value="王昭君";
    selectHero();
  }).catch(error=>toast("技能资料加载失败："+error.message,true));
  api("/api/vision/status").then(health=>{
    if(!state.active && !state.busy)status(health.message);
    byId("model-status").textContent=`${health.model} · ${health.message}`;
  }).catch(error=>status("初始化失败："+error.message));
})();
