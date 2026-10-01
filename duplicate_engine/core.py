"""Core algorithms and heuristics for Potential Duplicate Work Detection.

Features:
- Deterministic multi-pass blocking (intra-constituency + nationwide rare-token safety net)
- Explainable additive scoring with strict [0.0, 100.0] bounding
- Dedicated batch scheme classification independent of duplicate risk score
- Strict High-Confidence conjunction gate
- Clustering strictly on High-Confidence edges with size safeguards
- Neutral, auditor-focused explanations ending in standard disclaimer
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

DISCLAIMER_TEXT = (
    "This indicates potential duplicate entry or overlapping scope that may "
    "warrant verification and does not establish wrongdoing."
)

# 60+ domain-specific civil infrastructure and administrative template stopwords
DOMAIN_STOPWORDS = frozenset({
    "construction", "of", "repair", "renovation", "maintenance", "installation", "supply",
    "fixing", "erection", "fitting", "work", "works", "at", "in", "to", "towards", "from",
    "for", "near", "nearby", "under", "mp", "mplads", "scheme", "nag", "no", "nos", "km",
    "mtr", "meter", "meters", "cc", "road", "c.c.", "paver", "block", "rasta", "marg",
    "light", "led", "solar", "pole", "high", "mast", "pipe", "pipeline", "drain",
    "drainage", "boundary", "wall", "community", "hall", "bhavan", "bhawan", "school",
    "vidyalaya", "shala", "room", "shed", "building", "area", "para", "vistar", "nagar",
    "ward", "village", "gram", "panchayat", "house", "ghar", "mandir", "temple", "and", "the",
    "a", "an", "with", "by", "as", "per", "estimate", "approved", "interlocking", "providing",
    "laying", "development", "arrangement"
})


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_description(text: str | None) -> str:
    """Lowercase, strip non-alphanumeric, collapse whitespace."""
    if not text:
        return ""
    t = clean_text(text).lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return " ".join(t.split())


def char_trigrams(text: str | None) -> frozenset[str]:
    """Extract character trigrams for typo-tolerant similarity."""
    t = re.sub(r"\s+", " ", (text or "").lower().strip())
    if len(t) < 3:
        return frozenset({t}) if t else frozenset()
    return frozenset(t[i : i + 3] for i in range(len(t) - 2))


def trigram_dice(tg1: frozenset[str], tg2: frozenset[str]) -> float:
    """Dice coefficient over character trigrams."""
    if not tg1 or not tg2:
        return 0.0
    return (2.0 * len(tg1 & tg2)) / (len(tg1) + len(tg2))


def token_jaccard(tokens1: set[str], tokens2: set[str]) -> float:
    """Jaccard similarity over word tokens."""
    if not tokens1 or not tokens2:
        return 0.0
    intersection = len(tokens1 & tokens2)
    union = len(tokens1 | tokens2)
    return intersection / union if union else 0.0


def extract_core_and_numerals(text: str | None) -> tuple[set[str], set[str]]:
    """Extract distinctive core tokens (after domain stopword removal) and numeric tokens."""
    if not text:
        return set(), set()
    norm = normalize_description(text)
    tokens = norm.split()
    cores: set[str] = set()
    numerals: set[str] = set()

    for tok in tokens:
        if tok.isdigit():
            numerals.add(tok)
        elif len(tok) > 1 and tok not in DOMAIN_STOPWORDS:
            cores.add(tok)

    return cores, numerals


CONTEXT_PATTERNS = [
    ("ward", re.compile(r"\b(?:ward|w\.?\s*no\.?|ward\s*no\.?|ward\s*number)\s*[:#-]?\s*(\d+[a-z]?)\b", re.IGNORECASE)),
    ("pole", re.compile(r"\b(?:pole|pole\s*no\.?|pole\s*number|khamba|khambha)\s*[:#-]?\s*(\d+[a-z]?)\b", re.IGNORECASE)),
    ("house", re.compile(r"\b(?:house|h\.?\s*no\.?|house\s*no\.?|house\s*number|makan\s*no\.?|ghar\s*no\.?)\s*[:#-]?\s*(\d+[a-z]?)\b", re.IGNORECASE)),
    ("km", re.compile(r"\b(?:km|kilometer|k\.?m\.?|chainage|ch\.?)\s*[:#-]?\s*(\d+(?:\.\d+)?)\b", re.IGNORECASE)),
    ("sector_block", re.compile(r"\b(?:sector|sec\.?|block|phase|booth)\s*[:#-]?\s*(\d+[a-z]?)\b", re.IGNORECASE)),
    ("generic_no", re.compile(r"\b(?:no\.?|number|kramank|sankhya)\s*[:#-]?\s*(\d+[a-z]?)\b", re.IGNORECASE)),
]


def norm_num(s: str) -> str:
    s = s.strip().lower()
    return str(int(s)) if s.isdigit() else s.lstrip("0")


def extract_contextual_numerals(text: str | None) -> dict[str, set[str]]:
    """Extracts numbers grouped by semantic context class."""
    if not text:
        return {}
    res: dict[str, set[str]] = {}
    for ctx, pattern in CONTEXT_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            res[ctx] = {norm_num(m) for m in matches if m.strip()}
    return res


def detect_contextual_numeric_conflict(
    text1: str, text2: str, nums1: set[str], nums2: set[str]
) -> tuple[bool, str]:
    """Detects conflicting numerals using contextual classes first, then general positional logic."""
    ctx1 = extract_contextual_numerals(text1)
    ctx2 = extract_contextual_numerals(text2)

    # 1. Check shared contexts in priority order (ward, pole, house, km, etc. before generic_no)
    for ctx, _ in CONTEXT_PATTERNS:
        if ctx in ctx1 and ctx in ctx2:
            vals1 = ctx1[ctx]
            vals2 = ctx2[ctx]
            if vals1 and vals2 and len(vals1 & vals2) == 0:
                v1_str = ", ".join(sorted(vals1))
                v2_str = ", ".join(sorted(vals2))
                ctx_label = "serial/item" if ctx == "generic_no" else ctx
                return True, f"Conflicting {ctx_label} numbers detected ({v1_str} vs {v2_str}) - distinct locations"

    # 2. General numeral conflict:
    if nums1 and nums2:
        norm_n1 = {norm_num(n) for n in nums1}
        norm_n2 = {norm_num(n) for n in nums2}
        diff1 = norm_n1 - norm_n2
        diff2 = norm_n2 - norm_n1
        if diff1 and diff2:
            if len(norm_n1 & norm_n2) == 0:
                return True, f"Conflicting numerals detected ({', '.join(sorted(norm_n1))} vs {', '.join(sorted(norm_n2))}) - distinct locations"
            if any(len(d) <= 3 for d in diff1) and any(len(d) <= 3 for d in diff2):
                return True, f"Differing positional numerals detected ({', '.join(sorted(diff1))} vs {', '.join(sorted(diff2))})"

    return False, ""


def detect_numeric_conflict(nums1: set[str], nums2: set[str]) -> bool:
    """Legacy helper for backward compatibility."""
    if not nums1 or not nums2:
        return False
    return len(nums1 & nums2) == 0


def detect_name_conflict(cores1: set[str], cores2: set[str]) -> tuple[bool, set[str]]:
    """Detect soft entity divergence where shared core is small and distinct tokens differ."""
    if not cores1 or not cores2:
        return False, set()
    shared = cores1 & cores2
    diff = cores1 ^ cores2
    # If there are differing tokens and very few shared tokens, note the divergence
    if len(shared) <= 1 and len(diff) >= 2:
        return True, diff
    return False, set()


def extract_amount_with_fallback(work: dict[str, Any]) -> tuple[float | None, str]:
    """Sanction amount is primary; falls back to recommended amount if missing or zero."""
    for field_name in ("sanction_amount", "recommended_amount"):
        raw = work.get(field_name)
        if raw is not None and str(raw).strip():
            try:
                amt = float(raw)
                if amt > 0:
                    return amt, field_name
            except (ValueError, TypeError):
                continue
    return None, "none"


def extract_date_with_fallback(work: dict[str, Any]) -> tuple[date | None, str]:
    """Sanction date is primary; falls back to recommended date."""
    for field_name in ("sanction_date", "recommended_date"):
        raw = (work.get(field_name) or "").strip()
        if len(raw) >= 10:
            try:
                dt = date.fromisoformat(raw[:10])
                return dt, field_name
            except ValueError:
                continue
    return None, "none"


def is_consecutive_serial(id1: str, id2: str) -> bool:
    """Checks if two Work IDs share the same prefix/MP/year and have adjacent serial numbers."""
    try:
        parts1 = (id1 or "").strip().split("/")
        parts2 = (id2 or "").strip().split("/")
        if len(parts1) == 4 and len(parts2) == 4:
            if parts1[:3] == parts2[:3]:
                s1, s2 = int(parts1[3]), int(parts2[3])
                return abs(s1 - s2) == 1
    except (ValueError, TypeError):
        pass
    return False


@dataclass(slots=True)
class ScoredPair:
    pair_id: str
    work_id_a: str
    work_id_b: str
    cluster_id: str
    state: str
    constituency: str
    mp_name: str
    work_category: str
    description_a: str
    description_b: str
    sanction_amount_a: float | None
    sanction_amount_b: float | None
    amount_difference_pct: float | None
    sanction_date_a: str | None
    sanction_date_b: str | None
    date_gap_days: int | None
    text_similarity: float
    shared_entity_tokens: list[str]
    duplicate_risk_score: float
    review_priority: str
    is_batch_scheme: int
    batch_frequency: int
    consecutive_serials: int
    reasons: list[str]
    explanation_text: str
    is_high_edge: int = 0


def reconstruct_reasons(
    text_similarity: float,
    amount_difference_pct: float | None = None,
    amount_val: float | None = None,
    date_gap_days: int | None = None,
    date_val: str | None = None,
    same_constituency: bool = True,
    work_category: str | None = None,
    consecutive_serials: bool = False,
    shared_tokens_count: int = 0,
    numeric_conflict_reason: str = "",
    name_conflict_tokens: list[str] | None = None,
) -> list[str]:
    """Dynamically reconstructs explainable bullet points from pair features."""
    reasons: list[str] = []

    # 1. Text similarity
    if text_similarity >= 0.999:
        reasons.append("Normalized descriptions are identical")
    elif text_similarity >= 0.85:
        reasons.append(f"High text similarity ({text_similarity:.2f})")
    elif text_similarity >= 0.65:
        reasons.append(f"Moderate text similarity ({text_similarity:.2f})")

    # 2. Shared locality/entity tokens
    if shared_tokens_count > 0:
        reasons.append(f"{shared_tokens_count} shared locality/entity token(s)")

    # 3. Numeric conflict
    if numeric_conflict_reason:
        reasons.append(numeric_conflict_reason)

    # 4. Soft name/entity divergence
    if name_conflict_tokens:
        reasons.append(f"Differing entity/beneficiary reference ({', '.join(sorted(name_conflict_tokens)[:3])})")

    # 5. Financial amount
    if amount_difference_pct is not None:
        if amount_difference_pct == 0.0:
            if amount_val is not None and amount_val > 0:
                reasons.append(f"Identical financial amount (₹{amount_val:,.0f})")
            else:
                reasons.append("Identical financial amount")
        elif amount_difference_pct <= 0.05:
            reasons.append(f"Amounts differ by {amount_difference_pct * 100:.1f}%")
        elif amount_difference_pct <= 0.15:
            reasons.append(f"Amounts differ by {amount_difference_pct * 100:.1f}%")

    # 6. Milestone date
    if date_gap_days is not None:
        if date_gap_days == 0:
            if date_val:
                reasons.append(f"Identical milestone dates ({date_val})")
            else:
                reasons.append("Identical milestone dates")
        elif date_gap_days <= 90:
            reasons.append(f"Milestone dates are {date_gap_days} days apart")

    # 7. Administrative
    if same_constituency:
        reasons.append("Same parliamentary constituency")
    if work_category:
        reasons.append(f"Same work category ({work_category})")

    # 8. Consecutive serials
    if consecutive_serials:
        reasons.append("Consecutive portal entry serial numbers")

    return reasons


def format_explanation_text(reasons: list[str]) -> str:
    """Formats explainable reasons into a neutral human-readable explanation with mandatory disclaimer."""
    reasons_summary = "; ".join(reasons)
    return (
        f"Review recommended because {reasons_summary.lower()}. "
        f"{DISCLAIMER_TEXT}"
    )


def score_candidate_pair(
    w1: dict[str, Any],
    w2: dict[str, Any],
    d1_norm: str,
    d2_norm: str,
    tg1: frozenset[str],
    tg2: frozenset[str],
    core1: set[str],
    core2: set[str],
    num1: set[str],
    num2: set[str],
    const_template_freq: int,
    national_template_freq: int,
) -> ScoredPair | None:
    """Scores a candidate pair using the approved v2 explainable point framework."""
    # 1. Text Similarity
    exact_match = (d1_norm == d2_norm and len(d1_norm) > 0)
    tg_sim = 1.0 if exact_match else trigram_dice(tg1, tg2)

    # Fast pruning: pairs below 0.65 text similarity cannot meet review threshold
    if tg_sim < 0.65 and not exact_match:
        return None

    raw_score = 0.0
    reasons: list[str] = []

    if exact_match:
        raw_score += 45.0
        reasons.append("Normalized descriptions are identical")
    elif tg_sim >= 0.85:
        # Linear interpolation between 35 and 40 pts
        pts = 35.0 + ((tg_sim - 0.85) / 0.15) * 5.0
        raw_score += pts
        reasons.append(f"High text similarity ({tg_sim:.2f})")
    else:
        # Linear interpolation between 20 and 34 pts
        pts = 20.0 + ((tg_sim - 0.65) / 0.20) * 14.0
        raw_score += pts
        reasons.append(f"Moderate text similarity ({tg_sim:.2f})")

    # 2. Shared Core Tokens
    shared_core = core1 & core2
    if shared_core:
        pts = min(15.0, len(shared_core) * 4.0)
        raw_score += pts
        reasons.append(f"{len(shared_core)} shared locality/entity token(s)")

    # 3. Contextual Numeric Conflict Penalty (-30 pts)
    has_num_conflict, num_conflict_reason = detect_contextual_numeric_conflict(
        d1_norm, d2_norm, num1, num2
    )
    if has_num_conflict:
        raw_score -= 30.0
        reasons.append(num_conflict_reason)

    # 4. Soft Name/Entity Divergence Penalty (-10 pts)
    has_name_conflict, diff_names = detect_name_conflict(core1, core2)
    if has_name_conflict and not exact_match:
        raw_score -= 10.0
        reasons.append(f"Differing entity/beneficiary reference ({', '.join(sorted(diff_names)[:3])})")

    # 5. Financial Amount Proximity (Sanction primary, Recommended fallback)
    amt1, amt_src1 = extract_amount_with_fallback(w1)
    amt2, amt_src2 = extract_amount_with_fallback(w2)
    amt_diff_pct: float | None = None

    if amt1 is not None and amt2 is not None and max(amt1, amt2) > 0:
        amt_diff_pct = abs(amt1 - amt2) / max(amt1, amt2)
        if amt1 == amt2:
            raw_score += 20.0
            reasons.append(f"Identical financial amount (₹{amt1:,.0f})")
        elif amt_diff_pct <= 0.05:
            raw_score += 12.0
            reasons.append(f"Amounts differ by {amt_diff_pct * 100:.1f}%")
        elif amt_diff_pct <= 0.15:
            raw_score += 5.0
            reasons.append(f"Amounts differ by {amt_diff_pct * 100:.1f}%")

    # 6. Milestone Temporal Proximity (Sanction primary, Recommended fallback)
    dt1, dt_src1 = extract_date_with_fallback(w1)
    dt2, dt_src2 = extract_date_with_fallback(w2)
    days_gap: int | None = None

    if dt1 is not None and dt2 is not None:
        days_gap = abs((dt1 - dt2).days)
        if days_gap == 0:
            raw_score += 15.0
            reasons.append(f"Identical milestone dates ({dt1.isoformat()})")
        elif days_gap <= 30:
            raw_score += 10.0
            reasons.append(f"Milestone dates are {days_gap} days apart")
        elif days_gap <= 90:
            raw_score += 5.0
            reasons.append(f"Milestone dates are {days_gap} days apart")

    # 7. Administrative Context (Constituency + Category ONLY per specification)
    st1, co1 = (w1.get("state") or "").strip(), (w1.get("constituency") or "").strip()
    st2, co2 = (w2.get("state") or "").strip(), (w2.get("constituency") or "").strip()
    cat1 = (w1.get("work_category") or "").strip()
    cat2 = (w2.get("work_category") or "").strip()

    if co1 and co1 == co2:
        raw_score += 5.0
        reasons.append("Same parliamentary constituency")
    if cat1 and cat1 == cat2:
        raw_score += 5.0
        reasons.append(f"Same work category ({cat1})")

    # 8. Consecutive Portal Serials (Weak supporting signal: +3 pts)
    id1 = (w1.get("work_id") or "").strip()
    id2 = (w2.get("work_id") or "").strip()
    is_consec = is_consecutive_serial(id1, id2)
    if is_consec:
        raw_score += 3.0
        reasons.append("Consecutive portal entry serial numbers")

    # Bounded Normalization: Guaranteed [0.0, 100.0]
    final_score = max(0.0, min(100.0, round(raw_score, 1)))

    # 9. Strict High-Confidence Conjunction Gate
    is_high = (
        final_score >= 80.0
        and (tg_sim >= 0.85 or exact_match)
        and (amt_diff_pct is not None and amt_diff_pct <= 0.05)
        and (days_gap is not None and days_gap <= 90)
        and not has_num_conflict
    )

    # 10. Dedicated Batch Scheme Classification (Independent of score)
    is_batch = 1 if (const_template_freq >= 20 or national_template_freq >= 40) else 0

    if is_batch:
        priority = "Batch Scheme Representative Link"
    elif is_high:
        priority = "High-Confidence Potential Duplicate"
    elif final_score >= 60.0 and not has_num_conflict:
        priority = "Medium-Confidence Potential Duplicate"
    elif final_score >= 40.0:
        priority = "Low-Confidence Similar Work"
    else:
        return None

    # Build Neutral Explanation
    explanation = format_explanation_text(reasons)

    # Deterministic pair ID: DUP-SERIAL1-SERIAL2 (ordered)
    s1_num = id1.split("/")[-1] if "/" in id1 else id1
    s2_num = id2.split("/")[-1] if "/" in id2 else id2
    pair_id = f"DUP-{min(s1_num, s2_num)}-{max(s1_num, s2_num)}"

    return ScoredPair(
        pair_id=pair_id,
        work_id_a=min(id1, id2),
        work_id_b=max(id1, id2),
        cluster_id="",  # Populated after clustering
        state=st1 or st2,
        constituency=co1 or co2,
        mp_name=(w1.get("mp_name") or w2.get("mp_name") or "").strip(),
        work_category=cat1 or cat2,
        description_a=(w1.get("work_description") or "").strip(),
        description_b=(w2.get("work_description") or "").strip(),
        sanction_amount_a=amt1,
        sanction_amount_b=amt2,
        amount_difference_pct=round(amt_diff_pct, 4) if amt_diff_pct is not None else None,
        sanction_date_a=dt1.isoformat() if dt1 else None,
        sanction_date_b=dt2.isoformat() if dt2 else None,
        date_gap_days=days_gap,
        text_similarity=round(tg_sim, 4),
        shared_entity_tokens=sorted(shared_core),
        duplicate_risk_score=final_score,
        review_priority=priority,
        is_batch_scheme=is_batch,
        batch_frequency=const_template_freq,
        consecutive_serials=1 if is_consec else 0,
        reasons=reasons,
        explanation_text=explanation,
        is_high_edge=1 if is_high else 0,
    )


def build_clusters_from_high_edges(
    high_pairs: list[ScoredPair],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Constructs connected components strictly from High-Confidence edges.

    Returns:
    - work_id_to_cluster_id mapping
    - list of cluster summary dictionaries
    """
    adj: dict[str, set[str]] = defaultdict(set)
    for p in high_pairs:
        adj[p.work_id_a].add(p.work_id_b)
        adj[p.work_id_b].add(p.work_id_a)

    visited: set[str] = set()
    clusters: list[list[str]] = []

    for node in sorted(adj.keys()):
        if node not in visited:
            comp: list[str] = []
            queue = [node]
            visited.add(node)
            for curr in queue:
                comp.append(curr)
                for nbr in sorted(adj[curr]):
                    if nbr not in visited:
                        visited.add(nbr)
                        queue.append(nbr)
            clusters.append(comp)

    # Sort clusters by size descending
    clusters.sort(key=lambda c: len(c), reverse=True)

    work_to_cluster: dict[str, str] = {}
    cluster_records: list[dict[str, Any]] = []

    for idx, comp in enumerate(clusters, start=1):
        cluster_id = f"CLUST-{idx:05d}"
        for w_id in comp:
            work_to_cluster[w_id] = cluster_id

        # Safeguard: if cluster size exceeds 15, flag as batch cluster
        is_batch_cluster = 1 if len(comp) > 15 else 0

        cluster_records.append({
            "cluster_id": cluster_id,
            "work_count": len(comp),
            "is_batch_scheme": is_batch_cluster,
            "work_ids": ";".join(comp),
        })

    return work_to_cluster, cluster_records
