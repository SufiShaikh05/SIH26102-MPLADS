import { useMemo, useRef, useState, type MouseEvent } from 'react'
import { api, type TrendPoint, type TrendResponse } from './api/client'
import { Card, Empty, ErrorBox, Loading, formatPeriod, inr, inrExact, num, useAsync } from './ui'

type ViewMode = 'milestones' | 'expenditure' | 'transactions'

interface SeriesDef {
  key: string
  label: string
  color: string
  getValue: (pt: TrendPoint) => number
  formatValue: (v: number) => string
}

const MILESTONE_SERIES: SeriesDef[] = [
  {
    key: 'recommended',
    label: 'Recommended Works',
    color: '#1d4e89', // Navy / Accent
    getValue: pt => pt.recommended_works,
    formatValue: v => num(v),
  },
  {
    key: 'sanctioned',
    label: 'Sanctioned Works',
    color: '#b54708', // Amber
    getValue: pt => pt.sanctioned_works,
    formatValue: v => num(v),
  },
  {
    key: 'completed',
    label: 'Completed Works',
    color: '#027a48', // Green
    getValue: pt => pt.completed_works,
    formatValue: v => num(v),
  },
]

const EXPENDITURE_SERIES: SeriesDef[] = [
  {
    key: 'total_exp',
    label: 'Total Disbursed',
    color: '#1d4e89',
    getValue: pt => pt.expenditure_amount,
    formatValue: v => inr(v),
  },
  {
    key: 'success_exp',
    label: 'Payment Success',
    color: '#027a48',
    getValue: pt => pt.payment_success_amount,
    formatValue: v => inr(v),
  },
  {
    key: 'in_prog_exp',
    label: 'Payment In-Progress',
    color: '#b54708',
    getValue: pt => pt.payment_in_progress_amount,
    formatValue: v => inr(v),
  },
]

const TRANSACTION_SERIES: SeriesDef[] = [
  {
    key: 'transactions',
    label: 'Expenditure Transactions',
    color: '#1d4e89',
    getValue: pt => pt.expenditure_transactions,
    formatValue: v => num(v),
  },
]

interface ChartProps {
  series: TrendPoint[]
  defs: SeriesDef[]
  isCurrency?: boolean
}

function TrendChart({ series, defs, isCurrency }: ChartProps) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)
  const svgRef = useRef<SVGSVGElement | null>(null)

  const width = 900
  const height = 270
  const paddingLeft = isCurrency ? 80 : 65
  const paddingRight = 25
  const paddingTop = 25
  const paddingBottom = 42

  const plotWidth = width - paddingLeft - paddingRight
  const plotHeight = height - paddingTop - paddingBottom

  const n = series.length

  const maxVal = useMemo(() => {
    let m = 0
    for (const pt of series) {
      for (const d of defs) {
        const val = d.getValue(pt)
        if (val > m) m = val
      }
    }
    return m === 0 ? 10 : m
  }, [series, defs])

  const yMax = maxVal * 1.1

  const getX = (i: number) => {
    if (n <= 1) return paddingLeft + plotWidth / 2
    return paddingLeft + (i / (n - 1)) * plotWidth
  }

  const getY = (val: number) => {
    const clamped = Math.max(0, val)
    return paddingTop + plotHeight - (clamped / yMax) * plotHeight
  }

  const handleMouseMove = (e: MouseEvent<SVGSVGElement>) => {
    if (!svgRef.current || n === 0) return
    const rect = svgRef.current.getBoundingClientRect()
    const mouseX = e.clientX - rect.left
    const svgX = (mouseX / rect.width) * width
    const relX = svgX - paddingLeft
    if (relX < -15 || relX > plotWidth + 15) {
      setHoverIdx(null)
      return
    }
    const idx = Math.min(n - 1, Math.max(0, Math.round((relX / plotWidth) * (n - 1))))
    setHoverIdx(idx)
  }

  const yTicks = [0, yMax * 0.33, yMax * 0.66, yMax]
  const hoveredPoint = hoverIdx !== null && hoverIdx >= 0 && hoverIdx < n ? series[hoverIdx] : null

  return (
    <div className="trend-chart-container">
      <div className="trend-legend">
        {defs.map(d => (
          <span key={d.key} className="trend-legend-item">
            <span className="trend-legend-swatch" style={{ background: d.color }} />
            {d.label}
          </span>
        ))}
      </div>

      <div className="trend-svg-wrap">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${width} ${height}`}
          className="trend-svg"
          onMouseMove={handleMouseMove}
          onMouseLeave={() => setHoverIdx(null)}
          role="img"
          aria-label="Monthly trend chart"
        >
          {/* Horizontal grid lines and Y-axis labels */}
          {yTicks.map((tick, i) => {
            const y = getY(tick)
            return (
              <g key={i}>
                <line
                  x1={paddingLeft}
                  y1={y}
                  x2={paddingLeft + plotWidth}
                  y2={y}
                  stroke="var(--line)"
                  strokeDasharray={i === 0 ? undefined : '3 3'}
                  strokeWidth="1"
                />
                <text
                  x={paddingLeft - 10}
                  y={y + 4}
                  textAnchor="end"
                  fill="var(--muted)"
                  fontSize="12"
                  fontFamily="inherit"
                >
                  {isCurrency ? inr(tick) : num(Math.round(tick))}
                </text>
              </g>
            )
          })}

          {/* X-axis line */}
          <line
            x1={paddingLeft}
            y1={paddingTop + plotHeight}
            x2={paddingLeft + plotWidth}
            y2={paddingTop + plotHeight}
            stroke="var(--line)"
            strokeWidth="1.5"
          />

          {/* X-axis month ticks and labels */}
          {series.map((pt, i) => {
            const x = getX(i)
            // Label every 3rd month, plus the very first and last
            const shouldLabel = i === 0 || i === n - 1 || i % 3 === 0
            return (
              <g key={pt.period}>
                <line
                  x1={x}
                  y1={paddingTop + plotHeight}
                  x2={x}
                  y2={paddingTop + plotHeight + 5}
                  stroke="var(--line)"
                />
                {shouldLabel && (
                  <text
                    x={x}
                    y={paddingTop + plotHeight + 18}
                    textAnchor="middle"
                    fill="var(--muted)"
                    fontSize="11"
                    fontFamily="inherit"
                  >
                    {formatPeriod(pt.period)}
                  </text>
                )}
              </g>
            )
          })}

          {/* Series lines & points */}
          {defs.map(d => {
            const pathData = series
              .map((pt, i) => {
                const x = getX(i)
                const y = getY(d.getValue(pt))
                return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`
              })
              .join(' ')

            return (
              <g key={d.key}>
                <path
                  d={pathData}
                  fill="none"
                  stroke={d.color}
                  strokeWidth="2.2"
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
                {series.map((pt, i) => {
                  const x = getX(i)
                  const y = getY(d.getValue(pt))
                  const isHovered = hoverIdx === i
                  return (
                    <circle
                      key={pt.period}
                      cx={x}
                      cy={y}
                      r={isHovered ? 5.5 : 3}
                      fill={d.color}
                      stroke="#fff"
                      strokeWidth={isHovered ? 2 : 1}
                    />
                  )
                })}
              </g>
            )
          })}

          {/* Hover guideline */}
          {hoverIdx !== null && (
            <line
              x1={getX(hoverIdx)}
              y1={paddingTop}
              x2={getX(hoverIdx)}
              y2={paddingTop + plotHeight}
              stroke="var(--accent)"
              strokeDasharray="4 4"
              strokeWidth="1.5"
              pointerEvents="none"
            />
          )}
        </svg>
      </div>

      {/* Tooltip Card */}
      {hoveredPoint && (
        <div className="trend-tooltip-card">
          <div className="trend-tooltip-header">
            <strong>{formatPeriod(hoveredPoint.period)}</strong> ({hoveredPoint.period})
          </div>
          <div className="trend-tooltip-body">
            {defs.map(d => {
              const val = d.getValue(hoveredPoint)
              return (
                <div key={d.key} className="trend-tooltip-row">
                  <span className="trend-tooltip-name">
                    <span className="trend-legend-swatch" style={{ background: d.color }} />
                    {d.label}:
                  </span>
                  <strong className="trend-tooltip-val">
                    {d.formatValue(val)}
                    {isCurrency && val > 0 && (
                      <span className="trend-tooltip-exact"> ({inrExact(val)})</span>
                    )}
                  </strong>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

export default function ImplementationTrends() {
  const [selectedState, setSelectedState] = useState<string>('')
  const [viewMode, setViewMode] = useState<ViewMode>('milestones')

  const states = useAsync(api.states, [])
  const trends = useAsync(() => api.trends(selectedState), [selectedState])

  const data: TrendResponse | undefined = trends.data

  const totals = useMemo(() => {
    if (!data?.series?.length) return null
    return data.series.reduce(
      (acc, pt) => ({
        recommended: acc.recommended + pt.recommended_works,
        sanctioned: acc.sanctioned + pt.sanctioned_works,
        completed: acc.completed + pt.completed_works,
        transactions: acc.transactions + pt.expenditure_transactions,
        expenditure: acc.expenditure + pt.expenditure_amount,
      }),
      { recommended: 0, sanctioned: 0, completed: 0, transactions: 0, expenditure: 0 }
    )
  }, [data])

  const scopeLabel = selectedState || 'All India'
  const startLabel = data?.start_period ? formatPeriod(data.start_period) : '–'
  const endLabel = data?.end_period ? formatPeriod(data.end_period) : '–'

  const activeDefs =
    viewMode === 'milestones'
      ? MILESTONE_SERIES
      : viewMode === 'expenditure'
      ? EXPENDITURE_SERIES
      : TRANSACTION_SERIES

  return (
    <Card
      title="Implementation Trends"
      note={
        data?.start_period && data?.end_period
          ? `${scopeLabel} • ${startLabel} to ${endLabel} (${data.series.length} months)`
          : scopeLabel
      }
    >
      <div className="trend-controls">
        <div className="trend-tabs" role="tablist" aria-label="Trend View Mode">
          <button
            type="button"
            role="tab"
            aria-selected={viewMode === 'milestones'}
            className={`btn ${viewMode === 'milestones' ? 'primary' : ''}`}
            onClick={() => setViewMode('milestones')}
          >
            Work Milestones
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={viewMode === 'expenditure'}
            className={`btn ${viewMode === 'expenditure' ? 'primary' : ''}`}
            onClick={() => setViewMode('expenditure')}
          >
            Expenditure (₹)
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={viewMode === 'transactions'}
            className={`btn ${viewMode === 'transactions' ? 'primary' : ''}`}
            onClick={() => setViewMode('transactions')}
          >
            Transactions
          </button>
        </div>

        <div className="trend-scope-select">
          <label htmlFor="trend-state-select" className="muted" style={{ marginRight: '6px' }}>
            Scope:
          </label>
          <select
            id="trend-state-select"
            aria-label="Trend State Scope"
            value={selectedState}
            onChange={e => setSelectedState(e.target.value)}
          >
            <option value="">All India (National)</option>
            {states.data?.map(s => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Scope Headline Metric Cards */}
      {totals && (
        <div className="cards trend-cards">
          <div className="stat">
            <span>Recommended Works</span>
            <strong>{num(totals.recommended)}</strong>
          </div>
          <div className="stat">
            <span>Sanctioned Works</span>
            <strong>{num(totals.sanctioned)}</strong>
          </div>
          <div className="stat">
            <span>Completed Works</span>
            <strong>{num(totals.completed)}</strong>
          </div>
          <div className="stat">
            <span>Transactions</span>
            <strong>{num(totals.transactions)}</strong>
          </div>
          <div className="stat key">
            <span>Total Disbursed</span>
            <strong>{inr(totals.expenditure)}</strong>
          </div>
        </div>
      )}

      {/* Main Chart Body / States */}
      {trends.loading && !trends.data ? (
        <Loading text="Loading implementation trends…" />
      ) : trends.error ? (
        <ErrorBox message={trends.error} retry={trends.retry} />
      ) : !data || data.series.length === 0 ? (
        <Empty>
          No trend data available for {scopeLabel}. Select another state or All India.
        </Empty>
      ) : (
        <TrendChart
          series={data.series}
          defs={activeDefs}
          isCurrency={viewMode === 'expenditure'}
        />
      )}
    </Card>
  )
}
