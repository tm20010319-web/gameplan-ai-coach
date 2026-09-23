"use strict";
(()=>{
  const state={match:null,names:[],positions:Array(5).fill(null),allies:[],selected:null,key:null,generation:0,controller:null,plan:null,frame:null,knowledge:new Map(),knowledgeKey:null,heldAt:null,ended:false,seen:new Set(),subtitles:[]};
  const isUltimate=skill=>skill.is_ultimate??(skill.slot===3);
  const isSummoner=skill=>skill.is_ultimate===false||skill.slot===5||skill.skill==="闪现";
  const isKeyframe=timer=>timer?.timing_basis==="first_visible_effect";
  function playbackAge(){
    const playback=state.playback;
    return Number.isFinite(state.frame?.video_time_s)&&playback?.match===state.match
      ?Math.max(0,playback.time-state.frame.video_time_s):0;
  }
  const slotLabel=skill=>skill.passive||skill.slot===0?"被动":isUltimate(skill)?"大招":`${skill.slot} 技能`;
  function baseCooldown(skill){
    if(skill.cooldown_review_required)return "官网冷却字段待核对";
    if(skill.cooldown_kind==="dynamic")return "动态冷却："+skill.cooldown_text;
    const values=(skill.cooldown_s||[]).filter(value=>Number.isFinite(value)&&value>=0);
    if(!values.length)return "基础冷却："+(skill.cooldown_text||"资料待补充");
    if(values.every(value=>value===0))return skill.passive?"被动效果 · 无固定冷却":"无固定冷却，见技能机制";
    return `基础冷却 ${values.join(" / ")} 秒${values.length>1?"（按技能等级）":""}`;
  }
  function skillMarkup(hero,skill){
    return `<section class="enemy-skill ${isUltimate(skill)?"ultimate-skill":""}"><div class="enemy-skill-heading"><h4>${escapeHtml(slotLabel(skill))} · ${escapeHtml(skill.name)}</h4><span class="skill-base-cooldown">${escapeHtml(baseCooldown(skill))}</span></div><p>${escapeHtml(skill.description||"技能介绍待补充。")}</p>${skill.cooldown_note?`<p class="muted">${escapeHtml(skill.cooldown_note)}</p>`:""}${skill.special_mechanic?'<small class="muted">含特殊机制，计时条件需结合技能描述核对。</small>':""}${skill.passive||skill.slot===0?"":`<div class="skill-live-state" data-skill-hero="${escapeHtml(hero)}" data-skill-slot="${escapeHtml(skill.slot)}" data-skill-name="${escapeHtml(skill.name)}"></div>`}</section>`;
  }
  function knowledge(){
    const box=byId("enemy-skill-reference");
    const key=JSON.stringify(state.names.map(hero=>[hero,state.knowledge.get(hero)]));
    if(state.knowledgeKey===key)return;
    state.knowledgeKey=key;
    if(!state.names.length){box.innerHTML='<p class="muted">尚未识别敌方英雄。识别到阵容后，这里会显示大招基础冷却和技能介绍，无需等待施放。</p>';return;}
    box.innerHTML=state.names.map(hero=>{
      const record=state.knowledge.get(hero),skills=record?.skills||[],ultimate=skills.find(isUltimate),others=skills.filter(skill=>!isUltimate(skill));
      const source=record?.source_url&&/^https:\/\/pvp\.qq\.com\//.test(record.source_url)?`<a href="${escapeHtml(record.source_url)}" target="_blank" rel="noopener noreferrer">官网技能资料</a>`:"本地技能资料";
      return `<article class="enemy-hero-skills"><header><h3>${escapeHtml(hero)}</h3><small>${source} · 视觉候选，请核对</small></header>${ultimate?skillMarkup(hero,ultimate):'<p class="muted">大招资料待补充，基础冷却未知。</p>'}${others.length?`<details class="enemy-other-skills"><summary>查看其余 ${others.length} 个技能与被动介绍</summary>${others.map(skill=>skillMarkup(hero,skill)).join("")}</details>`:""}${!record?'<p class="muted">技能资料暂不可用，等待下次识别重试。</p>':""}</article>`;
    }).join("");
  }
  function timerSkill(timer){
    if(isSummoner(timer))return undefined;
    const skills=state.knowledge.get(timer.hero)?.skills||[];
    return skills.find(skill=>skill.name===timer.skill)||skills.find(skill=>skill.slot===timer.slot);
  }
  function ultimateState(hero){
    if(state.frame?.observation?.phase!=="in_game")return {status:"not_started"};
    const reading=state.frame?.enemy_ultimate_states?.find(item=>item.hero===hero);
    if(!reading||!Number.isInteger(reading.level))return {status:"level_unknown"};
    const now=Number.isFinite(state.frame?.video_time_s)?state.frame.captured_at+playbackAge():state.heldAt??Date.now()/1000;
    if(reading.level<4&&(!Number.isFinite(reading.level_captured_at)||now-reading.level_captured_at>10))return {status:"level_unknown"};
    return {...reading,level_stale:!Number.isFinite(reading.level_captured_at)||now-reading.level_captured_at>10};
  }
  function provisional(hero,skill){
    const items=state.frame?.enemy_skill_estimates||[];
    const now=Number.isFinite(state.frame?.video_time_s)?state.frame.captured_at+playbackAge():state.heldAt??Date.now()/1000;
    return items.find(e=>e.hero===hero&&(skill==="大招"?e.is_ultimate:skill==="召唤师技能"?!e.is_ultimate:e.skill===skill)&&e.expires_at>now&&(e.is_ultimate||[null,e.skill].includes(summonerState(hero).skill??null)));
  }
  function provisionalText(item){
    if(!item)return "等待确认，未开始计时";
    if(item.status==="retracted")return "估算已撤销 · "+item.cooldown_basis;
    if(!Array.isArray(item.remaining_range_s))return item.cooldown_basis||"起手时间未知，暂时无法估算";
    const age=Number.isFinite(state.frame?.video_time_s)?playbackAge():Math.max(0,(state.heldAt??Date.now()/1000)-(state.frame.processed_at||state.frame.captured_at));
    const [low,high]=item.remaining_range_s.map(v=>Math.max(0,Math.ceil(v-age)));
    return high===0?"疑似释放 · 估算冷却已结束，仍待确认":`疑似${item.is_ultimate===false?item.skill:"大招"} · 估算剩余 ${low===high?high:low+"～"+high} 秒 · ${item.identity_confirmed===false?"身份待确认":"待复核"}${item.timing_basis==="first_visible_candidate"?" · 首次观察起算":""}`;
  }
  function ultimateText(hero,timer){
    if(state.ended){
      const reading=state.frame?.enemy_ultimate_states?.find(item=>item.hero===hero);
      return `本局结束 · ${Number.isInteger(reading?.level)?`最后识别 ${reading.level} 级`:"等级未识别"}${timer?" · "+timerText(timer):""}`;
    }
    const reading=ultimateState(hero);
    if(reading.status==="not_started")return "等待进入对局";
    if(reading.status==="locked")return `${reading.level} 级 · 大招未解锁（4 级解锁）`;
    const estimated=provisional(hero,"大招");
    if(estimated&&(!timer||estimated.captured_at>timer.captured_at+1.5))return provisionalText(estimated)+(timer?" · 上次确认："+timerText(timer):"");
    if(isKeyframe(timer))return `${timerExpired(timer)?"上次大招":"大招特效已识别"} · ${timerText(timer)}`;
    const suspected=latestSuspicions().find(item=>item.hero===hero&&item.is_ultimate);
    if(suspected)return (timer?"疑似再次开大 · 本次待确认，未重启计时":"疑似开大 · 等待确认，未开始计时")+" · "+suspicionReason(suspected);
    const scan=state.frame?.skill_scan;
    const label=reading.status!=="unlocked"?"等级待确认":reading.level_stale?`当前等级待更新（上次 ${reading.level} 级）`:`${reading.level} 级`;
    if(!timer&&scan?.failure_reason==='model_timeout')return `${label} · 本轮大招判断超时 · 冷却状态未知`;
    if(!timer&&['incomplete_answer','partial'].includes(scan?.detector_status)&&!scan?.reviewed_heroes?.includes(hero))return `${label} · 本轮大招尚未检查完整 · 冷却状态未知`;
    if(!timer&&['no_visible_target','waiting_identity'].includes(scan?.detector_status))return `${label} · 本轮未定位可核验目标 · 冷却状态未知`;
    if(reading.status!=="unlocked")return "等级待确认 · 尚未确认释放";
    if(!timer&&state.frame?.enemy_skill_activity?.some(item=>item.hero===hero&&item.status==="possible_ongoing"))return `${label} · 采样时疑似使用大招 · 起手时间待确认`;
    return `${label} · ${timer?timerText(timer):"大招已解锁 · 冷却状态未知"}`;
  }
  function summonerState(hero){
    const reading=state.frame?.enemy_summoner_states?.find(item=>item.hero===hero);
    return reading?.status==="confirmed"&&reading.skill?reading:reading?.status==="confirming"?{...reading,skill:null}:{hero,skill:null,status:"unknown"};
  }
  function summonerText(hero,timer){
    const reading=summonerState(hero);
    if(state.ended)return `本局结束 · ${reading.skill||"携带技能未识别"}${timer?" · "+timerText(timer):""}`;
    const estimated=provisional(hero,"召唤师技能");
    if(estimated&&(!timer||estimated.captured_at>timer.captured_at+1.5))return `${estimated.skill} · ${provisionalText(estimated)}`+(timer?" · 上次确认："+timerText(timer):"");
    if(isKeyframe(timer))return `${timerExpired(timer)?"上次使用"+timer.skill:timer.skill+"特效已识别"} · ${timerText(timer)}`;
    const suspected=latestSuspicions().find(item=>item.hero===hero&&isSummoner(item));
    if(suspected)return timer?`疑似再次使用${suspected.skill} · 本次待确认，未重启计时`:`疑似使用${suspected.skill} · 等待确认，未开始计时`;
    if(reading.status==="confirming")return `召唤师图标核对中（${Math.min(3,Math.max(1,reading.confirmation_count||1))}/3）`;
    if(!reading.skill)return "召唤师技能待识别";
    if(state.frame?.observation?.phase!=="in_game")return `${reading.skill} · 已确认携带`;
    return timer?`${reading.skill} · ${timerText(timer)}`:`${reading.skill} · ${reading.source==="manual"?"你已核对携带 · ":"已确认携带 · "}未确认使用`;
  }
  function latestSuspicions(){
    if(state.ended)return [];
    if(state.frame?.observation?.phase!=="in_game"||state.frame?.focus==="heroes")return [];
    const video=Number.isFinite(state.frame.video_time_s),now=video?state.frame.captured_at+playbackAge():state.heldAt??Date.now()/1000;
    return (state.frame.enemy_skill_suspicions||[]).filter(item=>{
      if(!state.names.includes(item.hero)||item.status!=="suspected")return false;
      // A later confirmation supersedes this candidate. An older cast must
      // not hide a new suspected use after its cooldown or a possible refresh.
      if((state.frame.enemy_skill_timers||[]).some(timer=>timer.hero===item.hero&&timer.skill===item.skill&&timer.captured_at>=item.captured_at))return false;
      const expires=video?item.expires_at:item.display_expires_at??item.expires_at;
      if(!Number.isFinite(expires)||expires<=now)return false;
      if(isSummoner(item))return [null,item.skill].includes(summonerState(item.hero).skill??null);
      return item.is_ultimate&&ultimateState(item.hero).status!=="locked";
    });
  }
  function suspicionReason(item){
    return ({visual_identity_unconfirmed:"看到了疑似技能特效，角色身份仍待核实",model_uncertain:isSummoner(item)?"召唤师技能与其他特效尚未区分":"大招与其他技能尚未区分",ongoing_onset_unknown:"看到了持续特效，起手时间未知",
      onset_before_sequence:"首帧已有特效，缺少释放前画面",onset_identity_unconfirmed:"释放前的英雄身份未确认",
      large_field_unconfirmed:"大招范围特效未核实",transformation_unconfirmed:"变身过程未核实",
      flash_motion_unconfirmed:"闪现位移未核实",pair_recheck_unconfirmed:"局部候选待连续画面复核",
      awaiting_level_or_equipment_confirmation:isSummoner(item)?`携带${item.skill}尚未核实`:"释放时等级尚未核实"})[item.reason]||"释放证据不足";
  }
  function latestTimers(){
    const timers=new Map();
    if(state.frame?.observation?.phase!=="in_game")return [];
    for(const timer of state.frame.enemy_skill_timers||[]){
      if(!state.names.includes(timer.hero))continue;
      if(isUltimate(timerSkill(timer)||timer)&&(isKeyframe(timer)?ultimateState(timer.hero).status==="locked":ultimateState(timer.hero).status!=="unlocked"))continue;
      if(isSummoner(timer)&&(isKeyframe(timer)?![undefined,null,timer.skill].includes(summonerState(timer.hero).skill):summonerState(timer.hero).skill!==timer.skill))continue;
      const key=JSON.stringify([timer.hero,timerSkill(timer)?.slot??timer.slot??timer.skill]),previous=timers.get(key);
      if(!previous||Number(timer.captured_at)>=Number(previous.captured_at))timers.set(key,timer);
    }
    return [...timers.values()];
  }
  function timerAge(){
    const now=state.heldAt??Date.now()/1000;
    return Number.isFinite(state.frame?.video_time_s)?playbackAge():Math.max(0,now-(state.frame?.processed_at||state.frame?.captured_at||now));
  }
  function timerExpired(timer){
    const bounds=timer?.remaining_range_s;
    const remaining=Array.isArray(bounds)&&bounds.length===2&&bounds.every(Number.isFinite)?Math.max(...bounds):timer?.remaining_s;
    return Number.isFinite(remaining)&&remaining<=timerAge();
  }
  function timerText(timer,kind=""){
    if(!timer)return kind?`可能还有${kind} · ${kind==="闪现"?"携带及施放待确认":"未确认施放"}`:"未确认施放 · 剩余冷却未知";
    const skill=timerSkill(timer);
    if(skill?.cooldown_review_required&&!timer.reference_estimate)return "官网冷却字段待核对 · 剩余冷却未知";
    if(skill?.cooldown_kind==="dynamic")return "动态冷却 · 需确认目标技能冷却后计算";
    // A known ultimate cooldown is sufficient; optional mechanism metadata
    // explains the estimate and must not suppress its ticking clock.
    const baseEstimate=isUltimate(skill||timer)&&(timer.special_base_estimate===true||skill?.special_mechanic===true);
    const recharge=timer.hero==="马超"&&timer.charge_recovery_estimate===true&&isUltimate(skill||timer);
    if(skill?.special_mechanic&&!baseEstimate&&!recharge)return "已识别施放 · 特殊机制待核对，剩余冷却未知";
    if(!Number.isFinite(timer.remaining_s))return "已识别施放 · 剩余冷却未知";
    const age=timerAge();
    const remaining=Math.max(0,timer.remaining_s-age);
    const bounds=timer.remaining_range_s;
    const low=Array.isArray(bounds)&&bounds.length===2&&bounds.every(Number.isFinite)?Math.ceil(Math.max(0,bounds[0]-age)):null;
    const high=Array.isArray(bounds)&&bounds.length===2&&bounds.every(Number.isFinite)?Math.ceil(Math.max(0,bounds[1]-age)):null;
    if(recharge){
      const seconds=high!==null&&low!==high?`${low}–${high}`:Math.ceil(remaining);
      const text=remaining>0?`充能恢复估算剩余 ${seconds} 秒 · 可能仍有存储次数`:'本次充能恢复估算结束 · 剩余次数待确认';
      return (state.ended?"结束时：":state.heldAt?"暂停时：":"")+text;
    }
    const text=high!==null&&low!==high?`预计剩余 ${low}–${high} 秒（${timer.cast_window_start!==null&&Number.isFinite(timer.cast_window_start)?"知识库与起手范围估算":"知识库档位"}）`:remaining>0?`预计剩余 ${Math.ceil(remaining)} 秒`:"冷却估算结束 · 能否释放待确认";
    return (state.ended?"结束时：":state.heldAt?"暂停时：":"")+text+(baseEstimate?(timer.hero==="瑶"?" · 基础估算（未计附身/脱离调整）":" · 基础估算（未计刷新/返还等特殊调整）"):"")+(timer.reference_estimate?" · 官网参考值，版本待核对":"")+(isSummoner(timer)?" · 基础估算（未计冷却调整）":"");
  }
  function tick(){
    const timers=latestTimers();
    renderSubtitles(timers);
    const board=byId("enemy-cooldown-board");
    if(board)board.innerHTML=(document.body.classList.contains('overlay-mode')?state.positions:state.names).map((hero,index)=>{
      if(!hero)return `<div class="enemy-cooldown-row"><b>敌方 ${index+1}</b><span>待识别</span><span>待识别</span></div>`;
      const ultimate=timers.find(t=>t.hero===hero&&isUltimate(timerSkill(t)||t));
      const summoner=timers.find(t=>t.hero===hero&&isSummoner(t));
      return `<div class="enemy-cooldown-row"><b>${escapeHtml(hero)}</b><span>${escapeHtml(ultimateText(hero,ultimate))}</span><span>${escapeHtml(summonerText(hero,summoner))}</span></div>`;
    }).join("");
    for(const element of byId("enemy-skill-reference").querySelectorAll("[data-skill-hero]")){
      const timer=timers.find(timer=>timer.hero===element.dataset.skillHero&&(timerSkill(timer)?.name||timer.skill)===element.dataset.skillName);
      const skill=state.knowledge.get(element.dataset.skillHero)?.skills?.find(s=>s.name===element.dataset.skillName);
      element.textContent=isUltimate(skill||{})?ultimateText(element.dataset.skillHero,timer):timerText(timer);element.classList.toggle("has-cast",!!timer);
    }
    const box=byId("enemy-skill-timers"),frame=state.frame;
    if(state.ended&&!timers.length){box.textContent="本局已结束 · 保留最后识别记录";return;}
    if(!timers.length){
      box.textContent=!state.names.length?"识别到敌方英雄后显示技能资料；识别到一帧明确技能特效即可计时。":frame?.focus==="heroes"?"英雄优先模式只展示技能资料；切换“敌方大招与召唤师技能”后自动记录释放。":frame?.observation?.phase!=="in_game"?"当前为选人或加载阶段，展示基础冷却；进入对局并识别特效后显示倒计时。":"等待可归属敌方英雄的大招或召唤师技能特效；一帧识别成功即可计时。";
      return;
    }
    box.innerHTML=timers.map(timer=>{
      const cast=Number.isFinite(timer.video_time_s)?`录像 ${Math.floor(timer.video_time_s/60).toString().padStart(2,'0')}:${(timer.video_time_s%60).toFixed(1).padStart(4,'0')}`:Number.isFinite(timer.captured_at)?new Date(timer.captured_at*1000).toLocaleTimeString("zh-CN",{hour12:false}):"时间未知";
      return `<div class="enemy-timer"><div><b>${escapeHtml(timer.hero)} · ${isUltimate(timerSkill(timer)||timer)?"大招 · ":""}${escapeHtml(timer.skill)}</b><small>${isKeyframe(timer)?"特效首次识别":"自动识别 · 采样"} ${escapeHtml(cast)}</small>${timer.cooldown_basis?`<small>${escapeHtml(timer.cooldown_basis)}</small>`:""}</div><span>${escapeHtml(timerText(timer))}</span></div>`;
    }).join("");
  }
  function recordSubtitles(){
    const updates=state.frame?.enemy_skill_updates;
    const notices=new Map();
    const alert=(text)=>{
      try{
        const AudioContext=window.AudioContext||window.webkitAudioContext;
        if(AudioContext){const ctx=state.audioContext||(state.audioContext=new AudioContext());
          const osc=ctx.createOscillator(),gain=ctx.createGain();osc.type="sine";osc.frequency.value=880;
          gain.gain.setValueAtTime(.0001,ctx.currentTime);gain.gain.exponentialRampToValueAtTime(.12,ctx.currentTime+.015);
          gain.gain.exponentialRampToValueAtTime(.0001,ctx.currentTime+.22);osc.connect(gain).connect(ctx.destination);osc.start();osc.stop(ctx.currentTime+.24);}
      }catch{}
      if("Notification" in window){
        if(Notification.permission==="granted")new Notification("敌方技能识别",{body:text});
        else if(Notification.permission==="default")Notification.requestPermission().catch(()=>{});
      }
    };
    for(const timer of latestTimers()){
      const key=timer.id||JSON.stringify([timer.hero,timer.skill,timer.captured_at]);
      if(state.seen.has(key)||!Array.isArray(updates)||!updates.includes(timer.id))continue;
      if(!isSummoner(timer)&&!isUltimate(timerSkill(timer)||timer))continue;
      const now=Date.now()/1000,latency=(state.frame?.processed_at||now)-timer.captured_at;
      const sourceTime=Number.isFinite(timer.video_time_s)?`（录像 ${Math.floor(timer.video_time_s/60).toString().padStart(2,'0')}:${(timer.video_time_s%60).toFixed(1).padStart(4,'0')}）`:latency>5?`（${Math.ceil(latency)} 秒前的画面）`:'';
      const label=isSummoner(timer)?timer.skill:`大招（${timer.skill}）`;
      const text=isKeyframe(timer)?`识别到敌方${timer.hero}${label}特效${sourceTime} · 按首次识别计时 · ${timerText(timer)}`:`敌方${timer.hero}已用${label}${sourceTime} · ${timerText(timer)}`;
      state.seen.add(key);state.subtitles.push({timer,text,expiresAt:now+8});
      const labels=notices.get(timer.hero)||new Set();
      labels.add(isSummoner(timer)?timer.skill:"大招");notices.set(timer.hero,labels);
    }
    // One notification and one sound per response: desktop notification queues
    // may otherwise show only the last hero in a simultaneous burst.
    if(notices.size)alert(`识别到敌方 ${notices.size} 人技能：`+[...notices].map(([hero,labels])=>`${hero}：${[...labels].join("、")}`).join("；"));
    for(const candidate of latestSuspicions()){
      const key="suspected:"+candidate.id;
      if(!candidate.id||state.seen.has(key))continue;
      const kind=isSummoner(candidate)?`使用${candidate.skill}`:`开大（${candidate.skill}）`;
      const sourceTime=Number.isFinite(candidate.video_time_s)?`（录像 ${Math.floor(candidate.video_time_s/60).toString().padStart(2,'0')}:${(candidate.video_time_s%60).toFixed(1).padStart(4,'0')}）`:state.frame.processed_at-candidate.captured_at>5?`（${Math.ceil(state.frame.processed_at-candidate.captured_at)} 秒前的画面）`:"";
      state.seen.add(key);state.subtitles.push({timer:candidate,suspected:true,text:`敌方${candidate.hero}疑似${kind}${sourceTime} · ${suspicionReason(candidate)} · ${provisionalText(provisional(candidate.hero,candidate.skill))}`,expiresAt:Date.now()/1000+8});
    }
    state.subtitles=state.subtitles.slice(-10);
    if(state.seen.size>100)state.seen=new Set([...state.seen].slice(-100));
  }
  function renderSubtitles(timers){
    const box=byId("enemy-skill-subtitles"),status=byId("enemy-scan-status");if(!box)return;
    const now=state.heldAt??Date.now()/1000;
    const suspects=latestSuspicions();
    state.subtitles=state.subtitles.filter(item=>item.expiresAt>now&&state.names.includes(item.timer.hero)&&(item.suspected?
      suspects.some(s=>s.id===item.timer.id):
      timers.some(timer=>timer.id===item.timer.id)));
    // Keep the live-region text stable. The separate board owns ticking clocks.
    const text=state.subtitles.map(item=>item.text);
    const summary=byId("enemy-burst-summary");
    if(summary){
      const heroes=new Map();
      for(const item of state.subtitles.filter(item=>!item.suspected)){
        const labels=heroes.get(item.timer.hero)||new Set();
        labels.add(isSummoner(item.timer)?item.timer.skill:"大招");heroes.set(item.timer.hero,labels);
      }
      const brief=heroes.size>1?`最近识别到 ${heroes.size} 名敌人释放技能：`+[...heroes].map(([hero,labels])=>`${hero}（${[...labels].join("、")}）`).join("；"):"";
      if(summary.textContent!==brief)summary.textContent=brief;
      summary.hidden=!brief;
    }
    const markup=text.length?text.map(t=>`<p>${escapeHtml(t)}</p>`).join(""):"<p class=\"muted\">确认敌方释放后，这里会提示英雄名字、大招或召唤师技能。</p>";
    if(box.innerHTML!==markup)box.innerHTML=markup;
    box.classList.toggle("has-events",!!text.length);
    if(status){
      const scan=state.frame?.skill_scan;
      const target=scan?.target_latency_s??5;
      status.textContent=state.ended?"本局已结束 · 保留识别记录":state.heldAt?"已暂停 · 保留最近读数":state.frame?.focus==="heroes"?"英雄优先模式 · 技能识别未启用":!state.frame?"等待画面 · 单帧明确特效即可触发计时":scan?`采样画面 ${scan.frames} 张 · 本轮最早画面距结果 ${scan.latency_s} 秒${scan.over_target?` · 超过 ${target} 秒目标`:""}`:"正在观察敌方大招与召唤师技能";
      if(!state.heldAt&&Number.isFinite(state.frame?.video_time_s))status.textContent=`录像连续分析 · 本段 ${scan?.frames||1} 张 · ${state.playback?.match===state.match?"冷却随播放时间递减":"冷却按已分析的录像时间计算"}`;
      if(scan?.detection_policy==="single_frame_effect")status.textContent+=" · 单帧特效触发 · 按首次识别时间计时";
      if(scan?.visible_enemy_actors===0)status.textContent+=" · 本轮未定位到可核对归属的敌方角色";
      if(Array.isArray(scan?.reviewed_heroes)&&Array.isArray(scan?.pending_heroes)){
        status.textContent+=` · 已检查 ${scan.reviewed_heroes.length}/${scan.reviewed_heroes.length+scan.pending_heroes.length} 名敌人`;
        if(scan.pending_heroes.length)status.textContent+=` · ${scan.pending_heroes.join("、")}尚未检查完整`;
      }
      if(!state.heldAt&&state.frame?.observation?.phase==="in_game"&&state.frame?.focus!=="heroes"){
        const known=state.names.filter(hero=>Number.isInteger(ultimateState(hero).level)).length;
        status.textContent+=` · 敌方等级已确认 ${known} / ${state.names.length}`;
        if(scan?.rejected_candidates?.ultimate_level_unconfirmed_or_locked)status.textContent+=" · 大招候选未通过等级核对，未开始计时";
      }
      if(scan?.max_frame_gap_s>.75)status.textContent+=" · 画面间隔较大，瞬间释放可能漏记";
      if(scan?.detector_status==="waiting_identity")status.textContent+=" · 正在等待可辨认的英雄身份信息";
      if(scan?.detector_status==="waiting_gameplay_evidence")status.textContent+=" · 本轮未确认对局画面，未执行技能判断";
      if(scan?.detector_status==="loading_identity")status.textContent+=" · 正在从加载画面建立英雄身份对应";
      if(scan?.detector_status==="no_visible_target")status.textContent+=" · 未执行技能判断：未找到可核验的敌方角色；有昵称和等级的敌方血条可进入外形核验";
      if(scan?.detector_status==="incomplete_answer")status.textContent+=" · 本轮未得到完整技能判断";
      if(scan?.detector_status==="partial")status.textContent+=" · 本轮未检查完，后续轮换检查";
      const failure=({model_timeout:"模型回答超时，本轮未确认的技能不计时",model_unavailable:"模型连接失败",invalid_model_answer:"模型回答格式不完整",missing_skill_answer:"部分技能未回答，保留已通过核对的结果"})[scan?.failure_reason];
      if(failure)status.textContent+=" · "+failure;
      if(Number.isInteger(scan?.checks_total)&&scan.checks_total>0)status.textContent+=` · 完成 ${scan.checks_completed} / ${scan.checks_total} 项技能判断`;
      status.classList.toggle("scan-delayed",!!scan?.over_target);
    }
  }
  function details(){
    const hero=state.selected;
    byId("enemy-reference").hidden=!hero;
    if(!hero)return;
    byId("enemy-reference-title").textContent=hero+" · 敌方参考";
    const item=state.plan?.items?.find(item=>item.hero===hero);
    byId("enemy-threat").textContent=item?.threat?"主要威胁："+item.threat:"已识别英雄；具体威胁资料待确认。";
    byId("enemy-response").textContent=item?.advice?"应对思路："+item.advice:"先留意其位置和技能，避免单独探草。";
    const counters=(item?.counters||[]).filter(c=>!state.names.includes(c.hero));
    byId("enemy-counters").hidden=!!state.player||state.frame?.observation?.phase!=="bp";
    byId("enemy-counters").textContent=byId("enemy-counters").hidden?"":counters.length?"尚未确定英雄时的选人候选："+counters.map(c=>c.hero+"（"+c.reason+"）").join("；"):"暂无已收录的选人候选。";
  }
  function slots(){
    byId("enemy-count").textContent=state.names.length+" / 5";
    byId("enemy-slots").innerHTML=Array.from({length:5},(_,i)=>{
      const hero=state.positions[i];
      return hero?'<button type="button" class="enemy-slot" data-enemy-index="'+i+'" aria-pressed="'+(hero===state.selected)+'"><small>敌方 '+(i+1)+'</small><b>'+escapeHtml(hero)+'</b></button>':'<div class="enemy-slot empty-slot"><small>敌方 '+(i+1)+'</small><b>待显示</b></div>';
    }).join("");details();
  }
  function reset(){
    state.generation++;state.controller?.abort();
    Object.assign(state,{match:null,epoch:null,names:[],positions:Array(5).fill(null),allies:[],selected:null,key:null,controller:null,plan:null,frame:null,knowledge:new Map(),knowledgeKey:null,heldAt:null,ended:false,playback:null,player:null,seen:new Set(),subtitles:[]});
    byId("enemy-summoner-correction").value="";byId("enemy-correction-status").textContent="";
    byId("enemy-roster-status").textContent="敌方尚未显示或未能识别，等待选人画面。";slots();
    knowledge();tick();
  }
  function hold(message="已暂停 · 保留上次识别结果，等待新数据"){
    if(!state.frame)return;
    // Repeated disconnects and reconnect attempts must freeze at the same
    // instant, until observe() accepts a new enemy result.
    state.heldAt??=Date.now()/1000;
    byId("enemy-roster-status").textContent=message;
    tick();
  }
  function retain(message="保留本局已确认阵容，等待清晰画面"){
    if(!state.frame)return;
    byId("enemy-roster-status").textContent=message;
    tick();
  }
  async function observe(frame,context){
    const o=frame.observation;
    const playable=["bp","loading","in_game"].includes(o.phase);
    if(state.match!==appState.matchId||(playable&&frame.match_epoch&&state.epoch&&frame.match_epoch!==state.epoch)){reset();state.match=appState.matchId;}
    if(playable&&frame.match_epoch)state.epoch=frame.match_epoch;
    if(o.phase==="result"){state.ended=true;hold("本局已结束 · 保留本局识别记录");return;}
    if(!["bp","loading","in_game"].includes(o.phase)){retain("保留上次识别结果，等待新的对局画面");return;}
    if(frame.team_context?.side==="a")context={...context,side:"a"};
    if(o.phase==="loading"&&context.side==="neutral"&&!frame.bp_context){
      if(state.names.length){retain();return;}
      const match=state.match;reset();state.match=match;state.frame=frame;
      const known=(frame.loading_slots||[]).filter(slot=>slot.hero).length;
      byId("enemy-roster-status").textContent=`已保存 ${known} / 10 个英雄，双方阵营待确认。`;return;
    }
    const own=context.side==="b"?o.enemy_roster:o.ally_roster;
    const other=context.side==="b"?o.ally_roster:o.enemy_roster;
    const side=context.side==="b"?"left":"right";
    let positions=Array(5).fill(null);
    if(o.phase==="bp"&&Array.isArray(frame.bp_slots)){
      for(const slot of frame.bp_slots){
        if(slot.side===side&&Number.isInteger(slot.row)&&slot.row>=0&&slot.row<5&&slot.hero&&slot.status==="confirmed"&&!(own||[]).includes(slot.hero))positions[slot.row]=slot.hero;
      }
    }else if(Array.isArray(frame.loading_slots)&&frame.loading_slots.some(slot=>slot.side==="enemy_roster")){
      for(const slot of frame.loading_slots){
        if(slot.side==="enemy_roster"&&slot.hero&&Number.isInteger(slot.column)&&slot.column>=1&&slot.column<=5&&!(own||[]).includes(slot.hero))positions[slot.column-1]=slot.hero;
      }
      // Scoreboard discoveries can fill positions whose loading labels stayed unknown.
      for(const name of other||[]){if(!positions.includes(name)&&!(own||[]).includes(name)&&positions.includes(null))positions[positions.indexOf(null)]=name;}
    }else{
      [...new Set(other||[])].filter(name=>!(own||[]).includes(name)).slice(0,5).forEach((name,i)=>positions[i]=name);
    }
    const incoming=positions.filter(Boolean);
    if(o.phase!=="bp"&&state.names.length){
      if(!incoming.length){retain();return;}
      // Partial frames may fill gaps, but cannot erase confirmed match slots.
      // A complete corrected roster can replace them; order-only changes cannot.
      if(incoming.length<5||incoming.every(name=>state.names.includes(name))){
        const stable=state.positions.slice();
        for(const name of incoming){
          if(stable.includes(name)||!stable.includes(null))continue;
          const preferred=positions.indexOf(name);
          stable[stable[preferred]===null?preferred:stable.indexOf(null)]=name;
        }
        positions=stable;
      }
    }
    const names=positions.filter(Boolean);
    if(!names.length){
      if(state.heldAt!==null&&state.names.length)return;
      // An empty current BP reading must not inherit an older guessed roster.
      const match=state.match;reset();state.match=match;
      state.frame=frame;
      const known=(frame.loading_slots||[]).filter(slot=>slot.hero).length;
      byId("enemy-roster-status").textContent=frame.loading_slots?.length?`已保存 ${known} / 10 个英雄，${frame.side_status==="unknown"?"双方阵营待确认":"等待确认敌方槽位"}。`:"当前敌方槽位为空或身份待确认，等待清晰画面。";
      return;
    }
    state.frame=frame;state.heldAt=null;state.ended=false;state.player=context.player||o.player_hero||null;
    for(const record of frame.hero_knowledge||[])if(names.includes(record.hero))state.knowledge.set(record.hero,record);
    const key=JSON.stringify([positions,own]);
    byId("enemy-roster-status").textContent="从敌方队伍槽位识别 · 视觉候选，请核对。";
    if(state.key===key){knowledge();recordSubtitles();tick();details();return;}
    state.generation++;state.controller?.abort();const generation=state.generation;
    state.key=key;state.names=names;state.positions=positions;state.allies=own||[];state.plan=null;
    knowledge();recordSubtitles();tick();
    if(!names.includes(state.selected))state.selected=names[0];slots();
    byId("enemy-response").textContent="正在读取该阵容的 BP 参考…";
    const controller=new AbortController();state.controller=controller;
    try{
      const plan=await api("/api/bp/plan",{allies:state.allies,enemies:names},controller.signal);
      if(generation!==state.generation)return;
      state.plan=plan;details();
    }catch(error){if(generation===state.generation){state.key=null;byId("enemy-response").textContent="BP 参考暂不可用，下一次识别时重试。";}}
    finally{if(state.controller===controller)state.controller=null;}
  }
  byId("enemy-slots").addEventListener("click",event=>{
    const button=event.target.closest("[data-enemy-index]");if(!button)return;
    state.selected=state.positions[Number(button.dataset.enemyIndex)]||null;
    byId("enemy-summoner-correction").value="";byId("enemy-correction-status").textContent="";slots();
  });
  async function correctSummoner(clear=false){
    const hero=state.selected,match=appState.matchId,generation=state.generation;
    const skill=clear?null:byId("enemy-summoner-correction").value;
    if(!hero||!state.frame)return;
    if(!clear&&!skill){byId("enemy-correction-status").textContent="请先选择已核对的技能。";return;}
    const buttons=[byId("enemy-summoner-clear"),byId("enemy-summoner-save")];
    if(buttons.some(button=>button.disabled))return;
    buttons.forEach(button=>button.disabled=true);
    try{
      const result=await api("/api/monitor/summoner/correct",{match_id:match,hero,skill});
      if(match!==appState.matchId||generation!==state.generation)return;
      Object.assign(state.frame,result,{enemy_skill_updates:[]});tick();
      byId("enemy-correction-status").textContent=clear?`${hero}已恢复待识别。`:`${hero}已更正为${skill}，仅本局生效。`;
    }catch(error){if(match===appState.matchId&&generation===state.generation)byId("enemy-correction-status").textContent=error.message;}
    finally{buttons.forEach(button=>button.disabled=false);}
  }
  byId("enemy-summoner-save").addEventListener("click",()=>correctSummoner());
  byId("enemy-summoner-clear").addEventListener("click",()=>correctSummoner(true));
  window.gameplanEnemies={observe,reset,tick,
    setPlaybackTime(time){state.playback=Number.isFinite(time)?{match:appState.matchId,time}:null;tick();},
    setPlayer(player){state.player=player;details();},hold,retain};reset();
  window.addEventListener("pagehide",reset);
})();
