"use strict";

(() => {
  const state = { latest: null, signature: null, applied: null, controller: null, busy: false, generation: 0, result: null, timer: null, editing: false };
  const pipeline = text => { byId("coach-pipeline").textContent = text; };
  function reset(message = "观察已停止，等待新的游戏画面") {
    state.generation++; state.controller?.abort(); state.controller = null;
    clearTimeout(state.timer); state.timer = null;
    state.latest = null; state.signature = null; state.applied = null; state.result = null;
    state.editing = false;
    byId("coach-source").textContent = "等待画面";
    byId("coach-summary").textContent = message;
    byId("coach-meta").textContent = ""; byId("coach-detail").hidden = true;
    pipeline(message);
  }
  function historical(message="保留上次分析 · 等待更新") {
    if(!state.result)return;
    byId("coach-source").textContent="上次分析";
    if(!byId("coach-meta").textContent.includes("历史参考"))byId("coach-meta").textContent+=" · 历史参考";
    pipeline(message);
    state.applied=null;
  }
  function hold(message="已暂停 · 保留上次分析") {
    state.generation++;state.controller?.abort();state.controller=null;
    clearTimeout(state.timer);state.timer=null;state.latest=null;state.signature=null;
    if(state.result)historical(message);else pipeline(message);
  }
  function context() { return window.gameplanLoading?.analysisContext?.() || { side: "neutral", player: null, lineup: null }; }
  function signature(frame) {
    const o = frame?.observation;
    if (!o) return "null-frame";
    const hp = o.player_hp_percent;
    return JSON.stringify([o.phase, o.ally_roster, o.enemy_roster, o.player_hero,
      hp === null ? "unknown" : hp <= 25 ? "critical" : hp <= 50 ? "low" : "healthy",
      o.game_time_s === null ? null : Math.floor(o.game_time_s / 60), frame.input_kind, context()]);
  }
  async function providerStatus() {
    const health = await api("/api/coach/status");
    byId("coach-provider").textContent = `${health.model} · ${health.message}`;
    return health;
  }
  function render(result) {
    state.result = result;state.expiredResult=null;
    const local = result.source === "local_rules" || result.source === "local_fallback";
    byId("coach-source").textContent = result.source === "local_rules" ? "本地规则建议" : result.source === "local_fallback" ? "本地备用建议" : result.source === "deepseek" ? "DeepSeek 分析" : "等待新画面";
    byId("coach-summary").textContent = result.summary;
    const input = result.input_kind === "sample" ? "网上样本" : result.input_kind === "image" ? "上传截图" : result.input_kind === "video" ? "录像画面" : "屏幕观察";
    const player=result.player??context().player;
    byId("coach-meta").textContent = result.scope ? `${player?"建议对象："+player:"操控英雄待确认"} · ${input} · ${result.scope === "bp" ? "选人阶段应对参考" : result.scope === "general" ? "通用建议 · 分路待确认" : result.scope === "lane" ? "分路通用建议 · 英雄待确认" : result.scope === "live" ? "当前可见状态" : "阵容层面建议，非当前战斗指令"}${result.captured_at ? " · 画面 " + new Date(result.captured_at * 1000).toLocaleTimeString("zh-CN", {hour12:false}) : ""}` : "";
    pipeline(local ? "画面识别完成 · 已生成本地对局建议" : "等待有效信息");
    byId("coach-detail").hidden = local;
    if (!local) {
      byId("coach-reasoning").innerHTML = `<p>${escapeHtml(result.understanding)}</p><b>需要注意</b><ul>${(result.watch_for||[]).map(text=>`<li>${escapeHtml(text)}</li>`).join("")}</ul><b>争取优势</b><ul>${(result.opportunities||[]).map(text=>`<li>${escapeHtml(text)}</li>`).join("")}</ul>`;
    }
  }
  async function analyze(force = false) {
    if (!state.latest || state.busy || state.editing) return;
    if (!force && state.applied === state.signature && (!state.result?.expires_at || state.result.expires_at * 1000 > Date.now())) return;
    const generation = state.generation, activeSignature = state.signature, frame = state.latest;
    state.busy = true; const controller = new AbortController(); state.controller = controller;
    pipeline("画面识别完成，正在分析风险与配合机会…");
    try {
      const result = await api("/api/coach/analysis", { match_id: appState.matchId, frame_id: frame.frame_id, ...context() }, controller.signal);
      if (generation !== state.generation || activeSignature !== state.signature) return;
      if(state.result && ["waiting","throttled","stale"].includes(result.status))historical("等待有效的新分析 · 保留上次结果");
      else render(result);
      state.applied = ["ok", "cached"].includes(result.status) ? activeSignature : null;
      // Retry configuration/faults at a bounded rate, not once per UI render.
      if (!state.applied) {
        const delay = result.status === "waiting" ? 2500 : result.status === "throttled" ? Math.max(1000,Number(result.retry_after_s||20)*1000) : 20000;
        clearTimeout(state.timer);
        state.timer = setTimeout(() => { if (generation === state.generation) analyze(true); }, delay);
      }
    } catch (error) { if (generation === state.generation) pipeline("分析暂不可用：" + error.message); }
    finally {
      state.busy = false;
      if (state.controller === controller) state.controller = null;
      if (state.latest && (generation !== state.generation || activeSignature !== state.signature)) analyze();
    }
  }
  function observe(frame) {
    if (!frame || !frame.observation) {
      if (!state.result) reset("等待有效游戏画面");
      return;
    }
    if (!["bp", "loading", "in_game", "result"].includes(frame.observation.phase)) {
      reset("当前未看到有效游戏对局；Qwen3-VL 会继续观察，暂不调用外部分析。"); return;
    }
    state.latest = frame;
    if (state.editing) return;
    const next = signature(frame);
    if (next !== state.signature) {
      state.generation++; state.controller?.abort(); clearTimeout(state.timer);
      state.signature = next; state.applied = null;
      if(window.gameplanMonitor && state.result){
        historical("正在分析新画面 · 保留上次结果");
      }else{
        state.result = null;
        byId("coach-source").textContent = "正在更新";
        byId("coach-meta").textContent = "";
        byId("coach-summary").textContent = "游戏信息有变化，正在重新分析…";
        byId("coach-detail").hidden = true;
      }
    }
    return analyze();
  }
  action("coach-retry", async () => {
    await providerStatus();
    if(window.gameplanMonitor?.reobserve && !byId("frame-image").hidden) await window.gameplanMonitor.reobserve();
    else if (state.latest && Date.now()/1000 - state.latest.captured_at <= 150) await analyze(true);
    else if (!byId("frame-image").hidden && !appState.screenSource && !appState.stream) await window.gameplanVision?.once();
    else pipeline("接口状态已检查，等待 Qwen3-VL 读到新的游戏信息");
  });
  window.gameplanAdvisor = {
    reset, observe, hold,
    contextChanged: () => { state.editing = false; if (state.latest) observe(state.latest); },
    editing: () => {
      state.generation++; state.controller?.abort(); clearTimeout(state.timer);
      state.editing = true; state.applied = null; state.result = null;
      byId("coach-source").textContent = "等待阵容更新";
      byId("coach-summary").textContent = "阵容已修改，更新方案后重新分析。";
      byId("coach-detail").hidden = true;
    },
    reading: () => { if (!state.result) pipeline("Qwen3-VL 正在本机读取画面，完成后自动分析…"); },
  };
  setInterval(() => {
    if (state.result?.expires_at && state.result.expires_at * 1000 <= Date.now() && state.expiredResult!==state.result) {
      state.expiredResult=state.result;
      historical("分析已到更新时间 · 保留上次结果");
    }
  }, 500);
  window.addEventListener("pagehide", () => reset("页面已关闭"));
  providerStatus().catch(() => { byId("coach-provider").textContent = "外部接口状态暂不可用"; });
})();
