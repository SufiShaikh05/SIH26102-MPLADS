"""Profile the feature matrix and render ``docs/FEATURE_PROFILING_REPORT.md``.

Every number in the report is measured from the ``parsed`` rows ``build_features.py`` just
computed - nothing here re-derives a feature; it only describes what was already built.
Outlier language stays neutral throughout (statistical outlier / unusually high or low /
review candidate) - this module never asserts fraud, and never computes a risk score.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal

from feature_engineering import feature_definitions as fd

# A feature at or above this missing-% is INSUFFICIENT COVERAGE outright.
INSUFFICIENT_MISSING_PCT = 50.0
# A feature at or above this missing-% is called out in the Missingness section even if it
# doesn't cross the INSUFFICIENT line.
NOTABLE_MISSING_PCT = 10.0
# A feature's p99 this many times its median is flagged as heavily skewed (KEEP WITH
# TRANSFORMATION), provided the median itself is meaningfully away from zero.
HEAVY_TAIL_RATIO = 10.0
HEAVY_TAIL_MIN_MEDIAN = 1.0


@dataclass
class NumericStats:
    count: int
    non_null: int
    zero_count: int
    negative_count: int
    minimum: float | None
    maximum: float | None
    median: float | None
    mean: float | None
    stdev: float | None
    p90: float | None
    p95: float | None
    p99: float | None
    iqr: float | None
    lower_fence: float | None
    upper_fence: float | None
    outliers_below: int
    outliers_above: int

    @property
    def missing_pct(self) -> float:
        return 100.0 * (self.count - self.non_null) / self.count if self.count else 0.0


def _to_float(value: object) -> float:
    if isinstance(value, Decimal):
        return float(value)
    return float(value)  # type: ignore[arg-type]


def compute_numeric_stats(values: list[object], *, compute_iqr: bool) -> NumericStats:
    count = len(values)
    present = [_to_float(v) for v in values if v is not None]
    non_null = len(present)
    if non_null == 0:
        return NumericStats(count, 0, 0, 0, None, None, None, None, None, None, None, None, None, None, None, 0, 0)
    zero_count = sum(1 for v in present if v == 0)
    negative_count = sum(1 for v in present if v < 0)
    minimum, maximum = min(present), max(present)
    median = statistics.median(present)
    mean = statistics.fmean(present)
    stdev = statistics.pstdev(present) if non_null > 1 else 0.0
    if non_null >= 2:
        cuts = statistics.quantiles(present, n=100, method="inclusive")
        p90, p95, p99 = cuts[89], cuts[94], cuts[98]
        q1, q3 = cuts[24], cuts[74]
    else:
        p90 = p95 = p99 = q1 = q3 = present[0]
    iqr = lower_fence = upper_fence = None
    outliers_below = outliers_above = 0
    if compute_iqr and non_null >= 4:
        iqr = q3 - q1
        lower_fence = q1 - 1.5 * iqr
        upper_fence = q3 + 1.5 * iqr
        outliers_below = sum(1 for v in present if v < lower_fence)
        outliers_above = sum(1 for v in present if v > upper_fence)
    return NumericStats(
        count, non_null, zero_count, negative_count, minimum, maximum, median, mean, stdev,
        p90, p95, p99, iqr, lower_fence, upper_fence, outliers_below, outliers_above,
    )


def profile_all_numeric_features(rows: list[dict[str, object]]) -> dict[str, NumericStats]:
    numeric_features = [f for f in fd.FEATURES if f.dtype in ("decimal", "int")]
    result = {}
    for feat in numeric_features:
        values = [row.get(feat.name) for row in rows]
        # IQR fences are skipped for peer/context counters that are really just group sizes
        # repeated across many rows - the fences would describe the grouping, not the work.
        compute_iqr = feat.group != "peer" or "median" in feat.name
        result[feat.name] = compute_numeric_stats(values, compute_iqr=compute_iqr)
    return result


# ------------------------------------------------------------------------- peer-group viability
@dataclass
class PeerViability:
    key: str
    columns: tuple[str, ...]
    n_groups: int
    median_group_size: float
    min_group_size: int
    max_group_size: int
    pct_covered: float   # % of GROUPED works sitting in a group >= MIN_PEER_GROUP_SIZE
    ungrouped: int        # works missing one of the grouping values entirely


def compute_peer_viability(rows: list[dict[str, object]]) -> list[PeerViability]:
    out = []
    for key, group_cols in fd.PEER_GROUPINGS:
        groups: dict[tuple, int] = defaultdict(int)
        ungrouped = 0
        for row in rows:
            gk = tuple(row.get(c) for c in group_cols)
            if any(v is None for v in gk):
                ungrouped += 1
                continue
            groups[gk] += 1
        sizes = list(groups.values())
        covered = sum(s for s in sizes if s >= fd.MIN_PEER_GROUP_SIZE)
        total_grouped = sum(sizes)
        out.append(PeerViability(
            key=key, columns=group_cols, n_groups=len(groups),
            median_group_size=statistics.median(sizes) if sizes else 0.0,
            min_group_size=min(sizes) if sizes else 0, max_group_size=max(sizes) if sizes else 0,
            pct_covered=100.0 * covered / total_grouped if total_grouped else 0.0, ungrouped=ungrouped,
        ))
    return out


# ------------------------------------------------------------------------ work_status categories
def status_classification_table(rows: list[dict[str, object]]) -> list[tuple[str, int, str]]:
    """(status text, count, label) - the label is read back from the already-computed
    is_completed/is_partially_completed/is_sanctioned_only columns, never re-derived here,
    so the report can never disagree with the feature matrix it describes."""
    by_status: dict[str, list] = {}
    for row in rows:
        status = row.get("work_status")
        if not status:
            continue
        entry = by_status.setdefault(status, [0, None])
        entry[0] += 1
        if entry[1] is None:
            if row.get("is_partially_completed"):
                entry[1] = "is_partially_completed"
            elif row.get("is_completed"):
                entry[1] = "is_completed"
            else:
                entry[1] = "is_sanctioned_only"
    return sorted(((status, count, label) for status, (count, label) in by_status.items()), key=lambda t: -t[1])


# ------------------------------------------------------------------------------- suitability
def classify_suitability(feat: fd.FeatureSpec, stats: NumericStats | None, peer: PeerViability | None) -> tuple[str, str]:
    """One of KEEP / KEEP WITH TRANSFORMATION / RETROSPECTIVE ONLY / EXCLUDE / INSUFFICIENT
    COVERAGE, with a one-line justification grounded in the measured stats passed in."""
    if feat.name in ("id_mp_code", "id_financial_year"):
        return "EXCLUDE", "Retained as metadata only, per spec, until profiling on real data shows a justified standalone use"
    if feat.timing == "context":
        return "EXCLUDE", "Identifier/label field - not an analytical feature by itself"
    if feat.timing == "retrospective":
        return "RETROSPECTIVE ONLY", "Only known after completion - must not enter a monitoring-time model (see Leakage risks)"
    if stats is None:
        return "KEEP", "Non-numeric monitoring feature (context, completion flag, or cleaned text derivative)"
    if stats.non_null == 0:
        return "EXCLUDE", "No non-null values in this dataset"
    if stats.missing_pct >= INSUFFICIENT_MISSING_PCT:
        return "INSUFFICIENT COVERAGE", f"{stats.missing_pct:.1f}% missing, at or above the {INSUFFICIENT_MISSING_PCT:.0f}% usability threshold"
    if feat.group == "peer" and peer is not None and peer.pct_covered < 50.0:
        return "INSUFFICIENT COVERAGE", (
            f"Only {peer.pct_covered:.1f}% of works with a grouping value fall in a peer group of "
            f">= {fd.MIN_PEER_GROUP_SIZE}; the peer_group_sufficient_by_* flag gates this per row"
        )
    if stats.minimum is not None and stats.minimum == stats.maximum:
        return "EXCLUDE", "Zero variance in this dataset - no discriminative value"
    if (
        stats.median is not None and stats.p99 is not None and stats.median >= HEAVY_TAIL_MIN_MEDIAN
        and stats.p99 > HEAVY_TAIL_RATIO * stats.median
    ):
        return "KEEP WITH TRANSFORMATION", (
            f"p99 ({stats.p99:,.2f}) is more than {HEAVY_TAIL_RATIO:.0f}x the median ({stats.median:,.2f}) - "
            "heavily right-skewed; consider a log-transform or a capped/winsorized variant before use in a "
            "distance- or threshold-based model"
        )
    return "KEEP", "Adequate coverage and a non-degenerate distribution in this dataset"


# --------------------------------------------------------------------------------- rendering
def _fmt(x: float | None, *, places: int = 2) -> str:
    if x is None:
        return "-"
    if abs(x) >= 1 or x == 0:
        return f"{x:,.{places}f}"
    return f"{x:.6f}".rstrip("0").rstrip(".") or "0"


def _table(headers: list[str], rows: list[list[object]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        out.append("| " + " | ".join("" if c is None else str(c).replace("|", "\\|") for c in row) + " |")
    out.append("")
    return out


def _dataset_summary(rows: list[dict[str, object]], master_rows: list[dict], expenditure_rows: list[dict]) -> list[str]:
    out = ["## 1. Dataset summary\n"]
    total = len(rows)
    sanctioned = sum(1 for r in rows if r.get("in_sanctioned"))
    completed = sum(1 for r in rows if r.get("in_completed"))
    with_expenditure = sum(1 for r in rows if r.get("total_disbursed_all_rows") is not None or r.get("payment_count_all_rows") is not None)
    transactions = sum(int(r["payment_count_all_rows"]) for r in rows if r.get("payment_count_all_rows") is not None)
    out += _table(["Metric", "Count"], [
        ["Works (rows in the feature matrix)", f"{total:,}"],
        ["Sanctioned (in_sanctioned = 1)", f"{sanctioned:,}"],
        ["Completed (in_completed = 1)", f"{completed:,}"],
        ["Works with at least one expenditure row", f"{with_expenditure:,}"],
        ["Expenditure transactions attached to a valid Work ID (sum of payment_count_all_rows)", f"{transactions:,}"],
    ])
    out.append(
        "The last row is **not** the row count of `expenditure_transactions.csv` - this module joins only "
        "`works_master.csv` and `expenditure_by_work.csv` (per spec); a transaction whose Work ID was malformed "
        "is absent from `expenditure_by_work.csv` and so is absent from this count too. `works_master.csv` and "
        "`expenditure_by_work.csv` row counts themselves are in section 9 of `DATA_QUALITY_REPORT.md`.\n"
    )
    return out


def _feature_inventory(rows: list[dict[str, object]], stats_by_name: dict[str, NumericStats]) -> list[str]:
    out = ["## 2. Feature inventory\n"]
    total = len(rows)
    table_rows = []
    for feat in fd.FEATURES:
        stats = stats_by_name.get(feat.name)
        if stats is not None:
            missing = f"{stats.missing_pct:.1f}%"
        else:
            non_null = sum(1 for r in rows if r.get(feat.name) is not None)
            missing = f"{100 * (1 - non_null / total):.1f}%" if total else "-"
        table_rows.append([f"`{feat.name}`", feat.source, feat.definition, feat.dtype, feat.timing, missing, feat.future_use])
    out += _table(["Feature", "Source", "Definition", "Type", "Timing", "Missing %", "Intended future use"], table_rows)
    return out


def _distribution_analysis(stats_by_name: dict[str, NumericStats]) -> list[str]:
    out = ["## 3. Distribution analysis\n"]
    for group_name, title in (("financial", "Financial"), ("time", "Time"), ("transaction", "Transaction behavior"), ("completion", "Completion (numeric)")):
        names = [f.name for f in fd.FEATURES if f.group == group_name and f.dtype in ("decimal", "int")]
        if not names:
            continue
        out.append(f"### 3.{['financial','time','transaction','completion'].index(group_name)+1} {title}\n")
        rows = []
        for name in names:
            s = stats_by_name[name]
            rows.append([
                f"`{name}`", f"{s.non_null:,}/{s.count:,}", f"{s.missing_pct:.1f}%", f"{s.zero_count:,}", f"{s.negative_count:,}",
                _fmt(s.minimum), _fmt(s.maximum), _fmt(s.median), _fmt(s.mean), _fmt(s.stdev),
                _fmt(s.p90), _fmt(s.p95), _fmt(s.p99),
            ])
        out += _table(
            ["Feature", "Non-null/Rows", "Missing %", "Zeros", "Negatives", "Min", "Max", "Median", "Mean", "Std dev", "p90", "p95", "p99"],
            rows,
        )
    return out


def _missingness(stats_by_name: dict[str, NumericStats]) -> list[str]:
    out = ["## 4. Missingness\n"]
    notable = sorted(
        ((name, s) for name, s in stats_by_name.items() if s.missing_pct >= NOTABLE_MISSING_PCT),
        key=lambda kv: -kv[1].missing_pct,
    )
    if not notable:
        out.append(f"No numeric feature reaches the {NOTABLE_MISSING_PCT:.0f}% notable-missingness threshold in this dataset.\n")
        return out
    out.append(f"Numeric features at or above {NOTABLE_MISSING_PCT:.0f}% missing (features at or above {INSUFFICIENT_MISSING_PCT:.0f}% are marked INSUFFICIENT COVERAGE in section 7):\n")
    out += _table(["Feature", "Missing %", "Non-null / Rows"], [[f"`{n}`", f"{s.missing_pct:.1f}%", f"{s.non_null:,}/{s.count:,}"] for n, s in notable])
    return out


def _outlier_analysis(stats_by_name: dict[str, NumericStats]) -> list[str]:
    out = ["## 5. Outlier analysis\n"]
    out.append(
        "Tukey fences (`Q1 - 1.5*IQR`, `Q3 + 1.5*IQR`) on features with at least 4 non-null values and a "
        "non-degenerate spread. A value outside its fence is a **statistical outlier** / **review candidate** - "
        "unusually high or unusually low relative to the rest of this dataset. This is not a finding of error, "
        "and it is never a finding of fraud.\n"
    )
    rows = []
    for name, s in stats_by_name.items():
        if s.iqr is None:
            continue
        total_outliers = s.outliers_below + s.outliers_above
        if total_outliers == 0:
            continue
        rows.append([
            f"`{name}`", _fmt(s.lower_fence), _fmt(s.upper_fence),
            f"{s.outliers_below:,} unusually low", f"{s.outliers_above:,} unusually high",
            f"{100*total_outliers/s.non_null:.1f}%" if s.non_null else "-",
        ])
    if not rows:
        out.append("No numeric feature has a value outside its Tukey fences in this dataset.\n")
        return out
    out += _table(["Feature", "Lower fence", "Upper fence", "Below fence", "Above fence", "% of non-null values"], rows)
    return out


def _peer_group_viability(viability: list[PeerViability]) -> list[str]:
    out = ["## 6. Peer-group viability\n"]
    out.append(f"Minimum peer-group size used throughout: **{fd.MIN_PEER_GROUP_SIZE}** (documented in `feature_definitions.MIN_PEER_GROUP_SIZE`).\n")
    out += _table(
        ["Grouping", "Groups", "Median group size", "Min group size", "Max group size", f"% of grouped works in a group >= {fd.MIN_PEER_GROUP_SIZE}", "Works with no grouping value"],
        [[v.key, f"{v.n_groups:,}", f"{v.median_group_size:.1f}", f"{v.min_group_size:,}", f"{v.max_group_size:,}", f"{v.pct_covered:.1f}%", f"{v.ungrouped:,}"] for v in viability],
    )
    return out


def _feature_suitability(rows, stats_by_name: dict[str, NumericStats], viability: list[PeerViability]) -> list[str]:
    out = ["## 7. Feature suitability\n"]
    viability_by_key = {v.key: v for v in viability}
    counts: Counter = Counter()
    table_rows = []
    for feat in fd.FEATURES:
        stats = stats_by_name.get(feat.name)
        peer = None
        if feat.group == "peer":
            for key in viability_by_key:
                if feat.name.endswith(f"_by_{key}"):
                    peer = viability_by_key[key]
                    break
        verdict, reason = classify_suitability(feat, stats, peer)
        counts[verdict] += 1
        table_rows.append([f"`{feat.name}`", verdict, reason])
    out += _table(["Feature", "Verdict", "Justification (from measured stats)"], table_rows)
    out.append("Verdict counts: " + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) + "\n")
    return out


def _leakage_risks(rows) -> list[str]:
    out = ["## 8. Leakage risks\n"]
    retrospective = [f for f in fd.FEATURES if f.timing == "retrospective"]
    out.append(
        "The following features are only knowable **after** a work is completed. None of them may enter a "
        "monitoring-time / early-warning model without an explicit, separately-justified exception:\n"
    )
    out += _table(["Feature", "Why it's retrospective"], [[f"`{f.name}`", f.definition] for f in retrospective])
    out.append(
        "Caveat worth flagging even though `work_status` and its derived `is_completed` / `is_partially_completed` "
        "flags are classified MONITORING here (a status is recorded while the work is still being tracked, per "
        "spec): a status whose text already says something like *completed* offers little for **predicting** a "
        "future completion, since the outcome is already stated. Treat these three flags as monitoring-time "
        "*state* descriptors, not as predictive leading indicators, in any future early-warning use.\n"
    )
    return out


def _recommended_feature_set(rows, stats_by_name: dict[str, NumericStats], viability: list[PeerViability]) -> list[str]:
    out = ["## 9. Recommended feature set for Anomaly Engine v1\n"]
    out.append(
        "Proposed input list only - no anomaly algorithm, risk score, or classifier is implemented here. Every "
        "feature below was classified KEEP or KEEP WITH TRANSFORMATION in section 7; every RETROSPECTIVE ONLY "
        "feature (section 8) is excluded on purpose.\n"
    )
    viability_by_key = {v.key: v for v in viability}
    keep_names = []
    for feat in fd.FEATURES:
        stats = stats_by_name.get(feat.name)
        peer = None
        if feat.group == "peer":
            for key in viability_by_key:
                if feat.name.endswith(f"_by_{key}"):
                    peer = viability_by_key[key]
                    break
        verdict, _ = classify_suitability(feat, stats, peer)
        if verdict in ("KEEP", "KEEP WITH TRANSFORMATION"):
            keep_names.append((feat.name, verdict))
    out += _table(["Feature", "Verdict"], [[f"`{n}`", v] for n, v in keep_names])
    out.append(f"Total proposed inputs: **{len(keep_names)}**\n")
    return out


def render_report(parsed_rows: list[dict[str, object]], master_rows: list[dict], expenditure_rows: list[dict], cfg) -> str:
    stats_by_name = profile_all_numeric_features(parsed_rows)
    viability = compute_peer_viability(parsed_rows)
    status_table = status_classification_table(parsed_rows)

    out: list[str] = ["# Feature Profiling Report - work_features_v0\n"]
    out.append(
        f"> Generated by `feature_engineering/build_features.py` from `works_master.csv` and "
        f"`expenditure_by_work.csv`, left-joined on `work_id`. Snapshot date used for every day-count feature: "
        f"**{cfg.snapshot_date.isoformat()}** (documented default; never the machine clock unless explicitly "
        f"passed with `--snapshot-date`). Regenerate rather than edit by hand.\n"
    )
    out += _dataset_summary(parsed_rows, master_rows, expenditure_rows)
    out += _feature_inventory(parsed_rows, stats_by_name)
    out += _distribution_analysis(stats_by_name)
    out += _missingness(stats_by_name)
    out += _outlier_analysis(stats_by_name)
    out += _peer_group_viability(viability)

    out.append("## Work status categories found in this dataset\n")
    out.append(
        "Classification rule (applied to the exact text found, case-insensitively; nothing beyond this list is "
        "assumed about the real vocabulary): contains `incomplete` -> `is_sanctioned_only`; else contains "
        "`complet` and `partial` -> `is_partially_completed`; else contains `complet` -> `is_completed`; else "
        "-> `is_sanctioned_only`. A work with no `work_status` at all (never sanctioned) gets all three flags = 0.\n"
    )
    if status_table:
        out += _table(["work_status value found", "Count", "Classified as"], [[f"`{s}`", f"{c:,}", label] for s, c, label in status_table])
    else:
        out.append("No sanctioned works (with a work_status value) in this dataset.\n")

    out += _feature_suitability(parsed_rows, stats_by_name, viability)
    out += _leakage_risks(parsed_rows)
    out += _recommended_feature_set(parsed_rows, stats_by_name, viability)

    out.append("## Known limitations\n")
    out += [
        "- `recommendation_to_sanction_amount_ratio` is defined here as `sanction_amount / recommended_amount` "
        "(share of the recommended amount that was sanctioned). The name could plausibly be read the other way "
        "round; this interpretation is stated explicitly so it can be corrected if it does not match intent.",
        "- `id_mp_code` / `id_financial_year` are retained as metadata columns but excluded from the analytical "
        "feature set by default (see section 7) - this has not been evaluated against real data.",
        "- Audit/traceability columns from `works_master.csv` (`conflict_fields`, `*_source_row`, `*_row_count`) "
        "are intentionally not carried into the feature matrix; they remain in `works_master.csv` itself.",
        "- The 'expenditure transactions' count in section 1 is derived from `expenditure_by_work.csv` "
        "(`payment_count_all_rows`, summed), not from reading `expenditure_transactions.csv` directly - see the "
        "note under that table.",
        "- The heavy-tail (KEEP WITH TRANSFORMATION) and INSUFFICIENT COVERAGE thresholds are explicit, documented "
        "constants in this module, not derived from any external standard - they are a reasonable starting point "
        "for review, not a claim of statistical optimality.",
        "",
    ]
    return "\n".join(out).rstrip() + "\n"
