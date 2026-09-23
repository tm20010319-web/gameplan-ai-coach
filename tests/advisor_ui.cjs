const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const {JSDOM}=require('../data/ui-check/node_modules/jsdom');
const root=path.resolve(__dirname,'..');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};};

function harness(){
 const dom=new JSDOM(fs.readFileSync(path.join(root,'static/index.html'),'utf8'),{url:'http://localhost',runScripts:'outside-only'});
 const w=dom.window,requests=[],timers=new Map();let timerId=0;
 const options={handler:null,context:{side:'neutral',player:null,lineup:null}};
 w.byId=id=>w.document.getElementById(id);w.escapeHtml=text=>String(text??'').replace(/[&<>"']/g,'_');w.appState={matchId:'test'};
 w.setInterval=()=>1;w.setTimeout=(fn,delay)=>{timers.set(++timerId,{fn,delay});return timerId;};w.clearTimeout=id=>timers.delete(id);
 w.action=(id,handler)=>w.byId(id).addEventListener('click',()=>handler());
 w.gameplanLoading={analysisContext:()=>options.context};
 const result=payload=>({status:'ok',source:'deepseek',summary:'优先保护核心，再把机会转成塔。',understanding:'阵容理解',watch_for:['注意侧翼'],opportunities:['清线转塔'],scope:'lineup',input_kind:'sample',frame_id:payload.frame_id,expires_at:Date.now()/1000+120});
 w.api=async(url,payload,signal)=>{
  if(url==='/api/coach/status')return {configured:true,model:'deepseek-chat',message:'已配置'};
  requests.push({payload,signal});return options.handler?options.handler(payload,signal):result(payload);
 };
 vm.runInContext(fs.readFileSync(path.join(root,'static/advisor.js'),'utf8'),dom.getInternalVMContext());
 const frame=(id='a',phase='loading')=>({frame_id:id.repeat(32),input_kind:'sample',observation:{phase,ally_roster:['铠'],enemy_roster:['后羿'],player_hero:null,player_hp_percent:null,game_time_s:null}});
 return {w,requests,options,result,frame,timers,close:()=>w.close()};
}

test('game observations trigger an external analysis; unchanged state reuses it and non-game never calls',async()=>{
 const h=harness();try{
  h.w.gameplanAdvisor.observe(h.frame('a','not_game'));await flush();assert.equal(h.requests.length,0);
  h.w.gameplanAdvisor.observe(h.frame());await flush();assert.equal(h.requests.length,1);
  assert.equal(h.requests[0].payload.side,'neutral');assert.match(h.w.byId('coach-source').textContent,/DeepSeek/);
  h.w.gameplanAdvisor.observe(h.frame('b'));await flush();assert.equal(h.requests.length,1);
 }finally{h.close();}
});

test('only latest changed context runs after a busy request, and old result is not displayed',async()=>{
 const h=harness(),p=deferred();try{
  h.options.handler=()=>p.promise;h.w.gameplanAdvisor.observe(h.frame());await flush();
  h.options.context={side:'b',player:null,lineup:null};h.w.gameplanAdvisor.contextChanged();
  assert.equal(h.requests.length,1);
  h.options.handler=null;p.resolve({...h.result(h.requests[0].payload),summary:'旧建议'});await flush();await flush();
  assert.equal(h.requests.length,2);assert.equal(h.requests[1].payload.side,'b');
  assert.doesNotMatch(h.w.byId('coach-summary').textContent,/旧建议/);
 }finally{h.close();}
});

test('hero switch clears the previous paragraph synchronously and supersedes pending analysis',async()=>{
 const h=harness(),p=deferred();try{
  h.options.context={side:'a',player:'铠',lineup:null};h.w.gameplanAdvisor.observe(h.frame());await flush();
  assert.match(h.w.byId('coach-source').textContent,/DeepSeek/);
  h.options.handler=()=>p.promise;h.options.context={side:'b',player:'后羿',lineup:null};h.w.gameplanAdvisor.contextChanged();
  assert.equal(h.w.byId('coach-source').textContent,'正在更新');
  assert.doesNotMatch(h.w.byId('coach-summary').textContent,/优先保护核心/);
  const request=h.requests.at(-1);
  h.options.context={side:'b',player:null,lineup:null};h.w.gameplanAdvisor.contextChanged();
  assert.equal(request.signal.aborted,true);
  h.options.handler=null;p.resolve({...h.result(request.payload),summary:'过时英雄建议'});await flush();await flush();
  assert.doesNotMatch(h.w.byId('coach-summary').textContent,/过时英雄/);
  assert.equal(h.requests.at(-1).payload.player,null);
 }finally{h.close();}
});

test('stop aborts pending analysis and restart with identical input still runs',async()=>{
 const h=harness(),p=deferred();try{
  h.options.handler=()=>p.promise;h.w.gameplanAdvisor.observe(h.frame());await flush();
  h.w.gameplanAdvisor.reset('用户停止');assert.equal(h.requests[0].signal.aborted,true);
  h.w.gameplanAdvisor.observe(h.frame());h.options.handler=null;p.resolve(h.result(h.requests[0].payload));await flush();await flush();
  assert.equal(h.requests.length,2);assert.match(h.w.byId('coach-source').textContent,/DeepSeek/);
  h.w.gameplanAdvisor.reset('已停止');assert.equal(h.w.byId('coach-summary').textContent,'已停止');
 }finally{h.close();}
});

test('editing cancels advice and later observations wait until the correction is committed',async()=>{
 const h=harness();try{
  h.w.gameplanAdvisor.observe(h.frame());await flush();
  h.w.gameplanAdvisor.editing();h.w.gameplanAdvisor.observe(h.frame('b'));await flush();assert.equal(h.requests.length,1);
  h.options.context={side:'b',player:null,lineup:null};h.w.gameplanAdvisor.contextChanged();await flush();assert.equal(h.requests.length,2);
 }finally{h.close();}
});

test('missing credentials are shown as local fallback, with bounded retry cleared by stop',async()=>{
 const h=harness();try{
  h.options.handler=async p=>({...h.result(p),status:'not_configured',source:'local_fallback',message:'密钥未配置'});
  h.w.gameplanAdvisor.observe(h.frame());await flush();assert.equal(h.w.byId('coach-source').textContent,'本地备用建议');
  assert.equal(h.timers.size,1);assert.equal([...h.timers.values()][0].delay,20000);
  h.w.gameplanAdvisor.reset();assert.equal(h.timers.size,0);
 }finally{h.close();}
});

test('a changed hero only waits for the remaining external request cooldown',async()=>{
 const h=harness();try{
  h.options.handler=async p=>({...h.result(p),status:'throttled',source:'local_fallback',retry_after_s:3});
  h.w.gameplanAdvisor.observe(h.frame());await flush();
  assert.equal([...h.timers.values()][0].delay,3000);
 }finally{h.close();}
});

async function startup(query='',enabled=true,explicit=false){
 const dom=new JSDOM(fs.readFileSync(path.join(root,'static/index.html'),'utf8'),{url:'http://localhost/'+query,runScripts:'outside-only'});
 const w=dom.window;let starts=0;w.localStorage.setItem('gameplan-auto-monitor',String(enabled));
 w.byId=id=>w.document.getElementById(id);w.escapeHtml=String;w.appState={screenSource:null};w.toast=()=>{};
 w.api=async()=>({sources:[{id:'a'.repeat(16),label:'主屏'}]});w.action=(id,fn)=>w.byId(id).addEventListener('click',fn);
 w.stopMedia=()=>{w.appState.screenSource=null;};w.gameplanVision={start:async()=>{starts++;}};
 vm.runInContext(fs.readFileSync(path.join(root,'static/screen.js'),'utf8'),dom.getInternalVMContext());
 await flush();w.dispatchEvent(new w.Event('gameplan-ready'));await flush();w.dispatchEvent(new w.Event('gameplan-ready'));await flush();
 if(explicit){await w.gameplanScreen.start();assert.equal(w.location.search,'');}
 dom.window.close();return starts;
}

test('workbench entry starts selected whole-screen monitoring exactly once',async()=>assert.equal(await startup(),1));
test('sample, display, explicit off and unchecked preference do not start desktop capture',async()=>{
 for(const query of ['?sample=loading','?display=1','?monitor=off'])assert.equal(await startup(query),0);
 assert.equal(await startup('',false),0);
});
test('explicit desktop monitoring leaves static sample mode so refresh remains dynamic',async()=>{
 assert.equal(await startup('?sample=loading&plan_side=b&monitor=off',true,true),1);
});
