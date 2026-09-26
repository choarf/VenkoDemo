"""Test events (Start/Stop HMI) and operator comments."""
import json

import boto3

from common import ddb, settings, timeutil

_lambda = boto3.client("lambda")


def _text(body, key, max_len, required=False):
    v = str(body.get(key) or "").strip()
    if required and not v:
        raise ValueError(f"'{key}' is required")
    if len(v) > max_len:
        raise ValueError(f"'{key}' longer than {max_len} characters")
    return v


def list_tests(req):
    return {"tests": ddb.list_tests(int(req.query.get("limit", 50)))}


def get_active(_req):
    return {"test": ddb.active_test()}


def get_one(_req, test_id):
    return {"test": ddb.get_test(test_id), "comments": ddb.comments_for_test(test_id)}


def start(req):
    b = req.body
    return {"test": ddb.start_test(
        name=_text(b, "name", 120, required=True),
        operator=_text(b, "operator", 80, required=True),
        notes=_text(b, "notes", 2000),
    )}


def stop(req, test_id):
    test = ddb.stop_test(test_id, operator=_text(req.body, "operator", 80))
    # Report generation runs several Athena queries - too slow for the 29 s API
    # limit, so hand it to the report function and let the UI poll the test.
    _lambda.invoke(
        FunctionName=settings.REPORT_FUNCTION, InvocationType="Event",
        Payload=json.dumps({"kind": "test", "test_id": test_id}).encode(),
    )
    return {"test": test}


def list_comments(req):
    if req.query.get("test_id"):
        items = ddb.comments_for_test(req.query["test_id"])
    else:
        to_ms = timeutil.parse_ms(req.query["to"]) if req.query.get("to") else timeutil.now_ms()
        from_ms = timeutil.parse_ms(req.query["from"]) if req.query.get("from") else to_ms - 86_400_000
        timeutil.validate_range(from_ms, to_ms)
        items = ddb.comments_in_range(from_ms, to_ms)
    return {"comments": sorted(items, key=lambda c: c["ts_ms"], reverse=True)}


def add_comment(req):
    b = req.body
    test_id = _text(b, "test_id", 40)
    if not test_id:
        active = ddb.active_test()
        test_id = active["id"] if active else ""
    return {"comment": ddb.add_comment(
        text=_text(b, "text", 2000, required=True),
        author=_text(b, "author", 80, required=True),
        sensor=_text(b, "sensor", 80),
        test_id=test_id,
    )}
