"""In-memory service for Potential Duplicate Work Detection v1.

Loads potential_duplicate_pairs_v1.csv and potential_duplicate_clusters_v1.csv once
into compact indexed in-memory structures to serve instant paginated queries and work lookups.
"""

from __future__ import annotations

import csv
import logging
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path for duplicate_engine imports
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from duplicate_engine.core import (
    extract_core_and_numerals,
    format_explanation_text,
    reconstruct_reasons,
)

from ..config import Settings
from ..models import (
    DuplicateClusterRecord,
    DuplicatePage,
    DuplicatePairRecord,
    DuplicateSummary,
    WorkDuplicatesResponse,
)
from .data_service import WorkStore, normalize_work_id, read_csv, to_float, to_int, to_text

log = logging.getLogger("uvicorn.error.mplads")


@dataclass(slots=True)
class CompactPair:
    pair_id: str
    work_id_a: str
    work_id_b: str
    cluster_id: str
    duplicate_risk_score: float
    review_priority: str
    is_batch_scheme: bool
    batch_frequency: int
    consecutive_serials: bool
    text_similarity: float
    amount_difference_pct: float | None
    date_gap_days: int | None
    state: str = ""
    constituency: str = ""
    max_amount: float = 0.0


class DuplicateService:
    """Loads duplicate pairs and clusters once (lazily, thread-safe) and serves queries."""

    def __init__(self, settings: Settings, works: WorkStore | None = None) -> None:
        self._settings = settings
        self._works = works
        self._lock = threading.Lock()
        self._loaded = False
        self._pairs: list[CompactPair] = []
        self._pairs_by_work_id: dict[str, list[CompactPair]] = {}
        self._clusters: dict[str, DuplicateClusterRecord] = {}
        self._cluster_by_work_id: dict[str, DuplicateClusterRecord] = {}
        self._summary: DuplicateSummary | None = None

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if not self._loaded:
                self._load()
                self._loaded = True

    def _load(self) -> None:
        pairs_path = self._settings.duplicate_pairs_path
        clusters_path = self._settings.duplicate_clusters_path

        pairs: list[CompactPair] = []
        pairs_by_work: dict[str, list[CompactPair]] = {}
        clusters: dict[str, DuplicateClusterRecord] = {}
        cluster_by_work: dict[str, DuplicateClusterRecord] = {}

        # 1. Load Clusters if available
        if clusters_path.is_file():
            try:
                header, reader = read_csv(clusters_path)
                header_idx = {name.strip().lower(): i for i, name in enumerate(header)}
                for row in reader:
                    if not row:
                        continue
                    cid = row[header_idx["cluster_id"]] if "cluster_id" in header_idx else ""
                    if not cid:
                        continue
                    w_count = to_int(row[header_idx["work_count"]]) if "work_count" in header_idx else 0
                    is_b = bool(to_int(row[header_idx["is_batch_scheme"]])) if "is_batch_scheme" in header_idx else False
                    w_ids_str = row[header_idx["work_ids"]] if "work_ids" in header_idx else ""
                    w_ids = [normalize_work_id(w) for w in w_ids_str.split(";") if w.strip()]

                    rec = DuplicateClusterRecord(
                        cluster_id=cid,
                        work_count=w_count or len(w_ids),
                        is_batch_scheme=is_b,
                        work_ids=w_ids,
                    )
                    clusters[cid] = rec
                    for w in w_ids:
                        cluster_by_work[w] = rec
            except Exception as e:
                log.warning("Failed to load %s: %s", clusters_path.name, e)

        # 2. Load Pairs (Compact Representation)
        if pairs_path.is_file():
            try:
                header, reader = read_csv(pairs_path)
                header_idx = {name.strip().lower(): i for i, name in enumerate(header)}

                def get_val(r: list[str], col: str) -> str:
                    idx = header_idx.get(col)
                    return r[idx].strip() if idx is not None and idx < len(r) else ""

                for row in reader:
                    if not row:
                        continue
                    w_a = normalize_work_id(get_val(row, "work_id_a"))
                    w_b = normalize_work_id(get_val(row, "work_id_b"))
                    if not w_a or not w_b:
                        continue

                    pid = get_val(row, "pair_id")
                    if not pid:
                        s1 = w_a.split('/')[-1] if '/' in w_a else w_a
                        s2 = w_b.split('/')[-1] if '/' in w_b else w_b
                        pid = f"DUP-{min(s1, s2)}-{max(s1, s2)}"

                    cid = get_val(row, "cluster_id")
                    raw_score = to_float(get_val(row, "duplicate_risk_score")) or 0.0
                    bounded_score = max(0.0, min(100.0, round(raw_score, 1)))

                    pri_val = get_val(row, "review_priority")
                    is_b = bool(to_int(get_val(row, "is_batch_scheme")))
                    if is_b or pri_val in ("Batch", "B") or "Batch" in pri_val:
                        full_priority = "Batch Scheme Representative Link"
                        is_b = True
                    elif pri_val in ("High", "H") or "High" in pri_val:
                        full_priority = "High-Confidence Potential Duplicate"
                    elif pri_val in ("Low", "L") or "Low" in pri_val:
                        full_priority = "Low-Confidence Similar Work"
                    else:
                        full_priority = "Medium-Confidence Potential Duplicate"

                    st = ""
                    co = ""
                    max_amt = 0.0
                    if self._works is not None:
                        wa = self._works.get(w_a)
                        wb = self._works.get(w_b)
                        st_cand = (wa.state if wa and wa.state else "") or (wb.state if wb and wb.state else "")
                        if st_cand:
                            st = sys.intern(st_cand)
                        co_cand = (wa.constituency if wa and wa.constituency else "") or (wb.constituency if wb and wb.constituency else "")
                        if co_cand:
                            co = sys.intern(co_cand)

                        amt_a = (wa.sanction_amount or wa.recommended_amount or 0.0) if wa else 0.0
                        amt_b = (wb.sanction_amount or wb.recommended_amount or 0.0) if wb else 0.0
                        max_amt = max(amt_a, amt_b)

                    cp = CompactPair(
                        pair_id=pid,
                        work_id_a=w_a,
                        work_id_b=w_b,
                        cluster_id=cid,
                        duplicate_risk_score=bounded_score,
                        review_priority=full_priority,
                        is_batch_scheme=is_b,
                        batch_frequency=to_int(get_val(row, "batch_frequency")) or 0,
                        consecutive_serials=bool(to_int(get_val(row, "consecutive_serials"))),
                        text_similarity=to_float(get_val(row, "text_similarity")) or 0.0,
                        amount_difference_pct=to_float(get_val(row, "amount_difference_pct")),
                        date_gap_days=to_int(get_val(row, "date_gap_days")),
                        state=st,
                        constituency=co,
                        max_amount=max_amt,
                    )
                    pairs.append(cp)
                    pairs_by_work.setdefault(w_a, []).append(cp)
                    pairs_by_work.setdefault(w_b, []).append(cp)
            except Exception as e:
                log.warning("Failed to load %s: %s", pairs_path.name, e)

        # 3. Calculate Mutually Exclusive Summary Metrics
        ordinary_pairs = [p for p in pairs if not p.is_batch_scheme]
        high_cnt = sum(1 for p in ordinary_pairs if p.review_priority == "High-Confidence Potential Duplicate")
        med_cnt = sum(1 for p in ordinary_pairs if p.review_priority == "Medium-Confidence Potential Duplicate")
        batch_cnt = sum(1 for p in pairs if p.is_batch_scheme)
        affected_works = len(pairs_by_work)

        summary = DuplicateSummary(
            total_duplicate_pairs=len(ordinary_pairs),
            high_confidence_pairs=high_cnt,
            medium_confidence_pairs=med_cnt,
            batch_scheme_pairs=batch_cnt,
            total_clusters=len(clusters),
            affected_works_count=affected_works,
        )

        self._pairs = pairs
        self._pairs_by_work_id = pairs_by_work
        self._clusters = clusters
        self._cluster_by_work_id = cluster_by_work
        self._summary = summary
        log.info(
            "Loaded %d duplicate pairs (Ordinary: %d, Batch: %d), %d clusters, %d affected works",
            len(pairs),
            len(ordinary_pairs),
            batch_cnt,
            len(clusters),
            affected_works,
        )

    def summary(self) -> DuplicateSummary:
        self.ensure_loaded()
        assert self._summary is not None
        return self._summary

    def _hydrate_pair(self, cp: CompactPair) -> DuplicatePairRecord:
        wa = self._works.get(cp.work_id_a) if self._works else None
        wb = self._works.get(cp.work_id_b) if self._works else None
        ma = self._works.master_row(cp.work_id_a) if self._works else None
        mb = self._works.master_row(cp.work_id_b) if self._works else None

        desc_a = (wa.cleaned_work_description or (ma.work if ma else None)) if wa else None
        desc_b = (wb.cleaned_work_description or (mb.work if mb else None)) if wb else None

        st = cp.state or ((wa.state if wa else None) or (wb.state if wb else None))
        co = cp.constituency or ((wa.constituency if wa else None) or (wb.constituency if wb else None))
        mp = (wa.mp_name if wa else None) or (wb.mp_name if wb else None)
        cat = (wa.work_category if wa else None) or (wb.work_category if wb else None)

        amt_a = (wa.sanction_amount or wa.recommended_amount) if wa else None
        amt_b = (wb.sanction_amount or wb.recommended_amount) if wb else None

        dt_a = None
        if ma:
            dt_obj = ma.sanction_date or ma.recommended_date
            if dt_obj:
                dt_a = dt_obj.isoformat()
        dt_b = None
        if mb:
            dt_obj = mb.sanction_date or mb.recommended_date
            if dt_obj:
                dt_b = dt_obj.isoformat()

        shared_tokens: list[str] = []
        if desc_a and desc_b:
            c1, _ = extract_core_and_numerals(desc_a)
            c2, _ = extract_core_and_numerals(desc_b)
            shared_tokens = sorted(c1 & c2)

        same_co = bool(co and wa and wb and wa.constituency == wb.constituency)
        reasons = reconstruct_reasons(
            text_similarity=cp.text_similarity,
            amount_difference_pct=cp.amount_difference_pct,
            amount_val=amt_a or amt_b,
            date_gap_days=cp.date_gap_days,
            date_val=dt_a or dt_b,
            same_constituency=same_co,
            work_category=cat,
            consecutive_serials=cp.consecutive_serials,
            shared_tokens_count=len(shared_tokens),
        )
        explanation = format_explanation_text(reasons)

        return DuplicatePairRecord(
            pair_id=cp.pair_id,
            work_id_a=cp.work_id_a,
            work_id_b=cp.work_id_b,
            cluster_id=cp.cluster_id or None,
            state=st or None,
            constituency=co or None,
            mp_name=mp or None,
            work_category=cat or None,
            description_a=desc_a,
            description_b=desc_b,
            sanction_amount_a=amt_a,
            sanction_amount_b=amt_b,
            amount_difference_pct=cp.amount_difference_pct,
            sanction_date_a=dt_a,
            sanction_date_b=dt_b,
            date_gap_days=cp.date_gap_days,
            text_similarity=cp.text_similarity,
            shared_entity_tokens=shared_tokens,
            duplicate_risk_score=cp.duplicate_risk_score,
            review_priority=cp.review_priority,
            is_batch_scheme=cp.is_batch_scheme,
            batch_frequency=cp.batch_frequency,
            consecutive_serials=cp.consecutive_serials,
            reasons=reasons,
            explanation_text=explanation,
        )

    def query(
        self,
        page: int = 1,
        page_size: int = 25,
        state: str | None = None,
        constituency: str | None = None,
        priority: str | None = None,
        is_batch: bool | None = None,
        min_score: float | None = None,
        sort_by: str = "score_desc",
    ) -> DuplicatePage:
        self.ensure_loaded()

        filtered = self._pairs

        # 1. Batch separation & priority filtering
        if is_batch is None:
            if priority and "batch" in priority.strip().lower():
                filtered = [p for p in filtered if p.is_batch_scheme]
            elif priority and ("high" in priority.strip().lower() or "medium" in priority.strip().lower() or "low" in priority.strip().lower()):
                filtered = [p for p in filtered if not p.is_batch_scheme and priority.strip().lower() in p.review_priority.lower()]
            else:
                # Default query isolates batch links from ordinary duplicate review queue
                filtered = [p for p in filtered if not p.is_batch_scheme]
        elif is_batch is True:
            filtered = [p for p in filtered if p.is_batch_scheme]
            if priority and priority.strip():
                filtered = [p for p in filtered if priority.strip().lower() in p.review_priority.lower()]
        elif is_batch is False:
            filtered = [p for p in filtered if not p.is_batch_scheme]
            if priority and priority.strip():
                filtered = [p for p in filtered if priority.strip().lower() in p.review_priority.lower()]

        # 2. State & Constituency filtering
        if state and state.strip():
            target_state = state.strip().lower()
            filtered = [p for p in filtered if p.state and p.state.lower() == target_state]

        if constituency and constituency.strip():
            target_co = constituency.strip().lower()
            filtered = [p for p in filtered if p.constituency and p.constituency.lower() == target_co]

        # 3. Minimum score
        if min_score is not None:
            filtered = [p for p in filtered if p.duplicate_risk_score >= min_score]

        # 4. Sorting
        if sort_by == "score_asc":
            filtered = sorted(filtered, key=lambda p: p.duplicate_risk_score)
        elif sort_by == "amount_desc":
            filtered = sorted(filtered, key=lambda p: p.max_amount, reverse=True)
        elif sort_by == "date_gap_asc":
            filtered = sorted(filtered, key=lambda p: p.date_gap_days if p.date_gap_days is not None else 9999)
        else:  # score_desc default
            filtered = sorted(filtered, key=lambda p: p.duplicate_risk_score, reverse=True)

        total = len(filtered)
        start = (page - 1) * page_size
        page_items = filtered[start : start + page_size]
        hydrated_items = [self._hydrate_pair(p) for p in page_items]

        return DuplicatePage(items=hydrated_items, total=total, page=page, page_size=page_size)

    def get_for_work(self, work_id: str) -> WorkDuplicatesResponse:
        self.ensure_loaded()
        norm_id = normalize_work_id(work_id)
        pairs = self._pairs_by_work_id.get(norm_id, [])
        hydrated_pairs = [self._hydrate_pair(p) for p in pairs]

        cluster = self._cluster_by_work_id.get(norm_id)
        cluster_id = cluster.cluster_id if cluster else None
        cluster_works = cluster.work_ids if cluster else []

        return WorkDuplicatesResponse(
            work_id=norm_id,
            has_duplicates=len(pairs) > 0,
            duplicate_pairs=hydrated_pairs,
            cluster_id=cluster_id,
            cluster_work_ids=cluster_works,
        )

