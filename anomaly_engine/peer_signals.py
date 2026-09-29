"""Peer-relative measures and signals. Peer hierarchy: state + work_category, then state, then
work_category - a level is used only if the feature layer's peer_group_sufficient_by_* flag allows it."""

from __future__ import annotations

from collections import defaultdict

from anomaly_engine import rules

LEVELS = (
    ("state_work_category", ("state", "work_category")),
    ("state", ("state",)),
    ("work_category", ("work_category",)),
)
PEER_FIELDS = tuple(
    f"{prefix}_by_{key}" for key, _ in LEVELS
    for prefix in ("peer_group_sufficient", "peer_count", "peer_median_sanction_amount",
                   "peer_median_disbursed_amount", "peer_median_payment_count")
)


def safe_div(numerator, denominator):
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _group(w, cols):
    g = tuple(w[c] for c in cols)
    return g if all(v is not None for v in g) else None


def attach_peer_measures(w) -> None:
    """Adds peer_level, peer_group_size and the three work/peer-median ratios (None, never an
    invalid division, when a median is missing or zero)."""
    w["peer_level"], w["peer_group_size"] = "none", None
    w["sanction_to_peer_median"] = w["disbursed_to_peer_median"] = w["payment_count_to_peer_median"] = None
    for key, _ in LEVELS:
        if w[f"peer_group_sufficient_by_{key}"]:
            w["peer_level"], w["peer_group_size"] = key, w[f"peer_count_by_{key}"]
            w["sanction_to_peer_median"] = safe_div(w["sanction_amount"], w[f"peer_median_sanction_amount_by_{key}"])
            w["disbursed_to_peer_median"] = safe_div(w["total_disbursed_all_rows"], w[f"peer_median_disbursed_amount_by_{key}"])
            w["payment_count_to_peer_median"] = safe_div(w["payment_count_all_rows"], w[f"peer_median_payment_count_by_{key}"])
            return


def peer_count_thresholds(works, metric: str):
    """{(level, group): (p90, p95, p99)} from the works in each peer group that have a value;
    a group needs >= MIN_SAMPLE valued works, else that level is skipped for this metric."""
    out = {}
    for key, cols in LEVELS:
        groups = defaultdict(list)
        for w in works:
            g = _group(w, cols)
            if g is not None and w[f"peer_group_sufficient_by_{key}"] and w[metric] is not None:
                groups[g].append(w[metric])
        for g, values in groups.items():
            thr = rules.percentiles(values, rules.UPPER_TAIL)
            if thr is not None:
                out[(key, g)] = thr
    return out


def lookup_peer_thresholds(w, threshold_map):
    for key, cols in LEVELS:
        if w[f"peer_group_sufficient_by_{key}"]:
            g = _group(w, cols)
            thr = threshold_map.get((key, g)) if g is not None else None
            if thr is not None:
                return key, thr
    return "none", None


def peer_count_severity(value, thresholds) -> int:
    if value is None or thresholds is None or value < rules.MIN_COUNT_FOR_SIGNAL:
        return 0
    return rules.upper_level(value, thresholds)


def peer_financial_severity(w, t: rules.Thresholds) -> int:
    levels = [0]
    for key, thr in (("sanction_to_peer_median", t.peer_sanction), ("disbursed_to_peer_median", t.peer_disbursed)):
        v = w[key]
        if v is not None and thr is not None and v >= rules.MIN_PEER_MULTIPLE:
            levels.append(rules.upper_level(v, thr))
    return max(levels)
