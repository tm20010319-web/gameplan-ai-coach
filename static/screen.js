"use strict";

(() => {
  let sources = [];
  const status = message => { byId("screen-status").textContent = message; };
  byId("auto-monitor").checked = localStorage.getItem("gameplan-auto-monitor") !== "false";
  byId("auto-monitor").addEventListener("change", () => localStorage.setItem("gameplan-auto-monitor", String(byId("auto-monitor").checked)));

  async function refresh() {
    const selected = byId("screen-source").value || localStorage.getItem("gameplan-screen-source");
    const response = await api("/api/screen/sources");
    sources = response.sources;
    byId("screen-source").innerHTML = sources.length
      ? sources.map(source => `<option value="${source.id}">${escapeHtml(source.label)}</option>`).join("")
      : '<option value="">未发现可用屏幕</option>';
    if (sources.some(source => source.id === selected)) byId("screen-source").value = selected;
    byId("screen-start").disabled = !sources.length;
    if (!appState.screenSource) status(sources.length ? "屏幕已就绪；自动观察已启用时会在页面加载后开始。" : "请确认本程序运行在已登录的 Windows 桌面中。");
  }

  async function capture(signal) {
    const source = appState.screenSource;
    if (!source) throw new Error("请先选择屏幕并开始监控。");
    let frame;
    try { frame = await api("/api/screen/frame", { source_id: source.id }, signal); }
    catch (error) { error.captureFailure = true; throw error; }
    if (source !== appState.screenSource || signal?.aborted) throw new DOMException("采集已停止", "AbortError");
    byId("frame-image").src = frame.image_base64;
    byId("frame-image").hidden = false;
    byId("frame-video").hidden = true;
    byId("stage-placeholder").hidden = true;
    byId("frame-status").textContent = `${source.label} · 采样 ${new Date(frame.captured_at * 1000).toLocaleTimeString("zh-CN", { hour12: false })}`;
    status(`采集中 · ${frame.width} × ${frame.height} 送入模型`);
    return frame;
  }

  action("screen-refresh", refresh);
  action("check-model", async () => {
    const health = await api("/api/vision/status?model=" + encodeURIComponent(byId("vision-model").value));
    const message = `${health.model} · ${health.message}`;
    byId("model-status").textContent = message;
    status(message);
    toast(message, !health.ready);
  });
  async function start() {
    const source = sources.find(item => item.id === byId("screen-source").value);
    if (!source) throw new Error("请选择一个可用屏幕。");
    stopMedia();
    appState.screenSource = { ...source };
    const url = new URL(location.href);
    for (const key of ["sample", "plan_side", "monitor"]) url.searchParams.delete(key);
    history.replaceState({}, "", url);
    localStorage.setItem("gameplan-screen-source", source.id);
    byId("stop-media").hidden = false;
    byId("frame-status").textContent = "准备采集 · " + source.label;
    status("正在检查本地模型…");
    try { await window.gameplanVision.start(); }
    catch (error) { stopMedia(); status(error.message); throw error; }
  }
  action("screen-start", start);
  byId("screen-source").addEventListener("change", () => {
    if (appState.screenSource) stopMedia();
    localStorage.setItem("gameplan-screen-source", byId("screen-source").value);
    status("屏幕选择已变更，点击开始后才会采集。");
  });
  window.gameplanScreen = {
    capture, start,
    onStopped: () => status("采集已停止。"),
    onPaused: message => { if (appState.screenSource) status(message || "采样已暂停，可点击「开始连续观察」恢复。"); },
  };
  const ready = refresh().catch(error => {
    byId("screen-source").innerHTML = '<option value="">本机采集暂不可用</option>';
    byId("screen-start").disabled = true;
    status(error.message);
  });
  window.addEventListener("gameplan-ready", async () => {
    const params = new URLSearchParams(location.search);
    if (params.has("sample") || params.get("display") === "1" || params.get("monitor") === "off" || !byId("auto-monitor").checked) return;
    await ready;
    if (!sources.length || !byId("auto-monitor").checked || appState.screenSource || appState.mediaUrl || appState.stream || !byId("frame-image").hidden) return;
    try { await start(); } catch (error) { status("自动观察未启动：" + error.message); }
  }, { once: true });
})();
