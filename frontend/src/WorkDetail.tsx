import { api, type Anomaly } from './api/client'
import { Card, Chip, ErrorBox, Loading, inr, num, pct, signalText, useAsync } from './ui'

export default function WorkDetail({ id, back }: { id: string; back: () => void }) {
  const r = useAsync(async () => {
    const [a, w] = await Promise.all([api.anomaly(id), api.work(id).catch(() => ({}))])
    return { ...w, ...a } as Anomaly // anomaly record wins; /works adds any extra fields
  }, [id])
  const w = r.data
  const signals = w ? [w.top_signal_1, w.top_signal_2, w.top_signal_3].filter(Boolean) as string[] : []
  const facts: [string, string][] = w ? [
    ['Work ID', w.work_id], ['State', w.state], ['Constituency', w.constituency], ['MP', w.mp_name], ['Implementing authority', w.ida],
    ['Work category', w.work_category], ['Work status', w.work_status], ['Sanction amount', inr(w.sanction_amount)],
    ['Total expenditure (all rows)', inr(w.total_disbursed_all_rows)], ['Deduplicated expenditure', inr(w.deduplicated_disbursed_amount)],
    ['Utilization', pct(w.success_utilization_ratio)], ['Payment count', num(w.payment_count)], ['Vendor count', num(w.vendor_count)],
    ['Duplicate ratio', pct(w.duplicate_ratio)], ['Days since sanction', num(w.days_since_sanction)], ['Days since last payment', num(w.days_since_last_payment)],
  ] : []
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
        </>
      )}
    </div>
  )
}
