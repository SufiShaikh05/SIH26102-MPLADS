import { useState, type FormEvent } from 'react'
import { api, sortParam, type Query } from './api/client'
import { Bars, Card, Chip, Empty, ErrorBox, Loading, PRIORITY_LABELS, inr, num, pct, priorityRank, tone, useAsync } from './ui'

const PAGE_SIZE = 15
const COLS: { key: string; label: string; sort?: string; right?: boolean }[] = [
  { key: 'review_priority_label', label: 'Priority' }, { key: 'score', label: 'Score', sort: 'review_priority_score', right: true },
  { key: 'work_id', label: 'Work ID' }, { key: 'state', label: 'State' }, { key: 'work_category', label: 'Work Category' },
  { key: 'sanction', label: 'Sanction Amount', sort: 'sanction_amount', right: true }, { key: 'util', label: 'Utilization', sort: 'success_utilization_ratio', right: true },
  { key: 'pay', label: 'Payments', sort: 'payment_count', right: true }, { key: 'ven', label: 'Vendors', sort: 'vendor_count', right: true },
  { key: 'dup', label: 'Duplicate Ratio', sort: 'duplicate_ratio', right: true }, { key: 'status', label: 'Status' },
]

function Overview() {
  const s = useAsync(api.summary, [])
  if (s.loading && !s.data) return <Loading text="Loading overview…" />
  if (s.error) return <ErrorBox message={s.error} retry={s.retry} />
  const d = s.data!
  const cards: [string, number][] = [['Total Works', d.total_works], ['Works with Expenditure', d.works_with_expenditure], ['Completed Works', d.completed_works], ['Review Candidates', d.review_candidates]]
  const raw = d.label_distribution ?? {}
  // All four labels, most severe first (zero when absent); any unrecognised labels are appended.
  const dist: [string, number][] = Object.keys(raw).length
    ? [...[...PRIORITY_LABELS].reverse().map(l => [l, raw[l] ?? 0] as [string, number]), ...Object.entries(raw).filter(([l]) => priorityRank(l) < 0)]
    : []
  const states = [...(d.state_breakdown ?? [])].sort((a, b) => b.review_candidates - a.review_candidates).slice(0, 8)
  return (
    <>
      <div className="cards">{cards.map(([l, v], i) => <div key={l} className={`stat ${i === 3 ? 'key' : ''}`}><span>{l}</span><strong>{num(v)}</strong></div>)}</div>
      <div className="grid2">
        <Card title="Review Priority Distribution" note="Review candidates by label">
          {dist.length ? <Bars wide rows={dist.map(([l, v]) => ({ label: l, value: v, tone: tone(l) }))} /> : <Empty>The summary endpoint did not include a label distribution.</Empty>}
        </Card>
        <Card title="State-wise Review Candidates" note="Top 8 states">
          {states.length ? <Bars rows={states.map(x => ({ label: x.state, value: x.review_candidates, note: `of ${num(x.works)} works` }))} /> : <Empty>The summary endpoint did not include a state breakdown.</Empty>}
        </Card>
      </div>
    </>
  )
}

export default function Dashboard({ open }: { open: (id: string) => void }) {
  const [f, setF] = useState({ label: '', state: '', work_category: '', min_score: '' })
  const [page, setPage] = useState(1)
  const [sort, setSort] = useState({ field: 'review_priority_score', dir: 'desc' as 'asc' | 'desc' })
  const [search, setSearch] = useState('')
  const [searchMsg, setSearchMsg] = useState('')
  const states = useAsync(api.states, []), cats = useAsync(api.categories, [])
  const q: Query = { page, page_size: PAGE_SIZE, label: f.label || undefined, state: f.state || undefined, work_category: f.work_category || undefined,
    min_score: f.min_score === '' ? undefined : Number(f.min_score), sort: sortParam(sort.field, sort.dir) }
  const list = useAsync(() => api.anomalies(q), [JSON.stringify(q)])
  const set = (k: keyof typeof f, v: string) => { setF({ ...f, [k]: v }); setPage(1) }
  const filtered = Object.values(f).some(Boolean)
  const pages = Math.max(1, Math.ceil((list.data?.total ?? 0) / PAGE_SIZE))

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const id = search.trim(); if (!id) return
    setSearchMsg('')
    try { await api.anomaly(id); open(id) }
    catch (err) { setSearchMsg((err as { status?: number }).status === 404 ? `No work found with ID "${id}".` : (err as Error).message) }
  }
  const clickSort = (field: string) => { setSort(s => ({ field, dir: s.field === field && s.dir === 'desc' ? 'asc' : 'desc' })); setPage(1) }

  return (
    <>
      <Overview />
      <Card title="Review Candidates" note="Highest Review Priority first by default">
        <div className="filters">
          <form onSubmit={submit} className="search">
            <input value={search} onChange={e => { setSearch(e.target.value); setSearchMsg('') }} placeholder="Search by Work ID" aria-label="Search by Work ID" />
            <button className="btn primary" type="submit">Find work</button>
          </form>
          <select aria-label="State" value={f.state} onChange={e => set('state', e.target.value)}><option value="">All states</option>{states.data?.map(s => <option key={s}>{s}</option>)}</select>
          <select aria-label="Work category" value={f.work_category} onChange={e => set('work_category', e.target.value)}><option value="">All categories</option>{cats.data?.map(s => <option key={s}>{s}</option>)}</select>
          <select aria-label="Priority" value={f.label} onChange={e => set('label', e.target.value)}><option value="">All priorities</option>{PRIORITY_LABELS.map(l => <option key={l}>{l}</option>)}</select>
          <input className="score" type="number" min={0} aria-label="Minimum score" placeholder="Min score" value={f.min_score} onChange={e => set('min_score', e.target.value)} />
          {filtered && <button className="btn" onClick={() => { setF({ label: '', state: '', work_category: '', min_score: '' }); setPage(1) }}>Clear filters</button>}
        </div>
        {searchMsg && <div className="inline-msg" role="alert">{searchMsg}</div>}
        {list.error ? <ErrorBox message={list.error} retry={list.retry} /> : (
          <div className="tablewrap" aria-busy={list.loading}>
            <table>
              <thead><tr>{COLS.map(c => (
                <th key={c.key} className={c.right ? 'r' : ''} aria-sort={c.sort && sort.field === c.sort ? (sort.dir === 'desc' ? 'descending' : 'ascending') : undefined}>
                  {c.sort ? <button className="sortbtn" onClick={() => clickSort(c.sort!)}>{c.label}<span>{sort.field === c.sort ? (sort.dir === 'desc' ? ' ▼' : ' ▲') : ''}</span></button> : c.label}
                </th>))}</tr></thead>
              <tbody>
                {list.data?.items.map(w => (
                  <tr key={w.work_id} tabIndex={0} onClick={() => open(w.work_id)} onKeyDown={e => e.key === 'Enter' && open(w.work_id)}>
                    <td><Chip label={w.review_priority_label} /></td><td className="r score-cell">{w.review_priority_score}</td>
                    <td className="id">{w.work_id}</td><td>{w.state}</td><td>{w.work_category}</td><td className="r">{inr(w.sanction_amount)}</td>
                    <td className="r">{pct(w.success_utilization_ratio)}</td><td className="r">{num(w.payment_count)}</td><td className="r">{num(w.vendor_count)}</td>
                    <td className="r">{pct(w.duplicate_ratio)}</td><td>{w.work_status}</td>
                  </tr>))}
              </tbody>
            </table>
            {list.loading && !list.data && <Loading text="Loading review candidates…" />}
            {!list.loading && list.data?.items.length === 0 && <Empty>{filtered ? 'No works match these filters. Clear a filter to widen the results.' : 'No review candidates are available yet. Check that the anomaly output has been generated.'}</Empty>}
          </div>
        )}
        {list.data && list.data.total > 0 && (
          <div className="pager">
            <span className="muted">Page {page} of {pages}, {num(list.data.total)} works</span>
            <button className="btn" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
            <button className="btn" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
          </div>
        )}
      </Card>
    </>
  )
}
