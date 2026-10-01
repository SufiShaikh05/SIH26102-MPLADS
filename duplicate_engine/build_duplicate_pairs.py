"""Deterministic offline batch generator for Potential Duplicate Work Detection.

Generates:
- data/processed/potential_duplicate_pairs_v1.csv
- data/processed/potential_duplicate_clusters_v1.csv

Usage:
    python -m duplicate_engine.build_duplicate_pairs
"""

from __future__ import annotations

import csv
import logging
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from .core import (
    ScoredPair,
    build_clusters_from_high_edges,
    char_trigrams,
    extract_amount_with_fallback,
    extract_core_and_numerals,
    extract_date_with_fallback,
    normalize_description,
    score_candidate_pair,
    trigram_dice,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("duplicate_engine")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKS_MASTER_FILE = PROJECT_ROOT / "data" / "processed" / "works_master.csv"
OUTPUT_PAIRS_FILE = PROJECT_ROOT / "data" / "processed" / "potential_duplicate_pairs_v1.csv"
OUTPUT_CLUSTERS_FILE = PROJECT_ROOT / "data" / "processed" / "potential_duplicate_clusters_v1.csv"

# Minimum retention score threshold for human audit queue
RETENTION_SCORE_THRESHOLD = 75.0


def read_works_master(path: Path) -> list[dict[str, Any]]:
    """Reads works_master.csv robustly with utf-8-sig / cp1252 fallback."""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")

    reader = csv.DictReader(text.splitlines())
    return [row for row in reader if (row.get("work_id") or "").strip()]


def generate_candidate_pairs(
    works: list[dict[str, Any]],
    norm_texts: list[str],
    core_sets: list[set[str]],
    token_freq_national: Counter[str],
) -> tuple[set[tuple[int, int]], int]:
    """Generates candidate pairs across 3 passes (intra-constituency + cross-constituency safety net)."""
    total_works = len(works)
    candidates: set[tuple[int, int]] = set()

    # Pass 1: Intra-constituency Partitioning
    by_sc: dict[tuple[str, str], list[int]] = defaultdict(list)
    for idx, w in enumerate(works):
        st = (w.get("state") or "").strip()
        co = (w.get("constituency") or "").strip()
        by_sc[(st, co)].append(idx)

    # Identify batch works up front: if normalized description repeats >= 10x in constituency or >= 30x nationwide
    batch_works = set()
    for idx in range(total_works):
        d = norm_texts[idx]
        st = (works[idx].get("state") or "").strip()
        co = (works[idx].get("constituency") or "").strip()
        # If template appears >= 10 times in constituency or >= 30 times nationwide, it's a batch scheme
        # We will capture it via consecutive chaining rather than quadratic token cross-products
        from collections import Counter
        # Will be checked using desc_freq dictionaries

    for (st, co), idxs in by_sc.items():
        if len(idxs) < 2:
            continue

        # Inverted index on normalized description first
        norm_map: dict[str, list[int]] = defaultdict(list)
        for i in idxs:
            if norm_texts[i] and len(norm_texts[i]) > 3 and norm_texts[i] != "...":
                norm_map[norm_texts[i]].append(i)

        batch_template_indices: set[int] = set()

        for norm, m_idxs in norm_map.items():
            if len(m_idxs) > 1:
                if len(m_idxs) <= 8:
                    # Small cluster (e.g. 2-8 works): emit all pairs
                    for a in range(len(m_idxs)):
                        for b in range(a + 1, len(m_idxs)):
                            candidates.add((min(m_idxs[a], m_idxs[b]), max(m_idxs[a], m_idxs[b])))
                else:
                    # Large batch scheme (>= 9 works): emit representative consecutive serial pairs ONLY
                    batch_template_indices.update(m_idxs)
                    # Sort by work_id serial
                    sorted_m = sorted(m_idxs, key=lambda idx: works[idx].get("work_id", ""))
                    for a in range(len(sorted_m) - 1):
                        candidates.add((min(sorted_m[a], sorted_m[a + 1]), max(sorted_m[a], sorted_m[a + 1])))

        # Inverted index on distinctive core tokens (for non-batch works or small templates)
        tok_idx: dict[str, list[int]] = defaultdict(list)
        for i in idxs:
            # Skip quadratic token pairing for works that are already part of large batch schemes
            if i in batch_template_indices:
                continue
            for tok in core_sets[i]:
                tok_idx[tok].append(i)

        for tok, m_idxs in tok_idx.items():
            if 1 < len(m_idxs) <= 25:
                for a in range(len(m_idxs)):
                    for b in range(a + 1, len(m_idxs)):
                        candidates.add((min(m_idxs[a], m_idxs[b]), max(m_idxs[a], m_idxs[b])))
            elif len(m_idxs) > 25:
                # Chain consecutive serials for semi-frequent tokens
                sorted_m = sorted(m_idxs, key=lambda idx: works[idx].get("work_id", ""))
                for a in range(len(sorted_m) - 1):
                    candidates.add((min(sorted_m[a], sorted_m[a + 1]), max(sorted_m[a], sorted_m[a + 1])))

    # Pass 2 & 3: Cross-Constituency Safety Net
    cross_const_count = 0

    # 3A: Nationwide Exact Normalized Description Index
    nat_norm_map: dict[str, list[int]] = defaultdict(list)
    for idx in range(total_works):
        d = norm_texts[idx]
        if d and len(d) > 8 and d != "...":
            nat_norm_map[d].append(idx)

    for norm, m_idxs in nat_norm_map.items():
        if 1 < len(m_idxs) <= 15:
            for a in range(len(m_idxs)):
                for b in range(a + 1, len(m_idxs)):
                    i1, i2 = m_idxs[a], m_idxs[b]
                    if works[i1].get("constituency") != works[i2].get("constituency"):
                        candidates.add((min(i1, i2), max(i1, i2)))
                        cross_const_count += 1

    # 3B: Rare-Token Safety Net across Nation
    rare_tokens = {
        tok for tok, cnt in token_freq_national.items() if 2 <= cnt <= 6 and len(tok) >= 5
    }
    rare_tok_idx: dict[str, list[int]] = defaultdict(list)
    for idx in range(total_works):
        for tok in core_sets[idx]:
            if tok in rare_tokens:
                rare_tok_idx[tok].append(idx)

    for tok, m_idxs in rare_tok_idx.items():
        for a in range(len(m_idxs)):
            for b in range(a + 1, len(m_idxs)):
                i1, i2 = m_idxs[a], m_idxs[b]
                if works[i1].get("constituency") != works[i2].get("constituency"):
                    candidates.add((min(i1, i2), max(i1, i2)))
                    cross_const_count += 1

    return candidates, cross_const_count


def build_duplicate_artifacts(
    works_master_path: Path = WORKS_MASTER_FILE,
    output_pairs_path: Path = OUTPUT_PAIRS_FILE,
    output_clusters_path: Path = OUTPUT_CLUSTERS_FILE,
) -> dict[str, Any]:
    """Main execution pipeline."""
    t_start = time.time()
    log.info("Starting Duplicate Work Detection pipeline...")
    log.info("Reading works from %s", works_master_path)

    works = read_works_master(works_master_path)
    total_works = len(works)
    log.info("Loaded %d works in %.2fs", total_works, time.time() - t_start)

    t_feat = time.time()
    log.info("Precomputing text and entity representations...")
    norm_texts: list[str] = []
    trigram_sets: list[frozenset[str]] = []
    core_sets: list[set[str]] = []
    num_sets: list[set[str]] = []
    desc_freq_national: Counter[str] = Counter()
    desc_freq_constituency: Counter[tuple[str, str, str]] = Counter()
    token_freq_national: Counter[str] = Counter()

    for idx, w in enumerate(works):
        raw = w.get("work_description") or ""
        norm = normalize_description(raw)
        norm_texts.append(norm)
        desc_freq_national[norm] += 1
        st = (w.get("state") or "").strip()
        co = (w.get("constituency") or "").strip()
        desc_freq_constituency[(st, co, norm)] += 1

        trigram_sets.append(char_trigrams(raw))
        c, n = extract_core_and_numerals(raw)
        core_sets.append(c)
        num_sets.append(n)
        for tok in c:
            token_freq_national[tok] += 1

    log.info("Feature precomputation completed in %.2fs", time.time() - t_feat)

    t_cand = time.time()
    log.info("Generating candidate pairs with multi-pass blocking...")
    candidates, cross_const_count = generate_candidate_pairs(
        works, norm_texts, core_sets, token_freq_national
    )
    log.info(
        "Candidate generation completed in %.2fs. Total candidate pairs: %d (including %d cross-constituency checks)",
        time.time() - t_cand,
        len(candidates),
        cross_const_count,
    )

    t_score = time.time()
    log.info("Scoring candidate pairs...")
    scored_pairs: list[ScoredPair] = []
    candidates_scored_count = 0

    for i1, i2 in candidates:
        candidates_scored_count += 1
        w1, w2 = works[i1], works[i2]
        d1_norm, d2_norm = norm_texts[i1], norm_texts[i2]
        st1, co1 = (w1.get("state") or "").strip(), (w1.get("constituency") or "").strip()
        const_freq = desc_freq_constituency[(st1, co1, d1_norm)]
        nat_freq = desc_freq_national[d1_norm]

        scored = score_candidate_pair(
            w1=w1,
            w2=w2,
            d1_norm=d1_norm,
            d2_norm=d2_norm,
            tg1=trigram_sets[i1],
            tg2=trigram_sets[i2],
            core1=core_sets[i1],
            core2=core_sets[i2],
            num1=num_sets[i1],
            num2=num_sets[i2],
            const_template_freq=const_freq,
            national_template_freq=nat_freq,
        )
        if scored is not None:
            scored_pairs.append(scored)

    log.info("Candidate scoring completed in %.2fs. Total pairs evaluated as similar: %d", time.time() - t_score, len(scored_pairs))

    # Clustering strictly on High-Confidence Edges
    t_clust = time.time()
    high_pairs = [p for p in scored_pairs if p.is_high_edge]
    log.info("Constructing clusters from %d High-Confidence edges...", len(high_pairs))
    work_to_cluster, cluster_summaries = build_clusters_from_high_edges(high_pairs)

    # Attach cluster_id to scored pairs
    for p in scored_pairs:
        c1 = work_to_cluster.get(p.work_id_a)
        c2 = work_to_cluster.get(p.work_id_b)
        if c1 and c1 == c2:
            p.cluster_id = c1

    log.info("Clustering completed in %.2fs. Total clusters formed: %d", time.time() - t_clust, len(cluster_summaries))

    # Retention Filtering for human review artifact
    persisted_pairs: list[ScoredPair] = []
    high_retained = 0
    med_retained = 0
    batch_retained = 0

    for p in scored_pairs:
        if p.is_batch_scheme:
            persisted_pairs.append(p)
            batch_retained += 1
        elif p.review_priority == "High-Confidence Potential Duplicate":
            persisted_pairs.append(p)
            high_retained += 1
        elif p.duplicate_risk_score >= RETENTION_SCORE_THRESHOLD:
            persisted_pairs.append(p)
            med_retained += 1

    # Sort persisted pairs by duplicate_risk_score descending
    persisted_pairs.sort(key=lambda p: p.duplicate_risk_score, reverse=True)

    log.info(
        "Retention filtering: %d total pairs retained (High: %d, Medium: %d, Batch: %d)",
        len(persisted_pairs),
        high_retained,
        med_retained,
        batch_retained,
    )

    # Write Compact Pairs CSV
    output_pairs_path.parent.mkdir(parents=True, exist_ok=True)
    pair_fields = [
        "pair_id",
        "work_id_a",
        "work_id_b",
        "cluster_id",
        "duplicate_risk_score",
        "review_priority",
        "is_batch_scheme",
        "batch_frequency",
        "consecutive_serials",
        "text_similarity",
        "amount_difference_pct",
        "date_gap_days",
    ]

    with open(output_pairs_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(pair_fields)
        for p in persisted_pairs:
            if p.is_batch_scheme:
                pri = "Batch"
            elif "High" in p.review_priority:
                pri = "High"
            else:
                pri = "Medium"

            writer.writerow([
                p.pair_id,
                p.work_id_a,
                p.work_id_b,
                p.cluster_id,
                f"{p.duplicate_risk_score:.1f}",
                pri,
                p.is_batch_scheme,
                p.batch_frequency,
                p.consecutive_serials,
                f"{p.text_similarity:.2f}",
                f"{p.amount_difference_pct:.2f}" if p.amount_difference_pct is not None else "",
                p.date_gap_days if p.date_gap_days is not None else "",
            ])

    pairs_size_bytes = output_pairs_path.stat().st_size
    log.info("Written %s (%d rows, %.2f MB)", output_pairs_path.name, len(persisted_pairs), pairs_size_bytes / 1024 / 1024)

    # Write Clusters CSV
    output_clusters_path.parent.mkdir(parents=True, exist_ok=True)
    cluster_fields = [
        "cluster_id",
        "work_count",
        "is_batch_scheme",
        "work_ids",
    ]

    with open(output_clusters_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(cluster_fields)
        for c in cluster_summaries:
            writer.writerow([
                c["cluster_id"],
                c["work_count"],
                c["is_batch_scheme"],
                c["work_ids"],
            ])

    clusters_size_bytes = output_clusters_path.stat().st_size
    log.info("Written %s (%d rows, %.2f KB)", output_clusters_path.name, len(cluster_summaries), clusters_size_bytes / 1024)

    total_time = time.time() - t_start
    log.info("Pipeline completed successfully in %.2fs!", total_time)

    return {
        "works_processed": total_works,
        "candidates_generated": len(candidates),
        "candidates_scored": candidates_scored_count,
        "high_confidence_pairs": high_retained,
        "medium_retained_pairs": med_retained,
        "batch_representative_pairs": batch_retained,
        "total_persisted_pairs": len(persisted_pairs),
        "clusters_count": len(cluster_summaries),
        "pairs_file_bytes": pairs_size_bytes,
        "clusters_file_bytes": clusters_size_bytes,
        "total_time_seconds": round(total_time, 2),
    }


if __name__ == "__main__":
    stats = build_duplicate_artifacts()
    print("\n--- DUPLICATE DETECTION BUILD COMPLETE ---")
    for k, v in stats.items():
        print(f"  {k}: {v}")
