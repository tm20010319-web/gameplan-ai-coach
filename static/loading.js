"use strict";

(() => {
  const state = { groups: null, labels: ["第一组", "第二组"], side: "a", source: "manual", controller: null, generation: 0, fingerprint: null, manualOverride: false, explicitSide: false, phase: null, latest: null };
  const sources = { sample: "网上样本 · 阵容经助手核对", vision_draft: "视觉阵容草稿 · 请核对英雄", manual: "手动阵容 · 赛前方案" };
  const status = text => { byId("loading-status").textContent = text; };
  const names = text => text.split(/[、,，\s]+/).filter(Boolean);
  const current = () => state.groups?.[state.side === "a" ? 0 : 1] || [];

  function cancel() { state.generation++; state.controller?.abort(); state.controller = null; }
  function clear(message = "画面来源已更换，等待新的阵容") {
    const el = id => byId(id);
    window.gameplanRoster?.clear();
    window.gameplanAdvisor?.reset(message);
    cancel(); state.groups = null; state.fingerprint = null; state.manualOverride = false;
    state.explicitSide = false; state.phase = null; state.latest = null;
    if (el("loading-follow")) el("loading-follow").checked = true;
    if (el("loading-resume")) el("loading-resume").hidden = true;
    if (el("loading-result")) { el("loading-result").hidden = true; el("loading-result").replaceChildren(); }
    if (el("loading-brief")) el("loading-brief").hidden = true;
    if (el("loading-perspective")) el("loading-perspective").hidden = true;
    if (el("loading-group-a")) el("loading-group-a").value = "";
    if (el("loading-group-b")) el("loading-group-b").value = "";
    if (el("loading-source")) el("loading-source").textContent = "赛前阵容方案";
    if (el("loading-intro")) el("loading-intro").textContent = "根据双方阵容给出开局、团战和转资源方案。识别失败时可手动核对阵容。";
    status(message);
  }
  function updateControls() {
    const previous = byId("loading-player").value;
    byId("loading-player").innerHTML = '<option value="">先看全队配合</option>' + current().map(name => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join("");
    if (current().includes(previous)) byId("loading-player").value = previous;
    for (const side of ["a", "b"]) {
      const index = side === "a" ? 0 : 1;
      byId("loading-side-" + side).textContent = `看${state.labels[index]}打法`;
      byId("loading-side-" + side).setAttribute("aria-pressed", String(state.side === side));
      byId("loading-group-" + side + "-label").textContent = state.labels[index] + "英雄";
    }
    byId("loading-perspective").hidden = false;
  }
  function render(plan) {
    const label = state.labels[state.side === "a" ? 0 : 1];
    const source = `${sources[state.source]} · 当前查看${label}`;
    byId("loading-source").textContent = sources[state.source];
    byId("loading-result").innerHTML = `<div class="loading-route"><div class="loading-route-header"><small>${escapeHtml(source)}</small><h3>${escapeHtml(plan.title)}</h3><p>${escapeHtml(plan.summary)}</p></div><div class="loading-lineups"><div>方案方：${escapeHtml(plan.allies.join("、"))}</div><div>对手方：${escapeHtml(plan.enemies.join("、"))}</div></div>${plan.personal ? `<div class="loading-personal"><b>${escapeHtml(plan.personal.hero)} · 你的任务</b>${escapeHtml(plan.personal.text)}</div>` : ""}<div class="loading-steps">${[["01", "开局先做什么", plan.opening], ["02", "团战怎么配合", plan.teamfight], ["03", "有优势怎么推进", plan.advantage], ["04", "打不过怎么守", plan.fallback]].map(([number,title,text]) => `<article class="loading-step"><small>${number}</small><h4>${title}</h4><p>${escapeHtml(text)}</p></article>`).join("")}</div><div class="loading-cautions">${plan.cautions.map(item => `<article class="loading-caution"><b>${escapeHtml(item.title)}</b><p>${escapeHtml(item.text)}</p></article>`).join("")}</div><details class="loading-basis"><summary>依据与适用条件</summary>${plan.notes.map(note=>`<p>${escapeHtml(note)}</p>`).join("")}<p>${escapeHtml(plan.version_note)}</p><p>${plan.basis.map(item=>`${escapeHtml(item.hero)}：${escapeHtml(item.source)}`).join("；")}</p></details></div>`;
    byId("loading-result").hidden = false;
    byId("loading-brief-source").textContent = source;
    byId("loading-brief-title").textContent = plan.title;
    byId("loading-brief-text").textContent = plan.personal ? `${plan.personal.hero}：${plan.personal.text}` : plan.summary;
    byId("loading-brief").hidden = false;
    window.gameplanRoster?.plan(byId("loading-result").innerHTML);
    status("方案已更新 · 进入对局后按现场信息调整");
  }
  async function generate(notify = true) {
    cancel(); const generation = state.generation;
    byId("loading-result").hidden = true; if (byId("loading-brief")) byId("loading-brief").hidden = true;
    if (notify) window.gameplanAdvisor?.contextChanged();
    if (!state.groups) { status("先核对双方阵容"); return; }
    const rosterIndex = state.side === "a" ? 0 : 1;
    window.gameplanRoster?.sync(state.groups[rosterIndex], state.groups[1-rosterIndex], [state.labels[rosterIndex], state.labels[1-rosterIndex]], state.source);
    if (state.groups.some(group => !group.length)) { status("已更新可见英雄，等待读到双方阵容后生成配合方案"); return; }
    const controller = new AbortController(); state.controller = controller;
    status("正在生成打法…");
    try {
      const index = state.side === "a" ? 0 : 1;
      const plan = await api("/api/loading/plan", { allies: state.groups[index], enemies: state.groups[1-index], player: byId("loading-player").value || null, source: state.source }, controller.signal);
      if (generation !== state.generation) return;
      render(plan);
    } catch (error) { if (generation === state.generation) { status("方案未生成：" + error.message); window.gameplanRoster?.status("方案未生成：" + error.message); state.fingerprint = null; } }
    finally { if (state.controller === controller) state.controller = null; }
  }
  function setGroups(a, b, source, labels, side = "a") {
    state.groups = [a, b]; state.source = source; state.labels = labels; state.side = side;
    state.manualOverride = source !== "vision_draft";
    state.explicitSide = source !== "vision_draft";
    byId("loading-resume").hidden = source !== "manual";
    byId("loading-group-a").value = a.join("、"); byId("loading-group-b").value = b.join("、");
    byId("loading-player").value = "";
    updateControls();
    byId("loading-intro").textContent = source === "sample" ? "这是网上静态截图，不会随游戏换人变化。点击上方「开始屏幕监控」后，将持续识别实际游戏中的英雄。" : "以下是阵容方案，可切换查看方并选择英雄；先核对阵容，具体操作还要看局内情况。";
    return generate();
  }
  async function sample() {
    stopMedia(); clear("正在载入网上样本…");
    const generation = state.generation;
    const data = await api("/api/loading/sample");
    if (generation !== state.generation) return;
    byId("frame-image").src = data.image_url; byId("frame-image").hidden = false;
    if (byId("frame-image").decode) await byId("frame-image").decode();
    if (generation !== state.generation) return;
    byId("stage-placeholder").hidden = true; byId("stop-media").hidden = false;
    byId("frame-status").textContent = data.source_note;
    if (typeof appState !== "undefined") appState.mediaKind = "sample";
    await setGroups(data.group_a, data.group_b, "sample", ["上排", "下排"], new URLSearchParams(location.search).get("plan_side") === "b" ? "b" : "a");
    if (new URLSearchParams(location.search).get("monitor") !== "off") await window.gameplanVision?.once();
  }
  action("loading-sample", sample);
  action("loading-update", () => setGroups(names(byId("loading-group-a").value), names(byId("loading-group-b").value), "manual", state.labels, state.side));
  for (const side of ["a", "b"]) {
    action("loading-side-" + side, async () => { byId("loading-follow").checked = false; state.side = side; state.explicitSide = true; updateControls(); await generate(); });
    byId("loading-group-" + side).addEventListener("input", () => {
      window.gameplanRoster?.clear();
      window.gameplanAdvisor?.editing();
      cancel(); state.groups = null; state.fingerprint = null; state.manualOverride = true;
      byId("loading-resume").hidden = false;
      byId("loading-result").hidden = true; byId("loading-brief").hidden = true; byId("loading-perspective").hidden = true;
      status("阵容已修改，点击更新方案");
    });
  }
  byId("loading-player").addEventListener("change", () => { byId("loading-follow").checked = false; generate(); });
  function observe(response) {
    state.latest = response;
    const observation = response.observation;
    if (state.manualOverride) return;
    const expired = response.captured_at && (Date.now()/1000 - response.captured_at > 180 || Date.now()/1000 < response.captured_at);
    if (!["bp", "loading", "in_game"].includes(observation.phase) || expired) {
      if (state.source === "vision_draft") clear("当前没有有效游戏阵容，等待新画面");
      return;
    }
    const a = [...new Set(observation.ally_roster || [])], b = [...new Set(observation.enemy_roster || [])];
    const overlap = a.filter(hero => b.includes(hero));
    const groups = [a.filter(hero => !overlap.includes(hero)), b.filter(hero => !overlap.includes(hero))];
    if (!groups.some(group => group.length)) {
      if (state.source === "vision_draft") clear("英雄身份尚未确认，继续读取新画面");
      return;
    }
    const player = ["bp", "in_game"].includes(observation.phase) && groups.flat().includes(observation.player_hero) ? observation.player_hero : null;
    const fingerprint = JSON.stringify([observation.phase, groups, player]);
    if (state.fingerprint === fingerprint) return;
    const phaseChanged = state.phase !== observation.phase;
    state.fingerprint = fingerprint; state.phase = observation.phase;
    state.groups = groups; state.source = "vision_draft";
    state.labels = ["第一组", "第二组"];
    if (byId("loading-follow").checked) {
      state.explicitSide = Boolean(player);
      state.side = player && groups[1].includes(player) ? "b" : "a";
    } else if (phaseChanged) {
      state.explicitSide = false; byId("loading-player").value = "";
    }
    updateControls();
    if (byId("loading-follow").checked) byId("loading-player").value = player || "";
    byId("loading-group-a").value = groups[0].join("、"); byId("loading-group-b").value = groups[1].join("、");
    byId("loading-source").textContent = sources.vision_draft;
    byId("loading-resume").hidden = true;
    byId("loading-intro").textContent = `${{bp:"选人", loading:"加载", in_game:"局中"}[observation.phase]}画面 · 阵容随每次识别更新。${player ? "识别到的操控英雄：" + player + "。" : "尚未确认操控英雄，可手动选择查看视角。"}英雄身份仍需核对。`;
    // The vision loop publishes this same frame to the advisor after committing its context.
    generate(false);
  }
  byId("loading-follow").addEventListener("change", () => {
    if (byId("loading-follow").checked && !state.manualOverride && state.latest) {
      state.fingerprint = null; observe(state.latest); window.gameplanAdvisor?.contextChanged();
    }
  });
  action("loading-resume", () => {
    const latest = state.latest;
    clear("已恢复自动识别，等待新画面");
    if (latest) { observe(latest); window.gameplanAdvisor?.observe(latest); }
  });
  action("loading-jump", () => byId("loading-result").scrollIntoView({ behavior: "smooth", block: "start" }));
  window.gameplanLoading = {
    editRoster: () => {
      cancel(); state.groups = null; state.fingerprint = null; state.manualOverride = true;
      byId("loading-resume").hidden = false;
      byId("loading-result").hidden = true; byId("loading-brief").hidden = true;
      window.gameplanAdvisor?.editing();
      status("阵容槽位已修改，点击生成战术方案；也可恢复自动识别");
    },
    analysisContext: () => {
      if (!state.groups) return { side: "neutral", player: null, lineup: null };
      const index = state.side === "a" ? 0 : 1;
      const player = byId("loading-player").value || null;
      return { side: state.explicitSide || player ? state.side : "neutral", player,
        lineup: state.source === "vision_draft" ? null : { allies: state.groups[index], enemies: state.groups[1-index], player, source: state.source } };
    },
    clear,
    pause: (preserve=false) => { if (!preserve && state.source === "vision_draft") clear("视觉观察已暂停，阵容草稿已撤下"); },
    fromRoster: (allies, enemies) => {
      if (allies?.length && enemies?.length) setGroups(allies, enemies, "manual", ["己方", "敌方"]);
    },
    observe,
  };
  if (new URLSearchParams(location.search).get("sample") === "loading") {
    // Initialization may restore a match; start the sample only after that finishes.
    window.addEventListener("gameplan-ready", () => sample().catch(error => status(error.message)), { once: true });
  }
})();
