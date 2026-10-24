// Alarms view: active alarms (live), and for a range: totals, episodes per day, the episode
// table (alarms, warnings near a limit and read errors) with a drill-down chart, Excel export.
import { api } from "./api.js";
import { alarmRows } from "./overview.js";
import { drawTrend } from "./trend.js";
import { $, el, fillTable, fmtDateTime, fmtDuration, fmtNum, KIND_LABEL } from "./util.js";

const css = (v) => getComputedStyle(document.body).getPropertyValue(v).trim();
const CATEGORY = (kind) => (kind === "HIGH" || kind === "LOW" ? "alarm" : kind.startsWith("WARN_") ? "warn" : "err");
let cfg, chart = null, daysChart = null, hours = 24, loaded = false;

function kpi(label, value, cls = "") {
  return el("div", { class: `kpi ${cls}` }, el("span", {}, label), el("b", {}, value));
}

function drawDays(days) {
  if (daysChart) daysChart.destroy();
  const fmt = new Intl.DateTimeFormat("es-MX", { day: "2-digit", month: "short", timeZone: "UTC" });
  daysChart = Highcharts.chart($("alarmDays"), {
    chart: { type: "column", animation: false },
    title: { text: null },
    xAxis: { categories: days.map((d) => fmt.format(new Date(`${d.day}T12:00:00Z`))) },
    yAxis: { title: { text: "Eventos" }, allowDecimals: false, stackLabels: { enabled: true } },
    legend: { enabled: true },
    tooltip: { shared: true },
    exporting: { enabled: false },
    plotOptions: { column: { stacking: "normal", borderWidth: 0 } },
    series: [
      { name: "Alarmas", color: css("--bad"), data: days.map((d) => d.alarm) },
      { name: "Advertencias", color: css("--warn"), data: days.map((d) => d.warn) },
      { name: "Errores de lectura", color: css("--muted"), data: days.map((d) => d.err) },
    ],
  });
  // The card may still be settling its width (narrow screens): size the chart to it once laid out.
  requestAnimationFrame(() => daysChart?.reflow());
}

async function loadHistory() {
  const to = Date.now(), from = to - hours * 3600_000;
  $("alarmMsg").textContent = "Consultando…";
  try {
    const { episodes, summary } = await api.alarms(from, to);
    loaded = true;
    const t = summary.totals;
    $("alarmKpis").replaceChildren(
      kpi("Alarmas", t.alarm, t.alarm ? "alarm" : ""),
      kpi("Advertencias", t.warn, t.warn ? "warn" : ""),
      kpi("Errores de lectura", t.err, t.err ? "err" : ""),
      kpi("Sensor con más eventos", summary.top ? `${summary.top.label} (${summary.top.count})` : "—", "text"));
    drawDays(summary.days);
    const poll = cfg.gateway.poll_interval * 1000;
    fillTable($("alarmHistory"), ["Inicio", "Fin", "Duración", "Sensor", "Tipo", "Pico", "Límites", "Muestras"],
      episodes.map((e) => [fmtDateTime(e.start), fmtDateTime(e.end), fmtDuration(e.end - e.start + poll), e.label,
        { node: el("span", { class: `tag ${CATEGORY(e.kind)}` }, KIND_LABEL[e.kind] || e.kind) },
        { text: e.peak != null ? `${fmtNum(e.peak)} ${e.unit}` : "—", class: "num" },
        e.min != null ? `${e.min} – ${e.max} ${e.unit}` : "—", { text: e.samples, class: "num" }]),
      { empty: "Sin eventos en el periodo", onRow: (i) => drill(episodes[i]) });
    $("alarmMsg").textContent = episodes.length ? "Toca un evento para ver la gráfica del sensor en ese momento." : "";
  } catch (err) {
    $("alarmMsg").textContent = `Error: ${err.message}`;
  }
}

/** Show the sensor around the episode (episode ± its own length, min 30 min each side). */
async function drill(e) {
  const pad = Math.max(30 * 60_000, e.end - e.start);
  const box = $("alarmChart");
  box.hidden = false;
  const { series } = await api.history(e.start - pad, e.end + pad, [e.k]);
  if (chart) chart.destroy();
  chart = drawTrend(box, cfg.sensors, [e.k], series, { title: `${e.label} – ${KIND_LABEL[e.kind] || e.kind}` });
  const band = CATEGORY(e.kind) === "warn" ? "rgba(242,183,5,.18)" : "rgba(211,47,47,.15)";
  chart.xAxis[0].addPlotBand({ from: e.start, to: e.end, color: band });
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function exportXlsx() {
  const btn = $("alarmXlsx");
  btn.disabled = true;
  $("alarmMsg").textContent = "Generando archivo…";
  try {
    const to = Date.now(), from = to - hours * 3600_000;
    const res = await api.alarmsExport(from, to);
    $("alarmMsg").textContent = `${res.rows} eventos exportados.`;
    window.open(res.url, "_blank", "noopener");
  } catch (err) {
    $("alarmMsg").textContent = `Error: ${err.message}`;
  } finally { btn.disabled = false; }
}

export function initAlarms(config) {
  cfg = config;
  document.querySelectorAll("#alarmRanges [data-hours]").forEach((b) => b.addEventListener("click", () => {
    hours = Number(b.dataset.hours);
    document.querySelectorAll("#alarmRanges [data-hours]").forEach((x) => {
      x.classList.toggle("on", x === b);
      x.setAttribute("aria-pressed", String(x === b));
    });
    loadHistory();
  }));
  $("alarmXlsx").addEventListener("click", exportXlsx);
  document.addEventListener("themechange", () => { if (loaded) loadHistory(); });
}

export function onShow() { if (!loaded) loadHistory(); }

export function onLive(live) {
  fillTable($("activeAlarms"), ["Sensor", "Tipo", "Valor", "Límites"], alarmRows(live), { empty: "Sin alarmas activas" });
  const badge = $("navAlarmCount");
  badge.hidden = !live.alarms.length;
  badge.textContent = live.alarms.length;
}
