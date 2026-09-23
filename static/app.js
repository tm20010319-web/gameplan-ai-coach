"use strict";

const byId = (name) => document.getElementById(name);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
const labels = { engage: "接团", kite: "拉扯", retreat: "撤退", trade: "换资源" };
const executionLabels = { unobserved: "待观察", uncertain: "不确定", matched: "动作一致", different: "动作不同" };
const appState = { heroes: [], ally: Array(5).fill(null), enemy: Array(5).fill(null), matchId: "", socket: null, advice: null, picker: null, mediaUrl: null, stream: null, screenSource: null, vision: null, source: "manual", records: [] };
let toastTimer;
let searchTimer;

function toast(message, error = false) {
  clearTimeout(toastTimer);
  const node = byId("toast");
  if (!node) { console.warn(message); return; }
  node.textContent = message;
  node.classList.toggle("error", error);
  node.hidden = false;
  toastTimer = setTimeout(() => { if (byId("toast")) byId("toast").hidden = true; }, error ? 7000 : 3800);
}

async function api(path, payload, signal) {
  const response = await fetch(path, {
    method: payload === undefined ? "GET" : "POST",
    headers: payload === undefined ? {} : { "Content-Type": "application/json" },
    body: payload === undefined ? undefined : JSON.stringify(payload),
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(path.startsWith("/api/vision/") ? 135000 : 60000)]) : AbortSignal.timeout(path.startsWith("/api/vision/") ? 135000 : 60000),
  });
  const data = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map((item) => item.msg).join("；") : data.detail;
    const error = new Error(detail || "请求失败，请稍后重试。");
    error.status = response.status;
    throw error;
  }
  return data;
}

function action(name, handler) {
  byId(name).addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try { await handler(event); } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
  });
}

function formAction(name, handler) {
  byId(name).addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.submitter;
    button.disabled = true;
    try { await handler(); } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
  });
}

function setPage(name) {
  const titles = {
    bp: ["赛前战术", "先读懂阵容，再进入战场。", "把英雄机制变成下一步行动。无需摄像头，也能走完训练闭环。"],
    game: ["局中观察", "关键节点，只说关键一步。", "确认局面，观察行动。没有高价值事件时，保持安静。"],
    review: ["赛后复盘", "把这一局，变成下一局的经验。", "结合实际数据与执行记录生成战报，不凭 KDA 猜测过程。"],
    knowledge: ["英雄知识库", "60 位英雄，60 份应对思路。", "对线、团战、前中后期与己方配合。机制建议，不是绝对克制。"],
  };
  document.querySelectorAll(".page").forEach((page) => { page.hidden = page.id !== "page-" + name; });
  document.querySelectorAll(".nav").forEach((button) => button.classList.toggle("active", button.dataset.page === name));
  const [label, title, description] = titles[name];
  byId("page-label").textContent = label;
  byId("page-title").textContent = title;
  byId("page-description").textContent = description;
  if (name !== "knowledge" && byId("vision-kind").value !== "observe") byId("vision-kind").value = { bp: "bp", game: "analyze", review: "result" }[name];
}

function renderRosters() {
  for (const side of ["ally", "enemy"]) {
    byId(side + "-roster").classList.toggle("enemy-roster", side === "enemy");
    byId(side + "-roster").innerHTML = appState[side].map((name, index) => {
      const hero = appState.heroes.find((item) => item.hero === name);
      return '<button class="hero-slot ' + (name ? "filled" : "") + '" data-side="' + side + '" data-slot="' + index + '" aria-label="' + (side === "ally" ? "己方" : "敌方") + "第" + (index + 1) + '位英雄">' +
        (name ? '<span class="hero-initial">' + escapeHtml(name.slice(0, 1)) + '</span><b>' + escapeHtml(name) + '</b><small>' + escapeHtml(hero?.lane || "待收录") + '</small>' : '<span class="slot-plus">＋</span><b>选择英雄</b><small>位置 ' + (index + 1) + "</small>") + "</button>";
    }).join("");
  }
  byId("roster-count").textContent = appState.ally.concat(appState.enemy).filter(Boolean).length + " / 10 位英雄已选择";
}

function renderPool() {
  const query = byId("hero-query").value.trim();
  const selected = appState.ally.concat(appState.enemy).filter(Boolean);
  byId("hero-pool").innerHTML = appState.heroes.filter((hero) => (hero.hero + hero.lane).includes(query)).map((hero) =>
    '<button class="hero-pick" data-hero="' + escapeHtml(hero.hero) + '"' + (selected.includes(hero.hero) ? " disabled" : "") + '><b>' + escapeHtml(hero.hero) + "</b><small>" + escapeHtml(hero.lane) + "</small></button>"
  ).join("");
}

function resetHud(message = "确认双方英雄后，教练会在这里给出本局的第一条行动建议。") {
  appState.advice = null;
  byId("hud-tag").textContent = "STANDBY / 安静观察";
  byId("hud-title").textContent = "先看信息，再做决策。";
  byId("hud-message").textContent = message;
  byId("hud-backup").textContent = "先清安全线，等待有效信息。";
  byId("hud-meta").textContent = "局中只保留一条主建议和一个备用动作。";
  byId("hud-timer").hidden = true;
}

function renderBP(result) {
  window.gameplanLoading?.fromRoster(result.ally_heroes, result.enemy_heroes);
  document.body.classList.remove("report-ready");
  byId("display-report").hidden = true;
  appState.advice = null;
  byId("hud-timer").hidden = true;
  byId("hud-tag").textContent = "PRE-GAME / 赛前准备";
  byId("hud-title").textContent = "本局先做这一步。";
  byId("hud-message").textContent = result.message;
  byId("hud-backup").textContent = result.priorities[0];
  byId("hud-meta").textContent = "置信度：" + result.confidence + " · 稳定机制建议，需结合版本和熟练度。";
  byId("bp-results").innerHTML = '<div class="panel strategy"><h3>加载阶段 · 本局行动路线</h3><p>' + escapeHtml(result.loading_plan) + '</p><ol>' + result.priorities.map((text) => "<li>" + escapeHtml(text) + "</li>").join("") + '</ol>' + result.warnings.map((text) => '<p class="warning">' + escapeHtml(text) + "</p>").join("") + '</div><div class="card-grid">' + result.items.map((item) =>
    '<article class="panel tactic-card"><h3>' + escapeHtml(item.hero) + ' <small> / 敌方机制</small></h3><p><b>核心威胁</b>' + escapeHtml(item.threat) + '</p><p><b>对线应对</b>' + escapeHtml(item.laning) + '</p><p><b>团战动作</b>' + escapeHtml(item.advice) + '</p>' + item.counters.map((counter) => '<span class="tag">' + escapeHtml(counter.hero) + (counter.selected ? " · 己方已选" : " · 机制选择") + '</span><small>' + escapeHtml(counter.reason) + '</small>').join("") + "</article>"
  ).join("") + "</div>";
}

function renderAdvice(response) {
  document.body.classList.remove("report-ready");
  byId("display-report").hidden = true;
  const advice = response.advice;
  if (!advice) { resetHud(response.message || "暂无有效建议"); return; }
  appState.advice = advice;
  byId("hud-tag").textContent = "LIVE / " + (advice.input_source === "replay" ? "示例数据" : advice.input_source === "vision" ? "视觉确认" : "人工确认");
  byId("hud-title").textContent = labels[advice.decision] || "观察";
  byId("hud-message").textContent = advice.message;
  byId("hud-backup").textContent = advice.backup;
  byId("hud-meta").textContent = "置信度 " + Math.round(advice.confidence * 100) + "% · " + ({ rules: "本地规则", rules_fallback: "模型失败，规则降级", deepseek: "DeepSeek + 规则" }[advice.source] || "本地规则");
  byId("hud-timer").hidden = false;
}

function setRoster(side, names) {
  appState[side] = Array.from({ length: 5 }, (_, index) => names[index] || null);
}

function handleSnapshot(snapshot) {
  window.gameplanTimers?.applySnapshot(snapshot.skill_timers);
  if (snapshot.bp) {
    setRoster("ally", snapshot.bp.ally_heroes);
    setRoster("enemy", snapshot.bp.enemy_heroes);
    renderRosters();
    renderBP(snapshot.bp);
  }
  if (snapshot.advice) renderAdvice({ advice: snapshot.advice });
  else if (snapshot.phase === "in_game") resetHud("安静观察 · 当前没有有效建议。");
  renderRecords(snapshot.records);
  if (snapshot.last_report && snapshot.phase === "result") api("/api/reports/" + snapshot.last_report).then(renderReport).catch((error) => toast(error.message, true));
}

function connect() {
  if (appState.socket) {
    appState.socket.onclose = null;
    appState.socket.close();
  }
  const matchId = appState.matchId;
  const socket = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws/" + matchId);
  appState.socket = socket;
  socket.onopen = () => {
    byId("connection").textContent = "● 大屏已连接";
    byId("connection").classList.remove("offline");
  };
  socket.onmessage = (event) => {
    if (appState.matchId !== matchId || appState.socket !== socket) return;
    const message = JSON.parse(event.data);
    if (message.type === "skill_timers") window.gameplanTimers?.applySnapshot(message.data);
    if (message.type === "snapshot") handleSnapshot(message.data);
    if (message.type === "bp") {
      setRoster("ally", message.data.ally_heroes);
      setRoster("enemy", message.data.enemy_heroes);
      renderRosters();
      renderBP(message.data);
    }
    if (message.type === "advice") {
      renderAdvice(message.data);
      if (message.data.triggered) refreshRecords().catch((error) => toast(error.message, true));
    }
    if (message.type === "observation") refreshRecords().catch((error) => toast(error.message, true));
    if (message.type === "report") { appState.advice = null; renderReport(message.data); }
  };
  socket.onclose = () => {
    byId("connection").textContent = "● 重连中";
    byId("connection").classList.add("offline");
    setTimeout(() => { if (appState.matchId === matchId && appState.socket === socket) connect(); }, 2000);
  };
}

async function activateMatch(matchId) {
  window.gameplanLoading?.clear("切换对局，等待新的阵容");
  if (appState.screenSource) stopMedia();
  window.gameplanVision?.stop("切换对局，等待新观察");
  appState.matchId = matchId;
  window.gameplanTimers?.activate(matchId);
  localStorage.setItem("gameplan-match", matchId);
  const url = new URL(location.href);
  url.searchParams.set("match", matchId);
  history.replaceState({}, "", url);
  byId("match-code").textContent = "# " + matchId.slice(0, 8).toUpperCase();
  url.searchParams.set("display", "1");
  byId("screen-link").href = url.toString();
  connect();
}

const numberOrNull = (name) => byId(name).value === "" ? null : Number(byId(name).value);
const boolOrNull = (name) => byId(name).value === "" ? null : byId(name).value === "true";

function getState() {
  return {
    phase: "in_game", time_sec: numberOrNull("game-time"),
    heroes: {
      ally_visible: [...new Set(byId("visible-allies").value.split(/[、,，\s]+/).filter(Boolean))],
      enemy_visible: [...new Set(byId("visible-enemies").value.split(/[、,，\s]+/).filter(Boolean))],
      enemy_missing: [...new Set(byId("missing-heroes").value.split(/[、,，\s]+/).filter(Boolean))],
      confidence: Number(byId("confidence").value),
    },
    map: {
      ally_count_near_mid: numberOrNull("ally-count"), enemy_count_near_mid: numberOrNull("enemy-count"),
      ally_low_hp_count: numberOrNull("low-hp"), next_objective: byId("objective").value || null,
      objective_eta_sec: numberOrNull("objective-eta"), lane_state: byId("lane-state").value,
      key_skills_ready: boolOrNull("skills-ready"), core_present: boolOrNull("core-present"), position_safe: boolOrNull("position-safe"),
      safe_trade_available: byId("safe-trade").checked, overextended: byId("overextended").checked, confidence: Number(byId("confidence").value),
    },
    combat_signal: { heroes_closing_distance: byId("closing").checked, damage_exchange: byId("combat").checked, skills_or_ults_visible: false },
  };
}

function fillState(state) {
  const arena = state.map || {};
  const fields = { "game-time": state.time_sec, confidence: Math.min(state.heroes?.confidence ?? 0, arena.confidence ?? 0), "ally-count": arena.ally_count_near_mid, "enemy-count": arena.enemy_count_near_mid, "low-hp": arena.ally_low_hp_count, objective: arena.next_objective, "objective-eta": arena.objective_eta_sec, "lane-state": arena.lane_state || "unknown", "skills-ready": arena.key_skills_ready, "core-present": arena.core_present, "position-safe": arena.position_safe };
  for (const [name, value] of Object.entries(fields)) byId(name).value = value === null || value === undefined ? "" : String(value);
  byId("missing-heroes").value = (state.heroes?.enemy_missing || []).join("、");
  byId("visible-allies").value = (state.heroes?.ally_visible || []).join("、");
  byId("visible-enemies").value = (state.heroes?.enemy_visible || []).join("、");
  for (const [name, value] of Object.entries({ overextended: arena.overextended, "safe-trade": arena.safe_trade_available, closing: state.combat_signal?.heroes_closing_distance, combat: state.combat_signal?.damage_exchange })) byId(name).checked = Boolean(value);
  byId("observed-time").value = (state.time_sec || 0) + 5;
}

function scenario(name) {
  byId("state-form").reset();
  const state = {
    phase: "in_game", time_sec: 285, heroes: { enemy_missing: [], confidence: 0.95 },
    map: { ally_count_near_mid: 3, enemy_count_near_mid: 2, ally_low_hp_count: 0, next_objective: "暴君", objective_eta_sec: 24, lane_state: "mid_pushed_ally", key_skills_ready: true, core_present: true, position_safe: true, confidence: 0.95 },
    combat_signal: {},
  };
  if (name === "retreat" || name === "trade") { state.map.ally_count_near_mid = 2; state.map.enemy_count_near_mid = 4; }
  if (name === "trade") state.map.safe_trade_available = true;
  if (name === "missing") { state.heroes.enemy_missing = ["关羽", "镜"]; state.map.overextended = true; }
  if (name === "quiet") { state.map.next_objective = null; state.map.objective_eta_sec = null; state.map.enemy_count_near_mid = 3; }
  fillState(state);
  appState.source = "replay";
  byId("state-source").textContent = "示例局面 · 非实际对局";
  toast("示例已填入，点击「确认并评估局面」发送。");
}

function renderRecords(records) {
  appState.records = records;
  const previous = byId("record-select").value;
  byId("records-list").innerHTML = records.length ? records.slice(0, 8).map((record) =>
    '<article class="record"><h3>' + (record.game_time ?? "?") + ' 秒 · ' + escapeHtml(labels[record.decision]) + ' <small>' + escapeHtml(executionLabels[record.execution]) + '</small></h3><p>' + escapeHtml(record.message) + '</p><small>' + escapeHtml(record.review || "尚未记录动作与结果") + (record.outcome ? " · 结果：" + escapeHtml(record.outcome) : "") + "</small></article>"
  ).join("") : '<p class="empty">暂无建议，先评估一次局面。</p>';
  byId("record-select").innerHTML = records.length ? records.map((record) => '<option value="' + record.advice_id + '">' + (record.game_time ?? "?") + " 秒 · " + escapeHtml(labels[record.decision]) + " · " + record.advice_id.slice(-6) + "</option>").join("") : '<option value="">暂无记录</option>';
  if (records.some((record) => record.advice_id === previous)) byId("record-select").value = previous;
}

async function refreshRecords() {
  renderRecords((await api("/api/matches/" + appState.matchId)).records);
}

function renderReport(report) {
  document.body.classList.add("report-ready");
  byId("display-report").hidden = false;
  byId("display-report").innerHTML = '<div class="panel-head"><h2>本局训练卡 · 扫码带走</h2><span class="micro">转发链接即分享</span></div><div class="display-report-content"><img class="display-poster" src="' + escapeHtml(report.poster_url) + '" alt="本局复盘海报"><div class="display-share"><img src="' + escapeHtml(report.qr_url) + '" alt="手机扫码查看战报"><h2>把经验带进下一局。</h2><p>手机与电脑连接同一局域网</p><a href="' + escapeHtml(report.share_url) + '" target="_blank" rel="noopener">打开战报 ↗</a></div></div>';
  byId("report-result").innerHTML = '<div class="panel"><div class="panel-head"><h2>本局训练卡已生成</h2><span class="micro">置信度 ' + escapeHtml(report.confidence) + '</span></div><div class="report-layout"><div class="report-copy">' +
    report.sections.map((section) => "<h3>" + escapeHtml(section.title) + "</h3><p>" + escapeHtml(section.text) + "</p>").join("") +
    '<div class="report-actions"><a class="primary" href="' + escapeHtml(report.poster_url) + '" download="电竞训练战报.png">保存海报 ↓</a><a class="subtle" href="' + escapeHtml(report.share_url) + '" target="_blank" rel="noopener">打开手机战报 ↗</a></div><p class="warning">手机与电脑需在同一局域网。若扫码无法访问，请检查分享地址、访客网络隔离与防火墙；不要直接开放公网端口。</p></div><div class="report-qr"><img src="' + escapeHtml(report.qr_url) + '" alt="扫码查看本局战报"><p>' + escapeHtml(report.share_url) + '</p><small>转发链接即分享此战报</small></div></div></div>';
  resetHud("本局战报已生成，可切换赛后复盘查看海报和二维码。");
  if (report.is_demo) {
    const note = document.createElement("p");
    note.className = "warning";
    note.textContent = "示例战报：本对局包含演示局面记录，不代表实际游戏表现。开始真实对局前请新建对局。";
    byId("report-result").prepend(note);
  }
}

async function renderKnowledge() {
  const query = byId("knowledge-query").value.trim();
  const lane = byId("knowledge-lane").value;
  let items = query ? (await api("/api/knowledge/search?q=" + encodeURIComponent(query) + "&limit=60")).items : appState.heroes;
  if (byId("knowledge-query").value.trim() !== query) return;
  items = items.filter((item) => !lane || item.lane === lane);
  byId("knowledge-list").innerHTML = items.length ? items.map((item) =>
    '<article class="knowledge-card"><h3>' + escapeHtml(item.hero || item.title) + '</h3><small>' + escapeHtml(item.lane || "通用决策") + " / " + escapeHtml(item.role || "高级教练原则") + '</small><p><b>机制：</b>' + escapeHtml(item.threat || item.content) + '</p>' +
    (item.hero ? '<p><b>弱点：</b>' + escapeHtml(item.weakness) + '</p><p><b>对线：</b>' + escapeHtml(item.laning) + '</p><p><b>团战：</b>' + escapeHtml(item.teamfight) + '</p><details><summary>己方用法与阶段节奏</summary><p>' + escapeHtml(item.ally_plan) + '</p>' + Object.entries(item.tempo).map(([phase, value]) => "<p>" + ({ early: "前期", mid: "中期", late: "后期" }[phase]) + "：" + escapeHtml(value) + "</p>").join("") + '<p>整理：2026-09-08 · 待人工教练复核</p></details>' : "") + "</article>"
  ).join("") : '<p class="empty">未找到匹配条目，不补猜未收录英雄的机制。</p>';
}

function stopMedia() {
  appState.mediaKind = "image";
  window.gameplanLoading?.clear();
  window.gameplanVision?.stop("画面来源已停止或更换");
  if (appState.stream) appState.stream.getTracks().forEach((track) => track.stop());
  appState.stream = null;
  appState.screenSource = null;
  window.gameplanScreen?.onStopped();
  byId("frame-video").pause();
  byId("frame-video").srcObject = null;
  byId("frame-video").removeAttribute("src");
  byId("frame-video").load();
  if (appState.mediaUrl) URL.revokeObjectURL(appState.mediaUrl);
  appState.mediaUrl = null;
  byId("frame-image").hidden = true;
  byId("frame-image").removeAttribute("src");
  byId("frame-video").hidden = true;
  byId("stage-placeholder").hidden = false;
  byId("stop-media").hidden = true;
  byId("frame-status").textContent = "手动模式 · 无摄像头";
}

async function currentFrame() {
  const element = byId("frame-video").hidden ? byId("frame-image") : byId("frame-video");
  if (element.tagName === "VIDEO" && (!element.videoWidth || !element.videoHeight)) {
    await new Promise((resolve, reject) => {
      if (element.videoWidth && element.videoHeight) { resolve(); return; }
      const done = () => { cleanup(); resolve(); };
      const fail = () => { cleanup(); reject(new Error("视频还在加载，或浏览器无法读取该视频。请稍后重试或转换为 MP4。")); };
      const cleanup = () => { element.removeEventListener("loadedmetadata", done); element.removeEventListener("canplay", done); element.removeEventListener("error", fail); };
      element.addEventListener("loadedmetadata", done, { once: true });
      element.addEventListener("canplay", done, { once: true });
      element.addEventListener("error", fail, { once: true });
    });
  }
  const width = element.videoWidth || element.naturalWidth;
  const height = element.videoHeight || element.naturalHeight;
  if (element.hidden || !width || !height) throw new Error("请先上传截图 / 视频，或共享投屏窗口。");
  const scale = Math.min(1, 1920 / width, 1080 / height);
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  canvas.getContext("2d").drawImage(element, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.9);
}

async function visionPayload(signal) {
  const frame = appState.screenSource ? await window.gameplanScreen.capture(signal) : { captured_at: Date.now()/1000, image_base64: await currentFrame() };
  return {
    captured_at: frame.captured_at, match_id: appState.matchId, image_base64: frame.image_base64, model: byId("vision-model").value,
    input_kind: appState.screenSource || appState.stream ? "live" : byId("frame-video").hidden ? (appState.mediaKind || "image") : "video",
    focus: byId("observe-focus").value,
    roi: { x: Number(byId("roi-x").value), y: Number(byId("roi-y").value), width: Number(byId("roi-width").value), height: Number(byId("roi-height").value) },
  };
}

document.querySelectorAll(".nav").forEach((button) => button.addEventListener("click", () => setPage(button.dataset.page)));
byId("standalone-monitor").addEventListener("click", () => stopMedia());
document.querySelectorAll(".roster").forEach((roster) => roster.addEventListener("click", (event) => {
  const button = event.target.closest("[data-slot]");
  if (!button) return;
  appState.picker = { side: button.dataset.side, index: Number(button.dataset.slot) };
  byId("picker-title").textContent = (button.dataset.side === "ally" ? "己方" : "敌方") + " · 选择第 " + (Number(button.dataset.slot) + 1) + " 位英雄";
  byId("hero-query").value = "";
  renderPool();
  byId("hero-dialog").showModal();
  byId("hero-query").focus();
}));
byId("hero-pool").addEventListener("click", (event) => {
  const button = event.target.closest("[data-hero]");
  if (!button || button.disabled) return;
  appState[appState.picker.side][appState.picker.index] = button.dataset.hero;
  window.gameplanRoster?.editing();
  renderRosters();
  byId("hero-dialog").close();
});
byId("hero-query").addEventListener("input", renderPool);
action("close-picker", () => byId("hero-dialog").close());
action("clear-slot", () => { appState[appState.picker.side][appState.picker.index] = null; window.gameplanRoster?.editing(); renderRosters(); byId("hero-dialog").close(); });
action("demo-bp", () => {
  window.gameplanRoster?.editing();
  setRoster("ally", ["老夫子", "金蝉", "后羿", "赵云", "张飞"]);
  setRoster("enemy", ["关羽", "海月", "公孙离", "镜", "大乔"]);
  renderRosters();
  setPage("bp");
  toast("示例阵容已载入，点击「生成战术方案」。");
});
action("bp-run", async () => {
  window.gameplanRoster?.editing();
  renderBP(await api("/api/bp/advice", { match_id: appState.matchId, ally_heroes: appState.ally.filter(Boolean), enemy_heroes: appState.enemy.filter(Boolean) }));
  toast("战术方案已同步至当前对局大屏。");
});
document.querySelectorAll("[data-scenario]").forEach((button) => button.addEventListener("click", () => scenario(button.dataset.scenario)));
byId("state-form").addEventListener("input", () => { appState.source = "manual"; byId("state-source").textContent = "人工编辑 / 待确认"; });
formAction("state-form", async () => {
  const response = await api("/api/in_game/advice", { match_id: appState.matchId, state_json: getState(), source: appState.source });
  renderAdvice(response);
  await refreshRecords();
  toast(response.status === "duplicate" ? "同一有效事件已去重，不重复推送。" : response.triggered ? "建议已推送，12 秒后自动过期。" : response.message);
});
formAction("observation-form", async () => {
  const recordId = byId("record-select").value;
  if (!recordId) throw new Error("请先产生一条局中建议。");
  await api("/api/matches/" + appState.matchId + "/records/" + recordId + "/observation", { observed_action: byId("observed-action").value, observed_at: Number(byId("observed-time").value), action_confidence: Number(byId("action-confidence").value), outcome: byId("outcome").value || "未记录" });
  await refreshRecords();
  toast("动作与结果已登记，将用于本局复盘。");
});
action("refresh-records", refreshRecords);
formAction("review-form", async () => {
  const result = { result: byId("result").value, hero: byId("result-hero").value, kda: byId("kda").value || "未提供", towers: numberOrNull("towers"), objectives: numberOrNull("objectives"), damage: numberOrNull("damage"), damage_taken: numberOrNull("damage-taken"), gold: numberOrNull("gold"), participation: numberOrNull("participation") };
  renderReport(await api("/api/post_game/review", { match_id: appState.matchId, result }));
  byId("report-result").scrollIntoView({ behavior: "smooth", block: "start" });
  toast("复盘海报与二维码已生成。");
});
byId("knowledge-query").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => renderKnowledge().catch((error) => toast(error.message, true)), 180); });
byId("knowledge-lane").addEventListener("change", () => renderKnowledge().catch((error) => toast(error.message, true)));
action("new-match", async () => {
  if (!confirm("新建对局并切换工作台？当前对局记录会保留，可通过原对局链接返回。")) return;
  setRoster("ally", []); setRoster("enemy", []);
  document.body.classList.remove("report-ready"); byId("display-report").hidden = true;
  renderRosters(); resetHud();
  byId("bp-results").replaceChildren(); byId("report-result").replaceChildren();
  byId("state-form").reset(); byId("review-form").reset();
  appState.source = "manual"; byId("state-source").textContent = "人工确认输入";
  await activateMatch((await api("/api/matches", {})).match_id);
});
action("display-mode", () => {
  document.body.classList.add("display-only");
  if (!byId("exit-display")) {
    const button = document.createElement("button"); button.id = "exit-display"; button.className = "subtle exit-display"; button.textContent = "退出大屏";
    button.onclick = () => { document.body.classList.remove("display-only"); button.remove(); };
    document.body.append(button);
  }
});
byId("media-file").addEventListener("change", async () => {
  const file = byId("media-file").files[0];
  if (!file) return;
  const extension = file.name.split(".").pop()?.toLowerCase() || "";
  const isVideo = file.type.startsWith("video/") || ["mp4", "webm", "mov", "m4v", "ogg", "ogv"].includes(extension);
  const isImage = file.type.startsWith("image/") || ["jpg", "jpeg", "png", "webp", "gif", "bmp"].includes(extension);
  if (!isImage && !isVideo) { toast("请选择常用图片或视频文件（MP4 / WebM / MOV）。", true); return; }
  if (isImage && file.size > 8000000) { toast("截图最大支持 8MB。", true); return; }
  if (isVideo && file.size > 4 * 1024 * 1024 * 1024) { toast("视频最大支持 4GB；当前文件约 " + (file.size / 1024 / 1024 / 1024).toFixed(2) + "GB。", true); return; }
  // 手动上传优先于自动屏幕监控，避免上传后又被屏幕采集覆盖。
  const autoMonitor = byId("auto-monitor");
  if (autoMonitor) { autoMonitor.checked = false; localStorage.setItem("gameplan-auto-monitor", "false"); }
  stopMedia();
  appState.mediaUrl = URL.createObjectURL(file);
  appState.mediaKind = isImage ? "image" : "video";
  byId("stage-placeholder").hidden = true;
  const element = isImage ? byId("frame-image") : byId("frame-video");
  element.onerror = () => { stopMedia(); toast("浏览器无法解码此文件，请转换为常用 MP4（H.264 + AAC）或 WebM。", true); };
  element.src = appState.mediaUrl; element.hidden = false;
  if (appState.mediaKind === "video") {
    element.controls = true;
    element.muted = true;
    try {
      await new Promise((resolve, reject) => {
        if (element.readyState >= 1) { resolve(); return; }
        const done = () => { cleanup(); resolve(); };
        const fail = () => { cleanup(); reject(new Error("浏览器无法读取该视频，请转换为常用 MP4（H.264 + AAC）或 WebM。")); };
        const cleanup = () => { element.removeEventListener("loadedmetadata", done); element.removeEventListener("error", fail); };
        element.addEventListener("loadedmetadata", done, { once: true });
        element.addEventListener("error", fail, { once: true });
      });
      element.play().catch(() => { toast("视频已载入，请点击播放按钮后再开始连续观察。", false); });
    } catch (error) {
      stopMedia();
      toast(error.message, true);
      return;
    }
  }
  byId("stop-media").hidden = false;
  byId("frame-status").textContent = "本地素材 · " + file.name;
  byId("media-file").value = "";
});
action("stop-media", stopMedia);
action("share-screen", async () => {
  if (!navigator.mediaDevices?.getDisplayMedia) throw new Error("屏幕共享需要 localhost 或 HTTPS；也可上传本地截图 / 视频。");
  const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
  stopMedia(); appState.stream = stream;
  byId("frame-video").srcObject = stream; byId("frame-video").hidden = false; byId("stage-placeholder").hidden = true;
  byId("stop-media").hidden = false; byId("frame-status").textContent = "游戏窗口已共享 · 可开始连续观察";
  stream.getVideoTracks()[0].addEventListener("ended", () => { if (appState.stream === stream) stopMedia(); }, { once: true });
  await byId("frame-video").play();
});
action("crop-preview", async () => {
  byId("crop-image").src = (await api("/api/vision/crop", await visionPayload())).image_base64;
  byId("crop-image").hidden = false;
});
action("vision-run", async () => {
  const kind = byId("vision-kind").value;
  if (kind === "observe") { await window.gameplanVision.once(); return; }
  window.gameplanVision?.stop("单次识别模式");
  toast("正在调用本地视觉模型；首次加载可能较慢，最多等待约 2 分钟。");
  const response = await api("/api/vision/" + kind, await visionPayload());
  appState.vision = { kind, response };
  byId("vision-output").textContent = JSON.stringify(response.detection || response.state, null, 2);
  const confidence = kind === "analyze" ? Math.min(response.state?.heroes?.confidence ?? 0, response.state?.map?.confidence ?? 0) : response.detection?.confidence ?? 0;
  const phase = response.detection?.phase || response.state?.phase;
  const phaseValid = ({ bp: ["bp", "loading"], analyze: ["in_game"], result: ["result"] }[kind]).includes(phase);
  const canApply = confidence >= 0.8 && phaseValid;
  byId("apply-vision").disabled = !canApply;
  byId("apply-vision").textContent = canApply ? "填入表单，继续核对 →" : "阶段 / 置信度不明确，请手动录入";
  byId("vision-dialog").showModal();
});
action("close-vision", () => byId("vision-dialog").close());
action("apply-vision", () => {
  const { kind, response } = appState.vision;
  if (kind === "bp") {
    setRoster("ally", response.detection.ally_locked || []); setRoster("enemy", response.detection.enemy_locked || []); renderRosters(); setPage("bp");
  } else if (kind === "analyze") {
    fillState(response.state); appState.source = "manual"; byId("state-source").textContent = "视觉草稿 · 请人工核对后提交"; setPage("game");
  } else {
    const result = response.detection.result;
    byId("result").value = result.result || "未知"; byId("result-hero").value = result.hero || ""; byId("kda").value = result.kda === "未提供" ? "" : result.kda;
    for (const [name, key] of Object.entries({ towers: "towers", objectives: "objectives", damage: "damage", "damage-taken": "damage_taken", gold: "gold", participation: "participation" })) byId(name).value = result[key] ?? "";
    setPage("review");
  }
  byId("vision-dialog").close(); toast("已填入草稿，请核对后再提交。");
});
setInterval(() => {
  if (appState.socket?.readyState === WebSocket.OPEN) appState.socket.send("ping");
}, 20000);
setInterval(() => {
  if (!appState.advice) return;
  const remaining = appState.advice.expires_epoch * 1000 - Date.now();
  if (remaining <= 0) { resetHud("上一条建议已过期，等待新的局面确认。"); return; }
  byId("hud-timer").firstElementChild.style.width = Math.min(100, remaining / (appState.advice.expires_in_sec * 10)) + "%";
}, 250);

async function initialize() {
  const [catalog, config] = await Promise.all([api("/api/heroes"), api("/api/config")]);
  appState.heroes = catalog.items;
  byId("hero-names").innerHTML = catalog.items.map((hero) => '<option value="' + escapeHtml(hero.hero) + '">').join("");
  byId("vision-model").value = config.vision_model;
  byId("model-status").textContent = config.deepseek_configured ? "DeepSeek 已配置 · 失败自动降级" : "本地规则已就绪 · 无需 API Key";
  renderRosters();
  await renderKnowledge();
  let matchId = new URLSearchParams(location.search).get("match") || localStorage.getItem("gameplan-match");
  if (!matchId || !/^[a-zA-Z0-9_-]{1,64}$/.test(matchId)) matchId = (await api("/api/matches", {})).match_id;
  await activateMatch(matchId);
  if (new URLSearchParams(location.search).get("display") === "1") byId("display-mode").click();
  window.dispatchEvent(new Event("gameplan-ready"));
}
initialize().catch((error) => toast("初始化失败：" + error.message + "；请刷新重试。", true));
