"""Write the small, deterministic demo subset (real rows only - nothing synthetic)."""

from __future__ import annotations

import csv
from pathlib import Path

csv.field_size_limit(2**31 - 1)  # descriptions can be long; the default limit is 128 KiB

DEMO_TABLES = ("works_master", "expenditure_transactions", "expenditure_by_work")

README_TEMPLATE = """# Demo subset

This folder holds a **small, real subset** of the processed MPLADS data so the
dashboard / API can be developed and demonstrated without loading the full
snapshot. Nothing here is synthetic.

* Selection: {size} Work IDs that exist in `works_master.csv` **and** have
  payments in `expenditure_by_work.csv`, chosen by the smallest
  `blake2b("{seed}|<work_id>")` hash - deterministic, so the same input
  snapshot always yields the same sample.
* Files: `works_master_demo.csv`, `expenditure_transactions_demo.csv`,
  `expenditure_by_work_demo.csv` (same columns as the full files, filtered by
  `work_id`).
* Rows written: {counts}
* Regenerate: `python -m data_pipeline.build_dataset --demo-size {size} --demo-seed {seed}`
"""


def write_demo(processed_dir: Path, demo_dir: Path, work_ids: set[str], *, seed: str, size: int) -> dict[str, int]:
    """Filter each processed CSV to ``work_ids`` (streaming) and write a README."""
    demo_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for table in DEMO_TABLES:
        source = processed_dir / f"{table}.csv"
        target = demo_dir / f"{table}_demo.csv"
        written = 0
        with open(source, newline="", encoding="utf-8") as fin, open(
            target, "w", newline="", encoding="utf-8"
        ) as fout:
            reader = csv.reader(fin)
            writer = csv.writer(fout, lineterminator="\n")
            header = next(reader)
            writer.writerow(header)
            index = header.index("work_id")
            for row in reader:
                if row[index] in work_ids:
                    writer.writerow(row)
                    written += 1
        counts[table] = written
    readme = README_TEMPLATE.format(
        size=size, seed=seed, counts=", ".join(f"{name}: {n:,}" for name, n in counts.items())
    )
    (demo_dir / "README.md").write_text(readme, encoding="utf-8", newline="\n")
    return counts
