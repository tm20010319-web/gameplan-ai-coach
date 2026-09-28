const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const {JSDOM} = require("../data/ui-check/node_modules/jsdom");
const catalog = JSON.parse(fs.readFileSync("resources/knowledge/lol_catalog.json", "utf8"));
const tick = () => new Promise(resolve => setImmediate(resolve));
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
function detail(id) {
  const hero = catalog.heroes.find(h => h.id === id);
  return {...catalog, ...hero, build_reference: {...hero.build_reference, items: hero.build_reference.item_ids.map(key => catalog.items[key])}};
}
function listing(ids = ["Annie", "Yasuo"]) {
  return {...catalog, total: catalog.heroes.length, matched: ids.length, roles: ["法师", "战士"], heroes: ids.map(id => detail(id))};
}
function setup(handler) {
  const dom = new JSDOM(fs.readFileSync("static/lol-knowledge.html", "utf8"), {url:"http://localhost/knowledge/lol", runScripts:"outside-only"});
  dom.window.fetch = handler;
  vm.runInContext(fs.readFileSync("static/lol-knowledge.js", "utf8"), dom.getInternalVMContext());
  return dom;
}
const ok = data => ({ok:true, json:async () => data});

test("renders skills, sources and build boundaries; handles search and empty result", async () => {
  const dom = setup(async url => {
    if (url.includes("?")) return ok(listing(new URL(url, "http://localhost").searchParams.get("q") ? [] : undefined));
    const data = detail("Annie"); data.skills[0].description = "<img src=x onerror=alert(1)>";
    return ok(data);
  });
  try {
    await tick(); const d = dom.window.document;
    assert.equal(d.getElementById("roster-count").textContent, "173");
    assert.match(d.getElementById("hero-detail").textContent, /130 \/ 115 \/ 100 秒/);
    assert.match(d.getElementById("hero-detail").textContent, /非官方推荐/);
    assert.match(d.getElementById("hero-detail").textContent, /卢登/);
    assert.equal(d.querySelectorAll("img").length, 0);
    assert.equal(d.querySelector(".hero-button").getAttribute("aria-pressed"), "true");
    const search = d.getElementById("hero-search"); search.value = "不存在"; search.dispatchEvent(new dom.window.Event("input"));
    assert.doesNotMatch(d.getElementById("hero-detail").textContent, /提伯斯/);
    await pause(220);
    assert.match(d.getElementById("knowledge-status").textContent, /没有匹配/);
    assert.equal(d.querySelectorAll(".hero-button").length, 0);
  } finally {dom.window.close();}
});

test("late hero response cannot overwrite a newer selection", async () => {
  let release;
  const dom = setup(async url => url.includes("?") ? ok(listing()) : url.endsWith("Annie") ? new Promise(resolve => {release = () => resolve(ok(detail("Annie")));}) : ok(detail("Yasuo")));
  try {
    await tick(); const d = dom.window.document;
    d.querySelector('[data-hero="Yasuo"]').click(); await tick();
    release(); await tick();
    assert.equal(d.querySelector("#hero-detail h2").textContent, "亚索");
    assert.equal(d.getElementById("hero-detail").getAttribute("aria-busy"), "false");
  } finally {dom.window.close();}
});

test("failed loading offers a working retry and role filter is sent to server", async () => {
  let fail = true; const urls = [];
  const dom = setup(async url => {urls.push(url); if (fail) return {ok:false,status:503}; return ok(url.includes("?") ? listing() : detail("Annie"));});
  try {
    await tick(); const d = dom.window.document;
    assert.equal(d.getElementById("retry").hidden, false);
    fail = false; d.getElementById("retry").click(); await tick();
    assert.equal(d.querySelector("#hero-detail h2").textContent, "安妮");
    d.getElementById("role-filter").value = "法师";
    d.getElementById("role-filter").dispatchEvent(new dom.window.Event("change")); await tick();
    assert.ok(urls.some(url => new URL(url,"http://localhost").searchParams.get("role") === "法师"));
  } finally {dom.window.close();}
});

test("stale search response cannot restore a previous list", async () => {
  let release;
  const dom = setup(async url => {
    if (!url.includes("?")) return ok(detail(url.split("/").pop()));
    const q = new URL(url, "http://localhost").searchParams.get("q");
    if (q === "first") return new Promise(resolve => {release = () => resolve(ok(listing(["Annie"])));});
    return ok(listing(q === "second" ? ["Yasuo"] : undefined));
  });
  try {
    await tick(); const d = dom.window.document, input = d.getElementById("hero-search");
    input.value = "first"; input.dispatchEvent(new dom.window.Event("input")); await pause(220);
    input.value = "second"; input.dispatchEvent(new dom.window.Event("input")); await pause(220);
    release(); await tick();
    assert.equal(d.querySelector(".hero-button").dataset.hero, "Yasuo");
    assert.equal(d.querySelector("#hero-detail h2").textContent, "亚索");
  } finally {dom.window.close();}
});
