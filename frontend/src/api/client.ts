// Single place for all backend access. Mock data (./mock) is loaded ONLY when VITE_USE_MOCK=true.
export interface Anomaly {
  work_id: string; state: string; constituency: string; mp_name: string; ida: string
  work_category: string; work_status: string; sanction_amount: number
  total_disbursed_all_rows: number; deduplicated_disbursed_amount: number
  success_utilization_ratio: number; days_since_sanction: number; days_since_last_payment: number
  payment_count: number; vendor_count: number; duplicate_ratio: number
  review_priority_score: number; review_priority_label: string; signal_count: number
  top_signal_1: string | null; top_signal_2: string | null; top_signal_3: string | null
  explanation_text: string
  [extra: string]: unknown
}
export interface PriorityLabelCount { label: string; count: number }
export interface Summary {
  total_works: number; works_with_expenditure: number; completed_works: number; review_candidates: number
  // GET /api/v1/summary returns this as an ARRAY of { label, count } objects, not a { label: count } map.
  review_priority_label_counts?: PriorityLabelCount[]
}
export interface StateBreakdown { state: string; work_count: number; review_candidate_count: number }
export interface TrendPoint {
  period: string
  recommended_works: number
  sanctioned_works: number
  completed_works: number
  expenditure_transactions: number
  expenditure_amount: number
  payment_success_amount: number
  payment_in_progress_amount: number
}
export interface TrendResponse {
  granularity: 'month'
  start_period: string | null
  end_period: string | null
  state: string | null
  series: TrendPoint[]
}
export interface DuplicatePairRecord {
  pair_id: string
  work_id_a: string
  work_id_b: string
  cluster_id: string | null
  state: string | null
  constituency: string | null
  mp_name: string | null
  work_category: string | null
  description_a: string | null
  description_b: string | null
  sanction_amount_a: number | null
  sanction_amount_b: number | null
  amount_difference_pct: number | null
  sanction_date_a: string | null
  sanction_date_b: string | null
  date_gap_days: number | null
  text_similarity: number
  shared_entity_tokens: string[]
  duplicate_risk_score: number
  review_priority: string
  is_batch_scheme: boolean
  batch_frequency: number
  consecutive_serials: boolean
  reasons: string[]
  explanation_text: string
}

export interface DuplicateClusterRecord {
  cluster_id: string
  work_count: number
  is_batch_scheme: boolean
  work_ids: string[]
}

export interface DuplicateSummary {
  total_duplicate_pairs: number
  high_confidence_pairs: number
  medium_confidence_pairs: number
  batch_scheme_pairs: number
  total_clusters: number
  affected_works_count: number
}

export interface DuplicatePage {
  items: DuplicatePairRecord[]
  total: number
  page: number
  page_size: number
}

export interface WorkDuplicatesResponse {
  work_id: string
  has_duplicates: boolean
  duplicate_pairs: DuplicatePairRecord[]
  cluster_id: string | null
  cluster_work_ids: string[]
}

export interface DuplicateQuery {
  page?: number
  page_size?: number
  state?: string
  constituency?: string
  priority?: string
  is_batch?: boolean
  min_score?: number
  sort_by?: 'score_desc' | 'score_asc' | 'amount_desc' | 'date_gap_asc' | string
}

export interface Page<T> { items: T[]; total: number; page: number; page_size: number }
export interface Query {
  page: number; page_size: number; label?: string; state?: string
  work_category?: string; min_score?: number; max_score?: number; sort?: string
}

// --------------------------------------------------------------------------- compliance
export interface UniqueCounts {
  total_unique_flagged_works: number
  unique_policy_affected_works: number
  policy_derived_reconciliation_works: number
  unique_execution_affected_works: number
  unflagged_baseline_works: number
  total_works_evaluated: number
}

export interface TriggerDensity {
  total_trigger_instances: number
  average_triggers_per_flagged_work: number
}

export interface FinancialReconciliationOutlay {
  total_sanctioned_outlay_all_works_cr: number
  total_deduplicated_disbursed_outlay_all_works_cr: number
  flagged_works_sanctioned_outlay_cr: number
  flagged_works_deduplicated_disbursed_outlay_cr: number
  unflagged_works_sanctioned_outlay_cr: number
  unflagged_works_deduplicated_disbursed_outlay_cr: number
  policy_affected_sanctioned_outlay_cr: number
  policy_affected_deduplicated_disbursed_outlay_cr: number
  execution_affected_sanctioned_outlay_cr: number
  execution_affected_deduplicated_disbursed_outlay_cr: number
  post_completion_disbursed_outlay_cr: number
}

export interface RuleSummaryStat {
  rule_id: string
  rule_name: string
  authority_type: string
  classification: string
  source_reference: string
  threshold: string
  unique_works_count: number
  percentage_of_all_works: number
  sanctioned_outlay_cr: number
  disbursed_outlay_cr: number
  description: string
  limitation: string
  recommended_action: string
}

export interface ComplianceSummary {
  snapshot_date: string
  policy_baseline: string
  total_works_evaluated: number
  unique_counts: UniqueCounts
  trigger_density: TriggerDensity
  financial_outlay: FinancialReconciliationOutlay
  rule_breakdown: Record<string, RuleSummaryStat>
}

export interface BaseDocumentMeta {
  document_id: string
  title: string
  issuing_ministry: string
  publication_year: number
  applicable_period: string
  source_document_url: string
  portal_url: string
}

export interface PolicyProvisionMeta {
  provision_id: string
  clause_reference: string
  heading: string
  mandate_summary: string
  authority_type: string
  official_monitoring_proxy: boolean
  source_document_url: string
}

export interface OfficialMonitoringBenchmarkMeta {
  benchmark_id: string
  title: string
  authority_type: string
  source_url: string
  description: string
  official_indicator_name: string
}

export interface PolicyRegistryMeta {
  base_document: BaseDocumentMeta
  provisions: PolicyProvisionMeta[]
  official_monitoring_benchmarks: OfficialMonitoringBenchmarkMeta[]
}

export interface ComplianceRulesResponse {
  registry: PolicyRegistryMeta
  rules: RuleSummaryStat[]
}

export interface RuleEvaluation {
  rule_id: string
  rule_name: string
  authority_type: string
  classification: string
  source_reference: string
  is_triggered: boolean
  observed_value: string
  threshold: string
  reason: string
  limitation: string
  recommended_action: string
}

export interface WorkComplianceRecord {
  work_id: string
  work_description: string
  state: string
  constituency: string
  ida: string
  mp_name: string
  work_category: string
  work_status: string
  is_completed: boolean
  sanction_amount: number
  deduplicated_disbursed_amount: number
  utilization_ratio: number
  recommended_date: string | null
  sanction_date: string | null
  completion_date: string | null
  last_expenditure_date: string | null
  days_since_sanction: number | null
  days_since_last_payment: number | null
  triggered_rule_ids: string[]
  evaluations: RuleEvaluation[]
}

export interface ComplianceQueuePage {
  items: WorkComplianceRecord[]
  total_items: number
  page: number
  limit: number
  total_pages: number
}

export interface ComplianceQueueQuery {
  page?: number
  limit?: number
  rule_id?: string
  authority_type?: string
  classification?: string
  state?: string
  work_category?: string
  search?: string
}

export const isMock = import.meta.env.VITE_USE_MOCK === 'true'
export const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/$/, '')


export class ApiError extends Error {
  constructor(message: string, public status?: number) { super(message) }
}

async function get<T>(path: string, params: object = {}): Promise<T> {
  const q = new URLSearchParams()
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== '') q.set(k, String(v)) })
  const qs = q.toString()
  let res: Response
  try { res = await fetch(`${API_BASE}${path}${qs ? `?${qs}` : ''}`) }
  catch { throw new ApiError(`Cannot reach the API at ${API_BASE}. Check that the backend is running.`) }
  if (!res.ok) throw new ApiError(`The API returned status ${res.status} for ${path}.`, res.status)
  return res.json() as Promise<T>
}

// Assumed sort format: "field" ascending, "-field" descending. Change here if the backend differs.
export const sortParam = (field: string, dir: 'asc' | 'desc') => (dir === 'desc' ? `-${field}` : field)

const toList = (raw: unknown): string[] => {
  const arr = Array.isArray(raw) ? raw : ((raw as Record<string, unknown>)?.items ?? (raw as Record<string, unknown>)?.states ?? (raw as Record<string, unknown>)?.work_categories ?? [])
  return (arr as unknown[]).map(x => typeof x === 'string' ? x : String((x as Record<string, unknown>).name ?? (x as Record<string, unknown>).state ?? (x as Record<string, unknown>).work_category ?? '')).filter(Boolean)
}
// GET /api/v1/states returns { items: [{ state, work_count, review_candidate_count }] }.
const toStateBreakdown = (raw: unknown): StateBreakdown[] => {
  const arr = Array.isArray(raw) ? raw : ((raw as Record<string, unknown>)?.items ?? [])
  return (arr as Record<string, unknown>[])
    .map(x => ({ state: String(x.state ?? ''), work_count: Number(x.work_count ?? 0), review_candidate_count: Number(x.review_candidate_count ?? 0) }))
    .filter(x => x.state)
}
const toPage = (raw: Record<string, unknown>, q: Query): Page<Anomaly> => ({
  items: (raw.items ?? raw.results ?? raw.data ?? []) as Anomaly[],
  total: Number(raw.total ?? raw.total_count ?? 0), page: Number(raw.page ?? q.page), page_size: Number(raw.page_size ?? q.page_size),
})

const mock = () => import('./mock')

export const api = {
  summary: async (): Promise<Summary> => isMock ? (await mock()).summary() : get<Summary>('/api/v1/summary'),
  anomalies: async (q: Query): Promise<Page<Anomaly>> =>
    isMock ? (await mock()).anomalies(q) : toPage(await get<Record<string, unknown>>('/api/v1/anomalies', q), q),
  anomaly: async (id: string): Promise<Anomaly> =>
    isMock ? (await mock()).anomaly(id) : get<Anomaly>(`/api/v1/anomalies/${encodeURIComponent(id)}`),
  work: async (id: string): Promise<Partial<Anomaly>> =>
    isMock ? (await mock()).work(id) : get<Partial<Anomaly>>(`/api/v1/works/${encodeURIComponent(id)}`),
  states: async (): Promise<string[]> => isMock ? (await mock()).states() : toList(await get('/api/v1/states')),
  categories: async (): Promise<string[]> => isMock ? (await mock()).categories() : toList(await get('/api/v1/work-categories')),
  // Same /api/v1/states response as `states`, kept as the full { state, work_count, review_candidate_count } records for the chart.
  stateBreakdown: async (): Promise<StateBreakdown[]> => isMock ? (await mock()).stateBreakdown() : toStateBreakdown(await get('/api/v1/states')),
  trends: async (state?: string): Promise<TrendResponse> => {
    const params: Record<string, string> = {}
    if (state && state.trim()) params.state = state.trim()
    return isMock ? (await mock()).trends(params.state) : get<TrendResponse>('/api/v1/trends', params)
  },
  duplicateSummary: async (): Promise<DuplicateSummary> =>
    isMock ? (await mock()).duplicateSummary() : get<DuplicateSummary>('/api/v1/duplicates/summary'),
  duplicates: async (q: DuplicateQuery = {}): Promise<DuplicatePage> =>
    isMock ? (await mock()).duplicates(q) : get<DuplicatePage>('/api/v1/duplicates', q),
  workDuplicates: async (work_id: string): Promise<WorkDuplicatesResponse> =>
    isMock ? (await mock()).workDuplicates(work_id) : get<WorkDuplicatesResponse>(`/api/v1/duplicates/${encodeURIComponent(work_id)}`),
  complianceSummary: async (): Promise<ComplianceSummary> =>
    get<ComplianceSummary>('/api/v1/compliance/summary'),
  complianceRules: async (): Promise<ComplianceRulesResponse> =>
    get<ComplianceRulesResponse>('/api/v1/compliance/rules'),
  complianceQueue: async (q: ComplianceQueueQuery = {}): Promise<ComplianceQueuePage> =>
    get<ComplianceQueuePage>('/api/v1/compliance/queue', q),
  workCompliance: async (work_id: string): Promise<WorkComplianceRecord> =>
    get<WorkComplianceRecord>(`/api/v1/compliance/${encodeURIComponent(work_id)}`),
}
