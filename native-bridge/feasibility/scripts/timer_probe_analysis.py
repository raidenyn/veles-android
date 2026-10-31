"""Pure analysis for the Native Messaging timer probe (stdlib only).

The probe page runs a 1 s setTimeout chain. Chrome's intensive wake-up throttling (hidden > 5 min, chain >= 5)
lets a hidden page's timers run at most once per minute, so a median gap near 60 s means intensive throttling
engaged. This module only classifies samples; it does not decide acceptance.
"""

import statistics

MIN_SAMPLES = 3
THROTTLED_MEDIAN_MS = 30_000  # between the unthrottled 1 000 ms and the throttled ~60 000 ms


def summarize_timers(timers_ms, window_start_ms):
    """Gaps between consecutive timer callbacks whose later sample is at or after ``window_start_ms``."""
    gaps = [round(b - a, 3) for a, b in zip(timers_ms, timers_ms[1:]) if b >= window_start_ms]
    if len(gaps) < MIN_SAMPLES:
        return {"samples": len(gaps), "median_gap_ms": None, "max_gap_ms": None, "verdict": "inconclusive"}
    median = statistics.median(gaps)
    verdict = "intensively_throttled" if median >= THROTTLED_MEDIAN_MS else "not_intensively_throttled"
    return {"samples": len(gaps), "median_gap_ms": median, "max_gap_ms": max(gaps), "verdict": verdict}


def summarize_messages(messages, expected):
    """Delivery of host events: lag = page receive time minus host send time (same machine clock)."""
    lags = [m["recv_epoch_ms"] - m["host_epoch_ms"] for m in messages]
    seen = {m["seq"] for m in messages}
    return {
        "received": len(messages),
        "expected": expected,
        "missing_seqs": [n for n in range(1, expected + 1) if n not in seen],
        "max_lag_ms": max(lags) if lags else None,
        "median_lag_ms": statistics.median(lags) if lags else None,
    }
