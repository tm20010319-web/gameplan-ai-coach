"use strict";

(() => {
  let automatic = false;
  const layout = () => {
    const plan = document.querySelector(".loading-plan"), roster = document.querySelector(".roster-panel");
    const status = document.getElementById("roster-auto-status"), results = document.getElementById("bp-results");
    if (!plan || !roster || !status || !results || plan.dataset.integrated) return;
    plan.dataset.integrated = "true"; plan.classList.add("integrated-plan");
    const head = plan.querySelector(".panel-head h2"); if (head) head.textContent = "自动战术方案";
    const block = document.createElement("div"); block.className = "integrated-roster";
    block.append(roster, status, results); plan.insertBefore(block, plan.querySelector(".loading-setup"));
    document.getElementById("page-bp")?.setAttribute("hidden", "");
  };
  layout();
  const labels = () => document.querySelectorAll(".roster-panel .roster-label > span");
  window.gameplanRoster = {
    sync(a, b, names, source) {
      automatic = source === "vision_draft";
      setRoster("ally", a); setRoster("enemy", b); renderRosters();
      labels().forEach((element, index) => { element.textContent = "● " + names[index] + "阵容"; });
      byId("roster-count").textContent = `${a.length + b.length} / 10 位英雄${automatic ? "已识别 · 视觉草稿，请核对" : "已选择"}`;
      byId("bp-results").replaceChildren();
      byId("roster-auto-status").textContent = a.length && b.length ? "正在自动生成战术方案…" : "已填入可见英雄，等待另一组阵容";
    },
    plan(html) {
      byId("bp-results").innerHTML = html;
      byId("roster-auto-status").textContent = "战术方案已自动更新";
    },
    status(message) { byId("roster-auto-status").textContent = message; },
    clear() {
      if (!automatic) return;
      automatic = false;
      setRoster("ally", []); setRoster("enemy", []); renderRosters();
      byId("bp-results").replaceChildren();
      byId("roster-auto-status").textContent = "等待新画面识别阵容";
    },
    editing() {
      automatic = false;
      byId("bp-results").replaceChildren();
      byId("roster-auto-status").textContent = "已切换手动核对，点击生成战术方案";
      window.gameplanLoading?.editRoster();
    },
  };
})();
