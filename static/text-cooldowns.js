"use strict";
(() => {
  const el = id => document.getElementById("text-cooldown-" + id);
  const key = "gameplan-text-cooldown-match";
  const epochKey = "gameplan-text-cooldown-epoch";
  let match = "", revision = -1, epoch = 0, offset = 0, timers = [], busy = false;
  let observedEpoch = "";
  try { match = localStorage.getItem(key) || ""; } catch (_) {}
  try { observedEpoch = localStorage.getItem(epochKey) || ""; } catch (_) {}
  if (!/^[a-zA-Z0-9_-]{1,64}$/.test(match)) match = crypto.randomUUID();
  function saveMatch() {
    try { localStorage.setItem(key, match); }
    catch (_) { el("status").textContent = "浏览器无法保存对局编号，刷新恢复不可用。"; }
  }
  saveMatch();
  async function request(suffix, body, matchId = match) {
    const response = await fetch(`/api/matches/${matchId}/${suffix}`, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? {} : {"Content-Type": "application/json"},
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "指令或请求格式无效");
    return data;
  }
  function tick() {
    const ready = [];
    for (const item of timers) {
      const output = document.getElementById("text-timer-" + item.id);
      if (!output) continue;
      if (item.ready_epoch == null) { output.textContent = "已记录释放，冷却未知"; continue; }
      const seconds = Math.max(0, Math.ceil(item.ready_epoch - Date.now() / 1000 - offset));
      const label = `${item.hero}${item.skill_kind === "ultimate" || item.skill === "大招" ? "大招" : item.skill}`;
      output.textContent = seconds ? `预计${seconds <= 5 ? " " + seconds + " 秒后可用" : "剩余 " + seconds + " 秒"}` : `${label}预计可用`;
      if (!seconds) ready.push(`${label}预计可用`);
    }
    const message = ready.join("；");
    if (el("alert").textContent !== message) el("alert").textContent = message;
  }
  function apply(data) {
    if (data.match_id !== match || data.revision < revision || (data.revision === revision && data.server_epoch <= epoch)) return;
    revision = data.revision; epoch = data.server_epoch;
    offset = epoch - Date.now() / 1000; timers = data.timers;
    renderTimers();
  }
  function renderTimers() {
    el("count").textContent = `${timers.length} 个记录`;
    el("cards").replaceChildren();
    for (const item of timers) {
      const card = document.createElement("div"); card.className = "enemy-timer";
      const title = document.createElement("b"); title.textContent = `${item.hero} · ${item.skill}`;
      const output = document.createElement("span"); output.id = "text-timer-" + item.id;
      const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "移除";
      remove.setAttribute("aria-label", `移除${item.hero}${item.skill}`);
      remove.addEventListener("click", () => mutate(async () => apply(await request(`skill-timers/${item.id}/cancel`, {}))));
      card.append(title, output, remove); el("cards").append(card);
    }
    if (!timers.length) el("cards").textContent = "暂无记录";
    tick();
  }
  async function refresh() {
    const matchId = match;
    try { apply(await request("skill-timers")); }
    catch (error) { if (matchId === match) el("status").textContent = "计时同步失败：" + error.message; }
  }
  async function mutate(operation) {
    if (busy) return;
    const matchId = match;
    busy = true; el("submit").disabled = true;
    try { await operation(); }
    catch (error) { if (matchId === match) el("status").textContent = "未完成：" + error.message; }
    finally { if (matchId === match) { busy = false; el("submit").disabled = false; } }
  }
  function clearChoices() { el("choices").replaceChildren(); el("choices").hidden = true; }
  el("input").addEventListener("input", clearChoices);
  async function submitText(text) {
    const matchId = match;
    const originalInput = el("input").value;
    clearChoices();
    const result = await request("text-command", {text});
    if (matchId !== match) return;
    if (result.status === "needs_confirmation") {
      if (el("input").value !== originalInput) return;
      el("status").textContent = result.message;
      for (const candidate of result.candidates) {
        const button = document.createElement("button"); button.type = "button";
        button.textContent = "确认：" + candidate.label;
        button.addEventListener("click", () => {
          if (matchId === match && el("input").value === originalInput) void mutate(() => submitText(candidate.text));
        });
        el("choices").append(button);
      }
      const cancel = document.createElement("button"); cancel.type = "button"; cancel.textContent = "取消";
      cancel.addEventListener("click", () => { if (matchId === match) { clearChoices(); el("status").textContent = "已取消，本次未登记。"; } });
      el("choices").append(cancel); el("choices").hidden = false;
      return;
    }
    apply(result.skill_timers); el("status").textContent = result.message;
    if (el("input").value === originalInput) el("input").value = "";
  }
  el("form").addEventListener("submit", event => {
    event.preventDefault();
    const text = el("input").value.trim(); if (!text) return;
    void mutate(() => submitText(text));
  });
  el("clear").addEventListener("click", () => mutate(async () => {
    const matchId = match;
    clearChoices();
    const data = await request("skill-timers/clear", {});
    if (matchId !== match) return;
    apply(data); el("status").textContent = "记录已清空";
  }));
  function setObservedEpoch(value) {
    observedEpoch = value;
    try { localStorage.setItem(epochKey, value); } catch (_) {}
  }
  function activateMatch(matchId) {
    match = matchId; revision = -1; epoch = 0; offset = 0; timers = []; busy = false;
    clearChoices();
    el("input").value = ""; el("submit").disabled = false;
    renderTimers();
    el("status").textContent = "新对局已开始，上一局计时和提示已清空";
    void refresh();
  }
  function newMatch() {
    setObservedEpoch("");
    activateMatch(crypto.randomUUID());
    saveMatch();
  }
  function observeMatch(frame) {
    if (!["bp", "loading", "in_game"].includes(frame.observation?.phase) || !frame.match_epoch) return;
    const nextEpoch = String(frame.match_epoch);
    if (observedEpoch && observedEpoch !== nextEpoch) newMatch();
    setObservedEpoch(nextEpoch);
  }
  window.gameplanTextCooldowns = {newMatch, observeMatch};
  el("new").addEventListener("click", () => {
    if (window.gameplanMonitor?.newMatch) window.gameplanMonitor.newMatch();
    else newMatch();
  });
  window.addEventListener("storage", event => {
    if (event.key === epochKey) observedEpoch = event.newValue || "";
    if (event.key === key && /^[a-zA-Z0-9_-]{1,64}$/.test(event.newValue || "") && event.newValue !== match) activateMatch(event.newValue);
  });
  el("status").textContent = "大招 / 闪现已补偿3秒报时延迟；支持“六耳没大招”。";
  void refresh(); setInterval(tick, 250); setInterval(refresh, 3000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) void refresh(); });
})();
