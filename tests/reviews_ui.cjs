const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {JSDOM}=require('../data/ui-check/node_modules/jsdom');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const base={id:'match-one',game:'lol',hero:'艾希',started:1700000000,status:'ready',samples:4,outcome:'loss',result:{source:'deepseek',model:'deepseek-test',points:[{issue:'这次压线过深，退路不足。',advice:'下局先确认队友位置，再向前补刀。',evidence_ids:['frame-one']}]}};
const ok=data=>({ok:true,json:async()=>data});
function harness(configure=()=>{}){
 const dom=new JSDOM(fs.readFileSync('static/monitor.html','utf8'),{url:'http://localhost/monitor?monitor=off',runScripts:'outside-only'});
 const w=dom.window, calls=[],tasks=[];w.AbortSignal=AbortSignal;
 w.setTimeout=(fn)=>{tasks.push(fn);return tasks.length;};w.clearTimeout=()=>{};
 let current=null;const options={get:()=>current,history:[base],detail:{...base,frames:[{id:'frame-one',observation:{game_time_s:125,facts:[{text:'残血站在敌方塔前'}]}}]}};
 w.fetch=async(url,req={})=>{calls.push({url,req});
  if(url.includes('/streams/'))return ok({match:options.get()});
  if(url.includes('/retry'))return ok({...base,status:'analyzing',result:null});
  if(url.startsWith('/api/reviews?'))return ok({items:options.history,total:options.total??options.history.length});
  return ok(options.detail);
 };
 vm.runInContext('const appState={matchId:"stream-one"};',dom.getInternalVMContext());
 configure(w,options);
 vm.runInContext(fs.readFileSync('static/reviews.js','utf8'),dom.getInternalVMContext());
 return {w,calls,tasks,options,dom,observe:match=>w.gameplanReview.observe({input_kind:'live',review:match}),get:id=>w.document.getElementById(id),close:()=>w.close()};
}
test('short card renders issue and advice, safe text and only two points',async()=>{
 const h=harness();try{await tick();
  h.observe({...base,result:{...base.result,points:[{...base.result.points[0],issue:'<img src=x onerror=alert(1)>'},base.result.points[0],base.result.points[0]]}});
  assert.equal(h.get('review-current').querySelectorAll('li').length,2);
  assert.equal(h.get('review-current').querySelectorAll('img').length,0);
  assert.match(h.get('review-current').textContent,/下局：/);
  assert.equal(h.get('review-game').textContent,'英雄联盟');
 }finally{h.close();}
});
test('completed match is restored and evidence is fetched only on expansion',async()=>{
 const h=harness((w,o)=>{o.get=()=>base;});try{await tick();await tick();
  assert.match(h.get('review-current').textContent,/艾希/);
  assert.equal(h.calls.some(c=>c.url==='/api/reviews/match-one'),false);
  const detail=h.get('review-current').querySelector('details');detail.open=true;detail.dispatchEvent(new h.w.Event('toggle'));await tick();
  const image=h.get('review-current').querySelector('img');
  assert.equal(image.getAttribute('src'),'/api/reviews/frames/frame-one');
  assert.match(h.get('review-current').textContent,/2:05/);
 }finally{h.close();}
});
test('history supports pagination and loads game-specific cards',async()=>{
 const h=harness((w,o)=>{o.total=21;});try{await tick();
  h.get('review-history').open=true;h.get('review-history').dispatchEvent(new h.w.Event('toggle'));await tick();
  assert.match(h.get('review-history-list').textContent,/英雄联盟.*艾希/);
  assert.equal(h.get('review-next').disabled,false);
  h.get('review-next').click();await tick();
  assert.ok(h.calls.some(c=>c.url.endsWith('offset=10')));
  assert.equal(h.get('review-prev').disabled,false);
 }finally{h.close();}
});
test('late poll from previous stream cannot overwrite new match',async()=>{
 const h=harness();try{await tick();
  h.observe({...base,status:'analyzing',result:null});
  let resolve;h.options.get=()=>new Promise(r=>{resolve=r;});
  // Delay the entire fetch, as a real previous request would be delayed.
  const fetch=h.w.fetch;h.w.fetch=async(url,req)=>{if(url.includes('/streams/'))return new Promise(r=>{resolve=()=>r(ok({match:base}));});return fetch(url,req);};
  const polling=h.tasks[0]();await tick();
  h.w.gameplanReview.reset('new-stream');resolve();await polling;
  assert.equal(h.get('review-current').textContent,'');
 }finally{h.close();}
});
test('failure offers retry, insufficient evidence makes no invented suggestions',async()=>{
 const h=harness();try{await tick();
  h.observe({...base,status:'failed',result:{points:[],message:'DeepSeek 请求失败'}});
  h.get('review-current').querySelector('button').click();await tick();
  assert.ok(h.calls.some(c=>c.url.endsWith('/retry')&&c.req.method==='POST'));
  h.observe({...base,status:'insufficient',result:{points:[],message:'个人记录不足'}});
  assert.equal(h.get('review-current').querySelectorAll('li').length,0);
  assert.match(h.get('review-current').textContent,/记录不足/);
 }finally{h.close();}
});
test('refresh restores the stream identifier; video observations cannot overwrite live review',async()=>{
 const h=harness();try{await tick();
  h.w.gameplanReview.reset('saved-stream');
  assert.equal(h.w.gameplanReview.streamId(),'saved-stream');
  h.observe(base);h.w.gameplanReview.observe({input_kind:'video',review:{...base,hero:'错误英雄'}});
  assert.match(h.get('review-current').textContent,/艾希/);
  assert.doesNotMatch(h.get('review-current').textContent,/错误英雄/);
 }finally{h.close();}
});
test('recording keeps polling so a result completed during pause still appears',async()=>{
 const h=harness();try{await tick();
  h.observe({...base,status:'recording',result:null});
  assert.equal(h.tasks.length,1);
  h.options.get=()=>base;await h.tasks[0]();
  assert.match(h.get('review-current').textContent,/这次压线过深/);
  assert.equal(h.get('review-live-status').textContent,'已完成');
 }finally{h.close();}
});
