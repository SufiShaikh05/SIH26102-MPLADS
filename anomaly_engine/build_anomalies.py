"""Build work_anomalies_v1.csv (one row per work) and docs/ANOMALY_ENGINE_V1.md from
work_features_v0.csv. Read-only with respect to every earlier layer."""

from __future__ import annotations

import argparse
import csv
import hashlib
import statistics
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from anomaly_engine import explanations, peer_signals, rules, scoring

DEMO_SIZE = 500
DEMO_SEED = "SIH26102-anomaly-v1"

TEXT_FIELDS = ("work_id", "state", "constituency", "mp_name", "ida", "work_category", "work_status")
NUMERIC_FIELDS = (
    "sanction_amount", "total_disbursed_all_rows", "deduplicated_disbursed_amount",
    "success_utilization_ratio", "deduplicated_utilization_ratio",
    "days_since_sanction", "days_since_last_payment",
    "payment_count_all_rows", "deduplicated_payment_count", "vendor_count",
    "duplicate_record_count", "duplicate_ratio", "duplicate_amount_ratio",
    "in_progress_amount", "in_progress_ratio",
    "payment_span_days", "sanction_to_first_payment_days", "sanction_to_last_payment_days",
)
# Every input column the engine reads. Nothing else in work_features_v0.csv influences a score.
USED_FIELDS = tuple(dict.fromkeys(TEXT_FIELDS + NUMERIC_FIELDS + peer_signals.PEER_FIELDS))
# Retrospective (completion-time) fields and status-derived flags: never read, never scored.
RETROSPECTIVE_FIELDS = ("in_completed", "completion_date", "completed_amount_disbursed", "sanction_to_completion_days")
WORKFLOW_FLAGS_NOT_SCORED = ("is_completed", "is_partially_completed", "is_sanctioned_only")

SEVERITY_COLUMNS = tuple(f"sev_{name}" for name, _ in scoring.SIGNALS)
OUTPUT_COLUMNS = (
    *TEXT_FIELDS, *NUMERIC_FIELDS,
    "peer_level", "peer_group_size", "sanction_to_peer_median", "disbursed_to_peer_median",
    "payment_count_to_peer_median", "payment_peer_p95", "vendor_peer_p95",
    *SEVERITY_COLUMNS,
    "review_priority_score", "review_priority_label", "signal_count",
    "top_signal_1", "top_signal_2", "top_signal_3", "explanation_text",
)


class AnomalyInputError(RuntimeError):
    """work_features_v0.csv does not match the contract this engine expects."""


def _num(text):
    if text is None or text == "":
        return None
    try:
        return float(text)
    except ValueError as error:
        raise AnomalyInputError(f"{text!r} is not a number") from error


def fmt(x) -> str:
    if x is None:
        return ""
    if isinstance(x, float):
        return f"{x:.6f}".rstrip("0").rstrip(".") or "0"
    return str(x)


def load_features(path: Path):
    if not path.is_file():
        raise AnomalyInputError(f"input file not found: {path}")
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        header = list(reader.fieldnames or [])
    missing = [c for c in USED_FIELDS if c not in header]
    if missing:
        raise AnomalyInputError(f"{path}: missing required column(s) {missing}")
    return rows


def parse_work(raw: dict) -> dict:
    w = {f: raw.get(f) or None for f in TEXT_FIELDS}
    for f in NUMERIC_FIELDS:
        w[f] = _num(raw.get(f))
    for key, _ in peer_signals.LEVELS:
        w[f"peer_group_sufficient_by_{key}"] = raw.get(f"peer_group_sufficient_by_{key}") == "1"
        for prefix in ("peer_count", "peer_median_sanction_amount", "peer_median_disbursed_amount", "peer_median_payment_count"):
            w[f"{prefix}_by_{key}"] = _num(raw.get(f"{prefix}_by_{key}"))
    utils = [v for v in (w["success_utilization_ratio"], w["deduplicated_utilization_ratio"]) if v is not None]
    w["eff_util"] = max(utils) if utils else None   # conservative: "low" only if BOTH ratios are low
    w["dedup_count"] = w["deduplicated_payment_count"] if w["deduplicated_payment_count"] is not None else w["payment_count_all_rows"]
    return w


@dataclass
class Result:
    outputs: list[dict[str, str]]
    thresholds: rules.Thresholds
    payment_groups: int
    vendor_groups: int


def evaluate(raw_rows: list[dict]) -> Result:
    ids = [r["work_id"] for r in raw_rows]
    if len(set(ids)) != len(ids):
        raise AnomalyInputError("work_id is not unique in the input; expected exactly one row per work")
    works = [parse_work(r) for r in sorted(raw_rows, key=lambda r: r["work_id"])]
    for w in works:
        peer_signals.attach_peer_measures(w)
    thr = rules.compute_thresholds(works)
    pay_map = peer_signals.peer_count_thresholds(works, "dedup_count")
    ven_map = peer_signals.peer_count_thresholds(works, "vendor_count")

    outputs = []
    by_id = {r["work_id"]: r for r in raw_rows}
    for w in works:
        _, pay_thr = peer_signals.lookup_peer_thresholds(w, pay_map)
        _, ven_thr = peer_signals.lookup_peer_thresholds(w, ven_map)
        sev = {
            "age_utilization": rules.age_utilization_severity(w, thr),
            "duplicate_activity": rules.duplicate_severity(w, thr),
            "payment_count": peer_signals.peer_count_severity(w["dedup_count"], pay_thr),
            "payment_timing": rules.payment_timing_severity(w, thr),
            "vendor_count": peer_signals.peer_count_severity(w["vendor_count"], ven_thr),
            "peer_financial": peer_signals.peer_financial_severity(w, thr),
            "in_progress": rules.in_progress_severity(w, thr),
        }
        score = scoring.review_priority_score(sev)
        tops = scoring.top_signals(sev)
        count = scoring.signal_count(sev)
        raw = by_id[w["work_id"]]
        row = {f: raw.get(f, "") for f in (*TEXT_FIELDS, *NUMERIC_FIELDS)}
        row.update(
            peer_level=w["peer_level"], peer_group_size=fmt(w["peer_group_size"]),
            sanction_to_peer_median=fmt(w["sanction_to_peer_median"]),
            disbursed_to_peer_median=fmt(w["disbursed_to_peer_median"]),
            payment_count_to_peer_median=fmt(w["payment_count_to_peer_median"]),
            payment_peer_p95=fmt(pay_thr[1] if pay_thr else None), vendor_peer_p95=fmt(ven_thr[1] if ven_thr else None),
        )
        for name, _ in scoring.SIGNALS:
            row[f"sev_{name}"] = str(sev[name])
        row.update(
            review_priority_score=f"{score:.2f}", review_priority_label=scoring.review_priority_label(score),
            signal_count=str(count),
            top_signal_1=tops[0] if len(tops) > 0 else "", top_signal_2=tops[1] if len(tops) > 1 else "",
            top_signal_3=tops[2] if len(tops) > 2 else "", explanation_text=explanations.explain(tops, count),
        )
        outputs.append(row)
    return Result(outputs, thr, len(pay_map), len(ven_map))


def write_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(OUTPUT_COLUMNS)
        for row in rows:
            writer.writerow([row.get(c, "") for c in OUTPUT_COLUMNS])


def select_demo_ids(work_ids: list[str], features_demo: Path, size: int, seed: str) -> set[str]:
    """Same demo cohort the feature layer published when available; else a seeded-hash sample."""
    if features_demo.is_file():
        with open(features_demo, newline="", encoding="utf-8") as handle:
            chosen = {r["work_id"] for r in csv.DictReader(handle)} & set(work_ids)
        if chosen:
            return chosen
    ranked = sorted(work_ids, key=lambda w: hashlib.blake2b(f"{seed}|{w}".encode(), digest_size=8).digest())
    return set(ranked[:size])


# ---------------------------------------------------------------------------- documentation
def _tt(t, places=2):
    return "n/a (too few values)" if t is None else " / ".join(f"{v:,.{places}f}" for v in t)


def _table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |" for r in rows]
    return out + [""]


STATIC_SIGNALS = """Every signal returns a severity (0 none, 1 mild, 2 moderate, 3 strong); a signal's points are `max_points x severity / 3`.

| Signal | Max points | Inputs | Rule |
| --- | --- | --- | --- |
| `age_utilization` | 25 | `days_since_sanction`, `success_utilization_ratio`, `deduplicated_utilization_ratio`, `days_since_last_payment` | Work age gates and caps severity. Low utilization (both ratios low) and/or, while not fully utilised, a long gap since the last payment; both together add one level. |
| `duplicate_activity` | 20 | `duplicate_record_count`, `duplicate_ratio`, `duplicate_amount_ratio` | Any exact-duplicate row is mild; elevated versus other works that have duplicates is moderate/strong; a single duplicate row is capped at moderate. |
| `payment_count` | 15 | `deduplicated_payment_count` (falls back to all rows) | Above the peer group's p90/p95/p99, and at least 3 payments. Deduplicated so duplicates are scored once, in `duplicate_activity`. |
| `payment_timing` | 15 | `sanction_to_first_payment_days`, `sanction_to_last_payment_days`, `payment_span_days`, `days_since_last_payment` | Strongest of: long delay to first/last payment, long span (2+ payments), long inactivity while not fully utilised. Correlated timing measures do not stack. |
| `vendor_count` | 10 | `vendor_count` | Above the peer group's p90/p95/p99, and at least 3 vendors. Multiple vendors are not evidence of wrongdoing. |
| `peer_financial` | 10 | `sanction_amount`, `total_disbursed_all_rows` vs peer medians | Ratio to the peer median at least 2x and above the p90/p95/p99 of that ratio across works. |
| `in_progress` | 5 | `in_progress_amount`, `in_progress_ratio`, `days_since_last_payment` | In-progress share above 50% / 75% / 95%, only when the last payment is older than the median. Never independently meaningful. |

Utilization below 1 is never scored on its own: the real median is 1.00, so low utilization only counts together with work age, and staleness only counts while a work is not fully utilised."""


def render_doc(outputs, result: Result, source: str, banner: str | None) -> str:
    t = result.thresholds
    n = len(outputs)
    scores = [float(r["review_priority_score"]) for r in outputs]
    out = ["# Anomaly Engine v1\n"]
    if banner:
        out.append(f"> **{banner}**\n")
    out.append(f"> Generated by `python -m anomaly_engine.build_anomalies` from `{source}` ({n:,} works). Regenerate rather than edit by hand.\n")
    out += ["## 1. Objective\n",
            "An explainable hybrid **review-priority** engine: it surfaces unusual patterns that may warrant verification so a reviewer "
            "can decide where to look first. It does not detect, prove or classify wrongdoing.\n",
            "## 2. Input data\n",
            f"`data/processed/work_features_v0.csv`, one row per work ({n:,} rows in this run); `days_since_*` features use the feature "
            "layer's documented snapshot date (2026-09-25). Input columns read: " + ", ".join(f"`{c}`" for c in USED_FIELDS) + ".\n",
            "## 3. Signals\n", STATIC_SIGNALS + "\n",
            "## 4. Peer-group logic\n",
            "Hierarchy per work: **state + work_category**, then **state**, then **work_category** - the first level whose "
            "`peer_group_sufficient_by_*` flag (peer group of at least 20) is set. For the count signals a group also needs at least "
            f"{rules.MIN_SAMPLE} works with a value, else the next level is tried; if no level qualifies the peer signals are 0 "
            "(nothing is compared against an unreliable peer group). Relative measures - sanction / peer median sanction, disbursed / peer "
            "median disbursed, payment count / peer median payment count - are blank rather than divided by a missing or zero median.\n",
            "## 5. Threshold methodology\n",
            "Thresholds are computed from this run's own input distribution with fixed, documented percentile choices, so they adapt to the "
            "real data. The only non-data-derived constants are the engineering caps and floors listed below with their reason.\n"]
    out += _table(["Threshold", "Values in this run", "Basis"], [
        ["Work age gate (days since sanction) p50 / p75 / p90", _tt(t.age, 0), "Age must exceed the median to count at all; higher percentiles raise the severity cap"],
        ["Low utilization (mild / moderate / strong)", _tt(t.util, 3), f"Lower-tail p{'/p'.join(map(str, rules.UTIL_PERCENTILES))} of max(success, deduplicated) ratio, capped at {rules.UTIL_CEILINGS} so a ratio near 1 is never 'low'"],
        ["Fully-utilised floor", f"{rules.FULL_UTILIZATION_FLOOR}", "Real median utilization is 1.00; the margin absorbs rounding and a small final-payment shortfall"],
        ["Inactivity (days since last payment) p90 / p95 / p99", _tt(t.inactivity, 0), "Among works with payments that are not fully utilised"],
        ["Delay to first payment p90 / p95 / p99 (days)", _tt(t.first_delay, 0), "Works with a non-negative value"],
        ["Delay to last payment p90 / p95 / p99 (days)", _tt(t.last_delay, 0), "Works with a non-negative value"],
        ["Payment span p90 / p95 / p99 (days)", _tt(t.span, 0), "Works with 2+ payments"],
        [f"Duplicate ratio p50 / p75 / p90 ({t.n_with_duplicates:,} works with duplicates)", _tt(t.dup_ratio, 3), f"Among works with duplicates; needs {rules.MIN_SAMPLE}+ such works, else duplicates are mild only"],
        ["Duplicate amount ratio p50 / p75 / p90", _tt(t.dup_amount, 3), "As above"],
        ["Duplicate row count p50 / p75 / p90", _tt(t.dup_count, 1), "As above"],
        ["Median days since last payment (in-progress gate)", "n/a" if t.stale_median is None else f"{t.stale_median:,.0f}", "A recent in-progress payment is ordinary workflow"],
        ["Sanction / peer median p90 / p95 / p99", _tt(t.peer_sanction, 2), f"Also requires at least {rules.MIN_PEER_MULTIPLE}x the peer median"],
        ["Disbursed / peer median p90 / p95 / p99", _tt(t.peer_disbursed, 2), f"Also requires at least {rules.MIN_PEER_MULTIPLE}x the peer median"],
        ["Peer payment-count and vendor-count p90 / p95 / p99", f"{result.payment_groups:,} / {result.vendor_groups:,} peer groups had usable thresholds", f"Per peer group; value must also be at least {rules.MIN_COUNT_FOR_SIGNAL}"],
    ])
    out += ["## 6. Score formula\n",
            "`review_priority_score = min(100, sum(max_points x severity / 3))` over the seven signals (maximum points 25 + 20 + 15 + 15 + 10 + 10 + 5 = 100), "
            "rounded to 2 decimals. No single signal can exceed 25 points, so no single signal can make a work High Review Priority. "
            "`signal_count` is the number of signals with severity of at least 1; `top_signal_1..3` are the largest contributions.\n",
            "## 7. Priority labels\n"]
    out += _table(["Score", "Label"], [["0 - 24.99", "Normal Monitoring"], ["25 - 49.99", "Low Review Priority"],
                                      ["50 - 74.99", "Medium Review Priority"], ["75 - 100", "High Review Priority"]])
    out += ["These labels describe review priority only. They do not mean safe, verified, or anything about wrongdoing; a work with no "
            "triggered signal simply has nothing unusual on the v1 signals.\n",
            "## 8. Explanation generation\n",
            "Each triggered work gets up to three reasons, taken from its three largest signal contributions and rendered from fixed neutral "
            "phrases (see `anomaly_engine/explanations.py`), e.g. *\"Review priority increased because payment count is unusually high relative "
            "to comparable works and duplicate transaction activity may warrant verification.\"* Every explanation ends by stating that it "
            "indicates unusual patterns that may warrant verification and does not establish wrongdoing.\n",
            "## 9. Leakage controls\n",
            "Never read: " + ", ".join(f"`{c}`" for c in RETROSPECTIVE_FIELDS) + " (retrospective, only known after completion), and the "
            "status-derived flags " + ", ".join(f"`{c}`" for c in WORKFLOW_FLAGS_NOT_SCORED) + ". `work_status` is carried as workflow context "
            "only and is not scored; `is_sanctioned_only` is never read as 'no progress'. `recommendation_to_sanction_amount_ratio` (zero variance) "
            "and `average_payment_interval_days` (81.5% missing) are not used.\n",
            "## 10. Real-data score distribution\n"]
    ordered = sorted(scores)
    cuts = statistics.quantiles(ordered, n=100, method="inclusive") if len(ordered) > 1 else [ordered[0]] * 99
    out += _table(["Statistic", "Value"], [["Works scored", f"{n:,}"], ["Minimum", f"{ordered[0]:.2f}"], ["Median", f"{cuts[49]:.2f}"],
                                          ["Mean", f"{statistics.fmean(ordered):.2f}"], ["p90", f"{cuts[89]:.2f}"], ["p95", f"{cuts[94]:.2f}"],
                                          ["p99", f"{cuts[98]:.2f}"], ["Maximum", f"{ordered[-1]:.2f}"]])
    bins = Counter(min(int(s // 10), 9) for s in scores)
    out += _table(["Score band", "Works", "%"], [[f"{b*10}-{(b*10+9.99 if b < 9 else 100):.2f}", f"{bins[b]:,}", f"{100*bins[b]/n:.2f}%"] for b in range(10)])
    out += ["## 11. Label distribution\n"]
    labels = Counter(r["review_priority_label"] for r in outputs)
    out += _table(["Label", "Works", "%"], [[label, f"{labels[label]:,}", f"{100*labels[label]/n:.2f}%"] for _, label in reversed(scoring.LABELS)])
    out += ["## 12. Signal activation counts\n"]
    rows = []
    for name, weight in scoring.SIGNALS:
        c = Counter(r[f"sev_{name}"] for r in outputs)
        active = c["1"] + c["2"] + c["3"]
        rows.append([f"`{name}`", weight, f"{active:,}", f"{100*active/n:.2f}%", f"{c['1']:,}", f"{c['2']:,}", f"{c['3']:,}"])
    out += _table(["Signal", "Max points", "Works active", "% of works", "Mild", "Moderate", "Strong"], rows)
    sc = Counter(r["signal_count"] for r in outputs)
    out += _table(["Signals triggered per work", "Works"], [[k, f"{sc[k]:,}"] for k in sorted(sc, key=int)])
    out += ["## 13. Top 20 review candidates\n",
            "Ranked by review priority score, then signal count, then work_id. These are review candidates, not findings.\n"]
    top = sorted(outputs, key=lambda r: (-float(r["review_priority_score"]), -int(r["signal_count"]), r["work_id"]))[:20]
    out += _table(
        ["work_id", "state", "work_category", "sanction_amount", "utilization", "days_since_sanction", "days_since_last_payment",
         "payment_count", "vendor_count", "duplicate_ratio", "score", "label", "explanation_text"],
        [[r["work_id"], r["state"], r["work_category"], r["sanction_amount"], r["success_utilization_ratio"], r["days_since_sanction"],
          r["days_since_last_payment"], r["payment_count_all_rows"], r["vendor_count"], r["duplicate_ratio"],
          r["review_priority_score"], r["review_priority_label"], r["explanation_text"]] for r in top])
    out += ["## 14. Limitations\n",
            "- Thresholds are percentiles of this dataset, so a work is flagged for being unusual *relative to the others*; if unusual behaviour is common everywhere it will not stand out.",
            "- Works with no recorded expenditure cannot be assessed on payment-based signals and score 0 there; a low score is not reassurance.",
            "- Finished works that legitimately spent less than the sanction (savings) and are old can look like low utilization; `work_status` is not used to discount this.",
            "- Age/utilization and payment timing both use the length of time since the last payment (only while not fully utilised); the two signals overlap by design and are each capped.",
            "- Payment dates earlier than the sanction date (negative delays) are not scored.",
            "- Weights, percentile choices and caps are documented judgement calls, not fitted to labelled outcomes; no labels exist to validate them.",
            "- Duplicate transaction rows are retained upstream because the source has no transaction ID; a duplicate may be a genuine repeat payment.\n",
            "## 15. This is NOT a fraud probability\n",
            "The review priority score is **not** a probability of fraud, **not** a probability of wrongdoing, and **not** a statistical confidence of any kind. "
            "It ranks works by how many independent, explainable patterns look unusual so that a human can decide what to verify. "
            "A high score means *look here first*; a low score does not mean a work is safe or verified.\n"]
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------------------- run / CLI
def run(features_path: Path, out_csv: Path, demo_csv: Path, doc_path: Path, features_demo: Path, banner: str | None = None) -> Result:
    raw_rows = load_features(features_path)
    result = evaluate(raw_rows)
    write_csv(result.outputs, out_csv)
    demo_ids = select_demo_ids([r["work_id"] for r in result.outputs], features_demo, DEMO_SIZE, DEMO_SEED)
    write_csv([r for r in result.outputs if r["work_id"] in demo_ids], demo_csv)
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(render_doc(result.outputs, result, features_path.name, banner), encoding="utf-8", newline="\n")
    return result


def main(argv=None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Build work_anomalies_v1.csv from work_features_v0.csv.")
    parser.add_argument("--features", type=Path, default=root / "data" / "processed" / "work_features_v0.csv")
    parser.add_argument("--out", type=Path, default=root / "data" / "processed" / "work_anomalies_v1.csv")
    parser.add_argument("--demo-out", type=Path, default=root / "data" / "demo" / "work_anomalies_v1_demo.csv")
    parser.add_argument("--features-demo", type=Path, default=root / "data" / "demo" / "work_features_v0_demo.csv")
    parser.add_argument("--doc", type=Path, default=root / "docs" / "ANOMALY_ENGINE_V1.md")
    parser.add_argument("--banner", default=None, help="optional note printed at the top of the generated doc")
    args = parser.parse_args(argv)
    try:
        result = run(args.features, args.out, args.demo_out, args.doc, args.features_demo, args.banner)
    except AnomalyInputError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(f"wrote {len(result.outputs):,} anomaly rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
