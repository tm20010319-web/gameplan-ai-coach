const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
const {JSDOM}=require("../data/ui-check/node_modules/jsdom");
test("knowledge search renders sourced advice, unresolved equipment and aliases safely",async()=>{
 const dom=new JSDOM(fs.readFileSync("static/knowledge.html","utf8"),{url:"http://localhost",runScripts:"outside-only"});
 const w=dom.window;
 try{
 w.fetch=async url=>({ok:true,json:async()=>url==="/api/knowledge/heroes"?{heroes:[{hero:"后羿",aliases:["后裔"]}]}:{hero:"后羿",fetched_at:"2026-09-23",voice_aliases:{aliases:["鸟人"],asr_variants:["后裔"]},skills:[{is_ultimate:true,name:"灼日之矢",cooldown_text:"45",description:"<img src=x onerror=alert(1)>"}],strategy_reference:{review_note:"版本待核验",usage:"使用说明",counterplay:"对线参考",teamfight:"团战参考",builds:[{items:[{id:91040,name:null}],note:"按阵容调整"}]}}});
 vm.runInContext(fs.readFileSync("static/knowledge.js","utf8"),dom.getInternalVMContext());
 await new Promise(r=>setImmediate(r));
 assert.match(w.document.body.textContent,/对线参考/);
 assert.match(w.document.body.textContent,/91040（待核对）/);
 assert.equal(w.document.querySelectorAll("img").length,0);
 const input=w.document.getElementById("hero-search"); input.value="后裔";input.dispatchEvent(new w.Event("input"));await new Promise(r=>setImmediate(r));
 assert.equal(w.document.getElementById("hero-select").value,"后羿");
 input.value="不存在";input.dispatchEvent(new w.Event("input"));await new Promise(r=>setImmediate(r));
 assert.equal(w.document.getElementById("hero-knowledge").textContent,"");
 }finally{dom.window.close();}
});
