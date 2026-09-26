"""SQL builders for the Athena tables defined in infra/template.yaml.

readings_raw rows are one gateway message each:
  devices map<device, map<sensor, struct<val, status, alarm, err>>>
and are flattened with UNNEST. Every value interpolated here is either an int
or a sensor key validated by parse_sensor_keys, so the strings are injection-safe.
"""
import re

from . import settings, timeutil

_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+$")

_FLAT = (
    "FROM {table} "
    "CROSS JOIN UNNEST(devices) AS d(device, sensors) "
    "CROSS JOIN UNNEST(d.sensors) AS s(sensor, r) "
)


def parse_sensor_keys(raw: str | None) -> list[str]:
    """'DEV_5.PhSensor,DEV_1.Pt100Sensor' -> validated list (empty = all)."""
    if not raw:
        return []
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    bad = [k for k in keys if not _KEY_RE.match(k)]
    if bad:
        raise ValueError(f"invalid sensor key(s): {', '.join(bad)}")
    return keys


def _where(from_ms: int, to_ms: int, keys: list[str] | None = None) -> str:
    lo, hi = timeutil.dt_bounds(from_ms, to_ms)
    w = f"WHERE dt BETWEEN '{lo}' AND '{hi}' AND rx_ms >= {int(from_ms)} AND rx_ms < {int(to_ms)}"
    if keys:
        quoted = ", ".join(f"'{k}'" for k in keys)
        w += f" AND concat(d.device, '.', s.sensor) IN ({quoted})"
    return w


def history(from_ms: int, to_ms: int, keys: list[str], bucket_ms: int) -> str:
    b = int(bucket_ms)
    return (
        f"SELECT concat(d.device, '.', s.sensor) AS k, (rx_ms / {b}) * {b} AS t, "
        "avg(r.val) AS avg, min(r.val) AS min, max(r.val) AS max, count(*) AS n "
        + _FLAT.format(table=settings.DATA_TABLE)
        + _where(from_ms, to_ms, keys)
        + " AND r.status = 'OK' GROUP BY 1, 2 ORDER BY 1, 2"
    )


def abnormal_rows(from_ms: int, to_ms: int, keys: list[str] | None = None) -> str:
    """Every sample that is in alarm or failed to read; collapsed into episodes in Python."""
    return (
        "SELECT concat(d.device, '.', s.sensor) AS k, rx_ms AS t, r.val AS val, "
        "CASE WHEN r.status = 'OK' THEN r.alarm ELSE r.status END AS kind "
        + _FLAT.format(table=settings.DATA_TABLE)
        + _where(from_ms, to_ms, keys)
        + " AND (r.status <> 'OK' OR r.alarm IN ('LOW', 'HIGH')) ORDER BY 1, 2"
    )


def sensor_summary(from_ms: int, to_ms: int) -> str:
    return (
        "SELECT concat(d.device, '.', s.sensor) AS k, count(*) AS samples, "
        "count_if(r.status = 'OK') AS ok, "
        "min(r.val) FILTER (WHERE r.status = 'OK') AS min, "
        "avg(r.val) FILTER (WHERE r.status = 'OK') AS avg, "
        "max(r.val) FILTER (WHERE r.status = 'OK') AS max, "
        "stddev_samp(r.val) FILTER (WHERE r.status = 'OK') AS std, "
        "min_by(r.val, rx_ms) FILTER (WHERE r.status = 'OK') AS first, "
        "max_by(r.val, rx_ms) FILTER (WHERE r.status = 'OK') AS last, "
        "count_if(r.alarm = 'LOW') AS low, count_if(r.alarm = 'HIGH') AS high "
        + _FLAT.format(table=settings.DATA_TABLE)
        + _where(from_ms, to_ms)
        + " GROUP BY 1 ORDER BY 1"
    )


def data_gaps(from_ms: int, to_ms: int, max_gap_ms: int) -> str:
    """Intervals with no gateway message longer than max_gap_ms (gateway/network down)."""
    lo, hi = timeutil.dt_bounds(from_ms, to_ms)
    return (
        "SELECT prev AS start, t AS stop FROM ("
        "SELECT rx_ms AS t, lag(rx_ms) OVER (ORDER BY rx_ms) AS prev "
        f"FROM {settings.DATA_TABLE} WHERE dt BETWEEN '{lo}' AND '{hi}' "
        f"AND rx_ms >= {int(from_ms)} AND rx_ms < {int(to_ms)}) "
        f"WHERE t - prev > {int(max_gap_ms)} ORDER BY 1"
    )


def system_summary(from_ms: int, to_ms: int) -> str:
    lo, hi = timeutil.dt_bounds(from_ms, to_ms)
    return (
        "SELECT count(*) AS samples, "
        "avg(cpu_load_percent) AS cpu_avg, max(cpu_load_percent) AS cpu_max, "
        "avg(ram_usage_percent) AS ram_avg, max(ram_usage_percent) AS ram_max, "
        "max(disk_usage_percent) AS disk_max, "
        "max_by(ip_address, rx_ms) AS ip, max_by(os, rx_ms) AS os, "
        "max_by(platform_type, rx_ms) AS platform "
        f"FROM {settings.SYSTEM_TABLE} WHERE dt BETWEEN '{lo}' AND '{hi}' "
        f"AND rx_ms >= {int(from_ms)} AND rx_ms < {int(to_ms)}"
    )
