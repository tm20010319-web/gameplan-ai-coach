"use strict";
(()=>{
 const $=id=>document.getElementById('profile-'+id);
 const scopes={global:'通用偏好',hok:'王者荣耀',lol:'英雄联盟'};
 const categories={preference:'偏好',communication:'沟通方式',goal:'练习目标',hero:'常用英雄',role:'分路习惯',strength:'优势',improvement:'待改进',progress:'近期进步'};
 const labels={stated:'玩家明确表达',observed:'多局观察',tentative:'待验证'};
 const jobLabels={pending:'等待更新',running:'模型总结中',done:'已更新',failed:'待重试'};
 let me=null,target=null,current=null,generation=0,timer=null,offset=0;
 function n(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
 async function api(url,method='GET'){const r=await fetch(url,{method,signal:AbortSignal.timeout(15000)});const data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'档案暂时无法读取，请重试。');return data;}
 const query=()=>'?user_id='+encodeURIComponent(target);
 const date=value=>value?new Date(value).toLocaleString('zh-CN'):'尚未更新';
 function facts(){
  const box=$('facts');box.replaceChildren();
  const items=(current?.data.facts||[]).filter(f=>$('scope').value==='all'||f.scope===$('scope').value);
  if(!items.length){box.append(n('p',current?.version?'这个范围暂时没有画像记录。':'还没有长期画像。开始一局游戏并与小象助手聊天，局后将自动生成。','empty'));return;}
  items.forEach(f=>{
   const card=n('article',undefined,'fact');card.append(n('small',scopes[f.scope]+' · '+categories[f.category]),n('p',f.value),n('span',labels[f.status]||'待验证','badge'));
   if(f.supporting_matches?.length)card.append(n('p','来自 '+f.supporting_matches.length+' 局记录','meta'));
   const detail=n('details'),content=n('div');detail.append(n('summary','查看依据'),content);let loaded=false;const viewedUser=target;
   detail.addEventListener('toggle',async()=>{if(!detail.open||loaded)return;loaded=true;content.textContent='正在读取…';try{const data=await api('/api/profile/evidence?user_id='+encodeURIComponent(viewedUser)+'&scope='+encodeURIComponent(f.scope)+'&key='+encodeURIComponent(f.key));content.replaceChildren();data.items.forEach(item=>{content.append(n('small',item.content.kind==='user_message'?'玩家对话':'对局复盘'),n('blockquote',item.content.text));});}catch(error){loaded=false;content.textContent=error.message;}});
   card.append(detail);box.append(card);
  });
 }
 function jobs(items){
  $('jobs').replaceChildren();if(!items.length)$('jobs').append(n('p','对局结束后，这里会显示自动更新进度。'));
  items.forEach(job=>{const row=n('div',undefined,'job');row.append(n('strong',jobLabels[job.status]||job.status),n('small',' · '+date(job.created_at)));if(job.error)row.append(n('p',job.error));if(job.status==='failed'&&target===me.id){const button=n('button','重试这次更新');button.onclick=async()=>{button.disabled=true;try{await api('/api/profile/jobs/'+encodeURIComponent(job.id)+'/retry','POST');await refresh();}catch(error){$('status').textContent=error.message;button.disabled=false;}};row.append(button);} $('jobs').append(row);});
 }
 async function versions(append=false){
  const version=generation,requestedOffset=append?offset:0;
  try{const data=await api('/api/profile/versions'+query()+'&limit=10&offset='+requestedOffset);if(version!==generation)return;
   if(!append)$('versions').replaceChildren();
   if(!data.items.length&&!append)$('versions').append(n('p','画像首次更新后会保留版本记录。'));
   data.items.forEach(v=>{const row=n('div',undefined,'version');row.append(n('strong','版本 '+v.version),n('small',' · '+date(v.created_at)+' · '+v.model));if(!v.changes.length)row.append(n('p','本次没有新增的可靠画像信息，原画像已保留。'));v.changes.forEach(c=>row.append(n('p',(scopes[c.scope]||'')+'：'+c.value)));$('versions').append(row);});offset=requestedOffset+data.items.length;$('more').hidden=data.items.length<10;
  }catch(error){if(version===generation)$('status').textContent=error.message;}
 }
 async function refresh(){
  clearTimeout(timer);timer=null;const version=++generation;$('refresh').disabled=true;
  try{const data=await api('/api/profile'+query());if(version!==generation)return;current=data;facts();jobs(data.jobs);$('version').textContent='版本 '+data.version+' · '+date(data.updated_at);$('status').textContent='每局结束后自动汇总新复盘与新增对话。';$('status').classList.remove('error');if($('history').open)await versions();if(data.jobs.some(j=>['pending','running'].includes(j.status)))timer=setTimeout(refresh,5000);
  }catch(error){if(version===generation){$('status').textContent=error.message;$('status').classList.add('error');}}
  finally{if(version===generation)$('refresh').disabled=false;}
 }
 $('scope').onchange=facts;$('refresh').onclick=refresh;$('history').addEventListener('toggle',()=>{if($('history').open)void versions();});$('more').onclick=()=>versions(true);
 $('player').onchange=()=>{target=$('player').value;current=null;$('facts').replaceChildren();$('versions').replaceChildren();void refresh();};
 addEventListener('pagehide',()=>{generation++;clearTimeout(timer);});
 void(async()=>{try{me=await api('/api/me');target=me.id;$('identity').textContent=me.display_name+(me.development_user?' · 本地开发账号（登录待接入）':'');const users=await api('/api/accounts/players');users.items.forEach(u=>{const o=n('option',u.display_name);o.value=u.id;$('player').append(o);});$('player').value=target;$('player-label').hidden=users.items.length<2;await refresh();}catch(error){$('status').textContent=error.message;$('status').classList.add('error');}})();
})();
