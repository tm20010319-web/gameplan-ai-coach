const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { JSDOM } = require("../data/ui-check/node_modules/jsdom");
const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "static/index.html"), "utf8");
const script = fs.readFileSync(path.join(root, "static/vision.js"), "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

function harness() {
  const dom = new JSDOM(html, { url: "http://localhost", runScripts: "outside-only" });
  const w = dom.window, tasks = new Map(), requests = [], messages = [];
  let nextTask = 0, captured = 0;
  const options = { ready: true, infer: null, capture: null };
  w.setInterval = () => 1;
  w.setTimeout = (callback, delay) => { tasks.set(++nextTask, { callback, delay }); return nextTask; };
  w.clearTimeout = id => tasks.delete(id);
  w.byId = id => w.document.getElementById(id);
  w.escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, "_");
  w.appState = { screenSource: { id: "a".repeat(16) }, stream: null };
  w.resetHud = () => {};
  w.toast = (message, error) => messages.push({ message, error });
  w.visionPayload = async signal => { captured++; return options.capture ? options.capture(signal) : { image_base64: "frame" + captured, captured_at: Date.now() / 1000 }; };
  const result = payload => ({ observation: { phase: "not_game", player_hero: null, game_time_s: null, self_skills: [], note: "当前是桌面" },
    fresh: true, captured_at: payload.captured_at, elapsed_s: 1 });
  w.api = async (url, payload, signal) => {
    requests.push({ url, payload, signal });
    if (url === "/api/skills") return { heroes: [] };
    if (url.startsWith("/api/vision/status")) return { ready: options.ready, model: "qwen3-vl:8b", message: options.ready ? "就绪" : "模型未安装" };
    if (url === "/api/vision/observe") return options.infer ? options.infer(payload, signal) : result(payload);
    throw new Error(url);
  };
  vm.runInContext(script, dom.getInternalVMContext());
  return { w, options, tasks, requests, messages, result, get captured() { return captured; },
    async next() { const first = tasks.entries().next().value; assert.ok(first, "Expected a scheduled next frame"); tasks.delete(first[0]); await first[1].callback(); await flush(); },
    close: () => w.close() };
}

test("selected desktop runs continuously and captures a new frame each time", async () => {
  const h = harness();
  try {
    await flush();
    assert.equal(h.captured, 0, "loading the page must not capture the desktop");
    await h.w.gameplanVision.start(); await flush();
    assert.match(h.w.byId("live-metrics").textContent, /已识别 1 帧/);
    assert.equal(h.w.byId("live-note").textContent, "当前是桌面");
    assert.equal([...h.tasks.values()][0].delay, 0, "the default immediately reads the next frame");
    await h.next();
    assert.equal(h.captured, 2);
    assert.match(h.w.byId("live-metrics").textContent, /已识别 2 帧/);
    assert.deepEqual(h.requests.filter(r => r.url === "/api/vision/observe").map(r => r.payload.image_base64), ["frame1", "frame2"]);
    h.w.gameplanVision.stop();
    assert.equal(h.tasks.size, 0);
    assert.equal(h.w.byId("live-run").getAttribute("aria-pressed"), "false");
  } finally { h.close(); }
});

test("model preflight failure does not capture a screen", async () => {
  const h = harness();
  try {
    h.options.ready = false;
    await assert.rejects(h.w.gameplanVision.start(), /模型未安装/);
    assert.equal(h.captured, 0);
    assert.equal(h.tasks.size, 0);
  } finally { h.close(); }
});

test("stopping while capture is pending prevents sending the captured image", async () => {
  const h = harness(), pending = deferred();
  try {
    h.options.capture = () => pending.promise;
    await h.w.gameplanVision.start();
    h.w.gameplanVision.stop();
    pending.resolve({ captured_at: Date.now() / 1000, image_base64: "late" });
    await flush();
    assert.equal(h.requests.filter(r => r.url === "/api/vision/observe").length, 0);
    assert.equal(h.tasks.size, 0);
  } finally { h.close(); }
});

test("inference never queues another frame and late results cannot revive stopped monitoring", async () => {
  const h = harness(), pending = deferred(); let request;
  try {
    h.options.infer = (payload, signal) => { request = { payload, signal }; return pending.promise; };
    await h.w.gameplanVision.start(); await flush();
    assert.equal(h.captured, 1);
    assert.equal(h.tasks.size, 0);
    h.w.gameplanVision.stop("用户停止");
    assert.equal(request.signal.aborted, true);
    pending.resolve(h.result(request.payload));
    await flush();
    assert.equal(h.w.byId("live-note").textContent, "用户停止");
    assert.equal(h.tasks.size, 0);
  } finally { h.close(); }
});

test("transient model failures retry using new frames and pause after three failures", async () => {
  const h = harness();
  try {
    h.options.infer = () => { throw Object.assign(new Error("模型忙"), { status: 503 }); };
    await h.w.gameplanVision.start(); await flush();
    assert.match(h.w.byId("live-status").textContent, /重试/);
    await h.next(); await h.next();
    assert.equal(h.captured, 3);
    assert.equal(h.tasks.size, 0);
    assert.match(h.w.byId("live-status").textContent, /已暂停/);
  } finally { h.close(); }
});

test("capture failure stops immediately without model calls or screen fallback", async () => {
  const h = harness();
  try {
    h.options.capture = () => { throw Object.assign(new Error("屏幕已断开"), { captureFailure: true }); };
    await h.w.gameplanVision.start(); await flush();
    assert.equal(h.tasks.size, 0);
    assert.equal(h.requests.filter(r => r.url === "/api/vision/observe").length, 0);
    assert.match(h.w.byId("live-status").textContent, /屏幕已断开/);
  } finally { h.close(); }
});
