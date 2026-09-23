"""Render docs/DATA_QUALITY_REPORT.md and docs/DATA_DICTIONARY.md.

Every number comes from the statistics gathered while processing the real
workbooks in *this* run.  Nothing is hard-coded except column descriptions and
explanatory text.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

from data_pipeline.merge import CHECK_INFO, CONTAINMENT, EXTRA_ISSUE, TAG_LABELS
from data_pipeline.parsing import MPLADS_START, fold_person_name, fold_text
from data_pipeline.schemas import WORK_SOURCES
from data_pipeline.stats import PipelineStats
from data_pipeline.work_id import ERROR_CODES

SOURCE_URL = "https://mplads.mospi.gov.in/digigov/dashboard.html"
LOW_CARDINALITY = 40
EXCEL_MAX_ROWS = 1_048_576


# ------------------------------------------------------------------------------ helpers
def fmt(n: int) -> str:
    return f"{n:,}"


def pct(n: int, d: int, digits: int = 2) -> str:
    return "-" if not d else f"{100 * n / d:.{digits}f}%"


def esc(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def trunc(value: object, n: int = 70) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= n else text[: n - 3] + "..."


def money(value: Decimal | None) -> str:
    return "-" if value is None else f"{value:,.2f}"


def table(headers: list[str], rows: list[list[object]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(esc(cell) for cell in row) + " |" for row in rows]
    out.append("")
    return out


SHORT_TITLES = {
    "recommended": "Recommended",
    "sanctioned": "Sanctioned",
    "completed": "Completed",
    "expenditure": "Expenditure",
    "allocation": "Allocated Limit",
    "calamity": "Calamity Consent",
}


def _title(key: str) -> str:
    """Short label used in tables (full workbook names appear in section 3)."""
    return SHORT_TITLES[key]


def variant_clusters(values: Counter, limit: int = 8) -> tuple[int, list[tuple[str, list[tuple[str, int]]]]]:
    """Groups of raw values that differ only by case / spacing / punctuation."""
    groups: dict[str, list[tuple[str, int]]] = {}
    for value, count in values.items():
        groups.setdefault(fold_text(value), []).append((value, count))
    clusters = [(key, sorted(items, key=lambda t: -t[1])) for key, items in groups.items() if len(items) > 1]
    clusters.sort(key=lambda kv: -sum(count for _, count in kv[1]))
    return len(clusters), clusters[:limit]


def _sum_over(stats: PipelineStats, attr: str, kinds: tuple[str, ...] | None = None) -> dict[str, int]:
    totals = {}
    for key, profile in stats.profiles.items():
        totals[key] = sum(
            getattr(c, attr) for c in profile.columns.values() if kinds is None or c.kind in kinds
        )
    return totals


# ------------------------------------------------------------------------ quality report
def headline_findings(stats: PipelineStats) -> list[str]:
    lines: list[str] = []
    for key in (*WORK_SOURCES, "expenditure"):
        p = stats.profiles[key]
        if p.id.malformed:
            lines.append(
                f"**Malformed Work IDs** - {fmt(p.id.malformed)} of {fmt(p.id.total)} rows in {_title(key)} "
                f"({pct(p.id.malformed, p.id.total)}); logged in `malformed_ids.csv` and excluded from keyed outputs."
            )
    d = stats.duplicates
    for key in WORK_SOURCES:
        if d.extra_rows[key]:
            lines.append(
                f"**Repeated Work IDs** - {_title(key)} has {fmt(d.extra_rows[key])} extra row(s) for "
                f"{fmt(d.ids_with_duplicates[key])} ID(s): {fmt(d.identical[key])} identical, "
                f"{fmt(d.conflicting[key])} conflicting (first row kept, all repeats logged)."
            )
    for key, p in stats.profiles.items():
        if p.exact_duplicate_rows:
            lines.append(f"**Exact duplicate rows** - {_title(key)}: {fmt(p.exact_duplicate_rows)} (Sr. No. ignored).")
    c = stats.conflicts
    if c.works_with_conflicts:
        lines.append(
            f"**Cross-source conflicts** - {fmt(c.works_with_conflicts)} work(s) have at least one field "
            f"where Recommended/Sanctioned/Completed disagree (see `work_conflicts.csv`)."
        )
    j = stats.join
    for a, b, issue, write in CONTAINMENT:
        n = j.issue_counts[issue]
        if write and n:
            lines.append(
                f"**Unmatched IDs** - {fmt(n)} of {fmt(j.pair_total[(a, b)])} {TAG_LABELS[a]} ID(s) "
                f"({pct(n, j.pair_total[(a, b)])}) are not found in {TAG_LABELS[b]} (`{issue}`)."
            )
    if j.issue_counts[EXTRA_ISSUE]:
        lines.append(
            f"**Unmatched IDs** - {fmt(j.issue_counts[EXTRA_ISSUE])} Recommended work(s) carry a Sanction Date "
            "but the ID is not in Sanctioned."
        )
    for name, (group, description) in CHECK_INFO.items():
        hits, evaluated = stats.checks.hits[name], stats.checks.evaluated[name]
        if group != "Informational" and hits:
            lines.append(
                f"**{group}** - {fmt(hits)} of {fmt(evaluated)} evaluable work(s): {description}. "
                "Candidates for review, not proof of an error."
            )
    for key, p in stats.profiles.items():
        if p.layout and p.layout.other_sheets:
            lines.append(
                f"**Unread sheets** - {_title(key)}: the workbook has more sheet(s) "
                f"{', '.join(p.layout.other_sheets)}; only `{p.layout.sheet_name}` was read, so data may be missing."
            )
        if p.layout and p.layout.header_row + p.rows_read >= EXCEL_MAX_ROWS:
            lines.append(
                f"**Possible truncation** - {_title(key)} reached Excel's {fmt(EXCEL_MAX_ROWS)}-row sheet limit; "
                "the export may have been cut off."
            )
    for key, p in stats.profiles.items():
        if p.layout and p.layout.date1904:
            lines.append(
                f"**1904 date system** - {_title(key)}: the workbook uses Excel's 1904 date system, so any numeric "
                "(serial-number) dates would be read 4 years off; text dates such as 08-Jul-2024 are unaffected."
            )
    invalid = _sum_over(stats, "invalid")
    for key, n in invalid.items():
        if n:
            lines.append(f"**Invalid values** - {_title(key)}: {fmt(n)} unparseable date/amount cell(s).")
    negatives = _sum_over(stats, "negatives")
    for key, n in negatives.items():
        if n:
            lines.append(f"**Negative amounts** - {_title(key)}: {fmt(n)} value(s).")
    for key, p in stats.profiles.items():
        odd = sum(col.too_early + col.too_late for col in p.columns.values())
        if odd:
            lines.append(
                f"**Out-of-range dates** - {_title(key)}: {fmt(odd)} value(s) before {MPLADS_START.isoformat()} "
                f"or after the as-of date {stats.run.as_of}."
            )
        encoding = sum(col.suspect_encoding for col in p.columns.values())
        if encoding:
            lines.append(f"**Text encoding** - {_title(key)}: {fmt(encoding)} value(s) look like mojibake / contain U+FFFD.")
    return lines


def _section_run(stats: PipelineStats, out: list[str]) -> None:
    r = stats.run
    out.append("## 1. Run metadata\n")
    rows = [
        ["Generated (UTC)", r.finished_utc],
        ["Elapsed", f"{r.elapsed_seconds:,.0f} s"],
        ["As-of date for future-date checks", r.as_of],
        ["Earliest plausible date", f"{MPLADS_START.isoformat()} (MPLADS launch; assumption)"],
        ["Python", r.python],
        ["XLSX reader", r.xlsx_reader],
        ["Platform", r.platform],
        ["Raw workbooks unchanged (SHA-256 before == after)", {True: "yes", False: "NO - investigate", None: "not checked"}[r.raw_unchanged]],
        ["Row limit (`--limit-rows`)", r.limit_rows if r.limit_rows else "none (full run)"],
    ]
    out += table(["Item", "Value"], rows)
    peaks = [[stage, "n/a" if mib is None else f"{mib:,.0f} MiB"] for stage, mib in r.peak_memory_mib.items()]
    if peaks:
        out.append("Peak process memory (working set) after each stage:\n")
        out += table(["Stage", "Peak"], peaks)


def _section_headlines(stats: PipelineStats, out: list[str]) -> None:
    out.append("## 2. Headline findings\n")
    findings = headline_findings(stats)
    if not findings:
        out.append("No issues were detected by the checks in this report.\n")
    out += [f"- {line}" for line in findings]
    out.append("")


def _section_sources(stats: PipelineStats, out: list[str]) -> None:
    out.append("## 3. Source workbooks\n")
    rows = []
    for key, p in stats.profiles.items():
        layout, fp = p.layout, p.fingerprint
        rows.append([
            p.spec.file_name,
            f"{fp.get('size_bytes', 0) / 2**20:,.2f}",
            str(fp.get("sha256", ""))[:16],
            layout.sheet_name if layout else "-",
            layout.header_row if layout else "-",
            fmt(p.data_rows),
            fmt(p.blank_rows),
            f"{len(layout.positions)} mapped / {len(layout.unmapped_headers)} unmapped" if layout else "-",
        ])
    out += table(["Workbook", "MiB", "SHA-256 (first 16)", "Sheet", "Header row", "Data rows", "Blank rows", "Columns"], rows)
    for key, p in stats.profiles.items():
        if p.layout and (p.layout.unmapped_headers or p.layout.duplicate_headers or p.layout.other_sheets):
            out.append(
                f"- {_title(key)}: unmapped headers {p.layout.unmapped_headers or '-'}, "
                f"duplicate headers {p.layout.duplicate_headers or '-'}, "
                f"other sheets not read {p.layout.other_sheets or '-'}"
            )
    out.append("")


def _section_work_ids(stats: PipelineStats, out: list[str]) -> None:
    out.append("## 4. Work ID extraction and validation\n")
    out.append(
        "Canonical form: `PREFIX/MPCODE/YYYY-YYYY/SERIAL` (upper-case, spaces removed). The ID is taken from the "
        "start of the `Work` cell (Recommended, Sanctioned, Completed) or from the `Work ID` column (Expenditure). "
        "Sr. No. is never used. Malformed values are logged to `data/processed/malformed_ids.csv`.\n"
    )
    keys = (*WORK_SOURCES, "expenditure")
    rows = []
    for key in keys:
        p = stats.profiles[key]
        rows.append([
            _title(key), fmt(p.id.total), fmt(p.id.valid), fmt(p.id.malformed), pct(p.id.valid, p.id.total),
            fmt(p.id.with_warnings), fmt(p.distinct_ids if key != "expenditure" else stats.join.unique["E"]),
            fmt(len(p.id.mp_codes)),
        ])
    out += table(["Source", "Rows", "Valid IDs", "Malformed", "Valid %", "Valid but normalised", "Unique valid IDs", "Distinct MP codes"], rows)

    reasons = [r for r in ERROR_CODES if any(stats.profiles[k].id.reasons[r] for k in keys)]
    if reasons:
        out.append("Malformed reasons:\n")
        out += table(["Reason", *[_title(k) for k in keys]], [[r, *[fmt(stats.profiles[k].id.reasons[r]) for k in keys]] for r in reasons])
    warning_names = sorted({w for k in keys for w in stats.profiles[k].id.warnings})
    if warning_names:
        out.append("Accepted-but-normalised quirks (ID kept, form corrected):\n")
        out += table(["Quirk", *[_title(k) for k in keys]], [[w, *[fmt(stats.profiles[k].id.warnings[w]) for k in keys]] for w in warning_names])
    prefixes: Counter = Counter()
    years: Counter = Counter()
    for k in keys:
        prefixes.update(stats.profiles[k].id.prefixes)
        years.update(stats.profiles[k].id.financial_years)
    out.append(f"ID prefixes seen (all sources): {', '.join(f'`{p}` ({fmt(n)})' for p, n in prefixes.most_common(10)) or '-'}\n")
    out.append(f"Financial-year segments seen (all sources): {', '.join(f'`{y}` ({fmt(n)})' for y, n in sorted(years.items())[:15]) or '-'}\n")
    examples = [(k, *e) for k in keys for e in stats.profiles[k].id.examples[:6]]
    if examples:
        out.append("First malformed examples (full list in `malformed_ids.csv`):\n")
        out += table(["Source", "Row", "Raw value", "Reason", "Detail"], [[k, r, trunc(raw, 80), reason, trunc(detail, 60)] for k, r, raw, reason, detail in examples[:20]])


def _section_duplicates(stats: PipelineStats, out: list[str]) -> None:
    out.append("## 5. Duplicates\n")
    d = stats.duplicates
    out.append("### 5.1 Repeated Work IDs inside one source\n")
    out.append("Policy: the first row (lowest source row) is kept; every repeat is written to `work_id_duplicates.csv` as `identical` or `conflicting`.\n")
    rows = []
    for key in WORK_SOURCES:
        p = stats.profiles[key]
        rows.append([_title(key), fmt(p.id.valid), fmt(p.distinct_ids), fmt(d.ids_with_duplicates[key]),
                     fmt(d.extra_rows[key]), fmt(d.identical[key]), fmt(d.conflicting[key])])
    out += table(["Source", "Rows with valid ID", "Unique IDs", "IDs repeated", "Extra rows", "Identical repeats", "Conflicting repeats"], rows)
    for key in WORK_SOURCES:
        if d.samples[key]:
            out.append(f"Conflicting repeats in {_title(key)} (first {len(d.samples[key])}):\n")
            out += table(["Work ID", "Repeat row", "Kept row", "Differing fields"], [[s["work_id"], s["source_row"], s["kept_source_row"], s["differing_fields"]] for s in d.samples[key]])
    out.append("### 5.2 Exact duplicate rows (Sr. No. ignored)\n")
    rows = [[_title(k), fmt(p.data_rows), fmt(p.exact_duplicate_rows), pct(p.exact_duplicate_rows, p.data_rows),
             ", ".join(map(str, p.duplicate_examples[:5])) or "-"] for k, p in stats.profiles.items()]
    out += table(["Source", "Data rows", "Repeated rows", "%", "Example source rows"], rows)
    e = stats.expenditure
    out.append("### 5.3 Expenditure: payments per Work ID\n")
    out.append("Several payments per work are expected; the table shows how many. `duplicate_record = 1` in `expenditure_transactions.csv` marks a payment identical to an earlier row.\n")
    out += table(["Metric", "Value"], [
        ["Works with at least one payment", fmt(e.works)],
        ["Payment rows attached to a valid Work ID", fmt(e.payments)],
        ["Most payments for one work", f"{fmt(e.max_payments)} ({e.max_payments_work})"],
        *[[f"Works with {b} payment(s)", fmt(e.histogram[b])] for b in e.BUCKETS],
    ])


def _section_join(stats: PipelineStats, out: list[str]) -> None:
    j = stats.join
    out.append("## 6. Join analysis (Work ID)\n")
    out.append("### 6.1 Unique IDs per source\n")
    rows = []
    for tag, key in (("R", "recommended"), ("S", "sanctioned"), ("C", "completed"), ("E", "expenditure")):
        p = stats.profiles[key]
        rows.append([TAG_LABELS[tag], fmt(p.id.valid), fmt(j.unique[tag]), fmt(p.id.malformed)])
    rows.append(["Union of all four sources", "", fmt(j.union_ids), ""])
    rows.append([TAG_LABELS["M"] + " = master rows", "", fmt(j.master_rows), ""])
    out += table(["Source", "Rows with valid ID", "Unique valid IDs", "Malformed IDs"], rows)
    out.append("### 6.2 Which sources each ID appears in\n")
    label = lambda pattern: " + ".join(t for t, present in zip("RSCE", pattern) if present) or "(none)"  # noqa: E731
    out += table(["Present in", "Work IDs", "% of union"], [[label(p), fmt(n), pct(n, j.union_ids)] for p, n in sorted(j.patterns.items(), key=lambda kv: -kv[1])])
    out.append("R = Recommended, S = Sanctioned, C = Completed, E = Expenditure.\n")
    out.append("### 6.3 Match rates\n")
    out.append(
        "Rows marked *check* test a relationship the recommend -> sanction -> complete -> pay flow implies; unmatched IDs "
        "are written to `join_unmatched_ids.csv` (may also reflect snapshot timing or export filters). Rows marked *coverage* "
        "only describe how far works progressed and are not written to the file.\n"
    )
    rows = []
    for a, b, issue, write in CONTAINMENT:
        total, matched = j.pair_total[(a, b)], j.pair_match[(a, b)]
        rows.append([f"{TAG_LABELS[a]} -> {TAG_LABELS[b].split(' (')[0]}", "check" if write else "coverage",
                     fmt(total), fmt(matched), pct(matched, total), fmt(total - matched),
                     ", ".join(j.issue_samples[issue][:3]) or "-"])
    out += table(["IDs in ... found in ...", "Type", "IDs", "Found", "Match rate", "Unmatched", "Sample unmatched IDs"], rows)
    out.append(f"Recommended works with a Sanction Date but no Sanctioned record: **{fmt(j.issue_counts[EXTRA_ISSUE])}**\n")


def _section_conflicts(stats: PipelineStats, out: list[str]) -> None:
    c = stats.conflicts
    out.append("## 7. Conflicting values between sources\n")
    out.append(
        "Where Recommended, Sanctioned and Completed hold different non-empty values for the same Work ID, the preferred value "
        "is used in `works_master.csv` (descriptive fields: Sanctioned > Recommended > Completed; `sanction_date`: Sanctioned first; "
        "`recommended_date`: Recommended first) and the disagreement is written to `work_conflicts.csv`. Nothing is overwritten silently.\n"
    )
    fields = sorted(set(c.by_field) | set(c.format_only))
    if not fields:
        out.append("No conflicting or format-only differences found.\n")
        return
    out.append(f"Works with at least one true conflict: **{fmt(c.works_with_conflicts)}**\n")
    out += table(["Field", "True conflicts", "Format-only differences (case / spacing / punctuation)"], [[f, fmt(c.by_field[f]), fmt(c.format_only[f])] for f in fields])
    for field in sorted(c.samples):
        out.append(f"`{field}` - first examples:\n")
        out += table(["Work ID", "Chosen", "Recommended", "Sanctioned", "Completed"], [
            [s["work_id"], f"{trunc(s['chosen_value'], 40)} ({s['chosen_source']})", trunc(s["recommended_value"], 40),
             trunc(s["sanctioned_value"], 40), trunc(s["completed_value"], 40)] for s in c.samples[field]])


def _column_tables(stats: PipelineStats, out: list[str]) -> None:
    out.append("## 8. Column-level quality\n")
    for key, p in stats.profiles.items():
        out.append(f"### {_title(key)} (`{p.spec.file_name}`)\n")
        cols = list(p.columns.values())
        rows = [[c.name, c.kind, fmt(c.total), fmt(c.empty), fmt(c.placeholder), pct(c.missing, c.total), fmt(c.invalid),
                 fmt(c.cleaned), fmt(c.suspect_encoding)] for c in cols]
        out += table(["Column", "Kind", "Rows", "Empty", "N/A-like", "Missing %", "Invalid", "Whitespace-normalised", "Suspect encoding"], rows)
        amounts = [c for c in cols if c.kind == "amount"]
        if amounts:
            out += table(["Amount column", "Valid", "Negative", "Zero", "Min", "Max", "Sum (rupees, as read)", "Parse notes"], [
                [c.name, fmt(c.n_amounts), fmt(c.negatives), fmt(c.zeros), money(c.min_amount), money(c.max_amount), money(c.amount_sum),
                 ", ".join(f"{k}={fmt(v)}" for k, v in c.notes.items() if not k.startswith("invalid")) or "-"] for c in amounts])
        dates = [c for c in cols if c.kind == "date"]
        if dates:
            out += table(["Date column", "Valid", "Earliest", "Latest", f"Before {MPLADS_START}", f"After {stats.run.as_of}", "Non-standard text format", "From Excel serial number"], [
                [c.name, fmt(c.n_dates), c.min_date or "-", c.max_date or "-", fmt(c.too_early), fmt(c.too_late), fmt(c.notes["alt_format"]), fmt(c.notes["excel_serial"])] for c in dates])
        for c in cols:
            for kind, items in c.examples.items():
                out.append(f"- `{c.name}` {kind.replace('_', ' ')} examples (source row: value): " + "; ".join(f"{r}: `{trunc(v, 40)}`" for r, v in items[:5]))
        out.append("")


def _categorical(stats: PipelineStats, out: list[str]) -> None:
    out.append("## 9. Categorical consistency\n")
    out.append("Values are grouped after folding case, spacing and punctuation; a group with more than one spelling is an inconsistency candidate. Different *spellings* of the same name (typos) are not detected.\n")
    summary = []
    details: list[str] = []
    for key, p in stats.profiles.items():
        for c in p.columns.values():
            if c.values is None:
                continue
            n_clusters, clusters = variant_clusters(c.values)
            summary.append([_title(key), c.name, fmt(len(c.values)), "yes (cap reached)" if c.untracked else "no", fmt(n_clusters)])
            if len(c.values) <= LOW_CARDINALITY and c.values:
                details.append(f"- **{_title(key)} / {c.name}**: " + ", ".join(f"`{trunc(v, 40)}` ({fmt(n)})" for v, n in c.values.most_common()))
            for folded, items in clusters[:5]:
                details.append(f"- variants in {_title(key)} / {c.name}: " + " | ".join(f"`{trunc(v, 45)}` ({fmt(n)})" for v, n in items))
    out += table(["Source", "Column", "Distinct values", "Truncated", "Inconsistent groups"], summary)
    out += details
    out.append("")
    state_sets = {}
    for key in (*WORK_SOURCES, "expenditure", "allocation"):
        col = stats.profiles[key].columns.get("state")
        if col and col.values:
            state_sets[key] = {fold_text(v): v for v in col.values}
    if state_sets:
        everything = set().union(*state_sets.values())
        partial = sorted(k for k in everything if sum(k in s for s in state_sets.values()) < len(state_sets))
        out.append("### State names not present in every source\n")
        if partial:
            out += table(["State (folded)", *[_title(k) for k in state_sets]], [[k, *["yes" if k in s else "-" for s in state_sets.values()]] for k in partial[:30]])
        else:
            out.append("All sources use the same set of state names.\n")


def _section_checks(stats: PipelineStats, out: list[str]) -> None:
    out.append("## 10. Date / amount relationships (merged works)\n")
    out.append("A hit means the relationship looks impossible or unusual in the data as exported. These are review candidates - they do not establish an error and say nothing about intent.\n")
    rows = []
    for name, (group, description) in CHECK_INFO.items():
        evaluated, hits = stats.checks.evaluated[name], stats.checks.hits[name]
        rows.append([group, name, description, fmt(evaluated), fmt(hits), pct(hits, evaluated), ", ".join(stats.checks.samples[name][:3]) or "-"])
    out += table(["Group", "Check", "Rule", "Evaluated", "Hits", "% of evaluated", "Sample Work IDs"], rows)
    x = stats.diagnostics.status_xtab
    if x:
        out.append("### Work Status vs. downstream records (diagnostic - status meanings are not assumed)\n")
        out += table(["Work Status (Sanctioned)", "Works", "Also in Completed", "Also has expenditure"],
                     [[s, fmt(v[0]), fmt(v[1]), fmt(v[2])] for s, v in sorted(x.items(), key=lambda kv: -kv[1][0])])
    dg = stats.diagnostics
    multi_names = {c: n for c, n in dg.names_by_code.items() if len(n) > 1}
    multi_codes = {n: c for n, c in dg.codes_by_name.items() if len(c) > 1}
    out.append("### MP code inside the Work ID vs. MP name (diagnostic)\n")
    out += table(["Metric", "Value"], [
        ["Distinct MP codes in master", fmt(len(dg.names_by_code))],
        ["Codes that map to more than one MP name (after folding honorifics)", fmt(len(multi_names))],
        ["MP names that use more than one code", fmt(len(multi_codes))],
    ])
    for code, names in list(multi_names.items())[:5]:
        out.append(f"- code `{code}` -> {', '.join(sorted(names))}")
    for name, codes in list(multi_codes.items())[:5]:
        out.append(f"- name `{name}` -> {', '.join(sorted(codes))}")
    out.append("")


def _section_expenditure(stats: PipelineStats, out: list[str]) -> None:
    p = stats.profiles["expenditure"]
    amounts = p.extra.get("payment_status_amounts", {})
    out.append("## 11. Expenditure by payment status\n")
    out.append("`total_disbursed` in `expenditure_by_work.csv` adds up **all** statuses. Whether every status counts as money actually disbursed is not stated in the workbook - decide before using it in a detector.\n")
    out += table(["Payment Status", "Payments", "Sum of Fund Disbursed Amount"], [[s, fmt(n), money(total)] for s, (n, total) in sorted(amounts.items(), key=lambda kv: -kv[1][0])])


def _section_reference(stats: PipelineStats, out: list[str]) -> None:
    out.append("## 12. Reference workbooks and MP-name linkage\n")
    alloc, cal = stats.profiles["allocation"], stats.profiles["calamity"]
    out += table(["Workbook", "Rows", "Repeated rows", "Amount sum (rupees)"], [
        [_title("allocation"), fmt(alloc.data_rows), fmt(alloc.exact_duplicate_rows), money(alloc.columns["allocated_amount"].amount_sum)],
        [_title("calamity"), fmt(cal.data_rows), fmt(cal.exact_duplicate_rows), money(cal.columns["consent_amount"].amount_sum)],
    ])

    def names(*keys: str) -> tuple[set[str], bool]:
        found, truncated = set(), False
        for k in keys:
            col = stats.profiles[k].columns["mp_name"]
            truncated |= bool(col.untracked)
            found |= {fold_person_name(v) for v in (col.values or {})}
        return found - {""}, truncated

    allocation_names, t1 = names("allocation")
    works_names, t2 = names(*WORK_SOURCES, "expenditure")
    calamity_names, t3 = names("calamity")
    out += table(["Comparison (honorifics such as Shri / Smt / Dr ignored)", "Names", "Found on other side", "Not found"], [
        ["Allocated Limit MPs vs. MPs in works & expenditure", fmt(len(allocation_names)), fmt(len(allocation_names & works_names)), fmt(len(allocation_names - works_names))],
        ["MPs in works & expenditure vs. Allocated Limit", fmt(len(works_names)), fmt(len(works_names & allocation_names)), fmt(len(works_names - allocation_names))],
        ["Calamity consent MPs vs. Allocated Limit", fmt(len(calamity_names)), fmt(len(calamity_names & allocation_names)), fmt(len(calamity_names - allocation_names))],
    ])
    if t1 or t2 or t3:
        out.append("Note: distinct-name tracking hit its cap, so these overlaps are approximate.\n")
    missing = sorted(allocation_names - works_names)[:10]
    if missing:
        out.append("Allocated Limit MPs with no match in the works data (honorifics ignored; first 10): " + ", ".join(f"`{m}`" for m in missing) + "\n")


def _section_outputs(stats: PipelineStats, out: list[str]) -> None:
    out.append("## 13. Output files\n")
    out += table(["File", "Rows", "Columns"], [[f"{o.path.name}", fmt(o.rows), len(o.columns)] for o in stats.outputs.values()])
    if stats.demo:
        out.append("Demo subset (`data/demo/`): " + ", ".join(f"{k}: {fmt(v)} rows" for k, v in stats.demo.items()) + "\n")


ASSUMPTIONS = """## 14. Assumptions and limitations

* Only columns seen in the data spike are read. Headers are matched ignoring case, spacing and punctuation; a missing required column stops the run with an explicit error.
* Workbooks are read with a standard-library ZIP/XML streaming reader that ignores Excel styles and number formats. Values are read as stored (cached values, not formulas). Text keeps its original case; only whitespace is normalised. `N/A`, `-`, `null` and similar placeholders are treated as missing. A numeric cell in a date column is treated as an Excel serial number (1900 date system) and counted separately in section 8.
* Dates: `DD-Mon-YYYY` is the observed format. Other formats are accepted and counted as *non-standard*; numeric `dd/mm/yyyy` is read day-first. Impossible calendar dates are invalid, not guessed.
* Amounts: read as exact decimals; commas (Western or Indian grouping), currency symbols and accounting brackets are tolerated. Negative values are kept and counted. Values of 10^15 rupees or more, or with more than 12 decimal places, are treated as invalid (guard against corrupt cells).
* Only the first sheet that contains the expected header is read; any other sheet in a workbook is listed in section 3 and in the headline findings.
* The plausibility window for dates (1993-12-23 to the as-of date) is an assumption used only to flag values, never to change them.
* Work IDs must have four `/`-separated parts (prefix, MP code, `YYYY-YYYY`, numeric serial). Anything else is logged as malformed rather than repaired. A financial year that is not consecutive (e.g. 2024-2026) is accepted but counted as a quirk.
* Duplicates: first occurrence wins; repeats are logged. Exact-duplicate detection compares normalised content with a 64-bit hash (collision odds are negligible at this scale).
* A Work ID that appears only in Expenditure does not get a master row; it is reported under unmatched IDs.
* Checks in section 10 are diagnostics on the exported snapshot. Timing differences between exports, partial sanctions or data-entry conventions can all produce hits, so they are review candidates, not findings of error or wrongdoing.
* Categorical consistency detects case / spacing / punctuation variants only, not misspellings.
"""


def render_quality_report(stats: PipelineStats) -> str:
    out: list[str] = ["# MPLADS Data Quality Report\n"]
    out.append(
        "> Generated by `data_pipeline/build_dataset.py`. Every figure below was computed from the local workbooks during "
        "that run; regenerate rather than edit by hand.\n"
    )
    if stats.run.limit_rows:
        out.append(
            f"> **PARTIAL RUN** - only the first {fmt(stats.run.limit_rows)} data rows of each workbook were processed "
            "(`--limit-rows`). Join, duplicate and conflict figures describe that slice only.\n"
        )
    _section_run(stats, out)
    _section_headlines(stats, out)
    _section_sources(stats, out)
    _section_work_ids(stats, out)
    _section_duplicates(stats, out)
    _section_join(stats, out)
    _section_conflicts(stats, out)
    _column_tables(stats, out)
    _categorical(stats, out)
    _section_checks(stats, out)
    _section_expenditure(stats, out)
    _section_reference(stats, out)
    _section_outputs(stats, out)
    out.append(ASSUMPTIONS)
    return "\n".join(out).rstrip() + "\n"


# --------------------------------------------------------------------- data dictionary
# column -> (type, meaning, candidate detector(s), notes)
MASTER_DOCS = {
    "work_id": ("text", "Canonical Work ID `PREFIX/MPCODE/YYYY-YYYY/SERIAL`, parsed from the `Work` cell", "all (join key)", "Never derived from Sr. No."),
    "id_mp_code": ("text", "2nd segment of the Work ID (e.g. `MP620`)", "-", "Derived from work_id. Check the MP-code diagnostic in the quality report before treating it as an MP key."),
    "id_financial_year": ("text", "3rd segment of the Work ID (e.g. `2024-2025`)", "Delay", "Derived from work_id; not assumed to equal the sanction year."),
    "work_category": ("text", "Work category as written in the source", "Cost, Duplicate", "Sanctioned > Recommended > Completed"),
    "work": ("text", "Type of work: text that follows the ID in the `Work` cell", "Cost, Duplicate", "Sanctioned > Recommended > Completed"),
    "state": ("text", "State", "Cost, Fund-utilization", "Sanctioned > Recommended > Completed"),
    "ida": ("text", "Implementing district authority as written", "Cost", "Sanctioned > Recommended > Completed"),
    "mp_name": ("text", "Hon'ble Member of Parliament as written (honorifics kept)", "Fund-utilization", "Sanctioned > Recommended > Completed"),
    "constituency": ("text", "Constituency as written", "Cost", "Sanctioned > Recommended > Completed"),
    "work_description": ("text", "Free-text work description", "Duplicate", "Sanctioned > Recommended > Completed"),
    "recommended_date": ("date (ISO)", "Date the work was recommended", "Delay", "Recommended first, then Sanctioned"),
    "recommended_amount": ("decimal (rupees)", "Recommended amount", "Cost", "Recommended only"),
    "sanction_date": ("date (ISO)", "Sanction date", "Delay", "Sanctioned first, then the Sanction Date column of Recommended"),
    "sanction_amount": ("decimal (rupees)", "Sanctioned amount", "Cost, Fund-utilization", "Sanctioned only"),
    "work_status": ("text", "Work Status as written in the Sanctioned workbook", "Delay", "Meaning of each status is not assumed; see status table in the quality report"),
    "completion_date": ("date (ISO)", "Completion date", "Delay", "Completed only"),
    "completed_amount_disbursed": ("decimal (rupees)", "`Amount Disbursed` in the Completed workbook", "Fund-utilization", "Completed only"),
    "in_recommended": ("flag 1/0", "Work ID present in Recommended", "-", ""),
    "in_sanctioned": ("flag 1/0", "Work ID present in Sanctioned", "-", ""),
    "in_completed": ("flag 1/0", "Work ID present in Completed", "-", ""),
    "recommended_source_row": ("integer", "Excel row of the kept Recommended record", "-", "Traceability to the raw workbook"),
    "sanctioned_source_row": ("integer", "Excel row of the kept Sanctioned record", "-", "Traceability"),
    "completed_source_row": ("integer", "Excel row of the kept Completed record", "-", "Traceability"),
    "recommended_row_count": ("integer", "Rows carrying this Work ID in Recommended", "Duplicate", "More than 1 = repeated ID"),
    "sanctioned_row_count": ("integer", "Rows carrying this Work ID in Sanctioned", "Duplicate", "More than 1 = repeated ID"),
    "completed_row_count": ("integer", "Rows carrying this Work ID in Completed", "Duplicate", "More than 1 = repeated ID"),
    "conflict_fields": ("text", "`;`-separated fields where sources disagreed", "-", "Details in work_conflicts.csv"),
}
TXN_DOCS = {
    "work_id": ("text", "Canonical Work ID from the `Work ID` column", "all (join key)", "Empty when the ID was malformed (see malformed_ids.csv)"),
    "state": ("text", "State", "Fund-utilization", ""),
    "work": ("text", "Type of work (no ID in this workbook's `Work` column)", "-", ""),
    "ida": ("text", "Implementing district authority", "-", ""),
    "mp_name": ("text", "Hon'ble Member of Parliament as written", "-", ""),
    "constituency": ("text", "Constituency", "-", ""),
    "expenditure_date": ("date (ISO)", "Expenditure date", "Delay, Fund-utilization", "Empty when missing/invalid"),
    "vendor_name": ("text", "Vendor name as written", "Fund-utilization", "Case/punctuation variants are folded only when counting vendors"),
    "payment_status": ("text", "Payment status as written", "Fund-utilization", "Status meanings are not assumed"),
    "fund_disbursed_amount": ("decimal (rupees)", "Fund Disbursed Amount", "Fund-utilization", "Empty when missing/invalid; may be negative (counted in quality report)"),
    "source_row": ("integer", "Excel row in the raw workbook", "-", "Traceability; unique per record"),
    "duplicate_record": ("flag 1/0", "1 if an earlier row has identical content (Sr. No. ignored)", "Duplicate", "Repeats are kept, not dropped"),
}
AGG_DOCS = {
    "work_id": ("text", "Canonical Work ID", "all (join key)", "One row per Work ID with at least one payment"),
    "total_disbursed": ("decimal (rupees)", "Exact sum of valid `fund_disbursed_amount` values", "Fund-utilization", "All payment statuses, duplicates and negatives included"),
    "payment_count": ("integer", "Payment rows for the work", "Fund-utilization", "Includes rows with a missing amount"),
    "first_expenditure_date": ("date (ISO)", "Earliest valid expenditure date", "Delay", ""),
    "last_expenditure_date": ("date (ISO)", "Latest valid expenditure date", "Delay", ""),
    "vendor_count": ("integer", "Distinct vendor names after folding case/punctuation", "Fund-utilization", "Rows without a vendor name are not counted"),
    "amount_missing_count": ("integer", "Payments with no valid amount", "-", "total_disbursed excludes these"),
    "date_missing_count": ("integer", "Payments with no valid date", "-", ""),
    "negative_amount_count": ("integer", "Payments with a negative amount", "Fund-utilization", ""),
    "duplicate_record_count": ("integer", "Payments flagged `duplicate_record = 1`", "Duplicate", ""),
}
ALLOCATION_DOCS = {
    "source_row": ("integer", "Excel row in the raw workbook", "-", ""),
    "state": ("text", "State", "-", ""),
    "mp_name": ("text", "Hon'ble Member of Parliament as written", "Fund-utilization", "Header spelled `Members of Parliaments` in this workbook"),
    "constituency": ("text", "Constituency", "-", ""),
    "allocated_amount": ("decimal (rupees)", "Allocated limit", "Fund-utilization", ""),
    "duplicate_record": ("flag 1/0", "Identical to an earlier row", "-", ""),
}
CALAMITY_DOCS = {
    "source_row": ("integer", "Excel row in the raw workbook", "-", ""),
    "calamity_type": ("text", "Calamity type as written", "-", ""),
    "calamity_name": ("text", "Calamity name as written", "-", ""),
    "mp_name": ("text", "Hon'ble Member of Parliament as written", "-", ""),
    "consent_date": ("date (ISO)", "Date of consent", "-", ""),
    "consent_amount": ("decimal (rupees)", "Consent amount", "Fund-utilization", ""),
    "duplicate_record": ("flag 1/0", "Identical to an earlier row", "-", ""),
}
AUDIT_DOCS = {
    "malformed_ids": "Every Work / Work ID cell that could not be parsed: source, source_row, column header, raw_value, reason, detail. Rows are excluded from keyed outputs (Expenditure rows stay in the transactions file with an empty work_id).",
    "work_id_duplicates": "Every repeat of a Work ID inside Recommended / Sanctioned / Completed: which row was kept, whether the repeat is `identical` or `conflicting`, and the differing fields.",
    "work_conflicts": "Every field where two sources hold different non-empty values for the same Work ID: chosen source/value and the value from each source.",
    "join_unmatched_ids": "Work IDs that break an expected relationship (Sanctioned not in Recommended, Completed not in Sanctioned, Expenditure not in Sanctioned / master, Completed without Expenditure, Recommended with a Sanction Date but not in Sanctioned).",
}
DATASETS = [
    ("works_master", "works_master.csv", "One row per canonical Work ID found in Recommended, Sanctioned or Completed.", MASTER_DOCS),
    ("expenditure_transactions", "expenditure_transactions.csv", "One row per payment / expenditure record in the Expenditure workbook.", TXN_DOCS),
    ("expenditure_by_work", "expenditure_by_work.csv", "One row per Work ID with at least one payment (aggregated from the transactions).", AGG_DOCS),
    ("mp_allocated_limits", "mp_allocated_limits.csv", "Normalised copy of the Allocated Limit workbook.", ALLOCATION_DOCS),
    ("calamity_consents", "calamity_consents.csv", "Normalised copy of the Amount consented for Calamity workbook.", CALAMITY_DOCS),
]


def _dictionary_table(stats: PipelineStats, name: str, docs: dict) -> list[str]:
    profile = stats.outputs[name]
    rows = []
    for column in profile.columns:
        dtype, meaning, detectors, notes = docs.get(column, ("text", "", "-", ""))
        rows.append([f"`{column}`", dtype, meaning, pct(profile.missing[column], profile.rows), f"`{trunc(profile.example[column], 50)}`" if column in profile.example else "-", detectors, notes])
    return table(["Column", "Type", "Meaning", "Missing %", "Example", "Candidate detector(s)", "Notes"], rows)


def _carried_into(key: str, column: str) -> str:
    if key in WORK_SOURCES:
        return "works_master" if column not in ("sr_no", "image") else "not carried (profiled only)"
    if key == "expenditure":
        return "expenditure_transactions" if column != "sr_no" else "not carried"
    return {"allocation": "mp_allocated_limits", "calamity": "calamity_consents"}[key] if column != "sr_no" else "not carried"


def render_data_dictionary(stats: PipelineStats) -> str:
    out = ["# MPLADS Data Dictionary\n"]
    out.append(
        "> Generated by `data_pipeline/build_dataset.py` from the local workbooks. Types, missing percentages and examples are "
        "measured on the processed files of that run; regenerate rather than edit by hand. \"Candidate detector(s)\" is a planning "
        "note against the modules in `MVP_SPEC.md` - nothing is implemented from it yet.\n"
    )
    if stats.run.limit_rows:
        out.append(f"> **PARTIAL RUN** - built from the first {fmt(stats.run.limit_rows)} data rows of each workbook.\n")

    out.append("## 1. Data snapshot\n")
    out.append(f"- Source: {SOURCE_URL}")
    latest = max((c.max_date for p in stats.profiles.values() for c in p.columns.values() if c.max_date), default=None)
    out.append(f"- Snapshot date: not stored inside the workbooks. Latest date found in any date column: **{latest or 'n/a'}**; file modification times below.")
    out.append(f"- Processed at (UTC): {stats.run.finished_utc}\n")
    rows = []
    for key, p in stats.profiles.items():
        layout = p.layout
        rows.append([p.spec.file_name, f"{p.fingerprint.get('size_bytes', 0) / 2**20:,.2f}", p.fingerprint.get("modified_utc", ""),
                     str(p.fingerprint.get("sha256", ""))[:16], fmt(p.data_rows), len(layout.headers) if layout else "-"])
    out += table(["Workbook (data/raw)", "MiB", "Modified (UTC)", "SHA-256 (first 16)", "Data rows", "Columns"], rows)

    out.append("## 2. Processed datasets (`data/processed/`)\n")
    for name, filename, grain, docs in DATASETS:
        profile = stats.outputs[name]
        out.append(f"### {filename}\n")
        out.append(f"{grain} **{fmt(profile.rows)} rows x {len(profile.columns)} columns.**\n")
        out += _dictionary_table(stats, name, docs)

    out.append("### Audit files\n")
    rows = [[f"`{name}.csv`", fmt(stats.outputs[name].rows), ", ".join(stats.outputs[name].columns), desc] for name, desc in AUDIT_DOCS.items()]
    out += table(["File", "Rows", "Columns", "Purpose"], rows)

    out.append("## 3. Source workbook column mapping\n")
    for key, p in stats.profiles.items():
        layout = p.layout
        if not layout:
            continue
        out.append(f"### {p.spec.file_name}\n")
        out.append(f"Sheet `{layout.sheet_name}`, title row text `{layout.title or '-'}`, header on row {layout.header_row}.\n")
        rows = [[f"`{layout.header_for[c.name]}`", f"`{c.name}`", c.kind, _carried_into(key, c.name)] for c in p.spec.columns if c.name in layout.header_for]
        out += table(["Header found in workbook", "Canonical column", "Kind", "Carried into"], rows)
        if layout.unmapped_headers:
            out.append(f"Headers present but not used: {', '.join(f'`{h}`' for h in layout.unmapped_headers)}\n")

    out.append("## 4. Work ID format\n")
    example = stats.outputs["works_master"].example.get("work_id", "WS/MP620/2024-2025/133166")
    out.append(
        f"`PREFIX/MPCODE/YYYY-YYYY/SERIAL`, e.g. `{example}`. In Recommended, Sanctioned and Completed the ID sits at the start of the "
        "`Work` cell, followed by `-` and the work type; the Expenditure workbook has a dedicated `Work ID` column. Normalisation upper-cases "
        "the ID and removes spaces (e.g. `WS/ MP620/...` -> `WS/MP620/...`). Values that do not fit are logged to `malformed_ids.csv`.\n"
    )
    out.append("## 5. Merge rules for works_master.csv\n")
    out += [
        "1. Join key is the canonical Work ID only.",
        "2. A repeated ID inside one source keeps its first row; repeats are logged in `work_id_duplicates.csv`.",
        "3. Descriptive fields take the first non-empty value in the order Sanctioned, Recommended, Completed; `sanction_date` prefers Sanctioned, `recommended_date` prefers Recommended.",
        "4. Different non-empty values are never overwritten silently - they are listed in `work_conflicts.csv` and named in `conflict_fields`.",
        "5. IDs found only in Expenditure get no master row (see `join_unmatched_ids.csv`).",
        "",
    ]
    out.append("## 6. Required data-quality checks\n")
    out += [
        "| Check | Where reported |", "| --- | --- |",
        "| Missingness by column | DATA_QUALITY_REPORT section 8 |",
        "| Duplicate rows / duplicate Work IDs | sections 5 and 6 |",
        "| Malformed Work IDs | section 4, `malformed_ids.csv` |",
        "| Invalid dates, negative monetary values, numeric parsing issues | section 8 |",
        "| Impossible date order and amount relationships | section 10 |",
        "| Unknown / inconsistent categorical values | section 9 |",
        "| Encoding / text issues | section 8 (whitespace-normalised, suspect encoding) |",
        "| Zero / negative denominators | not applicable until ratio features exist |",
        "",
    ]
    return "\n".join(out).rstrip() + "\n"
