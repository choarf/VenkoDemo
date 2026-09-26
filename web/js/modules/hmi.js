// Test HMI: Start/Stop test events, elapsed clock, live trend, comments, recent tests.
import { api, openReport } from "./api.js";
import { appendLive, drawTrend, sensorPicker } from "./trend.js";
import { $, el, fillTable, fmtDateTime, fmtDuration, storageGet, storageSet } from "./util.js";

const STATUS = { RUNNING: "En curso", REPORT_PENDING: "Generando reporte…", DONE: "Reporte listo", REPORT_FAILED: "Error de reporte" };

let cfg, active = null, chart = null, pickSelected, confirmTimer = null, onChange = () => {};

function msg(text) { $("hmiMsg").textContent = text || ""; }

export function reportLinks(t) {
  if (t.status === "DONE" && t.report?.html) {
    return el("span", {},
      el("a", { href: "#", class: "link", onclick: (e) => { e.preventDefault(); openReport(t.report.html); } }, "HTML"), " · ",
      el("a", { href: "#", class: "link", onclick: (e) => { e.preventDefault(); openReport(t.report.xlsx); } }, "Excel"));
  }
  if (t.status === "REPORT_FAILED") {
    return el("a", { href: "#", class: "link", onclick: async (e) => {
      e.preventDefault(); await api.regenerateTest(t.id); refreshTests(); } }, "Reintentar");
  }
  return STATUS[t.status] || t.status;
}

export async function refreshTests() {
  const { tests } = await api.tests();
  fillTable($("testsTable"), ["Prueba", "Nombre", "Operador", "Inicio", "Duración", "Estado / reporte"],
    tests.slice(0, 15).map((t) => [t.id, t.name, t.operator, fmtDateTime(t.start_ms),
      fmtDuration((t.end_ms || Date.now()) - t.start_ms), { node: reportLinks(t) }]),
    { empty: "Aún no hay pruebas" });
  // Keep polling while a report is being generated.
  if (tests.some((t) => t.status === "REPORT_PENDING")) setTimeout(() => refreshTests().catch(() => {}), 8000);
  return tests;
}

async function loadTrend() {
  const keys = pickSelected();
  const to = Date.now();
  const from = active ? active.start_ms : to - 3600_000;
  let series = {};
  if (keys.length) {
    try { series = (await api.history(from, to, keys)).series; } catch (e) { msg(`Tendencia: ${e.message}`); }
  }
  if (chart) chart.destroy();
  chart = drawTrend($("testTrend"), cfg.sensors, keys, series,
    { title: active ? `Desde el inicio de ${active.name}` : "Última hora (monitoreo libre)" });
}

function renderActive() {
  $("startForm").hidden = !!active;
  $("runningBox").hidden = !active;
  $("commentScope").textContent = active ? `· ligados a ${active.id}` : "· monitoreo libre (últimas 24 h)";
  if (active) {
    $("runName").textContent = active.name;
    $("runOperator").textContent = active.operator;
    $("runStart").textContent = fmtDateTime(active.start_ms);
  }
  onChange(active);
}

export async function refreshComments() {
  const { comments } = await api.comments(active ? { test_id: active.id } : {});
  const labels = Object.fromEntries(cfg.sensors.map((s) => [s.key, s.label]));
  $("commentList").replaceChildren(...(comments.length ? comments.map((c) => el("li", {},
    el("div", { class: "meta" }, `${fmtDateTime(c.ts_ms)} · ${c.author} · ${labels[c.sensor] || "General"}`),
    el("div", {}, c.text))) : [el("li", { class: "empty" }, "Sin comentarios")]));
}

export async function setActive(test) {
  const changed = (test?.id || null) !== (active?.id || null);
  active = test;
  if (changed) {
    renderActive();
    await Promise.allSettled([loadTrend(), refreshComments(), refreshTests()]);
  }
}

function resetStop() {
  clearTimeout(confirmTimer);
  $("stopBtn").classList.remove("confirm");
  $("stopBtn").textContent = "■ Detener prueba";
  $("stopCancel").hidden = true;
}

export function initHmi(config, { onActiveChange }) {
  cfg = config;
  onChange = onActiveChange;
  const defaults = cfg.sensors.slice(0, 4).map((s) => s.key);
  pickSelected = sensorPicker($("trendPicker"), cfg.sensors, "venko.trendSensors", defaults, () => loadTrend());

  const sel = $("commentSensor");
  cfg.sensors.forEach((s) => sel.append(el("option", { value: s.key }, s.label)));
  const author = storageGet("venko.author", "");
  $("commentForm").elements.author.value = author;
  $("startForm").elements.operator.value = author;

  $("startForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, btn = f.querySelector("button");
    btn.disabled = true;
    try {
      const { test } = await api.startTest({ name: f.elements.name.value, operator: f.elements.operator.value, notes: f.elements.notes.value });
      storageSet("venko.author", f.elements.operator.value);
      f.reset();
      f.elements.operator.value = storageGet("venko.author", "");
      msg(`Prueba ${test.id} iniciada.`);
      await setActive(test);
    } catch (err) {
      msg(`No se pudo iniciar: ${err.message}`);
    } finally { btn.disabled = false; }
  });

  // Two-step stop instead of a blocking confirm() dialog.
  $("stopBtn").addEventListener("click", async () => {
    const btn = $("stopBtn");
    if (!btn.classList.contains("confirm")) {
      btn.classList.add("confirm");
      btn.textContent = "¿Confirmar detener?";
      $("stopCancel").hidden = false;
      confirmTimer = setTimeout(resetStop, 6000);
      return;
    }
    resetStop();
    btn.disabled = true;
    try {
      const { test } = await api.stopTest(active.id, storageGet("venko.author", ""));
      msg(`Prueba ${test.id} detenida. El reporte se está generando; aparecerá en "Pruebas recientes" y en Reportes.`);
      await setActive(null);
    } catch (err) {
      msg(`No se pudo detener: ${err.message}`);
    } finally { btn.disabled = false; }
  });
  $("stopCancel").addEventListener("click", resetStop);

  $("commentForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      await api.addComment({ text: f.elements.text.value, author: f.elements.author.value, sensor: f.elements.sensor.value, test_id: active?.id || "" });
      storageSet("venko.author", f.elements.author.value);
      f.elements.text.value = "";
      await refreshComments();
    } catch (err) { msg(`Comentario: ${err.message}`); }
  });

  setInterval(() => { if (active) $("runElapsed").textContent = fmtDuration(Date.now() - active.start_ms); }, 1000);
  renderActive();
  loadTrend();
  refreshComments().catch(() => {});
  refreshTests().catch(() => {});
}

export function onLive(live) { appendLive(chart, live); }
