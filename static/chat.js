"use strict";
(() => {
  const $ = id => document.getElementById("chat-" + id);
  if (new URLSearchParams(location.search).get("embedded") === "1") document.body.classList.add("embedded");
  const storageKey = "gameplan-knowledge-chat-session";
  let session = null, busy = false, generation = 0, controller, pending = null;
  let serverOffset = 0;
  const gameNames = {hok:"王者荣耀", lol:"英雄联盟"};
  function node(tag, text, className) {const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(className)n.className=className; return n;}
  function save() {try{localStorage.setItem(storageKey,session.id);}catch(_){$("status").textContent="浏览器无法保存对话编号，请保留当前页面。";}}
  function setBusy(value) {
    busy=value; $("send").disabled=value || !session; $("input").disabled=value;
    for(const button of document.querySelectorAll(".examples button,.choice-list button,.timer-remove,#chat-clear-timers"))button.disabled=value;
  }
  function acceptSession(value) {
    session=value;
    if(Number.isFinite(value.server_epoch))serverOffset=value.server_epoch-Date.now()/1000;
    else serverOffset=0;
  }
  function tickTimers() {
    const ready=[];
    for(const timer of session?.timers||[]){
      const output=document.getElementById("chat-timer-"+timer.id);
      if(!output)continue;
      if(timer.ready_at==null){output.textContent="冷却未知";output.title="已记录使用，冷却时间未知";continue;}
      const remaining=Math.max(0,Math.ceil(timer.ready_at-Date.now()/1000-serverOffset));
      const label=timer.hero+timer.skill;
      output.textContent=remaining?"剩余 "+remaining+" 秒":"预计已好";
      output.title=(gameNames[timer.game||"hok"]||"")+" · "+label+(remaining?"预计剩余 "+remaining+" 秒":"预计已好");
      output.closest(".timer-card").classList.toggle("ready",remaining===0);
      if(!remaining)ready.push(label+"预计已好");
    }
    const text=ready.join("；");
    if($("timer-alert").textContent!==text)$("timer-alert").textContent=text;
  }
  function renderTimers(){
    $("timers").hidden=!(session?.timers||[]).length;
    $("timer-cards").replaceChildren();
    for(const timer of session?.timers||[]){
      const card=node("div",undefined,"timer-card"),output=node("span");output.id="chat-timer-"+timer.id;
      const remove=node("button","取消","timer-remove");remove.type="button";remove.disabled=busy;
      remove.setAttribute("aria-label","取消"+timer.hero+timer.skill+"计时");
      const game=gameNames[timer.game||"hok"]||"王者荣耀";
      remove.addEventListener("click",()=>send("取消"+game+"的"+timer.hero+timer.skill+"计时"));
      card.append(node("strong",timer.hero+" · "+timer.skill),node("small",game+" · "+timer.skill_name),output,remove);$("timer-cards").append(card);
    }
    tickTimers();
  }
  async function api(path, body, signal) {
    const response=await fetch(path,{method:body===undefined?"GET":"POST",headers:body===undefined?{}:{"Content-Type":"application/json"},body:body===undefined?undefined:JSON.stringify(body),signal});
    const data=await response.json();
    if(!response.ok){const error=new Error(typeof data.detail==="string"?data.detail:"请求未完成，请重试。");error.status=response.status;throw error;}
    return data;
  }
  function sourceLink(parent,text,url) {
    if(!url)return;
    try{const parsed=new URL(url,location.origin);if(parsed.origin!==location.origin && (parsed.protocol!=="https:" || !["pvp.qq.com","lol.qq.com","ddragon.leagueoflegends.com"].includes(parsed.hostname)))return;
      const a=node("a",text);a.href=parsed.href;a.target="_blank";a.rel="noopener noreferrer";parent.append(a);
    }catch(_){}
  }
  function addMessage(role,text,response) {
    const card=node("section",undefined,"message "+role);card.append(node("p",role==="user"?"你":"小象助手","speaker"),node("p",text,"answer"));
    if(response){
      const labels={timer:"技能计时 · 本地估算",local:"本地知识库",hybrid:"知识库 + 模型补充",external:"模型补充 · 未联网",clarification:"需要确认",partial:"部分回答 · 补充未完成"};
      card.append(node("span",labels[response.status]||"回答","badge"+(["hybrid","external","partial"].includes(response.status)?" model":"")));
      if(response.model)card.append(node("p","补充模型："+response.model+" · 未联网核验","speaker"));
      if(response.profile_version>0)card.append(node("p","个人档案版本："+response.profile_version,"speaker"));
      if(response.sources?.length){
        const details=node("details",undefined,"source-detail");details.append(node("summary","查看知识库依据（"+response.sources.length+"）"));
        for(const source of response.sources){const entry=node("div",undefined,"source-record");entry.append(node("strong",[gameNames[source.game],source.hero,source.title].filter(Boolean).join(" · ")),node("p",source.text),node("p","版本："+source.version+"。"+source.note));sourceLink(entry,"打开知识库",source.page_url);sourceLink(entry,"原始来源",source.source_url);details.append(entry);}card.append(details);
      }
      if(response.choices?.length){const choices=node("div",undefined,"choice-list");for(const choice of response.choices){const b=node("button",choice.label);b.type="button";b.addEventListener("click",()=>send(choice.text));choices.append(b);}card.append(choices);}
    }
    $("messages").append(card);
  }
  function render(){
    renderTimers();
    $("messages").replaceChildren();
    if(!session?.turns.length){const welcome=node("div",undefined,"welcome");welcome.append(node("strong","把问题交给我，先从知识库找答案。"),node("p","支持王者荣耀和英雄联盟。可以直接说英雄名，也能接着追问；有歧义时我会先确认。"));$("messages").append(welcome);}
    for(const turn of session?.turns||[]){addMessage("user",turn.user);addMessage("assistant",turn.response.answer,turn.response);}
    const context=session?.context||{},heroes=(context.heroes||[]).map(h=>h.name).join("、");
    $("context").textContent=[gameNames[context.game],heroes].filter(Boolean).join(" · ")||"自动识别游戏与英雄";
    $("examples").hidden=!!session?.turns.length;
    $("messages").scrollTop=$("messages").scrollHeight;
  }
  async function initialize(fresh=false){
    const token=++generation;controller?.abort();controller=new AbortController();pending=null;setBusy(true);$("retry").hidden=true;
    $("status").textContent=fresh?"正在开始新对话…":"正在恢复对话…";
    try{
      let id=null;try{if(!fresh)id=localStorage.getItem(storageKey);}catch(_){}
      let result;
      if(id){try{result=await api("/api/chat/sessions/"+encodeURIComponent(id),undefined,controller.signal);}catch(error){if(error.status!==404)throw error;}}
      if(!result)result=await api("/api/chat/sessions",{},controller.signal);
      if(token!==generation)return;
      acceptSession(result);save();render();$("input").value="";$("status").textContent=session.busy?"上一条消息仍在处理，可稍后重试恢复。":"可以提问或报技能计时。Enter 发送，Shift + Enter 换行。";
    }catch(error){if(token===generation && error.name!=="AbortError"){$("status").textContent=error.message;$("retry").hidden=false;}}
    finally{if(token===generation)setBusy(false);}
  }
  async function send(text){
    text=text.trim();if(!text||busy||!session)return;
    const token=++generation;controller?.abort();controller=new AbortController();
    const request=pending && pending.text===text && pending.sid===session.id?pending:{text,sid:session.id,request_id:crypto.randomUUID(),revision:session.revision};pending=request;
    setBusy(true);$("retry").hidden=true;render();addMessage("user",text);addMessage("assistant","正在查询知识库；需要时会补充缺失资料…");$("messages").scrollTop=$("messages").scrollHeight;
    $("status").textContent="正在处理，可以用“新对话”切换话题。";
    const requestController=controller;
    const timeout=setTimeout(()=>requestController.abort(),90000);
    try{
      const result=await api("/api/chat/sessions/"+encodeURIComponent(request.sid)+"/messages",{text:request.text,request_id:request.request_id,revision:request.revision},controller.signal);
      if(token!==generation)return;
      acceptSession(result.session);pending=null;save();render();$("input").value="";$("status").textContent="回答已保存，可以继续追问。";
    }catch(error){
      if(token!==generation)return;
      if(error.status===409){try{const synced=await api("/api/chat/sessions/"+encodeURIComponent(request.sid));if(token!==generation)return;acceptSession(synced);save();}catch(_){} }
      render();$("input").value=text;$("status").textContent=error.name==="AbortError"?"等待超时，问题可能仍在处理。点击重试可恢复结果。":error.message;$("retry").hidden=false;
      if(error.status===409 && !session.busy)pending=null;
    }finally{clearTimeout(timeout);if(token===generation){setBusy(false);$("input").focus();}}
  }
  $("form").addEventListener("submit",event=>{event.preventDefault();void send($("input").value);});
  $("input").addEventListener("keydown",event=>{if(event.key==="Enter"&&!event.shiftKey&&!event.isComposing){event.preventDefault();void send($("input").value);}});
  $("new").addEventListener("click",()=>initialize(true));
  $("clear-timers").addEventListener("click",()=>send("清空计时器"));
  setInterval(tickTimers,250);
  document.addEventListener("visibilitychange",tickTimers);
  $("retry").addEventListener("click",()=>pending?send(pending.text):initialize());
  for(const button of document.querySelectorAll("[data-question]"))button.addEventListener("click",()=>send(button.dataset.question));
  void initialize();
})();
