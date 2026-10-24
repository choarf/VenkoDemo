#!/usr/bin/env python3
"""AWS cost per site, broken down by category, from Cost Explorer.

Every resource of a site's stack carries the tag aws:cloudformation:stack-name
= venko-<key> (set by CloudFormation, active as a cost allocation tag), plus the
stack tags Project/Site/Client (infra/samconfig.toml `tags`) and a Component tag
per resource (infra/template.yaml: ingest, storage, api, query, reports, web).

  python3 tools/site_costs.py                      # all sites, by service, last 3 months
  python3 tools/site_costs.py cmd4 --by component  # one site, by Component tag
  python3 tools/site_costs.py --by usage --months 1
  python3 tools/site_costs.py --activate-tags      # activate Site/Client/Project/Component for billing
  python3 tools/site_costs.py --budgets you@example.com --limit 5   # monthly budget + alerts per site

Not attributable to a site: IoT Core messaging and rules (billed per account,
shown here as the account's "AWS IoT" line), and the account's other projects.
Each Cost Explorer request costs USD 0.01; one run makes one per site plus one.
"""
import argparse
import sys
import tomllib
from collections import defaultdict
from datetime import date
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent.parent
STACK_TAG = "aws:cloudformation:stack-name"
USER_TAGS = ["Site", "Client", "Project", "Component"]
GROUP_BY = {
    "service": {"Type": "DIMENSION", "Key": "SERVICE"},
    "usage": {"Type": "DIMENSION", "Key": "USAGE_TYPE"},
    "component": {"Type": "TAG", "Key": "Component"},
}


def sites() -> dict:
    """samconfig env -> stack name, for every site."""
    cfg = tomllib.loads((ROOT / "infra" / "samconfig.toml").read_text())
    return {k: v["deploy"]["parameters"]["stack_name"] for k, v in cfg.items()
            if isinstance(v, dict) and "deploy" in v}


def months_back(n: int) -> tuple[str, str]:
    """[first day n-1 months ago, tomorrow) - Cost Explorer's End is exclusive."""
    today = date.today()
    y, m = today.year, today.month - (n - 1)
    while m < 1:
        y, m = y - 1, m + 12
    end = date.fromordinal(today.toordinal() + 1)
    return date(y, m, 1).isoformat(), end.isoformat()


def costs(ce, start, end, group, flt=None) -> dict:
    """{month: {category: usd}}."""
    args = {"TimePeriod": {"Start": start, "End": end}, "Granularity": "MONTHLY",
            "Metrics": ["UnblendedCost"], "GroupBy": [GROUP_BY[group]]}
    if flt:
        args["Filter"] = flt
    out = defaultdict(dict)
    while True:
        r = ce.get_cost_and_usage(**args)
        for period in r["ResultsByTime"]:
            for g in period["Groups"]:
                key = g["Keys"][0]
                if group == "component":  # "Component$api"; untagged = "Component$"
                    key = key.split("$", 1)[1] or "(no Component tag)"
                out[period["TimePeriod"]["Start"][:7]][key] = float(g["Metrics"]["UnblendedCost"]["Amount"])
        if not r.get("NextPageToken"):
            return out
        args["NextPageToken"] = r["NextPageToken"]


def print_table(title, data):
    print(f"\n== {title}")
    if not data:
        print("   no costs yet")
        return
    for month in sorted(data):
        rows = sorted(((v, k) for k, v in data[month].items() if v >= 0.0005), reverse=True)
        print(f"   {month}   total USD {sum(v for v, _ in rows):.3f}")
        for v, k in rows:
            print(f"     {v:9.3f}  {k}")


def activate_tags(ce):
    try:
        ce.update_cost_allocation_tags_status(
            CostAllocationTagsStatus=[{"TagKey": t, "Status": "Active"} for t in USER_TAGS])
        print(f"activated: {', '.join(USER_TAGS)} (costs grouped by them from now on, after up to 24 h)")
    except ClientError as e:
        # A tag key only becomes known to billing some hours after resources carry it.
        sys.exit(f"error: {e.response['Error']['Message']} - retry in a few hours")


def create_budgets(stacks: dict, email: str, limit: float):
    sts = boto3.client("sts")
    account = sts.get_caller_identity()["Account"]
    bud = boto3.client("budgets", region_name="us-east-1")
    for key, stack in stacks.items():
        name = f"venko-{key}-monthly"
        budget = {"BudgetName": name, "BudgetType": "COST", "TimeUnit": "MONTHLY",
                  "BudgetLimit": {"Amount": f"{limit:.2f}", "Unit": "USD"},
                  "CostFilters": {"TagKeyValue": [f"{STACK_TAG}${stack}"]}}
        alerts = [{"Notification": {"NotificationType": kind, "ComparisonOperator": "GREATER_THAN",
                                    "Threshold": pct, "ThresholdType": "PERCENTAGE"},
                   "Subscribers": [{"SubscriptionType": "EMAIL", "Address": email}]}
                  for kind, pct in (("ACTUAL", 80), ("ACTUAL", 100), ("FORECASTED", 100))]
        try:
            bud.create_budget(AccountId=account, Budget=budget, NotificationsWithSubscribers=alerts)
            print(f"budget {name}: USD {limit:.2f}/month, alerts at 80%/100% actual and 100% forecast -> {email}")
        except bud.exceptions.DuplicateRecordException:
            bud.update_budget(AccountId=account, NewBudget=budget)
            print(f"budget {name}: updated to USD {limit:.2f}/month (alerts unchanged)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("site", nargs="*", help="samconfig env(s); default: all sites")
    ap.add_argument("--by", choices=sorted(GROUP_BY), default="service")
    ap.add_argument("--months", type=int, default=3)
    ap.add_argument("--activate-tags", action="store_true")
    ap.add_argument("--budgets", metavar="EMAIL", help="create/update a monthly budget per site, alerts to EMAIL")
    ap.add_argument("--limit", type=float, default=5.0, help="budget per site in USD (default 5)")
    a = ap.parse_args()

    all_sites = sites()
    unknown = [s for s in a.site if s not in all_sites]
    if unknown:
        sys.exit(f"error: unknown site(s) {', '.join(unknown)} (sites: {', '.join(all_sites)})")
    stacks = {k: all_sites[k] for k in (a.site or all_sites)}
    ce = boto3.client("ce", region_name="us-east-1")

    if a.activate_tags:
        activate_tags(ce)
        return
    if a.budgets:
        create_budgets(stacks, a.budgets, a.limit)
        return

    start, end = months_back(a.months)
    for key, stack in stacks.items():
        flt = {"Tags": {"Key": STACK_TAG, "Values": [stack]}}
        print_table(f"{key} ({stack}) by {a.by}", costs(ce, start, end, a.by, flt))
    account = costs(ce, start, end, "service")
    print_table("whole account by service (IoT Core messaging is only here; includes other projects)", account)


if __name__ == "__main__":
    main()
