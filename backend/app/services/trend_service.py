"""Event-based monthly Trend Intelligence v1 service.

Aggregates implementation milestones (recommendation, sanction, completion) from
works_master.csv and expenditure transactions from expenditure_transactions.csv
into chronological monthly series.

Design principles:
- Independent of FastAPI.
- Streaming CSV reading with compact in-memory monthly buckets (no raw rows kept).
- Thread-safe lazy loading consistent with WorkStore.
- Resilient date and numeric parsing (invalid dates/amounts are safely ignored;
  no NaN or Infinity in responses).
- Continuous monthly timeline with inactive months zero-filled.
- Clean separation between event trends and static anomaly snapshots.
"""

from __future__ import annotations

import csv
import logging
import math
import threading
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from ..config import Settings
from ..models import TrendPoint, TrendResponse
from .data_service import to_date, to_flag, to_float

log = logging.getLogger("uvicorn.error.mplads")


@dataclass(slots=True)
class MonthlyBucket:
    """Compact aggregated metrics for a single month."""

    recommended_works: int = 0
    sanctioned_works: int = 0
    completed_works: int = 0
    expenditure_transactions: int = 0
    expenditure_amount: float = 0.0
    payment_success_amount: float = 0.0
    payment_in_progress_amount: float = 0.0


def to_period(raw: str | None) -> str | None:
    """Extract YYYY-MM period from an ISO date string, or None if invalid."""
    if not raw:
        return None
    d = to_date(raw)
    return f"{d.year:04d}-{d.month:02d}" if d else None


def to_amount(raw: str | None) -> float:
    """Convert amount string to a finite float, or 0.0 if missing/invalid."""
    if not raw:
        return 0.0
    num = to_float(raw)
    return num if num is not None and math.isfinite(num) else 0.0


def is_in_recommended(raw: str | None) -> bool:
    """Check if in_recommended indicates a valid recommendation source row.

    Returns False only when explicitly 0 / false; True if 1 / true or column absent.
    """
    if raw is None:
        return True
    flag = to_flag(raw)
    return flag is not False


def generate_period_range(start_period: str, end_period: str) -> list[str]:
    """Generate all consecutive months from start_period to end_period inclusive."""
    start_y, start_m = map(int, start_period.split("-"))
    end_y, end_m = map(int, end_period.split("-"))
    periods: list[str] = []
    curr_y, curr_m = start_y, start_m
    while (curr_y, curr_m) <= (end_y, end_m):
        periods.append(f"{curr_y:04d}-{curr_m:02d}")
        curr_m += 1
        if curr_m > 12:
            curr_m = 1
            curr_y += 1
    return periods


@contextmanager
def open_csv_stream(path: Path) -> Iterator[tuple[list[str], Iterator[list[str]]]]:
    """Stream CSV rows directly from disk without reading the entire file into RAM."""
    handle = None
    try:
        try:
            handle = open(path, "r", encoding="utf-8-sig", newline="")
            reader = csv.reader(handle)
            header = [col.strip() for col in next(reader, ())]
            yield header, reader
        except UnicodeDecodeError:
            if handle is not None:
                handle.close()
            handle = open(path, "r", encoding="cp1252", errors="replace", newline="")
            reader = csv.reader(handle)
            header = [col.strip() for col in next(reader, ())]
            yield header, reader
    finally:
        if handle is not None and not handle.closed:
            handle.close()


class TrendService:
    """Serves monthly event-based implementation and expenditure trends."""

    def __init__(
        self,
        settings: Settings,
        *,
        works_master_path: Path | None = None,
        expenditure_transactions_path: Path | None = None,
    ) -> None:
        self._settings = settings
        self._works_master_path = works_master_path or (settings.processed_dir / "works_master.csv")
        self._expenditure_transactions_path = expenditure_transactions_path or (
            settings.processed_dir / "expenditure_transactions.csv"
        )
        self._lock = threading.Lock()
        self._loaded = False
        self._national: dict[str, MonthlyBucket] = {}
        self._by_state: dict[str, dict[str, MonthlyBucket]] = {}
        self._warnings: list[str] = []
        self._loaded_at: datetime | None = None

    def ensure_loaded(self) -> None:
        """Thread-safe lazy loading."""
        if self._loaded:
            return
        with self._lock:
            if not self._loaded:
                self._load()
                self._loaded = True

    def _load(self) -> None:
        warnings: list[str] = []
        national: dict[str, MonthlyBucket] = defaultdict(MonthlyBucket)
        by_state: dict[str, dict[str, MonthlyBucket]] = defaultdict(lambda: defaultdict(MonthlyBucket))

        # 1. Load works_master events (recommendations, sanctions, completions)
        if self._works_master_path.is_file():
            self._load_works(self._works_master_path, national, by_state)
        else:
            warnings.append(
                f"{self._works_master_path.name} not found: works implementation trends will be empty."
            )

        # 2. Load expenditure transactions
        if self._expenditure_transactions_path.is_file():
            self._load_expenditures(self._expenditure_transactions_path, national, by_state)
        else:
            warnings.append(
                f"{self._expenditure_transactions_path.name} not found: expenditure trends will be empty."
            )

        self._national = dict(national)
        self._by_state = {state: dict(buckets) for state, buckets in by_state.items()}
        self._warnings = warnings
        self._loaded_at = datetime.now(timezone.utc).replace(microsecond=0)
        log.info(
            "MPLADS trends loaded: %d national month(s), %d state(s)",
            len(self._national),
            len(self._by_state),
        )

    def _load_works(
        self,
        path: Path,
        national: dict[str, MonthlyBucket],
        by_state: dict[str, dict[str, MonthlyBucket]],
    ) -> None:
        with open_csv_stream(path) as (header, reader):
            lookup = {name.lower(): idx for idx, name in enumerate(header)}
            state_idx = lookup.get("state")
            rec_idx = lookup.get("recommended_date")
            san_idx = lookup.get("sanction_date")
            com_idx = lookup.get("completion_date")
            in_rec_idx = lookup.get("in_recommended")

            for row in reader:
                if not row:
                    continue
                row_len = len(row)
                state = (
                    row[state_idx].strip()
                    if state_idx is not None and state_idx < row_len
                    else None
                )
                if state == "":
                    state = None

                # Recommended works: in_recommended != 0 and valid recommended_date
                in_rec_val = (
                    row[in_rec_idx].strip()
                    if in_rec_idx is not None and in_rec_idx < row_len
                    else None
                )
                if is_in_recommended(in_rec_val) and rec_idx is not None and rec_idx < row_len:
                    p_rec = to_period(row[rec_idx])
                    if p_rec:
                        national[p_rec].recommended_works += 1
                        if state:
                            by_state[state][p_rec].recommended_works += 1

                # Sanctioned works: valid sanction_date
                if san_idx is not None and san_idx < row_len:
                    p_san = to_period(row[san_idx])
                    if p_san:
                        national[p_san].sanctioned_works += 1
                        if state:
                            by_state[state][p_san].sanctioned_works += 1

                # Completed works: valid completion_date
                if com_idx is not None and com_idx < row_len:
                    p_com = to_period(row[com_idx])
                    if p_com:
                        national[p_com].completed_works += 1
                        if state:
                            by_state[state][p_com].completed_works += 1

    def _load_expenditures(
        self,
        path: Path,
        national: dict[str, MonthlyBucket],
        by_state: dict[str, dict[str, MonthlyBucket]],
    ) -> None:
        with open_csv_stream(path) as (header, reader):
            lookup = {name.lower(): idx for idx, name in enumerate(header)}
            state_idx = lookup.get("state")
            date_idx = lookup.get("expenditure_date")
            amt_idx = lookup.get("fund_disbursed_amount")
            status_idx = lookup.get("payment_status")

            if date_idx is None:
                return

            for row in reader:
                if not row:
                    continue
                row_len = len(row)
                if date_idx >= row_len:
                    continue

                p_exp = to_period(row[date_idx])
                if not p_exp:
                    continue  # Invalid or missing expenditure date safely ignored

                state = (
                    row[state_idx].strip()
                    if state_idx is not None and state_idx < row_len
                    else None
                )
                if state == "":
                    state = None

                raw_amt = row[amt_idx].strip() if amt_idx is not None and amt_idx < row_len else None
                amt = to_amount(raw_amt)

                status = (
                    row[status_idx].strip()
                    if status_idx is not None and status_idx < row_len
                    else ""
                )
                is_success = status == "Payment Success"
                is_in_progress = status == "Payment In-Progress"

                # National aggregation
                b_nat = national[p_exp]
                b_nat.expenditure_transactions += 1
                b_nat.expenditure_amount += amt
                if is_success:
                    b_nat.payment_success_amount += amt
                elif is_in_progress:
                    b_nat.payment_in_progress_amount += amt

                # State-filtered aggregation
                if state:
                    b_st = by_state[state][p_exp]
                    b_st.expenditure_transactions += 1
                    b_st.expenditure_amount += amt
                    if is_success:
                        b_st.payment_success_amount += amt
                    elif is_in_progress:
                        b_st.payment_in_progress_amount += amt

    def get_trends(self, state: str | None = None) -> TrendResponse:
        """Serve monthly trend intelligence, either national or state-filtered."""
        self.ensure_loaded()

        if state is None or not state.strip():
            # National trends
            target_buckets = self._national
            response_state = None
        else:
            req_state = state.strip()
            # State filtering: match exact existing stored state
            if req_state not in self._by_state:
                # Unknown state -> return valid empty response (HTTP 200)
                return TrendResponse(
                    granularity="month",
                    start_period=None,
                    end_period=None,
                    state=state,
                    series=[],
                )
            target_buckets = self._by_state[req_state]
            response_state = req_state

        if not target_buckets:
            return TrendResponse(
                granularity="month",
                start_period=None,
                end_period=None,
                state=response_state,
                series=[],
            )

        sorted_periods = sorted(target_buckets.keys())
        start_period = sorted_periods[0]
        end_period = sorted_periods[-1]
        all_periods = generate_period_range(start_period, end_period)

        series: list[TrendPoint] = []
        for p in all_periods:
            b = target_buckets.get(p)
            if b is not None:
                series.append(
                    TrendPoint(
                        period=p,
                        recommended_works=b.recommended_works,
                        sanctioned_works=b.sanctioned_works,
                        completed_works=b.completed_works,
                        expenditure_transactions=b.expenditure_transactions,
                        expenditure_amount=round(b.expenditure_amount, 2),
                        payment_success_amount=round(b.payment_success_amount, 2),
                        payment_in_progress_amount=round(b.payment_in_progress_amount, 2),
                    )
                )
            else:
                # Inactive month zero-filled
                series.append(
                    TrendPoint(
                        period=p,
                        recommended_works=0,
                        sanctioned_works=0,
                        completed_works=0,
                        expenditure_transactions=0,
                        expenditure_amount=0.0,
                        payment_success_amount=0.0,
                        payment_in_progress_amount=0.0,
                    )
                )

        return TrendResponse(
            granularity="month",
            start_period=start_period,
            end_period=end_period,
            state=response_state,
            series=series,
        )

    @property
    def warnings(self) -> list[str]:
        self.ensure_loaded()
        return list(self._warnings)

    @property
    def loaded_at(self) -> datetime | None:
        self.ensure_loaded()
        return self._loaded_at
