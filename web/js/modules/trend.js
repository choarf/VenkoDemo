// Multi-sensor line chart with one y axis per unit; shared by the Test HMI and History views.
import { el, storageGet, storageSet } from "./util.js";

const css = (v) => getComputedStyle(document.body).getPropertyValue(v).trim();
const SVG = "http://www.w3.org/2000/svg";

/** Sensor selector: a button "Sensores (n) · ..." opening checkboxes grouped like the overview.
 *  onChange runs when the panel closes with a different selection. Returns () => selected keys. */
export function sensorPicker(host, sensors, storeKey, defaults, onChange) {
  let selected = new Set(storageGet(storeKey, defaults).filter((k) => sensors.some((s) => s.key === k)));
  let before = null;
  const id = `${host.id}-panel`;
  const label = el("span");
  const chevron = document.createElementNS(SVG, "svg");
  chevron.setAttribute("width", "14"); chevron.setAttribute("height", "14"); chevron.setAttribute("viewBox", "0 0 14 14");
  chevron.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(SVG, "path");
  for (const [k, v] of Object.entries({ d: "M2 5 L7 10 L12 5", fill: "none", stroke: "currentColor", "stroke-width": "2", "stroke-linecap": "round", "stroke-linejoin": "round" })) p.setAttribute(k, v);
  chevron.append(p);
  const btn = el("button", { type: "button", class: "dd-btn", "aria-expanded": "false", "aria-controls": id }, label, chevron);

  const boxes = new Map();
  const groups = [...new Set(sensors.map((s) => s.group || "Sensores"))];
  const panel = el("div", { class: "dd-panel", id, hidden: true },
    el("div", { class: "dd-groups" }, groups.map((g) => el("fieldset", {}, el("legend", {}, g),
      sensors.filter((s) => (s.group || "Sensores") === g).map((s) => {
        const box = el("input", { type: "checkbox", onchange: () => { box.checked ? selected.add(s.key) : selected.delete(s.key); sync(); } });
        boxes.set(s.key, box);
        return el("label", {}, box, s.label);
      })))),
    el("div", { class: "dd-actions" },
      el("button", { type: "button", class: "ghost", onclick: () => { sensors.forEach((s) => selected.add(s.key)); sync(); } }, "Todos"),
      el("button", { type: "button", class: "ghost", onclick: () => { selected.clear(); sync(); } }, "Ninguno"),
      el("button", { type: "button", class: "btn", onclick: () => close(true) }, "Listo")));

  function sync() {
    const names = sensors.filter((s) => selected.has(s.key)).map((s) => s.label);
    label.replaceChildren(el("b", {}, `Sensores (${names.length})`), ` · ${names.length ? names.join(", ") : "ninguno"}`);
    for (const [k, b] of boxes) b.checked = selected.has(k);
  }
  function open() {
    before = [...selected].sort().join();
    panel.hidden = false;
    btn.setAttribute("aria-expanded", "true");
  }
  function close(focusBtn) {
    if (panel.hidden) return;
    panel.hidden = true;
    btn.setAttribute("aria-expanded", "false");
    if (focusBtn) btn.focus();
    if ([...selected].sort().join() !== before) {
      storageSet(storeKey, [...selected]);
      onChange([...selected]);
    }
  }
  btn.addEventListener("click", () => (panel.hidden ? open() : close(false)));
  document.addEventListener("click", (e) => { if (!host.contains(e.target)) close(false); });
  host.addEventListener("keydown", (e) => { if (e.key === "Escape") close(true); });

  host.className = "dd";
  host.replaceChildren(btn, panel);
  sync();
  return () => [...selected];
}

/** Warning/alarm lines for one axis: only when all its sensors share the same limits
 *  and the limit is close enough to the data to be worth showing. */
function limitLines(members, dataMax) {
  const s = members[0];
  if (!s || members.some((x) => x.min !== s.min || x.max !== s.max)) return [];
  const m = (s.max - s.min) * (s.warn_margin ?? 0.1);
  const show = (v) => dataMax === null || v <= dataMax * 1.4;
  const line = (value, color, text) => ({ value, color, width: 2, dashStyle: "Dash", zIndex: 3,
    label: { text, align: "right", x: -4, style: { color: css("--muted"), fontSize: "11px" } } });
  const lines = [];
  if (show(s.max)) lines.push(line(s.max, css("--bad"), `Alarma ${s.max}`), line(s.max - m, css("--warn"), `Advertencia ${+(s.max - m).toFixed(2)}`));
  if (s.min !== 0) lines.push(line(s.min, css("--bad"), `Alarma ${s.min}`), line(s.min + m, css("--warn"), `Advertencia ${+(s.min + m).toFixed(2)}`));
  return lines;
}

/** series: {key: [[t, avg, min, max], ...]} */
export function drawTrend(container, sensors, keys, series, { title = null } = {}) {
  const byKey = Object.fromEntries(sensors.map((s) => [s.key, s]));
  const units = [...new Set(keys.map((k) => byKey[k]?.unit ?? ""))];
  const yAxis = units.map((u, i) => {
    const members = keys.filter((k) => (byKey[k]?.unit ?? "") === u).map((k) => byKey[k]).filter(Boolean);
    const vals = members.flatMap((s) => (series[s.key] || []).map((p) => p[1])).filter((v) => typeof v === "number");
    const lines = limitLines(members, vals.length ? Math.max(...vals) : null);
    return { title: { text: u }, opposite: i % 2 === 1, plotLines: lines,
             softMax: lines.length ? Math.max(...lines.map((l) => l.value)) : undefined };
  });
  const chart = Highcharts.chart(container, {
    chart: { zooming: { type: "x" }, animation: false },
    title: { text: title },
    xAxis: { type: "datetime" },
    yAxis,
    legend: { enabled: true },
    tooltip: { shared: true, valueDecimals: 2 },
    lang: { noData: "Sin datos en el rango" },
    exporting: { enabled: true, buttons: { contextButton: { menuItems: ["downloadPNG", "downloadSVG"] } } },
    plotOptions: { series: { marker: { enabled: false }, animation: false } },
    series: keys.map((k) => {
      const s = byKey[k] || { label: k, unit: "" };
      return { id: k, name: `${s.label} (${s.unit})`, yAxis: units.indexOf(s.unit),
               data: (series[k] || []).map((p) => [p[0], p[1]]) };
    }),
  });
  return chart;
}

/** Append live values to an existing trend (ignores duplicates by time). */
export function appendLive(chart, live) {
  if (!chart || !live.rx_ms) return;
  for (const s of chart.series) {
    const r = live.values[s.options.id];
    const last = s.data[s.data.length - 1]?.x;
    if (r && r.status === "OK" && (last === undefined || live.rx_ms > last)) s.addPoint([live.rx_ms, r.val], false);
  }
  chart.redraw(false);
}
