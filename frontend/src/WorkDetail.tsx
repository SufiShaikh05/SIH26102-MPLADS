import { useState } from 'react'
import { api, type Anomaly, type DuplicatePairRecord } from './api/client'
import { AuthBadge, RuleTag } from './ComplianceReview'
import { Card, Chip, ErrorBox, GateBadge, Loading, inr, num, pct, pctDec, signalText, useAsync } from './ui'

export default function WorkDetail({ id, back }: { id: string; back: () => void }) {
  const [selectedPair, setSelectedPair] = useState<DuplicatePairRecord | null>(null)

  const r = useAsync(async () => {
    const [a, w] = await Promise.all([api.anomaly(id), api.work(id).catch(() => ({}))])
    return { ...w, ...a } as Anomaly // anomaly record wins; /works adds any extra fields
  }, [id])

  const dups = useAsync(async () => {
    return api.workDuplicates(id).catch(() => ({
      work_id: id,
      has_duplicates: false,
      duplicate_pairs: [],
      cluster_id: null,
      cluster_work_ids: [],
    }))
  }, [id])

  const comp = useAsync(async () => {
    return api.workCompliance(id).catch(() => null)
  }, [id])

  const w = r.data
  const dData = dups.data
  const signals = w ? [w.top_signal_1, w.top_signal_2, w.top_signal_3].filter(Boolean) as string[] : []
  const facts: [string, string][] = w ? [
    ['Work ID', w.work_id], ['State', w.state], ['Constituency', w.constituency], ['MP', w.mp_name], ['Implementing authority', w.ida],
    ['Work category', w.work_category], ['Work status', w.work_status], ['Sanction amount', inr(w.sanction_amount)],
    ['Total expenditure (all rows)', inr(w.total_disbursed_all_rows)], ['Deduplicated expenditure', inr(w.deduplicated_disbursed_amount)],
    ['Utilization', pct(w.success_utilization_ratio)], ['Payment count', num(w.payment_count)], ['Vendor count', num(w.vendor_count)],
    ['Duplicate ratio', pct(w.duplicate_ratio)], ['Days since sanction', num(w.days_since_sanction)], ['Days since last payment', num(w.days_since_last_payment)],
  ] : []

  const jumpToWork = (peerId: string) => {
    location.hash = `#/work/${encodeURIComponent(peerId)}`
    setSelectedPair(null)
  }

  return (
    <div className="detail">
      <button className="btn back" onClick={back}>Back to dashboard</button>
      {r.loading && !w && <Loading text="Loading work details…" />}
      {r.error && <ErrorBox message={r.error} retry={r.retry} />}
      {w && (
        <>
          <h2 className="dtitle">Work {w.work_id} <small>{w.work_category}, {w.state}</small></h2>
          <div className="dgrid">
            <section className="panel why">
              <header><h2>Why this work is a review candidate</h2></header>
              <div className="scoreband">
                <div><span className="muted">Review Priority Score</span><strong className="big">{w.review_priority_score}</strong></div>
                <div><span className="muted">Review Priority Label</span><Chip label={w.review_priority_label} /></div>
                <div><span className="muted">Signal count</span><strong className="big">{w.signal_count}</strong></div>
              </div>
              <h3>Indicators contributing to this score</h3>
              <div className="signals">
                {signals.length ? signals.map((s, i) => <div className="signal" key={i}><span className="rank">{i + 1}</span>{signalText(s)}</div>) : <p className="muted">No individual indicators were reported for this work.</p>}
              </div>
              <h3>Explanation</h3>
              <p className="expl">{w.explanation_text || 'No explanation text was provided for this work.'}</p>
              <div className="interp"><strong>Suggested interpretation</strong>
                <p>These indicators identify an unusual pattern that may warrant verification. They do not establish that any irregularity has occurred; a reviewer should check the underlying records.</p>
              </div>
            </section>
            <Card title="Work details">
              <dl className="facts">{facts.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v || '–'}</dd></div>)}</dl>
            </Card>
          </div>

          {/* Sentinel 2.0 Potential Duplicate Work Intelligence */}
          {dData && dData.has_duplicates && (
            <section className="panel dup-detail-panel" style={{ marginTop: '16px' }}>
              <header>
                <div>
                  <h2>Potential Duplicate Work Intelligence</h2>
                  <span className="muted">
                    Sentinel 2.0 identified {dData.duplicate_pairs.length} potential duplicate pair relationship(s)
                    {dData.cluster_id ? ` across Cluster ${dData.cluster_id} (${dData.cluster_work_ids.length} linked works)` : ''}.
                  </span>
                </div>
              </header>

              <div className="tablewrap" style={{ marginTop: '12px' }}>
                <table>
                  <thead>
                    <tr>
                      <th>Priority</th>
                      <th className="r">Risk Score</th>
                      <th>Paired Work</th>
                      <th>Text Similarity</th>
                      <th className="r">Peer Sanction</th>
                      <th className="r">Amount Diff</th>
                      <th className="r">Date Gap</th>
                      <th>Serials</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dData.duplicate_pairs.map(p => {
                      const peerId = p.work_id_a.toUpperCase() === id.toUpperCase() ? p.work_id_b : p.work_id_a
                      const peerAmt = p.work_id_a.toUpperCase() === id.toUpperCase() ? p.sanction_amount_b : p.sanction_amount_a
                      return (
                        <tr key={p.pair_id} onClick={() => setSelectedPair(p)}>
                          <td><Chip label={p.review_priority} /></td>
                          <td className="r score-cell"><strong>{p.duplicate_risk_score.toFixed(1)}</strong></td>
                          <td className="id">
                            <button
                              type="button"
                              className="linkbtn"
                              onClick={e => {
                                e.stopPropagation()
                                jumpToWork(peerId)
                              }}
                            >
                              {peerId}
                            </button>
                          </td>
                          <td>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                              <span style={{ fontVariantNumeric: 'tabular-nums', fontWeight: 600 }}>
                                {pctDec(p.text_similarity, 2)}
                              </span>
                              <GateBadge similarity={p.text_similarity} />
                            </div>
                          </td>
                          <td className="r">{inr(peerAmt)}</td>
                          <td className="r">{p.amount_difference_pct != null ? pctDec(p.amount_difference_pct, 1) : '–'}</td>
                          <td className="r">{p.date_gap_days != null ? `${p.date_gap_days}d` : '–'}</td>
                          <td>{p.consecutive_serials ? <span className="consec-tag">Consecutive</span> : '–'}</td>
                          <td>
                            <button
                              type="button"
                              className="btn"
                              style={{ padding: '3px 8px', fontSize: '13px' }}
                              onClick={e => {
                                e.stopPropagation()
                                setSelectedPair(p)
                              }}
                            >
                              Compare
                            </button>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>

              <div className="dup-disclaimer" style={{ marginTop: '12px' }}>
                <strong>Verification Note:</strong> Potential Duplicate Risk Scores identify similarity and proximity patterns for human verification. They do not constitute proof of duplication or wrongdoing.
              </div>
            </section>
          )}

          {/* Sentinel 2.0 Compliance & Execution Risk Intelligence */}
          {comp.data && (
            <section className="panel" style={{ marginTop: '16px', borderTop: '4px solid #1d4e89' }}>
              <header>
                <div>
                  <h2>Compliance &amp; Execution Risk Profile</h2>
                  <span className="muted">
                    Evaluated against MPLADS Guidelines 2023 provisions, official MoSPI monitoring benchmarks, and operational heuristics.
                  </span>
                </div>
                <div className="rule-chips-wrap">
                  {comp.data.triggered_rule_ids && comp.data.triggered_rule_ids.length > 0 ? (
                    comp.data.triggered_rule_ids.map(rId => <RuleTag key={rId} ruleId={rId} />)
                  ) : (
                    <span className="auth-badge" style={{ background: '#ecfdf3', color: '#027a48' }}>
                      All Benchmarks Met
                    </span>
                  )}
                </div>
              </header>

              <div className="trigger-highlight-banner" style={{ marginTop: '12px', marginBottom: '14px' }}>
                <div>
                  <h3>
                    {(comp.data.triggered_rule_ids || []).length} Active Review Trigger(s) &amp; Execution Alert(s)
                  </h3>
                  <span style={{ fontSize: '13px', color: '#78350f' }}>
                    {(comp.data.triggered_rule_ids || []).length > 0
                      ? 'This record requires administrative review against the benchmarks below.'
                      : 'All evaluated guideline and operational benchmarks were met for this work.'}
                  </span>
                </div>
              </div>

              {comp.data.evaluations && comp.data.evaluations.filter(e => e.is_triggered).length > 0 && (
                <div style={{ display: 'grid', gap: '12px' }}>
                  {comp.data.evaluations.filter(e => e.is_triggered).map(e => (
                    <div key={e.rule_id} className="rule-expl-card triggered" style={{ margin: 0 }}>
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
                  ))}
                </div>
              )}

              {comp.data.evaluations && comp.data.evaluations.filter(e => !e.is_triggered).length > 0 && (
                <details className="other-checks-summary">
                  <summary>
                    Other checks evaluated ({comp.data.evaluations.filter(e => !e.is_triggered).length} rules benchmark met)
                  </summary>
                  <div className="other-checks-list">
                    {comp.data.evaluations.filter(e => !e.is_triggered).map(e => (
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

              <div className="compliance-disclaimer" style={{ marginTop: '14px', marginBottom: 0 }}>
                <strong>Administrative Review Note:</strong> These indicators identify records requiring human review based on guidelines, official monitoring benchmarks, or operational analytics. They do not by themselves establish wrongdoing or intentional conduct.
              </div>
            </section>
          )}

          {/* Modal for side-by-side comparison from WorkDetail */}
          {selectedPair && (
            <div
              className="modal-backdrop"
              onClick={() => setSelectedPair(null)}
              role="dialog"
              aria-modal="true"
            >
              <div className="modal-content" onClick={e => e.stopPropagation()}>
                <header className="modal-header">
                  <div>
                    <h2>Pair Inspection: {selectedPair.pair_id}</h2>
                    <span className="muted">
                      {selectedPair.cluster_id ? `Cluster: ${selectedPair.cluster_id}` : 'Unclustered pair'} &bull; {selectedPair.state || '–'} ({selectedPair.constituency || '–'})
                    </span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <Chip label={selectedPair.review_priority} />
                    <button type="button" className="btn" onClick={() => setSelectedPair(null)}>✕ Close</button>
                  </div>
                </header>

                <div className="modal-body">
                  <div className="modal-scoreband">
                    <div className="modal-metric-card">
                      <span className="metric-label">Potential Duplicate Risk Score</span>
                      <strong className="big metric-value">{selectedPair.duplicate_risk_score.toFixed(1)}</strong>
                    </div>
                    <div className="modal-metric-card">
                      <span className="metric-label">Text Similarity</span>
                      <span className="metric-sublabel">Trigram</span>
                      <strong className="big metric-value">{pctDec(selectedPair.text_similarity, 2)}</strong>
                      <div className="metric-gate-wrap">
                        <GateBadge similarity={selectedPair.text_similarity} />
                      </div>
                    </div>
                    <div className="modal-metric-card">
                      <span className="metric-label">Sanction Amount Difference</span>
                      <strong className="big metric-value">
                        {selectedPair.amount_difference_pct != null ? pctDec(selectedPair.amount_difference_pct, 2) : '–'}
                      </strong>
                    </div>
                    <div className="modal-metric-card">
                      <span className="metric-label">Sanction Date Gap</span>
                      <strong className="big metric-value">
                        {selectedPair.date_gap_days != null ? `${selectedPair.date_gap_days} days` : '–'}
                      </strong>
                    </div>
                  </div>

                  <div className="side-by-side-grid">
                    <div className="compare-card">
                      <div className="compare-card-header">
                        <h3>Work A {selectedPair.work_id_a === id && '(Current)'}</h3>
                        {selectedPair.work_id_a !== id && (
                          <button
                            type="button"
                            className="btn primary"
                            style={{ fontSize: '13px', padding: '4px 10px' }}
                            onClick={() => jumpToWork(selectedPair.work_id_a)}
                          >
                            Open This Work
                          </button>
                        )}
                      </div>
                      <dl className="facts">
                        <div><dt>Work ID</dt><dd className="id">{selectedPair.work_id_a}</dd></div>
                        <div><dt>Category</dt><dd>{selectedPair.work_category || '–'}</dd></div>
                        <div><dt>Sanction Amount</dt><dd>{inr(selectedPair.sanction_amount_a)}</dd></div>
                        <div><dt>Sanction Date</dt><dd>{selectedPair.sanction_date_a || '–'}</dd></div>
                      </dl>
                      <div className="work-desc-box">
                        <strong>Work Description:</strong>
                        <p>{selectedPair.description_a || 'No description available.'}</p>
                      </div>
                    </div>

                    <div className="compare-card">
                      <div className="compare-card-header">
                        <h3>Work B {selectedPair.work_id_b === id && '(Current)'}</h3>
                        {selectedPair.work_id_b !== id && (
                          <button
                            type="button"
                            className="btn primary"
                            style={{ fontSize: '13px', padding: '4px 10px' }}
                            onClick={() => jumpToWork(selectedPair.work_id_b)}
                          >
                            Open This Work
                          </button>
                        )}
                      </div>
                      <dl className="facts">
                        <div><dt>Work ID</dt><dd className="id">{selectedPair.work_id_b}</dd></div>
                        <div><dt>Category</dt><dd>{selectedPair.work_category || '–'}</dd></div>
                        <div><dt>Sanction Amount</dt><dd>{inr(selectedPair.sanction_amount_b)}</dd></div>
                        <div><dt>Sanction Date</dt><dd>{selectedPair.sanction_date_b || '–'}</dd></div>
                      </dl>
                      <div className="work-desc-box">
                        <strong>Work Description:</strong>
                        <p>{selectedPair.description_b || 'No description available.'}</p>
                      </div>
                    </div>
                  </div>

                  {selectedPair.reasons.length > 0 && (
                    <div className="reasons-section">
                      <strong>Flagged Evidence Reasons:</strong>
                      <ul className="reasons-list">
                        {selectedPair.reasons.map((reason, idx) => <li key={idx}>{reason}</li>)}
                      </ul>
                    </div>
                  )}

                  <div className="dup-disclaimer">
                    <strong>Regulatory Review Disclaimer:</strong> Potential Duplicate Risk Scores highlight similarity and proximity patterns for human verification. They do not establish that any irregularity or intentional duplication has occurred.
                  </div>
                </div>

                <footer className="modal-footer">
                  <button type="button" className="btn" onClick={() => setSelectedPair(null)}>Close Inspection</button>
                </footer>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
