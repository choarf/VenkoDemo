#!/usr/bin/env python3
"""Local development server: serves web/ plus a MOCK of the VenkoDemo API.

For UI work without AWS only - every value it returns is SYNTHETIC. The real
demo reads gateway data from AWS IoT -> S3 through the deployed API.

  pip install openpyxl jinja2 tzdata
  python3 tools/mock_api.py            # http://localhost:8787
"""
import json
import math
import random
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools"))

from common import report_builder, timeutil  # noqa: E402
from sync_config import DEFAULT_OVERRIDES, DEFAULT_SOURCE, build  # noqa: E402

CFG = build(json.loads(DEFAULT_SOURCE.read_text()), json.loads(DEFAULT_OVERRIDES.read_text()))
POLL_MS = CFG["gateway"]["poll_interval"] * 1000
STATE = {"tests": {}, "active": None, "comments": [], "reports": {}}
ALARM_KEY = CFG["sensors"][4]["key"]  # one sensor periodically drifts out of range


def value(s, t):
    """Deterministic synthetic signal per sensor: slow sine + noise, inside limits."""
    mid, amp = (s["min"] + s["max"]) / 2, (s["max"] - s["min"]) * 0.25
    phase = sum(map(ord, s["key"])) % 60
    v = mid + amp * math.sin(t / 900_000 + phase) + random.uniform(-0.02, 0.02) * amp
    if s["key"] == ALARM_KEY and (t // 600_000) % 4 == 0:  # 10 min out of every 40
        v = s["max"] * 1.05
    return round(v, 3)


def reading(s, t):
    if s["key"] == CFG["sensors"][-1]["key"] and (t // 300_000) % 6 == 0:
        return {"status": "BUS_ERROR"}
    v = value(s, t)
    return {"val": v, "status": "OK", "alarm": "LOW" if v < s["min"] else "HIGH" if v > s["max"] else "NORMAL"}


def live():
    t = (timeutil.now_ms() // POLL_MS) * POLL_MS
    values = {s["key"]: reading(s, t) for s in CFG["sensors"]}
    alarms = []
    for s in CFG["sensors"]:
        r = values[s["key"]]
        kind = r.get("alarm") if r["status"] == "OK" else r["status"]
        if kind != "NORMAL":
            alarms.append({"key": s["key"], "label": s["label"], "kind": kind, "val": r.get("val"),
                           "unit": s["unit"], "min": s["min"], "max": s["max"]})
    now = timeutil.now_ms()
    return {"now_ms": now, "rx_ms": t, "age_s": round((now - t) / 1000, 1), "online": True, "values": values,
            "alarms": alarms, "system": {
                "gateway": CFG["gateway"]["gateway_id"], "city_time": timeutil.local_str(now, CFG["gateway"]["city"]),
                "platform_type": "Raspberry Pi (MOCK)", "os": "Linux 6.6", "ip_address": "192.168.10.12",
                "cpu_load_percent": 12 + random.random() * 8, "ram_usage_percent": 41.3, "disk_usage_percent": 23.0,
                "rx_ms": now - 30_000}}


def history(from_ms, to_ms, keys, bucket_ms):
    keys = keys or [s["key"] for s in CFG["sensors"]]
    by = {s["key"]: s for s in CFG["sensors"]}
    out = []
    t = (from_ms // bucket_ms) * bucket_ms
    while t < to_ms:
        for k in keys:
            vals = [value(by[k], t + i * POLL_MS) for i in range(max(1, bucket_ms // POLL_MS))]
            out.append({"k": k, "t": t, "avg": sum(vals) / len(vals), "min": min(vals), "max": max(vals), "n": len(vals)})
        t += bucket_ms
    return out


def abnormal(from_ms, to_ms):
    rows = []
    for s in CFG["sensors"]:
        for t in range((from_ms // POLL_MS) * POLL_MS, to_ms, POLL_MS):
            r = reading(s, t)
            kind = r.get("alarm") if r["status"] == "OK" else r["status"]
            if kind != "NORMAL":
                rows.append({"k": s["key"], "t": t, "val": r.get("val"), "kind": kind})
    return rows


def make_report(kind, title, from_ms, to_ms, prefix, meta, comments):
    bucket = timeutil.pick_bucket_ms(from_ms, to_ms, 300)
    series = history(from_ms, to_ms, [], bucket)
    ab = abnormal(from_ms, to_ms)
    summary = []
    for s in CFG["sensors"]:
        pts = [p for p in series if p["k"] == s["key"]]
        n = sum(p["n"] for p in pts)
        hi = sum(1 for r in ab if r["k"] == s["key"] and r["kind"] == "HIGH")
        summary.append({"k": s["key"], "samples": n, "ok": n, "min": min((p["min"] for p in pts), default=None),
                        "avg": sum(p["avg"] for p in pts) / len(pts) if pts else None,
                        "max": max((p["max"] for p in pts), default=None), "std": 0.1,
                        "first": pts[0]["avg"] if pts else None, "last": pts[-1]["avg"] if pts else None,
                        "low": 0, "high": hi})
    ctx = report_builder.build_context(
        kind=kind, title=title, config=CFG, from_ms=from_ms, to_ms=to_ms, summary=summary, series=series,
        bucket_ms=bucket, abnormal=ab, gaps=[], system={"Plataforma": "MOCK"},
        comments=comments, meta=meta, generated_ms=timeutil.now_ms())
    STATE["reports"][f"{prefix}/report.html"] = (report_builder.render_html(ctx), "text/html; charset=utf-8")
    STATE["reports"][f"{prefix}/report.xlsx"] = (report_builder.render_xlsx(ctx),
                                                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return {"html": f"{prefix}/report.html", "xlsx": f"{prefix}/report.xlsx"}, ctx


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT / "web"), **kw)

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("%s\n" % (fmt % args))

    def _json(self, obj, status=200):
        body = json.dumps(obj, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/app-config.js":
            body = b'window.VENKO = { apiBase: "/api", apiKey: "" }; // MOCK'
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.end_headers()
            self.wfile.write(body)
            return
        if u.path.startswith("/mock-reports/"):
            data, ctype = STATE["reports"].get(u.path[len("/mock-reports/"):], (None, None))
            if data is None:
                return self._json({"error": "not found"}, 404)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(data)
            return
        if u.path.startswith("/api/"):
            return self.api("GET", u.path[4:], {k: v[0] for k, v in parse_qs(u.query).items()}, {})
        return super().do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        return self.api("POST", u.path[4:], {}, body)

    def api(self, method, path, q, body):
        now = timeutil.now_ms()
        to = int(q.get("to") or now)
        frm = int(q.get("from") or to - 3_600_000)
        keys = [k for k in (q.get("sensors") or "").split(",") if k]
        if (method, path) == ("GET", "/config"):
            return self._json(CFG)
        if (method, path) == ("GET", "/live"):
            return self._json(live())
        if (method, path) == ("GET", "/history"):
            b = timeutil.pick_bucket_ms(frm, to)
            series = {}
            for p in history(frm, min(to, now), keys, b):
                series.setdefault(p["k"], []).append([p["t"], p["avg"], p["min"], p["max"]])
            return self._json({"from_ms": frm, "to_ms": to, "bucket_ms": b, "series": series})
        if (method, path) == ("GET", "/alarms"):
            eps = report_builder.collapse_episodes(abnormal(frm, to), int(POLL_MS * 2.5))
            labels = {s["key"]: s for s in CFG["sensors"]}
            for e in eps:
                e.update(label=labels[e["k"]]["label"], unit=labels[e["k"]]["unit"])
            return self._json({"episodes": sorted(eps, key=lambda e: -e["start"])})
        if (method, path) == ("GET", "/export"):
            return self._json({"error": "export not available in mock"}, 501)
        if (method, path) == ("GET", "/tests"):
            return self._json({"tests": sorted(STATE["tests"].values(), key=lambda t: t["id"], reverse=True)})
        if (method, path) == ("GET", "/tests/active"):
            return self._json({"test": STATE["tests"].get(STATE["active"])})
        if (method, path) == ("POST", "/tests/start"):
            if STATE["active"]:
                return self._json({"error": "a test is already running; stop it first"}, 409)
            if not body.get("name") or not body.get("operator"):
                return self._json({"error": "'name' and 'operator' are required"}, 400)
            tid = "T" + time.strftime("%Y%m%d-%H%M%S", time.gmtime())
            STATE["tests"][tid] = {"id": tid, "name": body["name"], "operator": body["operator"],
                                   "notes": body.get("notes", ""), "start_ms": now, "status": "RUNNING"}
            STATE["active"] = tid
            return self._json({"test": STATE["tests"][tid]})
        if method == "POST" and path.startswith("/tests/") and path.endswith("/stop"):
            tid = path.split("/")[2]
            if STATE["active"] != tid:
                return self._json({"error": f"{tid} is not the running test"}, 409)
            t = STATE["tests"][tid]
            t.update(end_ms=now, status="DONE", stopped_by=body.get("operator", ""))
            STATE["active"] = None
            t["report"], _ = make_report("test", f'Reporte de prueba {tid} – {t["name"]}', t["start_ms"], now,
                                         f"reports/tests/{tid}", {"prueba": tid, "nombre": t["name"], "operador": t["operator"],
                                                                  "notas": t["notes"]},
                                         [c for c in STATE["comments"] if c.get("test_id") == tid])
            return self._json({"test": t})
        if (method, path) == ("GET", "/comments"):
            tid = q.get("test_id")
            items = [c for c in STATE["comments"] if (c.get("test_id") == tid if tid else c["ts_ms"] >= now - 86_400_000)]
            return self._json({"comments": sorted(items, key=lambda c: -c["ts_ms"])})
        if (method, path) == ("POST", "/comments"):
            c = {"id": str(len(STATE["comments"])), "ts_ms": now, "text": body["text"], "author": body["author"],
                 "sensor": body.get("sensor", ""), "test_id": body.get("test_id") or STATE["active"] or ""}
            STATE["comments"].append(c)
            return self._json({"comment": c})
        if (method, path) == ("GET", "/reports"):
            weekly = [v for k, v in STATE.items() if k == "weekly_meta"]
            return self._json({"tests": [t for t in STATE["tests"].values() if t["status"] == "DONE"],
                               "weekly": weekly[0] if weekly else []})
        if (method, path) == ("GET", "/reports/url"):
            return self._json({"url": "/mock-reports/" + q["key"]})
        if (method, path) == ("POST", "/reports/weekly"):
            label, frm_w, to_w = timeutil.previous_iso_week(now, CFG["gateway"]["city"])
            keys_, ctx = make_report("weekly", f"Reporte semanal {label}", frm_w, to_w, f"reports/weekly/{label}",
                                     {"semana": label}, STATE["comments"])
            STATE["weekly_meta"] = [{"week": label, "from_ms": frm_w, "to_ms": to_w, "generated_ms": now,
                                     "episodes": len(ctx["episodes"]), "data_availability": ctx["data_availability"], **keys_}]
            return self._json({"requested": label})
        return self._json({"error": f"no route {method} {path}"}, 404)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    print(f"MOCK VenkoDemo (synthetic data) on http://localhost:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
