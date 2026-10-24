import json
from unittest import mock

from api import data, router
from common import ddb, sysauth


def call(method, path, query=None, body=None, headers=None):
    res = router.handler({"httpMethod": method, "path": path, "queryStringParameters": query, "headers": headers,
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


def test_system_stats_defaults_to_24h_and_parses_numbers():
    row = {"samples": "4320", "cpu_min": "7.1", "cpu_avg": "12.5", "cpu_max": "23.0", "ram_min": "31",
           "ram_avg": "33", "ram_max": "36", "disk_min": None, "disk_avg": None, "disk_max": None}
    stored = sysauth.hash_password("clave-sistema", iterations=1000)
    token, _ = sysauth.make_token(stored)
    with mock.patch.object(data.ddb, "system_password_hash", return_value=stored), \
         mock.patch.object(data.athena, "run", side_effect=[[row], [{"t": "1000", "cpu": "5.5", "ram": "30", "disk": None}]]):
        status, body = call("GET", "/system/stats", headers={"X-System-Token": token})
    assert status == 200 and body["to_ms"] - body["from_ms"] == 24 * 3_600_000
    assert body["samples"] == 4320 and body["cpu"] == {"min": 7.1, "avg": 12.5, "max": 23.0}
    assert body["disk"] == {"min": None, "avg": None, "max": None}
    assert body["series"] == {"cpu": [[1000, 5.5]], "ram": [[1000, 30.0]], "disk": []}


CFG = {"gateway": {"poll_interval": 20, "city": "America/Mexico_City"},
       "sensors": [{"key": "DEV_3.P3", "label": "P3", "unit": "psi", "min": 0, "max": 200}]}


def test_alarms_include_warnings_and_summary():
    rows = [{"k": "DEV_3.P3", "t": "1792800000000", "val": "185", "kind": "WARN_HIGH"}]
    with mock.patch.object(data.storage, "get_config", return_value=CFG), \
         mock.patch.object(data.athena, "run", return_value=rows) as run:
        status, body = call("GET", "/alarms")
    assert status == 200 and "'WARN_HIGH'" in run.call_args[0][0]
    assert body["episodes"][0]["kind"] == "WARN_HIGH" and body["episodes"][0]["label"] == "P3"
    assert body["summary"]["totals"]["warn"] == 1


def test_history_stats_route():
    row = {"k": "DEV_3.P3", "samples": "180", "ok": "178", "min": "120", "avg": "150.5", "max": "186",
           "last": "184", "alarm": "0", "warn": "12", "err": "2"}
    with mock.patch.object(data.storage, "get_config", return_value=CFG), \
         mock.patch.object(data.athena, "run", return_value=[row]):
        status, body = call("GET", "/history/stats", query={"sensors": "DEV_3.P3"})
    assert status == 200 and body["stats"][0]["warn"] == 12 and body["stats"][0]["avg"] == 150.5


# ------------------------------------------------------- Sistema password
STORED = sysauth.hash_password("clave-sistema", iterations=1000)


def test_system_routes_need_a_valid_token():
    with mock.patch.object(data.ddb, "system_password_hash", return_value=STORED), \
         mock.patch.object(data.storage, "get_json", return_value={"hw_serial": "abc"}):
        assert call("GET", "/system")[0] == 401
        assert call("GET", "/system/stats", headers={"x-system-token": "123.bad"})[0] == 401
        token, _ = sysauth.make_token(STORED)
        status, body = call("GET", "/system", headers={"X-System-Token": token})
    assert status == 200 and body["system"] == {"hw_serial": "abc"}


def test_system_login_right_and_wrong_password():
    with mock.patch.object(data.ddb, "system_password_hash", return_value=STORED), \
         mock.patch.object(data.time, "sleep") as slept:
        assert call("POST", "/system/login", body={"password": "otra-cosa"})[0] == 401
        slept.assert_called_once()
        status, body = call("POST", "/system/login", body={"password": "clave-sistema"})
    assert status == 200
    sysauth.check_token(body["token"], STORED)


def test_system_without_password_configured_is_503():
    with mock.patch.object(data.ddb, "system_password_hash", return_value=None):
        status, body = call("POST", "/system/login", body={"password": "x"})
    assert status == 503 and "set_system_password" in body["error"]


def test_live_has_no_system_details():
    cfg = {"gateway": {"poll_interval": 20}, "sensors": []}
    jsons = {"latest/data.json": {"rx_ms": 1, "devices": {}}, "latest/system.json": {"rx_ms": 5, "ip_address": "10.0.0.2"}}
    with mock.patch.object(data.storage, "get_config", return_value=cfg), \
         mock.patch.object(data.storage, "get_json", side_effect=jsons.get):
        body = call("GET", "/live")[1]
    assert "system" not in body and body["system_rx_ms"] == 5
