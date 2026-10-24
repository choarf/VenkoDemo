"""Process indicators computed from sensor values (config.json "derived", built by tools/sync_config.py).

Only fixed operations exist - nothing from the config is evaluated as code:
  ratio    a / b * scale        (recovery = permeate / feed * 100)
  diff     a - b                (filter or membrane pressure drop)
  balance  a - sum(minus)       (feed - permeate - reject, ~0 when the meters agree)
"""

OPS = ("ratio", "diff", "balance")


def value(d: dict, vals: dict) -> float | None:
    """d: one config "derived" entry; vals: sensor key -> number (None when missing)."""
    a = vals.get(d["a"])
    if a is None:
        return None
    if d["op"] == "ratio":
        b = vals.get(d["b"])
        return None if not b else a / b * d.get("scale", 1)
    if d["op"] == "diff":
        b = vals.get(d["b"])
        return None if b is None else a - b
    minus = [vals.get(k) for k in d["minus"]]
    return None if any(m is None for m in minus) else a - sum(minus)


def state(d: dict, v: float | None) -> str:
    """'ok' | 'warn' | 'alarm' | 'none', with the same warning band as the sensors."""
    if v is None:
        return "none"
    lo, hi = d["min"], d["max"]
    if v < lo or v > hi:
        return "alarm"
    m = (hi - lo) * d.get("warn_margin", 0.1)
    return "warn" if v < lo + m or v > hi - m else "ok"
