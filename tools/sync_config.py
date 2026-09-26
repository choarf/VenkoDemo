#!/usr/bin/env python3
"""Publish the dashboard config derived from Akvo_Green's config.json.

Keeps only what the dashboard/reports need (gateway timing + sensors with
units and min/max) - the aws/modbus sections (cert paths, endpoint, serial
port) are never uploaded - and merges config/dashboard_overrides.json for
labels, unit display names and grouping.

  python3 tools/sync_config.py --source ../Akvo_Green/config_data/config.json --out build/config.json
  python3 tools/sync_config.py --bucket venko-demo-<acct>-<region>        # upload to s3://.../config/config.json
"""
import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = Path("/home/chris/git_reps/claude/projects/Venko/Akvo_Green/config_data/config.json")
DEFAULT_OVERRIDES = ROOT / "config" / "dashboard_overrides.json"
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
        "generated_at": datetime.now(UTC).isoformat(),
        "source_hash": source.get("meta", {}).get("hash", ""),
        "site": overrides.get("site", {}),
        "groups": overrides.get("groups", []),
        "gateway": {k: gw.get(k) for k in ("gateway_id", "city", "poll_interval", "system_interval")},
        "topics": {"data": source.get("aws", {}).get("topic_pub"), "system": source.get("aws", {}).get("topic_system")},
        "sensors": sensors,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    ap.add_argument("--out", type=Path, help="write the result to a local file")
    ap.add_argument("--bucket", help="upload to s3://BUCKET/config/config.json")
    args = ap.parse_args()

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
