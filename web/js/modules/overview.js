// Overview / free-run monitoring: one dial per sensor, KPIs, system data table, active alarms.
import { $, el, fillTable, fmtAge, fmtDateTime, fmtNum, KIND_LABEL } from "./util.js";

const css = (v) => getComputedStyle(document.body).getPropertyValue(v).trim();
const dials = new Map(); // key -> {chart, card, sensor}
let lastLive = null;
let sensorsCfg = [];

function axisRange(s) {
  const span = (s.max - s.min) || 1;
  return { lo: s.min - span * 0.1, hi: s.max + span * 0.1, span };
}

/** 'ok' | 'warn' | 'alarm' | 'err' | 'none' for a live reading. */
export function readingState(s, r) {
  if (!r) return "none";
  if (r.status !== "OK") return "err";
  if (r.alarm === "LOW" || r.alarm === "HIGH") return "alarm";
  const m = (s.max - s.min) * (s.warn_margin ?? 0.1);
  if (r.val < s.min + m || r.val > s.max - m) return "warn";
  return "ok";
}

function stateColor(st) {
  return { ok: css("--accent"), warn: css("--warn"), alarm: css("--bad"), err: css("--muted"), none: css("--muted") }[st];
}

function dialOptions(s) {
  const { lo, hi, span } = axisRange(s);
  const w = span * (s.warn_margin ?? 0.1);
  const band = (from, to, color) => ({ from, to, color, innerRadius: "102%", outerRadius: "110%" });
  return {
    chart: { type: "solidgauge", backgroundColor: "transparent", spacing: [8, 4, 18, 4] },
    title: { text: s.label, style: { fontSize: "15px", fontWeight: "bold", color: css("--fg") } },
    pane: {
      startAngle: -120, endAngle: 120, center: ["50%", "62%"], size: "100%",
      background: { shape: "arc", innerRadius: "75%", outerRadius: "100%", backgroundColor: css("--gauge-track"), borderWidth: 0 },
    },
    tooltip: { enabled: false },
    yAxis: {
      min: lo, max: hi, lineWidth: 0, tickWidth: 0, minorTickInterval: null, labels: { enabled: false },
      plotBands: [band(lo, s.min, css("--bad")), band(s.min, s.min + w, css("--warn")),
                  band(s.max - w, s.max, css("--warn")), band(s.max, hi, css("--bad"))],
    },
    plotOptions: { solidgauge: { innerRadius: "75%", dataLabels: { y: -22, borderWidth: 0, useHTML: false } } },
    credits: { enabled: false },
    exporting: { enabled: false },
    series: [{
      name: s.label, data: [{ y: lo, color: css("--muted"), txt: "—" }],
      dataLabels: {
        formatter() {
          return `<span style="font-size:24px;color:${css("--fg")}">${this.point.options.txt}</span><br/>` +
                 `<span style="font-size:12px;color:${css("--muted")}">${s.unit}</span>`;
        },
      },
    }],
  };
}

function buildDials() {
  const host = $("dials");
  host.replaceChildren();
  dials.forEach((d) => d.chart.destroy());
  dials.clear();
  const groups = [...new Set(sensorsCfg.map((s) => s.group || ""))];
  for (const g of groups) {
    if (g) host.append(el("div", { class: "group-title" }, g));
    const grid = el("div", { class: "gauges" });
    host.append(grid);
    for (const s of sensorsCfg.filter((x) => (x.group || "") === g)) {
      const box = el("div", { style: "height:180px" });
      const card = el("div", { class: "card gauge" }, box, el("div", { class: "lim" }, `Límites ${s.min} – ${s.max} ${s.unit}`));
      grid.append(card);
      dials.set(s.key, { chart: Highcharts.chart(box, dialOptions(s)), card, sensor: s });
    }
  }
}

function updateDials(live) {
  for (const [key, d] of dials) {
    const s = d.sensor;
    const r = live.values[key];
    const st = live.online ? readingState(s, r) : "none";
    const { lo, hi } = axisRange(s);
    const has = r && r.status === "OK" && typeof r.val === "number";
    const y = has ? Math.min(hi, Math.max(lo, r.val)) : lo;
    const txt = has ? fmtNum(r.val, s.decimals ?? 2) : r ? "ERR" : "—";
    d.chart.series[0].points[0].update({ y, color: stateColor(st), txt }, true, { duration: 400 });
    d.card.classList.toggle("alarm", st === "alarm");
    d.card.classList.toggle("err", st === "err");
  }
}

function updateKpis(live) {
  let ok = 0, alarm = 0, err = 0;
  for (const s of sensorsCfg) {
    const st = readingState(s, live.values[s.key]);
    if (st === "alarm") alarm++;
    else if (st === "err") err++;
    else if (st !== "none") ok++;
  }
  $("kpiOk").textContent = `${ok} / ${sensorsCfg.length}`;
  $("kpiAlarm").textContent = alarm;
  $("kpiErr").textContent = err;
  $("kpiAge").textContent = fmtAge(live.age_s);
}

function updateSystem(live, cfg) {
  const sys = live.system || {};
  const rows = [
    ["Gateway", sys.gateway || cfg.gateway.gateway_id],
    ["Estado", live.online ? "En línea" : "Sin datos recientes"],
    ["Última lectura", fmtDateTime(live.rx_ms)],
    ["Hora local gateway", sys.city_time || "—"],
    ["Plataforma", sys.platform_type || "—"],
    ["Sistema operativo", sys.os || "—"],
    ["IP", sys.ip_address || "—"],
    ["CPU", sys.cpu_load_percent != null ? `${fmtNum(sys.cpu_load_percent, 1)} %` : "—"],
    ["RAM", sys.ram_usage_percent != null ? `${fmtNum(sys.ram_usage_percent, 1)} %` : "—"],
    ["Disco", sys.disk_usage_percent != null ? `${fmtNum(sys.disk_usage_percent, 1)} %` : "—"],
    ["Intervalo de muestreo", `${cfg.gateway.poll_interval} s`],
    ["Reporte de sistema", fmtDateTime(sys.rx_ms)],
  ];
  const t = $("systemTable");
  t.replaceChildren(...rows.map(([k, v]) => el("tr", {}, el("td", {}, k), el("td", {}, v))));
}

export function alarmRows(live) {
  return live.alarms.map((a) => [
    a.label,
    { text: KIND_LABEL[a.kind] || a.kind, class: `k-${a.kind}` },
    { text: a.val != null ? `${fmtNum(a.val)} ${a.unit}` : "—", class: "num" },
    `${a.min} – ${a.max}`,
  ]);
}

export function initOverview(cfg) {
  sensorsCfg = cfg.sensors;
  buildDials();
  document.addEventListener("themechange", () => { buildDials(); if (lastLive) updateDials(lastLive); });
}

export function onLive(live, cfg) {
  lastLive = live;
  updateDials(live);
  updateKpis(live);
  updateSystem(live, cfg);
  fillTable($("activeAlarmsMini"), ["Sensor", "Tipo", "Valor", "Límites"], alarmRows(live), { empty: "Sin alarmas activas" });
}
