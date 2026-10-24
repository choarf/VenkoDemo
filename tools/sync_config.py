#!/usr/bin/env python3
"""Publish the dashboard config derived from Akvo_Green's config.json.

Keeps only what the dashboard/reports need (gateway timing + sensors with
units and min/max) - the aws/modbus sections (cert paths, endpoint, serial
port) are never uploaded - and merges the site's config/sites/<key>.json for
labels, unit display names and grouping. Defaults to site cmd4's files.

  python3 tools/sync_config.py --source ../Akvo_Green/sites/<key>/config_data/config.json \
      --overrides config/sites/<key>.json --out build/config.json
  python3 tools/sync_config.py --source … --overrides … --bucket venko-<key>-<acct>-<region>   # upload to s3://.../config/config.json

New site: --init writes a starter config/sites/<key>.json from the gateway's
config.json instead - one entry per enabled sensor. Labels, groups, decimals and
unit names are reused from the other sites' files where the sensor key or unit
matches; anything else gets the raw sensor name, to translate by hand.

  python3 tools/sync_config.py --init --source ../Akvo_Green/sites/<key>/config_data/config.json \
      --overrides config/sites/<key>.json
"""
import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT.parent / "Akvo_Green" / "sites" / "cmd4" / "config_data" / "config.json"
DEFAULT_OVERRIDES = ROOT / "config" / "sites" / "cmd4.json"
S3_KEY = "config/config.json"


def build(source: dict, overrides: dict) -> dict:
    units = overrides.get("units", {})
    defaults = overrides.get("defaults", {})
    per_sensor = overrides.get("sensors", {})
    sensors = []
    for dev in source.get("devices", []):
        if not dev.get("enabled", True):
            continue
        for s in dev.get("sensors", []):
            if not s.get("enabled", True):
                continue
            key = f'{dev["id"]}.{s["name"]}'
            o = per_sensor.get(key, {})
            sensors.append({
                "key": key, "device": dev["id"], "name": s["name"],
                "label": o.get("label", s["name"]),
                "unit": o.get("unit", units.get(s.get("unit", ""), s.get("unit", ""))),
                "min": s.get("min"), "max": s.get("max"),
                "group": o.get("group", ""),
                "decimals": o.get("decimals", defaults.get("decimals", 2)),
                "warn_margin": o.get("warn_margin", defaults.get("warn_margin", 0.1)),
            })
    unknown = set(per_sensor) - {s["key"] for s in sensors}
    if unknown:
        print(f"warning: overrides for sensors not in config: {', '.join(sorted(unknown))}", file=sys.stderr)

    gw = source.get("gateway", {})
    return {
        "derived": derived_entries(overrides.get("derived", []), {s["key"] for s in sensors},
                                   defaults.get("warn_margin", 0.1)),
        "system_limits": system_limits(overrides.get("system_limits", {})),
        "generated_at": datetime.now(UTC).isoformat(),
        "source_hash": source.get("meta", {}).get("hash", ""),
        "site": overrides.get("site", {}),
        "groups": overrides.get("groups", []),
        "gateway": {k: gw.get(k) for k in ("gateway_id", "client", "city", "poll_interval", "system_interval")},
        "topics": {"data": source.get("aws", {}).get("topic_pub"), "system": source.get("aws", {}).get("topic_system")},
        "sensors": sensors,
    }


DERIVED_OPS = {"ratio": ("a", "b"), "diff": ("a", "b"), "balance": ("a", "minus")}


def derived_entries(entries: list, sensor_keys: set, warn_margin: float) -> list:
    """Process indicators (backend/common/derived.py): fixed operations on existing sensors."""
    out, seen = [], set()
    for i, d in enumerate(entries):
        where = f"derived[{i}] ({d.get('key', '?')})"
        key = d.get("key", "")
        if not key.replace("_", "").isalnum() or key in seen:
            raise ValueError(f"{where}: key must be unique letters/digits/_")
        seen.add(key)
        op = d.get("op")
        if op not in DERIVED_OPS:
            raise ValueError(f"{where}: op must be one of {', '.join(DERIVED_OPS)}")
        refs = [d.get("a")] + (d.get("minus") or [] if op == "balance" else [d.get("b")])
        if op == "balance" and not d.get("minus"):
            raise ValueError(f"{where}: balance needs a non-empty 'minus' list")
        missing = [r for r in refs if r not in sensor_keys]
        if missing:
            raise ValueError(f"{where}: unknown sensor(s) {', '.join(map(str, missing))}")
        lo, hi = d.get("min"), d.get("max")
        if not all(isinstance(v, (int, float)) for v in (lo, hi)) or lo >= hi:
            raise ValueError(f"{where}: needs numeric min < max")
        e = {"key": key, "label": d.get("label", key), "unit": d.get("unit", ""), "op": op, "a": d["a"],
             "min": lo, "max": hi, "decimals": d.get("decimals", 1), "warn_margin": d.get("warn_margin", warn_margin),
             "provisional": bool(d.get("provisional", False))}
        if op == "balance":
            e["minus"] = list(d["minus"])
        else:
            e["b"] = d["b"]
        if op == "ratio":
            e["scale"] = d.get("scale", 1)
        out.append(e)
    return out


SYSTEM_LIMITS = {"cpu": [80, 90], "ram": [80, 90], "disk": [80, 90]}


def system_limits(over: dict) -> dict:
    """Gateway resource thresholds in % ([warning, alarm]) for the Sistema view."""
    limits = dict(SYSTEM_LIMITS)
    for name, pair in over.items():
        if name not in SYSTEM_LIMITS:
            raise ValueError(f"system_limits: unknown resource {name!r} (use cpu, ram, disk)")
        if (not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(v, (int, float)) for v in pair)
                or not 0 < pair[0] < pair[1] <= 100):
            raise ValueError(f"system_limits.{name}: expected [warning, alarm] with 0 < warning < alarm <= 100")
        limits[name] = pair
    return limits


def init_overrides(source: dict, path: Path) -> dict:
    """Starter overrides for a new site, reusing what other sites already say."""
    known_sensors, known_units, defaults, brand = {}, {}, {}, ""
    for other in sorted(path.parent.glob("*.json")):
        o = json.loads(other.read_text())
        for k, v in o.get("sensors", {}).items():
            known_sensors.setdefault(k, v)
        for k, v in o.get("units", {}).items():
            known_units.setdefault(k, v)
        defaults = defaults or o.get("defaults", {})
        brand = brand or o.get("site", {}).get("brand", "")
    sensors, units, groups, reused = {}, {}, [], 0
    for dev in source.get("devices", []):
        if not dev.get("enabled", True):
            continue
        for s in dev.get("sensors", []):
            if not s.get("enabled", True):
                continue
            key = f'{dev["id"]}.{s["name"]}'
            if key in known_sensors:
                sensors[key] = known_sensors[key]
                reused += 1
            else:
                sensors[key] = {"label": s["name"], "group": ""}
            group = sensors[key].get("group")
            if group and group not in groups:
                groups.append(group)
            unit = s.get("unit", "")
            if unit:
                units[unit] = known_units.get(unit, unit)
    print(f"{len(sensors)} sensors ({reused} with labels from other sites, "
          f"{len(sensors) - reused} to label by hand)")
    return {
        "site": {"title": f"Venko Demo – {path.stem.capitalize()}", "brand": brand},
        "units": units,
        "defaults": defaults or {"warn_margin": 0.1, "decimals": 2},
        "groups": groups,
        "sensors": sensors,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    ap.add_argument("--out", type=Path, help="write the result to a local file")
    ap.add_argument("--bucket", help="upload to s3://BUCKET/config/config.json")
    ap.add_argument("--init", action="store_true", help="write a starter --overrides file from --source and exit")
    args = ap.parse_args()

    if args.init:
        if args.overrides.exists():
            sys.exit(f"error: {args.overrides} already exists - edit it, or delete it to start over")
        body = init_overrides(json.loads(args.source.read_text()), args.overrides)
        args.overrides.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {args.overrides} - set site.title, then translate labels and fill in groups")
        return

    cfg = build(json.loads(args.source.read_text()), json.loads(args.overrides.read_text()))
    body = json.dumps(cfg, indent=2, ensure_ascii=False)
    print(f"{len(cfg['sensors'])} sensors, gateway {cfg['gateway']['gateway_id']}, "
          f"sha1 {hashlib.sha1(body.encode()).hexdigest()[:10]}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(body)
        print(f"wrote {args.out}")
    if args.bucket:
        import boto3
        boto3.client("s3").put_object(Bucket=args.bucket, Key=S3_KEY, Body=body.encode(),
                                      ContentType="application/json")
        print(f"uploaded s3://{args.bucket}/{S3_KEY}")
    if not (args.out or args.bucket):
        print(body)


if __name__ == "__main__":
    main()
