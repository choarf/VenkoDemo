#!/usr/bin/env python3
"""Send a site's gateway config to its Pi through the AWS IoT named shadow "config".

Reads the committed ../Akvo_Green/sites/<key>/config_data/config.json, keeps the
parts the gateway accepts remotely (Akvo_Green gateway/shadow_sync.py: every
section except aws and modbus, plus modbus.simulate), adds the Akvo_Green commit
as meta.git_rev, and writes it as the shadow's desired state. The Pi applies it
within seconds, or when it next connects, and reports back the hash of what it
runs. Then it publishes the dashboard config (sync_config.py) so the dashboard
and the Pi never differ.

  python3 tools/push_shadow.py <key>             # send, update the dashboard config, wait for the Pi
  python3 tools/push_shadow.py <key> --status    # desired vs reported
  python3 tools/push_shadow.py <key> --dry-run   # print what would be sent

Refuses when the site's files in Akvo_Green have uncommitted changes (the Pi
must run something git has), when the config's client_id isn't AKVO_<key>, when
its topics differ from the stack's, or when aws.shadow_enabled is off (the Pi
wouldn't listen).
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AKVO = ROOT.parent / "Akvo_Green"
sys.path.insert(0, str(AKVO / "gateway"))
from shadow_sync import SHADOW_NAME, config_hash, shadow_subset  # noqa: E402

SHADOW_LIMIT = 8 * 1024   # bytes, AWS IoT shadow state document


def build_desired(config, git_rev):
    """The desired state for `config`, with meta.git_rev and its hash."""
    config = json.loads(json.dumps(config))
    config.setdefault("meta", {})["git_rev"] = git_rev
    desired = shadow_subset(config)
    desired["config_hash"] = config_hash(config)
    return desired


def nulls_for_removed(old, new):
    """Shadow updates merge objects key by key, so a key dropped from the
    config would stay in desired. Returns `new` with None (= delete) for every
    key of `old` it no longer has, at any object depth. Lists are replaced
    whole by the shadow, so they need nothing."""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return new
    out = {k: nulls_for_removed(old.get(k), v) for k, v in new.items()}
    out.update({k: None for k in old if k not in new})
    return out


def status_line(state):
    desired, reported = state.get("desired") or {}, state.get("reported") or {}
    if not desired:
        return "no desired config in the shadow yet"
    if reported.get("error"):
        return f"REFUSED by the gateway: {reported['error']}"
    if reported.get("config_hash") == desired.get("config_hash"):
        notes = []
        if reported.get("pending_service_restart"):
            notes.append(f"restart {reported['pending_service_restart']} (push_site.sh) to finish")
        return "applied" + (f" - {'; '.join(notes)}" if notes else "")
    return "PENDING - the gateway hasn't applied it yet (offline, or restarting)"


def git(*args):
    return subprocess.run(["git", "-C", str(AKVO), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def stack_param(cfn, stack, key):
    params = cfn.describe_stacks(StackName=stack)["Stacks"][0]["Parameters"]
    return next((p["ParameterValue"] for p in params if p["ParameterKey"] == key), None)


def stack_output(cfn, stack, key):
    outputs = cfn.describe_stacks(StackName=stack)["Stacks"][0]["Outputs"]
    return next((o["OutputValue"] for o in outputs if o["OutputKey"] == key), None)


def iot_data():
    import boto3
    endpoint = boto3.client("iot").describe_endpoint(endpointType="iot:Data-ATS")["endpointAddress"]
    return boto3.client("iot-data", endpoint_url=f"https://{endpoint}")


def get_shadow(client, thing):
    try:
        doc = client.get_thing_shadow(thingName=thing, shadowName=SHADOW_NAME)
    except client.exceptions.ResourceNotFoundException:
        return {}
    return json.loads(doc["payload"].read())


def print_status(doc):
    state, meta = doc.get("state", {}), doc.get("metadata", {})
    desired, reported = state.get("desired") or {}, state.get("reported") or {}

    def when(side):
        ts = meta.get(side, {}).get("config_hash", {}).get("timestamp")
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "-"

    print(f"  desired : {desired.get('config_hash', '-')[:12]}  git {desired.get('meta', {}).get('git_rev', '-')}"
          f"  simulate {desired.get('modbus', {}).get('simulate', False)}  (sent {when('desired')})")
    print(f"  reported: {reported.get('config_hash', '-')[:12]}  git {reported.get('git_rev', '-')}"
          f"  simulate {reported.get('simulate', '-')}  (at {when('reported')})")
    if reported.get("restart_for"):
        print(f"  restarting for: {reported['restart_for']}")
    print(f"  -> {status_line(state)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("key", help="site key (lowercase), e.g. akvo01002")
    ap.add_argument("--status", action="store_true", help="show desired vs reported and exit")
    ap.add_argument("--dry-run", action="store_true", help="print the desired state, send nothing")
    ap.add_argument("--no-dashboard", action="store_true", help="don't run sync_config.py afterwards")
    ap.add_argument("--wait", type=int, default=90, metavar="S", help="seconds to wait for the Pi (0 = don't)")
    args = ap.parse_args()

    key = args.key
    thing, stack = f"AKVO_{key}", f"venko-{key}"
    site_dir = AKVO / "sites" / key
    source = site_dir / "config_data" / "config.json"
    if not source.is_file():
        sys.exit(f"error: {source} not found")

    if args.status:
        print(f"{thing} shadow '{SHADOW_NAME}':")
        print_status(get_shadow(iot_data(), thing))
        return

    config = json.loads(source.read_text())
    aws = config.get("aws", {})
    if aws.get("client_id") != thing:
        sys.exit(f"error: {source} has client_id {aws.get('client_id')!r}, expected {thing!r}")
    if not aws.get("shadow_enabled"):
        sys.exit(f"error: aws.shadow_enabled is off for {key} - the gateway doesn't listen to the shadow "
                 f"(set shadow_enabled=1 in aws.csv, build, commit, push_site.sh)")
    dirty = git("status", "--porcelain", "--", f"sites/{key}")
    if dirty:
        sys.exit(f"error: uncommitted changes in Akvo_Green sites/{key} - commit first, the Pi must "
                 f"run a config git has:\n{dirty}")
    git_rev = git("rev-parse", "--short", "HEAD")

    desired = build_desired(config, git_rev)
    size = len(json.dumps({"desired": desired}, separators=(",", ":")).encode())
    print(f"{thing}: {sum(len(d['sensors']) for d in desired['devices'])} sensors, "
          f"simulate {desired.get('modbus', {}).get('simulate', False)}, git {git_rev}, "
          f"hash {desired['config_hash'][:12]}, {size} bytes")
    if size > SHADOW_LIMIT * 0.8:
        print(f"warning: {size} bytes is close to the shadow's {SHADOW_LIMIT} byte limit", file=sys.stderr)
    if args.dry_run:
        print(json.dumps(desired, indent=2, ensure_ascii=False))
        return

    import boto3
    cfn = boto3.client("cloudformation")
    for param, topic in (("DataTopic", aws.get("topic_pub")), ("SystemTopic", aws.get("topic_system"))):
        if stack_param(cfn, stack, param) != topic:
            sys.exit(f"error: config topic {topic!r} differs from stack {stack} {param} - "
                     f"wrong config.json for this site? Nothing was sent.")

    client = iot_data()
    old = get_shadow(client, thing).get("state", {}).get("desired") or {}
    payload = {"state": {"desired": nulls_for_removed(old, desired)}}
    sent = json.loads(client.update_thing_shadow(
        thingName=thing, shadowName=SHADOW_NAME,
        payload=json.dumps(payload, ensure_ascii=False).encode())["payload"].read())["timestamp"]
    print(f"sent desired config to {thing} shadow '{SHADOW_NAME}'")

    if not args.no_dashboard:
        bucket = stack_output(cfn, stack, "DataBucketName")
        overrides = ROOT / "config" / "sites" / f"{key}.json"
        subprocess.run([sys.executable, str(ROOT / "tools" / "sync_config.py"), "--source", str(source),
                        "--overrides", str(overrides), "--bucket", bucket], check=True)

    if args.wait <= 0:
        return
    print(f"waiting up to {args.wait} s for the gateway to report...")
    deadline = time.time() + args.wait
    while True:
        doc = get_shadow(client, thing)
        reported = doc.get("state", {}).get("reported") or {}
        # Only a report made after this send counts - an error left from an
        # earlier push mustn't look like a refusal of this one.
        fresh = doc.get("metadata", {}).get("reported", {}).get("config_hash", {}).get("timestamp", 0) >= sent
        done = reported.get("config_hash") == desired["config_hash"] and not reported.get("restart_for")
        if done or (fresh and reported.get("error")) or time.time() >= deadline:
            break
        time.sleep(3)
    print_status(doc)
    if not done:
        sys.exit(1)


if __name__ == "__main__":
    main()
