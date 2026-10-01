import { useEffect, useState, type ReactNode } from 'react'

export const inr = (n?: number | null) => {
  if (n == null || isNaN(n)) return '–'
  if (Math.abs(n) >= 1e7) return `₹${(n / 1e7).toFixed(2)} Cr`
  if (Math.abs(n) >= 1e5) return `₹${(n / 1e5).toFixed(2)} L`
  return `₹${Math.round(n).toLocaleString('en-IN')}`
}
export const inrExact = (n?: number | null) => {
  if (n == null || isNaN(n)) return '–'
  return `₹${n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}
export const formatPeriod = (p?: string | null) => {
  if (!p) return '–'
  const [y, m] = p.split('-')
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  const mi = parseInt(m, 10) - 1
  return months[mi] ? `${months[mi]} ${y}` : p
}
export const pct = (r?: number | null) => (r == null || isNaN(r) ? '–' : `${(r * 100).toFixed(0)}%`)
export const num = (n?: number | null) => (n == null || isNaN(n) ? '–' : n.toLocaleString('en-IN'))
export const signalText = (s?: string | null) => (s ? s.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase()) : '')

// Exact labels emitted by the anomaly engine, least to most severe.
export const PRIORITY_LABELS = ['Normal Monitoring', 'Low Review Priority', 'Medium Review Priority', 'High Review Priority']
export const priorityRank = (l: string) => PRIORITY_LABELS.findIndex(x => x.toLowerCase() === l.toLowerCase()) // -1 = unknown
export const tone = (l: string) => l.toLowerCase().split(' ')[0] // normal | low | medium | high (CSS hook)

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [s, set] = useState<{ data?: T; error?: string; loading: boolean }>({ loading: true })
  const [n, setN] = useState(0)
  useEffect(() => {
    let live = true
    set(p => ({ data: p.data, loading: true }))
    fn().then(data => live && set({ data, loading: false }), (e: Error) => live && set({ loading: false, error: e.message }))
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, n])
  return { ...s, retry: () => setN(x => x + 1) }
}

export const Chip = ({ label }: { label: string }) => <span className={`chip ${tone(label)}`}>{label}</span>
export const Loading = ({ text = 'Loading…' }: { text?: string }) => <div className="state" role="status"><span className="spinner" />{text}</div>
export const Empty = ({ children }: { children: ReactNode }) => <div className="state empty">{children}</div>
export const ErrorBox = ({ message, retry }: { message: string; retry: () => void }) => (
  <div className="state error" role="alert"><strong>Could not load data.</strong> {message}<button className="btn" onClick={retry}>Try again</button></div>
)
export const Card = ({ title, children, note }: { title: string; children: ReactNode; note?: string }) => (
  <section className="panel"><header><h2>{title}</h2>{note && <span className="muted">{note}</span>}</header>{children}</section>
)
export function Bars({ rows, wide }: { rows: { label: string; value: number; note?: string; tone?: string }[]; wide?: boolean }) {
  const max = Math.max(1, ...rows.map(r => r.value))
  return (
    <ul className={`bars${wide ? ' wide' : ''}`}>
      {rows.map(r => (
        <li key={r.label}>
          <span className="bl">{r.label}</span>
          <span className="track"><span className={`fill ${r.tone ?? ''}`} style={{ width: `${(r.value / max) * 100}%` }} /></span>
          <span className="bv">{num(r.value)}{r.note && <small> {r.note}</small>}</span>
        </li>
      ))}
    </ul>
  )
}
