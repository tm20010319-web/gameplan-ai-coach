const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const {JSDOM}=require("../data/ui-check/node_modules/jsdom");
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const fresh=id=>({id,revision:0,turns:[],context:{}});
const response=(answer="剑魔大招基础冷却120、100、80秒。")=>({answer,status:"local",sources:[],choices:[]});
function setup(fetch,configure=()=>{}){const dom=new JSDOM(fs.readFileSync("static/chat.html","utf8"),{url:"http://localhost/chat",runScripts:"outside-only"});dom.window.fetch=fetch;configure(dom.window);vm.runInContext(fs.readFileSync("static/chat.js","utf8"),dom.getInternalVMContext());return dom;}
const ok=data=>({ok:true,json:async()=>data});

test("League timers show their game and cancel through the League command",async()=>{
 const requests=[];const state={...fresh("lol"),timers:[{id:"lol-flash",game:"lol",hero:"艾希",hero_id:"Ashe",skill:"闪现",skill_name:"闪现",ready_at:Date.now()/1000+300}]};
 const dom=setup(async(url,options)=>{if(url.endsWith("messages")){requests.push(JSON.parse(options.body));return ok({session:{...state,revision:1,timers:[]}});}return ok(state);});
 try{await tick();const d=dom.window.document;assert.match(d.querySelector(".timer-card small").textContent,/英雄联盟/);d.querySelector(".timer-remove").click();await tick();assert.equal(requests[0].text,"取消英雄联盟的艾希闪现计时");assert.equal(d.getElementById("chat-timers").hidden,true);}finally{dom.window.close();}
});

test("restored timers count down against server time and announce ready, unknown stays unknown",async()=>{
 let now=500000,update;const state={...fresh("timers"),server_epoch:1000,timers:[{id:"a",hero:"明世隐",skill:"大招",skill_name:"泰卦·长生",ready_at:1040},{id:"b",hero:"测试英雄",skill:"大招",skill_name:"特殊机制",ready_at:null}]};
 const dom=setup(async()=>ok(state),w=>{w.Date.now=()=>now;w.setInterval=fn=>{update=fn;return 1;};});
 try{await tick();const d=dom.window.document;assert.equal(d.getElementById("chat-timers").hidden,false);assert.match(d.getElementById("chat-timer-a").textContent,/40 秒/);now+=17000;update();assert.match(d.getElementById("chat-timer-a").textContent,/23 秒/);now+=24000;update();assert.match(d.getElementById("chat-timer-alert").textContent,/明世隐大招预计已好/);assert.match(d.getElementById("chat-timer-b").textContent,/冷却未知/);assert.doesNotMatch(d.getElementById("chat-timer-alert").textContent,/测试英雄/);}finally{dom.window.close();}
});

test("timer cancellation sends canonical command and new chat clears old cards",async()=>{
 let state={...fresh("timers"),timers:[{id:"a",hero:"明世隐",skill:"大招",skill_name:"泰卦·长生",ready_at:Date.now()/1000+40}]};const requests=[];
 const dom=setup(async(url,opts)=>{if(url.endsWith("messages")){requests.push(JSON.parse(opts.body));state={...state,revision:1,timers:[]};return ok({session:state});}return ok(state);});
 try{await tick();const d=dom.window.document;d.querySelector(".timer-remove").click();await tick();assert.equal(requests[0].text,"取消王者荣耀的明世隐大招计时");assert.equal(d.getElementById("chat-timers").hidden,true);state=fresh("new");d.getElementById("chat-new").click();await tick();assert.equal(d.querySelectorAll(".timer-card").length,0);}finally{dom.window.close();}
});

test("submits text, renders evidence safely and persists the conversation id",async()=>{
 const requests=[];let state=fresh("first");
 const dom=setup(async(url,options)=>{if(url.endsWith("messages")){const body=JSON.parse(options.body);requests.push(body);const r={...response("<img src=x onerror=alert(1)>"),sources:[{id:"local",title:"资料",text:"原文",game:"lol",hero:"剑魔",version:"16.19.1",note:"基础值",page_url:"/knowledge/lol",source_url:"javascript:alert(1)"}]};state={...state,revision:1,context:{game:"lol",heroes:[{name:"亚托克斯"}]},turns:[{user:body.text,response:r}]};return ok({session:state,response:r});}return ok(state);});
 try{await tick();const w=dom.window,d=w.document;d.getElementById("chat-input").value="剑魔大招多久";d.getElementById("chat-form").dispatchEvent(new w.Event("submit",{cancelable:true}));await tick();assert.equal(requests[0].revision,0);assert.match(d.getElementById("chat-context").textContent,/亚托克斯/);assert.equal(d.querySelectorAll("img").length,0);assert.equal(d.querySelectorAll('a[href^="javascript:"]').length,0);assert.equal(w.localStorage.getItem("gameplan-knowledge-chat-session"),"first");assert.equal(d.getElementById("chat-input").value,"");}finally{dom.window.close();}
});

test("late response from old chat cannot overwrite a new conversation",async()=>{
 let release,count=0;const dom=setup(async(url,options)=>{if(url.endsWith("messages"))return new Promise(resolve=>{release=()=>resolve(ok({session:{...fresh("old"),revision:1,turns:[{user:"旧问题",response:response("旧回答")}]}}));});return ok(fresh(++count===1?"old":"new"));});
 try{await tick();const d=dom.window.document;d.getElementById("chat-input").value="旧问题";d.getElementById("chat-form").dispatchEvent(new dom.window.Event("submit",{cancelable:true}));await tick();d.getElementById("chat-new").click();await tick();release();await tick();assert.doesNotMatch(d.getElementById("chat-messages").textContent,/旧回答/);assert.equal(dom.window.localStorage.getItem("gameplan-knowledge-chat-session"),"new");assert.equal(d.getElementById("chat-send").disabled,false);}finally{dom.window.close();}
});

test("retry after network error reuses request id, while choices submit a clear hero",async()=>{
 let tries=0;const bodies=[];const dom=setup(async(url,options)=>{if(!url.endsWith("messages"))return ok(fresh("chat"));const body=JSON.parse(options.body);bodies.push(body);if(++tries===1)throw new Error("离线");const r={...response("你说的是哪一位？"),status:"clarification",choices:[{label:"英雄联盟 · 孙悟空",text:"英雄联盟的孙悟空"}]};return ok({session:{...fresh("chat"),revision:tries-1,turns:[{user:body.text,response:r}]},response:r});});
 try{await tick();const d=dom.window.document;d.getElementById("chat-input").value="猴子出装";d.getElementById("chat-form").dispatchEvent(new dom.window.Event("submit",{cancelable:true}));await tick();assert.equal(d.getElementById("chat-retry").hidden,false);d.getElementById("chat-retry").click();await tick();assert.equal(bodies[0].request_id,bodies[1].request_id);d.querySelector(".choice-list button").click();await tick();assert.equal(bodies[2].text,"英雄联盟的孙悟空");assert.equal(bodies[2].revision,1);}finally{dom.window.close();}
});
