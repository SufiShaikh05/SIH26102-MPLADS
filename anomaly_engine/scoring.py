"""Transparent 0-100 REVIEW PRIORITY score. Not a probability of anything."""

from __future__ import annotations

# (signal, maximum points). Order is also the tie-break order for top signals.
SIGNALS = (
    ("age_utilization", 25),
    ("duplicate_activity", 20),
    ("payment_count", 15),
    ("payment_timing", 15),
    ("vendor_count", 10),
    ("peer_financial", 10),
    ("in_progress", 5),
)
WEIGHTS = dict(SIGNALS)
ORDER = {name: i for i, (name, _) in enumerate(SIGNALS)}
assert sum(WEIGHTS.values()) == 100

LABELS = (
    (75.0, "High Review Priority"),
    (50.0, "Medium Review Priority"),
    (25.0, "Low Review Priority"),
    (0.0, "Normal Monitoring"),
)


def _clamp(severity: int) -> int:
    return max(0, min(3, int(severity)))


def contribution(name: str, severity: int) -> float:
    return WEIGHTS[name] * _clamp(severity) / 3


def review_priority_score(severities: dict[str, int]) -> float:
    total = sum(contribution(name, severities.get(name, 0)) for name, _ in SIGNALS)
    return round(max(0.0, min(100.0, total)), 2)


def review_priority_label(score: float) -> str:
    for floor, label in LABELS:
        if score >= floor:
            return label
    return LABELS[-1][1]


def signal_count(severities: dict[str, int]) -> int:
    return sum(1 for name, _ in SIGNALS if _clamp(severities.get(name, 0)) > 0)


def top_signals(severities: dict[str, int], n: int = 3) -> list[str]:
    active = [(name, contribution(name, severities.get(name, 0))) for name, _ in SIGNALS if _clamp(severities.get(name, 0)) > 0]
    active.sort(key=lambda item: (-item[1], ORDER[item[0]]))
    return [name for name, _ in active[:n]]
