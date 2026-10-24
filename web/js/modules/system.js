// Sistema view: gateway and hardware data, CPU / RAM / disk against system_limits, connections.
// Behind the site's Sistema password: POST /system/login gives a token (kept for this browser
// tab in sessionStorage) that GET /system and /system/stats need; a 401 shows the form again.
// Fields the gateway added later (uptime_s, wifi, modbus, mqtt_since, oled_enabled) show "—"
// until that Pi runs the new code.
import { api } from "./api.js";
import { $, el, fmtAge, fmtDateTime, fmtNum } from "./util.js";

const STATS_TTL_MS = 5 * 60_000;
const RES = [["cpu", "CPU", "cpu_load_percent"], ["ram", "RAM", "ram_usage_percent"], ["disk", "Disco", "disk_usage_percent"]];
const STATE_TEXT = { ok: "Normal", warn: "Advertencia", alarm: "Alarma", none: "Sin datos" };
const TOKEN_KEY = "venko.systemToken";
let cfg, stats = null, statsAt = 0, lastLive = null, sysData = null, loading = false;

function getToken() { try { return sessionStorage.getItem(TOKEN_KEY); } catch { return null; } }
function setToken(t) {
  try { t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY); } catch { /* private mode */ }
}

function showLocked(message = "") {
  setToken(null);
  sysData = null; stats = null; statsAt = 0;
  $("sysContent").hidden = true;
  $("sysLogin").hidden = false;
  $("sysLoginMsg").textContent = message;
}

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

const NET_TYPE = { wifi: "WiFi", ethernet: "Ethernet (cable)", cellular: "Celular (módem)", other: "Otra" };

/** "Red": the link the gateway uses to reach the internet, and WiFi details when it's WiFi. */
function netCard(sys) {
  const net = sys.network, wifi = sys.wifi;
  if (net === undefined) return conn("Red", "—", null, null, null);   // Pi without this report yet
  if (net === null) return conn("Red", "Sin ruta", false, "El gateway no tiene salida a internet", null);
  const extra = net.type === "wifi" && wifi
    ? `${wifi.ssid || "—"}${wifi.profile ? ` (${wifi.profile})` : ""}${wifi.signal != null ? ` · señal ${wifi.signal} %` : ""}`
    : null;
  return conn("Red", NET_TYPE[net.type] || net.type, true, `Interfaz ${net.iface} · IP ${sys.ip_address || "—"}`, extra);
}

function fmtBits(bps) {
  if (bps === null || bps === undefined) return "—";
  return bps >= 1e6 ? `${fmtNum(bps / 1e6, 1)} Mbit/s` : `${fmtNum(bps / 1e3, 1)} kbit/s`;
}
function fmtBytes(b) {
  return b >= 1e9 ? `${fmtNum(b / 1e9, 2)} GB` : b >= 1e6 ? `${fmtNum(b / 1e6, 1)} MB` : `${fmtNum(b / 1e3, 0)} kB`;
}

let netChart = null;
function drawNetChart() {
  const net = stats?.net;
  if (!net) return;
  const css = (v) => getComputedStyle(document.body).getPropertyValue(v).trim();
  $("netTotals").textContent = `Bajada ${fmtBytes(net.total_down)} · subida ${fmtBytes(net.total_up)} en 24 h`;
  netChart?.destroy();
  netChart = Highcharts.chart($("netChart"), {
    chart: { type: "area", animation: false, backgroundColor: "transparent" },
    title: { text: null }, credits: { enabled: false }, exporting: { enabled: false },
    xAxis: { type: "datetime" },
    yAxis: { min: 0, title: { text: null }, labels: { formatter() { return fmtBits(this.value); } } },
    tooltip: { shared: true, xDateFormat: "%d-%b %H:%M", pointFormatter() {
      return `<span style="color:${this.color}">●</span> ${this.series.name}: <b>${fmtBits(this.y)}</b><br/>`; } },
    plotOptions: { area: { marker: { enabled: false }, lineWidth: 2, fillOpacity: 0.12 } },
    lang: { noData: "Sin datos de red de 24 h" },
    series: [{ name: "Bajada", data: net.down, color: css("--accent") },
             { name: "Subida", data: net.up, color: css("--warn") }],
  });
}

function render() {
  const live = lastLive;
  if (!live || !sysData) return;
  $("sysLogin").hidden = true;
  $("sysContent").hidden = false;
  const sys = sysData.system || {};
  const sensors = cfg.sensors.length;
  const readOk = cfg.sensors.filter((s) => live.values[s.key]?.status === "OK").length;
  const sysAge = sys.rx_ms ? (sysData.now_ms - sys.rx_ms) / 1000 : null;

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

  buildResources();
  for (const [name, label, field] of RES) {
    const c = resCards[name], v = sys[field], st = resState(name, v), s = stats?.[name];
    c.card.className = `rcard ${st}`;
    c.card.setAttribute("aria-label", `${label} ${v ?? "sin dato"} %, ${STATE_TEXT[st]}`);
    c.tag.className = `tag ${st}`;
    c.tag.textContent = STATE_TEXT[st];
    c.value.textContent = v === null || v === undefined ? "—" : fmtNum(v, 0);
    c.range.textContent = s && s.min !== null ? `24 h: mín. ${fmtNum(s.min, 0)} % · prom. ${fmtNum(s.avg, 0)} % · máx. ${fmtNum(s.max, 0)} %`
      : stats ? "24 h: sin datos" : "24 h: consultando…";
  }
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
    netCard(sys),
    // Off is a site setting (display.csv), not a fault: neutral tag either way.
    conn("Pantalla OLED", sys.oled_enabled === undefined ? "—" : sys.oled_enabled ? "Activa" : "Apagada",
      sys.oled_enabled ? true : null, sys.oled_enabled === false ? "Desactivada en la configuración del sitio" : null, null));
}

// Resource cards are built once and updated in place, so the CPU / RAM charts aren't
// redrawn on every 10 s refresh - only when new stats arrive (every 5 min).
const CHARTED = ["cpu", "ram"];
let resCards = null;
const resCharts = {};

function buildResources() {
  if (resCards) return;
  resCards = {};
  $("sysResources").replaceChildren(...RES.map(([name, label]) => {
    const c = {
      tag: el("span", { class: "tag none" }, "—"),
      value: el("b", {}, "—"),
      range: el("span", { class: "muted small" }, ""),
      chart: CHARTED.includes(name) ? el("div", { class: "rchart", role: "img", "aria-label": `${label}, últimas 24 h` }) : null,
    };
    c.card = el("div", { class: "rcard", role: "group" },
      el("div", { class: "mcard-h" }, el("b", { class: "rname" }, label), c.tag),
      el("div", { class: "rval" }, c.value, el("span", { class: "muted" }, "%")),
      c.chart, c.range);
    resCards[name] = c;
    return c.card;
  }));
}

function drawResourceCharts() {
  if (!resCards || !stats?.series) return;
  const css = (v) => getComputedStyle(document.body).getPropertyValue(v).trim();
  for (const name of CHARTED) {
    resCharts[name]?.destroy();
    const [warn, alarm] = cfg.system_limits?.[name] || [80, 90];
    const data = stats.series[name] || [];
    const top = Math.max(alarm, ...data.map((p) => p[1]));
    resCharts[name] = Highcharts.chart(resCards[name].chart, {
      chart: { type: "area", height: 90, spacing: [4, 0, 2, 0], animation: false, backgroundColor: "transparent" },
      title: { text: null }, legend: { enabled: false }, credits: { enabled: false }, exporting: { enabled: false },
      xAxis: { type: "datetime", lineWidth: 0, tickLength: 0, labels: { style: { fontSize: "10px" } } },
      yAxis: { min: 0, max: Math.min(100, Math.ceil(top / 10) * 10), title: { text: null }, tickAmount: 3,
        labels: { format: "{value} %", style: { fontSize: "10px" } },
        plotLines: [{ value: warn, color: css("--warn"), width: 1, dashStyle: "Dash", zIndex: 3 },
                    { value: alarm, color: css("--bad"), width: 1, dashStyle: "Dash", zIndex: 3 }] },
      tooltip: { valueDecimals: 1, valueSuffix: " %", xDateFormat: "%d-%b %H:%M" },
      plotOptions: { area: { marker: { enabled: false }, lineWidth: 2, fillOpacity: 0.15, color: css("--accent"), threshold: 0 } },
      lang: { noData: "Sin datos de 24 h" },
      series: [{ name: name === "cpu" ? "CPU" : "RAM", data }],
    });
  }
}

/** GET /system (and /system/stats every 5 min) with the token; a 401 locks the view again. */
async function refresh() {
  const token = getToken();
  if (!token) return showLocked();
  if (loading) return;
  loading = true;
  try {
    sysData = await api.system(token);
    if (Date.now() - statsAt >= STATS_TTL_MS) {
      statsAt = Date.now();
      stats = await api.systemStats(token).catch((e) => { if (e.status === 401) throw e; statsAt = 0; return null; });
      render();
      drawResourceCharts();
      drawNetChart();
    } else {
      render();
    }
  } catch (err) {
    if (err.status === 401) showLocked("La sesión de Sistema expiró. Escribe la contraseña de nuevo.");
    else $("sysLoginMsg").textContent = `Error: ${err.message}`;
  } finally { loading = false; }
}

export function initSystem(config) {
  cfg = config;
  document.addEventListener("themechange", () => { drawResourceCharts(); drawNetChart(); });
  $("sysLoginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, btn = f.querySelector("button");
    btn.disabled = true;
    $("sysLoginMsg").textContent = "";
    try {
      const { token } = await api.systemLogin(f.elements.password.value);
      setToken(token);
      f.reset();
      $("sysPassword").type = "password";
      $("sysPwToggle").textContent = "Mostrar";
      $("sysPwToggle").setAttribute("aria-pressed", "false");
      await refresh();
    } catch (err) {
      $("sysLoginMsg").textContent = err.status === 401 ? "Contraseña incorrecta." : `Error: ${err.message}`;
    } finally { btn.disabled = false; }
  });
  $("sysLogout").addEventListener("click", () => showLocked("Sesión de Sistema cerrada."));
  $("sysPwToggle").addEventListener("click", () => {
    const input = $("sysPassword"), show = input.type === "password";
    input.type = show ? "text" : "password";
    $("sysPwToggle").textContent = show ? "Ocultar" : "Mostrar";
    $("sysPwToggle").setAttribute("aria-pressed", String(show));
    input.focus();
  });
}

export function onShow() {
  if (getToken()) refresh();
  else { showLocked(); $("sysLoginForm").elements.password.focus(); }
}

export function onLive(live) {
  lastLive = live;
  if (!$("view-system").hidden && getToken()) refresh();
}

/** Short, non-sensitive version for the Resumen page (details need the Sistema password). */
export function summaryRows(live) {
  return [
    ["Gateway", cfg.gateway.gateway_id],
    ["Estado", live.online ? "En línea" : "Sin datos recientes"],
    ["Última lectura", fmtDateTime(live.rx_ms)],
    ["Reporte de sistema", fmtDateTime(live.system_rx_ms)],
  ];
}
