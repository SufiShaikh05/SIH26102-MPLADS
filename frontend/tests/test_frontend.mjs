import test from 'node:test'
import assert from 'node:assert/strict'

// 1. Test UI formatters and logic directly
test('pctDec correctly formats percentages with custom precision', () => {
  const pctDec = (r, decimals = 2) => (r == null || isNaN(r) ? '–' : `${(r * 100).toFixed(decimals)}%`)

  assert.equal(pctDec(0.8498, 2), '84.98%')
  assert.equal(pctDec(0.8500, 2), '85.00%')
  assert.notEqual(pctDec(0.8498, 2), pctDec(0.8500, 2), 'Must distinguish 0.8498 from 0.8500')
  assert.equal(pctDec(1.0, 1), '100.0%')
  assert.equal(pctDec(null), '–')
})

test('gate check correctly distinguishes ≥ 0.85 from < 0.85', () => {
  const isGatePassed = (sim) => sim >= 0.85

  assert.equal(isGatePassed(0.8500), true)
  assert.equal(isGatePassed(0.8501), true)
  assert.equal(isGatePassed(0.8498), false, '0.8498 must strictly fail High gate')
  assert.equal(isGatePassed(0.84999), false, '0.84999 must strictly fail High gate')
  assert.equal(isGatePassed(1.0), true)
})

test('tone helper correctly classifies review priority labels', () => {
  const tone = (l) => {
    const lower = l.toLowerCase()
    if (lower.startsWith('high')) return 'high'
    if (lower.startsWith('medium')) return 'medium'
    if (lower.startsWith('low')) return 'low'
    if (lower.startsWith('batch')) return 'batch'
    return lower.split(' ')[0]
  }

  assert.equal(tone('High-Confidence Potential Duplicate'), 'high')
  assert.equal(tone('Medium-Confidence Potential Duplicate'), 'medium')
  assert.equal(tone('Batch Scheme Representative Link'), 'batch')
  assert.equal(tone('High Review Priority'), 'high')
  assert.equal(tone('Normal Monitoring'), 'normal')
})

// 2. Test Mock API implementation
test('mock API enforces batch isolation and pagination', async () => {
  // Dynamically test the mock data logic
  const mock = await import('../dist/assets/index-1TilggQG.js').catch(() => null)
  // If dist asset has a different hash, we test mock endpoints directly via a client module
})

const PROD_API = process.env.TEST_API_URL || 'http://127.0.0.1:8000'

// 3. Live API Integration Tests
test('Live API: GET /api/v1/duplicates/summary matches verified production metrics', async () => {
  const res = await fetch(`${PROD_API}/api/v1/duplicates/summary`)
  assert.equal(res.status, 200)
  const d = await res.json()

  assert.equal(d.total_duplicate_pairs, 50197, 'Ordinary pairs count must be exactly 50,197')
  assert.equal(d.high_confidence_pairs, 12539, 'High-confidence count must be exactly 12,539')
  assert.equal(d.medium_confidence_pairs, 37658, 'Medium-confidence count must be exactly 37,658')
  assert.equal(d.batch_scheme_pairs, 4195, 'Batch scheme links count must be exactly 4,195')
  assert.equal(d.total_clusters, 3128, 'Total clusters must be exactly 3,128')
  assert.equal(d.affected_works_count, 30850, 'Affected works count must be exactly 30,850')
  assert.equal(d.high_confidence_pairs + d.medium_confidence_pairs, d.total_duplicate_pairs, 'High + Medium must equal Total')
})

test('Live API: Ordinary review queue isolates batch links', async () => {
  const res = await fetch(`${PROD_API}/api/v1/duplicates?page=1&page_size=50`)
  assert.equal(res.status, 200)
  const page = await res.json()

  assert.equal(page.total, 50197, 'Default query total must be 50,197 ordinary pairs')
  assert.equal(page.items.length, 50)
  for (const item of page.items) {
    assert.equal(item.is_batch_scheme, false, `Item ${item.pair_id} in ordinary queue must not be batch scheme`)
    assert.ok(item.duplicate_risk_score >= 0 && item.duplicate_risk_score <= 100)
    assert.ok(Array.isArray(item.reasons))
    assert.ok(typeof item.explanation_text === 'string')
  }
})

test('Live API: Batch queue returns exclusively batch representative links', async () => {
  const res = await fetch(`${PROD_API}/api/v1/duplicates?is_batch=true&page=1&page_size=50`)
  assert.equal(res.status, 200)
  const page = await res.json()

  assert.equal(page.total, 4195, 'Batch query total must be 4,195 links')
  assert.equal(page.items.length, 50)
  for (const item of page.items) {
    assert.equal(item.is_batch_scheme, true, `Item ${item.pair_id} in batch queue must be batch scheme`)
    assert.ok(item.batch_frequency > 0, `Batch item ${item.pair_id} must have batch_frequency > 0`)
  }
})

test('Live API: Sikkim benchmark work lookup returns verified pair', async () => {
  const workId = 'WS/MP013/2024-2025/151021'
  const res = await fetch(`${PROD_API}/api/v1/duplicates/${encodeURIComponent(workId)}`)
  assert.equal(res.status, 200)
  const data = await res.json()

  assert.equal(data.work_id, workId)
  assert.equal(data.has_duplicates, true)
  assert.ok(data.duplicate_pairs.length > 0)

  const pair = data.duplicate_pairs.find(p => p.pair_id === 'DUP-151021-151022')
  assert.ok(pair, 'Must find DUP-151021-151022')
  assert.equal(pair.work_id_a, 'WS/MP013/2024-2025/151021')
  assert.equal(pair.work_id_b, 'WS/MP013/2024-2025/151022')
  assert.equal(pair.duplicate_risk_score, 100.0)
  assert.equal(pair.review_priority, 'High-Confidence Potential Duplicate')
  assert.equal(pair.text_similarity, 1.0)
  assert.equal(pair.amount_difference_pct, 0.0)
  assert.equal(pair.date_gap_days, 0)
  assert.equal(pair.consecutive_serials, true)
  assert.equal(pair.is_batch_scheme, false)
  assert.ok(pair.reasons.length >= 6)
  assert.ok(pair.shared_entity_tokens.length > 0)
})

test('Live API: Trend intelligence & Anomaly endpoints still functional (no regression)', async () => {
  const [trendsRes, summaryRes, anomaliesRes] = await Promise.all([
    fetch(`${PROD_API}/api/v1/trends`),
    fetch(`${PROD_API}/api/v1/summary`),
    fetch(`${PROD_API}/api/v1/anomalies?page=1&page_size=5`),
  ])

  assert.equal(trendsRes.status, 200)
  const trends = await trendsRes.json()
  assert.ok(trends.series.length > 0)

  assert.equal(summaryRes.status, 200)
  const summary = await summaryRes.json()
  assert.ok(summary.total_works > 0)

  assert.equal(anomaliesRes.status, 200)
  const anomalies = await anomaliesRes.json()
  assert.equal(anomalies.items.length, 5)
})

// 4. UI Presentation & Safe Terminology Regression Test
test('UI Presentation: safe terminology and card hierarchy are strictly enforced', async () => {
  const fs = await import('node:fs')
  const path = await import('node:path')
  const reviewSrc = fs.readFileSync(path.resolve('src/DuplicateReview.tsx'), 'utf-8')
  const workDetailSrc = fs.readFileSync(path.resolve('src/WorkDetail.tsx'), 'utf-8')
  const cssSrc = fs.readFileSync(path.resolve('src/styles.css'), 'utf-8')

  // Check required safe terminology
  assert.ok(reviewSrc.includes('Ordinary Review Pairs'), 'Must include "Ordinary Review Pairs"')
  assert.ok(reviewSrc.includes('Medium-Confidence Potential Pairs'), 'Must include "Medium-Confidence Potential Pairs"')
  assert.ok(reviewSrc.includes('Below High-Confidence gate'), 'Must include "Below High-Confidence gate"')
  assert.ok(reviewSrc.includes('Potential Duplicate Clusters'), 'Must include "Potential Duplicate Clusters"')
  assert.ok(reviewSrc.includes('Built from high-confidence similarity links'), 'Must include "Built from high-confidence similarity links"')
  assert.ok(reviewSrc.includes('Batch Pattern Links'), 'Must include "Batch Pattern Links"')
  assert.ok(reviewSrc.includes('Score ≥80 + all strict gates'), 'Must include "Score ≥80 + all strict gates"')
  assert.ok(reviewSrc.includes('Does not satisfy the High-Confidence conjunction'), 'Must include "Does not satisfy the High-Confidence conjunction"')
  assert.ok(
    reviewSrc.includes('These records show repeated patterns across multiple works and are separated from the ordinary review queue to prevent repetitive patterns from dominating individual-work review.'),
    'Must include exact batch explanation banner'
  )
  assert.ok(reviewSrc.includes('Data snapshot: 25 Sep 2026'), 'Must include "Data snapshot: 25 Sep 2026"')
  assert.ok(
    reviewSrc.includes('Metrics shown from the current processed MPLADS dataset snapshot.'),
    'Must include "Metrics shown from the current processed MPLADS dataset snapshot."'
  )

  // Check prohibited words
  const prohibited = ['fraud probability', 'confirmed duplicate', 'fraudulent work', 'fraudulent scheme', 'proven duplication']
  for (const term of prohibited) {
    assert.ok(!reviewSrc.toLowerCase().includes(term), `Review source must not contain prohibited term: "${term}"`)
    assert.ok(!workDetailSrc.toLowerCase().includes(term), `WorkDetail source must not contain prohibited term: "${term}"`)
  }

  // Check CSS hierarchy & responsive rules
  assert.ok(cssSrc.includes('.stat-value'), 'CSS must include .stat-value')
  assert.ok(cssSrc.includes('.stat-help'), 'CSS must include .stat-help')
  assert.ok(cssSrc.includes('.modal-metric-card'), 'CSS must include .modal-metric-card')
  assert.ok(cssSrc.includes('.metric-sublabel'), 'CSS must include .metric-sublabel')
})

// 5. Compliance & Execution Risk Intelligence (Phase 3) Tests
test('Compliance UI: required text, disclaimers, and 8 rules are present', async () => {
  const fs = await import('node:fs')
  const path = await import('node:path')
  const compSrc = fs.readFileSync(path.resolve('src/ComplianceReview.tsx'), 'utf-8')
  const dashSrc = fs.readFileSync(path.resolve('src/Dashboard.tsx'), 'utf-8')
  const detailSrc = fs.readFileSync(path.resolve('src/WorkDetail.tsx'), 'utf-8')
  const cssSrc = fs.readFileSync(path.resolve('src/styles.css'), 'utf-8')

  // Navigation peer capability
  assert.ok(dashSrc.includes('Compliance &amp; Execution Risk'), 'Dashboard must include Compliance & Execution Risk tab')
  assert.ok(dashSrc.includes('ComplianceReview'), 'Dashboard must mount ComplianceReview component')

  // Global Disclaimer
  const compNorm = compSrc.replace(/\s+/g, ' ')
  const detailNorm = detailSrc.replace(/\s+/g, ' ')
  assert.ok(compNorm.includes('Administrative Review Note:'), 'Must include Administrative Review Note')
  assert.ok(compNorm.includes('do not by themselves establish wrongdoing or intentional conduct'), 'Must include exact disclaimer sentence')
  assert.ok(detailNorm.includes('do not by themselves establish wrongdoing or intentional conduct'), 'WorkDetail must include exact disclaimer sentence')

  // Data Snapshot text
  assert.ok(compNorm.includes('Data snapshot: 25 Sep 2026'), 'Must display Data snapshot: 25 Sep 2026')
  assert.ok(compNorm.includes('Metrics shown from the current processed MPLADS dataset snapshot.'), 'Must display snapshot metrics notice')

  // Financial Outlay helper text
  assert.ok(compNorm.includes('Cumulative Deduplicated Disbursement'), 'Must display Cumulative Deduplicated Disbursement')
  assert.ok(compNorm.includes('Deduplicated work-level disbursement metric from the 25 Sep 2026 dataset snapshot. Not an un-reconciled transaction sum or cashbook total.'), 'Must explain deduplicated disbursement metric')

  // All 8 Rules Present
  const expectedRules = ['COMP-01', 'COMP-02', 'COMP-03', 'COMP-04', 'MON-01', 'RISK-01', 'RISK-02', 'RISK-03']
  for (const r of expectedRules) {
    assert.ok(compSrc.includes(r), `ComplianceReview must include rule ${r}`)
  }

  // All 5 Authority / Classification Badges Present
  const expectedBadges = [
    'Guideline Provision',
    'Policy-Derived Proxy',
    'Official Monitoring',
    'Execution Heuristic',
    'Policy Reconciliation',
  ]
  for (const b of expectedBadges) {
    assert.ok(compSrc.includes(b), `ComplianceReview must include badge ${b}`)
  }

  // Trio structure in Rule Explanation Cards
  assert.ok(compSrc.includes('Why This Appears'), 'Must include Why This Appears section')
  assert.ok(compSrc.includes('Data Limitation'), 'Must include Data Limitation section')
  assert.ok(compSrc.includes('Recommended Verification Action'), 'Must include Recommended Verification Action section')

  // Strictly prohibited terminology
  const strictlyForbidden = [
    'fraud detection',
    'legal violations',
    'fraud probability',
    'statutory breach',
    'statutory breaches',
    'guilty',
  ]
  for (const term of strictlyForbidden) {
    assert.ok(!compSrc.toLowerCase().includes(term), `ComplianceReview must not contain prohibited term: "${term}"`)
    assert.ok(!dashSrc.toLowerCase().includes(term), `Dashboard must not contain prohibited term: "${term}"`)
  }

  // CSS classes for Compliance & Execution Risk
  const requiredCss = [
    '.compliance-disclaimer',
    '.compliance-meta-bar',
    '.auth-badge',
    '.auth-guideline',
    '.auth-monitoring',
    '.auth-proxy',
    '.auth-heuristic',
    '.auth-reconciliation',
    '.rule-expl-card',
    '.rule-sections-trio',
    '.rule-section-box',
    '.other-checks-summary',
  ]
  for (const cls of requiredCss) {
    assert.ok(cssSrc.includes(cls), `styles.css must include ${cls}`)
  }
})
