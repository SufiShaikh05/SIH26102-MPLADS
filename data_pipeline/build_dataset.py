"""Build the normalised MPLADS datasets from the six raw workbooks.

Run from the repository root::

    python -m data_pipeline.build_dataset
    python -m data_pipeline.build_dataset --limit-rows 5000 --output-root .smoke   # quick trial

Raw workbooks are opened read-only and never modified; their SHA-256 is compared
before and after the run and the result is stated in the quality report.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # allow `python data_pipeline/build_dataset.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import datetime as dt
import decimal
import gc
import json
import logging
import platform
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from data_pipeline import staging
from data_pipeline.aggregate import AGG_COLUMNS, write_expenditure_by_work
from data_pipeline.demo import write_demo
from data_pipeline.excel_io import SchemaError, file_fingerprint
from data_pipeline.ingest import (
    MALFORMED_COLUMNS,
    TXN_COLUMNS,
    IngestContext,
    ingest_expenditure,
    ingest_reference,
    ingest_work_source,
)
from data_pipeline.merge import (
    CONFLICT_COLUMNS,
    DUPLICATE_COLUMNS,
    MASTER_COLUMNS,
    UNMATCHED_COLUMNS,
    MergeSinks,
    build_master,
)
from data_pipeline.parsing import MPLADS_START
from data_pipeline.profiling import SourceProfile
from data_pipeline.reports import render_data_dictionary, render_quality_report
from data_pipeline.schemas import SOURCES, WORK_SOURCES
from data_pipeline.sinks import CsvSink
from data_pipeline.stats import PipelineStats

log = logging.getLogger("mplads")


@dataclass(frozen=True)
class PipelineConfig:
    raw_dir: Path
    output_root: Path
    as_of: dt.date
    demo_size: int = 500
    demo_seed: str = "SIH26102"
    limit_rows: int | None = None
    keep_staging: bool = False
    progress_every: int = 100_000

    @property
    def processed_dir(self) -> Path:
        return self.output_root / "data" / "processed"

    @property
    def demo_dir(self) -> Path:
        return self.output_root / "data" / "demo"

    @property
    def interim_dir(self) -> Path:
        return self.output_root / "data" / "interim"

    @property
    def docs_dir(self) -> Path:
        return self.output_root / "docs"


def peak_memory_mib() -> float | None:
    """Peak working set of this process in MiB (stdlib only); ``None`` if unavailable."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return None
            return counters.PeakWorkingSetSize / 2**20
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / 2**20 if sys.platform == "darwin" else peak / 1024
    except Exception:  # noqa: BLE001 - diagnostics must never break the run
        return None


@contextmanager
def _run_logging(log_path: Path) -> Iterator[None]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout), logging.FileHandler(log_path, "w", encoding="utf-8")]
    for handler in handlers:
        handler.setFormatter(formatter)
        log.addHandler(handler)
    log.setLevel(logging.INFO)
    try:
        yield
    finally:
        for handler in handlers:
            log.removeHandler(handler)
            handler.close()


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def run_pipeline(cfg: PipelineConfig) -> PipelineStats:
    with _run_logging(cfg.processed_dir / "build_dataset.log"):
        return _run(cfg)


def _run(cfg: PipelineConfig) -> PipelineStats:
    decimal.getcontext().prec = 50
    started = time.perf_counter()
    stats = PipelineStats()
    run = stats.run
    run.started_utc = _utc_now()
    run.python = platform.python_version()
    run.xlsx_reader = "standard library (zipfile + xml.etree streaming; styles ignored)"
    run.platform = platform.platform()
    run.as_of = cfg.as_of
    run.limit_rows = cfg.limit_rows
    run.raw_dir, run.output_root = str(cfg.raw_dir), str(cfg.output_root)
    run.demo_size, run.demo_seed = cfg.demo_size, cfg.demo_seed

    raw_paths = {key: cfg.raw_dir / spec.file_name for key, spec in SOURCES.items()}
    missing = [str(p) for p in raw_paths.values() if not p.is_file()]
    if missing:
        raise FileNotFoundError("Missing raw workbook(s):\n  " + "\n  ".join(missing))
    for directory in (cfg.processed_dir, cfg.demo_dir, cfg.interim_dir, cfg.docs_dir):
        directory.mkdir(parents=True, exist_ok=True)

    log.info("Fingerprinting raw workbooks (read-only)")
    before = {key: file_fingerprint(path) for key, path in raw_paths.items()}
    profiles = {key: SourceProfile(spec) for key, spec in SOURCES.items()}
    for key, profile in profiles.items():
        profile.fingerprint = before[key]
    stats.profiles = profiles

    def mark(stage: str) -> None:
        gc.collect()
        run.peak_memory_mib[stage] = peak_memory_mib()
        log.info("peak memory after %s: %s", stage, run.peak_memory_mib[stage] and f"{run.peak_memory_mib[stage]:,.0f} MiB")

    processed = cfg.processed_dir
    sinks = {
        "master": CsvSink(processed / "works_master.csv", MASTER_COLUMNS),
        "transactions": CsvSink(processed / "expenditure_transactions.csv", TXN_COLUMNS),
        "by_work": CsvSink(processed / "expenditure_by_work.csv", AGG_COLUMNS),
        "allocation": CsvSink(processed / "mp_allocated_limits.csv", ["source_row", *SOURCES["allocation"].stored_columns, "duplicate_record"]),
        "calamity": CsvSink(processed / "calamity_consents.csv", ["source_row", *SOURCES["calamity"].stored_columns, "duplicate_record"]),
        "malformed": CsvSink(processed / "malformed_ids.csv", MALFORMED_COLUMNS),
        "duplicates": CsvSink(processed / "work_id_duplicates.csv", DUPLICATE_COLUMNS),
        "conflicts": CsvSink(processed / "work_conflicts.csv", CONFLICT_COLUMNS),
        "unmatched": CsvSink(processed / "join_unmatched_ids.csv", UNMATCHED_COLUMNS),
    }
    staging_path = cfg.interim_dir / "staging.sqlite"
    conn = staging.connect(staging_path)
    try:
        ctx = IngestContext(
            lo=MPLADS_START, hi=cfg.as_of, limit_rows=cfg.limit_rows,
            progress_every=cfg.progress_every, malformed=sinks["malformed"],
        )
        for key in WORK_SOURCES:
            log.info("Reading %s", SOURCES[key].file_name)
            ingest_work_source(SOURCES[key], raw_paths[key], conn, profiles[key], ctx)
            mark(f"ingest {key}")
        log.info("Reading %s", SOURCES["expenditure"].file_name)
        ingest_expenditure(SOURCES["expenditure"], raw_paths["expenditure"], conn, profiles["expenditure"], ctx, sinks["transactions"])
        mark("ingest expenditure")
        log.info("Aggregating expenditure by work")
        write_expenditure_by_work(conn, sinks["by_work"], stats.expenditure)
        for key in ("allocation", "calamity"):
            log.info("Reading %s", SOURCES[key].file_name)
            ingest_reference(SOURCES[key], raw_paths[key], profiles[key], ctx, sinks[key])
        mark("aggregate + reference data")
        log.info("Merging works and analysing joins")
        demo_ids = build_master(
            conn,
            MergeSinks(sinks["master"], sinks["conflicts"], sinks["duplicates"], sinks["unmatched"]),
            stats,
            demo_size=cfg.demo_size,
            demo_seed=cfg.demo_seed,
        )
        mark("merge")
    finally:
        for sink in sinks.values():
            sink.close()
        conn.close()
        if not cfg.keep_staging:
            staging_path.unlink(missing_ok=True)

    stats.outputs = {sink.profile.name: sink.profile for sink in sinks.values()}
    stats.malformed_logged = sinks["malformed"].rows
    if cfg.demo_size > 0:
        log.info("Writing demo subset (%s works)", len(demo_ids))
        stats.demo = write_demo(processed, cfg.demo_dir, demo_ids, seed=cfg.demo_seed, size=cfg.demo_size)

    log.info("Re-checking raw workbooks are unchanged")
    after = {key: file_fingerprint(path)["sha256"] for key, path in raw_paths.items()}
    run.raw_unchanged = all(after[key] == before[key]["sha256"] for key in raw_paths)

    run.finished_utc = _utc_now()
    run.elapsed_seconds = time.perf_counter() - started
    (cfg.docs_dir / "DATA_QUALITY_REPORT.md").write_text(render_quality_report(stats), encoding="utf-8", newline="\n")
    (cfg.docs_dir / "DATA_DICTIONARY.md").write_text(render_data_dictionary(stats), encoding="utf-8", newline="\n")
    (processed / "pipeline_summary.json").write_text(
        json.dumps(stats.to_summary(), indent=2, default=str), encoding="utf-8", newline="\n"
    )
    log.info("Done in %.0fs. Reports: %s", run.elapsed_seconds, cfg.docs_dir)
    return stats


def parse_args(argv: list[str] | None = None) -> PipelineConfig:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Build normalised MPLADS datasets from the raw workbooks.")
    parser.add_argument("--raw-dir", type=Path, default=root / "data" / "raw", help="folder with the six .xlsx files")
    parser.add_argument("--output-root", type=Path, default=root, help="folder that receives data/processed, data/demo, docs")
    parser.add_argument("--as-of", type=dt.date.fromisoformat, default=None, help="YYYY-MM-DD; dates after this are flagged (default: today)")
    parser.add_argument("--demo-size", type=int, default=500, help="works in data/demo (0 = none)")
    parser.add_argument("--demo-seed", default="SIH26102", help="seed for the deterministic demo sample")
    parser.add_argument("--limit-rows", type=int, default=None, help="process only the first N data rows per workbook (quick trial)")
    parser.add_argument("--keep-staging", action="store_true", help="keep data/interim/staging.sqlite for inspection")
    parser.add_argument("--progress-every", type=int, default=100_000, help="log progress every N rows")
    args = parser.parse_args(argv)
    return PipelineConfig(
        raw_dir=args.raw_dir,
        output_root=args.output_root,
        as_of=args.as_of or dt.date.today(),
        demo_size=args.demo_size,
        demo_seed=args.demo_seed,
        limit_rows=args.limit_rows,
        keep_staging=args.keep_staging,
        progress_every=args.progress_every,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles are often cp1252
    except (AttributeError, OSError, ValueError):
        pass
    cfg = parse_args(argv)
    try:
        run_pipeline(cfg)
    except FileNotFoundError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    except SchemaError as error:
        print(f"ERROR: unexpected workbook layout - {error}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
