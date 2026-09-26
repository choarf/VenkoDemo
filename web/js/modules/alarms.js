// Alarms view: active alarms (live) and alarm-episode history (Athena) with a drill-down chart.
import { api } from "./api.js";
import { alarmRows } from "./overview.js";
import { drawTrend } from "./trend.js";
import { $, fillTable, fmtDateTime, fmtDuration, fmtNum, KIND_LABEL } from "./util.js";

let cfg, chart = null;

async function loadHistory() {
  const hours = Number($("alarmRange").value);
  const to = Date.now(), from = to - hours * 3600_000;
  const btn = $("alarmLoad");
  btn.disabled = true;
  try {
    const { episodes } = await api.alarms(from, to);
    const poll = cfg.gateway.poll_interval * 1000;
    fillTable($("alarmHistory"), ["Inicio", "Fin", "Duración", "Sensor", "Tipo", "Pico", "Muestras"],
      episodes.map((e) => [fmtDateTime(e.start), fmtDateTime(e.end), fmtDuration(e.end - e.start + poll), e.label,
        { text: KIND_LABEL[e.kind] || e.kind, class: `k-${e.kind}` },
        { text: e.peak != null ? `${fmtNum(e.peak)} ${e.unit}` : "—", class: "num" }, { text: e.samples, class: "num" }]),
      { empty: "Sin alarmas en el periodo", onRow: (i) => drill(episodes[i]) });
  } catch (err) {
    fillTable($("alarmHistory"), ["Error"], [[err.message]]);
  } finally { btn.disabled = false; }
}

/** Show the sensor around the episode (episode ± its own length, min 30 min each side). */
async function drill(e) {
  const pad = Math.max(30 * 60_000, e.end - e.start);
  const box = $("alarmChart");
  box.hidden = false;
  const { series } = await api.history(e.start - pad, e.end + pad, [e.k]);
  if (chart) chart.destroy();
  chart = drawTrend(box, cfg.sensors, [e.k], series, { title: `${e.label} – ${KIND_LABEL[e.kind] || e.kind}` });
  const s = cfg.sensors.find((x) => x.key === e.k);
  chart.xAxis[0].addPlotBand({ from: e.start, to: e.end, color: "rgba(213,0,0,.15)" });
  if (s) [s.min, s.max].forEach((v) => chart.yAxis[0].addPlotLine({ value: v, color: "#D50000", dashStyle: "Dash", width: 1 }));
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

export function initAlarms(config) {
  cfg = config;
  $("alarmLoad").addEventListener("click", loadHistory);
}

export function onShow() { if (!$("alarmHistory").children.length) loadHistory(); }

export function onLive(live) {
  fillTable($("activeAlarms"), ["Sensor", "Tipo", "Valor", "Límites"], alarmRows(live), { empty: "Sin alarmas activas" });
  const badge = $("navAlarmCount");
  badge.hidden = !live.alarms.length;
  badge.textContent = live.alarms.length;
}
