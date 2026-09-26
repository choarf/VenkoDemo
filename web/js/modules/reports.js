// Reports view: per-test reports and weekly reports (HTML / Excel via presigned URLs).
import { api, openReport } from "./api.js";
import { reportLinks } from "./hmi.js";
import { $, el, fillTable, fmtDateTime, fmtDuration } from "./util.js";

function link(key, text) {
  return el("a", { href: "#", class: "link", onclick: (e) => { e.preventDefault(); openReport(key); } }, text);
}

export async function refreshReports() {
  try {
    const { tests, weekly } = await api.reports();
    fillTable($("testReports"), ["Prueba", "Nombre", "Operador", "Inicio", "Duración", "Reporte"],
      tests.map((t) => [t.id, t.name, t.operator, fmtDateTime(t.start_ms), fmtDuration(t.end_ms - t.start_ms), { node: reportLinks(t) }]),
      { empty: "Aún no hay reportes de prueba" });
    fillTable($("weeklyReports"), ["Semana", "Desde", "Hasta", "Disponibilidad", "Eventos de alarma", "Generado", "Reporte"],
      weekly.map((w) => [w.week, fmtDateTime(w.from_ms), fmtDateTime(w.to_ms), `${w.data_availability ?? "—"} %`,
        w.episodes ?? "—", fmtDateTime(w.generated_ms), { node: el("span", {}, link(w.html, "HTML"), " · ", link(w.xlsx, "Excel")) }]),
      { empty: "Aún no hay reportes semanales" });
  } catch (err) {
    $("weekMsg").textContent = `Error: ${err.message}`;
  }
}

export function initReports() {
  $("weekBtn").addEventListener("click", async () => {
    const week = $("weekInput").value.trim();
    if (week && !/^\d{4}-W\d{2}$/.test(week)) { $("weekMsg").textContent = "Formato: 2026-W39"; return; }
    try {
      await api.requestWeekly(week);
      $("weekMsg").textContent = `Generando ${week || "semana anterior"}… actualiza en un minuto.`;
      setTimeout(refreshReports, 60_000);
    } catch (err) { $("weekMsg").textContent = `Error: ${err.message}`; }
  });
}

export const onShow = refreshReports;
