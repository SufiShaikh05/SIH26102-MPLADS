"""Signal severity rules and the data-derived thresholds they use.

Severity scale for every signal: 0 none, 1 mild, 2 moderate, 3 strong. Thresholds are computed
from the input's own distribution at run time (so they adapt to the real data) using the fixed
percentile choices below; every constant that is NOT data-derived is named and justified here.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

# --- fixed, documented choices (not data-derived) -----------------------------------------
FULL_UTILIZATION_FLOOR = 0.95   # ratio >= this counts as "fully utilised": absorbs rounding noise and a
                                # small final-payment shortfall; the real median is 1.00, so a ratio near 1 is normal
UTIL_PERCENTILES = (20, 10, 5)  # lower-tail percentiles for mild / moderate / strong "low utilization"
UTIL_CEILINGS = (0.90, 0.60, 0.30)  # ...each capped so a ratio near 1 is never "low" even if a percentile lands there
AGE_PERCENTILES = (50, 75, 90)  # "sufficiently old": older than the median / p75 / p90 sanction age. Age is a gate
                                # that caps severity, not an anomaly in itself
UPPER_TAIL = (90, 95, 99)       # upper-tail percentiles for mild / moderate / strong on count, timing and peer-ratio signals
DUPLICATE_PERCENTILES = (50, 75, 90)  # tiers among works that HAVE duplicates (most have one duplicate row)
MIN_SAMPLE = 20                 # a percentile is only trusted from at least this many values (same as the peer-group minimum)
MIN_COUNT_FOR_SIGNAL = 3        # 1-2 payments/vendors is ordinary whatever a peer percentile says (real medians are 1)
IN_PROGRESS_SHARES = (0.50, 0.75, 0.95)  # majority / most / nearly all of disbursement still in progress
MIN_PEER_MULTIPLE = 2.0         # a work must be at least 2x its peer median before a peer-ratio percentile is applied


def percentiles(values, pcts, min_n: int = MIN_SAMPLE):
    if len(values) < max(min_n, 2):
        return None
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    return tuple(cuts[p - 1] for p in pcts)


def upper_level(value, thresholds) -> int:
    """Number of thresholds strictly exceeded (0..len)."""
    if value is None or thresholds is None:
        return 0
    return sum(1 for t in thresholds if value > t)


def lower_level(value, thresholds) -> int:
    """Number of thresholds the value is strictly below (thresholds descending)."""
    if value is None or thresholds is None:
        return 0
    return sum(1 for t in thresholds if value < t)


def has_payments(w) -> bool:
    return (w["payment_count_all_rows"] or 0) > 0


def not_fully_utilised(w) -> bool:
    return w["eff_util"] is not None and w["eff_util"] < FULL_UTILIZATION_FLOOR


@dataclass
class Thresholds:
    age: tuple | None = None
    util: tuple | None = None
    inactivity: tuple | None = None
    first_delay: tuple | None = None
    last_delay: tuple | None = None
    span: tuple | None = None
    dup_ratio: tuple | None = None
    dup_amount: tuple | None = None
    dup_count: tuple | None = None
    stale_median: float | None = None
    peer_sanction: tuple | None = None
    peer_disbursed: tuple | None = None
    n_with_duplicates: int = 0


def compute_thresholds(works) -> Thresholds:
    t = Thresholds()
    t.age = percentiles([w["days_since_sanction"] for w in works if w["days_since_sanction"] is not None], AGE_PERCENTILES)
    utils = [w["eff_util"] for w in works if w["eff_util"] is not None]
    p = percentiles(utils, UTIL_PERCENTILES)
    t.util = tuple(min(v, cap) for v, cap in zip(p, UTIL_CEILINGS)) if p else None
    t.inactivity = percentiles(
        [w["days_since_last_payment"] for w in works
         if has_payments(w) and not_fully_utilised(w) and w["days_since_last_payment"] is not None], UPPER_TAIL)
    t.first_delay = percentiles([w["sanction_to_first_payment_days"] for w in works
                                 if w["sanction_to_first_payment_days"] is not None and w["sanction_to_first_payment_days"] >= 0], UPPER_TAIL)
    t.last_delay = percentiles([w["sanction_to_last_payment_days"] for w in works
                                if w["sanction_to_last_payment_days"] is not None and w["sanction_to_last_payment_days"] >= 0], UPPER_TAIL)
    t.span = percentiles([w["payment_span_days"] for w in works
                          if (w["payment_count_all_rows"] or 0) >= 2 and w["payment_span_days"] is not None and w["payment_span_days"] >= 0], UPPER_TAIL)
    dups = [w for w in works if (w["duplicate_record_count"] or 0) >= 1]
    t.n_with_duplicates = len(dups)
    t.dup_ratio = percentiles([w["duplicate_ratio"] for w in dups if w["duplicate_ratio"] is not None], DUPLICATE_PERCENTILES)
    t.dup_amount = percentiles([w["duplicate_amount_ratio"] for w in dups if w["duplicate_amount_ratio"] is not None], DUPLICATE_PERCENTILES)
    t.dup_count = percentiles([w["duplicate_record_count"] for w in dups], DUPLICATE_PERCENTILES)
    stale = [w["days_since_last_payment"] for w in works if has_payments(w) and w["days_since_last_payment"] is not None]
    t.stale_median = statistics.median(stale) if stale else None
    t.peer_sanction = percentiles([w["sanction_to_peer_median"] for w in works if w["sanction_to_peer_median"] is not None], UPPER_TAIL)
    t.peer_disbursed = percentiles([w["disbursed_to_peer_median"] for w in works if w["disbursed_to_peer_median"] is not None], UPPER_TAIL)
    return t


# ------------------------------------------------------------------------------ severities
def age_utilization_severity(w, t: Thresholds) -> int:
    """Old work + low utilization and/or (while not fully utilised) a long gap since the last payment.
    Work age gates and caps the severity: a young work can never score here."""
    age_level = upper_level(w["days_since_sanction"], t.age)
    if age_level == 0:
        return 0
    u = lower_level(w["eff_util"], t.util)
    s = upper_level(w["days_since_last_payment"], t.inactivity) if has_payments(w) and not_fully_utilised(w) else 0
    if u == 0 and s == 0:
        return 0
    return min(3, max(u, s) + (1 if u and s else 0), age_level)


def payment_timing_severity(w, t: Thresholds) -> int:
    """Long delay to first/last payment, long payment span, or (while not fully utilised) long
    inactivity. Correlated timing measures do not stack: the strongest one sets the severity."""
    levels = [upper_level(w["sanction_to_first_payment_days"], t.first_delay) if (w["sanction_to_first_payment_days"] or 0) >= 0 else 0,
              upper_level(w["sanction_to_last_payment_days"], t.last_delay) if (w["sanction_to_last_payment_days"] or 0) >= 0 else 0]
    if (w["payment_count_all_rows"] or 0) >= 2:
        levels.append(upper_level(w["payment_span_days"], t.span))
    if has_payments(w) and not_fully_utilised(w):
        levels.append(upper_level(w["days_since_last_payment"], t.inactivity))
    return max(levels)


def duplicate_severity(w, t: Thresholds) -> int:
    """Any exact-duplicate row is at least mild; elevated relative to other works that have
    duplicates is moderate/strong. A single duplicate row is capped at moderate."""
    count = w["duplicate_record_count"] or 0
    if count < 1:
        return 0
    sev = max(1, upper_level(w["duplicate_ratio"], t.dup_ratio), upper_level(w["duplicate_amount_ratio"], t.dup_amount),
              upper_level(count, t.dup_count))
    return min(sev, 2) if count == 1 else sev


def in_progress_severity(w, t: Thresholds) -> int:
    """High in-progress share, only when the last payment is older than the median (a recent
    in-progress payment is ordinary workflow)."""
    amount, ratio, since = w["in_progress_amount"], w["in_progress_ratio"], w["days_since_last_payment"]
    if not amount or amount <= 0 or ratio is None or since is None or t.stale_median is None or since <= t.stale_median:
        return 0
    return sum(1 for share in IN_PROGRESS_SHARES if ratio > share)
