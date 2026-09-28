"use strict";
(async () => {
  const byId = id => document.getElementById(id);
  let heroes = [], generation = 0;
  async function get(url) {const response = await fetch(url); if (!response.ok) throw new Error("知识库暂不可用"); return response.json();}
  function paragraph(parent, label, text) {const node=document.createElement("p"); node.textContent=label+"："+(text || "待补充"); parent.append(node);}
  function section(title) {const node=document.createElement("section");node.className="panel";const h=document.createElement("h2");h.textContent=title;node.append(h);return node;}
  async function show() {
    const id=++generation, hero=byId("hero-select").value;byId("hero-knowledge").replaceChildren();if(!hero)return;
    try {
      const data=await get("/api/knowledge/skills/"+encodeURIComponent(hero));if(id!==generation)return;
      const root=byId("hero-knowledge"), intro=section(data.hero);
      paragraph(intro,"别名",(data.voice_aliases?.aliases||[]).join("、"));
      paragraph(intro,"同音/错字映射",(data.voice_aliases?.asr_variants||[]).join("、"));
      paragraph(intro,"资料采集",data.fetched_at);paragraph(intro,"版本状态","官网未标明完整正式服版本；基础冷却不等于当前剩余冷却。");root.append(intro);
      const skills=section("技能与冷却");
      for(const skill of data.skills||[]) {
        const details=document.createElement("details"), title=document.createElement("summary");
        title.textContent=(skill.is_ultimate?"大招 · ":skill.passive?"被动 · ":"")+skill.name;details.open=skill.is_ultimate;
        details.append(title);paragraph(details,"基础冷却",skill.cooldown_review_required?"待核对（"+skill.cooldown_text+"）":skill.cooldown_text);paragraph(details,"技能效果",skill.description);
        if(skill.special_mechanic)paragraph(details,"计时条件","含特殊冷却、充能或刷新机制，需结合技能描述确认。");
        skills.append(details);
      }root.append(skills);
      const ref=data.strategy_reference, strategy=section("打法与出装 · 版本待核验");
      if(ref){paragraph(strategy,"使用边界",ref.review_note);
        for(const [label,key] of [["分路参考","suggested_lane"],["使用技巧","usage"],["对线反制","counterplay"],["团战","teamfight"],["加点","skill_order"],["铭文思路","runes_note"]])paragraph(strategy,label,ref[key]);
        for(const [index,build] of (ref.builds||[]).entries()) {paragraph(strategy,"出装参考 "+(index+1),(build.items||[]).map(item=>item.name||"装备"+item.id+"（待核对）").join(" → "));paragraph(strategy,"出装思路",build.note);}
      }else paragraph(strategy,"状态","攻略待补充");
      root.append(strategy);
      if(data.source_url?.startsWith("https://pvp.qq.com/")){const link=document.createElement("a");link.href=data.source_url;link.textContent="查看腾讯官网原始资料";link.target="_blank";link.rel="noopener noreferrer";root.append(link);}
      byId("knowledge-status").textContent="已加载 "+hero+"；共享别名请在列表中选择具体英雄。";
    }catch(error){if(id===generation)byId("knowledge-status").textContent=error.message;}
  }
  function filter(){const query=byId("hero-search").value.trim();const select=byId("hero-select");select.replaceChildren();for(const hero of heroes.filter(row=>[row.hero,...row.aliases].some(name=>name.includes(query)))){const option=document.createElement("option");option.value=hero.hero;option.textContent=hero.hero;select.append(option);}byId("knowledge-status").textContent=select.options.length?select.options.length+" 个候选":"没有找到，请使用官方名称。";void show();}
  try {heroes=(await get("/api/knowledge/heroes")).heroes;byId("hero-search").addEventListener("input",filter);byId("hero-select").addEventListener("change",show);filter();}catch(error){byId("knowledge-status").textContent=error.message;}
})();
