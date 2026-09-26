"""API Gateway (REST, proxy integration) entry point: routing, JSON, CORS, errors."""
import json
import logging
import re

from common import athena, ddb, settings

from . import data, reports, testruns

log = logging.getLogger()
log.setLevel(logging.INFO)

# (method, path regex, handler(req, **path_params))
ROUTES = [
    ("GET", r"/config", data.get_config),
    ("GET", r"/live", data.get_live),
    ("GET", r"/history", data.get_history),
    ("GET", r"/alarms", data.get_alarms),
    ("GET", r"/export", data.get_export),
    ("GET", r"/tests", testruns.list_tests),
    ("GET", r"/tests/active", testruns.get_active),
    ("POST", r"/tests/start", testruns.start),
    ("GET", r"/tests/(?P<test_id>[A-Za-z0-9\-]+)", testruns.get_one),
    ("POST", r"/tests/(?P<test_id>[A-Za-z0-9\-]+)/stop", testruns.stop),
    ("POST", r"/tests/(?P<test_id>[A-Za-z0-9\-]+)/report", reports.regenerate_test),
    ("GET", r"/comments", testruns.list_comments),
    ("POST", r"/comments", testruns.add_comment),
    ("GET", r"/reports", reports.list_reports),
    ("GET", r"/reports/url", reports.report_url),
    ("POST", r"/reports/weekly", reports.request_weekly),
]
_COMPILED = [(m, re.compile(f"^{p}$"), h) for m, p, h in ROUTES]


class Request:
    def __init__(self, event):
        self.method = event.get("httpMethod", "GET")
        self.path = event.get("path") or "/"
        self.query = event.get("queryStringParameters") or {}
        raw = event.get("body") or ""
        try:
            self.body = json.loads(raw) if raw else {}
        except json.JSONDecodeError as e:
            raise ValueError("body must be JSON") from e


def _resp(status: int, payload) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": settings.ALLOWED_ORIGIN,
            "Access-Control-Allow-Headers": "Content-Type,X-Api-Key",
            "Cache-Control": "no-store",
        },
        "body": json.dumps(payload, default=str),
    }


def handler(event, _context):
    if event.get("httpMethod") == "OPTIONS":
        return _resp(204, {})
    try:
        req = Request(event)
        for method, rx, fn in _COMPILED:
            m = rx.match(req.path)
            if m and method == req.method:
                return _resp(200, fn(req, **m.groupdict()))
        return _resp(404, {"error": f"no route {req.method} {req.path}"})
    except (ValueError, KeyError) as e:
        return _resp(400, {"error": str(e)})
    except ddb.NotFound as e:
        return _resp(404, {"error": f"not found: {e}"})
    except ddb.Conflict as e:
        return _resp(409, {"error": str(e)})
    except athena.QueryError as e:
        log.warning("athena: %s", e)
        return _resp(502, {"error": f"query failed: {e}"})
    except LookupError as e:
        return _resp(503, {"error": str(e)})
    except Exception:
        log.exception("unhandled")
        return _resp(500, {"error": "internal error"})
