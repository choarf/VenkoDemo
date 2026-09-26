// Thin client for the VenkoDemo API (API Gateway + Lambda). Settings come from app-config.js.
const CFG = window.VENKO || {};
const BASE = (CFG.apiBase || "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(status, msg) { super(msg); this.status = status; }
}

async function call(method, path, { query, body } = {}) {
  const url = new URL(BASE + path, location.href);
  for (const [k, v] of Object.entries(query || {})) if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
  const headers = { "Content-Type": "application/json" };
  if (CFG.apiKey) headers["X-Api-Key"] = CFG.apiKey;
  let res;
  try {
    res = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined });
  } catch (e) {
    throw new ApiError(0, "Sin conexión con la API");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data.error || data.message || `HTTP ${res.status}`);
  return data;
}

export const api = {
  config: () => call("GET", "/config"),
  live: () => call("GET", "/live"),
  history: (from, to, sensors, bucket_ms) => call("GET", "/history", { query: { from, to, sensors: sensors.join(","), bucket_ms } }),
  alarms: (from, to) => call("GET", "/alarms", { query: { from, to } }),
  exportUrl: (from, to, sensors, format) => call("GET", "/export", { query: { from, to, sensors: sensors.join(","), format } }),
  tests: () => call("GET", "/tests"),
  activeTest: () => call("GET", "/tests/active"),
  test: (id) => call("GET", `/tests/${encodeURIComponent(id)}`),
  startTest: (b) => call("POST", "/tests/start", { body: b }),
  stopTest: (id, operator) => call("POST", `/tests/${encodeURIComponent(id)}/stop`, { body: { operator } }),
  regenerateTest: (id) => call("POST", `/tests/${encodeURIComponent(id)}/report`, { body: {} }),
  comments: (q) => call("GET", "/comments", { query: q }),
  addComment: (b) => call("POST", "/comments", { body: b }),
  reports: () => call("GET", "/reports"),
  reportUrl: (key) => call("GET", "/reports/url", { query: { key } }),
  requestWeekly: (week) => call("POST", "/reports/weekly", { body: { week } }),
};

/** Open a report file via a short-lived presigned URL. */
export async function openReport(key) {
  const { url } = await api.reportUrl(key);
  window.open(url, "_blank", "noopener");
}
