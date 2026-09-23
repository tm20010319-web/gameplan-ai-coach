"use strict";

(() => {
  const state = { matchId: "", revision: -1, serverEpoch: 0, offset: 0, timers: [], history: [], notified: new Set() };
  const team = item => item.side === "enemy" ? "敌方" : "己方";
  const name = item => `${team(item)} ${item.hero} · ${item.skill}`;
  const now = () => Date.now() / 1000 + state.offset;
  const localTime = epoch => new Date(epoch * 1000).toLocaleTimeString("zh-CN", { hour12: false });
  const gameTime = seconds => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
  const seenKey = () => "gameplan-skill-notified:" + state.matchId;

  function timing(item) {
    const cast = item.cast_epoch === null ? "释放时间未知 · 剩余时间登记于 " + localTime(item.recorded_epoch)
      : "释放 " + localTime(item.cast_epoch) + (item.cast_game_time_s === null ? "" : " / 局内 " + gameTime(item.cast_game_time_s));
    const ready = "预计就绪 " + localTime(item.ready_epoch) + (item.ready_game_time_s === null ? "" : " / 局内 " + gameTime(item.ready_game_time_s));
    return `${cast}；${ready}`;
  }

  function updateClocks() {
    const timestamp = now();
    const ready = [], newlyReady = [];
    for (const item of state.timers) {
      const card = byId("manual-timers").querySelector(`[data-timer-id="${item.id}"]`);
      const remaining = Math.max(0, Math.ceil(item.ready_epoch - timestamp));
      if (card) {
        card.querySelector(".timer-remaining").textContent = remaining > 0 ? `${remaining} 秒` : "预计可用";
        card.classList.toggle("timer-ready", remaining === 0);
        card.classList.toggle("timer-soon", remaining > 0 && remaining <= 5);
      }
      if (!remaining) {
        ready.push(name(item));
        if (!state.notified.has(item.id)) { state.notified.add(item.id); newlyReady.push(name(item)); }
      }
    }
    const alert = byId("skill-ready-alert");
    const message = ready.length ? ready.join("；") + "：冷却计时结束，预计可用，请留意。" : "";
    if (alert.textContent !== message) alert.textContent = message;
    alert.hidden = !ready.length;
    if (newlyReady.length) {
      toast(newlyReady.join("；") + "：冷却计时结束，预计可用。");
      try { sessionStorage.setItem(seenKey(), JSON.stringify([...state.notified].slice(-200))); } catch (_) { /* Cards still provide reminders if storage is unavailable. */ }
    }
  }

  function render() {
    byId("timer-count").textContent = `${state.timers.length} 个记录`;
    byId("timer-clear").disabled = !state.timers.length;
    byId("manual-timers").innerHTML = state.timers.length ? state.timers.map(item =>
      `<div class="manual-clock" data-timer-id="${item.id}"><span>${escapeHtml(name(item))}</span><b class="timer-remaining"></b><small>${escapeHtml(timing(item))}</small>${item.special_mechanic ? '<small>含刷新 / 充能机制，请按本局情况校正</small>' : ''}<button class="subtle" data-cancel-timer="${item.id}" type="button" aria-label="移除${escapeHtml(name(item))}的计时">移除</button></div>`
    ).join("") : '<p class="empty">确认敌方释放技能后，在「英雄技能基础资料」中登记。</p>';
    const ended = { superseded: "已被再次释放替换", cancelled: "已移除", cleared: "已清空", match_ended: "对局已结算" };
    byId("timer-history").innerHTML = state.history.length ? state.history.map(item =>
      `<div class="timer-history-row"><b>${escapeHtml(name(item))}</b><p>${escapeHtml(timing(item))}</p><small>${item.mode === "cast" ? "录入冷却" : "录入剩余"} ${item.seconds} 秒 · ${ended[item.ended_reason] || "当前记录"}</small></div>`
    ).join("") : '<p class="micro">暂无记录</p>';
    updateClocks();
  }

  function applySnapshot(data) {
    if (!data || data.match_id !== state.matchId || data.revision < state.revision ||
        (data.revision === state.revision && data.server_epoch <= state.serverEpoch)) return;
    state.revision = data.revision;
    state.serverEpoch = data.server_epoch;
    state.offset = data.server_epoch - Date.now() / 1000;
    state.timers = data.timers;
    state.history = data.history;
    render();
  }

  async function refresh() {
    const matchId = state.matchId;
    if (!matchId) return;
    try { applySnapshot(await api(`/api/matches/${matchId}/skill-timers`)); }
    catch (error) { if (matchId === state.matchId) toast("技能计时同步失败：" + error.message, true); }
  }

  function activate(matchId) {
    Object.assign(state, { matchId, revision: -1, serverEpoch: 0, offset: 0, timers: [], history: [], notified: new Set() });
    try { state.notified = new Set(JSON.parse(sessionStorage.getItem(seenKey()) || "[]")); } catch (_) { /* Use in-memory deduplication. */ }
    render();
    refresh();
  }

  byId("timer-mode").addEventListener("change", () => {
    const cast = byId("timer-mode").value === "cast";
    byId("timer-cast-fields").hidden = !cast;
    byId("timer-seconds-label").textContent = cast ? "本局实际冷却 / 秒" : "已确认剩余 / 秒";
    byId("timer-add").textContent = cast ? "记录释放并倒计时" : "登记剩余并倒计时";
  });

  action("timer-add", async () => {
    if (!state.matchId || state.matchId !== appState.matchId) throw new Error("对局尚未就绪，请稍后登记。");
    const mode = byId("timer-mode").value;
    const fields = ["timer-seconds", ...(mode === "cast" ? ["timer-elapsed", "timer-game-time"] : [])];
    if (!byId("timer-seconds").value || !byId("skill-hero").value || !fields.every(id => byId(id).checkValidity())) {
      throw new Error("请选择主动技能，填写有效的冷却秒数和释放时间。");
    }
    const matchId = state.matchId;
    const data = await api(`/api/matches/${matchId}/skill-timers`, {
      side: byId("timer-side").value, hero: byId("skill-hero").value,
      slot: Number(byId("skill-slot").value), mode, seconds: Number(byId("timer-seconds").value),
      elapsed_s: mode === "cast" ? Number(byId("timer-elapsed").value) : 0,
      game_time_s: mode === "cast" ? numberOrNull("timer-game-time") : null,
    });
    applySnapshot(data);
    if (state.matchId === matchId) {
      byId("timer-elapsed").value = "0";
      byId("timer-game-time").value = "";
    }
  });

  action("timer-clear", async () => {
    if (state.matchId) applySnapshot(await api(`/api/matches/${state.matchId}/skill-timers/clear`, {}));
  });
  byId("manual-timers").addEventListener("click", async event => {
    const button = event.target.closest("[data-cancel-timer]");
    if (!button || button.disabled) return;
    button.disabled = true;
    try { applySnapshot(await api(`/api/matches/${state.matchId}/skill-timers/${button.dataset.cancelTimer}/cancel`, {})); }
    catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  window.addEventListener("pageshow", event => { if (event.persisted) refresh(); });
  window.gameplanTimers = { activate, applySnapshot };
  if (appState.matchId) activate(appState.matchId);
  setInterval(updateClocks, 250);
})();
