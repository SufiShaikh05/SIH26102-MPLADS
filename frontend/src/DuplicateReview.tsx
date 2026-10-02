import { useState, useEffect, type FormEvent } from 'react'
import { api, type DuplicatePairRecord, type DuplicateQuery } from './api/client'
import {
  Card,
  Chip,
  Empty,
  ErrorBox,
  GateBadge,
  Loading,
  inr,
  num,
  pctDec,
  useAsync,
} from './ui'

const PAGE_SIZE = 20

interface DuplicateReviewProps {
  openWork: (id: string) => void
}

export default function DuplicateReview({ openWork }: DuplicateReviewProps) {
  // Mode: Ordinary Review Queue (false) vs Batch Pattern Links (true)
  const [isBatch, setIsBatch] = useState(false)
  const [page, setPage] = useState(1)
  const [stateFilter, setStateFilter] = useState('')
  const [priorityFilter, setPriorityFilter] = useState('')
  const [minScore, setMinScore] = useState('')
  const [sortBy, setSortBy] = useState<'score_desc' | 'score_asc' | 'amount_desc' | 'date_gap_asc'>('score_desc')
  const [selectedPair, setSelectedPair] = useState<DuplicatePairRecord | null>(null)
  const [searchWorkId, setSearchWorkId] = useState('')
  const [searchResult, setSearchResult] = useState<DuplicatePairRecord[] | null>(null)
  const [searchError, setSearchError] = useState('')
  const [searching, setSearching] = useState(false)

  // Load summary metrics and state list
  const summary = useAsync(api.duplicateSummary, [])
  const states = useAsync(api.states, [])

  // Build query
  const query: DuplicateQuery = {
    page,
    page_size: PAGE_SIZE,
    is_batch: isBatch,
    state: stateFilter || undefined,
    priority: priorityFilter || undefined,
    min_score: minScore !== '' ? Number(minScore) : undefined,
    sort_by: sortBy,
  }

  const list = useAsync(() => api.duplicates(query), [JSON.stringify(query)])

  // Keyboard navigation for modal
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && selectedPair) {
        setSelectedPair(null)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [selectedPair])

  const totalPages = Math.max(1, Math.ceil((list.data?.total ?? 0) / PAGE_SIZE))
  const isFiltered = Boolean(stateFilter || priorityFilter || minScore || sortBy !== 'score_desc')

  const clearFilters = () => {
    setStateFilter('')
    setPriorityFilter('')
    setMinScore('')
    setSortBy('score_desc')
    setPage(1)
    setSearchResult(null)
    setSearchError('')
  }

  const handleSearchSubmit = async (e: FormEvent) => {
    e.preventDefault()
    const id = searchWorkId.trim()
    if (!id) return
    setSearching(true)
    setSearchError('')
    setSearchResult(null)
    try {
      const res = await api.workDuplicates(id)
      if (res.duplicate_pairs && res.duplicate_pairs.length > 0) {
        setSearchResult(res.duplicate_pairs)
      } else {
        setSearchError(`No potential duplicate pairs found for work "${id}".`)
      }
    } catch (err) {
      setSearchError((err as Error).message || `Failed to find duplicates for "${id}".`)
    } finally {
      setSearching(false)
    }
  }

  const clearSearch = () => {
    setSearchWorkId('')
    setSearchResult(null)
    setSearchError('')
  }

  const d = summary.data

  return (
    <div className="dup-review-view">
      {/* Data Snapshot Metadata */}
      <div className="dup-meta-bar">
        <span>Data snapshot: 25 Sep 2026</span>
        <span className="muted">&bull; Metrics shown from the current processed MPLADS dataset snapshot.</span>
      </div>

      {/* 1. Summary Metrics Cards */}
      {summary.loading && !d ? (
        <Loading text="Loading duplicate detection summary…" />
      ) : summary.error ? (
        <ErrorBox message={summary.error} retry={summary.retry} />
      ) : d ? (
        <div className="cards dup-summary-cards">
          <div className="stat key">
            <span className="stat-label">Ordinary Review Pairs</span>
            <strong className="stat-value">{num(d.total_duplicate_pairs)}</strong>
            <span className="stat-help muted">Batch-pattern links excluded</span>
          </div>
          <div className="stat">
            <span className="stat-label" style={{ color: 'var(--high)' }}>High-Confidence Pairs</span>
            <strong className="stat-value" style={{ color: 'var(--high)' }}>{num(d.high_confidence_pairs)}</strong>
            <span className="stat-help muted">Score ≥80 + all strict gates</span>
          </div>
          <div className="stat" title="Below High-Confidence gate">
            <span className="stat-label" style={{ color: 'var(--medium)' }}>Medium-Confidence Potential Pairs</span>
            <strong className="stat-value" style={{ color: 'var(--medium)' }}>{num(d.medium_confidence_pairs)}</strong>
            <span className="stat-help muted" title="Below High-Confidence gate">Does not satisfy the High-Confidence conjunction</span>
          </div>
          <div className="stat">
            <span className="stat-label" style={{ color: 'var(--accent)' }}>Batch Pattern Links</span>
            <strong className="stat-value" style={{ color: 'var(--accent)' }}>{num(d.batch_scheme_pairs)}</strong>
            <span className="stat-help muted">Isolated from ordinary review</span>
          </div>
          <div className="stat">
            <span className="stat-label">Potential Duplicate Clusters</span>
            <strong className="stat-value">{num(d.total_clusters)}</strong>
            <span className="stat-help muted">Built from high-confidence similarity links</span>
          </div>
          <div className="stat">
            <span className="stat-label">Affected Works</span>
            <strong className="stat-value">{num(d.affected_works_count)}</strong>
            <span className="stat-help muted">Total works involved</span>
          </div>
        </div>
      ) : null}

      {/* 2. Batch Isolation Tabs */}
      <div className="dup-queue-tabs" role="tablist" aria-label="Duplicate Review Queues">
        <button
          role="tab"
          aria-selected={!isBatch}
          className={`dup-queue-btn ${!isBatch ? 'active' : ''}`}
          onClick={() => {
            setIsBatch(false)
            setPage(1)
            setPriorityFilter('')
          }}
        >
          Ordinary Review Queue ({d ? num(d.total_duplicate_pairs) : '…'})
        </button>
        <button
          role="tab"
          aria-selected={isBatch}
          className={`dup-queue-btn ${isBatch ? 'active' : ''}`}
          onClick={() => {
            setIsBatch(true)
            setPage(1)
            setPriorityFilter('')
          }}
        >
          Batch Pattern Links ({d ? num(d.batch_scheme_pairs) : '…'})
        </button>
      </div>

      {isBatch && (
        <div className="dup-batch-notice" role="note">
          <strong>Batch Pattern Links:</strong> These records show repeated patterns across multiple works and are separated from the ordinary review queue to prevent repetitive patterns from dominating individual-work review.
        </div>
      )}

      {/* 3. Review Candidates Panel */}
      <Card
        title={isBatch ? 'Batch Pattern Links' : 'Potential Duplicate Work Pairs'}
        note={isBatch ? 'Isolated repeated pattern links' : 'Human review candidates ordered by risk score'}
      >
        {/* Filters Toolbar */}
        <div className="filters">
          <form onSubmit={handleSearchSubmit} className="search">
            <input
              value={searchWorkId}
              onChange={e => setSearchWorkId(e.target.value)}
              placeholder="Look up Work ID duplicates"
              aria-label="Look up Work ID duplicates"
            />
            <button className="btn primary" type="submit" disabled={searching}>
              {searching ? 'Finding…' : 'Find Pair'}
            </button>
            {searchResult && (
              <button className="btn" type="button" onClick={clearSearch}>
                Clear Search
              </button>
            )}
          </form>

          <select
            aria-label="Filter by state"
            value={stateFilter}
            onChange={e => {
              setStateFilter(e.target.value)
              setPage(1)
            }}
          >
            <option value="">All States</option>
            {states.data?.map(s => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>

          <select
            aria-label="Filter by priority"
            value={priorityFilter}
            onChange={e => {
              setPriorityFilter(e.target.value)
              setPage(1)
            }}
          >
            <option value="">All Priorities</option>
            {!isBatch && (
              <>
                <option value="high">High-Confidence</option>
                <option value="medium">Medium-Confidence</option>
              </>
            )}
            {isBatch && <option value="batch">Batch Pattern Link</option>}
          </select>

          <input
            className="score"
            type="number"
            min={0}
            max={100}
            aria-label="Minimum Duplicate Risk Score"
            placeholder="Min score"
            value={minScore}
            onChange={e => {
              setMinScore(e.target.value)
              setPage(1)
            }}
          />

          <select
            aria-label="Sort by"
            value={sortBy}
            onChange={e => {
              setSortBy(e.target.value as typeof sortBy)
              setPage(1)
            }}
          >
            <option value="score_desc">Highest Risk Score</option>
            <option value="score_asc">Lowest Risk Score</option>
            <option value="amount_desc">Largest Sanction Amount</option>
            <option value="date_gap_asc">Shortest Date Gap</option>
          </select>

          {isFiltered && (
            <button className="btn" onClick={clearFilters}>
              Clear Filters
            </button>
          )}
        </div>

        {searchError && (
          <div className="inline-msg" role="alert">
            {searchError}
          </div>
        )}

        {searchResult && (
          <div className="search-banner">
            <span>
              Showing <strong>{searchResult.length}</strong> duplicate pair(s) for work <strong>{searchWorkId}</strong>:
            </span>
            <button className="btn" onClick={clearSearch}>
              Back to Full List
            </button>
          </div>
        )}

        {/* Data Table */}
        {list.error && !searchResult ? (
          <ErrorBox message={list.error} retry={list.retry} />
        ) : (
          <div className="tablewrap" aria-busy={list.loading}>
            <table>
              <thead>
                <tr>
                  <th>Priority</th>
                  <th className="r">Risk Score</th>
                  <th>Work A</th>
                  <th>Work B</th>
                  <th>State &amp; Constituency</th>
                  <th>Text Similarity</th>
                  <th className="r">Sanction A</th>
                  <th className="r">Sanction B</th>
                  <th className="r">Date Gap</th>
                  <th>Serials</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {(searchResult ?? list.data?.items ?? []).map(p => {
                  return (
                    <tr
                      key={p.pair_id}
                      onClick={() => setSelectedPair(p)}
                      tabIndex={0}
                      onKeyDown={e => e.key === 'Enter' && setSelectedPair(p)}
                    >
                      <td>
                        <Chip label={p.review_priority} />
                      </td>
                      <td className="r score-cell">
                        <strong>{p.duplicate_risk_score.toFixed(1)}</strong>
                      </td>
                      <td className="id">
                        <button
                          type="button"
                          className="linkbtn"
                          title="Open Work A details"
                          onClick={e => {
                            e.stopPropagation()
                            openWork(p.work_id_a)
                          }}
                        >
                          {p.work_id_a}
                        </button>
                      </td>
                      <td className="id">
                        <button
                          type="button"
                          className="linkbtn"
                          title="Open Work B details"
                          onClick={e => {
                            e.stopPropagation()
                            openWork(p.work_id_b)
                          }}
                        >
                          {p.work_id_b}
                        </button>
                      </td>
                      <td>
                        <div>{p.state || '–'}</div>
                        <small className="muted">{p.constituency || '–'}</small>
                      </td>
                      <td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                          <span style={{ fontVariantNumeric: 'tabular-nums', fontWeight: 600 }}>
                            {pctDec(p.text_similarity, 2)}
                          </span>
                          <GateBadge similarity={p.text_similarity} />
                        </div>
                      </td>
                      <td className="r">{inr(p.sanction_amount_a)}</td>
                      <td className="r">{inr(p.sanction_amount_b)}</td>
                      <td className="r">
                        {p.date_gap_days != null ? `${p.date_gap_days}d` : '–'}
                      </td>
                      <td>
                        {p.consecutive_serials ? (
                          <span className="consec-tag" title="Sanctions have consecutive serial numbers">
                            Consecutive
                          </span>
                        ) : (
                          <span className="muted">–</span>
                        )}
                      </td>
                      <td>
                        <button
                          className="btn"
                          style={{ padding: '3px 8px', fontSize: '13px' }}
                          onClick={e => {
                            e.stopPropagation()
                            setSelectedPair(p)
                          }}
                        >
                          Inspect
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>

            {list.loading && !list.data && !searchResult && (
              <Loading text="Loading duplicate candidate pairs…" />
            )}

            {!list.loading && !searchResult && list.data?.items.length === 0 && (
              <Empty>
                {isFiltered
                  ? 'No potential duplicate pairs match the current filter criteria.'
                  : 'No potential duplicate pairs are available for this queue.'}
              </Empty>
            )}
          </div>
        )}

        {/* Pagination */}
        {!searchResult && list.data && list.data.total > 0 && (
          <div className="pager">
            <span className="muted">
              Page {page} of {totalPages}, {num(list.data.total)} candidate pairs
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

      {/* 4. Side-by-side Work Comparison Modal */}
      {selectedPair && (
        <div
          className="modal-backdrop"
          onClick={() => setSelectedPair(null)}
          role="dialog"
          aria-modal="true"
          aria-labelledby="modal-pair-title"
        >
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <header className="modal-header">
              <div>
                <h2 id="modal-pair-title">
                  Pair Inspection: {selectedPair.pair_id}
                </h2>
                <span className="muted">
                  {selectedPair.cluster_id ? `Cluster: ${selectedPair.cluster_id}` : 'Unclustered pair'} &bull; {selectedPair.state || '–'} ({selectedPair.constituency || '–'})
                </span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <Chip label={selectedPair.review_priority} />
                <button
                  type="button"
                  className="btn"
                  onClick={() => setSelectedPair(null)}
                  aria-label="Close inspection modal"
                >
                  ✕ Close
                </button>
              </div>
            </header>

            <div className="modal-body">
              {/* Score & Gate Highlights Banner */}
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
                    {selectedPair.amount_difference_pct != null
                      ? pctDec(selectedPair.amount_difference_pct, 2)
                      : '–'}
                  </strong>
                </div>
                <div className="modal-metric-card">
                  <span className="metric-label">Sanction Date Gap</span>
                  <strong className="big metric-value">
                    {selectedPair.date_gap_days != null ? `${selectedPair.date_gap_days} days` : '–'}
                  </strong>
                </div>
              </div>

              {/* Side-by-side Comparison Grid */}
              <div className="side-by-side-grid">
                {/* Work A Column */}
                <div className="compare-card">
                  <div className="compare-card-header">
                    <h3>Work A</h3>
                    <button
                      type="button"
                      className="btn primary"
                      style={{ fontSize: '13px', padding: '4px 10px' }}
                      onClick={() => openWork(selectedPair.work_id_a)}
                    >
                      View Full Work
                    </button>
                  </div>
                  <dl className="facts">
                    <div>
                      <dt>Work ID</dt>
                      <dd className="id">{selectedPair.work_id_a}</dd>
                    </div>
                    <div>
                      <dt>Category</dt>
                      <dd>{selectedPair.work_category || '–'}</dd>
                    </div>
                    <div>
                      <dt>Sanction Amount</dt>
                      <dd>{inr(selectedPair.sanction_amount_a)}</dd>
                    </div>
                    <div>
                      <dt>Sanction Date</dt>
                      <dd>{selectedPair.sanction_date_a || '–'}</dd>
                    </div>
                    <div>
                      <dt>MP Name</dt>
                      <dd>{selectedPair.mp_name || '–'}</dd>
                    </div>
                  </dl>
                  <div className="work-desc-box">
                    <strong>Work Description:</strong>
                    <p>{selectedPair.description_a || 'No description available.'}</p>
                  </div>
                </div>

                {/* Work B Column */}
                <div className="compare-card">
                  <div className="compare-card-header">
                    <h3>Work B</h3>
                    <button
                      type="button"
                      className="btn primary"
                      style={{ fontSize: '13px', padding: '4px 10px' }}
                      onClick={() => openWork(selectedPair.work_id_b)}
                    >
                      View Full Work
                    </button>
                  </div>
                  <dl className="facts">
                    <div>
                      <dt>Work ID</dt>
                      <dd className="id">{selectedPair.work_id_b}</dd>
                    </div>
                    <div>
                      <dt>Category</dt>
                      <dd>{selectedPair.work_category || '–'}</dd>
                    </div>
                    <div>
                      <dt>Sanction Amount</dt>
                      <dd>{inr(selectedPair.sanction_amount_b)}</dd>
                    </div>
                    <div>
                      <dt>Sanction Date</dt>
                      <dd>{selectedPair.sanction_date_b || '–'}</dd>
                    </div>
                    <div>
                      <dt>MP Name</dt>
                      <dd>{selectedPair.mp_name || '–'}</dd>
                    </div>
                  </dl>
                  <div className="work-desc-box">
                    <strong>Work Description:</strong>
                    <p>{selectedPair.description_b || 'No description available.'}</p>
                  </div>
                </div>
              </div>

              {/* Shared Entity / Location Tokens */}
              {selectedPair.shared_entity_tokens && selectedPair.shared_entity_tokens.length > 0 && (
                <div className="tokens-section">
                  <strong>Shared Location &amp; Entity Keywords:</strong>
                  <div className="token-pills">
                    {selectedPair.shared_entity_tokens.map(token => (
                      <span key={token} className="token-pill">
                        {token}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Evidence Reasons List */}
              <div className="reasons-section">
                <strong>Why this pair was flagged:</strong>
                <ul className="reasons-list">
                  {selectedPair.reasons.map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              </div>

              {/* Narrative Explanation */}
              {selectedPair.explanation_text && (
                <div className="explanation-section">
                  <strong>Summary Explanation:</strong>
                  <p className="expl">{selectedPair.explanation_text}</p>
                </div>
              )}

              {/* Neutral Regulatory Disclaimer */}
              <div className="dup-disclaimer" role="note">
                <strong>Regulatory Review Disclaimer:</strong> Potential Duplicate Risk Scores highlight similarity and proximity patterns for human verification. They do not establish that any irregularity or intentional duplication has occurred. An implementing authority or review officer should inspect the physical site, geo-tagging photographs, and sanction records.
              </div>
            </div>

            <footer className="modal-footer">
              <button
                type="button"
                className="btn"
                onClick={() => setSelectedPair(null)}
              >
                Close Inspection
              </button>
            </footer>
          </div>
        </div>
      )}
    </div>
  )
}
