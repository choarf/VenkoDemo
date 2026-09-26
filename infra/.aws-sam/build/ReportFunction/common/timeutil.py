"""Time helpers. All storage/query times are epoch milliseconds (UTC)."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from . import settings


def now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def parse_ms(value) -> int:
    """Accept epoch ms (int/str digits) or ISO-8601 (naive = UTC)."""
    if value is None or value == "":
        raise ValueError("missing time value")
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if s.lstrip("-").isdigit():
        return int(s)
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def iso_utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat().replace("+00:00", "Z")


def local_str(ms: int, tz: str | None = None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    zone = ZoneInfo(tz or settings.REPORT_TZ)
    return datetime.fromtimestamp(ms / 1000, zone).strftime(fmt)


def dt_bounds(from_ms: int, to_ms: int) -> tuple[str, str]:
    """Partition (dt=yyyy-MM-dd, Firehose arrival day in UTC) bounds, padded one
    day each side because Firehose arrival and IoT rx_ms can straddle midnight."""
    lo = datetime.fromtimestamp(from_ms / 1000, UTC) - timedelta(days=1)
    hi = datetime.fromtimestamp(to_ms / 1000, UTC) + timedelta(days=1)
    return lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d")


def validate_range(from_ms: int, to_ms: int) -> None:
    if to_ms <= from_ms:
        raise ValueError("'to' must be after 'from'")
    if to_ms - from_ms > settings.MAX_RANGE_DAYS * 86_400_000:
        raise ValueError(f"range limited to {settings.MAX_RANGE_DAYS} days")


def pick_bucket_ms(from_ms: int, to_ms: int, target_points: int = 400) -> int:
    """Round bucket size to a 'nice' step so charts have ~target_points points."""
    raw = (to_ms - from_ms) / target_points
    for step_s in (20, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400, 21600, 43200, 86400):
        if step_s * 1000 >= raw:
            return step_s * 1000
    return 86_400_000


def previous_iso_week(ref_ms: int, tz: str | None = None) -> tuple[str, int, int]:
    """(label 'YYYY-Www', start_ms, end_ms) for the full week before ref, in local time."""
    zone = ZoneInfo(tz or settings.REPORT_TZ)
    ref = datetime.fromtimestamp(ref_ms / 1000, zone)
    this_monday = (ref - timedelta(days=ref.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start = this_monday - timedelta(days=7)
    return week_bounds(start.strftime("%G-W%V"), tz)


def week_bounds(label: str, tz: str | None = None) -> tuple[str, int, int]:
    zone = ZoneInfo(tz or settings.REPORT_TZ)
    start = datetime.strptime(label + "-1", "%G-W%V-%u").replace(tzinfo=zone)
    end = start + timedelta(days=7)
    return label, int(start.timestamp() * 1000), int(end.timestamp() * 1000)
