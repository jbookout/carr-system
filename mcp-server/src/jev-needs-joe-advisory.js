// Optional, read-only Jev advice for the authenticated Needs Joe projection.
// The source queue is never changed by this module.
const ENDPOINT = "https://api.typesafe.ai/v1/systemone";
const MODEL = "jev-1.13.0";
const DEADLINE_MS = 1100;
const MAX_ITEMS = 20;
const MAX_REQUEST_CHARS = 52000;
const CLASSES = Object.freeze({
  decision_ready: "The recorded next action asks Joe for a concrete decision or approval.",
  information_needed: "Joe needs to supply or obtain information before a decision.",
  blocked: "The recorded action is blocked by an external dependency.",
  routine_review: "The recorded action is a routine review without an urgent decision.",
  unclear: "The available record does not establish a useful attention class.",
});
const FIELDS = Object.freeze({ human_ref: 15, title: 160, state: 32,
  source_label: 80, source_freshness: 32, next_human_action: 160 });

function clip(value, limit) {
  return typeof value === "string" ? value.trim().slice(0, limit) : "";
}

function probability(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1;
}

function hex(bytes) {
  return [...new Uint8Array(bytes)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

async function digest(value) {
  return `sha256:${hex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(value))))}`;
}

function unavailable(reason, snapshot, observedAt, configDigest, vendorElapsedMs = null) {
  return { schema: "jev_c13_decision_queue_advisory/v1", status: "unavailable",
    reason, snapshot_digest: snapshot, source_observed_at: observedAt,
    model: MODEL, question_config_digest: configDigest, items: [],
    vendor_elapsed_ms: vendorElapsedMs, judged_count: 0, abstained_count: 0 };
}

function boundedItem(item) {
  return {
    human_ref: clip(item?.human_ref, FIELDS.human_ref),
    title: clip(item?.title, FIELDS.title),
    state: clip(item?.state, FIELDS.state),
    source_label: clip(item?.source?.label, FIELDS.source_label),
    source_freshness: clip(item?.source?.freshness, FIELDS.source_freshness),
    next_human_action: clip(item?.next_human_action, FIELDS.next_human_action),
  };
}

function questionKey(index, ref, dimension) {
  return `item_${index}_${ref}_${dimension}`;
}

function questionsFor(items) {
  const questions = {};
  for (const [index, item] of items.entries()) {
    const target = `Only judge \`state.items[${index}]\`, whose human_ref is ${item.human_ref}. Ignore other items.`;
    const options = Object.fromEntries(Object.entries(CLASSES).map(([key, description]) =>
      [`${index}|${item.human_ref}|${key}`, description]));
    questions[questionKey(index, item.human_ref, "class")] = {
      type: "choice", instructions: `${target} Which attention class best describes its recorded next human action?`, criteria: options,
    };
    questions[questionKey(index, item.human_ref, "priority")] = {
      type: "noul", instructions: `${target} Does Joe need to act on this item within the next business week, based on the recorded action? Do not calculate dates.`,
      criteria: { true: "The recorded action calls for Joe's near-term attention.", false: "No near-term Joe action is established." },
    };
    questions[questionKey(index, item.human_ref, "relevance")] = {
      type: "noul", instructions: `${target} Does this work directly advance a DoctorCRE user capability?`,
      criteria: { true: "Direct DoctorCRE product capability.", false: "Indirect infrastructure or unrelated work." },
    };
    questions[questionKey(index, item.human_ref, "ambiguity")] = {
      type: "noul", instructions: `${target} Is the stated next human action too ambiguous for Joe to act without clarification?`,
      criteria: { true: "Joe must ask what action is intended.", false: "A concrete action is stated." },
    };
  }
  return questions;
}

function parseItem(answers, item, index) {
  const prefix = dimension => questionKey(index, item.human_ref, dimension);
  const attention = answers[prefix("class")];
  const expectedPrefix = `${index}|${item.human_ref}|`;
  const selected = attention?.choice;
  const attentionClass = typeof selected === "string" && selected.startsWith(expectedPrefix)
    ? selected.slice(expectedPrefix.length) : null;
  const expectedOptions = Object.keys(CLASSES).map(key => `${expectedPrefix}${key}`);
  const distribution = attention?.probabilities;
  const validDistribution = distribution && typeof distribution === "object" && !Array.isArray(distribution) &&
    Object.keys(distribution).length === expectedOptions.length &&
    expectedOptions.every(key => Object.hasOwn(distribution, key) && probability(distribution[key])) &&
    Math.abs(expectedOptions.reduce((sum, key) => sum + distribution[key], 0) - 1) <= 0.02 &&
    probability(distribution[selected]) &&
    expectedOptions.every(key => distribution[selected] >= distribution[key] - 0.000001);
  const priority = answers[prefix("priority")];
  const relevance = answers[prefix("relevance")];
  const ambiguity = answers[prefix("ambiguity")];
  const base = { human_ref: item.human_ref, index, judged: false,
    reason_code: "advisory_abstained" };
  if (item.next_human_action.length < 12 || item.title.length < 8)
    return { ...base, reason_code: "insufficient_recorded_evidence" };
  if (!Object.hasOwn(CLASSES, attentionClass || "") || attention?.type !== "choice" || !validDistribution ||
      !probability(attention.confidence) || attention.confidence < 0.55 ||
      [priority, relevance, ambiguity].some(answer => answer?.type !== "noul" || !probability(answer.noul)))
    return base;
  if (attentionClass === "unclear") return base;
  return { human_ref: item.human_ref, index, judged: true, reason_code: attentionClass,
    attention_class: attentionClass, class_confidence: attention.confidence,
    priority_probability: priority.noul, relevance_probability: relevance.noul,
    ambiguity_probability: ambiguity.noul, calibration_status: "unverified_model_output" };
}

export async function needsJoeAdvisory(queue, { apiKey, fetchImpl = fetch, now = new Date() } = {}) {
  const observedAt = now.toISOString();
  const original = queue?.items;
  const snapshot = await digest(original);
  let configDigest = await digest({ model: MODEL, classes: CLASSES, fields: FIELDS, version: 1 });
  if (!Array.isArray(original) || original.length > MAX_ITEMS || original.length === 0)
    return unavailable("invalid_projection", snapshot, observedAt, configDigest);
  const items = original.map(boundedItem);
  if (items.some(item => !/^WR-[0-9]{1,12}$/.test(item.human_ref)) ||
      new Set(items.map(item => item.human_ref)).size !== items.length)
    return unavailable("invalid_projection", snapshot, observedAt, configDigest);
  if (!apiKey) return unavailable("jev_unavailable", snapshot, observedAt, configDigest);
  const questions = questionsFor(items);
  configDigest = await digest({ model: MODEL, questions });
  const payload = { model: MODEL, state: { items }, questions };
  const body = JSON.stringify(payload);
  if (body.length > MAX_REQUEST_CHARS)
    return unavailable("egress_limit", snapshot, observedAt, configDigest);
  const vendorStarted = performance.now();
  try {
    const response = await fetchImpl(ENDPOINT, { method: "POST",
      headers: { authorization: `Bearer ${apiKey}`, "content-type": "application/json" },
      body, signal: AbortSignal.timeout(DEADLINE_MS) });
    if (!response.ok) return unavailable("jev_unavailable", snapshot, observedAt, configDigest,
      Math.round(performance.now() - vendorStarted));
    const result = await response.json();
    const answers = result?.answers;
    if (!answers || typeof answers !== "object" || Array.isArray(answers) ||
        result.model !== MODEL || Object.keys(answers).length !== Object.keys(questions).length ||
        Object.keys(questions).some(key => !Object.hasOwn(answers, key)))
      return unavailable("invalid_jev_answer", snapshot, observedAt, configDigest,
        Math.round(performance.now() - vendorStarted));
    const judgments = items.map((item, index) => parseItem(answers, item, index));
    return { schema: "jev_c13_decision_queue_advisory/v1",
      status: judgments.every(item => item.judged) ? "available" : "partial",
      reason: null, snapshot_digest: snapshot, source_observed_at: observedAt,
      model: MODEL, question_config_digest: configDigest, items: judgments,
      vendor_elapsed_ms: Math.round(performance.now() - vendorStarted),
      judged_count: judgments.filter(item => item.judged).length,
      abstained_count: judgments.filter(item => !item.judged).length };
  } catch {
    return unavailable("jev_unavailable", snapshot, observedAt, configDigest,
      Math.round(performance.now() - vendorStarted));
  }
}
