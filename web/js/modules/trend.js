// Multi-sensor line chart with one y axis per unit; shared by the Test HMI and History views.
import { el, storageGet, storageSet } from "./util.js";

export function sensorPicker(host, sensors, storeKey, defaults, onChange) {
  let selected = new Set(storageGet(storeKey, defaults).filter((k) => sensors.some((s) => s.key === k)));
  host.replaceChildren();
  for (const s of sensors) {
    const chip = el("button", { type: "button", class: "chip" + (selected.has(s.key) ? " on" : "") }, s.label);
    chip.addEventListener("click", () => {
      selected.has(s.key) ? selected.delete(s.key) : selected.add(s.key);
      chip.classList.toggle("on");
      storageSet(storeKey, [...selected]);
      onChange([...selected]);
    });
    host.append(chip);
  }
  return () => [...selected];
}

/** series: {key: [[t, avg, min, max], ...]} */
export function drawTrend(container, sensors, keys, series, { title = null } = {}) {
  const byKey = Object.fromEntries(sensors.map((s) => [s.key, s]));
  const units = [...new Set(keys.map((k) => byKey[k]?.unit ?? ""))];
  const chart = Highcharts.chart(container, {
    chart: { zooming: { type: "x" }, animation: false },
    title: { text: title },
    xAxis: { type: "datetime" },
    yAxis: units.map((u, i) => ({ title: { text: u }, opposite: i % 2 === 1 })),
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
