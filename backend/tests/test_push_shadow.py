"""tools/push_shadow.py: desired state built from a gateway config.json.

Imports the gateway's own shadow_sync.py from ../Akvo_Green, so these tests
also check that the hash the tool sends is the one the gateway computes."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "push_shadow.py"
SITE_CONFIG = ROOT.parent / "Akvo_Green" / "sites" / "akvo01002" / "config_data" / "config.json"

if not (ROOT.parent / "Akvo_Green" / "gateway" / "shadow_sync.py").is_file():
    pytest.skip("needs the sibling Akvo_Green repo", allow_module_level=True)

spec = importlib.util.spec_from_file_location("push_shadow", TOOL)
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)
import shadow_sync  # noqa: E402 - on sys.path once push_shadow is loaded


def site_config():
    return json.loads(SITE_CONFIG.read_text())


def test_desired_leaves_out_aws_and_serial_settings():
    desired = ps.build_desired(site_config(), "abc1234")
    assert "aws" not in desired
    assert set(desired.get("modbus", {})) <= {"simulate"}
    assert desired["meta"]["git_rev"] == "abc1234"


def test_desired_hash_is_what_the_gateway_computes_after_merging():
    current = site_config()
    current["gateway"]["poll_interval"] += 1        # the Pi runs something older
    desired = ps.build_desired(site_config(), "abc1234")
    merged = shadow_sync.merge_desired(current, desired)
    assert shadow_sync.config_hash(merged) == desired["config_hash"]


def test_desired_fits_the_shadow_limit():
    desired = ps.build_desired(site_config(), "abc1234")
    assert len(json.dumps({"desired": desired}).encode()) < ps.SHADOW_LIMIT


def test_removed_keys_are_sent_as_null():
    old = {"gateway": {"a": 1, "b": 2}, "wifi": {"networks": ["x"]}, "devices": [1, 2]}
    new = {"gateway": {"a": 1}, "devices": [1]}
    assert ps.nulls_for_removed(old, new) == {"gateway": {"a": 1, "b": None}, "devices": [1], "wifi": None}


def test_status_line():
    d = {"config_hash": "h1"}
    assert ps.status_line({}) == "no desired config in the shadow yet"
    assert ps.status_line({"desired": d, "reported": {"config_hash": "h1"}}) == "applied"
    assert ps.status_line({"desired": d, "reported": {"config_hash": "h0"}}).startswith("PENDING")
    assert "REFUSED" in ps.status_line({"desired": d, "reported": {"config_hash": "h0", "error": "bad"}})
