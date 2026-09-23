// Run: node --test tests/skill_timers_ui.cjs
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { JSDOM } = require("../data/ui-check/node_modules/jsdom");
const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "static/index.html"), "utf8");
const script = fs.readFileSync(path.join(root, "static/timers.js"), "utf8");

function harness(savedSeen = null) {
  const dom = new JSDOM(html, { url: "http://localhost", runScripts: "outside-only" });
  const w = dom.window, callbacks = [], messages = [], requests = [];
  let clock = 50000;
  w.Date.now = () => clock;
  w.setInterval = callback => callbacks.push(callback);
  w.appState = { matchId: "test" };
  w.byId = id => w.document.getElementById(id);
  w.escapeHtml = text => String(text ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  w.numberOrNull = id => w.byId(id).value === "" ? null : Number(w.byId(id).value);
  w.toast = (message, error) => messages.push({ message, error });
  w.api = async (url, data) => {
    requests.push({ url, data });
    return { match_id: w.appState.matchId, revision: 0, server_epoch: 1000, timers: [], history: [] };
  };
  w.action = (id, fn) => w.byId(id).addEventListener("click", () => fn().catch(error => w.toast(error.message, true)));
  if (savedSeen) w.sessionStorage.setItem("gameplan-skill-notified:test", savedSeen);
  vm.runInContext(script, dom.getInternalVMContext());
  return { w, messages, requests, close: () => w.close(), advance: ms => { clock += ms; callbacks.forEach(fn => fn()); } };
}

function item(overrides = {}) {
  return { id: "a1", side: "enemy", hero: "王昭君", slot: 3, skill: "凛冬已至", mode: "cast", seconds: 30,
    recorded_epoch: 1000, cast_epoch: 995, cast_game_time_s: 285, ready_epoch: 1025, ready_game_time_s: 315, ...overrides };
}
function snapshot(h, timers, revision = 1, overrides = {}) {
  h.w.gameplanTimers.applySnapshot({ match_id: "test", revision, server_epoch: 1000, timers, history: timers, ...overrides });
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test("backfilled release counts down, turns ready, and alerts once", () => {
  const h = harness();
  try {
    snapshot(h, [item()]);
    assert.equal(h.w.byId("manual-timers").querySelector("b").textContent, "25 秒");
    assert.match(h.w.byId("manual-timers").textContent, /局内 4:45.*局内 5:15/);
    h.advance(21000);
    assert.equal(h.w.document.querySelector(".timer-soon b").textContent, "4 秒");
    h.advance(5000);
    assert.equal(h.w.document.querySelector(".timer-ready b").textContent, "预计可用");
    assert.equal(h.messages.length, 1);
    assert.match(h.messages[0].message, /敌方 王昭君.*预计可用/);
    h.advance(10000);
    assert.equal(h.messages.length, 1);
    assert.equal(h.w.byId("skill-ready-alert").hidden, false);
  } finally { h.close(); }
});

test("multiple skills expire independently and cancelled skills stop alerting", () => {
  const h = harness();
  try {
    snapshot(h, [item({ ready_epoch: 1001 }), item({ id: "b2", skill: "禁锢寒霜", slot: 2, ready_epoch: 1100 })]);
    h.advance(2000);
    assert.equal(h.w.document.querySelectorAll(".timer-ready").length, 1);
    assert.equal(h.w.document.querySelectorAll(".manual-clock").length, 2);
    snapshot(h, [], 2, { server_epoch: 1002 });
    assert.equal(h.w.byId("skill-ready-alert").hidden, true);
    h.advance(120000);
    assert.equal(h.messages.length, 1);
  } finally { h.close(); }
});

test("refresh restores elapsed timers without repeating a delivered reminder", () => {
  const first = harness();
  snapshot(first, [item({ ready_epoch: 999 })]);
  assert.equal(first.messages.length, 1);
  const savedSeen = first.w.sessionStorage.getItem("gameplan-skill-notified:test");
  first.close();
  const second = harness(savedSeen);
  try {
    snapshot(second, [item({ ready_epoch: 999 })]);
    assert.match(second.w.byId("manual-timers").textContent, /预计可用/);
    assert.equal(second.messages.length, 0);
  } finally { second.close(); }
});

test("older responses and other matches cannot resurrect stale timers", () => {
  const h = harness();
  try {
    snapshot(h, [item()], 3);
    snapshot(h, [], 2);
    assert.equal(h.w.document.querySelectorAll(".manual-clock").length, 1);
    h.w.appState.matchId = "next";
    h.w.gameplanTimers.activate("next");
    snapshot(h, [item()], 4);
    assert.equal(h.w.document.querySelectorAll(".manual-clock").length, 0);
    h.advance(50000);
    assert.equal(h.messages.length, 0);
  } finally { h.close(); }
});

test("remaining-time mode keeps the release time unknown", () => {
  const h = harness();
  try {
    snapshot(h, [item({ mode: "remaining", cast_epoch: null, cast_game_time_s: null, ready_game_time_s: null })]);
    assert.match(h.w.byId("manual-timers").textContent, /释放时间未知/);
    assert.doesNotMatch(h.w.byId("manual-timers").textContent, /局内/);
  } finally { h.close(); }
});

test("the form submits manual cast evidence and excludes it in remaining mode", async () => {
  const h = harness();
  try {
    h.w.byId("skill-hero").innerHTML = '<option value="王昭君">王昭君</option>';
    h.w.byId("skill-slot").innerHTML = '<option value="3">3 技能</option>';
    h.w.byId("timer-seconds").value = "30";
    h.w.byId("timer-elapsed").value = "5";
    h.w.byId("timer-game-time").value = "285";
    h.w.byId("timer-add").click();
    await flush();
    let request = h.requests.find(request => request.data);
    assert.equal(request.data.elapsed_s, 5);
    assert.equal(request.data.game_time_s, 285);
    assert.equal(request.data.mode, "cast");
    assert.equal(h.w.byId("timer-elapsed").value, "0");
    assert.equal(h.w.byId("timer-game-time").value, "");
    h.w.byId("timer-mode").value = "remaining";
    h.w.byId("timer-mode").dispatchEvent(new h.w.Event("change"));
    assert.equal(h.w.byId("timer-cast-fields").hidden, true);
    h.w.byId("timer-elapsed").value = "55";
    h.w.byId("timer-game-time").value = "555";
    h.w.byId("timer-add").click();
    await flush();
    request = h.requests.at(-1);
    assert.equal(request.data.game_time_s, null);
    assert.equal(request.data.elapsed_s, 0);
    assert.equal(request.data.mode, "remaining");
    assert.equal(h.messages.length, 0);
  } finally { h.close(); }
});

test("negative and empty durations fail locally without posting", async () => {
  const h = harness();
  try {
    h.w.byId("skill-hero").innerHTML = '<option value="王昭君">王昭君</option>';
    for (const value of ["", "-2"]) {
      h.w.byId("timer-seconds").value = value;
      h.w.byId("timer-add").click();
      await flush();
    }
    assert.equal(h.requests.filter(request => request.data).length, 0);
    assert.equal(h.messages.filter(message => message.error).length, 2);
  } finally { h.close(); }
});
