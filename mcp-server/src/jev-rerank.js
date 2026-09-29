// Offline Jev reranking trial for doctrine source selection. Default OFF.
// The production report-problem writer does not import or call this module.
//
// WHAT THIS CHANGES AND WHAT IT NEVER TOUCHES. Retrieval, visibility and
// authority stay in code and in the database: the caller hands this module a
// shortlist that search_doctrine_situations already ranked. This module only
// REORDERS that bounded list. It never adds a candidate, never drops one, and
// never sees or returns an id the caller did not give it — `order` holds the
// caller's own objects, so whatever revision binding a row carries travels
// through untouched. Ids and revision ids are never sent to the model.
//
// WHY. The deterministic final_score is relative, not a relevance probability:
// the any-word fallback lane returns ten-way ties at 1.0 and report-problem
// then takes the first row the database happens to return. Jev answers typed
// questions with calibrated probabilities, which is the missing quantity.
//
// THREE MODES, one flag (CARR_JEV_RERANK_MODE, exact strings only):
//   score — ONE request, one ten-level score per candidate. Jev returns the
//           probability-weighted level, so relevance = score / 9.
//   noul  — ONE request, one yes/no per candidate. relevance = P(yes).
//   beam  — a K=3 beam over the pinned doctrine taxonomy snapshot
//           (class -> document -> section), one request per level that has
//           more than one node, paths scored as a sum of log yes-probabilities.
// Anything else, including an unset flag, is the deterministic order.
//
// FALL BACK TO THE DETERMINISTIC ORDER. Jev down, a malformed answer, an
// unpinned taxonomy: every one returns the order the caller passed in, marked
// judged:false with a reason. Nothing here throws on a model failure.
//
// Per ops/typesafe_client.py: questions are asked TOGETHER (one request per
// shortlist, never one per candidate), arithmetic stays in code, a typed answer
// is validated before use, and the state carries only the situation.

import {
  DOCTRINE_TAXONOMY_DIGEST, DOCTRINE_TAXONOMY_SNAPSHOT_ID,
} from "./doctrine-taxonomy-snapshot.v1.js";

export const JEV_RERANK_FLAG = "CARR_JEV_RERANK_MODE";
export const JEV_RERANK_MODES = Object.freeze(["score", "noul", "beam"]);
export const JEV_RERANK_SHORTLIST_MAX = 10;
export const BEAM_WIDTH = 3;
export const JEV_RERANK_MODEL = "jev-latest";
// Flat ambiguity: top relevance within 0.1 of the second. Beam ambiguity: the
// top path is less than twice as likely as the second (margin < ln 2 nats).
// Both are starting points to replace once live answers are measured.
export const FLAT_AMBIGUITY_MARGIN = 0.1;
export const BEAM_AMBIGUITY_NATS = Math.log(2);

const SNIPPET_CHARS = 600;
const TITLE_CHARS = 200;
const PROBABILITY_FLOOR = 1e-6;

export const RELEVANCE_LEVELS = Object.freeze([
  "Unrelated: the section is about a different subject than the reported problem.",
  "Shares only incidental words with the reported problem; its subject is different.",
  "Same broad area as the problem, but about a different system or activity.",
  "Mentions the problem's system in passing while governing something else.",
  "Background a responder might skim; it gives no rule or step for this problem.",
  "Covers a neighbouring procedure a responder would consult second, not first.",
  "Partly governs the problem: it states a relevant rule or step but misses its core.",
  "Governs the problem's area with usable rules or steps, though not specific to this failure.",
  "Directly governs this kind of problem with specific rules or steps for it.",
  "The authoritative section for exactly this problem; a responder should start here.",
]);

export function jevRerankPosture(env) {
  const value = env ? env[JEV_RERANK_FLAG] : undefined;
  if (value === undefined || value === null || value === "" || value === "off")
    return { enabled: false, mode: "off", posture: "off", reason: null };
  if (JEV_RERANK_MODES.includes(value))
    return { enabled: true, mode: value, posture: "enabled", reason: null };
  return { enabled: false, mode: "off", posture: "misconfigured",
    reason: `${JEV_RERANK_FLAG} must be exactly off, score, noul or beam` };
}

function clip(value, max) {
  return typeof value === "string" ? value.replace(/\s+/g, " ").trim().slice(0, max) : "";
}

function quoted(value) {
  return JSON.stringify(value);
}

export function rerankCandidateText(candidate) {
  const title = clip(candidate.title, TITLE_CHARS) || clip(candidate.section_key, TITLE_CHARS);
  const doc = clip(candidate.doc_slug, 120);
  const kind = clip(candidate.content_class, 40);
  const excerpt = clip(candidate.snippet, SNIPPET_CHARS);
  return `Doctrine section ${quoted(title)} in document ${quoted(doc)}` +
    (kind ? ` (class ${kind})` : "") + `. Excerpt: ${quoted(excerpt)}`;
}

function key(i) {
  return `c${String(i).padStart(2, "0")}`;
}

export function buildFlatRerankRequest(situation, candidates, variant) {
  const questions = {};
  candidates.forEach((candidate, i) => {
    const text = rerankCandidateText(candidate);
    if (variant === "score") {
      questions[key(i)] = { type: "score", criteria: [...RELEVANCE_LEVELS],
        instructions: `How well does this doctrine section govern the operational problem reported in \`situation\`? ${text}` };
    } else if (variant === "noul") {
      questions[key(i)] = { type: "noul",
        instructions: `Is this the doctrine section a responder should read first to handle the operational problem reported in \`situation\`? ${text}`,
        criteria: {
          true: "The section governs this problem: its rules or steps apply directly to what was reported.",
          false: "The section is about another subject, only shares words with the problem, or is background a responder would not start from.",
        } };
    } else {
      throw new Error(`unknown flat rerank variant: ${variant}`);
    }
  });
  return { state: { situation: String(situation ?? "") }, questions };
}

function isProbability(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1;
}

function validUsage(usage) {
  return usage && typeof usage === "object" && !Array.isArray(usage) &&
    Number.isSafeInteger(usage.input_tokens) && usage.input_tokens > 0 &&
    Number.isSafeInteger(usage.output_tokens) && usage.output_tokens >= 0;
}

// Returns a map question-key -> relevance in [0, 1], or null when the answer
// is not exactly one typed, in-range answer per question plus reported usage.
function readAnswers(questions, result) {
  if (!result || typeof result !== "object" || typeof result.model !== "string" ||
      !result.model.trim() || !validUsage(result.usage))
    return null;
  const answers = result.answers;
  if (!answers || typeof answers !== "object") return null;
  const keys = Object.keys(questions);
  if (Object.keys(answers).length !== keys.length) return null;
  const out = {};
  for (const k of keys) {
    const question = questions[k];
    const answer = answers[k];
    if (!answer || answer.type !== question.type) return null;
    if (question.type === "noul") {
      if (!isProbability(answer.noul)) return null;
      out[k] = answer.noul;
    } else if (question.type === "score") {
      const top = question.criteria.length - 1;
      const value = answer.score;
      if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > top) return null;
      out[k] = value / top;
    } else {
      return null;
    }
  }
  return out;
}

function addUsage(total, usage) {
  return { input_tokens: (total?.input_tokens || 0) + usage.input_tokens,
    output_tokens: (total?.output_tokens || 0) + usage.output_tokens };
}

function round(value) {
  return Number(value.toFixed(9));
}

function base(mode, candidates) {
  return { schema: "carr.jev-rerank.v1", mode, judged: false, reason: null, model: null,
    order: [...candidates], scores: [], ambiguity: null, requests: 0, usage: null };
}

function fallback(result, reason) {
  return { ...result, judged: false, reason, scores: [], ambiguity: null };
}

async function ask(askJev, request, model) {
  const result = await askJev({ state: request.state, questions: request.questions, model });
  return result;
}

export async function rerankShortlist({ situation, candidates, variant, askJev, model = JEV_RERANK_MODEL }) {
  const all = Array.isArray(candidates) ? candidates : [];
  let result = base(variant, all);
  if (!all.length) return fallback(result, "empty_shortlist");
  const head = all.slice(0, JEV_RERANK_SHORTLIST_MAX);
  const tail = all.slice(JEV_RERANK_SHORTLIST_MAX);
  if (head.length < 2) return fallback(result, "single_candidate");
  if (typeof askJev !== "function") return fallback(result, "jev_unavailable");
  const request = buildFlatRerankRequest(situation, head, variant);
  let answered;
  try {
    answered = await ask(askJev, request, model);
  } catch {
    return fallback({ ...result, requests: 1 }, "jev_unavailable");
  }
  result = { ...result, requests: 1 };
  const relevance = readAnswers(request.questions, answered);
  if (!relevance) return fallback(result, "invalid_jev_answer");
  result.usage = addUsage(null, answered.usage);
  const scored = head.map((candidate, i) => ({ candidate, rank: i + 1, relevance: relevance[key(i)] }))
    .sort((a, b) => b.relevance - a.relevance || a.rank - b.rank);
  const scores = scored.map(row => ({ deterministic_rank: row.rank, section_key: row.candidate.section_key ?? null,
    relevance: round(row.relevance) }));
  const margin = round(scored[0].relevance - scored[1].relevance);
  return {
    ...result, judged: true, reason: null, model: answered.model,
    order: [...scored.map(row => row.candidate), ...tail],
    scores,
    ambiguity: { top_rank: scored[0].rank, second_rank: scored[1].rank, margin,
      ambiguous: margin < FLAT_AMBIGUITY_MARGIN },
  };
}

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object")
    return `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(",")}}`;
  return JSON.stringify(value);
}

async function pinned(taxonomy) {
  if (!taxonomy || taxonomy.digest !== DOCTRINE_TAXONOMY_DIGEST ||
      taxonomy.snapshot_id !== DOCTRINE_TAXONOMY_SNAPSHOT_ID ||
      !Array.isArray(taxonomy.documents) || !taxonomy.documents.length ||
      !taxonomy.classes || typeof taxonomy.classes !== "object")
    return false;
  try {
    const body = { classes: taxonomy.classes,
      documents: taxonomy.documents.map(d => ({ slug: d.slug, title: d.title, class: d.class })) };
    const bytes = new TextEncoder().encode(canonical(body));
    const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
    const hex = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
    return `sha256:${hex}` === DOCTRINE_TAXONOMY_DIGEST;
  } catch {
    return false;
  }
}

function logp(p) {
  return Math.log(Math.min(1 - PROBABILITY_FLOOR, Math.max(PROBABILITY_FLOOR, p)));
}

function nodeQuestion(level, node) {
  if (level === "class")
    return { type: "noul",
      instructions: `Does the operational problem reported in \`situation\` fall under this kind of doctrine? ${node.label}`,
      criteria: { true: "Guidance for this problem would be filed under this kind of doctrine.",
        false: "Guidance for this problem would be filed under a different kind of doctrine." } };
  if (level === "document")
    return { type: "noul",
      instructions: `Is doctrine document ${quoted(node.label)} (${node.slug}) likely to hold the guidance a responder needs for the operational problem reported in \`situation\`?`,
      criteria: { true: "This document is where the governing guidance for the problem would live.",
        false: "This document is about something else, or would only mention the problem in passing." } };
  return { type: "noul",
    instructions: `Is this the doctrine section a responder should read first to handle the operational problem reported in \`situation\`? ${node.label}`,
    criteria: { true: "The section governs this problem: its rules or steps apply directly to what was reported.",
      false: "The section is about another subject, only shares words with the problem, or is background a responder would not start from." } };
}

// Ask one level. Nodes carry {id, label, parentScore}. A level with a single
// node is not asked: every path shares it, so it cannot change the order.
async function scoreLevel(level, nodes, situation, askJev, model, state) {
  if (nodes.length === 1) return new Map([[nodes[0].id, nodes[0].parentScore]]);
  const questions = {};
  nodes.forEach((node, i) => { questions[key(i)] = nodeQuestion(level, node); });
  state.requests += 1;
  const answered = await ask(askJev, { state: { situation: String(situation ?? "") }, questions }, model);
  const probabilities = readAnswers(questions, answered);
  if (!probabilities) throw Object.assign(new Error("invalid"), { reason: "invalid_jev_answer" });
  state.usage = addUsage(state.usage, answered.usage);
  state.model = answered.model;
  return new Map(nodes.map((node, i) => [node.id, node.parentScore + logp(probabilities[key(i)])]));
}

function keepTop(nodes, scores, width) {
  return [...nodes].sort((a, b) => scores.get(b.id) - scores.get(a.id) || a.order - b.order).slice(0, width);
}

export async function beamRerank({ situation, candidates, askJev, taxonomy, k = BEAM_WIDTH, model = JEV_RERANK_MODEL }) {
  const all = Array.isArray(candidates) ? candidates : [];
  let result = { ...base("beam", all), taxonomy_snapshot_id: null, unmapped_ranks: [] };
  if (!all.length) return fallback(result, "empty_shortlist");
  if (!await pinned(taxonomy)) return fallback(result, "taxonomy_not_pinned");
  result.taxonomy_snapshot_id = taxonomy.snapshot_id;
  const head = all.slice(0, JEV_RERANK_SHORTLIST_MAX);
  const tail = all.slice(JEV_RERANK_SHORTLIST_MAX);
  if (head.length < 2) return fallback(result, "single_candidate");
  if (typeof askJev !== "function") return fallback(result, "jev_unavailable");

  const docs = new Map(taxonomy.documents.map(d => [d.slug, d]));
  const mapped = [];
  head.forEach((candidate, i) => {
    const doc = docs.get(candidate.doc_slug);
    // A candidate whose document is missing from the snapshot, or whose class
    // no longer matches it, is outside the pinned tree: it keeps its
    // deterministic place after the beam rather than being guessed into it.
    if (doc && (!candidate.content_class || candidate.content_class === doc.class))
      mapped.push({ candidate, rank: i + 1, doc });
    else result.unmapped_ranks.push(i + 1);
  });
  if (!mapped.length) return fallback(result, "no_mapped_candidates");

  const state = { requests: 0, usage: null, model: null };
  let sectionScores;
  let beamSections;
  try {
    const classNodes = [...new Set(mapped.map(m => m.doc.class))].sort()
      .map((klass, order) => ({ id: klass, order, parentScore: 0,
        label: `${klass}: ${taxonomy.classes[klass] || klass}` }));
    const classScores = await scoreLevel("class", classNodes, situation, askJev, model, state);
    const keptClasses = new Set(keepTop(classNodes, classScores, k).map(n => n.id));

    const docNodes = [];
    for (const m of mapped) {
      if (!keptClasses.has(m.doc.class) || docNodes.some(n => n.id === m.doc.slug)) continue;
      docNodes.push({ id: m.doc.slug, slug: m.doc.slug, label: m.doc.title, order: m.rank,
        parentScore: classScores.get(m.doc.class) });
    }
    const docScores = await scoreLevel("document", docNodes, situation, askJev, model, state);
    const keptDocs = new Set(keepTop(docNodes, docScores, k).map(n => n.id));

    beamSections = mapped.filter(m => keptDocs.has(m.doc.slug));
    const sectionNodes = beamSections.map(m => ({ id: m.rank, order: m.rank,
      label: rerankCandidateText(m.candidate), parentScore: docScores.get(m.doc.slug) }));
    sectionScores = await scoreLevel("section", sectionNodes, situation, askJev, model, state);
  } catch (error) {
    return fallback({ ...result, requests: state.requests, usage: state.usage },
      error?.reason || "jev_unavailable");
  }

  const ranked = [...beamSections].sort((a, b) =>
    sectionScores.get(b.rank) - sectionScores.get(a.rank) || a.rank - b.rank);
  const inBeam = new Set(ranked.map(m => m.rank));
  const rest = head.map((candidate, i) => ({ candidate, rank: i + 1 })).filter(row => !inBeam.has(row.rank));
  const scores = ranked.map(m => ({ deterministic_rank: m.rank, section_key: m.candidate.section_key ?? null,
    log_score: sectionScores.get(m.rank) }));
  const ambiguity = ranked.length > 1
    ? (() => {
      const margin = sectionScores.get(ranked[0].rank) - sectionScores.get(ranked[1].rank);
      return { top_rank: ranked[0].rank, second_rank: ranked[1].rank, margin,
        ambiguous: margin < BEAM_AMBIGUITY_NATS };
    })()
    : { top_rank: ranked[0].rank, second_rank: null, margin: null, ambiguous: false };
  return {
    ...result, judged: true, reason: null, model: state.model, requests: state.requests, usage: state.usage,
    order: [...ranked.map(m => m.candidate), ...rest.map(row => row.candidate), ...tail],
    scores, ambiguity,
  };
}

export async function jevRerank({ mode, situation, candidates, askJev, taxonomy, model }) {
  if (mode === "score" || mode === "noul")
    return rerankShortlist({ situation, candidates, variant: mode, askJev, model });
  if (mode === "beam") return beamRerank({ situation, candidates, askJev, taxonomy, model });
  return fallback(base("off", Array.isArray(candidates) ? candidates : []), "flag_off");
}
