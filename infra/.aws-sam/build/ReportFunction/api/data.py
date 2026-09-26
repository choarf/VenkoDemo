"""Config, live values, history, alarm history and ad-hoc export."""
import csv
import io

from common import athena, queries, storage, timeutil
from common.report_builder import collapse_episodes

OFFLINE_FACTOR = 3  # gateway offline if no message for 3 poll intervals


def get_config(_req):
    return storage.get_config()


def get_live(_req):
    cfg = storage.get_config()
    data = storage.get_json("latest/data.json") or {}
    system = storage.get_json("latest/system.json") or {}
    poll_s = cfg["gateway"].get("poll_interval", 20)
    now = timeutil.now_ms()
    rx = data.get("rx_ms")
    age_s = round((now - rx) / 1000, 1) if rx else None

    values, alarms = {}, []
    labels = {s["key"]: s for s in cfg["sensors"]}
    for dev, sensors in (data.get("devices") or {}).items():
        for name, r in sensors.items():
            key = f"{dev}.{name}"
            values[key] = r
            kind = r.get("alarm") if r.get("status") == "OK" else r.get("status")
            if kind and kind != "NORMAL":
                sc = labels.get(key, {})
                alarms.append({"key": key, "label": sc.get("label", key), "kind": kind, "val": r.get("val"),
                               "unit": sc.get("unit", ""), "min": sc.get("min"), "max": sc.get("max")})
    return {
        "now_ms": now, "rx_ms": rx, "ts": data.get("ts"), "age_s": age_s,
        "online": age_s is not None and age_s < poll_s * OFFLINE_FACTOR,
        "values": values, "alarms": alarms, "system": system,
    }


def _range(req, default_hours=1):
    to_ms = timeutil.parse_ms(req.query["to"]) if req.query.get("to") else timeutil.now_ms()
    from_ms = (timeutil.parse_ms(req.query["from"]) if req.query.get("from")
               else to_ms - default_hours * 3_600_000)
    timeutil.validate_range(from_ms, to_ms)
    return from_ms, to_ms


def fetch_history(from_ms, to_ms, keys, bucket_ms):
    rows = athena.run(queries.history(from_ms, to_ms, keys, bucket_ms))
    return [{"k": r["k"], "t": int(r["t"]), "avg": athena.num(r["avg"]), "min": athena.num(r["min"]),
             "max": athena.num(r["max"]), "n": int(r["n"])} for r in rows]


def fetch_abnormal(from_ms, to_ms, keys=None):
    rows = athena.run(queries.abnormal_rows(from_ms, to_ms, keys))
    return [{"k": r["k"], "t": int(r["t"]), "val": athena.num(r["val"]), "kind": r["kind"]} for r in rows]


def get_history(req):
    from_ms, to_ms = _range(req)
    keys = queries.parse_sensor_keys(req.query.get("sensors"))
    bucket_ms = int(req.query["bucket_ms"]) if req.query.get("bucket_ms") else timeutil.pick_bucket_ms(from_ms, to_ms)
    series = {}
    for p in fetch_history(from_ms, to_ms, keys, bucket_ms):
        series.setdefault(p["k"], []).append([p["t"], p["avg"], p["min"], p["max"]])
    return {"from_ms": from_ms, "to_ms": to_ms, "bucket_ms": bucket_ms, "series": series}


def get_alarms(req):
    from_ms, to_ms = _range(req, default_hours=24)
    keys = queries.parse_sensor_keys(req.query.get("sensors"))
    cfg = storage.get_config()
    poll_ms = cfg["gateway"].get("poll_interval", 20) * 1000
    labels = {s["key"]: s for s in cfg["sensors"]}
    episodes = collapse_episodes(fetch_abnormal(from_ms, to_ms, keys), max_gap_ms=int(poll_ms * 2.5))
    for e in episodes:
        sc = labels.get(e["k"], {})
        e.update(label=sc.get("label", e["k"]), unit=sc.get("unit", ""))
    return {"from_ms": from_ms, "to_ms": to_ms, "episodes": sorted(episodes, key=lambda e: -e["start"])}


def get_export(req):
    """Bucketed readings for a range as CSV or XLSX in S3; returns a download URL."""
    from_ms, to_ms = _range(req)
    keys = queries.parse_sensor_keys(req.query.get("sensors"))
    fmt = req.query.get("format", "xlsx")
    if fmt not in ("xlsx", "csv"):
        raise ValueError("format must be xlsx or csv")
    cfg = storage.get_config()
    tz = cfg["gateway"].get("city") or "UTC"
    bucket_ms = int(req.query["bucket_ms"]) if req.query.get("bucket_ms") else timeutil.pick_bucket_ms(from_ms, to_ms, 2000)
    rows = fetch_history(from_ms, to_ms, keys, bucket_ms)

    head = ["time_local", "time_utc", "sensor", "unit", "avg", "min", "max", "samples"]
    units = {s["key"]: s.get("unit", "") for s in cfg["sensors"]}
    table = [[timeutil.local_str(r["t"], tz), timeutil.iso_utc(r["t"]), r["k"], units.get(r["k"], ""),
              r["avg"], r["min"], r["max"], r["n"]] for r in rows]

    name = f"venko_{timeutil.local_str(from_ms, tz, '%Y%m%d-%H%M')}_{timeutil.local_str(to_ms, tz, '%Y%m%d-%H%M')}"
    key = f"exports/{timeutil.now_ms()}/{name}.{fmt}"
    if fmt == "csv":
        buf = io.StringIO()
        csv.writer(buf).writerows([head] + table)
        storage.put_bytes(key, buf.getvalue().encode("utf-8"), "text/csv")
    else:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Lecturas"
        ws.append(head)
        for r in table:
            ws.append(r)
        out = io.BytesIO()
        wb.save(out)
        storage.put_bytes(key, out.getvalue(),
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return {"url": storage.presign(key), "rows": len(table), "bucket_ms": bucket_ms}
