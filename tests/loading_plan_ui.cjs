const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const { JSDOM } = require("../data/ui-check/node_modules/jsdom");
const root = path.resolve(__dirname, "..");
const flush = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

test("vision fills the ten slots and generates the same tactical plan without a button click", async () => {
  const h = harness(); try {
    const observation = {phase:'loading', ally_roster:['铠','后羿'], enemy_roster:['赵云']};
    h.w.gameplanLoading.observe({observation}); await flush();
    assert.deepEqual(Array.from(h.w.appState.ally), ['铠','后羿',null,null,null]);
    assert.deepEqual(Array.from(h.w.appState.enemy), ['赵云',null,null,null,null]);
    assert.match(h.w.byId('roster-count').textContent, /3 \/ 10.*视觉草稿/);
    assert.match(h.w.document.querySelector('.roster-label > span').textContent, /第一组/);
    assert.equal(h.w.byId('bp-results').innerHTML, h.w.byId('loading-result').innerHTML);
    assert.match(h.w.byId('bp-results').textContent, /开局分工/);
    h.w.gameplanLoading.observe({observation}); await flush();
    assert.equal(h.requests.length,1);
    h.w.gameplanLoading.clear();
    assert.equal(h.w.byId('bp-results').textContent,'');
    assert.ok(h.w.appState.ally.every(hero=>hero===null));
  } finally { h.close(); }
});

test("partial vision fills slots, and late plans cannot overwrite manual slot edits", async () => {
  const h=harness(), pending=deferred(); try {
    h.w.gameplanLoading.observe({observation:{phase:'loading',ally_roster:['铠'],enemy_roster:[]}});
    assert.equal(h.w.appState.ally[0],'铠');
    assert.equal(h.requests.length,0);
    h.config.handler=()=>pending.promise;
    const response={observation:{phase:'loading',ally_roster:['铠'],enemy_roster:['后羿']}};
    h.w.gameplanLoading.observe(response);
    const request=h.requests.at(-1);
    h.w.appState.ally[0]='赵云';h.w.gameplanRoster.editing();
    pending.resolve(h.plan(request.payload));await flush();
    h.w.gameplanLoading.observe(response);await flush();
    assert.equal(h.w.appState.ally[0],'赵云');
    assert.equal(h.w.byId('bp-results').textContent,'');
    assert.equal(request.signal.aborted,true);
  } finally {h.close();}
});

function harness() {
  const dom = new JSDOM(fs.readFileSync(path.join(root, "static/index.html"), "utf8"), { url: "http://localhost", runScripts: "outside-only" });
  const w = dom.window, requests = [], errors = [];
  const config = { handler: null };
  w.byId = id => w.document.getElementById(id);
  w.escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, "_");
  w.stopMedia = () => {};
  w.appState = { ally: [], enemy: [] };
  w.setRoster = (side, names) => { w.appState[side] = Array.from({length:5}, (_, i) => names[i] || null); };
  w.renderRosters = () => {
    for (const side of ['ally','enemy']) w.byId(side+'-roster').textContent = w.appState[side].join('、');
  };
  w.action = (id, fn) => w.byId(id).addEventListener("click", () => Promise.resolve(fn()).catch(error=>errors.push(error)));
  const plan = payload => ({ ...payload, title: payload.allies[0], summary: payload.source, opening: "开局分工", teamfight: "团战配合", advantage: "推进", fallback: "防守", cautions: [], personal: payload.player ? {hero:payload.player,text:"个人任务"} : null, notes:[], basis:[], version_note:"机制草案" });
  w.api = async (url,payload,signal) => {
    if (url === "/api/loading/sample") return {group_a:["铠","鲁班七号"],group_b:["后羿","蔡文姬"],image_url:"/static/samples/loading-lineup.png",source_note:"网上样本"};
    requests.push({payload,signal});
    return config.handler ? config.handler(payload,signal) : plan(payload);
  };
  vm.runInContext(fs.readFileSync(path.join(root,"static/roster-sync.js"),"utf8"),dom.getInternalVMContext());
  vm.runInContext(fs.readFileSync(path.join(root,"static/loading.js"),"utf8"),dom.getInternalVMContext());
  return {w,requests,config,errors,plan,close:()=>w.close()};
}

test("sample shows both perspectives without claiming automatic recognition; player switches correctly", async () => {
  const h=harness();try {
    h.w.byId("loading-sample").click();await flush();
    assert.match(h.w.byId("loading-source").textContent,/网上样本/);
    assert.equal(h.requests[0].payload.source,"sample");
    h.w.byId("loading-side-b").click();await flush();
    assert.equal(h.requests.at(-1).payload.allies[0],"后羿");
    h.w.byId("loading-player").value="蔡文姬";h.w.byId("loading-player").dispatchEvent(new h.w.Event("change"));await flush();
    assert.match(h.w.byId("loading-result").textContent,/蔡文姬 · 你的任务/);
    h.w.byId("loading-side-a").click();await flush();
    assert.equal(h.requests.at(-1).payload.player,null);
    assert.equal(h.errors.length,0);
  }finally{h.close();}
});

test("editing lineups removes old advice and never revives an in-flight answer",async()=>{
  const h=harness(),pending=deferred();try{
    h.w.byId("loading-sample").click();await flush();
    h.config.handler=()=>pending.promise;
    h.w.byId("loading-side-b").click();await flush();
    const request=h.requests.at(-1);
    h.w.byId("loading-group-a").value="赵云";h.w.byId("loading-group-a").dispatchEvent(new h.w.Event("input"));
    assert.equal(request.signal.aborted,true);
    pending.resolve(h.plan(request.payload));await flush();
    assert.equal(h.w.byId("loading-result").hidden,true);
    assert.equal(h.w.byId("loading-brief").hidden,true);
    h.config.handler=null;
    h.w.byId("loading-update").click();await flush();
    assert.equal(h.requests.at(-1).payload.source,"manual");
    assert.equal(h.requests.at(-1).payload.enemies[0],"赵云");
  }finally{h.close();}
});

test("loading observations automatically create a draft, deduplicate and clear on phase change",async()=>{
  const h=harness();try{
    const response={observation:{phase:"loading",ally_roster:["铠"],enemy_roster:["后羿"]}};
    h.w.gameplanLoading.observe(response);await flush();
    assert.equal(h.requests.length,1);
    assert.equal(h.requests[0].payload.source,"vision_draft");
    h.w.gameplanLoading.observe(response);await flush();assert.equal(h.requests.length,1);
    h.w.gameplanLoading.observe({observation:{phase:"in_game"}});
    assert.equal(h.w.byId("loading-result").hidden,true);
    h.w.gameplanLoading.observe(response);await flush();
    h.w.gameplanLoading.pause();assert.equal(h.w.byId("loading-brief").hidden,true);
  }finally{h.close();}
});

test("source change cancels pending recommendations; failures leave no stale plan",async()=>{
  const h=harness(),pending=deferred();try{
    h.config.handler=()=>pending.promise;
    h.w.gameplanLoading.fromRoster(["后羿"],["铠"]);await flush();
    h.w.gameplanLoading.clear();pending.resolve(h.plan(h.requests[0].payload));await flush();
    assert.equal(h.w.byId("loading-result").hidden,true);
    h.config.handler=()=>Promise.reject(new Error("测试失败"));
    h.w.gameplanLoading.fromRoster(["后羿"],["铠"]);await flush();
    assert.match(h.w.byId("loading-status").textContent,/测试失败/);
    assert.equal(h.w.byId("loading-brief").hidden,true);
  }finally{h.close();}
});

test("manual correction is not overwritten by a later vision candidate",async()=>{
  const h=harness();try{
    const response={observation:{phase:"loading",ally_roster:["铠"],enemy_roster:["后羿"]}};
    h.w.gameplanLoading.observe(response);await flush();
    h.w.byId("loading-group-a").value="赵云";h.w.byId("loading-group-a").dispatchEvent(new h.w.Event("input"));
    h.w.gameplanLoading.observe(response);await flush();
    assert.equal(h.w.byId("loading-group-a").value,"赵云");
    h.w.byId("loading-update").click();await flush();
    const count=h.requests.length;
    h.w.gameplanLoading.observe(response);await flush();
    assert.equal(h.requests.length,count);
    assert.equal(h.w.byId("loading-brief-title").textContent,"赵云");
  }finally{h.close();}
});

test("live BP A to B to C changes replace the selected hero and personal plan",async()=>{
  const h=harness();try{
    for (const hero of ["后羿","孙尚香","狄仁杰"]) {
      h.w.gameplanLoading.observe({captured_at:Date.now()/1000,input_kind:"live",observation:{phase:"bp",player_hero:hero,ally_roster:[hero,"蔡文姬"],enemy_roster:["铠"]}});
      await flush();
      assert.equal(h.w.byId("loading-player").value,hero);
      assert.equal(h.w.gameplanLoading.analysisContext().player,hero);
      assert.equal(h.requests.at(-1).payload.player,hero);
      assert.match(h.w.byId("loading-brief-text").textContent,new RegExp(hero));
      assert.equal(h.w.byId("loading-group-a").value,hero+"、蔡文姬");
    }
    assert.equal(h.errors.length,0);
  }finally{h.close();}
});

test("manual perspective stays selected until automatic following is restored",async()=>{
  const h=harness();try{
    const frame=hero=>({observation:{phase:"in_game",player_hero:hero,ally_roster:["后羿","蔡文姬"],enemy_roster:["铠"]}});
    h.w.gameplanLoading.observe(frame("后羿"));await flush();
    h.w.byId("loading-player").value="蔡文姬";h.w.byId("loading-player").dispatchEvent(new h.w.Event("change"));await flush();
    h.w.gameplanLoading.observe(frame(null));await flush();
    assert.equal(h.w.gameplanLoading.analysisContext().player,"蔡文姬");
    h.w.gameplanLoading.observe(frame("后羿"));await flush();
    assert.equal(h.w.gameplanLoading.analysisContext().player,"蔡文姬");
    h.w.byId("loading-follow").checked=true;h.w.byId("loading-follow").dispatchEvent(new h.w.Event("change"));await flush();
    assert.equal(h.w.gameplanLoading.analysisContext().player,"后羿");
  }finally{h.close();}
});

test("loading and unknown identities remove player focus and stale personal advice",async()=>{
  const h=harness();try{
    const o={phase:"bp",player_hero:"后羿",ally_roster:["后羿"],enemy_roster:["铠"]};
    h.w.gameplanLoading.observe({observation:o});await flush();
    h.w.gameplanLoading.observe({observation:{...o,phase:"loading"}});await flush();
    assert.equal(h.w.gameplanLoading.analysisContext().side,"neutral");
    assert.equal(h.w.gameplanLoading.analysisContext().player,null);
    assert.equal(h.w.byId("loading-result").textContent.includes("你的任务"),false);
    h.w.gameplanLoading.observe({observation:{...o,ally_roster:[],enemy_roster:[],player_hero:null}});await flush();
    assert.equal(h.w.byId("loading-brief").hidden,true);
    assert.equal(h.w.gameplanLoading.analysisContext().player,null);
  }finally{h.close();}
});

test("restoring automatic lineups applies the latest frame after manual correction",async()=>{
  const h=harness();try{
    h.w.gameplanLoading.fromRoster(["后羿"],["铠"]);await flush();
    h.w.gameplanLoading.observe({observation:{phase:"bp",player_hero:"孙尚香",ally_roster:["孙尚香"],enemy_roster:["赵云"]}});await flush();
    assert.equal(h.w.byId("loading-group-a").value,"后羿");
    h.w.byId("loading-resume").click();await flush();
    assert.equal(h.w.byId("loading-group-a").value,"孙尚香");
    assert.equal(h.w.gameplanLoading.analysisContext().player,"孙尚香");
  }finally{h.close();}
});

test("a late old hero plan cannot replace the latest hero's plan",async()=>{
  const h=harness(),pending=deferred();try{
    const frame=hero=>({observation:{phase:"bp",player_hero:hero,ally_roster:[hero],enemy_roster:["铠"]}});
    h.config.handler=()=>pending.promise;h.w.gameplanLoading.observe(frame("后羿"));await flush();
    const old=h.requests.at(-1);
    h.config.handler=null;h.w.gameplanLoading.observe(frame("孙尚香"));await flush();
    assert.equal(old.signal.aborted,true);
    pending.resolve(h.plan(old.payload));await flush();
    assert.equal(h.w.byId("loading-player").value,"孙尚香");
    assert.doesNotMatch(h.w.byId("loading-brief-text").textContent,/后羿/);
  }finally{h.close();}
});
