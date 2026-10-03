import { useEffect, useState, type FormEvent } from 'react'
import {
  api,
  type ComplianceQueueQuery,
  type ComplianceSummary,
  type RuleEvaluation,
  type RuleSummaryStat,
  type WorkComplianceRecord,
} from './api/client'
import { Card, Empty, ErrorBox, Loading, inr, num, pct, useAsync } from './ui'

const PAGE_SIZE = 25

export function AuthBadge({ authority, classification }: { authority: string; classification?: string }) {
  const normAuth = (authority || '').toUpperCase().trim()
  const normClass = (classification || '').toUpperCase().trim()

  if (normClass.includes('RECONCILIATION')) {
    return <span className="auth-badge auth-reconciliation" title="Financial/record reconciliation check">Policy Reconciliation</span>
  }
  if (normAuth === 'GUIDELINE_PROVISION' || normClass.includes('POLICY-DERIVED REVIEW TRIGGER')) {
    return <span className="auth-badge auth-guideline" title="Official provision from MPLADS Guidelines 2023">Guideline Provision</span>
  }
  if (normAuth === 'OPERATIONAL_PROXY' || normClass.includes('PROXY')) {
    return <span className="auth-badge auth-proxy" title="Dataset-derived proxy for guideline recommendation latency benchmark">Policy-Derived Proxy</span>
  }
  if (normAuth === 'OFFICIAL_MONITORING' || normClass.includes('OFFICIAL MONITORING')) {
    return <span className="auth-badge auth-monitoring" title="MoSPI official monitoring benchmark">Official Monitoring</span>
  }
  return <span className="auth-badge auth-heuristic" title="Operational execution heuristic">Execution Heuristic</span>
}

export function RuleTag({ ruleId }: { ruleId: string }) {
  let cls = 'rule-tag'
  if (ruleId.startsWith('COMP-04')) cls += ' reconciliation'
  else if (ruleId.startsWith('COMP')) cls += ' policy'
  else cls += ' triggered'
  return <span className={cls}>{ruleId}</span>
}

export default function ComplianceReview({ openWork }: { openWork: (id: string) => void }) {
  // Summary & Rules metadata
  const summaryAsync = useAsync(api.complianceSummary, [])
  const rulesAsync = useAsync(api.complianceRules, [])
  const statesAsync = useAsync(api.states, [])
  const catsAsync = useAsync(api.categories, [])

  // Review Queue Filters
  const [filterRule, setFilterRule] = useState('')
  const [filterAuthority, setFilterAuthority] = useState('')
  const [filterClassification, setFilterClassification] = useState('')
  const [filterState, setFilterState] = useState('')
  const [filterCategory, setFilterCategory] = useState('')
  const [search, setSearch] = useState('')
  const [activeSearch, setActiveSearch] = useState('')
  const [page, setPage] = useState(1)

  // Work Investigation Modal State
  const [selectedWorkId, setSelectedWorkId] = useState<string | null>(null)
  const workAsync = useAsync(
    () => (selectedWorkId ? api.workCompliance(selectedWorkId) : Promise.resolve(null)),
    [selectedWorkId]
  )

  const queueQuery: ComplianceQueueQuery = {
    page,
    limit: PAGE_SIZE,
    rule_id: filterRule || undefined,
    authority_type: filterAuthority || undefined,
    classification: filterClassification || undefined,
    state: filterState || undefined,
    work_category: filterCategory || undefined,
    search: activeSearch || undefined,
  }

  const queueAsync = useAsync(() => api.complianceQueue(queueQuery), [JSON.stringify(queueQuery)])

  const handleSearchSubmit = (e: FormEvent) => {
    e.preventDefault()
    setActiveSearch(search.trim())
    setPage(1)
  }

  const clearFilters = () => {
    setFilterRule('')
    setFilterAuthority('')
    setFilterClassification('')
    setFilterState('')
    setFilterCategory('')
    setSearch('')
    setActiveSearch('')
    setPage(1)
  }

  const isFiltered = Boolean(
    filterRule || filterAuthority || filterClassification || filterState || filterCategory || activeSearch
  )

  const summary = summaryAsync.data
  const queue = queueAsync.data
  const totalPages = queue?.total_pages || 1

  const selectRuleFilter = (ruleId: string) => {
    if (filterRule === ruleId) {
      setFilterRule('')
    } else {
      setFilterRule(ruleId)
    }
    setPage(1)
  }

  return (
    <div className="compliance-container">
      {/* 1. Global Neutrality Disclaimer */}
      <div className="compliance-disclaimer" role="note">
        <strong>Administrative Review Note:</strong> These indicators identify records requiring human review based on
        guidelines, official monitoring benchmarks, or operational analytics. They do not by themselves establish
        wrongdoing or intentional conduct.
      </div>

      {/* 2. Metadata Snapshot Bar */}
      <div className="compliance-meta-bar">
        <div>
          <span className="badge-snapshot">Data snapshot: 25 Sep 2026</span>
          <span style={{ marginLeft: '10px' }}>
            Metrics shown from the current processed MPLADS dataset snapshot.
          </span>
        </div>
        <div className="muted">
          Policy Baseline: <strong>MPLADS Guidelines 2023</strong> &amp; official MoSPI monitoring provisions
        </div>
      </div>

      {/* Loading & Error States for Overview */}
      {summaryAsync.loading && !summary && <Loading text="Loading compliance intelligence overview…" />}
      {summaryAsync.error && <ErrorBox message={summaryAsync.error} retry={summaryAsync.retry} />}

      {summary && (
        <>
          {/* 3. KPI Cards */}
          <div className="cards" style={{ marginBottom: '14px' }}>
            <div className="stat">
              <span className="stat-label">Total Works Evaluated</span>
              <strong className="stat-value">{num(summary.total_works_evaluated)}</strong>
              <small className="stat-help">Full national dataset coverage</small>
            </div>
            <div className="stat key">
              <span className="stat-label">Policy/Guideline Triggers</span>
              <strong className="stat-value">{num(summary.unique_counts.unique_policy_affected_works)}</strong>
              <small className="stat-help">
                {pct(summary.unique_counts.unique_policy_affected_works / summary.total_works_evaluated)} of total works
              </small>
            </div>
            <div className="stat" style={{ borderLeft: '4px solid #b45309' }}>
              <span className="stat-label">Execution Risk Alerts</span>
              <strong className="stat-value">{num(summary.unique_counts.unique_execution_affected_works)}</strong>
              <small className="stat-help">
                {pct(summary.unique_counts.unique_execution_affected_works / summary.total_works_evaluated)} of total works
              </small>
            </div>
            <div className="stat" style={{ borderLeft: '4px solid #16a34a' }}>
              <span className="stat-label">Unflagged Baseline Works</span>
              <strong className="stat-value">{num(summary.unique_counts.unflagged_baseline_works)}</strong>
              <small className="stat-help">
                {pct(summary.unique_counts.unflagged_baseline_works / summary.total_works_evaluated)} all benchmarks met
              </small>
            </div>
          </div>

          {/* 4. Financial Reconciliation Section */}
          <Card
            title="Financial Outlay & Execution Reconciliation"
            note="Deduplicated work-level metrics from authoritative expenditure records"
          >
            <div className="fin-cards-grid">
              <div className="stat" style={{ background: '#f8fafc' }}>
                <span className="stat-label">Cumulative Deduplicated Disbursement</span>
                <strong className="stat-value" style={{ color: '#0f172a' }}>
                  ₹{summary.financial_outlay.total_deduplicated_disbursed_outlay_all_works_cr.toLocaleString('en-IN', { minimumFractionDigits: 2 })} Cr
                </strong>
                <small className="stat-help">
                  Deduplicated work-level disbursement metric from the 25 Sep 2026 dataset snapshot. Not an un-reconciled transaction sum or cashbook total.
                </small>
              </div>
              <div className="stat" style={{ background: '#f8fafc' }}>
                <span className="stat-label">Disbursement to Flagged Works</span>
                <strong className="stat-value" style={{ color: '#b45309' }}>
                  ₹{summary.financial_outlay.flagged_works_deduplicated_disbursed_outlay_cr.toLocaleString('en-IN', { minimumFractionDigits: 2 })} Cr
                </strong>
                <small className="stat-help">
                  Recorded disbursements across the 71,098 unique works carrying one or more review triggers.
                </small>
              </div>
              <div className="stat" style={{ background: '#f8fafc' }}>
                <span className="stat-label">Sanctioned Commitment of Flagged Works</span>
                <strong className="stat-value" style={{ color: '#1d4e89' }}>
                  ₹{summary.financial_outlay.flagged_works_sanctioned_outlay_cr.toLocaleString('en-IN', { minimumFractionDigits: 2 })} Cr
                </strong>
                <small className="stat-help">
                  Total administrative sanction value of flagged works (total all works: ₹4,293.83 Cr).
                </small>
              </div>
            </div>
          </Card>

          {/* 5. Rule Breakdown Section */}
          <Card
            title="Guideline Provisions & Operational Risk Rules"
            note="8 deterministic compliance, monitoring, and execution benchmarks (Click a card to filter the review queue)"
          >
            <div className="rules-grid">
              {Object.values(summary.rule_breakdown).map((rule: RuleSummaryStat) => {
                const isActive = filterRule === rule.rule_id
                return (
                  <div
                    key={rule.rule_id}
                    className={`rule-summary-card ${isActive ? 'active-filter' : ''}`}
                    tabIndex={0}
                    role="button"
                    onClick={() => selectRuleFilter(rule.rule_id)}
                    onKeyDown={e => e.key === 'Enter' && selectRuleFilter(rule.rule_id)}
                  >
                    <div>
                      <div className="rule-card-top">
                        <span className="rule-card-id">{rule.rule_id}</span>
                        <AuthBadge authority={rule.authority_type} classification={rule.classification} />
                      </div>
                      <div className="rule-card-title">{rule.rule_name}</div>
                    </div>

                    <div className="rule-card-stat">
                      <span className="count">{num(rule.unique_works_count)}</span>
                      <span className="pct">({rule.percentage_of_all_works}%)</span>
                    </div>

                    <div className="rule-card-meta">
                      <div><strong>Benchmark:</strong> {rule.threshold}</div>
                      <div><strong>Ref:</strong> {rule.source_reference}</div>
                    </div>
                  </div>
                )
              })}
            </div>
          </Card>
        </>
      )}

      {/* 6. Review Queue Table */}
      <Card
        title="Compliance & Execution Risk Review Queue"
        note="Filterable list of works flagged for administrative review (Click row for full rule breakdown)"
      >
        <div className="filters">
          <form onSubmit={handleSearchSubmit} className="search">
            <input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search Work ID, MP, or District"
              aria-label="Search Work ID, MP, or District"
            />
            <button className="btn primary" type="submit">Search</button>
          </form>

          <select
            aria-label="Filter by Rule"
            value={filterRule}
            onChange={e => { setFilterRule(e.target.value); setPage(1) }}
          >
            <option value="">All 8 Rules</option>
            <option value="COMP-01">COMP-01: Recommendation Latency (&gt;45d)</option>
            <option value="COMP-02">COMP-02: Sanction Below Benchmark (&lt;₹2.5L)</option>
            <option value="COMP-03">COMP-03: Extended Execution (&gt;365d)</option>
            <option value="COMP-04">COMP-04: Completed with Zero Outlay</option>
            <option value="MON-01">MON-01: MoSPI Monitoring (91–180d Unpaid)</option>
            <option value="RISK-01">RISK-01: Execution Dormancy (&gt;180d Unpaid)</option>
            <option value="RISK-02">RISK-02: Stalled Disbursement (&lt;20%)</option>
            <option value="RISK-03">RISK-03: Post-Completion Outlay (&gt;30d)</option>
          </select>

          <select
            aria-label="Authority Classification"
            value={filterAuthority}
            onChange={e => { setFilterAuthority(e.target.value); setPage(1) }}
          >
            <option value="">All Authority Types</option>
            <option value="GUIDELINE_PROVISION">Guideline Provision</option>
            <option value="OFFICIAL_MONITORING">Official Monitoring</option>
            <option value="OPERATIONAL_PROXY">Policy-Derived Proxy</option>
            <option value="HEURISTIC">Execution Heuristic</option>
          </select>

          <select
            aria-label="Rule Classification"
            value={filterClassification}
            onChange={e => { setFilterClassification(e.target.value); setPage(1) }}
          >
            <option value="">All Classifications</option>
            <option value="POLICY-DERIVED REVIEW TRIGGER">Policy Review Trigger</option>
            <option value="POLICY-DERIVED PROXY">Policy-Derived Proxy</option>
            <option value="POLICY-DERIVED RECONCILIATION CHECK">Policy Reconciliation Check</option>
            <option value="OFFICIAL MONITORING BENCHMARK">Official Monitoring Benchmark</option>
            <option value="EXECUTION HEURISTIC">Execution Heuristic</option>
          </select>

          <select
            aria-label="State"
            value={filterState}
            onChange={e => { setFilterState(e.target.value); setPage(1) }}
          >
            <option value="">All States</option>
            {statesAsync.data?.map(s => <option key={s} value={s}>{s}</option>)}
          </select>

          <select
            aria-label="Work Category"
            value={filterCategory}
            onChange={e => { setFilterCategory(e.target.value); setPage(1) }}
          >
            <option value="">All Categories</option>
            {catsAsync.data?.map(c => <option key={c} value={c}>{c}</option>)}
          </select>

          {isFiltered && (
            <button className="btn" type="button" onClick={clearFilters}>
              Clear filters
            </button>
          )}
        </div>

        {queueAsync.error ? (
          <ErrorBox message={queueAsync.error} retry={queueAsync.retry} />
        ) : (
          <div className="tablewrap" aria-busy={queueAsync.loading}>
            <table>
              <thead>
                <tr>
                  <th>Work ID</th>
                  <th>State</th>
                  <th>Constituency</th>
                  <th>Category</th>
                  <th className="r">Sanction Amount</th>
                  <th className="r">Recorded Outlay</th>
                  <th>Status</th>
                  <th>Triggered Rules</th>
                  <th>Primary Classification</th>
                </tr>
              </thead>
              <tbody>
                {queue?.items.map(w => {
                  const hasRules = w.triggered_rule_ids && w.triggered_rule_ids.length > 0
                  return (
                    <tr
                      key={w.work_id}
                      tabIndex={0}
                      onClick={() => setSelectedWorkId(w.work_id)}
                      onKeyDown={e => e.key === 'Enter' && setSelectedWorkId(w.work_id)}
                    >
                      <td className="id">{w.work_id}</td>
                      <td>{w.state}</td>
                      <td>{w.constituency}</td>
                      <td>{w.work_category}</td>
                      <td className="r">{inr(w.sanction_amount)}</td>
                      <td className="r">{inr(w.deduplicated_disbursed_amount)}</td>
                      <td>{w.work_status}</td>
                      <td>
                        <div className="rule-chips-wrap">
                          {hasRules ? (
                            w.triggered_rule_ids.map(rId => <RuleTag key={rId} ruleId={rId} />)
                          ) : (
                            <span className="muted" style={{ fontSize: '12px' }}>None (Baseline)</span>
                          )}
                        </div>
                      </td>
                      <td>
                        {w.evaluations && w.evaluations.length > 0 ? (
                          (() => {
                            const firstTriggered = w.evaluations.find(e => e.is_triggered)
                            return firstTriggered ? (
                              <AuthBadge
                                authority={firstTriggered.authority_type}
                                classification={firstTriggered.classification}
                              />
                            ) : (
                              <span className="auth-badge" style={{ background: '#ecfdf3', color: '#027a48' }}>
                                Baseline Met
                              </span>
                            )
                          })()
                        ) : (
                          <span className="muted">–</span>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>

            {queueAsync.loading && !queue && <Loading text="Loading review queue…" />}
            {!queueAsync.loading && queue?.items.length === 0 && (
              <Empty>
                {isFiltered
                  ? 'No works match the selected filter criteria. Clear or broaden filters to view records.'
                  : 'No review items are available in the compliance queue.'}
              </Empty>
            )}
          </div>
        )}

        {queue && queue.total_items > 0 && (
          <div className="pager">
            <span className="muted">
              Page {page} of {totalPages}, {num(queue.total_items)} flagged works
            </span>
            <button className="btn" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              Previous
            </button>
            <button className="btn" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>
              Next
            </button>
          </div>
        )}
      </Card>

      {/* 7. Work Investigation Panel / Modal */}
      {selectedWorkId && (
        <div className="modal-backdrop" onClick={() => setSelectedWorkId(null)}>
          <div
            className="modal-content compliance-modal-content"
            onClick={e => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-labelledby="compliance-modal-title"
          >
            <div className="modal-header">
              <div>
                <h2 id="compliance-modal-title">Work Compliance &amp; Execution Risk Profile</h2>
                <span className="muted" style={{ fontSize: '13px' }}>
                  Authoritative review profile evaluated against MPLADS Guidelines 2023 and monitoring benchmarks
                </span>
              </div>
              <button
                type="button"
                className="btn"
                onClick={() => setSelectedWorkId(null)}
                aria-label="Close modal"
              >
                ✕ Close
              </button>
            </div>

            <div className="modal-body">
              {workAsync.loading && !workAsync.data && (
                <Loading text="Loading detailed compliance evaluations…" />
              )}
              {workAsync.error && (
                <ErrorBox message={workAsync.error} retry={workAsync.retry} />
              )}

              {workAsync.data && (
                (() => {
                  const w = workAsync.data
                  const triggeredEvals = (w.evaluations || []).filter(e => e.is_triggered)
                  const nonTriggeredEvals = (w.evaluations || []).filter(e => !e.is_triggered)

                  return (
                    <>
                      {/* Section A: Work Overview */}
                      <div className="work-overview-grid">
                        <div className="work-overview-item">
                          <span>Work ID</span>
                          <strong className="id">{w.work_id}</strong>
                        </div>
                        <div className="work-overview-item">
                          <span>State &amp; Constituency</span>
                          <strong>{w.state}, {w.constituency}</strong>
                        </div>
                        <div className="work-overview-item">
                          <span>Work Category</span>
                          <strong>{w.work_category}</strong>
                        </div>
                        <div className="work-overview-item">
                          <span>Work Status</span>
                          <strong>{w.work_status}</strong>
                        </div>
                        <div className="work-overview-item">
                          <span>Sanction Amount</span>
                          <strong>{inr(w.sanction_amount)}</strong>
                        </div>
                        <div className="work-overview-item">
                          <span>Recorded Outlay</span>
                          <strong>{inr(w.deduplicated_disbursed_amount)}</strong>
                        </div>
                        <div className="work-overview-item">
                          <span>Utilization Ratio</span>
                          <strong>{pct(w.utilization_ratio)}</strong>
                        </div>
                        <div className="work-overview-item">
                          <span>Key Dates</span>
                          <span style={{ fontSize: '12px', color: 'var(--ink)' }}>
                            Sanc: {w.sanction_date || '–'} | Comp: {w.completion_date || '–'}
                          </span>
                        </div>
                      </div>

                      {w.work_description && (
                        <div className="work-desc-box">
                          <strong>Cleaned Work Description</strong>
                          {w.work_description}
                        </div>
                      )}

                      {/* Section B: Trigger Summary */}
                      <div className="trigger-highlight-banner">
                        <div>
                          <h3>
                            {triggeredEvals.length} Review Trigger(s) &amp; Execution Alert(s) Identified
                          </h3>
                          <span style={{ fontSize: '13px', color: '#78350f' }}>
                            {triggeredEvals.length > 0
                              ? 'This record requires administrative verification against the observed benchmarks below.'
                              : 'All 8 guideline and monitoring benchmarks were evaluated and met.'}
                          </span>
                        </div>
                        <div className="rule-chips-wrap">
                          {triggeredEvals.map(e => <RuleTag key={e.rule_id} ruleId={e.rule_id} />)}
                        </div>
                      </div>

                      {/* Section C: Rule Explanation Cards (Triggered Rules) */}
                      <div>
                        <h3 style={{ fontSize: '15px', marginBottom: '10px' }}>
                          Active Review Triggers &amp; Benchmark Findings
                        </h3>
                        {triggeredEvals.length === 0 ? (
                          <div className="state empty" style={{ border: '1px solid var(--line)', borderRadius: '6px' }}>
                            No guideline or execution review triggers were activated for this work.
                          </div>
                        ) : (
                          triggeredEvals.map((e: RuleEvaluation) => (
                            <div key={e.rule_id} className="rule-expl-card triggered">
                              <div className="rule-expl-header">
                                <div className="rule-expl-header-left">
                                  <RuleTag ruleId={e.rule_id} />
                                  <h3>{e.rule_name}</h3>
                                </div>
                                <AuthBadge authority={e.authority_type} classification={e.classification} />
                              </div>

                              <div className="rule-benchmark-bar">
                                <div className="rule-benchmark-item">
                                  <span>Observed Value</span>
                                  <strong>{e.observed_value}</strong>
                                </div>
                                <div className="rule-benchmark-item">
                                  <span>Benchmark Threshold</span>
                                  <strong>{e.threshold}</strong>
                                </div>
                                <div className="rule-benchmark-item">
                                  <span>Source Reference</span>
                                  <strong>{e.source_reference}</strong>
                                </div>
                              </div>

                              <div className="rule-sections-trio">
                                <div className="rule-section-box why">
                                  <h4>Why This Appears</h4>
                                  <p>{e.reason}</p>
                                </div>
                                <div className="rule-section-box limitation">
                                  <h4>Data Limitation</h4>
                                  <p>{e.limitation}</p>
                                </div>
                                <div className="rule-section-box action">
                                  <h4>Recommended Verification Action</h4>
                                  <p>{e.recommended_action}</p>
                                </div>
                              </div>
                            </div>
                          ))
                        )}
                      </div>

                      {/* Section D: Non-triggered Rules (Collapsed) */}
                      {nonTriggeredEvals.length > 0 && (
                        <details className="other-checks-summary">
                          <summary>
                            Other checks evaluated ({nonTriggeredEvals.length} rules benchmark met)
                          </summary>
                          <div className="other-checks-list">
                            {nonTriggeredEvals.map(e => (
                              <div key={e.rule_id} className="other-check-row">
                                <div>
                                  <strong style={{ marginRight: '8px' }}>{e.rule_id}</strong>
                                  <span>{e.rule_name}</span>
                                  <span className="muted" style={{ marginLeft: '10px', fontSize: '12px' }}>
                                    (Observed: {e.observed_value}, Threshold: {e.threshold})
                                  </span>
                                </div>
                                <span className="other-check-status">✓ Benchmark Met</span>
                              </div>
                            ))}
                          </div>
                        </details>
                      )}
                    </>
                  )
                })()
              )}
            </div>

            <div className="modal-footer">
              <button
                type="button"
                className="btn"
                style={{ marginRight: 'auto' }}
                onClick={() => {
                  if (selectedWorkId) {
                    openWork(selectedWorkId)
                    setSelectedWorkId(null)
                  }
                }}
              >
                View Full Work Record &rarr;
              </button>
              <button type="button" className="btn primary" onClick={() => setSelectedWorkId(null)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
