"use strict";
async function loadReport() {
  const reportId = location.pathname.split("/").filter(Boolean).pop();
  if (!/^[a-f0-9]{32}$/.test(reportId)) throw new Error("战报编号无效");
  const response = await fetch("/api/reports/" + reportId);
  if (!response.ok) throw new Error("战报不存在或暂时无法读取");
  const report = await response.json();
  document.getElementById("report-status").textContent = (report.is_demo ? "示例战报 / 非真实对局 · " : "") + report.result.result + " · " + (report.result.hero || "本局复盘") + " · 置信度 " + report.confidence;
  const poster = document.getElementById("poster");
  poster.src = report.poster_url;
  poster.hidden = false;
  const download = document.getElementById("download-poster");
  download.href = report.poster_url;
  download.hidden = false;
  for (const section of report.sections) {
    const title = document.createElement("h3");
    title.textContent = section.title;
    const text = document.createElement("p");
    text.textContent = section.text;
    document.getElementById("accessible-review").append(title, text);
  }
}
loadReport().catch((error) => { document.getElementById("report-status").textContent = error.message; });
