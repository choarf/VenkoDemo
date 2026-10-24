// History / query view: time range + sensors -> chart, Excel/CSV export.
import { api } from "./api.js";
import { drawTrend, sensorPicker } from "./trend.js";
import { $, el, fillTable, fmtNum, fromLocalInput, toLocalInput } from "./util.js";

let cfg, chart = null, selected;

function range() {
  const from = fromLocalInput($("histFrom").value), to = fromLocalInput($("histTo").value);
  if (!(to > from)) throw new Error("El rango no es válido");
  return { from, to };
}

function setHours(h) {
  const to = Date.now();
  $("histTo").value = toLocalInput(to);
  $("histFrom").value = toLocalInput(to - h * 3600_000);
}

async function load() {
  const msg = $("histMsg");
  const keys = selected();
  if (!keys.length) { msg.textContent = "Selecciona al menos un sensor."; return; }
  $("histLoad").disabled = true;
  msg.textContent = "Consultando…";
  try {
    const { from, to } = range();
    const [res] = await Promise.all([api.history(from, to, keys), loadStats(from, to, keys)]);
    if (chart) chart.destroy();
    chart = drawTrend($("histChart"), cfg.sensors, keys, res.series);
    const n = Object.values(res.series).reduce((a, s) => a + s.length, 0);
    msg.textContent = `${n} puntos · promedio cada ${Math.round(res.bucket_ms / 1000)} s · horas en ${cfg.gateway.city}`;
  } catch (err) {
    msg.textContent = `Error: ${err.message}`;
  } finally { $("histLoad").disabled = false; }
}

async function loadStats(from, to, keys) {
  const byKey = Object.fromEntries(cfg.sensors.map((s) => [s.key, s]));
  try {
    const { stats } = await api.historyStats(from, to, keys);
    fillTable($("histStats"), ["Sensor", "Mínimo", "Promedio", "Máximo", "Último", "Límites", "Alertas"],
      stats.filter((r) => byKey[r.k]).map((r) => {
        const s = byKey[r.k], d = s.decimals ?? 2, u = (v) => (v == null ? "—" : `${fmtNum(v, d)} ${s.unit}`);
        const al = r.alarm ? ["alarm", `${r.alarm} lecturas en alarma`] : r.warn ? ["warn", `${r.warn} lecturas en advertencia`]
          : r.err ? ["err", `${r.err} errores de lectura`] : ["ok", "Sin alertas"];
        return [s.label, { text: u(r.min), class: "num" }, { text: u(r.avg), class: "num" }, { text: u(r.max), class: "num" },
          { text: u(r.last), class: "num" }, `${s.min} – ${s.max} ${s.unit}`, { node: el("span", { class: `tag ${al[0]}` }, al[1]) }];
      }), { empty: "Sin lecturas en el rango" });
  } catch (err) {
    fillTable($("histStats"), ["Error"], [[err.message]]);
  }
}

async function exportData(format) {
  const msg = $("histMsg");
  msg.textContent = "Generando archivo…";
  try {
    const { from, to } = range();
    const res = await api.exportUrl(from, to, selected(), format);
    msg.textContent = `${res.rows} filas exportadas.`;
    window.open(res.url, "_blank", "noopener");
  } catch (err) { msg.textContent = `Error: ${err.message}`; }
}

export function initHistory(config) {
  cfg = config;
  selected = sensorPicker($("histPicker"), cfg.sensors, "venko.histSensors", cfg.sensors.slice(0, 3).map((s) => s.key), () => {});
  setHours(24);
  document.querySelectorAll("#view-history [data-hours]").forEach((b) =>
    b.addEventListener("click", () => { setHours(Number(b.dataset.hours)); load(); }));
  $("histLoad").addEventListener("click", load);
  $("histXlsx").addEventListener("click", () => exportData("xlsx"));
  $("histCsv").addEventListener("click", () => exportData("csv"));
}

export function onShow() { if (!chart) load(); }
