"""DynamoDB single table: tests and operator comments.

  pk="TEST"               sk=<test_id>              test record (test_id sorts by start time)
  pk="STATE"              sk="ACTIVE"               pointer to the running test (only one at a time)
  pk="COMMENT#yyyy-mm-dd" sk=<iso ts>#<id>          comment; test_id attr feeds GSI by_test
"""
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from . import settings, timeutil

_table = boto3.resource("dynamodb").Table(settings.TABLE) if settings.TABLE else None


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


def _plain(item: dict | None) -> dict | None:
    """Decimal -> int/float, drop key attrs."""
    if item is None:
        return None
    out = {}
    for k, v in item.items():
        if k in ("pk",):
            continue
        if isinstance(v, Decimal):
            v = int(v) if v == v.to_integral_value() else float(v)
        out[k] = v
    return out


# ------------------------------------------------------------------ tests
def start_test(name: str, operator: str, notes: str = "") -> dict:
    now = timeutil.now_ms()
    test_id = "T" + datetime.fromtimestamp(now / 1000, UTC).strftime("%Y%m%d-%H%M%S")
    try:
        _table.put_item(
            Item={"pk": "STATE", "sk": "ACTIVE", "test_id": test_id},
            ConditionExpression="attribute_not_exists(pk)",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise Conflict("a test is already running; stop it first") from e
        raise
    item = {
        "pk": "TEST", "sk": test_id, "id": test_id, "name": name, "operator": operator,
        "notes": notes, "start_ms": now, "status": "RUNNING",
    }
    _table.put_item(Item=item)
    return _plain(item)


def stop_test(test_id: str, operator: str = "") -> dict:
    try:
        _table.delete_item(
            Key={"pk": "STATE", "sk": "ACTIVE"},
            ConditionExpression="test_id = :t",
            ExpressionAttributeValues={":t": test_id},
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise Conflict(f"{test_id} is not the running test") from e
        raise
    res = _table.update_item(
        Key={"pk": "TEST", "sk": test_id},
        UpdateExpression="SET end_ms = :e, #s = :s, stopped_by = :o",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":e": timeutil.now_ms(), ":s": "REPORT_PENDING", ":o": operator},
        ReturnValues="ALL_NEW",
    )
    return _plain(res["Attributes"])


def set_test_report(test_id: str, status: str, report: dict | None = None, error: str = "") -> None:
    _table.update_item(
        Key={"pk": "TEST", "sk": test_id},
        UpdateExpression="SET #s = :s, report = :r, report_error = :e",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": status, ":r": report or {}, ":e": error},
    )


def get_test(test_id: str) -> dict:
    item = _table.get_item(Key={"pk": "TEST", "sk": test_id}).get("Item")
    if not item:
        raise NotFound(test_id)
    return _plain(item)


def active_test() -> dict | None:
    ptr = _table.get_item(Key={"pk": "STATE", "sk": "ACTIVE"}).get("Item")
    return get_test(ptr["test_id"]) if ptr else None


def list_tests(limit: int = 50) -> list[dict]:
    res = _table.query(KeyConditionExpression=Key("pk").eq("TEST"), ScanIndexForward=False, Limit=limit)
    return [_plain(i) for i in res["Items"]]


# --------------------------------------------------------------- comments
def add_comment(text: str, author: str, sensor: str = "", test_id: str = "", at_ms: int | None = None) -> dict:
    ts = at_ms or timeutil.now_ms()
    day = datetime.fromtimestamp(ts / 1000, UTC).strftime("%Y-%m-%d")
    cid = uuid.uuid4().hex[:8]
    item = {
        "pk": f"COMMENT#{day}", "sk": f"{timeutil.iso_utc(ts)}#{cid}", "id": cid,
        "ts_ms": ts, "text": text, "author": author, "sensor": sensor,
    }
    if test_id:
        item["test_id"] = test_id  # sparse GSI attribute
    _table.put_item(Item=item)
    return _plain(item)


def comments_for_test(test_id: str) -> list[dict]:
    res = _table.query(IndexName="by_test", KeyConditionExpression=Key("test_id").eq(test_id))
    return [_plain(i) for i in res["Items"] if i["pk"].startswith("COMMENT#")]


def comments_in_range(from_ms: int, to_ms: int) -> list[dict]:
    lo, hi = timeutil.iso_utc(from_ms), timeutil.iso_utc(to_ms)
    day = datetime.fromtimestamp(from_ms / 1000, UTC).date()
    last = datetime.fromtimestamp(to_ms / 1000, UTC).date()
    out = []
    while day <= last:
        res = _table.query(
            KeyConditionExpression=Key("pk").eq(f"COMMENT#{day.isoformat()}") & Key("sk").between(lo, hi + "~")
        )
        out += [_plain(i) for i in res["Items"]]
        day += timedelta(days=1)
    return out
