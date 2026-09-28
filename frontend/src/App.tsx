import { useEffect, useState } from 'react'
import Dashboard from './Dashboard'
import WorkDetail from './WorkDetail'
import { API_BASE, isMock } from './api/client'

const parse = () => { const m = location.hash.match(/^#\/work\/(.+)$/); return m ? decodeURIComponent(m[1]) : null }

export default function App() {
  const [id, setId] = useState(parse())
  useEffect(() => {
    const f = () => { setId(parse()); window.scrollTo(0, 0) }
    addEventListener('hashchange', f); return () => removeEventListener('hashchange', f)
  }, [])
  const open = (w: string) => { location.hash = `#/work/${encodeURIComponent(w)}` }
  return (
    <>
      {isMock && <div className="mockbar">Mock data mode: figures below are synthetic and not from the MPLADS backend.</div>}
      <header className="top">
        <div className="wrap">
          <h1>MPLADS AI Monitoring Dashboard</h1>
          <p>Data-driven anomaly and implementation monitoring</p>
        </div>
      </header>
      <main className="wrap">
        {/* Dashboard stays mounted so filters survive a visit to the detail view */}
        <div hidden={!!id}><Dashboard open={open} /></div>
        {id && <WorkDetail id={id} back={() => { location.hash = '' }} />}
      </main>
      <footer className="wrap foot">
        Review Priority scores highlight unusual patterns for verification. They are not proof of wrongdoing.
        {!isMock && <span> Data source: {API_BASE}</span>}
      </footer>
    </>
  )
}
