"use strict";
(()=>{
  const host=document.getElementById('workspace'),status=document.getElementById('workspace-status');
  const header=document.querySelector('header');
  new ResizeObserver(()=>{host.style.height=`calc(100dvh - ${header.getBoundingClientRect().height}px)`;}).observe(header);
  const paths={screen:'/monitor?mode=screen&embedded=true'+(new URLSearchParams(location.search).get('monitor')==='off'?'&monitor=off':'')};
  const labels={screen:'统一对局工作台'};
  let current='';
  function selected(){return 'screen';}
  function show(view,push=false){
    view='screen';if(view===current)return;
    const old=host.querySelector('iframe');
    // Stop the active sampler before destroying its document and pending UI state.
    if(old){try{old.contentWindow.dispatchEvent(new Event('pagehide'));}catch{}old.remove();}
    current=view;status.textContent='正在打开';
      if(push)history.pushState({},'',`/monitor?view=screen`);
    const frame=document.createElement('iframe');frame.title=labels[view];frame.src=paths[view];
    frame.addEventListener('load',()=>{
      if(current!==view)return;
      const doc=frame.contentDocument;
      if(!doc){status.textContent='页面未就绪';return;}
      const header=doc.querySelector('.masthead');if(header)header.hidden=true;
      doc.querySelectorAll('a[href]').forEach(a=>a.addEventListener('click',event=>{
        const url=new URL(a.href);if(url.origin!==location.origin)return;
        if(url.pathname==='/bp'||url.pathname==='/monitor'){event.preventDefault();show(url.pathname==='/bp'?'bp':url.searchParams.get('mode')==='screen'?'screen':'loading',true);}
      }));
      status.textContent=labels[view]+' · 就绪';
      const phase=doc.getElementById('phase');
      const phaseLabel=doc.getElementById('workspace-phase');
      const syncPhase=()=>{if(phaseLabel&&phase)phaseLabel.textContent='当前阶段 · '+phase.textContent;};
      syncPhase();
      new MutationObserver(syncPhase).observe(phase,{childList:true,subtree:true,characterData:true});
    });host.append(frame);
  }
  addEventListener('popstate',()=>show(selected()));show(selected());
})();

