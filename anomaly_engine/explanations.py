"""Neutral, machine-traceable explanation text. Never asserts wrongdoing."""

from __future__ import annotations

PHRASES = {
    "age_utilization": "the work is comparatively old with low fund utilization and/or a long gap since its last payment",
    "duplicate_activity": "duplicate transaction activity may warrant verification",
    "payment_count": "payment count is unusually high relative to comparable works",
    "payment_timing": "payment timing is unusual (a long delay, a long payment span, or a long gap since the last payment)",
    "vendor_count": "the number of vendors is unusually high relative to comparable works",
    "peer_financial": "sanctioned or disbursed amounts are unusually large relative to comparable works",
    "in_progress": "a high share of disbursement is still in progress and the last payment is not recent",
}
NO_SIGNAL_TEXT = "No v1 review signals were triggered."
CLOSING = "This indicates unusual patterns that may warrant verification and does not establish wrongdoing."


def explain(top: list[str], total_signals: int) -> str:
    if not top:
        return NO_SIGNAL_TEXT
    phrases = [PHRASES[name] for name in top[:3]]
    if len(phrases) == 1:
        joined = phrases[0]
    elif len(phrases) == 2:
        joined = " and ".join(phrases)
    else:
        joined = ", ".join(phrases[:-1]) + ", and " + phrases[-1]
    text = f"Review priority increased because {joined}."
    extra = total_signals - len(phrases)
    if extra > 0:
        text += f" {extra} further signal{'s' if extra > 1 else ''} also contributed."
    return f"{text} {CLOSING}"
