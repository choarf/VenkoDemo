// Light/dark theme (V6 "Fondo" toggle) applied to CSS and to every Highcharts chart.
import { storageGet, storageSet } from "./util.js";

const css = (v) => getComputedStyle(document.body).getPropertyValue(v).trim();

export function chartTheme() {
  const fg = css("--fg"), muted = css("--muted"), line = css("--line");
  return {
    chart: { backgroundColor: "transparent", style: { fontFamily: "Segoe UI, system-ui, sans-serif" } },
    title: { style: { color: fg } },
    legend: { itemStyle: { color: fg }, itemHoverStyle: { color: muted } },
    xAxis: { labels: { style: { color: muted } }, lineColor: line, tickColor: line, gridLineColor: line },
    yAxis: { labels: { style: { color: muted } }, gridLineColor: line, title: { style: { color: muted } } },
    credits: { enabled: false },
  };
}

function apply() {
  const t = chartTheme();
  Highcharts.setOptions(t);
  Highcharts.charts.forEach((c) => {
    if (!c) return;
    c.update({ chart: t.chart, title: t.title, legend: t.legend }, false, false, false);
    c.xAxis.forEach((a) => a.update(t.xAxis, false));
    c.yAxis.forEach((a) => a.update({ labels: t.yAxis.labels, gridLineColor: t.yAxis.gridLineColor }, false));
    c.redraw(false);
  });
  document.dispatchEvent(new CustomEvent("themechange"));
}

export function initTheme(button) {
  document.body.classList.toggle("dark", storageGet("venko.dark", true));
  Highcharts.setOptions(chartTheme());
  button.addEventListener("click", () => {
    const dark = !document.body.classList.contains("dark");
    document.body.classList.toggle("dark", dark);
    storageSet("venko.dark", dark);
    apply();
  });
}
