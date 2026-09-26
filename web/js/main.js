// VenkoDemo dashboard bootstrap: config, routing, live polling.
import * as alarms from "./modules/alarms.js";
import { api } from "./modules/api.js";
import * as hmi from "./modules/hmi.js";
import * as history from "./modules/history.js";
import * as overview from "./modules/overview.js";
import * as reports from "./modules/reports.js";
import { initTheme } from "./modules/theme.js";
import { $, fmtAge, fmtDuration, setTimezone } from "./modules/util.js";

const LIVE_POLL_MS = 10_000;
const VIEWS = { overview: null, test: null, alarms: alarms.onShow, history: history.onShow, reports: reports.onShow };
let cfg, active = null;

function showError(text) {
  const b = $("errorBanner");
  b.hidden = !text;
  b.textContent = text || "";
}

function route() {
  const view = (location.hash || "#overview").slice(1);
  const name = view in VIEWS ? view : "overview";
  for (const v of Object.keys(VIEWS)) $(`view-${v}`).hidden = v !== name;
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === name));
  // Charts created while their view was hidden have zero width.
  requestAnimationFrame(() => Highcharts.charts.forEach((c) => c && c.reflow()));
  VIEWS[name]?.();
}

function renderMode() {
  const pill = $("modePill");
  if (active) {
    pill.className = "pill run";
    pill.textContent = `● Prueba en curso: ${active.name} · ${fmtDuration(Date.now() - active.start_ms)}`;
  } else {
    pill.className = "pill";
    pill.textContent = "Monitoreo libre";
  }
}

async function pollLive() {
  try {
    const [live, act] = await Promise.all([api.live(), api.activeTest()]);
    showError("");
    const link = $("linkPill");
    link.className = `pill ${live.online ? "ok" : "bad"}`;
    link.textContent = live.online ? `Gateway en línea · ${fmtAge(live.age_s)}` : `Gateway sin datos · ${fmtAge(live.age_s)}`;
    overview.onLive(live, cfg);
    alarms.onLive(live);
    hmi.onLive(live);
    // Another operator may have started/stopped a test from a different browser.
    if ((act.test?.id || null) !== (active?.id || null)) await hmi.setActive(act.test);
  } catch (err) {
    showError(`No se pudieron leer los datos en vivo: ${err.message}`);
  }
}

async function boot() {
  initTheme($("themeBtn"));
  try {
    cfg = await api.config();
  } catch (err) {
    showError(`No se pudo cargar la configuración: ${err.message}. Revisa app-config.js (apiBase / apiKey).`);
    return;
  }
  setTimezone(cfg.gateway.city);
  Highcharts.setOptions({ time: { timezone: cfg.gateway.city }, lang: { decimalPoint: ".", thousandsSep: "," } });
  document.title = cfg.site.title || "Venko Demo";
  $("siteTitle").textContent = cfg.site.title || "Venko Demo";
  $("brand").textContent = cfg.site.brand || "";
  $("gwInfo").textContent = `Gateway ${cfg.gateway.gateway_id} · ${cfg.sensors.length} sensores · muestreo ${cfg.gateway.poll_interval} s`;

  overview.initOverview(cfg);
  hmi.initHmi(cfg, { onActiveChange: (t) => { active = t; renderMode(); } });
  alarms.initAlarms(cfg);
  history.initHistory(cfg);
  reports.initReports();

  window.addEventListener("hashchange", route);
  route();
  await pollLive();
  setInterval(pollLive, LIVE_POLL_MS);
  setInterval(renderMode, 1000);
}

boot();
