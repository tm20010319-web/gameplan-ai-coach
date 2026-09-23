"use strict";
(()=>{
  const $=id=>document.getElementById(id),names=id=>$(id).value.split(/[、，,\s]+/).filter(Boolean);
  let running=false,generation=0,timer=null,controller=null,busy=false,signature='',version=0;
  async function api(path,body,signal){const r=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:signal?AbortSignal.any([signal,AbortSignal.timeout(150000)]):AbortSignal.timeout(20000)});const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'请核对英雄名称和名单');return d;}
  function validHandoff(){return Boolean($('side').value&&$('lane').value&&$('confirmed').checked&&(names('left').length||names('right').length));}
  function handoffControls(){const ready=validHandoff();$('enter-monitor').disabled=!ready;$('handoff-note').textContent=ready?'BP 已确认。进入对局后会保留这份阵容和分路，并开始记录敌方已确认施放技能的冷却。':'核对阵容、己方位置和分路后，可进入对局监控。';}
  function saveHandoff(){if(!validHandoff())return false;const side=$('side').value;localStorage.setItem('gameplan-bp-context',JSON.stringify({version:1,created_at:Date.now(),side,lane:$('lane').value,player:$('locked').value||null,allies:names(side),enemies:names(side==='left'?'right':'left'),banned:names('banned')}));return true;}
  function clear(){version++;$('results').replaceChildren();$('message').textContent='等待核对当前选人信息';handoffControls();}
  function invalidate(){signature='';$('confirmed').checked=false;clear();}
  function lockOptions(){const old=$('locked').value,a=$('side').value?names($('side').value):[];$('locked').replaceChildren(new Option('尚未锁定',''),...a.map(h=>new Option(h,h)));if(a.includes(old))$('locked').value=old;}
  async function recommend(){clear();const v=version,side=$('side').value;if(!side||!$('lane').value||!$('confirmed').checked)return;try{const r=await api('/api/bp-assistant/recommend',{allies:names(side),enemies:names(side==='left'?'right':'left'),banned:names('banned'),available:names('available'),lane:$('lane').value,confirmed:true,locked:$('locked').value||null});if(v!==version)return;$('message').textContent=r.message;$('boundary').textContent=r.note;for(const item of r.items){const section=document.createElement('section'),h=document.createElement('h3'),p=document.createElement('p'),small=document.createElement('small');h.textContent=`${item.hero} → ${item.enemy}`;p.textContent=item.reason+'。'+item.condition;small.textContent=item.relation+' · '+item.source_id;section.append(h,p,small);$('results').append(section);}}catch(e){if(v===version)$('message').textContent=e.message;}}
  function pause(){running=false;generation++;clearTimeout(timer);controller?.abort();$('start').disabled=false;$('stop').disabled=true;$('status').textContent='已暂停';}
  async function scan(image,token){$('preview').src=image;$('preview').hidden=false;$('status').textContent='本地 Ollama 读取选人槽位';const r=await api('/api/bp-assistant/observe',{image_base64:image},controller.signal);if(token!==generation)return;$('phase').textContent=r.phase;$('note').textContent=r.note;if(r.lane){$('lane').value=r.lane;$('note').textContent+=`· 我的分路：${r.lane}（${r.lane_source}）`;}
    if(r.phase!=='bp'||r.confidence<.8){invalidate();for(const id of ['left','right','banned'])$(id).value='';lockOptions();$('status').textContent='等待清晰选人画面';return;}
    const key=JSON.stringify([r.left,r.right,r.banned]);if(key!==signature){invalidate();signature=key;for(const id of ['left','right','banned'])$(id).value=r[id].join('、');lockOptions();}
    $('status').textContent='识别候选 · 请核对';}
  async function tick(token){if(!running||token!==generation||busy)return;busy=true;controller=new AbortController();try{const frame=await api('/api/screen/frame',{source_id:$('screen').value},controller.signal);if(token===generation)await scan(frame.image_base64,token);}catch(e){if(token===generation){invalidate();$('status').textContent=e.message;}}finally{busy=false;if(running&&token===generation)timer=setTimeout(()=>tick(token),2000);}}
  $('start').onclick=()=>{if(busy){$('status').textContent='等待上一帧结束后再开始';return;}if(!$('screen').value)return;pause();invalidate();running=true;$('start').disabled=true;$('stop').disabled=false;tick(generation);};
  $('stop').onclick=()=>{pause();invalidate();};$('screen').onchange=()=>{pause();invalidate();};
  window.addEventListener('pagehide',()=>{pause();invalidate();});
  $('reset').onclick=()=>{pause();invalidate();for(const id of ['left','right','banned'])$(id).value='';lockOptions();$('preview').hidden=true;$('preview').removeAttribute('src');};
  $('file').onchange=async()=>{const file=$('file').files[0];$('file').value='';if(!file)return;pause();invalidate();if(busy){$('status').textContent='等待上一帧结束后重新选择';return;}if(file.size>8000000){$('status').textContent='图片不能超过8MB';return;}const token=generation;busy=true;controller=new AbortController();try{const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(file);});if(token===generation)await scan(data,token);}catch(e){if(token===generation)$('status').textContent=e.message;}finally{busy=false;}};
  for(const id of ['left','right','banned'])$(id).oninput=()=>{invalidate();lockOptions();};
  $('side').onchange=()=>{invalidate();lockOptions();};
  for(const id of ['lane','available','confirmed'])$(id).onchange=recommend;
  $('locked').onchange=()=>{if($('locked').value)pause();recommend();};
  $('enter-monitor').onclick=()=>{if(saveHandoff())location.assign('/monitor?mode=screen&embedded=true');};
  api('/api/bp-assistant/heroes').then(r=>{available.value=r.join('、');}).catch(()=>{});
  api('/api/screen/sources').then(r=>{$('screen').replaceChildren(...r.sources.map(s=>new Option(s.label,s.id)));if(r.sources.length){$('status').textContent='已连接投屏，自动寻找选人界面';$('start').click();}}).catch(e=>$('status').textContent=e.message);
})();



