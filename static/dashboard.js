"use strict";

function initializeDashboard() {
  const $ = id => document.getElementById(id);
  const pick = selector => document.querySelector(selector);
  const button = (text, fn) => {
    const b = document.createElement("button"); b.className = "subtle";
    b.type = "button"; b.textContent = text; b.addEventListener("click", fn); return b;
  };
  function modal(title, nodes) {
    const dialog = document.createElement("dialog"); dialog.className = "dashboard-dialog";
    const header = document.createElement("div"); header.className = "dialog-head";
    const heading = document.createElement("h2"); heading.textContent = title;
    header.append(heading, button("关闭", () => dialog.close()));
    const content = document.createElement("div"); content.className = "dashboard-dialog-body";
    nodes.filter(Boolean).forEach(n => content.append(n)); dialog.append(header, content);
    document.body.append(dialog);
    return () => { if (!dialog.open) dialog.showModal(); };
  }

  const stage = pick(".stage"), plan = pick(".integrated-plan"), vision = pick(".vision-monitor");
  if (document.body.classList.contains("three-panel") && stage && vision) {
    const settings = modal("采集与识别设置", [pick(".screen-controls"), pick(".roi")]);
    stage.querySelector(".panel-head").append(button("采集设置", settings));
    vision.querySelector("h2").textContent = "技能冷却";
    const reference = pick(".vision-reference");
    const openReference = modal("技能资料与人工冷却登记", [reference]);
    reference.open = true;
    vision.querySelector(".panel-head").append(button("技能资料 / 登记", openReference));
    const details = modal("AI 分析详情", [$("coach-detail"), $("coach-provider"), $("coach-meta")]);
    $("coach-detail").open = true;
    pick(".coach-summary .panel-head").append(button("详情", details));
    const body = document.createElement("div");
    body.className = "skill-panel-body";
    [...vision.children].slice(1).forEach(node => body.append(node));
    vision.append(body);
    pick(".center").prepend(stage, vision);
    return;
  }
  if (!stage || !plan || !vision) return;
  document.body.classList.add("one-screen");
  const openSettings = modal("采集与识别设置", [pick(".screen-controls"), pick(".roi")]);
  stage.querySelector(".panel-head").append(button("采集设置", openSettings));
  const openPlan = modal("完整战术方案", [$("loading-result")]);
  const openLineups = modal("核对阵容与分析视角", [pick(".loading-setup")]);
  const openReference = modal("技能资料与人工冷却登记", [pick(".vision-reference")]);
  const reference = pick(".vision-reference"); if (reference) reference.open = true;
  const openVision = modal("识别详情", [pick(".vision-detail")]);
  pick(".vision-detail").open = true;
  const timers = pick(".merged-cooldown");
  const openTimers = modal("本局技能计时", [timers]);
  const openCoach = modal("AI 分析详情", [$("coach-detail"), $("coach-provider"), $("coach-meta")]);
  $("coach-detail").open = true;
  pick(".coach-summary .panel-head").append(button("详情", openCoach));
  const openHud = modal("局中战术提醒", [pick(".hud-panel")]);
  pick(".coach-summary .panel-head").append(button("提醒", openHud));

  const deck = document.createElement("section"); deck.className = "dashboard-deck panel";
  const tabs = document.createElement("div"); tabs.className = "dashboard-tabs"; tabs.setAttribute("role", "tablist");
  const rosterView = document.createElement("div"); rosterView.className = "dashboard-roster";
  rosterView.append(pick(".roster-panel"), $("roster-auto-status"));
  rosterView.querySelector(".panel-head").append(button("核对 / 视角", openLineups));
  const brief = $("loading-brief"); brief.classList.add("dashboard-brief");
  // Visibility comes from the deck tabs; updates to hidden on the legacy brief are harmless.
  const tacticView = document.createElement("div"); tacticView.append(brief);
  const empty = document.createElement("p"); empty.className = "dashboard-plan-empty";
  empty.textContent = "识别到双方英雄后，自动生成本局打法。"; tacticView.append(empty);
  $("loading-jump").addEventListener("click", openPlan);
  const skillsView = document.createElement("div"); skillsView.append(vision);
  const actions = document.createElement("div"); actions.className = "dashboard-skill-actions";
  actions.append(button("技能资料 / 登记", openReference), button("查看倒计时", openTimers), button("识别详情", openVision));
  vision.append(actions);
  const views = [rosterView, tacticView, skillsView];
  const switches = [];
  function select(index) {
    views.forEach((v, i) => { v.hidden = i !== index; });
    switches.forEach((b, i) => { b.setAttribute("aria-selected", String(i === index)); b.tabIndex = i === index ? 0 : -1; });
  }
  ["双方阵容", "本局打法", "识别与技能"].forEach((label, index) => {
    const b = button(label, () => select(index)); b.setAttribute("role", "tab");
    b.addEventListener("keydown", e => {
      if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
      e.preventDefault(); const next = (index + (e.key === "ArrowRight" ? 1 : 2)) % 3;
      select(next); switches[next].focus();
    });
    switches.push(b); tabs.append(b);
    views[index].classList.add("dashboard-view"); views[index].setAttribute("role", "tabpanel");
  });
  deck.append(tabs, ...views); plan.before(deck); plan.hidden = true;
  select(0);
  // Keep the old targets mounted for existing rendering code without duplicate plans.
  $("bp-results").hidden = true;
  const nav = pick(".rail nav"); pick(".topbar").prepend(nav);
  for (const id of ["page-game", "page-review", "page-knowledge"]) {
    const page = $(id); const show = modal(({"page-game":"局中观察", "page-review":"赛后复盘", "page-knowledge":"英雄知识库"})[id], [page]);
    pick(`[data-page="${id.slice(5)}"]`).addEventListener("click", show);
  }
  pick('[data-page="bp"]').addEventListener("click", () => select(0));
}
window.addEventListener("gameplan-ready", () => setTimeout(initializeDashboard, 0), { once: true });
