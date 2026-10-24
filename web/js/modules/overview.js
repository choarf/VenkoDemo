// Overview / free-run monitoring: one dial per sensor, KPIs, system data table, active alarms.
import { summaryRows } from "./system.js";
import { $, el, fillTable, fmtAge, fmtNum, KIND_LABEL } from "./util.js";

const SVG = "http://www.w3.org/2000/svg";
const dials = new Map(); // key -> {sensor, card, tag, num, fill, needle, marker}
let sensorsCfg = [];

const STATE_TEXT = { ok: "Normal", warn: "Advertencia", alarm: "Alarma", err: "Error de lectura", none: "Sin datos" };

/** Dial scale: the limits plus 10 % of the range on each side (out-of-range zone). */
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

// Half-circle dial in a 200x118 box, centre (100, 100); t = 0 (left) .. 1 (right).
function pt(r, t) {
  const a = Math.PI * (1 - t);
  return [100 + r * Math.cos(a), 100 - r * Math.sin(a)];
}
function arc(r, t1, t2) {
  if (t2 - t1 < 0.0005) return "M0 0";
  const [x1, y1] = pt(r, t1), [x2, y2] = pt(r, t2);
  return `M${x1.toFixed(2)} ${y1.toFixed(2)} A${r} ${r} 0 0 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}
function svg(tag, attrs) {
  const n = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
}

function scale(s) {
  const { lo, hi, span } = axisRange(s);
  const tot = hi - lo, w = span * (s.warn_margin ?? 0.1) / tot;
  return { lo, hi, tot, tMin: (s.min - lo) / tot, tMax: (s.max - lo) / tot, w };
}

function sensorCard(s) {
  const { tMin, tMax, w } = scale(s);
  const tag = el("span", { class: "tag none" }, STATE_TEXT.none);
  const num = el("span", { class: "num" }, "—");

  const dial = svg("svg", { viewBox: "0 0 200 118", role: "img", "aria-label": `${s.label}, límites ${s.min} a ${s.max} ${s.unit}` });
  const zone = (cls, a, b) => dial.append(svg("path", { d: arc(84, a, b), class: cls, fill: "none", "stroke-width": 8 }));
  zone("z-bad", 0, tMin); zone("z-warn", tMin, tMin + w); zone("z-mid", tMin + w, tMax - w);
  zone("z-warn", tMax - w, tMax); zone("z-bad", tMax, 1);
  dial.append(svg("path", { d: arc(68, 0, 1), class: "track", fill: "none", "stroke-width": 16 }));
  const fill = svg("path", { d: "M0 0", class: "fill", fill: "none", "stroke-width": 16 });
  const needle = svg("line", { x1: 100, y1: 100, x2: 100, y2: 100, class: "needle", "stroke-width": 3, "stroke-linecap": "round" });
  dial.append(fill, needle, svg("circle", { cx: 100, cy: 100, r: 6, class: "hub" }));
  // Limit labels under the arc ends, anchored inward so wide numbers (1000) stay inside the box.
  for (const [v, t, anchor] of [[s.min, tMin, "start"], [s.max, tMax, "end"]]) {
    const x = Math.min(198, Math.max(2, pt(92, t)[0] + (anchor === "start" ? -6 : 6)));
    const label = svg("text", { x: x.toFixed(1), y: 116, "text-anchor": anchor });
    label.textContent = String(v);
    dial.append(label);
  }

  const zones = el("div", { class: "zones" });
  for (const [cls, f] of [["b", tMin], ["w", w], ["m", tMax - w - tMin - w], ["w", w], ["b", 1 - tMax]]) {
    zones.append(el("div", { class: cls, style: `flex:0 0 ${(f * 100).toFixed(2)}%` }));
  }
  const marker = el("div", { class: "mk", style: "left:0" });

  const card = el("div", { class: "scard none" },
    el("div", { class: "hd" }, el("span", { class: "lbl", title: s.label }, s.label), tag),
    el("div", { class: "dial" }, dial),
    el("div", { class: "val" }, num, el("span", { class: "unit" }, s.unit)),
    el("div", { class: "bar", "aria-hidden": "true" }, zones, marker),
    el("span", { class: "lim" }, `Límites ${s.min} – ${s.max} ${s.unit}`));
  dials.set(s.key, { sensor: s, card, tag, num, fill, needle, marker, dial });
  return card;
}

function buildDials() {
  const host = $("dials");
  host.replaceChildren();
  dials.clear();
  const groups = [...new Set(sensorsCfg.map((s) => s.group || ""))];
  for (const g of groups) {
    const members = sensorsCfg.filter((x) => (x.group || "") === g);
    host.append(el("section", { "aria-label": g || "Sensores" },
      g ? el("h2", { class: "group-title" }, g) : null,
      el("div", { class: "sgrid" }, members.map(sensorCard))));
  }
}

function updateDials(live) {
  for (const d of dials.values()) {
    const s = d.sensor;
    const r = live.values[s.key];
    const st = live.online ? readingState(s, r) : "none";
    const has = r && r.status === "OK" && typeof r.val === "number";
    const { lo, tot } = scale(s);
    const t = has ? Math.max(0, Math.min(1, (r.val - lo) / tot)) : 0;
    const txt = has ? fmtNum(r.val, s.decimals ?? 2) : r ? "ERR" : "—";
    d.card.className = `scard ${st}`;
    d.tag.className = `tag ${st}`;
    d.tag.textContent = STATE_TEXT[st];
    d.num.textContent = txt;
    d.fill.setAttribute("d", has ? arc(68, 0, t) : "M0 0");
    const [nx, ny] = pt(62, t);
    d.needle.setAttribute("x2", nx.toFixed(1));
    d.needle.setAttribute("y2", ny.toFixed(1));
    d.marker.style.left = `calc(${(t * 100).toFixed(2)}% - 2px)`;
    d.dial.setAttribute("aria-label", `${s.label}: ${txt} ${s.unit}, ${STATE_TEXT[st]}, límites ${s.min} a ${s.max} ${s.unit}`);
  }
}

// ---- Process indicators (config "derived"; same fixed operations as backend/common/derived.py)
const derivedCards = new Map(); // key -> {d, card, tag, num}

function derivedValue(d, vals) {
  const a = vals[d.a];
  if (a === undefined || a === null) return null;
  if (d.op === "ratio") { const b = vals[d.b]; return b ? (a / b) * (d.scale ?? 1) : null; }
  if (d.op === "diff") { const b = vals[d.b]; return b === undefined || b === null ? null : a - b; }
  const minus = d.minus.map((k) => vals[k]);
  return minus.some((m) => m === undefined || m === null) ? null : a - minus.reduce((x, y) => x + y, 0);
}

function derivedState(d, v) {
  if (v === null) return "none";
  if (v < d.min || v > d.max) return "alarm";
  const m = (d.max - d.min) * (d.warn_margin ?? 0.1);
  return v < d.min + m || v > d.max - m ? "warn" : "ok";
}

function buildDerived(cfg) {
  const list = cfg.derived || [];
  $("derivedCard").hidden = !list.length;
  if (!list.length) return;
  const short = (k) => (cfg.sensors.find((s) => s.key === k)?.label || k).split(" ")[0];
  const how = (d) => d.op === "ratio" ? `${short(d.a)} ÷ ${short(d.b)}${d.scale && d.scale !== 1 ? ` × ${d.scale}` : ""}`
    : d.op === "diff" ? `${short(d.a)} − ${short(d.b)}` : [d.a, ...d.minus].map(short).join(" − ");
  $("derivedGrid").replaceChildren(...list.map((d) => {
    const tag = el("span", { class: "tag none" }, STATE_TEXT.none);
    const num = el("b", {}, "—");
    const card = el("div", { class: "icard none" },
      el("div", { class: "hd" }, el("span", { class: "lbl" }, d.label), tag),
      el("div", { class: "ival" }, num, el("span", { class: "unit" }, d.unit)),
      el("span", { class: "muted small" }, `${how(d)} · límites ${d.min} – ${d.max} ${d.unit}`));
    derivedCards.set(d.key, { d, card, tag, num });
    return card;
  }));
  $("derivedNote").textContent = list.some((d) => d.provisional) ? "Límites provisionales, por confirmar" : "Calculados con los sensores";
}

function updateDerived(live) {
  const vals = {};
  for (const [k, r] of Object.entries(live.values)) if (r && r.status === "OK" && typeof r.val === "number") vals[k] = r.val;
  for (const { d, card, tag, num } of derivedCards.values()) {
    const v = live.online ? derivedValue(d, vals) : null;
    const st = derivedState(d, v);
    card.className = `icard ${st}`;
    tag.className = `tag ${st}`;
    tag.textContent = STATE_TEXT[st];
    num.textContent = v === null ? "—" : fmtNum(v, d.decimals ?? 1);
  }
}

function updateKpis(live) {
  let ok = 0, warn = 0, alarm = 0, err = 0;
  for (const s of sensorsCfg) {
    const st = readingState(s, live.values[s.key]);
    if (st === "alarm") alarm++;
    else if (st === "warn") warn++;
    else if (st === "err") err++;
    else if (st === "ok") ok++;
  }
  $("kpiOk").textContent = `${ok} / ${sensorsCfg.length}`;
  $("kpiWarn").textContent = warn;
  $("kpiAlarm").textContent = alarm;
  $("kpiErr").textContent = err;
  $("kpiAge").textContent = fmtAge(live.age_s);
  $("kpiWarnBox").className = `kpi${warn ? " warn" : ""}`;
  $("kpiAlarmBox").className = `kpi${alarm ? " alarm" : ""}`;
  $("kpiErrBox").className = `kpi${err ? " err" : ""}`;
}

function updateSystem(live) {
  $("systemTable").replaceChildren(...summaryRows(live).map(([k, v]) => el("tr", {}, el("td", {}, k), el("td", {}, v))));
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
  buildDerived(cfg);
}

export function onLive(live, cfg) {
  updateDials(live);
  updateDerived(live);
  updateKpis(live);
  updateSystem(live);
  fillTable($("activeAlarmsMini"), ["Sensor", "Tipo", "Valor", "Límites"], alarmRows(live), { empty: "Sin alarmas activas" });
}
