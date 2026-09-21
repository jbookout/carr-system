// Bounded, read-only Jev advisory for one authenticated Deal Room record.
// The record is fetched by the caller through the existing get-deal-room verb.
// No model answer changes a deal, its ordering, or a partner's next action.
export const JEV_DEAL_EVIDENCE_FLOOR = 200;
const ENDPOINT = "https://api.typesafe.ai/v1/systemone";
const LEVELS = [
  "A client and space requirement are recorded, but no specific property is identified.",
  "Specific properties are being researched, toured, or compared; no offer is recorded.",
  "An offer or proposal was sent; the other side's response is outstanding.",
  "The parties exchanged a counter and are negotiating specific economic terms.",
  "Economic terms are settled; drafting, review, or signature remains.",
];
const WAITING = {
  client: "The tenant or buyer client owes the next decision, reply, approval, or information.",
  counterparty: "The landlord, seller, or their representative owes the next move.",
  carr: "The CARR agent owes the next research, assembly, send, or scheduling step.",
  market: "No acceptable property exists; the transaction awaits inventory.",
  third_party: "A lender, architect, contractor, franchisor, or authority owes the next move.",
  client_timing: "Nothing is blocked; the client's planned time has not arrived.",
  not_recorded: "The record does not establish who owes the next move.",
};

function clip(value, max = 1200) {
  return typeof value === "string" ? value.trim().slice(0, max) : "";
}

export function dealReadingState(record, now = new Date()) {
  const lines = [
    ...record.thread.map(n => clip(n.text)),
    ...record.activities.map(a => [clip(a.summary), clip(a.detail)].filter(Boolean).join(" ")),
    ...record.next_actions.map(a => clip(a.description)),
    ...record.negotiation_rounds.map(n => clip(n.note)),
    ...record.critical_dates.map(d => clip(d.note)),
  ].filter(Boolean).slice(0, 40);
  const nextStep = clip(record.next_step);
  const evidenceChars = nextStep.length + lines.reduce((sum, line) => sum + line.length, 0);
  const lastTouch = record.last_touch ? new Date(record.last_touch) : null;
  const daysQuiet = lastTouch && !Number.isNaN(lastTouch.valueOf())
    ? Math.max(0, Math.floor((now.valueOf() - lastTouch.valueOf()) / 86400000)) : null;
  return {
    evidenceChars,
    state: {
      deal: {
        recorded_phase: record.phase || null,
        transaction_type: record.type || null,
        next_step_on_file: nextStep || null,
        history: lines,
        days_since_last_recorded_touch: daysQuiet,
      },
    },
  };
}

export function dealReadingQuestions() {
  return {
    movement: { type: "score", instructions: "Judge how far this commercial real estate transaction has actually moved toward a signed agreement from the recorded evidence. Do not infer movement from the hand-maintained phase label alone.", criteria: LEVELS },
    waiting_on: { type: "choice", instructions: "Who or what owes the very next move according to the recorded evidence? Choose not_recorded if the record does not establish this.", criteria: WAITING },
    silence_is_bad: { type: "noul", instructions: "Given the recorded next step and the days since last touch, is the silence evidence that the transaction is in trouble? An unknown last touch is not evidence of trouble.", criteria: { true: "The wait exceeds what the recorded step normally takes and nothing explains the gap.", false: "The wait fits the recorded step, a future date, or the client's planned timeline." } },
  };
}

function probability(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1;
}

export async function readDealWithJev(record, { apiKey, fetchImpl = fetch, now = new Date() } = {}) {
  const { evidenceChars, state } = dealReadingState(record, now);
  const base = { schema: "carr.jev-deal-reading.v1", advisory_only: true,
    evidence_chars: evidenceChars, evidence_floor: JEV_DEAL_EVIDENCE_FLOOR };
  if (evidenceChars < JEV_DEAL_EVIDENCE_FLOOR)
    return { ...base, judged: false, reason: "insufficient_recorded_evidence" };
  if (!apiKey) return { ...base, judged: false, reason: "jev_unavailable" };
  try {
    const response = await fetchImpl(ENDPOINT, {
      method: "POST",
      headers: { authorization: `Bearer ${apiKey}`, "content-type": "application/json" },
      body: JSON.stringify({ model: "jev-latest", state, questions: dealReadingQuestions() }),
      signal: AbortSignal.timeout(20000),
    });
    if (!response.ok) return { ...base, judged: false, reason: "jev_unavailable" };
    const result = await response.json();
    const answers = result?.answers;
    const movement = answers?.movement?.score;
    const waiting = answers?.waiting_on?.choice;
    const silence = answers?.silence_is_bad?.noul;
    if (typeof movement !== "number" || !Number.isFinite(movement) || movement < 0 || movement > 4 ||
        !Object.hasOwn(WAITING, waiting) || !probability(silence))
      return { ...base, judged: false, reason: "invalid_jev_answer" };
    const rung = Math.round(movement);
    return { ...base, judged: true, reason: null, model: result.model || "unknown",
      movement_score: movement, movement_rung: rung + 1, movement_rungs: LEVELS.length,
      movement_label: LEVELS[rung], waiting_on: waiting,
      waiting_on_confidence: probability(answers.waiting_on.confidence) ? answers.waiting_on.confidence : null,
      silence_is_bad: silence };
  } catch {
    return { ...base, judged: false, reason: "jev_unavailable" };
  }
}
