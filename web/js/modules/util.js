// DOM + formatting helpers. All user/device text goes through textContent.

export function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return n;
}

export const $ = (id) => document.getElementById(id);

/** Replace a table's content: headers + rows (arrays of cells: string | Node | {text, class}). */
export function fillTable(table, headers, rows, { empty = "Sin datos", onRow } = {}) {
  table.replaceChildren();
  table.append(el("thead", {}, el("tr", {}, headers.map((h) => el("th", {}, h)))));
  const tb = el("tbody");
  if (!rows.length) tb.append(el("tr", {}, el("td", { class: "empty", colspan: headers.length }, empty)));
  rows.forEach((r, i) => {
    const tr = el("tr", onRow ? { class: "clickable", onclick: () => onRow(i) } : {});
    r.forEach((c) => {
      if (c && typeof c === "object" && !(c instanceof Node)) tr.append(el("td", { class: c.class }, c.node ?? c.text ?? ""));
      else tr.append(el("td", {}, c ?? "—"));
    });
    tb.append(tr);
  });
  table.append(tb);
}

let TZ = "America/Mexico_City";
export function setTimezone(tz) { if (tz) TZ = tz; }
export function getTimezone() { return TZ; }

const fmtCache = {};
function dtf(opts) {
  const k = JSON.stringify(opts);
  return (fmtCache[TZ + k] ||= new Intl.DateTimeFormat("es-MX", { timeZone: TZ, hour12: false, ...opts }));
}
export function fmtDateTime(ms) {
  if (ms === null || ms === undefined) return "—";
  return dtf({ year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(ms);
}
export function fmtNum(v, d = 2) {
  return v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(d);
}
export function fmtDuration(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const p = (n) => String(n).padStart(2, "0");
  return `${p(Math.floor(s / 3600))}:${p(Math.floor((s % 3600) / 60))}:${p(s % 60)}`;
}
export function fmtAge(s) {
  if (s === null || s === undefined) return "—";
  if (s < 90) return `${Math.round(s)} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1)} h`;
}

/** datetime-local input value <-> epoch ms, interpreted in the gateway timezone. */
export function toLocalInput(ms) {
  const parts = Object.fromEntries(
    dtf({ year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
      .formatToParts(ms).map((p) => [p.type, p.value]));
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour === "24" ? "00" : parts.hour}:${parts.minute}`;
}
export function fromLocalInput(value) {
  // Guess as UTC, then correct by the zone offset at that instant (twice, for DST edges).
  const asUtc = Date.parse(value + ":00Z");
  let ms = asUtc;
  for (let i = 0; i < 2; i++) ms = asUtc - (Date.parse(toLocalInput(ms) + ":00Z") - ms);
  return ms;
}

export function storageGet(k, fallback) {
  try { const v = localStorage.getItem(k); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
}
export function storageSet(k, v) {
  try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ }
}

export const KIND_LABEL = { HIGH: "Alto", LOW: "Bajo", BUS_ERROR: "Error bus", EXCEPTION: "Error lectura" };
