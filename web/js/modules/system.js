// Sistema view: gateway and hardware data, CPU / RAM / disk against system_limits, connections.
// Fields the gateway added later (uptime_s, wifi, modbus, mqtt_since, oled_enabled) show "—"
// until that Pi runs the new code.
import { api } from "./api.js";
import { $, el, fmtAge, fmtDateTime, fmtNum } from "./util.js";

const STATS_TTL_MS = 5 * 60_000;
const RES = [["cpu", "CPU", "cpu_load_percent"], ["ram", "RAM", "ram_usage_percent"], ["disk", "Disco", "disk_usage_percent"]];
const STATE_TEXT = { ok: "Normal", warn: "Advertencia", alarm: "Alarma", none: "Sin datos" };
let cfg, stats = null, statsAt = 0, lastLive = null;

function fmtUptime(s) {
  if (s === null || s === undefined) return "—";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
}

function resState(name, v) {
  if (v === null || v === undefined) return "none";
  const [warn, alarm] = cfg.system_limits?.[name] || [80, 90];
  return v >= alarm ? "alarm" : v >= warn ? "warn" : "ok";
}

function kv(tableId, rows) {
  $(tableId).replaceChildren(...rows.map(([k, v]) => el("tr", {}, el("td", {}, k), el("td", {}, v ?? "—"))));
}

function kpi(label, value, sub, cls = "") {
  return el("div", { class: `kpi ${cls}` }, el("span", {}, label), el("b", {}, value), el("small", { class: "muted" }, sub));
}

function conn(name, status, ok, detail, extra) {
  return el("div", { class: "mcard" },
    el("div", { class: "mcard-h" }, el("b", {}, name), el("span", { class: `tag ${ok === null ? "none" : ok ? "ok" : "alarm"}` }, status)),
    el("span", { class: "muted small detail" }, detail || "—"),
    extra ? el("span", { class: "muted small" }, extra) : null);
}

function render() {
  const live = lastLive;
  if (!live) return;
  const sys = live.system || {};
  const sensors = cfg.sensors.length;
  const readOk = cfg.sensors.filter((s) => live.values[s.key]?.status === "OK").length;
  const sysAge = sys.rx_ms ? (live.now_ms - sys.rx_ms) / 1000 : null;

  $("sysKpis").replaceChildren(
    kpi("Estado", live.online ? "En línea" : "Sin datos", `Última lectura hace ${fmtAge(live.age_s)}`, live.online ? "" : "alarm"),
    kpi("Reporte de sistema", sysAge === null ? "—" : `hace ${fmtAge(sysAge)}`, `Cada ${cfg.gateway.system_interval ?? 60} s`),
    kpi("Sensores que responden", `${readOk} / ${sensors}`, `${sensors - readOk} con error de lectura`, readOk < sensors ? "err" : ""),
    kpi("Tiempo encendido", fmtUptime(sys.uptime_s), "Desde el último reinicio"));

  kv("sysGateway", [
    ["Cliente", cfg.gateway.client],
    ["Gateway", sys.gateway || cfg.gateway.gateway_id],
    ["Estado", live.online ? "En línea" : "Sin datos recientes"],
    ["Última lectura", fmtDateTime(live.rx_ms)],
    ["Hora local gateway", sys.city_time],
    ["Intervalo de muestreo", `${cfg.gateway.poll_interval} s`],
    ["Modo de datos", sys.modbus ? (sys.modbus.simulated ? "Simulado" : "Sensores reales (Modbus RTU)") : null],
  ]);
  kv("sysHardware", [
    ["Equipo", sys.hw_model],
    ["N.º de serie", sys.hw_serial],
    ["Hostname", sys.hostname],
    ["IP", sys.ip_address],
    ["Plataforma", sys.platform_type],
    ["Sistema operativo", sys.os],
    ["Página local", sys.hostname ? `http://${sys.hostname}.local:8080` : null],
  ]);

  $("sysResources").replaceChildren(...RES.map(([name, label, field]) => {
    const v = sys[field];
    const st = resState(name, v);
    const s = stats?.[name];
    const range = s && s.min !== null ? `24 h: mín. ${fmtNum(s.min, 0)} % · prom. ${fmtNum(s.avg, 0)} % · máx. ${fmtNum(s.max, 0)} %`
      : stats ? "24 h: sin datos" : "24 h: consultando…";
    return el("div", { class: `rcard ${st}`, role: "group", "aria-label": `${label} ${v ?? "sin dato"} %, ${STATE_TEXT[st]}` },
      el("div", { class: "mcard-h" }, el("b", { class: "rname" }, label), el("span", { class: `tag ${st}` }, STATE_TEXT[st])),
      el("div", { class: "rval" }, el("b", {}, v === null || v === undefined ? "—" : fmtNum(v, 0)), el("span", { class: "muted" }, "%")),
      el("span", { class: "muted small" }, range));
  }));
  const lim = RES.map(([n, label]) => [label, cfg.system_limits?.[n] || [80, 90]]);
  $("sysLimitsNote").textContent = lim.every(([, l]) => l.join() === lim[0][1].join())
    ? `Marco amarillo desde ${lim[0][1][0]} %, rojo desde ${lim[0][1][1]} %`
    : lim.map(([label, [w, a]]) => `${label}: amarillo ${w} %, rojo ${a} %`).join(" · ");

  const mb = sys.modbus, wifi = sys.wifi;
  $("sysLinks").replaceChildren(
    conn("AWS IoT Core (MQTT)", live.online ? "Recibiendo datos" : "Sin datos", live.online,
      `Temas ${cfg.topics?.data || "—"} y ${cfg.topics?.system || "—"}`,
      sys.mqtt_since ? `Conectado desde ${fmtDateTime(sys.mqtt_since * 1000)}` : null),
    conn("Modbus RS-485", mb ? (mb.connected ? "Conectado" : "Desconectado") : "—", mb ? mb.connected : null,
      mb ? (mb.simulated ? "Simulado (sin puerto serie)" : `${mb.port || "—"} · ${mb.baudrate || "—"} baud`) : null,
      `${readOk} de ${sensors} sensores responden`),
    // wifi undefined: the Pi doesn't report it yet; null: no active WiFi link (cable or other network).
    wifi === null
      ? conn("WiFi", "No se usa", null, "El gateway sale por cable u otra red", null)
      : conn("WiFi", wifi ? "Conectado" : "—", wifi ? true : null,
          wifi?.ssid ? `${wifi.ssid}${wifi.profile ? ` (${wifi.profile})` : ""}` : null,
          wifi?.signal !== undefined && wifi?.signal !== null ? `Señal ${wifi.signal} %` : null),
    // Off is a site setting (display.csv), not a fault: neutral tag either way.
    conn("Pantalla OLED", sys.oled_enabled === undefined ? "—" : sys.oled_enabled ? "Activa" : "Apagada",
      sys.oled_enabled ? true : null, sys.oled_enabled === false ? "Desactivada en la configuración del sitio" : null, null));
}

async function loadStats() {
  if (Date.now() - statsAt < STATS_TTL_MS) return;
  statsAt = Date.now();
  try { stats = await api.systemStats(); } catch { stats = null; statsAt = 0; }
  render();
}

export function initSystem(config) { cfg = config; }

export function onShow() { render(); loadStats(); }

export function onLive(live) {
  lastLive = live;
  if (!$("view-system").hidden) render();
}

/** Short version for the Resumen page. */
export function summaryRows(live) {
  const sys = live.system || {};
  const pct = (v) => (v === null || v === undefined ? "—" : `${fmtNum(v, 0)} %`);
  return [
    ["Gateway", sys.gateway || cfg.gateway.gateway_id],
    ["Equipo", sys.hw_model || "—"],
    ["CPU / RAM / Disco", `${pct(sys.cpu_load_percent)} / ${pct(sys.ram_usage_percent)} / ${pct(sys.disk_usage_percent)}`],
    ["Reporte de sistema", fmtDateTime(sys.rx_ms)],
  ];
}
