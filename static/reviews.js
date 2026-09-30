"use strict";
(() => {
  const el = id => document.getElementById(id);
  const games = {hok: "王者荣耀", lol: "英雄联盟"};
  const states = {recording: "记录中", pending: "等待分析", analyzing: "DeepSeek 分析中", ready: "已完成", failed: "待重试", insufficient: "记录不足", incomplete: "未确认结束"};
  let current = null, stream = null, timer = null, revision = 0, offset = 0, total = 0, cardKey = "";
  const node = (tag, text, className) => {
    const value = document.createElement(tag); if (text !== undefined) value.textContent = text;
    if (className) value.className = className; return value;
  };
  async function request(path, method = "GET") {
    const response = await fetch(path, {method, signal: AbortSignal.timeout(15000)});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "复盘暂时无法读取，请重试。");
    return data;
  }
  function streamId() {
    try {
      const value = sessionStorage.getItem("gameplan-review-stream");
      if (/^[a-zA-Z0-9_-]{1,64}$/.test(value || "")) return value;
    } catch {}
    return crypto.randomUUID();
  }
  function timeLabel(match) {
    return new Date(match.started * 1000).toLocaleString("zh-CN", {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"});
  }
  function gameTime(value) {return value == null ? "局内采样" : `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;}
  function fillCard(container, match) {
    container.dataset.reviewId = match.id;
    container.replaceChildren();
    const result = match.result;
    container.append(node("p", `${games[match.game] || "游戏待确认"} · ${match.hero || "操控英雄待确认"} · ${timeLabel(match)}${match.outcome === "win" ? " · 胜利" : match.outcome === "loss" ? " · 失败" : ""}`, "review-meta"));
    if (result?.points?.length && match.status === "ready") {
      const list = node("ol");
      result.points.slice(0, 2).forEach(point => {
        const item = node("li"); item.append(node("p", point.issue), node("p", `下局：${point.advice}`, "review-advice")); list.append(item);
      });
      container.append(list, node("small", `DeepSeek · ${result.model || ""} · 基于已记录画面`, "review-meta"));
    } else {
      container.append(node("p", result?.message || (match.status === "recording" ? `已记录 ${match.samples} 次画面，结束后自动生成复盘。` : "对局已结束，正在生成一两点个人建议…")));
    }
    if (match.status === "failed") {
      const button = node("button", "重试分析"); button.type = "button";
      button.onclick = async () => {
        button.disabled = true;
        try {
          const updated = await request(`/api/reviews/${encodeURIComponent(match.id)}/retry`, "POST");
          if (container.dataset.reviewId !== match.id) return;
          fillCard(container, updated);
          if (current?.id === match.id) {current = updated; cardKey = ""; poll();}
          else watchRetry(container, match.id);
        }
        catch (error) {container.append(node("p", error.message, "review-error")); button.disabled = false;}
      };
      container.append(button);
    }
    if (match.samples > 0) {
      const details = node("details"), body = node("div", undefined, "review-evidence");
      details.append(node("summary", "查看关键画面与依据"), body); let loaded = false;
      details.addEventListener("toggle", async () => {
        if (!details.open || loaded) return;
        loaded = true; body.textContent = "正在读取…";
        try {
          const data = await request(`/api/reviews/${encodeURIComponent(match.id)}`);
          const ids = new Set((data.result?.points || []).flatMap(p => p.evidence_ids));
          const frames = ids.size ? data.frames.filter(f => ids.has(f.id)) : data.frames.filter(f => f.observation.facts?.length).slice(-3);
          body.replaceChildren();
          if (!frames.length) body.append(node("p", "尚无可用于个人判断的关键记录。"));
          frames.slice(0, 6).forEach(frame => {
            const figure = node("figure"), image = node("img"); image.src = `/api/reviews/frames/${encodeURIComponent(frame.id)}`;
            image.alt = `${gameTime(frame.observation.game_time_s)}的对局采样`; image.loading = "lazy";
            const caption = node("figcaption", `${gameTime(frame.observation.game_time_s)} · ${(frame.observation.facts || []).map(f => f.text).join("；")}`);
            figure.append(image, caption); body.append(figure);
          });
          body.append(node("small", "只记录监控期间的采样，未覆盖的过程不作判断。", "review-meta"));
        } catch (error) {loaded = false; body.textContent = error.message;}
      });
      container.append(details);
    }
  }
  function show(match, status) {
    if (!match) {
      if (!current) el("review-live-status").textContent = status || "自动识别王者荣耀 / 英雄联盟，结束后生成简短复盘。";
      return;
    }
    current = match;
    el("review-game").textContent = games[match.game] || "自动识别";
    el("review-live-status").textContent = status?.includes("未完成") ? status : states[match.status] || "等待记录";
    const key = JSON.stringify([match.id, match.status, match.hero, match.samples, match.result]);
    if (key !== cardKey) {cardKey = key; fillCard(el("review-current"), match);}
    if (["recording", "pending", "analyzing"].includes(match.status)) poll();
  }
  function watchRetry(container, id) {
    setTimeout(async () => {
      if (!container.isConnected || container.dataset.reviewId !== id) return;
      try {
        const data = await request(`/api/reviews/${encodeURIComponent(id)}`);
        if (!container.isConnected || container.dataset.reviewId !== id) return;
        fillCard(container, data);
        if (["pending", "analyzing"].includes(data.status)) watchRetry(container, id);
      } catch (error) {if (container.isConnected) container.append(node("p", error.message, "review-error"));}
    }, 3000);
  }
  function poll() {
    if (timer || !stream) return;
    const version = revision, requestedStream = stream;
    timer = setTimeout(async () => {
      timer = null;
      try {
        const data = await request(`/api/reviews/streams/${encodeURIComponent(requestedStream)}`);
        if (version !== revision) return;
        show(data.match);
        if (el("review-history").open) await history();
      } catch (error) {if (version === revision) {el("review-live-status").textContent = error.message; poll();}}
    }, current?.status === "recording" ? 10000 : 3000);
  }
  function reset(id) {
    revision++; clearTimeout(timer); timer = null; current = null; cardKey = ""; stream = id;
    try {sessionStorage.setItem("gameplan-review-stream", id);} catch {}
    el("review-current").replaceChildren(); delete el("review-current").dataset.reviewId; el("review-game").textContent = "自动识别"; show(null);
  }
  async function history() {
    const version = offset;
    try {
      const data = await request(`/api/reviews?limit=10&offset=${offset}`);
      if (version !== offset) return;
      total = data.total; const list = el("review-history-list"); list.replaceChildren();
      if (!data.items.length) list.append(node("p", "还没有保存的对局复盘。", "review-meta"));
      data.items.forEach(match => {
        const details = node("details", undefined, "review-history-item"), body = node("div");
        details.append(node("summary", `${timeLabel(match)} · ${games[match.game] || "未知游戏"} · ${match.hero || "英雄待确认"} · ${states[match.status] || ""}`), body);
        let loaded = false;
        details.addEventListener("toggle", () => {if (details.open && !loaded) {loaded = true; fillCard(body, match);}});
        list.append(details);
      });
      el("review-page").textContent = total ? `${offset + 1}–${Math.min(offset + 10, total)} / ${total} 局` : "0 局";
      el("review-prev").disabled = offset === 0; el("review-next").disabled = offset + 10 >= total;
    } catch (error) {el("review-history-list").textContent = error.message;}
  }
  window.gameplanReview = {streamId, reset, close(id) {void request(`/api/reviews/streams/${encodeURIComponent(id)}/close`, "POST").catch(error => {el("review-live-status").textContent = error.message;});}, observe(response) {
    if (response.input_kind !== "live") return;
    if (stream !== appState.matchId) reset(appState.matchId);
    show(response.review, response.review_status);
  }};
  el("review-history").addEventListener("toggle", () => {if (el("review-history").open) void history();});
  el("review-prev").onclick = () => {offset = Math.max(0, offset - 10); void history();};
  el("review-next").onclick = () => {if (offset + 10 < total) {offset += 10; void history();}};
  el("review-refresh").onclick = () => void history();
  addEventListener("DOMContentLoaded", async () => {
    reset(appState.matchId);
    const version = revision;
    try {const data = await request(`/api/reviews/streams/${encodeURIComponent(stream)}`); if (version === revision) show(data.match);}
    catch (error) {if (version === revision) el("review-live-status").textContent = error.message;}
  });
  addEventListener("pagehide", () => {revision++; clearTimeout(timer); timer = null;});
})();
