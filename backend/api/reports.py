"""Report generation (report Lambda) and report listing/download (API)."""
import json
import logging

import boto3

from common import athena, ddb, queries, report_builder, settings, storage, timeutil

from .data import fetch_abnormal, fetch_history

log = logging.getLogger()
log.setLevel(logging.INFO)
_lambda = boto3.client("lambda")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def generate(kind: str, title: str, from_ms: int, to_ms: int, prefix: str,
             meta: dict | None, comments: list[dict]) -> dict:
    """Query everything for [from_ms, to_ms), render HTML + XLSX, store under prefix."""
    cfg = storage.get_config()
    poll_ms = cfg["gateway"].get("poll_interval", 20) * 1000
    bucket_ms = timeutil.pick_bucket_ms(from_ms, to_ms, target_points=300)

    summary = [{
        "k": r["k"], **{f: athena.num(r[f]) for f in
                        ("samples", "ok", "min", "avg", "max", "std", "first", "last", "low", "high")}
    } for r in athena.run(queries.sensor_summary(from_ms, to_ms), timeout_s=120)]
    gaps = [{"start": int(r["start"]), "stop": int(r["stop"])}
            for r in athena.run(queries.data_gaps(from_ms, to_ms, poll_ms * 3), timeout_s=120)]
    sys_rows = athena.run(queries.system_summary(from_ms, to_ms), timeout_s=120)
    system = {}
    if sys_rows and athena.num(sys_rows[0]["samples"]):
        s = sys_rows[0]
        system = {
            "Plataforma": s["platform"], "SO": s["os"], "IP": s["ip"],
            "CPU prom / máx %": f'{_fmt(s["cpu_avg"])} / {_fmt(s["cpu_max"])}',
            "RAM prom / máx %": f'{_fmt(s["ram_avg"])} / {_fmt(s["ram_max"])}',
            "Disco máx %": _fmt(s["disk_max"]),
        }

    ctx = report_builder.build_context(
        kind=kind, title=title, config=cfg, from_ms=from_ms, to_ms=to_ms,
        summary=summary, series=fetch_history(from_ms, to_ms, [], bucket_ms), bucket_ms=bucket_ms,
        abnormal=fetch_abnormal(from_ms, to_ms), gaps=gaps, system=system,
        comments=comments, meta=meta, generated_ms=timeutil.now_ms(),
    )
    html_key, xlsx_key = f"{prefix}/report.html", f"{prefix}/report.xlsx"
    storage.put_bytes(html_key, report_builder.render_html(ctx), "text/html; charset=utf-8")
    storage.put_bytes(xlsx_key, report_builder.render_xlsx(ctx), XLSX)
    storage.put_bytes(f"{prefix}/meta.json", json.dumps({
        "kind": kind, "title": title, "from_ms": from_ms, "to_ms": to_ms,
        "generated_ms": timeutil.now_ms(), "alarm_sensors": ctx["alarm_sensor_count"],
        "episodes": len(ctx["episodes"]), "data_availability": ctx["data_availability"],
    }).encode(), "application/json")
    return {"html": html_key, "xlsx": xlsx_key}


def _fmt(v):
    n = athena.num(v)
    return "—" if n is None else round(n, 1)


def build_test_report(test_id: str) -> dict:
    t = ddb.get_test(test_id)
    try:
        keys = generate(
            kind="test", title=f'Reporte de prueba {t["id"]} – {t["name"]}',
            from_ms=t["start_ms"], to_ms=t["end_ms"], prefix=f"reports/tests/{test_id}",
            meta={"prueba": t["id"], "nombre": t["name"], "operador": t["operator"],
                  "detenida por": t.get("stopped_by", ""), "notas": t.get("notes", "")},
            comments=ddb.comments_for_test(test_id),
        )
    except Exception as e:
        log.exception("test report %s failed", test_id)
        ddb.set_test_report(test_id, "REPORT_FAILED", error=str(e)[:500])
        raise
    ddb.set_test_report(test_id, "DONE", keys)
    return keys


def build_weekly_report(label: str | None = None) -> dict:
    if label:
        label, from_ms, to_ms = timeutil.week_bounds(label)
    else:
        label, from_ms, to_ms = timeutil.previous_iso_week(timeutil.now_ms())
    return generate(
        kind="weekly", title=f"Reporte semanal {label}", from_ms=from_ms, to_ms=to_ms,
        prefix=f"reports/weekly/{label}", meta={"semana": label},
        comments=ddb.comments_in_range(from_ms, to_ms),
    )


def report_handler(event, _context):
    """Report Lambda: {"kind": "test", "test_id"} or {"kind": "weekly", "week"?}."""
    if event.get("kind") == "test":
        return build_test_report(event["test_id"])
    return build_weekly_report(event.get("week"))


# ------------------------------------------------------------------ API
def list_reports(_req):
    weekly = []
    for key in storage.list_keys("reports/weekly/"):
        if key.endswith("/meta.json"):
            m = storage.get_json(key) or {}
            prefix = key.rsplit("/", 1)[0]
            weekly.append({**m, "week": prefix.rsplit("/", 1)[1],
                           "html": f"{prefix}/report.html", "xlsx": f"{prefix}/report.xlsx"})
    tests = [t for t in ddb.list_tests(100) if t.get("status") in ("DONE", "REPORT_PENDING", "REPORT_FAILED")]
    return {"weekly": sorted(weekly, key=lambda w: w["week"], reverse=True), "tests": tests}


def report_url(req):
    key = req.query["key"]
    if not key.startswith("reports/") or ".." in key:
        raise ValueError("invalid report key")
    return {"url": storage.presign(key)}


def request_weekly(req):
    """Generate (or regenerate) a weekly report on demand: {"week": "2026-W39"}."""
    week = str(req.body.get("week") or "")
    if week:
        timeutil.week_bounds(week)  # validates format
    _lambda.invoke(FunctionName=settings.REPORT_FUNCTION, InvocationType="Event",
                   Payload=json.dumps({"kind": "weekly", "week": week or None}).encode())
    return {"requested": week or "previous"}


def regenerate_test(_req, test_id):
    t = ddb.get_test(test_id)
    if not t.get("end_ms"):
        raise ValueError("test is still running")
    ddb.set_test_report(test_id, "REPORT_PENDING")
    _lambda.invoke(FunctionName=settings.REPORT_FUNCTION, InvocationType="Event",
                   Payload=json.dumps({"kind": "test", "test_id": test_id}).encode())
    return {"test": ddb.get_test(test_id)}
