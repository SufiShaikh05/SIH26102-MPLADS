// DEVELOPMENT FALLBACK ONLY. Loaded when VITE_USE_MOCK=true. Matches the API contract; all values are synthetic.
import { ApiError } from './client'
import type { Anomaly, Page, Query, StateBreakdown, Summary } from './client'

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
