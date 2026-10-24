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


def warn_bands(sensors: list[dict]) -> list[tuple[str, float, float]]:
    """(key, warn below, warn above) per sensor: the warn_margin share of its range inside each limit.

    Sensors without numeric limits or with a key that fails the key check are skipped, so
    what reaches the SQL is only validated keys and plain floats."""
    bands = []
    for s in sensors:
        lo, hi, key = s.get("min"), s.get("max"), s.get("key", "")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lo, hi)) or hi <= lo:
            continue
        if not _KEY_RE.match(key):
            continue
        m = (hi - lo) * float(s.get("warn_margin", 0.1))
        bands.append((key, float(lo + m), float(hi - m)))
    return bands


def _kind(bands: list[tuple[str, float, float]] | None) -> str:
    """Per-sample kind: read error status, LOW/HIGH alarm, WARN_LOW/WARN_HIGH near a limit, else NULL."""
    k = "concat(d.device, '.', s.sensor)"
    expr = "CASE WHEN r.status <> 'OK' THEN r.status WHEN r.alarm IN ('LOW', 'HIGH') THEN r.alarm"
    if bands:
        high = " OR ".join(f"({k} = '{key}' AND r.val > {wh!r})" for key, _, wh in bands)
        low = " OR ".join(f"({k} = '{key}' AND r.val < {wl!r})" for key, wl, _ in bands)
        expr += f" WHEN {high} THEN 'WARN_HIGH' WHEN {low} THEN 'WARN_LOW'"
    return expr + " END"


def abnormal_rows(from_ms: int, to_ms: int, keys: list[str] | None = None,
                  bands: list[tuple[str, float, float]] | None = None) -> str:
    """Every sample that failed to read, is in alarm or (with bands) near a limit;
    collapsed into episodes in Python."""
    return (
        "SELECT k, t, val, kind FROM ("
        f"SELECT concat(d.device, '.', s.sensor) AS k, rx_ms AS t, r.val AS val, {_kind(bands)} AS kind "
        + _FLAT.format(table=settings.DATA_TABLE)
        + _where(from_ms, to_ms, keys)
        + ") WHERE kind IS NOT NULL ORDER BY 1, 2"
    )


def range_stats(from_ms: int, to_ms: int, keys: list[str] | None = None,
                bands: list[tuple[str, float, float]] | None = None) -> str:
    """Per sensor over a range: min/avg/max/last of good readings and how many were
    in alarm, near a limit (warning) or failed to read (Históricos summary)."""
    return (
        "SELECT k, count(*) AS samples, count_if(status = 'OK') AS ok, "
        "min(val) FILTER (WHERE status = 'OK') AS min, avg(val) FILTER (WHERE status = 'OK') AS avg, "
        "max(val) FILTER (WHERE status = 'OK') AS max, max_by(val, t) FILTER (WHERE status = 'OK') AS last, "
        "count_if(kind IN ('LOW', 'HIGH')) AS alarm, count_if(kind IN ('WARN_LOW', 'WARN_HIGH')) AS warn, "
        "count_if(status <> 'OK') AS err FROM ("
        f"SELECT concat(d.device, '.', s.sensor) AS k, rx_ms AS t, r.val AS val, r.status AS status, {_kind(bands)} AS kind "
        + _FLAT.format(table=settings.DATA_TABLE)
        + _where(from_ms, to_ms, keys)
        + ") GROUP BY 1 ORDER BY 1"
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


def system_stats(from_ms: int, to_ms: int) -> str:
    """Min / average / max of the gateway's CPU, RAM and disk use over a range (Sistema view)."""
    lo, hi = timeutil.dt_bounds(from_ms, to_ms)
    cols = ", ".join(
        f"min({c}) AS {n}_min, avg({c}) AS {n}_avg, max({c}) AS {n}_max"
        for n, c in (("cpu", "cpu_load_percent"), ("ram", "ram_usage_percent"), ("disk", "disk_usage_percent"))
    )
    return (
        f"SELECT count(*) AS samples, {cols} "
        f"FROM {settings.SYSTEM_TABLE} WHERE dt BETWEEN '{lo}' AND '{hi}' "
        f"AND rx_ms >= {int(from_ms)} AND rx_ms < {int(to_ms)}"
    )


def system_history(from_ms: int, to_ms: int, bucket_ms: int) -> str:
    """CPU / RAM / disk averages per time bucket (Sistema view charts)."""
    lo, hi = timeutil.dt_bounds(from_ms, to_ms)
    b = int(bucket_ms)
    return (
        f"SELECT (rx_ms / {b}) * {b} AS t, avg(cpu_load_percent) AS cpu, avg(ram_usage_percent) AS ram, "
        "avg(disk_usage_percent) AS disk "
        f"FROM {settings.SYSTEM_TABLE} WHERE dt BETWEEN '{lo}' AND '{hi}' "
        f"AND rx_ms >= {int(from_ms)} AND rx_ms < {int(to_ms)} GROUP BY 1 ORDER BY 1"
    )


def system_network(from_ms: int, to_ms: int, bucket_ms: int) -> str:
    """Download / upload bytes per time bucket from the gateway's cumulative counters
    (net_bytes_recv/sent, all interfaces but loopback): the difference between consecutive
    reports, skipping negative ones (counters restart with the Pi)."""
    lo, hi = timeutil.dt_bounds(from_ms, to_ms)
    b = int(bucket_ms)
    return (
        f"SELECT (t / {b}) * {b} AS t, sum(dt) AS dt, sum(down) AS down, sum(up) AS up FROM ("
        "SELECT rx_ms AS t, rx_ms - lag(rx_ms) OVER (ORDER BY rx_ms) AS dt, "
        "net_bytes_recv - lag(net_bytes_recv) OVER (ORDER BY rx_ms) AS down, "
        "net_bytes_sent - lag(net_bytes_sent) OVER (ORDER BY rx_ms) AS up "
        f"FROM {settings.SYSTEM_TABLE} WHERE dt BETWEEN '{lo}' AND '{hi}' "
        f"AND rx_ms >= {int(from_ms)} AND rx_ms < {int(to_ms)}"
        ") WHERE dt > 0 AND down >= 0 AND up >= 0 GROUP BY 1 ORDER BY 1"
    )
