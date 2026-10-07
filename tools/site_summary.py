#!/usr/bin/env python3
"""Write docs/sites/<key>.md: everything worth knowing about one site in one page.

Collects the site's AWS resources (stack, buckets, IoT thing/certificate/policy/
rules, Firehose, Lambdas, API, CloudFront, Athena, DynamoDB), its gateway config
from Akvo_Green, and its live status (last data received, simulated or real),
plus ready-to-run commands. Rerun it after any change to the site.

Secrets are never written: the API key appears only as its id (with the command
that reveals it), certificates only as their id/status and file locations.

  python3 tools/site_summary.py cmd4 --pi pi@192.168.68.129
  python3 tools/site_summary.py default        # site 1 (stack sam-app)

<key> is the samconfig.toml env (`default` = site 1). --pi is remembered from the
previous summary when omitted.
"""
import argparse
import json
import re
import shlex
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

ROOT = Path(__file__).resolve().parent.parent
AKVO_GREEN = ROOT.parent / "Akvo_Green"
OUT_DIR = ROOT / "docs" / "sites"
NA = "*not found*"


def try_(fn, default=None):
    """Run one AWS lookup; a missing resource becomes `default`, not a crash."""
    try:
        return fn()
    except (ClientError, BotoCoreError, KeyError, IndexError) as e:
        print(f"warning: {e}", file=sys.stderr)
        return default


def samconfig_env(key: str) -> dict:
    cfg = tomllib.loads((ROOT / "infra" / "samconfig.toml").read_text())
    if key not in cfg:
        sys.exit(f"error: no [{key}.*] section in infra/samconfig.toml (envs: {', '.join(k for k, v in cfg.items() if isinstance(v, dict))})")
    deploy = cfg[key]["deploy"]["parameters"]
    params = dict(p.split("=", 1) for p in shlex.split(deploy.get("parameter_overrides", "")))
    region = deploy.get("region") or cfg[key].get("global", {}).get("parameters", {}).get("region", "us-east-1")
    return {"stack": deploy["stack_name"], "region": region, "params": params}


def site_paths(key: str) -> dict:
    if key == "default":
        return {"config_dir": AKVO_GREEN / "config_data", "certs": AKVO_GREEN / "gateway" / "certs",
                "overrides": ROOT / "config" / "dashboard_overrides.json", "push": "root"}
    return {"config_dir": AKVO_GREEN / "sites" / key / "config_data", "certs": AKVO_GREEN / "sites" / key / "certs",
            "overrides": ROOT / "config" / "sites" / f"{key}.json", "push": key}


def rel(p: Path) -> str:
    """Path as written in the README: relative to the Venko projects folder."""
    try:
        return str(p.relative_to(ROOT.parent))
    except ValueError:
        return str(p)


def previous_pi(path: Path) -> str | None:
    if not path.exists():
        return None
    m = re.search(r"^\| Pi \(SSH\) \| `([^`]+)` \|", path.read_text(), re.M)
    return m.group(1) if m else None


def collect(key: str, pi: str | None) -> dict:
    env = samconfig_env(key)
    stack, region = env["stack"], env["region"]
    s = boto3.Session(region_name=region)
    cfn, s3, iot = s.client("cloudformation"), s.client("s3"), s.client("iot")
    acct = s.client("sts").get_caller_identity()["Account"]

    st = try_(lambda: cfn.describe_stacks(StackName=stack)["Stacks"][0])
    if not st:
        sys.exit(f"error: stack {stack} not found in {region} - deploy it first (sam deploy --config-env {key})")
    outputs = {o["OutputKey"]: o["OutputValue"] for o in st.get("Outputs", [])}
    params = {p["ParameterKey"]: p.get("ParameterValue", "") for p in st.get("Parameters", [])}
    res = {r["LogicalResourceId"]: r["PhysicalResourceId"]
           for r in cfn.describe_stack_resources(StackName=stack)["StackResources"]}

    paths = site_paths(key)
    gw_cfg = try_(lambda: json.loads((paths["config_dir"] / "config.json").read_text()), {})
    if not gw_cfg:
        print(f"warning: no gateway config at {paths['config_dir'] / 'config.json'}", file=sys.stderr)
    aws_cfg, gw = gw_cfg.get("aws", {}), gw_cfg.get("gateway", gw_cfg.get("system", {}))
    overrides = try_(lambda: json.loads(paths["overrides"].read_text()), {})
    n_sensors = sum(len([x for x in d.get("sensors", []) if x.get("enabled", True)])
                    for d in gw_cfg.get("devices", []) if d.get("enabled", True))

    # IoT: thing (= client id) -> certificate -> attached policies
    client_id = aws_cfg.get("client_id", "")
    certs = []
    for arn in try_(lambda: iot.list_thing_principals(thingName=client_id)["principals"], []) if client_id else []:
        cert_id = arn.rsplit("/", 1)[-1]
        desc = try_(lambda: iot.describe_certificate(certificateId=cert_id)["certificateDescription"], {})
        pols = []
        for p in try_(lambda: iot.list_attached_policies(target=arn)["policies"], []):
            doc = try_(lambda: iot.get_policy(policyName=p["policyName"])["policyDocument"], "{}")
            pols.append({"name": p["policyName"], "statements": json.loads(doc).get("Statement", [])})
        certs.append({"id": cert_id, "status": desc.get("status", NA),
                      "created": desc.get("creationDate"), "policies": pols})
    rules = []
    for logical in ("DataRule", "SystemRule"):
        name = res.get(logical)
        r = try_(lambda: iot.get_topic_rule(ruleName=name)["rule"], {}) if name else {}
        topic = re.search(r"FROM\s+'([^']+)'", r.get("sql", ""))
        rules.append({"name": name or NA, "topic": topic.group(1) if topic else NA,
                      "enabled": not r.get("ruleDisabled", True) if r else None})

    lam = s.client("lambda")
    functions = []
    for logical in ("ApiFunction", "ReportFunction"):
        name = res.get(logical)
        c = try_(lambda: lam.get_function_configuration(FunctionName=name), {}) if name else {}
        functions.append({"name": name or NA, "runtime": c.get("Runtime", NA),
                          "modified": (c.get("LastModified") or "")[:19]})
    schedule = try_(lambda: s.client("events").describe_rule(Name=res["ReportFunctionWeekly"])["ScheduleExpression"], NA)
    dist = try_(lambda: s.client("cloudfront").get_distribution(Id=res["WebDistribution"])["Distribution"], {})

    data_bucket = outputs.get("DataBucketName", "")
    live = {}
    for name in ("data", "system"):
        h = try_(lambda: s3.head_object(Bucket=data_bucket, Key=f"latest/{name}.json"))
        live[name] = h["LastModified"] if h else None
    latest = try_(lambda: json.loads(s3.get_object(Bucket=data_bucket, Key="latest/data.json")["Body"].read()), {})
    errors = try_(lambda: s3.list_objects_v2(Bucket=data_bucket, Prefix="errors/", MaxKeys=1000).get("KeyCount", 0), None)

    return {
        "key": key, "stack": stack, "region": region, "account": acct, "pi": pi,
        "status": st["StackStatus"], "created": st["CreationTime"], "updated": st.get("LastUpdatedTime"),
        "outputs": outputs, "params": params, "res": res, "paths": paths,
        "gw": gw, "aws_cfg": aws_cfg, "simulate": gw_cfg.get("modbus", {}).get("simulate", False),
        "n_sensors": n_sensors, "title": overrides.get("site", {}).get("title", ""),
        "certs": certs, "rules": rules, "functions": functions, "schedule": schedule,
        "cf_domain": dist.get("DomainName", NA), "cf_status": dist.get("Status", NA),
        "live": live, "live_simulated": latest.get("simulated", False) if latest else None,
        "live_ts": latest.get("ts") if latest else None, "errors": errors,
    }


def listed(v) -> str:
    """IAM fields are a string or a list of strings."""
    return v if isinstance(v, str) else ", ".join(v or [])


def age(t: datetime | None, now: datetime) -> str:
    if not t:
        return "**never** - nothing received yet"
    secs = int((now - t).total_seconds())
    ago = f"{secs}s" if secs < 120 else f"{secs // 60} min" if secs < 7200 else f"{secs // 3600} h" if secs < 172800 else f"{secs // 86400} days"
    return f"{t:%Y-%m-%d %H:%M:%S} UTC ({ago} ago)"


def render(d: dict, now: datetime) -> str:
    o, p, r, key, stack, region = d["outputs"], d["params"], d["res"], d["key"], d["stack"], d["region"]
    data_b, web_b = o.get("DataBucketName", NA), o.get("WebBucketName", NA)
    env_flag = "" if key == "default" else f" --config-env {key}"
    cfg_path = d["paths"]["config_dir"] / "config.json"
    pi = d["pi"] or "pi@<pi-address>"
    online = d["live"]["data"] and (now - d["live"]["data"]).total_seconds() < 3 * (d["gw"].get("poll_interval") or 20)
    mode = ("**SIMULATED** - synthetic data, not real sensors" if d["live_simulated"]
            else "real sensors" if d["live"]["data"] else "unknown (no data yet)")
    cfg_mode = "1 (simulated)" if d["simulate"] else "0 (real sensors)"

    L = [f"# Site `{key}` - {d['title'] or stack}", "",
         f"Generated by `tools/site_summary.py {key}` on {now:%Y-%m-%d %H:%M} UTC. Rerun it after changing the site; "
         "don't edit by hand. No secrets in this file: the API key is listed by id only.", "",
         "## At a glance", "",
         "| | |", "|---|---|",
         f"| Web page (dashboard) | {o.get('DashboardUrl', NA)} |",
         f"| S3 data bucket | `{data_b}` |",
         f"| S3 web bucket | `{web_b}` |",
         f"| Status | {'🟢 online' if online else '🔴 no recent data'} - last data {age(d['live']['data'], now)} |",
         f"| Data mode | {mode} |",
         f"| Pi (SSH) | `{d['pi'] or 'unknown - rerun with --pi user@host'}` |",
         f"| Gateway / MQTT client id | `{d['aws_cfg'].get('client_id', NA)}` |",
         f"| Topics | `{p.get('DataTopic', NA)}`, `{p.get('SystemTopic', NA)}` |",
         f"| Sensors | {d['n_sensors']} |",
         f"| AWS account / region | `{d['account']}` / `{region}` |",
         f"| Stack | `{stack}` ({d['status']}), samconfig env `{key}` |",
         "", "## Live data", "",
         "| | |", "|---|---|",
         f"| `latest/data.json` | {age(d['live']['data'], now)} |",
         f"| `latest/system.json` | {age(d['live']['system'], now)} |",
         f"| Last payload `ts` | {d['live_ts'] or NA} |",
         f"| Payload `simulated` | {d['live_simulated']} |",
         f"| `modbus.csv` `simulate_enabled` (local build) | {cfg_mode} |",
         f"| Ingestion errors (`errors/`) | {d['errors'] if d['errors'] is not None else NA} objects{' - inspect them!' if d['errors'] else ''} |",
         "", "## Web & API", "",
         "| | |", "|---|---|",
         f"| Dashboard URL | {o.get('DashboardUrl', NA)} |",
         f"| CloudFront distribution | `{r.get('WebDistribution', NA)}` ({d['cf_domain']}, {d['cf_status']}) |",
         f"| API URL | {o.get('ApiUrl', NA)} |",
         f"| API id / stage | `{r.get('Api', NA)}` / `prod` |",
         f"| API key id | `{o.get('ApiKeyId', NA)}` (value: `aws apigateway get-api-key --api-key {o.get('ApiKeyId', '')} --include-value --query value --output text`) |",
         f"| CORS `AllowedOrigin` | `{p.get('AllowedOrigin', NA)}` |",
         "", "## S3 buckets", "",
         "| Bucket | Purpose |", "|---|---|",
         f"| `{data_b}` | Data: `raw/` history (Athena), `latest/` live view, `config/config.json`, `reports/`, `exports/`, `errors/`. Retained if the stack is deleted |",
         f"| `{web_b}` | Dashboard files + `app-config.js`; readable only through CloudFront |",
         "", "## AWS IoT", "",
         "| | |", "|---|---|",
         f"| Endpoint | `{d['aws_cfg'].get('host', NA)}` |",
         f"| Thing | `{d['aws_cfg'].get('client_id', NA)}` |"]
    if not d["certs"]:
        L.append(f"| Certificate | {NA} - no IoT thing named after the client id, or no certificate attached to it; "
                 "the Pi may connect with a certificate that isn't linked to a thing |")
    for c in d["certs"]:
        L.append(f"| Certificate | `{c['id']}` - **{c['status']}**, created {c['created']:%Y-%m-%d} |" if c["created"]
                 else f"| Certificate | `{c['id']}` - **{c['status']}** |")
        for pol in c["policies"]:
            allows = "; ".join(f"{st.get('Effect', 'Allow')} {listed(st.get('Action'))} on `{listed(st.get('Resource'))}`"
                               for st in pol["statements"])
            L.append(f"| Policy | `{pol['name']}`: {allows} |")
    for rule in d["rules"]:
        state = "enabled" if rule["enabled"] else "DISABLED" if rule["enabled"] is False else NA
        L.append(f"| Topic rule | `{rule['name']}` ← `{rule['topic']}` ({state}) |")
    L += [f"| Certificate files | `{rel(d['paths']['certs'])}/` (git-ignored; private key exists only there and on the Pi) |",
          "", "## Processing & storage", "",
          "| Resource | Name |", "|---|---|",
          f"| Firehose (data / system) | `{r.get('DataStream', NA)}` / `{r.get('SystemStream', NA)}` |"]
    for f in d["functions"]:
        L.append(f"| Lambda | `{f['name']}` ({f['runtime']}, updated {f['modified'] or NA}) - logs `/aws/lambda/{f['name']}` |")
    L += [f"| Weekly report schedule | `{d['schedule']}` (UTC), report timezone `{p.get('ReportTimezone', NA)}` |",
          f"| DynamoDB (tests, comments) | `{r.get('AppTable', NA)}` |",
          f"| Glue DB / tables | `{r.get('GlueDatabase', NA)}` / `readings_raw`, `system_raw` (from {p.get('PartitionStartDate', NA)}) |",
          f"| Athena workgroup | `{r.get('AthenaWorkGroup', NA)}` |",
          "| IAM roles | " + ", ".join(f"`{r[k]}`" for k in ("ApiFunctionRole", "ReportFunctionRole", "FirehoseRole", "IotRuleRole") if k in r) + " |",
          "", "## Gateway", "",
          "| | |", "|---|---|",
          f"| Config folder | `{rel(d['paths']['config_dir'])}/` (`devices.csv`, `modbus.csv`, `system.csv`, `aws.csv` → `config.json`) |",
          f"| Gateway id | `{d['gw'].get('gateway_id', NA)}` |",
          f"| Time zone | `{d['gw'].get('city', NA)}` |",
          f"| Poll / system interval | {d['gw'].get('poll_interval', NA)} s / {d['gw'].get('system_interval', NA)} s |",
          f"| Modbus mode | {cfg_mode} |",
          f"| Dashboard labels | `{rel(d['paths']['overrides'])}` |",
          "", "## Commands", "",
          "```bash",
          "# is data arriving?",
          f"aws s3 ls s3://{data_b}/latest/",
          f"aws s3 cp s3://{data_b}/latest/data.json - | head -c 300; echo",
          "",
          "# gateway on the Pi",
          f"ssh {pi} 'systemctl status akvo-green --no-pager; journalctl -u akvo-green -n 30 --no-pager'",
          "",
          "# rebuild gateway config and send it to the Pi (from Akvo_Green/gateway)",
          "python3 config_manager.py build" + ("" if key == "default" else f" --config-dir ../sites/{key}/config_data"),
          f"cd .. && tools/push_site.sh {d['paths']['push']} {pi}",
          "",
          "# stack and dashboard (from VenkoDemo)",
          f"cd infra && sam build --template template.yaml && sam deploy{env_flag}",
          f"bash tools/deploy_web.sh {stack} {rel(cfg_path).replace('Akvo_Green', '../Akvo_Green', 1)} {rel(d['paths']['overrides']).replace('VenkoDemo/', '', 1)}",
          "",
          "# weekly report on demand",
          f"aws lambda invoke --function-name {next((f['name'] for f in d['functions'] if f['name'].endswith('-report')), stack + '-report')} \\",
          "  --cli-binary-format raw-in-base64-out --payload '{\"kind\":\"weekly\"}' /dev/stdout",
          "",
          "# disable this Pi's certificate (lost/stolen Pi)",
          *(f"aws iot update-certificate --certificate-id {c['id']} --new-status INACTIVE" for c in d["certs"]),
          "```", ""]
    return "\n".join(L)


def glance(page: str) -> dict:
    """The "At a glance" rows of one generated site page, label -> value."""
    section = page.split("## At a glance", 1)[-1].split("\n## ", 1)[0]
    return {m.group(1): m.group(2) for m in re.finditer(r"^\| ([^|]+?) \| (.+) \|$", section, re.M)}


def write_index(out_dir: Path) -> Path:
    """docs/sites/README.md: every site side by side, built from the site pages
    already generated (no AWS calls), so it always matches them."""
    pages = sorted(out_dir.glob("*.md"), key=lambda f: (f.stem != "default", f.stem))
    rows = [(f.stem, glance(f.read_text()), f.read_text()) for f in pages if f.name != "README.md"]
    when = lambda page: (re.search(r"on (\d{4}-\d\d-\d\d \d\d:\d\d) UTC", page) or [None, "?"])[1]
    name = lambda k: "site 1 (`default`)" if k == "default" else f"`{k}`"
    pi = lambda v: v if v.startswith("`") and "unknown" not in v else "unknown"
    mode = lambda v: "**simulated**" if "SIMULATED" in v else "real sensors" if "real" in v else "unknown (no data yet)"
    status = lambda v: v.split(" - ")[0]
    L = ["# Sites", "",
         "Every site at a glance. Generated by `tools/site_summary.py` from the pages in this folder - "
         "rerun it for a site (`python3 tools/site_summary.py <key> --pi pi@<address>`) rather than editing this file. "
         "Each site's page has the full detail: IoT certificate and policy, API, Lambdas, Athena, commands.", "",
         "## Gateways", "",
         "| Site | Client id | Topics | Pi | Modbus | Status (when generated) |", "|---|---|---|---|---|---|"]
    L += [f"| [{name(k)}]({k}.md) | {g.get('Gateway / MQTT client id', NA)} | {g.get('Topics', NA)} | {pi(g.get('Pi (SSH)', ''))} | "
          f"{mode(g.get('Data mode', ''))} | {status(g.get('Status', NA))} ({when(page)} UTC) |" for k, g, page in rows]
    L += ["", "## AWS", "",
          "| Site | Web page (dashboard) | S3 data bucket | S3 web bucket | Stack |", "|---|---|---|---|---|"]
    L += [f"| [{name(k)}]({k}.md) | {g.get('Web page (dashboard)', NA)} | {g.get('S3 data bucket', NA)} | "
          f"{g.get('S3 web bucket', NA)} | {g.get('Stack', NA).split(' (')[0]} |" for k, g, page in rows]
    index = out_dir / "README.md"
    index.write_text("\n".join(L) + "\n")
    return index


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("key", help="samconfig.toml env of the site (default = site 1)")
    ap.add_argument("--pi", help="SSH target of the site's Pi, e.g. pi@192.168.68.129")
    ap.add_argument("--out", type=Path, help="output file (default docs/sites/<key>.md)")
    args = ap.parse_args()

    out = args.out or OUT_DIR / f"{args.key}.md"
    now = datetime.now(UTC)
    d = collect(args.key, args.pi or previous_pi(out))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(d, now))
    print(f"wrote {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    index = write_index(out.parent)
    print(f"wrote {index.relative_to(ROOT) if index.is_relative_to(ROOT) else index}")


if __name__ == "__main__":
    main()
