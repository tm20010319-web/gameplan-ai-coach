const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const {JSDOM} = require("../data/ui-check/node_modules/jsdom");
const html = fs.readFileSync("static/monitor.html", "utf8");
const script = fs.readFileSync("static/text-cooldowns.js", "utf8");
const flush = () => new Promise(resolve => setImmediate(resolve));
function harness(rows = []) {
  const dom = new JSDOM(html, {url:"http://localhost",runScripts:"outside-only"});
  const w = dom.window, calls = [], ticks = [];
  let revision = 0, data = rows;
  w.localStorage.setItem("gameplan-text-cooldown-match", "saved-match");
  w.Date.now = () => 100000;
  w.setInterval = fn => ticks.push(fn);
  const snapshot = () => ({match_id:"saved-match",revision,server_epoch:100,timers:data});
  w.fetch = async (url, options) => {
    calls.push({url,options});
    if (url.endsWith("/text-command")) {
      data = [{id:"timer1",hero:"妲己",skill:"大招",ready_epoch:115}]; revision++;
      return {ok:true,json:async()=>({message:"已记录",skill_timers:snapshot()})};
    }
    if (url.endsWith("/cancel") || url.endsWith("/clear")) { data = []; revision++; }
    return {ok:true,json:async()=>snapshot()};
  };
  vm.runInContext(script, dom.getInternalVMContext());
  return {w,dom,calls,ticks};
}
test("text submission creates a card and reaches ready without removing it", async () => {
  const h = harness();
  try {
    await flush();
    h.w.document.getElementById("text-cooldown-input").value = "妲己放大了";
    h.w.document.getElementById("text-cooldown-form").dispatchEvent(new h.w.Event("submit",{cancelable:true}));
    await flush();
    assert.equal(JSON.parse(h.calls.at(-1).options.body).text,"妲己放大了");
    assert.match(h.w.document.getElementById("text-cooldown-cards").textContent,/15 秒/);
    h.w.Date.now = () => 115000; h.ticks[0]();
    assert.match(h.w.document.getElementById("text-cooldown-cards").textContent,/妲己大招预计可用/);
    assert.match(h.w.document.getElementById("text-cooldown-alert").textContent,/妲己/);
    h.w.document.querySelector("#text-cooldown-cards button").click(); await flush();
    assert.equal(h.w.document.getElementById("text-cooldown-cards").textContent,"暂无记录");
  } finally {h.dom.window.close();}
});
test("reload restores saved session, unknown cooldown never becomes ready", async () => {
  const h = harness([{id:"unknown",hero:"朵莉亚",skill:"大招",ready_epoch:null}]);
  try {
    await flush();
    assert.match(h.calls[0].url,/saved-match/);
    assert.match(h.w.document.getElementById("text-cooldown-cards").textContent,/冷却未知/);
    h.w.Date.now = () => 999999999; h.ticks[0]();
    assert.equal(h.w.document.getElementById("text-cooldown-alert").textContent,"");
  } finally {h.dom.window.close();}
});

test("ambiguous hero waits for selection, confirmation submits the canonical command", async () => {
  const h = harness();
  try {
    await flush(); const sent = [];
    h.w.fetch = async (url, options) => {
      const text = JSON.parse(options.body).text; sent.push(text);
      return {ok:true,json:async()=> text === "鲁班放大了" ? {
        ok:false,status:"needs_confirmation",message:"请选择英雄",candidates:[
          {label:"鲁班七号 · 大招",text:"小象助手，鲁班七号放大招了"},
          {label:"鲁班大师 · 大招",text:"小象助手，鲁班大师放大招了"}
        ]
      } : {ok:true,message:"已记录鲁班大师，冷却未知",skill_timers:{
        match_id:"saved-match",revision:1,server_epoch:101,
        timers:[{id:"unknown",hero:"鲁班大师",skill:"大招",ready_epoch:null}]
      }}};
    };
    const input = h.w.document.getElementById("text-cooldown-input"); input.value = "鲁班放大了";
    h.w.document.getElementById("text-cooldown-form").dispatchEvent(new h.w.Event("submit",{cancelable:true}));
    await flush();
    assert.equal(sent.length,1);
    assert.equal(h.w.document.getElementById("text-cooldown-cards").textContent,"暂无记录");
    const choices = h.w.document.querySelectorAll("#text-cooldown-choices button");
    assert.equal(choices.length,3); choices[1].click(); await flush();
    assert.equal(sent[1],"小象助手，鲁班大师放大招了");
    assert.match(h.w.document.getElementById("text-cooldown-cards").textContent,/鲁班大师/);
    assert.equal(h.w.document.getElementById("text-cooldown-choices").hidden,true);
  } finally {h.dom.window.close();}
});
test("editing the command invalidates old confirmation buttons", async () => {
  const h = harness();
  try {
    await flush(); let calls = 0;
    h.w.fetch = async () => { calls++; return {ok:true,json:async()=>({status:"needs_confirmation",message:"请选择",candidates:[
      {label:"鲁班七号",text:"小象助手，鲁班七号放大了"}
    ]})}; };
    const input = h.w.document.getElementById("text-cooldown-input"); input.value="鲁班放大了";
    h.w.document.getElementById("text-cooldown-form").dispatchEvent(new h.w.Event("submit",{cancelable:true}));
    await flush();
    const old = h.w.document.querySelector("#text-cooldown-choices button");
    input.value="小明放大了"; input.dispatchEvent(new h.w.Event("input"));
    old.click(); await flush(); assert.equal(calls,1);
    assert.equal(h.w.document.getElementById("text-cooldown-choices").hidden,true);
  } finally {h.dom.window.close();}
});
