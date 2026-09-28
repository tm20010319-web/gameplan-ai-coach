"use strict";
(() => {
  const byId = id => document.getElementById(id);
  let searchGeneration = 0, detailGeneration = 0, selected = "", timer;
  let searchAbort, detailAbort;
  const initialHero = new URLSearchParams(location.search).get("hero");
  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function section(title) { const node = el("section", undefined, "section"); node.append(el("h3", title)); return node; }
  async function get(url, signal) {
    const response = await fetch(url, {signal});
    if (!response.ok) throw new Error(response.status === 404 ? "没有找到这位英雄。" : "知识库加载失败，请确认项目服务已启动后重试。");
    return response.json();
  }
  function showError(error) {
    byId("knowledge-status").textContent = error.message;
    byId("retry").hidden = false;
  }
  function tipList(parent, tips, empty) {
    if (!tips.length) { parent.append(el("p", empty, "muted")); return; }
    const list = el("ul");
    for (const tip of tips) list.append(el("li", tip));
    parent.append(list);
  }
  function render(data) {
    const root = byId("hero-detail");
    const intro = el("section", undefined, "hero-header");
    intro.append(el("p", data.title + " / " + data.id, "hero-title"), el("h2", data.name));
    const chips = el("div", undefined, "chips");
    for (const role of data.roles) chips.append(el("span", role, "chip"));
    intro.append(chips, el("p", "别名与检索词：" + data.aliases.join("、"), "muted"));
    const skillSection = section("技能与基础冷却");
    skillSection.append(el("p", "数值依次对应技能等级，单位为秒；未计技能急速、刷新和特殊缩减。展示官方资料中的被动及 Q / W / E / R 主技能，变身或组合子技能需参照游戏内说明。", "muted"));
    const grid = el("div", undefined, "skill-grid");
    for (const skill of data.skills) {
      const card = el("div", undefined, "skill"), heading = el("h4");
      heading.append(el("span", skill.slot, "key"), document.createTextNode(skill.name));
      card.append(heading, el("p", skill.cooldown_text, "cooldown"), el("p", skill.description));
      if (skill.special && skill.slot !== "P") card.append(el("p", "特殊机制：官方基础字段不能直接作为实战倒计时；请核对充能、形态、攻速或技能自身规则。", "notice"));
      grid.append(card);
    }
    skillSection.append(grid);
    const play = section("打法与对线");
    play.append(el("h4", "官方使用技巧 · 版本待复核"));
    tipList(play, data.ally_tips, "官方暂未提供使用技巧，请结合下方类型打法与技能说明。");
    play.append(el("h4", "官方对抗技巧 · 版本待复核"));
    tipList(play, data.enemy_tips, "官方暂未提供对抗技巧。");
    if (data.tips_review_note) play.append(el("p", data.tips_review_note, "muted"));
    play.append(el("h4", "团战思路 · " + data.play_reference.label), el("p", data.play_reference.text), el("p", data.play_reference.kind + "；官方技巧也可能滞后于版本调整。", "muted"));
    const builds = section("出装参考 · " + data.build_reference.label);
    builds.append(el("p", data.build_reference.kind, "notice"), el("p", data.build_reference.note));
    const items = el("div", undefined, "items");
    for (const item of data.build_reference.items) {
      const card = el("details", undefined, "item");
      card.append(el("summary", item.name), el("span", item.gold + " 金币 · 总价", "gold"), el("p", item.description));
      items.append(card);
    }
    builds.append(items);
    const source = section("版本与资料来源");
    source.append(el("p", "端游 · Data Dragon " + data.version + " · 采集 " + data.fetched_at.slice(0, 10)), el("p", data.note, "muted"));
    const links = el("div", undefined, "source-links");
    for (const [label, url] of [["英雄官网", data.official_url], ["Riot 英雄数据", data.source_url], ["Riot 装备数据", data.sources.items]]) {
      const target = new URL(url);
      if (target.protocol !== "https:" || !["lol.qq.com", "ddragon.leagueoflegends.com"].includes(target.hostname)) continue;
      const a = el("a", label); a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer"; links.append(a);
    }
    source.append(links);
    root.replaceChildren(intro, skillSection, play, builds, source);
  }
  async function select(heroId) {
    selected = heroId;
    const generation = ++detailGeneration;
    detailAbort?.abort(); detailAbort = new AbortController();
    const root = byId("hero-detail");
    root.setAttribute("aria-busy", "true");
    root.replaceChildren(el("div", "正在读取英雄资料…", "empty"));
    for (const button of byId("hero-list").querySelectorAll("button")) button.setAttribute("aria-pressed", String(button.dataset.hero === heroId));
    byId("retry").hidden = true;
    try {
      const data = await get("/api/knowledge/lol/heroes/" + encodeURIComponent(heroId), detailAbort.signal);
      if (generation !== detailGeneration) return;
      render(data);
      const url = new URL(location.href); url.searchParams.set("hero", heroId); history.replaceState(null, "", url);
      byId("knowledge-status").textContent = "已加载 " + data.name + " · 可继续搜索英雄、技能或装备。";
    } catch (error) {
      if (generation !== detailGeneration || error.name === "AbortError") return;
      root.replaceChildren(el("div", "资料加载失败，请点击重新加载。", "empty")); showError(error);
    } finally { if (generation === detailGeneration) root.setAttribute("aria-busy", "false"); }
  }
  async function search() {
    const generation = ++searchGeneration;
    searchAbort?.abort(); searchAbort = new AbortController();
    const query = byId("hero-search").value.trim(), role = byId("role-filter").value;
    byId("knowledge-status").textContent = "正在搜索…";
    byId("retry").hidden = true;
    try {
      const data = await get("/api/knowledge/lol/heroes?" + new URLSearchParams({q: query, role}), searchAbort.signal);
      if (generation !== searchGeneration) return;
      byId("roster-count").textContent = data.total;
      byId("snapshot-version").textContent = "版本 " + data.version;
      if (byId("role-filter").options.length === 1) for (const role of data.roles) {const option = el("option", role); option.value = role; byId("role-filter").append(option);}
      const list = byId("hero-list"); list.replaceChildren();
      byId("result-count").textContent = data.matched + " / " + data.total;
      for (const hero of data.heroes) {
        const button = el("button", undefined, "hero-button"); button.type = "button"; button.dataset.hero = hero.id;
        button.append(el("strong", hero.name), el("span", hero.title + " · " + hero.roles.join(" / ")));
        button.addEventListener("click", () => select(hero.id)); list.append(button);
      }
      if (!data.heroes.length) {
        selected = ""; ++detailGeneration; detailAbort?.abort();
        byId("hero-detail").setAttribute("aria-busy", "false");
        byId("hero-detail").replaceChildren(el("div", "没有找到匹配资料。试试英雄全名、英文名，或清除职业筛选。", "empty"));
        byId("knowledge-status").textContent = "没有匹配结果。"; return;
      }
      const desired = [selected, initialHero, data.heroes[0].id].find(id => data.heroes.some(hero => hero.id === id));
      await select(desired);
    } catch (error) { if (generation === searchGeneration && error.name !== "AbortError") showError(error); }
  }
  function invalidate() {
    ++searchGeneration; ++detailGeneration; searchAbort?.abort(); detailAbort?.abort();
    byId("hero-list").replaceChildren(); byId("result-count").textContent = "";
    byId("hero-detail").replaceChildren(el("div", "正在查找匹配资料…", "empty"));
    byId("hero-detail").setAttribute("aria-busy", "false");
  }
  byId("hero-search").addEventListener("input", () => {clearTimeout(timer); invalidate(); timer = setTimeout(search, 180);});
  byId("role-filter").addEventListener("change", () => {clearTimeout(timer); invalidate(); void search();});
  byId("retry").addEventListener("click", () => {clearTimeout(timer); void search();});
  void search();
})();
