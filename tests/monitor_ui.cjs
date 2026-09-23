const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const {JSDOM}=require('../data/ui-check/node_modules/jsdom');
const root=path.resolve(__dirname,'..');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
function harness({auto=false,query='',savedModel=null,savedSource='a'.repeat(16),savedRegion=null,videoMode='latest',savedBp=null}={}){
 const dom=new JSDOM(fs.readFileSync(path.join(root,'static/monitor.html'),'utf8'),{url:'http://localhost/monitor'+query,runScripts:'outside-only'});
 const w=dom.window,tasks=new Map(),requests=[],channels=[],intervals=[];let next=0;
 if(videoMode!==null)w.document.getElementById('video-mode').value=videoMode;
 const options={hero:'后羿',phase:'bp',ready:true,infer:null,capture:null,observeFailures:[],videoError:false,videoDuration:120,modelBusy:0,sources:[{id:'a'.repeat(16),label:'屏幕一'},{id:'b'.repeat(16),label:'屏幕二'}]};
 const videos=[],revoked=[],seeks=[],decoded=[];
 w.URL.createObjectURL=()=>`blob:test-${videos.length}`;w.URL.revokeObjectURL=url=>revoked.push(url);
 const create=w.document.createElement.bind(w.document);
 w.document.createElement=(tag,...args)=>{
  const element=create(tag,...args);
  if(tag==='video'){
   videos.push(element);let current=0,paused=true;
   const callbacks=new Map();let callbackId=0;
   element.requestVideoFrameCallback=fn=>{callbacks.set(++callbackId,fn);return callbackId;};
   element.cancelVideoFrameCallback=id=>callbacks.delete(id);
   element.presentFrame=(index,time)=>{current=time;const pending=[...callbacks.values()];callbacks.clear();pending.forEach(fn=>fn(0,{presentedFrames:index,mediaTime:time}));};
   Object.defineProperty(element,'paused',{get:()=>paused});
   Object.defineProperties(element,{ended:{get:()=>current>=options.videoDuration},duration:{get:()=>options.videoDuration},videoWidth:{value:1280},videoHeight:{value:576},currentTime:{get:()=>current,set:value=>{seeks.push(value);if(value!==current){current=value;queueMicrotask(()=>element.dispatchEvent(new w.Event('seeked')));}}}});
   element.play=async()=>{paused=false;};element.advance=()=>{if(!paused)current=Math.min(options.videoDuration,current+10);};element.pause=()=>{paused=true;};element.load=()=>{if(element.hasAttribute('src'))queueMicrotask(()=>element.dispatchEvent(new w.Event(options.videoError?'error':'loadeddata')));};
  }
  if(tag==='canvas'){element.getContext=()=>({getImageData:()=>({data:options.probePixels||new Uint8ClampedArray(64*36*4)}),drawImage(media){if(media?.tagName==='VIDEO')decoded.push(media.currentTime);}});element.toDataURL=type=> `data:${type};base64,AAAA`;}
  return element;
 };
 w.localStorage.setItem('gameplan-monitor-autostart',String(auto));w.AbortController=AbortController;w.AbortSignal=AbortSignal;
 if(savedSource!==null)w.localStorage.setItem('gameplan-screen-source',savedSource);
 if(savedRegion!==null)w.localStorage.setItem('gameplan-monitor-region',savedRegion);
 if(savedModel!==null)w.localStorage.setItem('monitor-vision-model',savedModel);
 if(savedBp!==null)w.localStorage.setItem('gameplan-bp-context',JSON.stringify(savedBp));
 w.setInterval=fn=>{intervals.push(fn);return intervals.length;};w.setTimeout=(fn,delay)=>{tasks.set(++next,{fn,delay});return next;};w.clearTimeout=id=>tasks.delete(id);
 w.BroadcastChannel=class{constructor(){this.messages=[];channels.push(this);}postMessage(message){this.messages.push(message);}close(){}};
 const result=payload=>({frame_id:String(next+1).padStart(32,'0'),focus:payload.focus,input_kind:payload.input_kind,video_time_s:payload.video_time_s,captured_at:payload.captured_at,elapsed_s:1,fresh:true,
  observation:{phase:options.phase,player_hero:options.phase==='bp'?options.hero:null,ally_roster:[options.hero,'蔡文姬'],enemy_roster:['铠'],game_time_s:null,player_hp_percent:null,self_skills:[],note:'测试识别'}});
 w.fetch=async(url,request)=>{
  const payload=request.body?JSON.parse(request.body):undefined;requests.push({url,payload,signal:request.signal});let data;
  if(url==='/api/screen/sources')data={sources:options.sources};
  else if(url.startsWith('/api/vision/status'))data={ready:options.ready,model:'qwen3-vl:8b',message:options.ready?'就绪':'未安装'};
  else if(url.startsWith('/api/vision/warmup')){
   if(options.warmupBusy>0){options.warmupBusy--;return {ok:false,status:429,json:async()=>({detail:'模型正在处理画面，稍后重试。'})};}
   data=options.warmup?await options.warmup():{ready:options.ready};
  }
  else if(url==='/api/coach/status')data={configured:true,model:'deepseek-v4-flash',message:'已配置'};
  else if(url==='/api/monitor/match/reset')data={cleared:true};
  else if(url==='/api/screen/frame')data=options.capture?await options.capture(payload):{image_base64:'data:image/png;base64,AAAA',captured_at:Date.now()/1000};
  else if(url==='/api/vision/observe'){
   if(payload.input_kind==='video')videos.forEach(video=>video.advance());
   const failure=options.observeFailures.shift();
   if(failure instanceof Error)throw failure;
   if(failure)return {ok:false,status:failure.status,json:async()=>{if(failure.nonJson)throw new SyntaxError('not JSON');return {detail:failure.detail};}};
   if(options.modelBusy>0){options.modelBusy--;return {ok:false,status:429,json:async()=>({detail:'模型正在处理上一帧'})};}
   data=options.infer?await options.infer(payload):result(payload);
  }
  else if(url==='/api/loading/plan')data={summary:'配合清线',personal:payload.player?{hero:payload.player,text:'当前英雄任务'}:null};
  else if(url==='/api/bp/plan')data={items:payload.enemies.map(hero=>({hero,threat:'测试威胁',counters:[{hero:'王昭君',reason:'限制进场'}],advice:'留意敌方位置'}))};
  else if(url==='/api/coach/analysis')data={status:'ok',source:'deepseek',summary:`${payload.player||'全队'}注意配合`,understanding:'阵容理解',watch_for:[],opportunities:[],scope:'lineup',input_kind:'live',expires_at:Date.now()/1000+120};
  else throw new Error(url);
  return {ok:true,json:async()=>data};
 };
 for(const file of ['video-candidates.js','monitor.js','enemies.js','advisor.js','overlay.js'])vm.runInContext(fs.readFileSync(path.join(root,'static',file),'utf8'),dom.getInternalVMContext());
 return {w,tasks,requests,options,channels,intervals,result,videos,revoked,seeks,decoded,get:id=>w.document.getElementById(id),
  async chooseVideo(name='test.mp4'){const input=w.document.getElementById('video-file');Object.defineProperty(input,'files',{configurable:true,value:[new w.File(['video'],name,{type:'video/mp4'})]});input.dispatchEvent(new w.Event('change'));await flush();await flush();},
  async next(){const before=requests.filter(r=>r.url==='/api/vision/observe').length;for(let i=0;i<5;i++){const task=[...tasks.entries()].find(([,t])=>t.delay<6000);assert.ok(task);tasks.delete(task[0]);await task[1].fn();await flush();if(requests.filter(r=>r.url==='/api/vision/observe').length>before)break;}},close:()=>w.close()};
}

test('compact panel keeps five enemy rows, shared timers and accessible capture controls',async()=>{
 const h=harness({query:'?panel=1&source='+ 'a'.repeat(16)});
 try {await flush();await flush();
  assert.equal(h.w.document.body.classList.contains('overlay-mode'),true);
  assert.equal(h.get('enemy-cooldown-board').children.length,5);
  assert.ok(h.w.document.querySelector('.overlay-content').contains(h.get('enemy-skill-subtitles')));
  assert.equal(h.w.document.querySelector('.controls').hidden,true);
  h.get('panel-settings').click();
  assert.equal(h.w.document.querySelector('.controls').hidden,false);
  assert.equal(h.get('panel-settings').getAttribute('aria-expanded'),'true');
  await h.w.gameplanMonitor.start();await flush();
  assert.equal(h.get('panel-pause').textContent,'暂停');
  h.get('panel-pause').click();await flush();
  assert.equal(h.get('monitor-pause').disabled,true);
 } finally {h.close();}
});

test('window capture ignores a persisted half-screen crop and exposes full-window mode',async()=>{
 const h=harness({savedRegion:'left'});
 try {await flush();await flush();
  h.options.sources=[{id:'a'.repeat(16),label:'AirDroid',kind:'window'}];
  h.get('screen-refresh').click();await flush();
  await h.w.gameplanMonitor.start();await flush();
  assert.equal(h.get('capture-region').disabled,true);
  assert.equal(h.requests.find(r=>r.url==='/api/screen/frame').payload.roi,null);
 } finally {h.close();}
});

test('a minimized selected window waits for restoration without warming the model or capturing',async()=>{
 const h=harness();
 try {await flush();await flush();
  h.options.sources=[{id:'a'.repeat(16),label:'AirDroid',kind:'window',minimized:true}];
  h.get('screen-refresh').click();await flush();
  await h.w.gameplanMonitor.start();await flush();
  assert.equal(h.requests.some(r=>r.url.startsWith('/api/vision/warmup')||r.url==='/api/screen/frame'),false);
  assert.match(h.get('monitor-status').textContent,/最小化/);
 } finally {h.close();}
});

test('standalone opens without workbench APIs, obeys opt-out, and defaults to automatic capture',async()=>{
 for(const config of [{auto:false},{auto:true,query:'?monitor=off'},{auto:true}]){
  const h=harness(config);try{await flush();await flush();assert.equal(h.requests.some(r=>r.url==='/api/screen/frame'),config.auto&&!config.query);assert.ok(h.requests.every(r=>!r.url.includes('/matches')));h.w.gameplanMonitor.stop();}finally{h.close();}
 }
});

test('first use waits for one selected source then automatically starts that source',async()=>{
 const h=harness({auto:true,savedSource:null});try{await flush();await flush();
  assert.equal(h.requests.some(r=>r.url==='/api/screen/frame'),false);
  assert.equal(h.get('monitor-start').disabled,true);assert.match(h.get('monitor-status').textContent,/首次使用/);
  const old=h.get('match-label').textContent;
  h.get('screen-source').value='b'.repeat(16);h.get('screen-source').dispatchEvent(new h.w.Event('change'));await flush();await flush();
  assert.notEqual(h.get('match-label').textContent,old);
  assert.equal(h.requests.find(r=>r.url==='/api/screen/frame').payload.source_id,'b'.repeat(16));
 }finally{h.close();}
});

test('saved absent window waits without fallback and reconnects only to that identity',async()=>{
 const h=harness({auto:true,savedSource:'c'.repeat(16)});try{await flush();await flush();
  assert.equal(h.requests.some(r=>r.url==='/api/screen/frame'),false);assert.equal(h.get('monitor-stop').disabled,false);
  h.options.sources.push({id:'c'.repeat(16),label:'窗口 · 手机'});
  await h.next();await flush();
  assert.equal(h.requests.find(r=>r.url==='/api/screen/frame').payload.source_id,'c'.repeat(16));
 }finally{h.close();}
});

test('stop, opt-out and another monitor cancel a pending source reconnect',async()=>{
 for(const cancel of ['stop','auto','other']){
  const h=harness({auto:true,savedSource:'c'.repeat(16)});try{await flush();await flush();
   assert.ok([...h.tasks.values()].some(t=>t.delay===2000));
   if(cancel==='stop')h.get('monitor-stop').click();
   if(cancel==='auto'){h.get('monitor-auto').checked=false;h.get('monitor-auto').dispatchEvent(new h.w.Event('change'));}
   if(cancel==='other')h.channels[0].onmessage({data:{owner:'other'}});
   assert.equal([...h.tasks.values()].some(t=>t.delay===2000),false);
   assert.equal(h.requests.some(r=>r.url==='/api/screen/frame'),false);
  }finally{h.close();}
 }
});

test('workspace embeds an auto-starting monitor unless URL explicitly opts out',()=>{
 for(const query of ['', '?monitor=off']){
  const dom=new JSDOM(fs.readFileSync(path.join(root,'static/workspace.html'),'utf8'),{url:'http://localhost/monitor'+query,runScripts:'outside-only'});
  try{dom.window.ResizeObserver=class{observe(){}};
   vm.runInContext(fs.readFileSync(path.join(root,'static/workspace.js'),'utf8'),dom.getInternalVMContext());
   const url=new URL(dom.window.document.querySelector('iframe').src);
   assert.equal(url.searchParams.get('mode'),'screen');assert.equal(url.searchParams.get('monitor'),query?'off':null);
  }finally{dom.window.close();}
 }
});

test('model preload finishes before capture, and stopping preload never starts capture later',async()=>{
 const h=harness(),pending=deferred();try{await flush();h.options.warmup=()=>pending.promise;
  const starting=h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('monitor-status').textContent,/预热/);assert.equal(h.requests.some(r=>r.url==='/api/screen/frame'),false);
  h.w.gameplanMonitor.stop();pending.resolve({ready:true});await starting;await flush();
  assert.equal(h.requests.some(r=>r.url==='/api/screen/frame'),false);
 }finally{h.close();}
});
test('successive heroes update identity, local task and external analysis without re-entering the page',async()=>{
 const h=harness();try{await flush();await h.w.gameplanMonitor.start();await flush();assert.equal(h.get('current-hero').textContent,'后羿');
  h.options.hero='孙尚香';await h.next();assert.equal(h.get('current-hero').textContent,'孙尚香');assert.match(h.get('quick-text').textContent,/选人阶段.*铠/);assert.match(h.get('coach-summary').textContent,/孙尚香/);
  h.options.hero='狄仁杰';await h.next();assert.equal(h.get('current-hero').textContent,'狄仁杰');assert.equal(h.requests.filter(r=>r.url==='/api/vision/observe').length,3);
 }finally{h.close();}
});
test('pause preserves the last observation while stop clears it',async()=>{
 const h=harness();try{await flush();await h.w.gameplanMonitor.start();await flush();h.get('monitor-pause').click();await flush();
  assert.equal(h.get('frame-image').hidden,false);assert.equal(h.get('preview-paused').hidden,false);assert.equal(h.get('current-hero').textContent,'后羿');assert.equal(h.get('quick-plan').hidden,false);assert.equal(h.tasks.size,0);
  h.get('monitor-stop').click();assert.equal(h.get('frame-image').hidden,true);assert.equal(h.get('frame-image').hasAttribute('src'),false);
 }finally{h.close();}
});
test('stopped inference cannot restore a previous hero, and a restarted session accepts only new frames',async()=>{
 const h=harness(),pending=deferred();try{await flush();let old;h.options.infer=p=>{old=p;return pending.promise;};await h.w.gameplanMonitor.start();await flush();h.w.gameplanMonitor.stop();
  h.options.infer=null;h.options.hero='孙尚香';await h.w.gameplanMonitor.start();await flush();pending.resolve({...h.result(old),observation:{...h.result(old).observation,player_hero:'后羿'}});await flush();
  assert.equal(h.get('current-hero').textContent,'待确认');await h.next();assert.equal(h.get('current-hero').textContent,'孙尚香');
 }finally{h.close();}
});
test('model and desktop failures do not keep capturing or switch to another screen',async()=>{
 const h=harness();try{await flush();h.options.ready=false;await assert.rejects(h.w.gameplanMonitor.start(),/未安装/);assert.equal(h.requests.filter(r=>r.url==='/api/screen/frame').length,0);
  h.options.ready=true;h.options.capture=()=>{throw new Error('桌面不可访问');};await h.w.gameplanMonitor.start();await flush();assert.match(h.get('monitor-status').textContent,/桌面不可访问/);assert.equal(h.requests.filter(r=>r.url==='/api/vision/observe').length,0);assert.equal(h.get('monitor-pause').disabled,true);
 }finally{h.close();}
});
test('source changes, new matches and another monitor window clear active observations',async()=>{
 const h=harness();try{await flush();await h.w.gameplanMonitor.start();await flush();h.get('screen-source').value='b'.repeat(16);h.get('screen-source').dispatchEvent(new h.w.Event('change'));assert.equal(h.get('frame-image').hidden,true);
  await h.w.gameplanMonitor.start();await flush();assert.equal(h.requests.filter(r=>r.url==='/api/screen/frame').at(-1).payload.source_id,'b'.repeat(16));h.channels[0].onmessage({data:{owner:'another-window'}});assert.match(h.get('monitor-status').textContent,/另一个窗口/);
  const previous=h.get('match-label').textContent;h.get('monitor-new').click();assert.notEqual(h.get('match-label').textContent,previous);
 }finally{h.close();}
});
test('own-team perspective rejects enemy selection and non-game frames clear advice',async()=>{
 const h=harness();try{await flush();await h.w.gameplanMonitor.start();await flush();h.get('monitor-side').value='b';h.get('monitor-side').dispatchEvent(new h.w.Event('change'));h.get('monitor-hero').value='铠';h.get('monitor-hero').dispatchEvent(new h.w.Event('change'));await flush();await flush();assert.doesNotMatch(h.get('coach-summary').textContent,/铠/);assert.equal(h.get('monitor-hero').value,'');
  h.options.phase='not_game';await h.next();assert.equal(h.get('current-hero').textContent,'待确认');assert.equal(h.get('quick-plan').hidden,true);assert.doesNotMatch(h.get('coach-summary').textContent,/铠/);
 }finally{h.close();}
});
test('closing the window aborts in-flight capture before it can be sent to Qwen',async()=>{
 const h=harness(),pending=deferred();try{await flush();h.options.capture=()=>pending.promise;await h.w.gameplanMonitor.start();await flush();h.w.dispatchEvent(new h.w.Event('pagehide'));pending.resolve({image_base64:'late',captured_at:Date.now()/1000});await flush();assert.equal(h.requests.filter(r=>r.url==='/api/vision/observe').length,0);assert.equal(h.get('frame-image').hidden,true);
 }finally{h.close();}
});

test('video plays continuously and observes current playback position without fixed seeks',async()=>{
 const h=harness();try{await flush();await h.chooseVideo();
  const observations=h.requests.filter(r=>r.url==='/api/vision/observe');
  assert.equal(observations.length,12);assert.ok(observations.every(r=>r.payload.input_kind==='video'));
  assert.ok(observations.every(r=>r.payload.image_base64.startsWith('data:image/png;')));
  assert.deepEqual(h.seeks,[]);
  assert.equal(h.requests.some(r=>r.url==='/api/screen/frame'),false);
  assert.match(h.get('video-status').textContent,/视频播放与识别完成.*12 帧/);assert.equal(h.get('video-status').hidden,false);
  assert.equal(h.get('frame-image').hidden,false);assert.match(h.get('capture-status').textContent,/播放 02:00 \/ 02:00.*最近完成 01:50/);
  assert.equal(h.get('monitor-pause').disabled,true);assert.equal(h.revoked.length,1);
 }finally{h.close();}
});

test('pause aborts video inference, ignores its late response and resumes the pending sample',async()=>{
 const h=harness(),pending=deferred();try{await flush();let payload;h.options.infer=p=>{payload=p;return pending.promise;};await h.chooseVideo();
  assert.equal(h.get('monitor-pause').disabled,false);h.get('monitor-pause').click();await flush();
  assert.equal(h.requests.find(r=>r.url==='/api/vision/observe').signal.aborted,true);
  assert.match(h.get('monitor-start').textContent,/继续视频分析/);assert.equal(h.get('preview-paused').hidden,false);
  pending.resolve(h.result(payload));await flush();assert.equal(h.get('current-hero').textContent,'待确认');
  h.options.infer=null;await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('video-status').textContent,/视频播放与识别完成/);assert.equal(h.get('frame-count').textContent,'11 帧');assert.equal(h.revoked.length,2);
 }finally{h.close();}
});

test('replacing a video prevents the prior request from updating the completed new video',async()=>{
 const h=harness(),pending=deferred();try{await flush();let old;h.options.infer=p=>{old=p;return pending.promise;};await h.chooseVideo('old.mp4');
  h.options.infer=null;h.options.hero='孙尚香';await h.chooseVideo('new.mp4');
  pending.resolve({...h.result(old),observation:{...h.result(old).observation,player_hero:'后羿'}});await flush();
  assert.equal(h.get('current-hero').textContent,'孙尚香');assert.match(h.get('video-status').textContent,/new.mp4/);assert.equal(h.get('frame-count').textContent,'12 帧');assert.equal(h.revoked.length,2);
 }finally{h.close();}
});

test('invalid media releases its URL, reports the failure and can be replaced',async()=>{
 const h=harness();try{await flush();h.options.videoError=true;await h.chooseVideo('bad.mp4');
  assert.match(h.get('video-status').textContent,/无法解码/);assert.equal(h.requests.some(r=>r.url==='/api/vision/observe'),false);assert.equal(h.revoked.length,1);
  h.options.videoError=false;h.options.videoDuration=.5;await h.chooseVideo('short.mp4');assert.match(h.get('video-status').textContent,/完成.*1 帧/);
 }finally{h.close();}
});

test('video waits for a previous model request to finish and retry remains cancellable',async()=>{
 for(const cancel of [false,true]){
  const h=harness();try{await flush();h.options.modelBusy=1;await h.chooseVideo();
   assert.equal(h.get('monitor-pause').disabled,false);assert.doesNotMatch(h.get('video-status').textContent,/失败/);
   if(cancel){h.get('monitor-stop').click();await flush();assert.equal(h.revoked.length,1);assert.equal(h.requests.filter(r=>r.url==='/api/vision/observe').length,1);}
   else{await h.next();assert.match(h.get('video-status').textContent,/完成.*11 帧/);}
  }finally{h.close();}
 }
});

test('video warmup waits through repeated 429 responses and starts automatically',async()=>{
 const h=harness();try{await flush();h.options.warmupBusy=2;await h.chooseVideo();
  assert.equal(h.get('monitor-pause').disabled,false);
  assert.match(h.get('video-status').textContent,/等待.*自动开始/);
  assert.doesNotMatch(h.get('frame-time').textContent,/已暂停/);
  assert.equal(h.get('preview-paused').hidden,true);
  assert.equal(h.channels[0].messages.length,1,'take ownership before waiting for another window to release the model');
  assert.equal(h.requests.some(r=>r.url==='/api/vision/observe'),false);
  await h.next();await flush();
  assert.equal(h.requests.filter(r=>r.url.startsWith('/api/vision/warmup')).length,3);
  assert.match(h.get('video-status').textContent,/视频播放与识别完成.*12 帧/);
 }finally{h.w.gameplanMonitor.stop();h.close();}
});

test('stopping, pausing or replacing a video cancels its warmup retry',async()=>{
 for(const action of ['stop','pause','replace']){
  const h=harness();try{await flush();h.options.warmupBusy=1;await h.chooseVideo('old.mp4');
   assert.equal(h.get('monitor-pause').disabled,false);
   assert.ok([...h.tasks.values()].some(t=>t.delay===1000));
   if(action==='replace')await h.chooseVideo('new.mp4');
   else h.get('monitor-'+action).click();
   await flush();
   assert.equal([...h.tasks.values()].some(t=>t.delay===1000),false);
   assert.equal(h.revoked.length,action==='replace'?2:1);
   if(action==='replace')assert.match(h.get('video-status').textContent,/完成.*new.mp4/);
   else{
    assert.equal(h.requests.some(r=>r.url==='/api/vision/observe'),false);
    if(action==='pause'){
     await h.w.gameplanMonitor.start();await flush();
     assert.match(h.get('video-status').textContent,/视频播放与识别完成/);
    }
   }
  }finally{h.w.gameplanMonitor.stop();h.close();}
 }
});

test('screen monitoring also waits for busy warmup without reporting a pause',async()=>{
 const h=harness();try{await flush();h.options.warmupBusy=1;
  const starting=h.w.gameplanMonitor.start().catch(error=>error);await flush();
  assert.equal(h.get('monitor-pause').disabled,false);
  assert.equal(h.channels[0].messages.length,1);
  assert.match(h.get('monitor-status').textContent,/等待.*自动开始/);
  await h.next();assert.equal(await starting,undefined);
  assert.ok(h.requests.some(r=>r.url==='/api/screen/frame'));
 }finally{h.w.gameplanMonitor.stop();h.close();}
});

test('temporary HTTP, timeout and network failures keep video playing and recover on a fresh frame',async()=>{
 for(const failure of [{status:502,detail:'本帧无法解析'},{status:503,nonJson:true},new TypeError('Failed to fetch'),Object.assign(new Error('timeout'),{name:'TimeoutError'})]){
  const h=harness(),pending=deferred();try{await flush();h.options.observeFailures=[failure];await h.chooseVideo();
   const video=h.videos.at(-1);assert.equal(video.paused,false);assert.equal(video.isConnected,true);
   assert.match(h.get('video-status').textContent,/视频继续播放/);assert.equal(h.get('preview-paused').hidden,true);
   h.options.infer=()=>pending.promise;
   await h.next();assert.equal(h.requests.filter(r=>r.url==='/api/vision/observe').length,2);
   assert.match(h.get('capture-status').textContent,/正在识别 00:10/);
   h.options.infer=null;const payload=h.requests.filter(r=>r.url==='/api/vision/observe').at(-1).payload;
   pending.resolve(h.result(payload));await flush();await flush();
   assert.match(h.get('video-status').textContent,/视频播放与识别完成.*11 帧/);
  }finally{h.w.gameplanMonitor.stop();h.close();}
 }
});

test('repeated failures back off, report the reason, and a manual pause cancels recovery',async()=>{
 const h=harness();try{await flush();h.options.observeFailures=Array.from({length:5},()=>({status:503,detail:'模型暂不可用'}));await h.chooseVideo();
  await h.next();await h.next();
  assert.equal(h.videos.at(-1).paused,false);
  assert.match(h.get('video-status').textContent,/连续 3 次.*模型暂不可用.*视频继续播放/);
  assert.ok([...h.tasks.values()].some(t=>t.delay===4000));
  h.get('monitor-pause').click();await flush();
  assert.equal(h.videos.at(-1).paused,true);assert.equal(h.revoked.length,1);
  assert.equal([...h.tasks.values()].some(t=>t.delay===4000),false);
  assert.equal(h.requests.filter(r=>r.url==='/api/vision/observe').length,3);
 }finally{h.close();}
});

test('validation details are visible and permanent bad requests do not retry indefinitely',async()=>{
 const h=harness();try{await flush();h.options.observeFailures=[{status:422,detail:[{loc:['body','recent_frames',0,'image_base64'],msg:'String should have at most 12000000 characters',input:'private-frame-data'}]}];await h.chooseVideo();
  assert.match(h.get('video-status').textContent,/recent_frames\.0\.image_base64.*12000000.*HTTP 422/);
  assert.doesNotMatch(h.get('video-status').textContent,/private-frame-data|请求未完成/);
  assert.equal(h.videos.at(-1).paused,true);assert.equal(h.requests.filter(r=>r.url==='/api/vision/observe').length,1);
 }finally{h.close();}
});

test('ending a video on a failed analysis does not claim recognition succeeded',async()=>{
 const h=harness();try{await flush();h.options.videoDuration=5;h.options.observeFailures=[{status:502,detail:'本帧无法解析'}];await h.chooseVideo();
  assert.match(h.get('video-status').textContent,/视频播放完成.*0 帧已识别.*最近画面未能识别/);
  assert.doesNotMatch(h.get('video-status').textContent,/播放与识别完成/);assert.equal(h.revoked.length,1);
 }finally{h.close();}
});

test('stopping or closing the page discards in-flight video results and releases media',async()=>{
 for(const closePage of [false,true]){
  const h=harness(),pending=deferred();try{await flush();let payload;h.options.infer=p=>{payload=p;return pending.promise;};await h.chooseVideo();
   if(closePage)h.w.dispatchEvent(new h.w.Event('pagehide'));else h.get('monitor-stop').click();
   pending.resolve(h.result(payload));await flush();assert.equal(h.get('frame-image').hidden,true);assert.equal(h.get('current-hero').textContent,'待确认');assert.equal(h.revoked.length,1);
   assert.equal(h.requests.filter(r=>r.url==='/api/vision/observe').length,1);assert.doesNotMatch(h.get('monitor-start').textContent,/视频/);
  }finally{h.close();}
 }
});

test('video keeps the last completed advice visible while reading the next frame and makes no secondary BP call',async()=>{
 const h=harness(),pending=deferred();try{await flush();let count=0;
  h.options.infer=p=>++count===1?h.result(p):pending.promise;
  await h.chooseVideo();
  assert.match(h.get('coach-summary').textContent,/后羿/);
  assert.equal(h.requests.some(r=>r.url==='/api/bp-assistant/observe'),false);
  assert.equal(h.requests.filter(r=>r.url==='/api/coach/analysis').length,1);
  assert.equal(count,2);
  h.w.gameplanMonitor.stop();pending.resolve(h.result({captured_at:Date.now()/1000}));await flush();
  assert.doesNotMatch(h.get('monitor-status').textContent,/持续观察中/);
 }finally{h.close();}
});

test('loading advice uses the lane without promoting a cached BP hero to current player',async()=>{
 const h=harness();try{await flush();h.options.phase='loading';h.options.infer=p=>({...h.result(p),bp_context:{player:'后羿',lane:'发育路',source:'official_portrait_match'}});
  await h.w.gameplanMonitor.start();await flush();
  const request=h.requests.find(r=>r.url==='/api/coach/analysis');
  assert.equal(request.payload.player,null);assert.equal(request.payload.lane,'发育路');assert.equal(request.payload.side,'neutral');
  assert.doesNotMatch(h.get('coach-summary').textContent,/后羿/);assert.match(h.get('quick-text').textContent,/发育路/);
 }finally{h.close();}
});


test('lane-only observation shows farm-lane advice without inventing a hero',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>({...h.result(p),lane_context:{lane:'发育路',evidence:'本局您的分路 / 发育路',advice:'优先补刀和生存',from_current_frame:true},observation:{...h.result(p).observation,phase:'in_game',player_hero:null,ally_roster:[],enemy_roster:[]}});
  await h.w.gameplanMonitor.start();await flush();
  assert.equal(h.get('current-lane').textContent,'发育路');assert.equal(h.get('current-hero').textContent,'待确认');
  assert.equal(h.get('lane-plan').hidden,false);assert.match(h.get('quick-text').textContent,/发育路/);
  assert.ok(!h.requests.some(r=>r.url==='/api/loading/plan'));
  assert.equal(h.requests.find(r=>r.url==='/api/coach/analysis').payload.lane,'发育路');
  h.get('monitor-new').click();assert.equal(h.get('current-lane').textContent,'待确认');assert.equal(h.get('lane-plan').hidden,true);
 }finally{h.close();}
});

test('BP recommendations still appear when the player hero is unknown',async()=>{
 const h=harness();try{await flush();h.options.infer=p=>({...h.result(p),observation:{...h.result(p).observation,player_hero:null}});
 await h.w.gameplanMonitor.start();await flush();assert.ok(h.requests.some(r=>r.url==='/api/bp/plan'));assert.match(h.get('quick-text').textContent,/选人阶段/);
 }finally{h.close();}
});

test('pending hero plan cannot overwrite newer unknown-hero lane advice',async()=>{
 const h=harness(),pending=deferred();try{
  await flush();const original=h.w.fetch;
  h.w.fetch=async(url,req)=>url==='/api/loading/plan'?{ok:true,json:()=>pending.promise}:original(url,req);
  h.options.infer=p=>({...h.result(p),observation:{...h.result(p).observation,phase:'in_game',player_hero:'后羿'}});
  await h.w.gameplanMonitor.start();await flush();assert.equal(h.get('current-hero').textContent,'后羿');
  h.get('monitor-stop').click();
  h.options.infer=p=>({...h.result(p),lane_context:{lane:'发育路',evidence:'发育路',advice:'保血补刀'},observation:{...h.result(p).observation,phase:'in_game',player_hero:null}});
  await h.w.gameplanMonitor.start();pending.resolve({personal:{hero:'后羿',text:'旧英雄建议'}});await flush();await h.next();await flush();
  assert.match(h.get('quick-text').textContent,/发育路/);assert.doesNotMatch(h.get('quick-text').textContent,/旧英雄建议/);
 }finally{h.close();}
});


test('retry button rereads paused frame and replaces unknown lane with recognized lane',async()=>{
 const h=harness();try{
  await flush();h.options.phase='in_game';await h.w.gameplanMonitor.start();await flush();h.get('monitor-pause').click();await flush();
  const before=h.requests.filter(r=>r.url==='/api/vision/observe').length;
  h.options.infer=p=>({...h.result(p),lane_context:{lane:'发育路',evidence:'顶部金色分路横幅：发育路',advice:'保血补刀',from_current_frame:true},observation:{...h.result(p).observation,player_hero:null}});
  h.get('coach-retry').click();await flush();await flush();
  assert.equal(h.requests.filter(r=>r.url==='/api/vision/observe').length,before+1);
  assert.equal(h.get('current-lane').textContent,'发育路');assert.equal(h.get('lane-plan').hidden,false);
  assert.match(h.get('quick-text').textContent,/发育路/);assert.equal(h.get('coach-retry').disabled,false);
  assert.equal(h.get('monitor-pause').disabled,true);assert.equal(h.get('preview-paused').hidden,false);
 }finally{h.close();}
});


test('paused analysis and lane card survive more than three minutes without further requests',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>({...h.result(p),lane_context:{lane:'发育路',evidence:'顶部金色分路横幅：发育路',advice:'保血补刀'},observation:{...h.result(p).observation,phase:'in_game',player_hero:null}});
  await h.w.gameplanMonitor.start();await flush();h.get('monitor-pause').click();await flush();
  const summary=h.get('coach-summary').textContent,quick=h.get('quick-text').textContent,count=h.requests.length;
  const later=h.w.Date.now()+240000;h.w.Date.now=()=>later;
  for(let i=0;i<2;i++)for(const tick of h.intervals)tick();await flush();
  assert.equal(h.get('coach-summary').textContent,summary);assert.equal(h.get('quick-text').textContent,quick);
  assert.equal(h.get('lane-plan').hidden,false);assert.equal(h.get('current-lane').textContent,'发育路');
  assert.match(h.get('coach-meta').textContent,/历史参考/);assert.equal(h.requests.length,count);
  h.get('monitor-new').click();assert.doesNotMatch(h.get('coach-summary').textContent,/注意配合/);assert.equal(h.get('lane-plan').hidden,true);
 }finally{h.close();}
});

test('monitor retains completed paragraph until changed-frame analysis completes',async()=>{
 const h=harness(),pending=deferred();try{
  await flush();await h.w.gameplanMonitor.start();await flush();const summary=h.get('coach-summary').textContent;
  const original=h.w.fetch;h.w.fetch=async(url,req)=>url==='/api/coach/analysis'?{ok:true,json:()=>pending.promise}:original(url,req);
  h.options.hero='孙尚香';const next=h.next();await flush();await flush();
  assert.equal(h.get('coach-summary').textContent,summary);assert.match(h.get('coach-meta').textContent,/历史参考/);
  pending.resolve({status:'ok',source:'local_rules',summary:'新分析已完成',scope:'bp',expires_at:Date.now()/1000+120});await next;await flush();
  assert.equal(h.get('coach-summary').textContent,'新分析已完成');assert.doesNotMatch(h.get('coach-meta').textContent,/历史参考/);
 }finally{h.close();}
});

test('waiting response keeps the completed analysis visible',async()=>{
 const h=harness();try{
  await flush();await h.w.gameplanMonitor.start();await flush();const summary=h.get('coach-summary').textContent;
  const original=h.w.fetch;h.w.fetch=async(url,req)=>url==='/api/coach/analysis'?{ok:true,json:async()=>({status:'waiting',source:'none',summary:'等待新画面'})}:original(url,req);
  h.options.hero='孙尚香';await h.next();await flush();assert.equal(h.get('coach-summary').textContent,summary);
  h.get('monitor-pause').click();assert.equal(h.tasks.size,0);
 }finally{h.close();}
});


test('enemy panel shows five slots and clicking enemies never changes own perspective',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>({...h.result(p),observation:{...h.result(p).observation,enemy_roster:['铠','韩信']}});
  await h.w.gameplanMonitor.start();await flush();
  assert.equal(h.get('enemy-count').textContent,'2 / 5');assert.equal(h.get('enemy-slots').children.length,5);
  assert.equal(h.get('enemy-slots').querySelectorAll('button').length,2);
  assert.match(h.get('enemy-reference-title').textContent,/铠/);assert.equal(h.get('enemy-counters').hidden,true);
  const own=h.w.gameplanLoading.analysisContext();h.get('enemy-slots').querySelector('[data-enemy-index="1"]').click();
  assert.match(h.get('enemy-reference-title').textContent,/韩信/);assert.deepEqual(h.w.gameplanLoading.analysisContext(),own);
 }finally{h.close();}
});

test('hero pool never fills an empty enemy lineup and new match clears previous enemies',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>({...h.result(p),observation:{...h.result(p).observation,enemy_roster:[],note:'英雄池有孙尚香、后羿'}});
  await h.w.gameplanMonitor.start();await flush();assert.equal(h.get('enemy-count').textContent,'0 / 5');assert.equal(h.get('enemy-reference').hidden,true);assert.doesNotMatch(h.get('enemy-slots').textContent,/孙尚香|后羿/);
  h.options.infer=null;await h.next();assert.match(h.get('enemy-slots').textContent,/铠/);
  h.options.infer=p=>({...h.result(p),observation:{...h.result(p).observation,enemy_roster:[]}});await h.next();assert.match(h.get('enemy-roster-status').textContent,/为空或身份待确认/);assert.doesNotMatch(h.get('enemy-slots').textContent,/铠/);
  h.get('monitor-pause').click();assert.equal(h.get('enemy-count').textContent,'0 / 5');
  h.get('monitor-new').click();assert.equal(h.get('enemy-count').textContent,'0 / 5');assert.equal(h.get('enemy-reference').hidden,true);
 }finally{h.close();}
});

test('saved loading slots keep their gaps and unknown teams stay explicit',async()=>{
 const h=harness();try{
  await flush();
  const frame={...h.result({}),observation:{phase:'loading',ally_roster:[],enemy_roster:[]},
   loading_slots:[{slot:1,column:1,hero:'铠',side:'unknown'},{slot:3,column:3,hero:'妲己',side:'unknown'}],side_status:'unknown'};
  await h.w.gameplanEnemies.observe(frame,{side:'neutral'});
  assert.match(h.get('enemy-roster-status').textContent,/已保存 2 \/ 10 个英雄/);
  assert.match(h.get('enemy-roster-status').textContent,/阵营待确认/);
  assert.equal(h.get('enemy-count').textContent,'0 / 5');
  frame.team_context={side:'a'};frame.side_status='confirmed';frame.observation.enemy_roster=['铠','妲己'];
  frame.loading_slots.forEach(slot=>slot.side='enemy_roster');
  await h.w.gameplanEnemies.observe(frame,{side:'a'});
  const rows=h.get('enemy-slots').querySelectorAll('.enemy-slot');
  assert.equal(h.get('enemy-count').textContent,'2 / 5');
  assert.match(h.get('enemy-slots').textContent,/铠/);
  assert.equal(rows[1].querySelector('b').textContent,'待显示');
  assert.equal(rows[2].querySelector('b').textContent,'妲己');
 }finally{h.close();}
});

test('unknown loading orientation does not label either screen-order group as enemy',async()=>{
 const h=harness();try{
  await flush();h.options.phase='loading';await h.w.gameplanMonitor.start();await flush();
  assert.equal(h.get('enemy-count').textContent,'0 / 5');assert.match(h.get('enemy-roster-status').textContent,/阵营待确认/);
 }finally{h.close();}
});


test('BP slot evidence overrides guessed hero lists and preserves actual player positions',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>({...h.result(p),bp_slots:Array.from({length:5},(_,row)=>({side:'right',row,hero:row===2?'瑶':null,status:row===2?'confirmed':'unconfirmed'})),observation:{...h.result(p).observation,enemy_roster:['后羿','黄忠','伽罗','莱西奥','李元芳']}});
  await h.w.gameplanMonitor.start();await flush();
  assert.equal(h.get('enemy-count').textContent,'1 / 5');assert.match(h.get('enemy-slots').children[2].textContent,/敌方 3.*瑶/);
  assert.doesNotMatch(h.get('enemy-slots').textContent,/后羿|黄忠|伽罗|莱西奥|李元芳/);
  h.get('enemy-slots').children[2].click();assert.match(h.get('enemy-reference-title').textContent,/瑶/);
  h.options.infer=p=>({...h.result(p),bp_slots:Array.from({length:5},(_,row)=>({side:'right',row,hero:null,status:'unconfirmed'}))});
  await h.next();assert.equal(h.get('enemy-count').textContent,'0 / 5');assert.equal(h.get('enemy-reference').hidden,true);
 }finally{h.close();}
});


test('capture continues during slow inference and only the latest frame is consumed',async()=>{
 const h=harness(),pending=deferred();try{
  await flush();let count=0,first;
  h.options.capture=()=>({image_base64:'frame-'+(++count),captured_at:Date.now()/1000});
  h.options.infer=p=>{first=p;return pending.promise;};
  await h.w.gameplanMonitor.start();await flush();
  assert.equal(first.image_base64,'frame-1');
  await h.next();assert.ok(count>=5);
  assert.equal(h.requests.filter(r=>r.url==='/api/vision/observe').length,1);
  assert.ok(h.get('frame-image').src.endsWith('frame-'+count));
  h.options.infer=null;pending.resolve(h.result(first));await flush();await h.next();
  assert.equal(h.requests.filter(r=>r.url==='/api/vision/observe').at(-1).payload.image_base64,'frame-'+count);
  h.w.gameplanMonitor.stop();assert.equal(h.tasks.size,0);
 }finally{h.close();}
});

const catalog=JSON.parse(fs.readFileSync(path.join(root,'resources/knowledge/skill_catalog.json'),'utf8'));
function knowledgeRecord(hero){
 const record=catalog.heroes.find(record=>record.hero===hero);
 return {...record,skills:(record?.skills||[]).map(skill=>({...skill,cooldown_s:skill.base_cooldowns_s}))};
}
function skillsFrame(h,p,{hero='铠',phase='in_game',timers=[],records=[knowledgeRecord(hero)]}={}){
 return {...h.result(p),processed_at:Date.now()/1000,hero_knowledge:records,enemy_skill_timers:timers,
  enemy_ultimate_states:timers.length?[{hero,level:4,level_captured_at:p.captured_at,status:'unlocked',unlock_level:4}]:[],
  enemy_summoner_states:timers.some(timer=>timer.skill==='闪现')?[{hero,skill:'闪现',status:'confirmed'}]:[],
  observation:{...h.result(p).observation,phase,enemy_roster:[hero]}};
}

test('casting exit retains the enemy panel and live clock through non-game frames',async()=>{
 const h=harness({query:'?panel=1'});
 try{
  await flush();
  const now=Date.now();h.w.Date.now=()=>now;
  h.options.capture=()=>({image_base64:'data:image/png;base64,AAAA',captured_at:h.w.Date.now()/1000});
  h.options.infer=p=>({...skillsFrame(h,p,{timers:[{hero:'铠',skill:'不灭魔躯',slot:3,captured_at:p.captured_at,remaining_s:40}]}),processed_at:now/1000});
  await h.w.gameplanMonitor.start();await flush();
  assert.equal(h.get('enemy-count').textContent,'1 / 5');
  const slots=h.get('enemy-slots').innerHTML;
  h.options.infer=p=>skillsFrame(h,p,{phase:'not_game',records:[]});
  await h.next();
  assert.equal(h.get('enemy-count').textContent,'1 / 5');
  assert.equal(h.get('enemy-slots').innerHTML,slots);
  assert.match(h.get('enemy-skill-reference').textContent,/不灭魔躯/);
  assert.match(h.get('enemy-roster-status').textContent,/保留/);
  assert.match(h.get('enemy-cooldown-board').textContent,/40 秒/);
  assert.doesNotMatch(h.get('enemy-cooldown-board').textContent,/暂停时/);
  h.w.Date.now=()=>now+10000;
  await h.next();h.intervals.forEach(fn=>fn());
  assert.match(h.get('enemy-cooldown-board').textContent,/30 秒/);
  h.options.infer=p=>skillsFrame(h,p,{phase:'unknown',records:[]});await h.next();
  assert.match(h.get('enemy-cooldown-board').textContent,/30 秒/);
  h.options.infer=p=>({...skillsFrame(h,p),observation:{...h.result(p).observation,phase:'in_game',enemy_roster:[]}});await h.next();
  assert.match(h.get('enemy-cooldown-board').textContent,/铠.*30 秒/);
  h.options.infer=p=>skillsFrame(h,p,{hero:'敖隐'});await h.next();
  assert.match(h.get('enemy-cooldown-board').textContent,/敖隐/);
  assert.match(h.get('enemy-cooldown-board').textContent,/铠/);
  assert.doesNotMatch(h.get('enemy-cooldown-board').textContent,/暂停时/);
  assert.doesNotMatch(h.get('enemy-roster-status').textContent,/保留/);
  h.get('monitor-new').click();assert.equal(h.get('enemy-count').textContent,'0 / 5');
 }finally{h.w.gameplanMonitor.stop();await flush();h.close();}
});

test('same match keeps five enemy positions through partial and reordered readings',async()=>{
 const h=harness({query:'?panel=1'});try{
  await flush();const names=['韩信','金蝉','程咬金','桑启','妲己'];
  const frame=heroes=>({...skillsFrame(h,{captured_at:Date.now()/1000}),
   observation:{phase:'in_game',ally_roster:['后羿'],enemy_roster:heroes}});
  await h.w.gameplanEnemies.observe(frame(names),{side:'a'});
  const slots=h.get('enemy-slots').innerHTML;
  for(const partial of [names.slice(0,4),[...names.slice(0,3),'吕布'],[],names.slice().reverse()]){
   await h.w.gameplanEnemies.observe(frame(partial),{side:'a'});
   assert.equal(h.get('enemy-count').textContent,'5 / 5');
   assert.equal(h.get('enemy-slots').innerHTML,slots);
  }
  const unknown=frame([]);unknown.observation.phase='loading';
  await h.w.gameplanEnemies.observe(unknown,{side:'neutral'});
  assert.equal(h.get('enemy-slots').innerHTML,slots);
  h.get('monitor-new').click();assert.equal(h.get('enemy-count').textContent,'0 / 5');
 }finally{await flush();h.close();}
});

test('expired keyframe ultimate is historical and a fresh cast starts a new countdown',async()=>{
 const h=harness();try{
  await flush();const now=Date.now();h.w.Date.now=()=>now;
  const frame=remaining=>({...skillsFrame(h,{captured_at:now/1000},{timers:[{
   hero:'铠',skill:'不灭魔躯',slot:3,is_ultimate:true,captured_at:now/1000-50,
   remaining_s:remaining,timing_basis:'first_visible_effect'}]}),processed_at:now/1000});
  await h.w.gameplanEnemies.observe(frame(0),{side:'a'});
  assert.match(h.get('enemy-cooldown-board').textContent,/上次大招.*冷却估算结束.*待确认/);
  assert.doesNotMatch(h.get('enemy-cooldown-board').textContent,/大招特效已识别/);
  await h.w.gameplanEnemies.observe(frame(40),{side:'a'});
  assert.match(h.get('enemy-cooldown-board').textContent,/大招特效已识别.*40 秒/);
  h.w.Date.now=()=>now+10000;h.w.gameplanEnemies.tick();
  assert.match(h.get('enemy-cooldown-board').textContent,/30 秒/);
  h.w.gameplanEnemies.hold();const frozen=h.get('enemy-cooldown-board').textContent;
  assert.match(frozen,/暂停时/);
  h.w.Date.now=()=>now+20000;h.w.gameplanEnemies.tick();
  assert.equal(h.get('enemy-cooldown-board').textContent,frozen);
 }finally{h.close();}
});

test('server match boundary clears retained five heroes but complete corrections remain possible',async()=>{
 const h=harness();try{
  await flush();
  const frame=(names,epoch)=>({...skillsFrame(h,{captured_at:Date.now()/1000}),match_epoch:epoch,
   observation:{phase:'in_game',ally_roster:['后羿'],enemy_roster:names}});
  await h.w.gameplanEnemies.observe(frame(['韩信','金蝉','程咬金','桑启','妲己'],'first'),{side:'a'});
  await h.w.gameplanEnemies.observe(frame(['韩信','金蝉','程咬金','桑启','吕布'],'first'),{side:'a'});
  assert.match(h.get('enemy-slots').textContent,/吕布/);
  assert.doesNotMatch(h.get('enemy-slots').textContent,/妲己/);
  const ended=frame([],'ended');ended.observation.phase='result';
  await h.w.gameplanEnemies.observe(ended,{side:'a'});
  assert.equal(h.get('enemy-count').textContent,'5 / 5');
  await h.w.gameplanEnemies.observe(frame(['铠'],'second'),{side:'a'});
  assert.equal(h.get('enemy-count').textContent,'1 / 5');
  assert.doesNotMatch(h.get('enemy-slots').textContent,/韩信|吕布/);
 }finally{await flush();h.close();}
});

test('casting reconnect keeps frozen enemy data until a fresh recognition succeeds',async()=>{
 const h=harness({query:'?panel=1'}),pending=deferred();let payload;
 try{
  await flush();const now=Date.now();h.w.Date.now=()=>now;
  const captureFrame=()=>({image_base64:'data:image/png;base64,AAAA',captured_at:h.w.Date.now()/1000});h.options.capture=captureFrame;
  h.options.infer=p=>skillsFrame(h,p,{timers:[{hero:'铠',skill:'不灭魔躯',slot:3,captured_at:p.captured_at,remaining_s:40}]});
  await h.w.gameplanMonitor.start();await flush();h.get('monitor-auto').checked=true;
  assert.equal(h.get('enemy-count').textContent,'1 / 5');
  h.options.sources=[];
  h.options.capture=()=>{throw Object.assign(new Error('投屏窗口已关闭'),{status:503});};
  const capture=[...h.tasks.entries()].find(([,task])=>task.delay>0&&task.delay<=100);
  assert.ok(capture);h.tasks.delete(capture[0]);await capture[1].fn();await flush();
  const frozen=h.get('enemy-cooldown-board').textContent;
  assert.match(frozen,/铠.*40 秒/,h.get('monitor-status').textContent);
  h.w.Date.now=()=>now+300000;
  const retry=[...h.tasks.entries()].find(([,task])=>task.delay===2000);
  assert.ok(retry);h.tasks.delete(retry[0]);await retry[1].fn();await flush();
  assert.equal(h.get('enemy-cooldown-board').textContent,frozen);
  h.options.sources=[{id:'a'.repeat(16),kind:'window',label:'手机投屏'}];h.options.capture=captureFrame;
  h.options.infer=p=>{payload=p;return pending.promise;};
  const reconnect=[...h.tasks.entries()].find(([,task])=>task.delay===2000);
  assert.ok(reconnect);h.tasks.delete(reconnect[0]);await reconnect[1].fn();await flush();
  assert.equal(h.get('enemy-cooldown-board').textContent,frozen);
  assert.ok(payload);pending.resolve(skillsFrame(h,payload,{hero:'敖隐'}));await flush();
  assert.match(h.get('enemy-cooldown-board').textContent,/敖隐/);
  assert.match(h.get('enemy-cooldown-board').textContent,/铠/);
  assert.doesNotMatch(h.get('enemy-cooldown-board').textContent,/暂停时/);
 }finally{h.w.gameplanMonitor.stop();if(payload)pending.resolve(h.result(payload));await flush();h.close();}
});

test('enemy ultimate cooldown and descriptions appear in BP before any cast is seen',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>skillsFrame(h,p,{phase:'bp'});await h.w.gameplanMonitor.start();await flush();
  const box=h.get('enemy-skill-reference');
  assert.match(box.textContent,/大招 · 不灭魔躯/);assert.match(box.textContent,/50 \/ 45 \/ 40 秒（按技能等级）/);
  assert.match(box.textContent,/召唤魔铠/);assert.match(box.textContent,/回旋之刃/);assert.match(box.textContent,/未确认施放 · 剩余冷却未知/);
  assert.match(h.get('enemy-skill-timers').textContent,/选人或加载阶段/);
  assert.doesNotMatch(box.textContent,/后羿|蔡文姬/);
  const details=box.querySelector('details');details.open=true;await h.next();assert.equal(box.querySelector('details'),details);assert.equal(details.open,true);
 }finally{h.close();}
});

test('new casts update unchanged rosters, only latest cast is shown, and pause freezes the display',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>skillsFrame(h,p);await h.w.gameplanMonitor.start();await flush();
  const timer={hero:'铠',skill:'不灭魔躯',slot:3,captured_at:Date.now()/1000-10,remaining_s:40};
  h.options.infer=p=>skillsFrame(h,p,{timers:[{...timer,captured_at:timer.captured_at-60,remaining_s:0},timer,{...timer,hero:'后羿'}]});
  await h.next();assert.equal(h.get('enemy-skill-timers').querySelectorAll('.enemy-timer').length,1);
  assert.match(h.get('enemy-skill-reference').textContent,/预计剩余 40 秒/);
  h.get('monitor-pause').click();const paused=h.get('enemy-skill-timers').textContent;
  const original=h.w.Date.now;h.w.Date.now=()=>original()+100000;h.w.gameplanEnemies.tick();
  assert.equal(h.get('enemy-skill-timers').textContent,paused);assert.match(paused,/暂停时/);h.w.Date.now=original;
  h.get('monitor-new').click();assert.doesNotMatch(h.get('enemy-skill-reference').textContent,/不灭魔躯/);assert.equal(h.get('enemy-skill-timers').querySelectorAll('.enemy-timer').length,0);
 }finally{h.close();}
});

test('Machao shows a persistent recharge estimate without claiming no charges remain',async()=>{
 const h=harness();try{
  await flush();
  const timer={id:'machao-ult',hero:'马超',skill:'万刃归鞘',slot:3,is_ultimate:true,
   timing_basis:'first_visible_effect',charge_recovery_estimate:true,captured_at:100,
   cooldown_s:15,remaining_s:15,remaining_range_s:[12,15]};
  const frame={...skillsFrame(h,{captured_at:100},{hero:'马超',timers:[timer]}),
   input_kind:'video',video_time_s:20,processed_at:100,enemy_skill_updates:[]};
  await h.w.gameplanEnemies.observe(frame,{side:'a'});
  const board=()=>h.get('enemy-cooldown-board').textContent;
  assert.match(board(),/充能恢复估算剩余 12–15 秒/);
  assert.match(board(),/可能仍有存储次数/);
  h.w.gameplanEnemies.setPlaybackTime(25);
  assert.match(board(),/充能恢复估算剩余 7–10 秒/);
  await h.w.gameplanEnemies.observe({...frame,enemy_skill_updates:[]},{side:'a'});
  assert.match(board(),/充能恢复估算剩余 7–10 秒/);
  h.w.gameplanEnemies.setPlaybackTime(35);
  assert.match(board(),/充能恢复估算结束/);
  assert.doesNotMatch(board(),/冷却估算结束|剩余冷却未知|NaN/);
  h.w.gameplanEnemies.reset();assert.equal(board(),'');
 }finally{h.close();}
});

test('saved left-half region is sent with every desktop capture',async()=>{
 const h=harness({auto:true,savedRegion:'left'});try{await flush();await flush();
  assert.equal(h.get('capture-region').value,'left');
  const captures=h.requests.filter(r=>r.url==='/api/screen/frame');
  assert.ok(captures.length>0);
  assert.deepEqual(captures[0].payload.roi,{x:0,y:0,width:0.5,height:1});
 }finally{h.w.gameplanMonitor.stop();h.close();}
});

for(const [spell,seconds] of [['惩击',30],['终结',60],['狂暴',75],['疾跑',75],['治疗术',120],['干扰',90],['眩晕',90],['净化',120],['弱化',75],['闪现',120],['传送',75]]){
 test(`${spell} counts down only in summoner column with replay persistence and reset`,async()=>{
  const h=harness();try{
   await flush();
   const timer={id:'spell-test',hero:'铠',skill:spell,slot:5,is_ultimate:false,
    timing_basis:'first_visible_effect',captured_at:100,cooldown_s:seconds,
    remaining_s:seconds,remaining_range_s:[seconds,seconds]};
   const frame={...skillsFrame(h,{captured_at:100},{timers:[timer]}),input_kind:'video',
    video_time_s:20,processed_at:100,enemy_skill_updates:['spell-test'],
    enemy_summoner_states:[{hero:'铠',skill:spell,status:'confirmed'}]};
   await h.w.gameplanEnemies.observe(frame,{side:'a'});
   const columns=()=>h.get('enemy-cooldown-board').querySelectorAll('.enemy-cooldown-row span');
   assert.match(columns()[1].textContent,new RegExp(`${spell}特效已识别.*预计剩余 ${seconds} 秒`));
   assert.doesNotMatch(columns()[0].textContent,/预计剩余/);
   assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,new RegExp(`大招（${spell}）`));
   h.w.gameplanEnemies.setPlaybackTime(25);
   assert.match(columns()[1].textContent,new RegExp(`预计剩余 ${seconds-5} 秒`));
   h.w.gameplanEnemies.hold();
   const paused=columns()[1].textContent,originalNow=h.w.Date.now;
   h.w.Date.now=()=>originalNow()+30000;h.w.gameplanEnemies.tick();
   assert.equal(columns()[1].textContent,paused);h.w.Date.now=originalNow;
   await h.w.gameplanEnemies.observe({...frame,enemy_skill_updates:[]},{side:'a'});
   assert.match(columns()[1].textContent,new RegExp(`预计剩余 ${seconds-5} 秒`));
   h.w.gameplanEnemies.setPlaybackTime(20+seconds);
   assert.match(columns()[1].textContent,/冷却估算结束/);
   h.w.gameplanEnemies.reset();assert.equal(h.get('enemy-cooldown-board').textContent,'');
  }finally{h.close();}
 });
}

for(const [hero,skill,seconds,flags,word] of [
 ['露娜','新月突击',25,{special_base_estimate:true},/未计刷新/],
 ['鲁班大师','强力收纳',40,{reference_estimate:true},/官网参考值，版本待核对/],
 ['司马懿','死神降临',35,{reference_estimate:true,special_base_estimate:true},/官网参考值，版本待核对/]]){
 test(`${hero} exposes its estimate caveat while countdown advances`,async()=>{
  const h=harness();try{
   await flush();
   const timer={id:'special',hero,skill,slot:3,is_ultimate:true,...flags,
    timing_basis:'first_visible_effect',captured_at:100,remaining_s:seconds};
   const frame={...skillsFrame(h,{captured_at:100},{hero,timers:[timer]}),input_kind:'video',video_time_s:20,processed_at:100};
   await h.w.gameplanEnemies.observe(frame,{side:'a'});
   assert.match(h.get('enemy-cooldown-board').textContent,word);
   h.w.gameplanEnemies.setPlaybackTime(25);
   assert.match(h.get('enemy-cooldown-board').textContent,new RegExp(`预计剩余 ${seconds-5} 秒`));
  }finally{h.close();}
 });
}

test('Xishi confirmed cooldown renders even when the optional special-mechanic flag is absent',async()=>{
 const h=harness();try{
  await flush();
  const timer={id:'xishi-confirmed',hero:'西施',skill:'心无旁骛',slot:3,is_ultimate:true,
   timing_basis:'first_visible_effect',captured_at:100,cooldown_s:35,remaining_s:35,
   remaining_range_s:[25,35]};
  const frame={...skillsFrame(h,{captured_at:100},{hero:'西施',timers:[timer]}),
   input_kind:'video',video_time_s:20,processed_at:100,enemy_skill_updates:['xishi-confirmed']};
  await h.w.gameplanEnemies.observe(frame,{side:'a'});
  const board=()=>h.get('enemy-cooldown-board').textContent;
  assert.match(board(),/预计剩余 25–35 秒/);
  assert.doesNotMatch(board(),/特殊机制待核对|剩余冷却未知/);
  h.w.gameplanEnemies.setPlaybackTime(41);
  assert.match(board(),/预计剩余 4–14 秒/);
  await h.w.gameplanEnemies.observe({...frame,enemy_skill_updates:[]},{side:'a'});
  assert.match(board(),/预计剩余 4–14 秒/);
  h.w.gameplanEnemies.setPlaybackTime(55);
  assert.match(board(),/冷却估算结束/);
 }finally{h.close();}
});

test('every supported hero backend countdown reaches the right ultimate column with unknown levels',async()=>{
 const python=process.env.PYTHON||path.join(root,'.venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
 const payload=require('node:child_process').execFileSync(python,['-X','utf8','-c',`
import json
from gameplan.skills.auto_skill_monitor import EventTracker
rows=[]
for hero in EventTracker().catalog:
    tracker=EventTracker()
    tracker.ingest([dict(hero=hero,skill='大招',used=True,confidence=.96,event_type='keyframe',frame_index=0,evidence='已确认敌方大招专属特效')],100,enemies=[hero])
    rows.append(tracker.snapshot(100)[0])
print(json.dumps(rows,ensure_ascii=False))
`],{cwd:root,encoding:'utf8'});
 const timers=JSON.parse(payload),h=harness();let verified=0;
 try{
  await flush();
  for(const source of timers){
   h.w.gameplanEnemies.reset();
   const timer={...source};delete timer.special_base_estimate;
   const hero=timer.hero;
   const frame={...skillsFrame(h,{captured_at:100},{hero,timers:[timer]}),input_kind:'video',
    video_time_s:20,processed_at:100,enemy_ultimate_states:[],enemy_skill_updates:[]};
   frame.observation.ally_roster=[];frame.observation.player_hero=null;
   await h.w.gameplanEnemies.observe(frame,{side:'a'});
   const cell=()=>{const element=h.get('enemy-cooldown-board').querySelector('.enemy-cooldown-row span');assert.ok(element,hero);return element.textContent;};
   if(hero==='朵莉亚'){assert.match(cell(),/需确认目标技能冷却后计算/);continue;}
   const high=Math.max(...timer.cooldown_values_s),low=Math.min(...timer.cooldown_values_s);
   const seconds=Math.ceil(low)===Math.ceil(high)?`${Math.ceil(high)}`:`${Math.ceil(low)}–${Math.ceil(high)}`;
   assert.match(cell(),new RegExp(`剩余 ${seconds} 秒`),hero);
   assert.doesNotMatch(cell(),/特殊机制待核对|剩余冷却未知/,hero);
   h.w.gameplanEnemies.setPlaybackTime(22);
   const nextLow=Math.max(0,Math.ceil(low-2)),nextHigh=Math.max(0,Math.ceil(high-2));
   const later=nextLow===nextHigh?`${nextHigh}`:`${nextLow}–${nextHigh}`;
   assert.match(cell(),nextHigh===0?/冷却估算结束|充能恢复估算结束/:new RegExp(`剩余 ${later} 秒`),hero);
   verified++;
  }
  assert.equal(verified,catalog.heroes.length-1);
 }finally{h.close();}
});

test('Yao base estimate persists in the right ultimate column until replay countdown ends',async()=>{
 const h=harness();try{
  await flush();
  const timer={id:'yao-ult',hero:'瑶',skill:'独立兮山之上',slot:3,is_ultimate:true,
   timing_basis:'first_visible_effect',special_base_estimate:true,captured_at:100,
   cooldown_s:15,remaining_s:15,remaining_range_s:[15,15]};
  const frame={...skillsFrame(h,{captured_at:100},{hero:'瑶',timers:[timer]}),
   input_kind:'video',video_time_s:20,processed_at:100,enemy_skill_updates:[]};
  await h.w.gameplanEnemies.observe(frame,{side:'a'});
  const board=()=>h.get('enemy-cooldown-board').textContent;
  assert.match(board(),/预计剩余 15 秒.*基础估算/);
  h.w.gameplanEnemies.setPlaybackTime(25);
  assert.match(board(),/预计剩余 10 秒/);
  await h.w.gameplanEnemies.observe({...frame,enemy_skill_updates:[]},{side:'a'});
  assert.match(board(),/预计剩余 10 秒/);
  h.w.gameplanEnemies.setPlaybackTime(35);
  assert.match(board(),/冷却估算结束 · 能否释放待确认/);
  assert.doesNotMatch(board(),/剩余冷却未知|NaN/);
  h.w.gameplanEnemies.reset();assert.equal(board(),'');
 }finally{h.close();}
});

test('special cooldown mechanics and missing cooldown readings stay unknown',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>skillsFrame(h,p,{timers:[{hero:'铠',skill:'回旋之刃',slot:1,captured_at:Date.now()/1000,remaining_s:10},{hero:'铠',skill:'不灭魔躯',slot:3,captured_at:Date.now()/1000,remaining_s:null}]});
  await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('enemy-skill-timers').textContent,/特殊机制待核对，剩余冷却未知/);
  assert.doesNotMatch(h.get('enemy-skill-timers').textContent,/预计可用|NaN|预计剩余/);
 }finally{h.close();}
});

test('unknown loading side hides knowledge, missing data is explicit, and four-active-skill heroes show the correct ultimate',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>skillsFrame(h,p,{phase:'loading'});await h.w.gameplanMonitor.start();await flush();
  assert.doesNotMatch(h.get('enemy-skill-reference').textContent,/不灭魔躯/);
  h.options.infer=p=>({...skillsFrame(h,p,{hero:'卢雅那',records:[{hero:'卢雅那',skills:[]}]}),match_epoch:'match-luoyana'});await h.next();assert.match(h.get('enemy-skill-reference').textContent,/卢雅那.*大招资料待补充/s);
  h.options.infer=p=>({...skillsFrame(h,p,{hero:'敖隐'}),match_epoch:'match-aoyin'});await h.next();assert.match(h.get('enemy-skill-reference').querySelector('.ultimate-skill').textContent,/大招 · 穷乎玄间/);
  assert.doesNotMatch(h.get('enemy-skill-reference').textContent,/卢雅那/);
  h.options.infer=p=>skillsFrame(h,p,{phase:'not_game'});await h.next();assert.match(h.get('enemy-skill-reference').textContent,/穷乎玄间/);
 }finally{h.close();}
});

test('Erin can be selected when self identity is unknown and counter picks never enter the main advice',async()=>{
 const h=harness();try{
  await flush();h.options.hero='艾琳';h.options.infer=p=>({...h.result(p),observation:{...h.result(p).observation,player_hero:null,enemy_roster:['吕布']}});
  const original=h.w.fetch;
  h.w.fetch=async(url,req)=>url==='/api/bp/plan'?{ok:true,json:async()=>({items:[{hero:'吕布',advice:'留意附魔，保持安全距离',counters:[{hero:'公孙离',reason:'位移拉开距离'}]}]})}:original(url,req);
  await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('enemy-counters').textContent,/选人候选.*公孙离/);
  assert.doesNotMatch(h.get('quick-text').textContent,/公孙离/);
  h.get('monitor-hero').value='艾琳';h.get('monitor-hero').dispatchEvent(new h.w.Event('change'));await flush();await flush();
  assert.equal(h.requests.filter(r=>r.url==='/api/coach/analysis').at(-1).payload.player,'艾琳');
  assert.match(h.get('quick-text').textContent,/建议对象：艾琳/);assert.doesNotMatch(h.get('quick-text').textContent,/公孙离/);
  assert.match(h.get('coach-meta').textContent,/建议对象：艾琳/);
  assert.equal(h.get('enemy-counters').hidden,true);assert.equal(h.get('enemy-counters').textContent,'');
  await h.next();assert.equal(h.w.gameplanLoading.analysisContext().player,'艾琳');
 }finally{h.close();}
});

test('fourth-button ultimates display and receive named cast timers without replacing the third skill',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>skillsFrame(h,p,{hero:'大乔',timers:[{hero:'大乔',skill:'漩涡之门',slot:3,captured_at:Date.now()/1000,remaining_s:70}]});
  await h.w.gameplanMonitor.start();await flush();
  const box=h.get('enemy-skill-reference');
  assert.match(box.querySelector('.ultimate-skill').textContent,/大招 · 漩涡之门/);
  assert.match(box.querySelector('.ultimate-skill').textContent,/预计剩余 70 秒/);
  assert.match(box.querySelector('details').textContent,/3 技能 · 绝断之桥/);
  assert.doesNotMatch(box.querySelector('details').textContent,/预计剩余 70 秒/);
 }finally{h.close();}
});

test('conditional and conflicting official cooldowns remain explicit in reference and timer views',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>skillsFrame(h,p,{hero:'朵莉亚',timers:[{hero:'朵莉亚',skill:'天籁',slot:3,remaining_s:10}]});
  await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('enemy-skill-reference').textContent,/动态冷却：10\/9\/8\(\+120%目标技能当前冷却\)/);
  assert.match(h.get('enemy-skill-timers').textContent,/需确认目标技能冷却后计算/);
  assert.doesNotMatch(h.get('enemy-skill-reference').textContent,/预计剩余 10 秒/);
  h.options.infer=p=>skillsFrame(h,p,{hero:'鲁班大师',timers:[{hero:'鲁班大师',skill:'强力收纳',slot:3,remaining_s:12}]});await h.next();
  assert.match(h.get('enemy-skill-reference').textContent,/官网冷却字段待核对/);
  assert.doesNotMatch(h.get('enemy-skill-timers').textContent,/预计剩余|预计可用/);
 }finally{h.close();}
});

test('hero-only mode keeps reference data, escaped descriptions, and BP uses confirmed slots',async()=>{
 const h=harness();try{
  await flush();const record=knowledgeRecord('铠');record.skills[3].description='<img src=x onerror=alert(1)> 测试';
  h.options.infer=p=>({...skillsFrame(h,p,{records:[record]}),focus:'heroes'});await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('enemy-skill-reference').textContent,/<img src=x/);assert.equal(h.get('enemy-skill-reference').querySelector('img'),null);
  assert.match(h.get('enemy-skill-timers').textContent,/英雄优先模式只展示技能资料/);
  h.options.infer=p=>({...skillsFrame(h,p,{phase:'bp'}),bp_slots:[{side:'right',row:2,status:'confirmed',hero:'瑶'}],hero_knowledge:[knowledgeRecord('瑶'),record]});
  await h.next();assert.match(h.get('enemy-skill-reference').textContent,/独立兮山之上/);assert.doesNotMatch(h.get('enemy-skill-reference').textContent,/不灭魔躯/);
 }finally{h.close();}
});

test('expired estimates are tentative and missing timer numbers never render NaN',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>skillsFrame(h,p,{timers:[{hero:'铠',skill:'不灭魔躯',slot:3,captured_at:Date.now()/1000-70,remaining_s:0},{hero:'铠',skill:'闪现',slot:5,captured_at:Date.now()/1000}]});
  await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('enemy-skill-timers').textContent,/冷却估算结束 · 能否释放待确认/);assert.match(h.get('enemy-skill-timers').textContent,/剩余冷却未知/);
  assert.doesNotMatch(h.get('enemy-skill-timers').textContent,/NaN|已确认可用/);
 }finally{h.close();}
});


test('confirmed simultaneous casts show hero-specific subtitles and retain timers after subtitles expire',async()=>{
 const h=harness();const original=h.w.Date.now;let now=Date.now();h.w.Date.now=()=>now;
 try{
  await flush();h.options.capture=()=>({image_base64:'test',captured_at:now/1000});const t=now/1000;
  const timers=[{id:'ult1',hero:'铠',skill:'不灭魔躯',slot:3,is_ultimate:true,captured_at:t-2,remaining_s:48},
   {id:'flash1',hero:'铠',skill:'闪现',slot:5,is_ultimate:false,captured_at:t-1,remaining_s:119}];
  h.options.infer=p=>({...skillsFrame(h,p,{timers}),processed_at:now/1000,enemy_skill_updates:['ult1','flash1'],skill_scan:{frames:5,latency_s:3.2,over_target:false}});
  await h.w.gameplanMonitor.start();await flush();
  assert.equal(h.get('observe-focus').value,'skills');
  assert.match(h.get('enemy-skill-subtitles').textContent,/敌方铠已用大招（不灭魔躯）/);
  assert.match(h.get('enemy-skill-subtitles').textContent,/敌方铠已用闪现/);
  assert.equal(h.get('enemy-skill-subtitles').querySelectorAll('p').length,2);
  assert.match(h.get('enemy-cooldown-board').textContent,/预计剩余 48 秒.*预计剩余 119 秒/);
  assert.match(h.get('enemy-scan-status').textContent,/3.2 秒/);
  now+=9000;h.intervals.forEach(fn=>fn());
  assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,/已用/);
  assert.match(h.get('enemy-cooldown-board').textContent,/预计剩余 39 秒/);
  h.options.infer=p=>({...skillsFrame(h,p,{timers:timers.map(t=>({...t,remaining_s:t.remaining_s-9}))}),enemy_skill_updates:[]});
  await h.next();
  assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,/已用/);
  h.get('monitor-new').click();await flush();
  assert.equal(h.get('enemy-cooldown-board').textContent,'');
  assert.ok(h.requests.some(r=>r.url==='/api/monitor/match/reset'));
 }finally{h.w.Date.now=original;h.close();}
});

test('simultaneous heroes share one complete notification and keep separate timers',async()=>{
 const h=harness({query:'?panel=1'});try{
  await flush();const notices=[];
  h.w.Notification=class{static permission='granted';constructor(title,options){notices.push(options.body);}};
  const names=['铠','韩信','金蝉','程咬金','桑启'],now=Date.now()/1000;
  const records=names.map(knowledgeRecord);
  const timers=records.flatMap((r,i)=>[
   {id:'multi-ult-'+i,hero:r.hero,skill:r.skills.find(s=>s.is_ultimate).name,is_ultimate:true,remaining_s:40,captured_at:now,timing_basis:'first_visible_effect'},
   {id:'multi-flash-'+i,hero:r.hero,skill:'闪现',slot:5,is_ultimate:false,remaining_s:120,captured_at:now,timing_basis:'first_visible_effect'}]);
  const frame={...skillsFrame(h,{captured_at:now},{timers,records}),
   observation:{phase:'in_game',ally_roster:[],enemy_roster:names},enemy_skill_updates:timers.map(t=>t.id),
   skill_scan:{frames:1,reviewed_heroes:names,pending_heroes:[],detector_status:'observed'}};
  await h.w.gameplanEnemies.observe(frame,{side:'a'});
  assert.equal(h.get('enemy-skill-subtitles').querySelectorAll('p').length,10);
  assert.equal(notices.length,1);
  assert.equal(h.get('enemy-burst-summary').hidden,false);
  assert.match(h.get('enemy-burst-summary').textContent,/最近识别到 5 名敌人/);
  for(const name of names){
   assert.ok(notices[0].includes(name));
   assert.ok(h.get('enemy-burst-summary').textContent.includes(name));
   const row=[...h.get('enemy-cooldown-board').children].find(r=>r.querySelector('b').textContent===name);
   assert.match(row.textContent,/40 秒.*120 秒/);
  }
  await h.w.gameplanEnemies.observe(frame,{side:'a'});
  assert.equal(notices.length,1);
  await h.w.gameplanEnemies.observe({...frame,enemy_skill_updates:[],
   skill_scan:{frames:1,reviewed_heroes:names.slice(0,2),pending_heroes:names.slice(2),detector_status:'partial'}},{side:'a'});
  assert.match(h.get('enemy-scan-status').textContent,/已检查 2\/5 名敌人/);
  assert.match(h.get('enemy-scan-status').textContent,/金蝉、程咬金、桑启尚未检查完整/);
  assert.equal(notices.length,1);
  const wall=Date.now();h.w.Date.now=()=>wall+9000;h.w.gameplanEnemies.tick();
  assert.equal(h.get('enemy-burst-summary').hidden,true);
  assert.equal(h.get('enemy-cooldown-board').children.length,5);
 }finally{await flush();h.close();}
});

test('unknown skills stay tentative, rejected candidates are not subtitles and delay is visible',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>({...skillsFrame(h,p),enemy_skill_updates:[],skill_scan:{frames:8,latency_s:6.4,over_target:true},
   observation:{...skillsFrame(h,p).observation,enemy_skill_events:[{hero:'铠',skill:'闪现',used:true,confidence:.4}]}});
  await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('enemy-cooldown-board').textContent,/等级待确认.*召唤师技能待识别/);
  assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,/已用/);
  assert.match(h.get('enemy-scan-status').textContent,/超过 5 秒目标/);
 }finally{h.close();}
});

test('live capture covers the whole pending interval during five-second inference',async()=>{
 const h=harness(),pending=deferred();let clock=Date.now()/1000;try{
  await flush();h.options.capture=()=>({image_base64:'frame-'+clock,captured_at:clock+=.25});
  h.options.infer=()=>pending.promise;
  await h.w.gameplanMonitor.start();await flush();
  const first=h.requests.find(r=>r.url==='/api/vision/observe').payload;
  assert.deepEqual(first.recent_frames,[]);
  for(let i=0;i<20;i++){
   const task=[...h.tasks.entries()].find(([,t])=>t.delay<=100);assert.ok(task);
   h.tasks.delete(task[0]);await task[1].fn();await flush();
  }
  pending.resolve(h.result(first));await flush();h.options.infer=null;await h.next();
  const payload=h.requests.filter(r=>r.url==='/api/vision/observe').at(-1).payload;
  assert.equal(payload.recent_frames.length,7);
  const times=[...payload.recent_frames.map(f=>f.captured_at),payload.captured_at];
  assert.ok(times.every((t,i)=>i===0||t>times[i-1]));
  assert.ok(times[0]<=first.captured_at+.25, 'The effect at the start of slow inference was dropped');
  assert.ok(times.at(-1)-times[0]<=7.5);
  assert.ok(times.at(-1)-times[0]>=5);
  assert.ok(times.every((t,i)=>i===0||t-times[i-1]<=1));
 }finally{h.close();}
});

test('video labels playing, pending and completed times separately during slow inference',async()=>{
 const h=harness(),pending=deferred();try{await flush();let payload;
  h.options.infer=p=>{payload=p;return pending.promise;};await h.chooseVideo();
  h.intervals.forEach(fn=>fn());
  assert.match(h.get('capture-status').textContent,/播放 00:10 \/ 02:00.*正在识别 00:00.*落后 10.0 秒/);
  assert.doesNotMatch(h.get('capture-status').textContent,/最近完成/);
  h.videos.at(-1).advance();h.intervals.forEach(fn=>fn());
  assert.match(h.get('capture-status').textContent,/播放 00:20.*正在识别 00:00.*落后 20.0 秒/);
  h.get('monitor-pause').click();const paused=h.get('capture-status').textContent;
  h.intervals.forEach(fn=>fn());assert.equal(h.get('capture-status').textContent,paused);
  pending.resolve(h.result(payload));await flush();
  assert.equal(h.get('capture-status').textContent,paused);
 }finally{h.close();}
});

test('entering a match does not claim ultimate availability and level four alone never emits a cast',async()=>{
 const h=harness();try{
  await flush();let phase='bp',level=null,used=false;
  h.options.infer=p=>({...skillsFrame(h,p,{phase,timers:used?[{id:'level-cast',hero:'铠',skill:'不灭魔躯',slot:3,is_ultimate:true,captured_at:p.captured_at,remaining_s:50}]:[]}),
   enemy_ultimate_states:level===null?[]:[{hero:'铠',level,level_captured_at:p.captured_at,status:level<4?'locked':'unlocked',unlock_level:4}],
   enemy_skill_updates:used?['level-cast']:[]});
  await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('enemy-cooldown-board').textContent,/等待进入对局/);
  assert.doesNotMatch(h.get('enemy-cooldown-board').textContent,/可能还有大招/);
  phase='in_game';await h.next();
  assert.match(h.get('enemy-cooldown-board').textContent,/等级待确认 · 尚未确认释放/);
  assert.doesNotMatch(h.get('enemy-cooldown-board').textContent,/可能使用过|疑似/);
  used=true;await h.next();
  assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,/已用大招/);
  for(level of [1,2,3]){
   await h.next();
   assert.match(h.get('enemy-cooldown-board').textContent,new RegExp(`${level} 级 · 大招未解锁`));
   assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,/已用大招/);
   assert.doesNotMatch(h.get('enemy-skill-reference').textContent,/预计剩余/);
  }
  level=4;used=false;await h.next();
  assert.match(h.get('enemy-cooldown-board').textContent,/4 级 · 大招已解锁 · 冷却状态未知/);
  assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,/已用大招/);
  level=11;await h.next();
  assert.match(h.get('enemy-cooldown-board').textContent,/11 级 · 大招已解锁 · 冷却状态未知/);
  assert.match(h.get('enemy-skill-reference').textContent,/11 级 · 大招已解锁/);
  assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,/已用大招/);
  used=true;await h.next();
  assert.match(h.get('enemy-skill-subtitles').textContent,/敌方铠已用大招/);
  assert.match(h.get('enemy-cooldown-board').textContent,/预计剩余 50 秒/);
  assert.doesNotMatch(h.get('enemy-cooldown-board').textContent,/大招已解锁 · 冷却状态未知/);
  level=3;await h.next();
  assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,/已用大招/);
  assert.match(h.get('enemy-cooldown-board').textContent,/大招未解锁/);
  h.get('monitor-new').click();
  assert.equal(h.get('enemy-cooldown-board').textContent,'');
 }finally{h.close();}
});

test('an old level-three sighting becomes unknown instead of permanently locking the enemy ultimate',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>({...skillsFrame(h,p),enemy_ultimate_states:[{hero:'铠',level:3,level_captured_at:p.captured_at-20,status:'locked',unlock_level:4}]});
  await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('enemy-cooldown-board').textContent,/等级待确认/);
  assert.doesNotMatch(h.get('enemy-cooldown-board').textContent,/大招未解锁|可能还有大招/);
 }finally{h.close();}
});

test('old unlocked level is historical, fresh sightings update it and match end is explicit',async()=>{
 const h=harness();try{
  await flush();const now=Date.now()/1000;h.w.Date.now=()=>now*1000;
  const frame={...skillsFrame(h,{captured_at:now}),processed_at:now,
   enemy_ultimate_states:[{hero:'铠',level:7,level_captured_at:now-540,status:'unlocked'}]};
  await h.w.gameplanEnemies.observe(frame,{side:'a'});
  assert.match(h.get('enemy-cooldown-board').textContent,/当前等级待更新.*上次.*7 级/);
  assert.doesNotMatch(h.get('enemy-cooldown-board').textContent,/7 级 · 大招已解锁/);
  frame.enemy_ultimate_states=[{hero:'铠',level:14,level_captured_at:now,status:'unlocked'}];
  await h.w.gameplanEnemies.observe(frame,{side:'a'});
  assert.match(h.get('enemy-cooldown-board').textContent,/14 级 · 大招已解锁/);
  await h.w.gameplanEnemies.observe({...frame,observation:{...frame.observation,phase:'result'}},{side:'a'});
  assert.match(h.get('enemy-cooldown-board').textContent,/本局结束.*最后识别.*14 级/);
  assert.doesNotMatch(h.get('enemy-cooldown-board').textContent,/可能有大招|暂停时/);
 }finally{h.close();}
});

for(const mode of ['normal','retry','expired','reset','full'])test(`live capture retains a brief scoreboard during a long skill inference: ${mode}`,async()=>{
 const h=harness(),pending=deferred();let first,stamp=Date.now()/1000;
 try{
  await flush();h.options.phase='in_game';h.options.capture=()=>({image_base64:'game',captured_at:stamp});
  if(mode==='full')h.get('observe-focus').value='full';
  h.options.infer=p=>{first=p;return pending.promise;};
  await h.w.gameplanMonitor.start();await flush();
  const capture=async()=>{
   const task=[...h.tasks.entries()].find(([,t])=>t.delay<=100);
   assert.ok(task);h.tasks.delete(task[0]);await task[1].fn();await flush();
  };
  stamp+=1;h.options.capture=()=>({image_base64:'brief-panel',captured_at:stamp,panel_candidate:true});await capture();
  stamp+=mode==='expired'?16:10;h.options.capture=()=>({image_base64:'new-game',captured_at:stamp,panel_candidate:false});await capture();
  h.options.infer=null;pending.resolve(h.result(first));await flush();
  if(mode==='reset'){h.w.gameplanMonitor.stop();await h.w.gameplanMonitor.start();await flush();}
  else {if(mode==='retry')h.options.modelBusy=1;await h.next();}
  if(mode==='retry')await h.next();
  const latest=h.requests.filter(r=>r.url==='/api/vision/observe').at(-1).payload;
  assert.equal(latest.image_base64,'new-game');
  assert.equal(latest.recent_frames.some(f=>f.image_base64==='brief-panel'),!['expired','reset','full'].includes(mode));
  if(['normal','retry'].includes(mode))assert.equal(latest.recent_frames.find(f=>f.image_base64==='brief-panel').panel_candidate,true);
  assert.ok(latest.recent_frames.length<=7);
 }finally{h.w.gameplanMonitor.stop();if(first)pending.resolve(h.result(first));await flush();h.close();}
});

test('summoner column shows actual mixed equipment and never treats smite or unknown as flash',async()=>{
 const h=harness();try{
  await flush();const heroes=['铠','孙策','瑶','吕布','嬴政'];
  h.options.infer=p=>({...skillsFrame(h,p,{records:heroes.map(knowledgeRecord),timers:[
   {id:'wrong-flash',hero:'孙策',skill:'闪现',slot:5,remaining_s:120,captured_at:p.captured_at},
   {id:'real-flash',hero:'瑶',skill:'闪现',slot:5,remaining_s:118,captured_at:p.captured_at}]}),
   observation:{...skillsFrame(h,p).observation,enemy_roster:heroes},
   enemy_summoner_states:[{hero:'铠',skill:null,status:'unknown'},{hero:'孙策',skill:'惩击',status:'confirmed'},
    {hero:'瑶',skill:'闪现',status:'confirmed'},{hero:'吕布',skill:'净化',status:'confirmed'},
    {hero:'嬴政',skill:'疾跑',status:'confirmed'}],enemy_skill_updates:['wrong-flash','real-flash']});
  await h.w.gameplanMonitor.start();await flush();
  const rows=h.get('enemy-cooldown-board').children;
  assert.match(rows[0].textContent,/召唤师技能待识别/);assert.doesNotMatch(rows[0].textContent,/闪现/);
  assert.match(rows[1].textContent,/惩击 · 已确认携带/);assert.doesNotMatch(rows[1].textContent,/闪现|120/);
  assert.match(rows[2].textContent,/闪现 · 预计剩余 118 秒/);
  assert.match(rows[3].textContent,/净化/);assert.match(rows[4].textContent,/疾跑/);
  assert.match(h.get('enemy-skill-subtitles').textContent,/敌方瑶已用闪现/);
  assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,/孙策/);
  assert.equal(h.get('enemy-skill-timers').querySelectorAll('.enemy-timer').length,1);
 }finally{h.close();}
});

test('loading equipment shows confirmation progress then all five actual spells without release alerts',async()=>{
 const h=harness();try{
  await flush();const equipment={李信:'传送',百里玄策:'惩击',高渐离:'狂暴',虞姬:'闪现',瑶:'治疗术'};
  const heroes=Object.keys(equipment);let confirmed=false;
  h.options.infer=p=>({...skillsFrame(h,p,{records:heroes.map(knowledgeRecord),timers:[]}),
   observation:{...skillsFrame(h,p).observation,phase:'loading',enemy_roster:heroes},
   loading_context:{side:'a',source:'local_name_highlight'},
   enemy_summoner_states:heroes.map(hero=>confirmed?{hero,skill:equipment[hero],status:'confirmed'}:
    {hero,skill:null,status:'confirming',confirmation_count:2})});
  await h.w.gameplanMonitor.start();await flush();
  assert.equal(h.get('enemy-cooldown-board').children.length,5);
  assert.match(h.get('enemy-cooldown-board').textContent,/召唤师图标核对中（2\/3）/);
  assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,/已用/);
  confirmed=true;await h.next();
  [...h.get('enemy-cooldown-board').children].forEach((row,i)=>{
   assert.ok(row.textContent.includes(equipment[heroes[i]]+' · 已确认携带'));
   assert.match(row.textContent,/等待进入对局/);
  });
  assert.doesNotMatch(h.get('enemy-cooldown-board').textContent,/待识别|核对中/);
  assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,/已用/);
 }finally{h.close();}
});

test('countdown ticks never replace or repeat the release live-region announcement',async()=>{
 const h=harness();let now=Date.now();h.w.Date.now=()=>now;
 try{
  await flush();h.options.capture=()=>({image_base64:'fixture',captured_at:now/1000});
  h.options.infer=p=>({...skillsFrame(h,p,{timers:[{id:'once',hero:'铠',skill:'不灭魔躯',slot:3,captured_at:p.captured_at,remaining_s:50}]}),processed_at:now/1000,enemy_skill_updates:['once']});
  await h.w.gameplanMonitor.start();await flush();
  const node=h.get('enemy-skill-subtitles').firstElementChild,text=node.textContent;
  now+=2000;h.intervals.forEach(fn=>fn());
  assert.equal(h.get('enemy-skill-subtitles').firstElementChild,node);
  assert.equal(node.textContent,text);
  assert.match(h.get('enemy-cooldown-board').textContent,/预计剩余 48 秒/);
  await h.next();
  assert.equal(h.get('enemy-skill-subtitles').firstElementChild,node);
 }finally{h.close();}
});

test('a snapshot of an existing timer without a new-event id never announces a new release',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>skillsFrame(h,p,{timers:[{id:'old',hero:'铠',skill:'不灭魔躯',slot:3,captured_at:p.captured_at-15,remaining_s:35}]});
  await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('enemy-cooldown-board').textContent,/预计剩余 35 秒/);
  assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,/已用大招/);
 }finally{h.close();}
});


test('manual summoner correction is explicit, labeled and does not announce a cast',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>skillsFrame(h,p);await h.w.gameplanMonitor.start();await flush();
  const original=h.w.fetch;const submitted=[];
  h.w.fetch=async(url,req)=>{
   if(url!=='/api/monitor/summoner/correct')return original(url,req);
   const p=JSON.parse(req.body);submitted.push(p);
   return {ok:true,json:async()=>({enemy_summoner_states:[{hero:p.hero,skill:p.skill,status:p.skill?'confirmed':'unknown',...(p.skill?{source:'manual'}:{})}],enemy_skill_timers:[]})};
  };
  h.get('enemy-summoner-save').click();await flush();assert.equal(submitted.length,0);
  h.get('enemy-summoner-correction').value='惩击';h.get('enemy-summoner-save').click();await flush();
  assert.equal(submitted[0].hero,'铠');assert.equal(submitted[0].skill,'惩击');
  assert.match(h.get('enemy-cooldown-board').textContent,/惩击 · 你已核对携带/);
  assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,/已用/);
  h.get('enemy-summoner-clear').click();await flush();assert.equal(submitted[1].skill,null);
  assert.match(h.get('enemy-cooldown-board').textContent,/召唤师技能待识别/);
 }finally{h.close();}
});

test('ongoing skill observation is tentative and never starts a timer or release subtitle',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>({...skillsFrame(h,p),enemy_ultimate_states:[{hero:'铠',level:4,status:'unlocked',level_captured_at:p.captured_at}],enemy_skill_activity:[{hero:'铠',skill:'不灭魔躯',status:'possible_ongoing'}]});
  await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('enemy-cooldown-board').textContent,/采样时疑似使用大招 · 起手时间待确认/);
  assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,/已用/);
  assert.equal(h.get('enemy-skill-timers').querySelectorAll('.enemy-timer').length,0);
 }finally{h.close();}
});

test('a late correction response cannot restore previous-match equipment',async()=>{
 const h=harness(),pending=deferred();try{
  await flush();h.options.infer=p=>skillsFrame(h,p);await h.w.gameplanMonitor.start();await flush();
  const original=h.w.fetch;let corrections=0;
  h.w.fetch=async(url,req)=>{
   if(url!=='/api/monitor/summoner/correct')return original(url,req);
   corrections++;return pending.promise;
  };
  h.get('enemy-summoner-correction').value='惩击';h.get('enemy-summoner-save').click();
  h.get('enemy-summoner-clear').click();await flush();assert.equal(corrections,1);
  h.get('monitor-new').click();await flush();
  pending.resolve({ok:true,json:async()=>({enemy_summoner_states:[{hero:'铠',skill:'惩击',status:'confirmed',source:'manual'}],enemy_skill_timers:[]})});
  await flush();assert.doesNotMatch(h.get('enemy-cooldown-board').textContent,/惩击/);
  assert.equal(h.get('enemy-correction-status').textContent,'');
 }finally{h.close();}
});

test('partial skill scans expose incomplete coverage instead of suggesting every skill was checked',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>({...skillsFrame(h,p),skill_scan:{frames:2,latency_s:22,over_target:true,detector_status:'partial',checks_total:6,checks_attempted:2,checks_completed:1}});
  await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('enemy-scan-status').textContent,/本轮未检查完/);
  assert.match(h.get('enemy-scan-status').textContent,/完成 1 \/ 6 项/);
  assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,/已用/);
 }finally{h.close();}
});

test('monitor migrates its old default model while preserving a custom model choice',async()=>{
 for(const savedModel of [null,'qwen3-vl:8b','my-custom-vision']){
  const h=harness({savedModel});try{
   await flush();assert.equal(h.get('vision-model').value,savedModel==='my-custom-vision'?savedModel:'qwen3-vl:8b-instruct');
  }finally{h.close();}
 }
});



test('complete replay covers every 200ms sample despite playback ending during slow inference',async()=>{
 const h=harness({videoMode:'complete'}),pending=deferred();try{
  await flush();h.options.videoDuration=3;let first;
  h.options.infer=p=>{first=p;return pending.promise;};await h.chooseVideo();
  assert.equal(h.requests.filter(r=>r.url==='/api/vision/observe').length,1);
  assert.equal(h.videos[0].currentTime,3);
  assert.equal(first.recent_frames.length,7);
  h.options.infer=null;pending.resolve(h.result(first));await flush();await flush();
  const observations=h.requests.filter(r=>r.url==='/api/vision/observe').map(r=>r.payload);
  assert.equal(observations.length,2);
  assert.deepEqual(observations.map(p=>Math.round(p.video_time_s*10)),[14,28]);
  const timeline=observations.flatMap(p=>[...p.recent_frames,p].map(f=>Math.round((f.captured_at-observations[0].recent_frames[0].captured_at)*10)));
  assert.deepEqual([...new Set(timeline)],Array.from({length:15},(_,i)=>i*2));
  assert.equal(timeline.filter(t=>t===14).length,2,'boundary preserves previous frame');
  assert.match(h.get('video-status').textContent,/连续片段分析完成/);
 }finally{h.w.gameplanMonitor.stop();h.close();}
});


test('complete replay retries the same segment and pause resumes its unacknowledged range',async()=>{
 const h=harness({videoMode:'complete'}),pending=deferred();try{
  await flush();h.options.videoDuration=3;h.options.observeFailures=[{status:503,detail:'暂不可用'}];await h.chooseVideo();
  const first=h.requests.filter(r=>r.url==='/api/vision/observe')[0].payload;
  assert.match(h.get('video-status').textContent,/保留本段画面/);
  h.options.infer=()=>pending.promise;await h.next();
  const retry=h.requests.filter(r=>r.url==='/api/vision/observe')[1].payload;
  assert.deepEqual(retry,first);
  h.get('monitor-pause').click();await flush();
  pending.resolve(h.result(first));await flush();
  assert.equal(h.get('frame-count').textContent,'0 帧');
  h.options.infer=null;await h.w.gameplanMonitor.start();await flush();await flush();
  const resumed=h.requests.filter(r=>r.url==='/api/vision/observe')[2].payload;
  assert.equal(resumed.video_time_s,first.video_time_s);
  assert.equal(resumed.captured_at,first.captured_at);
  assert.match(h.get('video-status').textContent,/连续片段分析完成/);
  assert.equal(h.revoked.length,2);
 }finally{h.w.gameplanMonitor.stop();h.close();}
});

test('incomplete detector answers do not advance complete replay or count as completed frames',async()=>{
 const h=harness({videoMode:'complete'});try{
  await flush();h.options.videoDuration=2;
  h.options.infer=p=>({...h.result(p),skill_scan:{detector_status:'incomplete_answer'}});
  await h.chooseVideo();const first=h.requests.find(r=>r.url==='/api/vision/observe').payload;
  assert.equal(h.get('frame-count').textContent,'0 帧');
  h.options.infer=null;await h.next();
  assert.equal(h.requests.filter(r=>r.url==='/api/vision/observe')[1].payload.video_time_s,first.video_time_s);
  assert.match(h.get('video-status').textContent,/连续片段分析完成/);
 }finally{h.w.gameplanMonitor.stop();h.close();}
});


test('suspected ultimate is visible without a confirmed level or timer and upgrades once',async()=>{
 const h=harness();try{
  await flush();let candidate;
  h.options.infer=p=>{
   candidate={id:'suspect-1',hero:'铠',skill:'不灭魔躯',is_ultimate:true,status:'suspected',captured_at:p.captured_at,expires_at:p.captured_at+8,display_expires_at:Date.now()/1000+8};
   return {...skillsFrame(h,p),enemy_skill_suspicions:[candidate]};
  };
  await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('enemy-skill-subtitles').textContent,/敌方铠疑似开大/);
  assert.match(h.get('enemy-cooldown-board').textContent,/疑似开大.*未开始计时/);
  assert.equal(h.get('enemy-skill-timers').querySelectorAll('.enemy-timer').length,0);
  h.options.infer=p=>({...skillsFrame(h,p,{timers:[{id:'confirmed-1',hero:'铠',skill:'不灭魔躯',slot:3,is_ultimate:true,captured_at:p.captured_at-.5,remaining_s:49.5}]}),enemy_skill_updates:['confirmed-1'],enemy_skill_suspicions:[]});
  await h.next();
  assert.match(h.get('enemy-skill-subtitles').textContent,/敌方铠已用大招/);
  assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,/疑似/);
  assert.equal(h.get('enemy-skill-timers').querySelectorAll('.enemy-timer').length,1);
 }finally{h.close();}
});

test('tentative flash is distinct from confirmed use and expires without repeated subtitles',async()=>{
 const h=harness();try{
  await flush();const now=Date.now()/1000;
  h.options.infer=p=>({...skillsFrame(h,p),enemy_skill_suspicions:[{id:'flash-suspect',hero:'铠',skill:'闪现',is_ultimate:false,status:'suspected',captured_at:now,expires_at:now+8,display_expires_at:now+8}]});
  await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('enemy-skill-subtitles').textContent,/疑似使用闪现/);
  assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,/已用闪现/);
  assert.equal(h.get('enemy-skill-timers').querySelectorAll('.enemy-timer').length,0);
  const original=h.w.Date.now;h.w.Date.now=()=>original()+9000;
  h.w.gameplanEnemies.tick();
  assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,/疑似使用闪现/);
  assert.doesNotMatch(h.get('enemy-cooldown-board').textContent,/疑似使用闪现/);
  h.w.Date.now=original;
 }finally{h.close();}
});

test('knowledge rank ranges are displayed with their basis and frozen on replay clock',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>({...skillsFrame(h,p,{timers:[{id:'range',hero:'铠',skill:'不灭魔躯',slot:3,captured_at:p.captured_at-5,remaining_s:45,remaining_range_s:[35,45],cooldown_basis:'用户知识库：大招等级未确认'}]}),video_time_s:300});
  await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('enemy-skill-timers').textContent,/35–45 秒（知识库档位）/);
  assert.match(h.get('enemy-skill-timers').textContent,/用户知识库：大招等级未确认/);
  const original=h.w.Date.now;h.w.Date.now=()=>original()+9000;h.w.gameplanEnemies.tick();
  assert.match(h.get('enemy-skill-timers').textContent,/35–45 秒/);
  h.w.Date.now=original;
 }finally{h.close();}
});

test('following video cooldown decreases with playback while the next inference is pending',async()=>{
 const h=harness(),pending=deferred();let second;
 try{
  await flush();let calls=0;
  h.options.infer=p=>{
   if(++calls>1){second=p;return pending.promise;}
   return {...skillsFrame(h,p,{timers:[{id:'playback-cast',hero:'铠',skill:'不灭魔躯',slot:3,is_ultimate:true,captured_at:p.captured_at,remaining_s:50}]}),video_time_s:p.video_time_s};
  };
  await h.chooseVideo();h.intervals.forEach(fn=>fn());
  assert.match(h.get('enemy-skill-timers').textContent,/预计剩余 30 秒/);
  h.videos[0].advance();h.intervals.forEach(fn=>fn());
  assert.match(h.get('enemy-skill-timers').textContent,/预计剩余 20 秒/);
  h.get('monitor-pause').click();
  const paused=h.get('enemy-skill-timers').textContent;
  h.intervals.forEach(fn=>fn());assert.equal(h.get('enemy-skill-timers').textContent,paused);
 }finally{h.w.gameplanMonitor.stop();if(second)pending.resolve(h.result(second));await flush();h.close();}
});

test('verified teams stay on the same side despite a wrong player guess or cached BP position',async()=>{
 const allies=['艾琳','廉颇','云缨','海月','杨戬'],enemies=['吕布','孙策','嬴政','敖隐','瑶'];
 for(const cached of [false,true]){
  const h=harness({savedBp:cached?{version:1,created_at:Date.now(),side:'right',allies,enemies}:null});
  try{
   await flush();let phase='loading',player=null;
   h.options.infer=p=>({...h.result(p),team_context:{side:'a',source:'local_name_highlight'},
    loading_context:phase==='loading'?{side:'a',source:'local_name_highlight'}:null,
    observation:{...h.result(p).observation,phase,player_hero:player,ally_roster:allies,enemy_roster:enemies}});
   await h.w.gameplanMonitor.start();await flush();
   const check=()=>{
    assert.deepEqual([...h.get('enemy-slots').querySelectorAll('button b')].map(n=>n.textContent),enemies);
    assert.equal(h.w.gameplanLoading.analysisContext().side,'a');
    assert.doesNotMatch(h.get('enemy-cooldown-board').textContent,/艾琳|廉颇|云缨|海月|杨戬/);
   };
   check();phase='in_game';player=cached?'艾琳':'敖隐';await h.next();check();
   assert.notEqual(h.w.gameplanLoading.analysisContext().player,'敖隐');
   h.get('monitor-hero').value='廉颇';h.get('monitor-hero').dispatchEvent(new h.w.Event('change'));check();
   await h.next();assert.equal(h.requests.filter(r=>r.url==='/api/vision/observe').at(-1).payload.perspective_side,'a');
   h.get('monitor-new').click();assert.equal(h.get('enemy-count').textContent,'0 / 5');
  }finally{h.close();}
 }
});

test('legacy observations never infer the opposing team from a guessed enemy player',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>({...h.result(p),observation:{...h.result(p).observation,phase:'in_game',player_hero:'铠'}});
  await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('enemy-slots').textContent,/铠/);
  assert.doesNotMatch(h.get('enemy-slots').textContent,/后羿|蔡文姬/);
  assert.equal(h.w.gameplanLoading.analysisContext().player,null);
 }finally{h.close();}
});

test('single-frame ultimate and flash timers display with unknown level and equipment, tick and clear',async()=>{
 const h=harness();try{
  await flush();
  h.options.infer=p=>({...skillsFrame(h,p,{timers:[
   {id:'keyframe-ultimate',hero:'铠',skill:'不灭魔躯',slot:3,is_ultimate:true,captured_at:p.captured_at,remaining_s:50,timing_basis:'first_visible_effect'},
   {id:'keyframe-flash',hero:'铠',skill:'闪现',slot:5,is_ultimate:false,captured_at:p.captured_at,remaining_s:120,timing_basis:'first_visible_effect'}
  ]}),video_time_s:100,enemy_ultimate_states:[],enemy_summoner_states:[],
   enemy_skill_updates:['keyframe-ultimate','keyframe-flash'],skill_scan:{frames:1,detector_status:'observed',detection_policy:'single_frame_effect'}});
  await h.w.gameplanMonitor.start();await flush();
  assert.equal(h.get('enemy-skill-timers').querySelectorAll('.enemy-timer').length,2);
  assert.match(h.get('enemy-cooldown-board').textContent,/大招特效已识别.*50 秒/);
  assert.match(h.get('enemy-cooldown-board').textContent,/闪现特效已识别.*120 秒/);
  assert.match(h.get('enemy-skill-subtitles').textContent,/按首次识别计时/);
  assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,/已用大招/);
  assert.match(h.get('enemy-scan-status').textContent,/单帧特效触发/);
  h.w.gameplanEnemies.setPlaybackTime(103);
  assert.match(h.get('enemy-cooldown-board').textContent,/47 秒/);
  assert.match(h.get('enemy-cooldown-board').textContent,/117 秒/);
  h.get('monitor-pause').click();const frozen=h.get('enemy-cooldown-board').textContent;
  h.intervals.forEach(fn=>fn());assert.equal(h.get('enemy-cooldown-board').textContent,frozen);
  h.get('monitor-new').click();assert.equal(h.get('enemy-skill-timers').querySelectorAll('.enemy-timer').length,0);
 }finally{h.w.gameplanMonitor.stop();h.close();}
});

test('real Aoyin transformation response displays the fourth-skill cooldown and new-cast alert',async()=>{
 const h=harness();try{
  await flush();
  // Captured real OCR + qwen3-vl:8b-instruct result at video 06:17.2.
  const response=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/combat/untargetable/response.json'),'utf8'));
  h.options.infer=()=>response;
  await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('enemy-skill-subtitles').textContent,/敌方敖隐已用大招/);
  assert.match(h.get('enemy-skill-timers').textContent,/穷乎玄间/);
  assert.match(h.get('enemy-skill-timers').textContent,/70–80 秒/);
  const slot=h.get('enemy-skill-reference').querySelector('[data-skill-hero="敖隐"][data-skill-slot="4"]');
  assert.match(slot.textContent,/70–80 秒/);
  h.w.gameplanEnemies.setPlaybackTime(response.video_time_s+3);
  assert.match(h.get('enemy-cooldown-board').textContent,/67–77 秒/);
 }finally{h.w.gameplanMonitor.stop();h.close();}
});

test('partial replay renders a verified ultimate while retrying its missing flash check',async()=>{
 const h=harness({videoMode:'complete'});try{
  await flush();h.options.videoDuration=2;
  h.options.infer=p=>({...skillsFrame(h,p,{timers:[{id:'partial-ult',hero:'铠',skill:'不灭魔躯',slot:3,is_ultimate:true,captured_at:p.captured_at,remaining_s:50}]}),
   video_time_s:p.video_time_s,enemy_skill_updates:['partial-ult'],skill_scan:{detector_status:'partial',failure_reason:'missing_skill_answer'}});
  await h.chooseVideo();
  assert.equal(h.get('frame-count').textContent,'0 帧');
  assert.match(h.get('enemy-cooldown-board').textContent,/预计剩余 50 秒/);
  assert.match(h.get('enemy-skill-subtitles').textContent,/敌方铠已用大招/);
  assert.match(h.get('enemy-scan-status').textContent,/部分技能未回答/);
  const first=h.requests.find(r=>r.url==='/api/vision/observe').payload;
  h.options.infer=null;await h.next();
  assert.equal(h.requests.filter(r=>r.url==='/api/vision/observe')[1].payload.video_time_s,first.video_time_s);
 }finally{h.w.gameplanMonitor.stop();h.close();}
});

test('ultimate rows explain skipped or timed-out recognition without implying readiness',async()=>{
 const h=harness();try{
  await flush();
  for(const [scan,text] of [
   [{detector_status:'incomplete_answer',failure_reason:'model_timeout'},'本轮大招判断超时'],
   [{detector_status:'no_visible_target'},'本轮未定位可核验目标'],
   [{detector_status:'partial',reviewed_heroes:[]},'本轮大招尚未检查完整']]){
   await h.w.gameplanEnemies.observe({...skillsFrame(h,{captured_at:Date.now()/1000}),skill_scan:scan},{side:'a'});
   assert.ok(h.get('enemy-cooldown-board').textContent.includes(text));
   assert.doesNotMatch(h.get('enemy-cooldown-board').textContent,/可能有大招|预计剩余/);
  }
 }finally{h.close();}
});

test('a suspected ultimate explains its missing evidence and timeout does not create a cooldown',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>({...skillsFrame(h,p),
   enemy_skill_suspicions:[{id:'missing-onset',hero:'铠',skill:'不灭魔躯',is_ultimate:true,status:'suspected',
    captured_at:p.captured_at,expires_at:p.captured_at+8,display_expires_at:Date.now()/1000+8,reason:'ongoing_onset_unknown'}],
   skill_scan:{detector_status:'incomplete_answer',failure_reason:'model_timeout'}});
  await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('enemy-cooldown-board').textContent,/起手时间未知/);
  assert.match(h.get('enemy-scan-status').textContent,/模型回答超时/);
  assert.equal(h.get('enemy-skill-timers').querySelectorAll('.enemy-timer').length,0);
 }finally{h.w.gameplanMonitor.stop();h.close();}
});

test('an earlier confirmed cast cannot hide a newly suspected ultimate or flash',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>{
   const timers=[{id:'old-ult',hero:'铠',skill:'不灭魔躯',slot:3,is_ultimate:true,captured_at:p.captured_at-180,remaining_s:0},
                 {id:'old-flash',hero:'铠',skill:'闪现',slot:5,is_ultimate:false,captured_at:p.captured_at-180,remaining_s:0}];
   return {...skillsFrame(h,p,{timers}),enemy_skill_suspicions:timers.map(t=>({...t,id:'suspected-'+t.id,status:'suspected',captured_at:p.captured_at,expires_at:p.captured_at+8,display_expires_at:p.captured_at+8}))};
  };
  await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('enemy-skill-subtitles').textContent,/疑似开大/);
  assert.match(h.get('enemy-skill-subtitles').textContent,/疑似使用闪现/);
  assert.match(h.get('enemy-cooldown-board').textContent,/疑似再次开大/);
  assert.match(h.get('enemy-cooldown-board').textContent,/疑似再次使用闪现/);
  assert.equal(h.get('enemy-skill-timers').querySelectorAll('.enemy-timer').length,2);
  assert.doesNotMatch(h.get('enemy-skill-timers').textContent,/预计剩余/);
 }finally{h.close();}
});

test('unverified gameplay states explicitly say skill recognition did not run',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>({...skillsFrame(h,p),skill_scan:{frames:4,latency_s:4,detector_status:'waiting_gameplay_evidence'}});
  await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('enemy-scan-status').textContent,/未确认对局画面，未执行技能判断/);
  assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,/已用大招|已用闪现/);
 }finally{h.close();}
});


test('latest video mode follows current playback after slow inference with stable source time',async()=>{
 const h=harness(),pending=deferred();try{
  await flush();assert.equal(h.get('video-mode').value,'latest');
  let first;h.options.infer=p=>{first=p;return pending.promise;};
  await h.chooseVideo();
  for(let i=0;i<5;i++)h.videos[0].advance();
  assert.equal(h.requests.filter(r=>r.url==='/api/vision/observe').length,1);
  const current=h.videos[0].currentTime;
  h.options.infer=null;pending.resolve(h.result(first));await flush();await flush();
  const calls=h.requests.filter(r=>r.url==='/api/vision/observe').map(r=>r.payload);
  assert.equal(calls[0].video_time_s,0);
  assert.equal(calls[1].video_time_s,current);
  assert.equal(calls[1].captured_at-calls[0].captured_at,current);
 }finally{h.w.gameplanMonitor.stop();h.close();}
});


test('60fps replay sends all 60 samples of one second in ordered six-frame batches',async()=>{
 const h=harness({videoMode:'all60'});try{
  await flush();h.options.videoDuration=1;await h.chooseVideo();
  const calls=h.requests.filter(r=>r.url==='/api/vision/observe').map(r=>r.payload);
  assert.equal(calls.length,10);
  assert.ok(calls.every(p=>p.all_frames&&p.recent_frames.length===5));
  const times=calls.flatMap(p=>[...p.recent_frames,p].map(f=>f.captured_at));
  assert.equal(times.length,60);
  assert.equal(new Set(times).size,60);
  times.forEach((t,i)=>assert.ok(Math.abs(t-times[0]-i/60)<1e-6));
  assert.equal(h.videos[0].currentTime,1);
  assert.match(h.get('video-status').textContent,/60 帧.*完成/);
 }finally{h.w.gameplanMonitor.stop();h.close();}
});


test('new video tasks default to every tenth frame',()=>{
 const dom=new JSDOM(fs.readFileSync(path.join(root,'static/monitor.html'),'utf8'));
 try{assert.equal(dom.window.document.getElementById('video-mode').value,'stride10');}finally{dom.window.close();}
});

test('switching from exhaustive analysis abandons backlog and stale responses',async()=>{
 for(const mode of ['latest','stride10']){
 const h=harness({videoMode:'all60'}),pending=deferred(),fresh=deferred();let oldPayload,newPayload;
 try{
  await flush();h.options.infer=p=>{oldPayload=p;return pending.promise;};await h.chooseVideo();
  for(let i=0;i<5;i++)h.videos[0].advance();
  const current=h.videos[0].currentTime,oldRequest=h.requests.find(r=>r.url==='/api/vision/observe');
  h.options.infer=p=>{newPayload=p;return fresh.promise;};
  h.get('video-mode').value=mode;h.get('video-mode').dispatchEvent(new h.w.Event('change'));
  await flush();await flush();
  assert.ok(oldRequest.signal.aborted);
  if(mode==='stride10'){
   const video=h.videos.at(-1);assert.equal(video.currentTime,current);
   for(let i=1;i<=60;i++)video.presentFrame(i,current+(i-1)/60);
   video.currentTime=current+1;await h.next();await flush();
  }
  const expected=mode==='stride10'?current+50/60:current;
  assert.equal(newPayload.video_time_s,expected);
  assert.equal(newPayload.match_id,oldPayload.match_id);
  assert.ok(Math.abs(newPayload.captured_at-oldPayload.captured_at-(expected-oldPayload.video_time_s))<1e-6);
  assert.ok(!newPayload.all_frames);
  pending.resolve({...h.result(oldPayload),observation:{...h.result(oldPayload).observation,note:'stale exhaustive result'}});await flush();
  assert.doesNotMatch(h.get('observation-note').textContent,/stale exhaustive result/);
  assert.match(h.get('capture-status').textContent,mode==='stride10'?/每隔 10 帧/:/跟随播放位置/);
 }finally{
  h.w.gameplanMonitor.stop();if(oldPayload)pending.resolve(h.result(oldPayload));if(newPayload)fresh.resolve(h.result(newPayload));await flush();h.close();
 }
 }
});

test('stride mode skips expired batches while inference is busy',async()=>{
 const h=harness({videoMode:'stride10'}),pending=deferred(),fresh=deferred();let first,second;
 try{
  await flush();h.options.infer=p=>{first=p;return pending.promise;};await h.chooseVideo();
  const video=h.videos[0];
  for(let i=1;i<=60;i++)video.presentFrame(i,(i-1)/60);
  video.currentTime=1;await h.next();await flush();
  for(let i=61;i<=600;i++)video.presentFrame(i,(i-1)/60);
  video.currentTime=10;
  assert.equal(h.requests.filter(r=>r.url==='/api/vision/observe').length,1);
  h.options.infer=p=>{second=p;return fresh.promise;};
  pending.resolve(h.result(first));await flush();await flush();
  assert.ok(second.video_time_s>=9&&second.video_time_s<=10);
  assert.ok([...second.recent_frames,second].every(f=>f.captured_at-first.captured_at>=5));
  assert.ok(second.captured_at-second.recent_frames[0].captured_at<=4);
  assert.match(h.get('capture-status').textContent,/本段未覆盖 5\./);
  assert.equal(h.requests.filter(r=>r.url==='/api/vision/observe').length,2);
 }finally{
  h.w.gameplanMonitor.stop();if(first)pending.resolve(h.result(first));if(second)fresh.resolve(h.result(second));await flush();h.close();
 }
});

test('stride mode retains cast onset during slow inference instead of only the last three frames',async()=>{
 const h=harness({videoMode:'stride10'}),pending=deferred(),fresh=deferred();let first,second;
 try{
  await flush();h.options.infer=p=>{first=p;return pending.promise;};await h.chooseVideo();
  const video=h.videos[0];
  for(let i=1;i<=60;i++)video.presentFrame(i,(i-1)/60);
  video.currentTime=1;await h.next();await flush();
  assert.equal(first.recent_frames.length,5);
  for(let i=61;i<=240;i++)video.presentFrame(i,(i-1)/60);
  video.currentTime=4;
  h.options.infer=p=>{second=p;return fresh.promise;};
  pending.resolve({...h.result(first),elapsed_s:4});await flush();await flush();
  const samples=[...second.recent_frames,second];
  assert.ok(samples.length>=3&&samples.length<=6);
  assert.ok(samples[0].captured_at<=first.captured_at+.2,'Missing preceding cast context while inference was busy');
  assert.ok(second.captured_at-first.captured_at>=2.9);
  assert.ok(samples.every((frame,i)=>i===0||frame.captured_at-samples[i-1].captured_at<=.8));
 }finally{
  h.w.gameplanMonitor.stop();if(first)pending.resolve(h.result(first));if(second)fresh.resolve(h.result(second));await flush();h.close();
 }
});


test('smart mode prioritizes current identity and never requests exhaustive analysis',async()=>{
 const h=harness({videoMode:'smart60'}),pending=deferred();try{
  await flush();let first;h.options.infer=p=>{first=p;return pending.promise;};
  await h.chooseVideo();assert.equal(first.focus,'heroes');assert.equal(first.recent_frames.length,0);
  h.videos[0].advance();h.intervals.forEach(fn=>fn());
  h.options.infer=null;pending.resolve(h.result(first));await flush();await flush();
  const calls=h.requests.filter(r=>r.url==='/api/vision/observe').map(r=>r.payload);
  assert.equal(calls[1].video_time_s,20);
  assert.ok(calls.every(p=>!p.all_frames&&p.recent_frames.length<=7));
  assert.match(h.get('capture-status').textContent,/智能筛选/);
 }finally{h.w.gameplanMonitor.stop();h.close();}
});


test('smart mode switches from identity to bounded skill clips after a confirmed roster',async()=>{
 const h=harness({videoMode:'smart60'}),pending=deferred();try{
  await flush();h.options.infer=p=>({...h.result(p),observation:{...h.result(p).observation,phase:'in_game',ally_roster:['后羿','蔡文姬','李白','王昭君','吕布'],enemy_roster:['铠','虞姬','高渐离','李信','瑶']}});
  await h.chooseVideo();
  const calls=h.requests.filter(r=>r.url==='/api/vision/observe').map(r=>r.payload);
  assert.equal(calls[0].focus,'heroes');assert.equal(calls[1].focus,'skills');
  assert.ok(calls.every(p=>p.recent_frames.length<=7));
 }finally{h.w.gameplanMonitor.stop();h.close();}
});


test('stride mode sends six nonadjacent frames per second at 60fps and three at 30fps',async()=>{
 for(const fps of [60,30]){
  const h=harness({videoMode:'stride10'}),pending=deferred();let payload;
  try{
   await flush();h.options.infer=p=>{payload=p;return pending.promise;};await h.chooseVideo();
   const video=h.videos[0];for(let i=1;i<=fps;i++)video.presentFrame(i,(i-1)/fps);
   video.currentTime=1;await h.next();await flush();
   assert.ok(payload);const frames=[...payload.recent_frames,payload];
   assert.equal(frames.length,fps/10);
   frames.forEach((f,i)=>assert.ok(Math.abs(f.captured_at-frames[0].captured_at-i*10/fps)<1e-6));
   assert.equal(payload.video_time_s,(fps-10)/fps);
   assert.ok(!payload.all_frames);
  }finally{h.w.gameplanMonitor.stop();if(payload)pending.resolve(h.result(payload));await flush();h.close();}
 }
});


test('real Sun Ce replay warning appears in the alert area without a confirmed cooldown',async()=>{
 const h=harness();try{
  await flush();const response=JSON.parse(fs.readFileSync(path.join(root,'tests/fixtures/combat/sunce-warning/response.json'),'utf8'));
  response.enemy_skill_suspicions.forEach(s=>{s.display_expires_at=Date.now()/1000+8;});
  h.options.infer=()=>response;await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('enemy-skill-subtitles').textContent,/孙策疑似开大/);
  assert.match(h.get('enemy-skill-subtitles').textContent,/角色身份仍待核实/);
  assert.equal(h.get('enemy-skill-timers').querySelectorAll('.enemy-timer').length,0);
  assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,/已用大招/);
 }finally{h.w.gameplanMonitor.stop();h.close();}
});


test('a brief scoreboard captured during inference is checked before fresh gameplay',async()=>{
 const h=harness({videoMode:'stride10'}),first=deferred(),second=deferred();let payload;
 try{
  await flush();h.options.infer=p=>{payload=p;return first.promise;};await h.chooseVideo();
  const v=h.videos[0];for(let i=1;i<=60;i++)v.presentFrame(i,(i-1)/60);v.currentTime=1;await h.next();
  assert.ok(payload);
  h.options.probePixels=JSON.parse(fs.readFileSync(path.join(root,'tests/fixtures/combat/stream-scoreboard/probe.json'),'utf8'));
  v.presentFrame(721,12);
  h.options.probePixels=null;v.presentFrame(1201,20);
  h.options.infer=()=>second.promise;first.resolve(h.result(payload));await flush();await flush();
  const calls=h.requests.filter(r=>r.url==='/api/vision/observe').map(r=>r.payload);
  assert.equal(calls[1].video_time_s,12);assert.equal(calls[1].recent_frames.length,0);
 }finally{h.w.gameplanMonitor.stop();if(payload){first.resolve(h.result(payload));second.resolve(h.result(payload));}await flush();h.close();}
});


test('suspected onset shows provisional range without creating a confirmed timer',async()=>{
 const h=harness();try{
  await flush();h.options.infer=p=>({...skillsFrame(h,p),
   enemy_skill_suspicions:[{id:'estimate-candidate',hero:'铠',skill:'不灭魔躯',is_ultimate:true,status:'suspected',captured_at:p.captured_at,expires_at:p.captured_at+8}],
   enemy_skill_estimates:[{id:'estimate-1',hero:'铠',skill:'不灭魔躯',is_ultimate:true,status:'estimated',confirmed:false,captured_at:p.captured_at,expires_at:p.captured_at+70,remaining_range_s:[45,55]}]});
  await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('enemy-cooldown-board').textContent,/估算剩余/);
  assert.match(h.get('enemy-skill-subtitles').textContent,/估算剩余/);
  assert.doesNotMatch(h.get('enemy-skill-subtitles').textContent,/已用大招/);
  assert.equal(h.get('enemy-skill-timers').querySelectorAll('.enemy-timer').length,0);
  h.options.infer=p=>({...skillsFrame(h,p),enemy_skill_estimates:[{id:'estimate-1',hero:'铠',skill:'不灭魔躯',is_ultimate:true,status:'retracted',confirmed:false,expires_at:p.captured_at+15,cooldown_basis:'身份冲突'}]});
  await h.next();assert.match(h.get('enemy-cooldown-board').textContent,/估算已撤销/);
 }finally{h.w.gameplanMonitor.stop();h.close();}
});

test('a new tentative clock stays visible beside an older confirmed keyframe clock',async()=>{
 const h=harness();try{
  await flush();const now=Date.now();h.w.Date.now=()=>now;
  h.options.capture=()=>({image_base64:'data:image/png;base64,AAAA',captured_at:now/1000});
  h.options.infer=p=>({...skillsFrame(h,p,{timers:[{id:'confirmed-old',hero:'铠',skill:'不灭魔躯',slot:3,is_ultimate:true,
    timing_basis:'first_visible_effect',captured_at:p.captured_at-80,remaining_s:0}]}),processed_at:now/1000,
   enemy_skill_estimates:[{id:'new-suspected',hero:'铠',skill:'不灭魔躯',is_ultimate:true,status:'estimated',confirmed:false,
    identity_confirmed:false,timing_basis:'first_visible_candidate',captured_at:p.captured_at,expires_at:p.captured_at+60,remaining_range_s:[40,50]}]});
  await h.w.gameplanMonitor.start();await flush();
  assert.match(h.get('enemy-cooldown-board').textContent,/估算剩余 40～50 秒.*身份待确认/);
  assert.match(h.get('enemy-cooldown-board').textContent,/上次确认/);
  h.w.Date.now=()=>now+5000;h.intervals.forEach(fn=>fn());
  assert.match(h.get('enemy-cooldown-board').textContent,/估算剩余 35～45 秒/);
  assert.equal(h.get('enemy-skill-timers').querySelectorAll('.enemy-timer').length,1);
 }finally{h.w.gameplanMonitor.stop();h.close();}
});
