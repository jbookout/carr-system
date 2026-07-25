export const meta = {
  name: 'health-audit',
  description: 'Monthly system health audit: 12 report-card categories graded in parallel, fresh-context verified, merged',
  whenToUse: 'Monthly audit (the 4th) or an on-demand re-score. Local Claude Code sessions only. Source of truth for the grading rubric stays 00_Context/system-report-card-2026-07-07.md; this graph runs its v3 prompt wider, not differently.',
  phases: [
    { title: 'Measure', detail: 'FINAL-baseline metrics re-measured (T1 Haiku)', model: 'haiku' },
    { title: 'Grade', detail: 'one grader per category, independent, no anchoring (T2 Sonnet)', model: 'sonnet' },
    { title: 'Verify', detail: 'fresh-context skeptic per grade — refute or confirm (top seat)' },
    { title: 'Synthesize', detail: 'merge, overall score, top-5 corrective actions' },
  ],
}

const ARGS = (() => { try { return typeof args === 'string' ? JSON.parse(args) : (args || {}) } catch { return {} } })()

// Graph-engineering build 2026-07-25. Design rules honored here:
//  - graders never see each other or prior scores (the no-anchoring rule, now structural)
//  - verifiers get FRESH context and judge evidence, not the grader's claim (fan-out hygiene rule 1)
//  - scoped: 12 + 12 + 2 agents, known cost shape, no unbounded loops (fan-out hygiene rule 2)
//  - the workflow WRITES NOTHING. Score-history columns, decision-history, open-loops are
//    top-seat writes the invoking session performs after reading this result (model-tiering hard floor).
// args: { vault: string (CARR AI absolute path), today: 'YYYY-MM-DD' } — timestamps come in via args.

const VAULT = (ARGS && ARGS.vault) ||
  '/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI'
const TODAY = (ARGS && ARGS.today) || 'UNDATED-pass-args.today'
const CARD = `${VAULT}/00_Context/system-report-card-2026-07-07.md`

const CATEGORIES = [
  'Knowledge capture & ingestion', 'Architecture & information design',
  'Automation reliability & safety', 'Governance & self-maintenance',
  'Token efficiency', 'Content & marketing system', 'Deal execution capability',
  'Vendor/referral network', 'Team scalability (Dell)', 'Data quality',
  'Pipeline & lead generation', 'Proven business outcomes',
]

const GRADE = { type: 'object', required: ['category', 'score', 'evidence', 'gaps'], properties: {
  category: { type: 'string' }, score: { type: 'number', minimum: 0, maximum: 100 },
  evidence: { type: 'array', items: { type: 'string' }, description: 'concrete observations with file paths / counts backing the score' },
  gaps: { type: 'array', items: { type: 'string' } } } }

const VERDICT = { type: 'object', required: ['upheld', 'adjustedScore', 'reason'], properties: {
  upheld: { type: 'boolean' }, adjustedScore: { type: 'number', minimum: 0, maximum: 100 },
  reason: { type: 'string' } } }

phase('Measure')
const metrics = await agent(
  `Re-measure the FINAL-baseline metrics for the CARR AI monthly audit. Read the "monthly audit prompt" and FINAL metrics table in "${CARD}" and re-measure each metric with the SAME method it names (file sizes, row counts from the named xlsx/md files under "${VAULT}", ledger counts). Measure, don't estimate; report "unmeasurable: <why>" honestly where a method needs something this environment lacks. Return one line per metric: name | method | value.`,
  { label: 'measure:baselines', model: 'haiku', effort: 'low' })

phase('Grade')
const graded = await pipeline(
  CATEGORIES,
  cat => agent(
    `You are grading ONE category of the CARR AI system report card, independently — you have NO access to prior scores and must not guess them. Category: "${cat}". Read the rubric for this category in "${CARD}" (and ONLY what that rubric points you to under "${VAULT}"). Fresh measurements you may trust:\n${metrics}\nGrade 0-100 with concrete evidence.`,
    { label: `grade:${cat}`, phase: 'Grade', model: 'sonnet', schema: GRADE }),
  (g, cat) => g && agent(
    `Fresh-context audit verification. A grader scored the CARR AI category "${cat}" at ${g.score}/100 citing: ${JSON.stringify(g.evidence)}. You have NOT seen its reasoning and owe it nothing. Independently spot-check the citations against the real files under "${VAULT}" (rubric: "${CARD}"). Refute the score if evidence is wrong, stale, or cherry-picked; uphold only what checks out. Return your own adjustedScore either way.`,
    { label: `verify:${cat}`, phase: 'Verify', schema: VERDICT }
  ).then(v => ({ ...g, verdict: v }))
)

phase('Synthesize')
const clean = graded.filter(Boolean)
const rows = clean.map(g => ({
  category: g.category,
  score: g.verdict && !g.verdict.upheld ? g.verdict.adjustedScore : g.score,
  graderScore: g.score, upheld: g.verdict ? g.verdict.upheld : null,
  verifierReason: g.verdict ? g.verdict.reason : 'verifier missing',
  evidence: g.evidence, gaps: g.gaps,
}))
const summary = await agent(
  `Synthesize the ${TODAY} CARR AI health-audit verdict from these verified category grades:\n${JSON.stringify(rows, null, 1)}\nReturn: overall score (judgment aggregate, per the report card's method), the top 5 corrective actions ranked by leverage (each tied to a category gap), and one plain sentence on whether the system is improving or idling. No writes — the invoking session records results.`,
  { label: 'synthesize:verdict' })

log(`health-audit ${TODAY}: ${rows.length}/12 categories graded+verified`)
return { date: TODAY, metrics, rows, summary,
  writeBackReminder: 'Top seat now: append dated column to score-history + FINAL metrics tables, decision-history entry, open 🔔 loops (audit-task.md rules).' }
