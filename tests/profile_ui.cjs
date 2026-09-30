const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {JSDOM}=require('../data/ui-check/node_modules/jsdom');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const fact={scope:'global',category:'communication',key:'response_length',value:'<img src=x onerror=alert(1)>',status:'stated',supporting_matches:[]};
const ok=data=>({ok:true,json:async()=>data});
function setup(profile,extra=()=>null){
 const dom=new JSDOM(fs.readFileSync('static/profile.html','utf8'),{url:'http://localhost/profile',runScripts:'outside-only'});
 dom.window.fetch=async(url,opts)=>{
  const value=extra(url,opts);if(value)return value;
  if(url==='/api/me')return ok({id:'own',display_name:'开发玩家',development_user:true});
  if(url==='/api/accounts/players')return ok({items:[{id:'own',display_name:'开发玩家'}]});
  if(url.startsWith('/api/profile/versions'))return ok({items:[{version:1,changes:[],model:'test'}]});
  if(url.startsWith('/api/profile/evidence'))return ok({items:[{content:{kind:'user_message',text:'只讲重点'}}]});
  return ok(profile);
 };
 vm.runInContext(fs.readFileSync('static/profile.js','utf8'),dom.getInternalVMContext());return dom;
}
test('renders safe facts, filters game and loads evidence/history',async()=>{
 const dom=setup({version:1,data:{facts:[fact]},jobs:[]});
 try{await tick();const d=dom.window.document;
  assert.match(d.getElementById('profile-identity').textContent,/登录待接入/);
  assert.equal(d.querySelectorAll('img').length,0);
  assert.match(d.getElementById('profile-facts').textContent,/<img/);
  const details=d.querySelector('.fact details');details.open=true;details.dispatchEvent(new dom.window.Event('toggle'));await tick();
  assert.match(details.textContent,/只讲重点/);
  d.getElementById('profile-scope').value='lol';d.getElementById('profile-scope').dispatchEvent(new dom.window.Event('change'));
  assert.match(d.getElementById('profile-facts').textContent,/暂时没有/);
  const history=d.getElementById('profile-history');history.open=true;history.dispatchEvent(new dom.window.Event('toggle'));await tick();
  assert.match(d.getElementById('profile-versions').textContent,/版本 1/);
 }finally{await new Promise(resolve=>setTimeout(resolve,20));dom.window.close();}
});
test('empty profile explains automatic creation and owner can retry',async()=>{
 let retried=false;const dom=setup({version:0,data:{facts:[]},jobs:[{id:'job',status:'failed'}]},(url,opts)=>{
  if(url.endsWith('/retry')){retried=opts.method==='POST';return ok({queued:true});}
 });
 try{await tick();const d=dom.window.document;assert.match(d.getElementById('profile-facts').textContent,/还没有长期画像/);
  d.querySelector('#profile-jobs button').click();await tick();assert.equal(retried,true);
 }finally{dom.window.close();}
});
