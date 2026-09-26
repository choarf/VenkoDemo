import json
from unittest import mock

from api import router
from common import ddb


def call(method, path, query=None, body=None):
    res = router.handler({"httpMethod": method, "path": path, "queryStringParameters": query,
                          "body": json.dumps(body) if body is not None else None}, None)
    return res["statusCode"], json.loads(res["body"])


def test_unknown_route_404():
    assert call("GET", "/nope")[0] == 404


def test_active_route_not_captured_by_test_id():
    with mock.patch.object(ddb, "active_test", return_value=None) as m:
        assert call("GET", "/tests/active") == (200, {"test": None})
        m.assert_called_once()


def test_start_requires_name_and_operator():
    status, body = call("POST", "/tests/start", body={"name": "x"})
    assert status == 400 and "operator" in body["error"]


def test_conflict_maps_to_409():
    with mock.patch.object(ddb, "start_test", side_effect=ddb.Conflict("running")):
        assert call("POST", "/tests/start", body={"name": "x", "operator": "y"})[0] == 409


def test_bad_sensor_key_400():
    assert call("GET", "/history", query={"sensors": "a;drop"})[0] == 400


def test_report_url_rejects_other_prefixes():
    assert call("GET", "/reports/url", query={"key": "config/config.json"})[0] == 400
