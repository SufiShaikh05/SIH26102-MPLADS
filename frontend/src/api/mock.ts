import { ApiError } from './client'
import type {
  Anomaly,
  DuplicatePage,
  DuplicatePairRecord,
  DuplicateQuery,
  DuplicateSummary,
  Page,
  Query,
  StateBreakdown,
  Summary,
  TrendPoint,
  TrendResponse,
  WorkDuplicatesResponse,
} from './client'

let seed = 7
const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647
const pick = <T,>(a: T[]) => a[Math.floor(rnd() * a.length)]
const STATES = ['Uttar Pradesh', 'Maharashtra', 'Madhya Pradesh', 'Rajasthan', 'Bihar', 'Tamil Nadu', 'Karnataka', 'Gujarat', 'West Bengal', 'Odisha']
const CATS = ['Roads and Bridges', 'Drinking Water', 'Education', 'Health', 'Sanitation', 'Community Buildings', 'Electricity', 'Sports']
const STATUS = ['Physical Inspection', 'Sanction', 'Vendor Identification', 'Work partially Completed', 'Work Completed', 'Time Estimation']
const SIGNALS = [
  'High share of duplicate payment rows', 'Expenditure unusually high for the sanctioned amount',
  'Several vendors on a small sanction', 'Long gap since last payment', 'Payments recorded but status not completed', 'Completed status with very low expenditure',
]

const DATA: Anomaly[] = Array.from({ length: 120 }, (_, i) => {
  const state = pick(STATES), sanction = Math.round((5 + rnd() * 95) * 1e5), util = rnd() * 1.2
  const dup = rnd() < 0.35 ? rnd() * 0.5 : rnd() * 0.05, score = Math.round(20 + rnd() * 79)
  const n = score >= 70 ? 3 : score >= 40 ? 2 : 1, sig = [0, 1, 2].map(k => SIGNALS[(i + k * 2) % SIGNALS.length]).slice(0, n)
  return {
    work_id: `MOCK-${100000 + i * 37}`, state, constituency: `${state} Constituency ${(i % 9) + 1}`, mp_name: `Sample MP ${(i % 20) + 1}`,
    ida: 'District Authority (sample)', work_category: pick(CATS), work_status: pick(STATUS), sanction_amount: sanction,
    total_disbursed_all_rows: Math.round(sanction * util * (1 + dup)), deduplicated_disbursed_amount: Math.round(sanction * util),
    success_utilization_ratio: util, days_since_sanction: 200 + Math.floor(rnd() * 1800), days_since_last_payment: Math.floor(rnd() * 900),
    payment_count: 1 + Math.floor(rnd() * 8), vendor_count: 1 + Math.floor(rnd() * 4), duplicate_ratio: dup,
    review_priority_score: score, review_priority_label: score >= 70 ? 'High Review Priority' : score >= 50 ? 'Medium Review Priority' : score >= 35 ? 'Low Review Priority' : 'Normal Monitoring', signal_count: n,
    top_signal_1: sig[0] ?? null, top_signal_2: sig[1] ?? null, top_signal_3: sig[2] ?? null,
    explanation_text: `This work shows ${n} indicator(s): ${sig.join('; ').toLowerCase()}. The combination differs from comparable works and may warrant verification.`,
  }
})

const wait = <T,>(v: T) => new Promise<T>(r => setTimeout(() => r(v), 250))

export const summary = (): Promise<Summary> => {
  const dist: Record<string, number> = {}
  DATA.forEach(d => { dist[d.review_priority_label] = (dist[d.review_priority_label] ?? 0) + 1 })
  return wait({
    total_works: 81335, works_with_expenditure: 57700, completed_works: 35475, review_candidates: DATA.length,
    review_priority_label_counts: Object.entries(dist).map(([label, count]) => ({ label, count })),
  })
}

// Mirrors the real /api/v1/states shape: { state, work_count, review_candidate_count }.
export const stateBreakdown = (): Promise<StateBreakdown[]> => {
  const by = new Map<string, StateBreakdown>()
  DATA.forEach(d => {
    const s = by.get(d.state) ?? { state: d.state, work_count: 0, review_candidate_count: 0 }
    s.work_count += 800 + d.payment_count * 40; s.review_candidate_count += 1; by.set(d.state, s)
  })
  return wait([...by.values()])
}

export const anomalies = (q: Query): Promise<Page<Anomaly>> => {
  let rows = DATA.filter(d =>
    (!q.label || d.review_priority_label.toLowerCase() === q.label.toLowerCase()) && (!q.state || d.state === q.state) &&
    (!q.work_category || d.work_category === q.work_category) && (q.min_score === undefined || d.review_priority_score >= q.min_score) &&
    (q.max_score === undefined || d.review_priority_score <= q.max_score))
  const desc = q.sort?.startsWith('-'), f = (q.sort ?? '-review_priority_score').replace(/^-/, '') as keyof Anomaly
  rows = [...rows].sort((a, b) => (Number(a[f]) - Number(b[f])) * (desc || !q.sort ? -1 : 1))
  return wait({ items: rows.slice((q.page - 1) * q.page_size, q.page * q.page_size), total: rows.length, page: q.page, page_size: q.page_size })
}
export const anomaly = (id: string): Promise<Anomaly> => {
  const d = DATA.find(x => x.work_id.toLowerCase() === id.toLowerCase())
  return d ? wait(d) : Promise.reject(new ApiError(`No record found for work ${id}.`, 404))
}
export const work = (id: string) => anomaly(id)
export const states = () => wait(STATES)
export const categories = () => wait(CATS)

const PERIODS = [
  '2024-07', '2024-08', '2024-09', '2024-10', '2024-11', '2024-12',
  '2025-01', '2025-02', '2025-03', '2025-04', '2025-05', '2025-06',
  '2025-07', '2025-08', '2025-09', '2025-10', '2025-11', '2025-12',
  '2026-01', '2026-02', '2026-03', '2026-04', '2026-05', '2026-06',
  '2026-07', '2026-08', '2026-09',
]

export const trends = (state?: string): Promise<TrendResponse> => {
  if (state && !STATES.includes(state)) {
    return wait({
      granularity: 'month',
      start_period: null,
      end_period: null,
      state,
      series: [],
    })
  }

  const scale = state ? 0.08 : 1.0
  const series: TrendPoint[] = PERIODS.map((period, idx) => {
    const rec = Math.round((1000 + idx * 150) * scale)
    const san = Math.round((800 + idx * 170) * scale)
    const com = Math.round((300 + idx * 120) * scale)
    const tx = Math.round((900 + idx * 200) * scale)
    const succAmt = Math.round((2.8e8 + idx * 7.5e7) * scale)
    const inProgAmt = Math.round((2e7 + idx * 5e6) * scale)
    const expAmt = succAmt + inProgAmt

    return {
      period,
      recommended_works: rec,
      sanctioned_works: san,
      completed_works: com,
      expenditure_transactions: tx,
      expenditure_amount: expAmt,
      payment_success_amount: succAmt,
      payment_in_progress_amount: inProgAmt,
    }
  })

  return wait({
    granularity: 'month',
    start_period: PERIODS[0],
    end_period: PERIODS[PERIODS.length - 1],
    state: state ?? null,
    series,
  })
}

// --------------------------------------------------------------------------- duplicates mock
const MOCK_DUPLICATE_PAIRS: DuplicatePairRecord[] = [
  {
    pair_id: 'DUP-151021-151022',
    work_id_a: 'WS/MP013/2024-2025/151021',
    work_id_b: 'WS/MP013/2024-2025/151022',
    cluster_id: 'CLU-151021',
    state: 'Sikkim',
    constituency: 'Sikkim Parliamentary Constituency',
    mp_name: 'Indra Hang Subba',
    work_category: 'Roads and Bridges',
    description_a: 'Construction of CC Footpath from Main Road to GPU Boundary at Ward No 3',
    description_b: 'Construction of CC Footpath from Main Road to GPU Boundary at Ward No 3',
    sanction_amount_a: 3000000,
    sanction_amount_b: 3000000,
    amount_difference_pct: 0.0,
    sanction_date_a: '2025-02-27',
    sanction_date_b: '2025-02-27',
    date_gap_days: 0,
    text_similarity: 1.0,
    shared_entity_tokens: ['boundary', 'cc', 'construction', 'footpath', 'gpu', 'main', 'road', 'ward'],
    duplicate_risk_score: 100.0,
    review_priority: 'High-Confidence Potential Duplicate',
    is_batch_scheme: false,
    batch_frequency: 0,
    consecutive_serials: true,
    reasons: [
      'Normalized descriptions are identical',
      '13 shared locality/entity token(s)',
      'Identical financial amount (₹3,000,000)',
      'Identical milestone dates (2025-02-27)',
      'Same parliamentary constituency (Sikkim)',
      'Same work category',
      'Consecutive portal entry serial numbers (151021 and 151022)',
    ],
    explanation_text:
      'Potential Duplicate Risk Score 100/100 (High-Confidence Potential Duplicate). High text similarity (100.0%) in Sikkim. Exact matching sanction amounts of ₹30.00 L with 0 days milestone date difference. Consecutive portal serial numbers.',
  },
  {
    pair_id: 'DUP-184521-184522',
    work_id_a: 'WS/MP042/2024-2025/184521',
    work_id_b: 'WS/MP042/2024-2025/184522',
    cluster_id: 'CLU-184521',
    state: 'Uttar Pradesh',
    constituency: 'Varanasi',
    mp_name: 'Sample MP Varanasi',
    work_category: 'Drinking Water',
    description_a: 'Installation of Submersible Handpump near Primary School Mahmoorganj',
    description_b: 'Installation of Submersible Handpump near Primary School Mahmoorganj Ward 12',
    sanction_amount_a: 125000,
    sanction_amount_b: 125000,
    amount_difference_pct: 0.0,
    sanction_date_a: '2024-08-10',
    sanction_date_b: '2024-08-15',
    date_gap_days: 5,
    text_similarity: 0.885,
    shared_entity_tokens: ['handpump', 'installation', 'mahmoorganj', 'primary', 'school', 'submersible'],
    duplicate_risk_score: 95.0,
    review_priority: 'High-Confidence Potential Duplicate',
    is_batch_scheme: false,
    batch_frequency: 0,
    consecutive_serials: true,
    reasons: [
      'High text similarity (88.5%)',
      'Identical sanction amount (₹1.25 L)',
      'Close sanction dates (5 days difference)',
      'Consecutive sanction serial numbers (184521 and 184522)',
      'Same parliamentary constituency (Varanasi)',
      'Same work category (Drinking Water)',
      '6 shared entity/location keywords',
    ],
    explanation_text:
      'Potential Duplicate Risk Score 95/100 (High-Confidence Potential Duplicate). High text similarity (88.5%) in Drinking Water in Uttar Pradesh. Identical sanction amounts (₹1.25 L) with 5 days gap.',
  },
  {
    pair_id: 'DUP-203112-203113',
    work_id_a: 'WS/MP088/2024-2025/203112',
    work_id_b: 'WS/MP088/2024-2025/203113',
    cluster_id: null,
    state: 'Maharashtra',
    constituency: 'Pune',
    mp_name: 'Sample MP Pune',
    work_category: 'Community Buildings',
    description_a: 'Construction of Community Hall at Kothrud Sector 4 Pune',
    description_b: 'Construction of Community Hall and Shed at Kothrud Sector 4 Pune District',
    sanction_amount_a: 1500000,
    sanction_amount_b: 1520000,
    amount_difference_pct: 0.0133,
    sanction_date_a: '2024-09-01',
    sanction_date_b: '2024-09-22',
    date_gap_days: 21,
    text_similarity: 0.8498,
    shared_entity_tokens: ['community', 'construction', 'hall', 'kothrud', 'pune', 'sector'],
    duplicate_risk_score: 92.0,
    review_priority: 'Medium-Confidence Potential Duplicate',
    is_batch_scheme: false,
    batch_frequency: 0,
    consecutive_serials: true,
    reasons: [
      'Substantial text similarity (84.98% - below 85.0% High-Confidence gate)',
      'Nearly identical sanction amount (1.33% difference)',
      'Close sanction dates (21 days difference)',
      'Consecutive sanction serial numbers (203112 and 203113)',
      'Same parliamentary constituency (Pune)',
      'Same work category (Community Buildings)',
      '6 shared entity/location keywords',
    ],
    explanation_text:
      'Potential Duplicate Risk Score 92/100 (Medium-Confidence Potential Duplicate). Text similarity of 84.98% sits just below the strict 85.0% High-Confidence threshold. Amounts differ by 1.33% with 21 days gap.',
  },
  {
    pair_id: 'DUP-BATCH-300101-300102',
    work_id_a: 'WS/MP015/2024-2025/300101',
    work_id_b: 'WS/MP015/2024-2025/300102',
    cluster_id: 'CLU-BATCH-300101',
    state: 'Rajasthan',
    constituency: 'Jaipur',
    mp_name: 'Sample MP Jaipur',
    work_category: 'Electricity',
    description_a: 'Installation of Solar Street Light under Ward Electrification Scheme Block A',
    description_b: 'Installation of Solar Street Light under Ward Electrification Scheme Block B',
    sanction_amount_a: 45000,
    sanction_amount_b: 45000,
    amount_difference_pct: 0.0,
    sanction_date_a: '2024-06-15',
    sanction_date_b: '2024-06-15',
    date_gap_days: 0,
    text_similarity: 0.942,
    shared_entity_tokens: ['electrification', 'installation', 'light', 'scheme', 'solar', 'street', 'ward'],
    duplicate_risk_score: 88.0,
    review_priority: 'Batch Scheme Representative Link',
    is_batch_scheme: true,
    batch_frequency: 48,
    consecutive_serials: true,
    reasons: [
      'Representative link for repeated batch template scheme (48 works in template cluster)',
      'Identical sanction amount (₹45,000)',
      'Same sanction date (0 days difference)',
      'Template text similarity (94.2%)',
      'Isolated from ordinary review queue to prevent flooding',
    ],
    explanation_text:
      'Representative link for batch template scheme (48 items identified). Programmatic installation of identical items across decentralized wards/locations.',
  },
]

export const duplicateSummary = (): Promise<DuplicateSummary> => {
  return wait({
    total_duplicate_pairs: 50197,
    high_confidence_pairs: 12539,
    medium_confidence_pairs: 37658,
    batch_scheme_pairs: 4195,
    total_clusters: 3128,
    affected_works_count: 30850,
  })
}

export const duplicates = (q: DuplicateQuery = {}): Promise<DuplicatePage> => {
  let rows = [...MOCK_DUPLICATE_PAIRS]

  // Batch isolation
  if (q.is_batch === true) {
    rows = rows.filter(r => r.is_batch_scheme)
  } else if (q.is_batch === false || q.is_batch === undefined) {
    if (q.priority && q.priority.toLowerCase().includes('batch')) {
      rows = rows.filter(r => r.is_batch_scheme)
    } else {
      rows = rows.filter(r => !r.is_batch_scheme)
    }
  }

  // Priority filter
  if (q.priority && !q.priority.toLowerCase().includes('batch')) {
    const prio = q.priority.toLowerCase()
    rows = rows.filter(r => r.review_priority.toLowerCase().includes(prio))
  }

  // State filter
  if (q.state && q.state.trim()) {
    rows = rows.filter(r => r.state?.toLowerCase() === q.state!.trim().toLowerCase())
  }

  // Constituency filter
  if (q.constituency && q.constituency.trim()) {
    rows = rows.filter(r => r.constituency?.toLowerCase() === q.constituency!.trim().toLowerCase())
  }

  // Min score filter
  if (q.min_score !== undefined && !isNaN(q.min_score)) {
    rows = rows.filter(r => r.duplicate_risk_score >= q.min_score!)
  }

  // Sorting
  if (q.sort_by === 'score_asc') {
    rows.sort((a, b) => a.duplicate_risk_score - b.duplicate_risk_score)
  } else if (q.sort_by === 'amount_desc') {
    rows.sort((a, b) => Math.max(b.sanction_amount_a ?? 0, b.sanction_amount_b ?? 0) - Math.max(a.sanction_amount_a ?? 0, a.sanction_amount_b ?? 0))
  } else if (q.sort_by === 'date_gap_asc') {
    rows.sort((a, b) => (a.date_gap_days ?? 9999) - (b.date_gap_days ?? 9999))
  } else {
    // Default score_desc
    rows.sort((a, b) => b.duplicate_risk_score - a.duplicate_risk_score)
  }

  const page = q.page || 1
  const pageSize = q.page_size || 25
  const start = (page - 1) * pageSize
  const items = rows.slice(start, start + pageSize)

  return wait({
    items,
    total: rows.length,
    page,
    page_size: pageSize,
  })
}

export const workDuplicates = (work_id: string): Promise<WorkDuplicatesResponse> => {
  const normId = work_id.trim().toUpperCase()
  const matched = MOCK_DUPLICATE_PAIRS.filter(
    p => p.work_id_a.toUpperCase() === normId || p.work_id_b.toUpperCase() === normId,
  )
  const clusterId = matched.length > 0 ? (matched[0].cluster_id ?? null) : null
  const clusterWorks = matched.length > 0 ? Array.from(new Set(matched.flatMap(m => [m.work_id_a, m.work_id_b]))) : []

  return wait({
    work_id,
    has_duplicates: matched.length > 0,
    duplicate_pairs: matched,
    cluster_id: clusterId,
    cluster_work_ids: clusterWorks,
  })
}
