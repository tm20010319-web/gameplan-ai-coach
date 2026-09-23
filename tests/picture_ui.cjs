const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const {JSDOM}=require('../data/ui-check/node_modules/jsdom');
const root=path.resolve(__dirname,'..');
const flush=()=>new Promise(r=>setImmediate(r));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const signature=n=>Buffer.alloc(96*54*3,n).toString('base64');
function harness({roi=true}={}){
 const dom=new JSDOM(fs.readFileSync(path.join(root,'static/picture.html'),'utf8'),{url:'http://localhost/monitor?monitor=off',runScripts:'outside-only'});
 const w=dom.window,tasks=new Map(),requests=[];let next=0;
 const options={frame:0,infer:null,capture:null,error:null};
 w.localStorage.setItem('gameplan-picture-auto','false');
 if(roi)w.localStorage.setItem('gameplan-picture-region-'+ 'a'.repeat(16),JSON.stringify({width:800,height:600,roi:{x:.25,y:.25,width:.5,height:.5}}));
 w.AbortController=AbortController;w.AbortSignal=AbortSignal;w.setInterval=()=>1;
 w.setTimeout=(fn,delay)=>{tasks.set(++next,{fn,delay});return next;};w.clearTimeout=id=>tasks.delete(id);
 w.HTMLImageElement.prototype.decode=async()=>{};
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;};
 w.document.getElementById('region-stage').setPointerCapture=()=>{};
 w.document.getElementById('region-image').getBoundingClientRect=()=>({left:0,top:0,width:800,height:600});
 const result=p=>({verdict:{phase:'loading',top_heroes:[options.frame===0?'小乔':'后羿'],bottom_heroes:['蔡文姬'],summary:`图片 ${options.frame} ${p.side} 的打法建议`,uncertainty:[],watch_for:['别单独探草'],opportunities:['先清线再支援']},model:'actual-model',qwen_model:'qwen',elapsed_s:2,requires_review:false});
 w.fetch=async(url,req={})=>{
  const p=req.body?JSON.parse(req.body):undefined;requests.push({url,p,signal:req.signal});let data;
  if(url==='/api/screen/sources')data={sources:[{id:'a'.repeat(16),width:800,height:600,label:'屏幕一'},{id:'b'.repeat(16),width:800,height:600,label:'屏幕二'}]};
  else if(url==='/api/picture/status')data={configured:true,requested_model:'vision-alias',message:'仅发送所选图片'};
  else if(url==='/api/picture/revision')data={ok:true};
  else if(url==='/api/screen/frame')data={image_base64:'local-full-screen',captured_at:Date.now()/1000};
  else if(url==='/api/picture/frame')data=options.capture?await options.capture(p):{image_base64:'selected-picture-'+options.frame,signature:signature(options.frame*100),captured_at:Date.now()/1000};
  else if(url==='/api/picture/analysis'){if(options.error)return {ok:false,json:async()=>({detail:options.error})};data=options.infer?await options.infer(p):result(p);}
  else throw new Error(url);
  return {ok:true,json:async()=>data};
 };
 vm.runInContext(fs.readFileSync(path.join(root,'static/picture.js'),'utf8'),dom.getInternalVMContext());
 return {w,tasks,options,requests,result,get:id=>w.document.getElementById(id),
  async next(){const item=[...tasks.entries()].find(([,t])=>t.delay<5000);assert.ok(item,'scheduled work');tasks.delete(item[0]);await item[1].fn();await flush();},close:()=>w.close()};
}
test('only selected ROI is sampled; stable unchanged images invoke the model once',async()=>{
 const h=harness();try{await flush();assert.equal(h.requests.some(r=>r.url==='/api/picture/frame'),false);await h.w.gameplanPicture.start();await flush();
  assert.equal(h.requests.filter(r=>r.url==='/api/picture/analysis').length,0);await h.next();
  assert.equal(h.get('roster-a').textContent,'小乔');for(let i=0;i<5;i++)await h.next();
  assert.equal(h.requests.filter(r=>r.url==='/api/picture/analysis').length,1);
  const captures=h.requests.filter(r=>r.url==='/api/picture/frame');assert.ok(captures.length>=7);assert.deepEqual(captures[0].p.roi,{x:.25,y:.25,width:.5,height:.5});
 }finally{h.close();}
});
test('capture continues during inference, changed image clears advice, only latest result is shown',async()=>{
 const h=harness(),pending=deferred();try{await flush();let old;h.options.infer=p=>{old=h.result(p);return pending.promise;};await h.w.gameplanPicture.start();await flush();await h.next();
  h.options.frame=1;await h.next();await h.next();assert.equal(h.requests.filter(r=>r.url==='/api/picture/analysis').length,1);assert.equal(h.get('roster-a').textContent,'尚未识别');
  h.options.infer=null;pending.resolve(old);await flush();assert.doesNotMatch(h.get('coach-summary').textContent,/图片 0/);
  await h.next();await h.next();assert.equal(h.get('roster-a').textContent,'后羿');assert.match(h.get('coach-summary').textContent,/图片 1/);
 }finally{h.close();}
});
test('pause and stop revoke in-flight output and remove the selected image on stop',async()=>{
 const h=harness(),pending=deferred();try{await flush();let p;h.options.infer=payload=>{p=payload;return pending.promise;};await h.w.gameplanPicture.start();await flush();await h.next();
  h.get('pause').click();assert.equal(h.get('frame-image').hidden,false);assert.equal(h.get('preview-paused').hidden,false);
  pending.resolve(h.result(p));await flush();assert.doesNotMatch(h.get('coach-summary').textContent,/图片 0/);h.get('stop').click();assert.equal(h.get('frame-image').hidden,true);assert.equal(h.get('frame-image').hasAttribute('src'),false);
 }finally{h.close();}
});
test('region selection keeps whole-screen preview local and stores physical-screen proportions',async()=>{
 const h=harness({roi:false});try{await flush();await h.w.gameplanPicture.start();assert.equal(h.get('region-dialog').open,true);
  assert.equal(h.requests.some(r=>r.url==='/api/picture/analysis'),false);const stage=h.get('region-stage');
  stage.dispatchEvent(new h.w.MouseEvent('pointerdown',{button:0,clientX:200,clientY:150}));stage.dispatchEvent(new h.w.MouseEvent('pointermove',{clientX:600,clientY:450}));stage.dispatchEvent(new h.w.MouseEvent('pointerup'));
  h.get('region-save').click();await flush();await h.next();assert.equal(h.requests.find(r=>r.url==='/api/picture/analysis').p.image_base64,'selected-picture-0');
  assert.equal(h.get('region-image').hasAttribute('src'),false);
 }finally{h.close();}
});
test('changing display invalidates a pending capture before any model receives it',async()=>{
 const h=harness(),pending=deferred();try{await flush();h.options.capture=()=>pending.promise;await h.w.gameplanPicture.start();await flush();h.get('screen-source').value='b'.repeat(16);h.get('screen-source').dispatchEvent(new h.w.Event('change'));
  pending.resolve({image_base64:'late-desktop',signature:signature(0),captured_at:Date.now()/1000});await flush();assert.equal(h.requests.filter(r=>r.url==='/api/picture/analysis').length,0);
 }finally{h.close();}
});
test('perspective changes reanalyze the current picture without leaking HTML from a model',async()=>{
 const h=harness();try{await flush();await h.w.gameplanPicture.start();await flush();await h.next();h.get('side').value='b';h.get('side').dispatchEvent(new h.w.Event('change'));await flush();assert.match(h.get('coach-summary').textContent,/ b /);
  h.options.infer=async p=>({...h.result(p),verdict:{...h.result(p).verdict,summary:'<img src=x onerror=alert(1)> 截图分析'}});
  h.get('retry').click();await flush();await flush();assert.equal(h.get('coach-summary').querySelector('img'),null);assert.match(h.get('coach-summary').textContent,/<img/);
 }finally{h.close();}
});
test('analysis failures are visible and do not leave old tactical advice',async()=>{
 const h=harness();try{await flush();await h.w.gameplanPicture.start();await flush();await h.next();h.options.error='接口未完成';h.options.frame=1;await h.next();await h.next();assert.match(h.get('coach-summary').textContent,/接口未完成/);assert.equal(h.get('advice-detail').hidden,true);
 }finally{h.close();}
});
test('small noise is ignored while large image changes trigger detection',async()=>{
 const h=harness();try{await flush();assert.equal(h.w.gameplanPicture.different(signature(0),signature(1)),false);assert.equal(h.w.gameplanPicture.different(signature(0),signature(100)),true);}finally{h.close();}
});
