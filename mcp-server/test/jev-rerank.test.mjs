import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";

import {
  JEV_RERANK_FLAG, JEV_RERANK_SHORTLIST_MAX, RELEVANCE_LEVELS, BEAM_WIDTH,
  jevRerankPosture, buildFlatRerankRequest, rerankShortlist, beamRerank, jevRerank,
} from "../src/jev-rerank.js";
import {
  DOCTRINE_TAXONOMY_SNAPSHOT, DOCTRINE_TAXONOMY_DIGEST, DOCTRINE_TAXONOMY_SNAPSHOT_ID,
} from "../src/doctrine-taxonomy-snapshot.v1.js";

function candidate(n, extra = {}) {
  return {
    section_id: `30000000-0000-0000-0000-0000000000${String(n).padStart(2, "0")}`,
    current_revision_id: `40000000-0000-0000-0000-0000000000${String(n).padStart(2, "0")}`,
    doc_slug: "engineering-workflow-sop", section_key: `s${n}`, content_class: "sop",
    title: `Section ${n}`, snippet: `excerpt ${n}`, ...extra,
  };
}

const USAGE = { input_tokens: 900, output_tokens: 20 };

// A fake Jev that answers every question from a per-candidate table keyed on
// the candidate title appearing in the instructions. It records each request.
function fakeJev(valueFor, { model = "jev-test" } = {}) {
  const requests = [];
  const ask = async request => {
    requests.push(structuredClone(request));
    const answers = {};
    for (const [key, question] of Object.entries(request.questions)) {
      const value = valueFor(question.instructions, question.type);
      answers[key] = question.type === "noul"
        ? { type: "noul", noul: value }
        : { type: "score", score: value, confidence: 0.9 };
    }
    return { model, answers, usage: USAGE };
  };
  return { ask, requests };
}

function byTitle(table, fallback = 0) {
  return instructions => {
    for (const [title, value] of Object.entries(table))
      if (instructions.includes(`"${title}"`)) return value;
    return fallback;
  };
}

test("the flag defaults off and only exact mode strings enable it", () => {
  assert.equal(JEV_RERANK_FLAG, "CARR_JEV_RERANK_MODE");
  assert.deepEqual(jevRerankPosture(undefined), { enabled: false, mode: "off", posture: "off", reason: null });
  assert.deepEqual(jevRerankPosture({}), { enabled: false, mode: "off", posture: "off", reason: null });
  assert.equal(jevRerankPosture({ CARR_JEV_RERANK_MODE: "off" }).posture, "off");
  for (const mode of ["score", "noul", "beam"])
    assert.deepEqual(jevRerankPosture({ CARR_JEV_RERANK_MODE: mode }),
      { enabled: true, mode, posture: "enabled", reason: null });
  for (const value of ["true", "SCORE", " score", "1", "on"]) {
    const posture = jevRerankPosture({ CARR_JEV_RERANK_MODE: value });
    assert.equal(posture.enabled, false);
    assert.equal(posture.mode, "off");
    assert.equal(posture.posture, "misconfigured");
  }
});

test("the flat request is ONE request with one ten-level score or one noul per candidate", () => {
  assert.equal(RELEVANCE_LEVELS.length, 10);
  const shortlist = [candidate(1), candidate(2), candidate(3)];
  const scored = buildFlatRerankRequest("the nightly backup did not run", shortlist, "score");
  assert.deepEqual(scored.state, { situation: "the nightly backup did not run" });
  assert.deepEqual(Object.keys(scored.questions), ["c00", "c01", "c02"]);
  for (const question of Object.values(scored.questions)) {
    assert.equal(question.type, "score");
    assert.deepEqual(question.criteria, RELEVANCE_LEVELS);
    assert.match(question.instructions, /`situation`/);
  }
  assert.match(scored.questions.c01.instructions, /"Section 2"/);
  const nouls = buildFlatRerankRequest("x y", shortlist, "noul");
  for (const question of Object.values(nouls.questions)) {
    assert.equal(question.type, "noul");
    assert.deepEqual(Object.keys(question.criteria), ["true", "false"]);
  }
  // Ids and revision bindings are code's business; the model never sees them.
  const wire = JSON.stringify([scored.state, scored.questions, nouls.questions]);
  for (const c of shortlist) {
    assert.equal(wire.includes(c.section_id), false);
    assert.equal(wire.includes(c.current_revision_id), false);
  }
  assert.throws(() => buildFlatRerankRequest("x", shortlist, "beam"), /unknown flat rerank variant/);
});

test("score variant reorders by probability-weighted level and returns the same objects", async () => {
  const shortlist = [candidate(1), candidate(2), candidate(3), candidate(4)];
  const jev = fakeJev(byTitle({ "Section 1": 2.0, "Section 2": 8.1, "Section 3": 5.5, "Section 4": 8.1 }));
  const out = await rerankShortlist({ situation: "ci timed out", candidates: shortlist, variant: "score", askJev: jev.ask });
  assert.equal(jev.requests.length, 1, "one request for the whole shortlist");
  assert.equal(out.judged, true);
  assert.equal(out.reason, null);
  assert.equal(out.model, "jev-test");
  assert.deepEqual(out.usage, USAGE);
  // Tie between 2 and 4 keeps the deterministic order.
  assert.deepEqual(out.order.map(c => c.section_key), ["s2", "s4", "s3", "s1"]);
  assert.equal(out.order[0], shortlist[1], "identity preserved, so the revision binding travels untouched");
  assert.equal(new Set(out.order).size, shortlist.length);
  assert.deepEqual(out.scores.map(s => s.deterministic_rank), [2, 4, 3, 1]);
  assert.equal(out.scores[0].relevance, 0.9);
  assert.equal(out.ambiguity.margin, 0);
  assert.equal(out.ambiguity.ambiguous, true);
  assert.equal(out.requests, 1);
});

test("noul variant ranks by yes-probability and records top-vs-second ambiguity", async () => {
  const shortlist = [candidate(1), candidate(2), candidate(3)];
  const jev = fakeJev(byTitle({ "Section 1": 0.05, "Section 2": 0.2, "Section 3": 0.93 }));
  const out = await rerankShortlist({ situation: "token leaked", candidates: shortlist, variant: "noul", askJev: jev.ask });
  assert.deepEqual(out.order.map(c => c.section_key), ["s3", "s2", "s1"]);
  assert.equal(out.ambiguity.top_rank, 3);
  assert.equal(out.ambiguity.second_rank, 2);
  assert.equal(out.ambiguity.margin, 0.73);
  assert.equal(out.ambiguity.ambiguous, false);
});

test("only the bounded shortlist is sent; the tail keeps its deterministic order", async () => {
  assert.equal(JEV_RERANK_SHORTLIST_MAX, 10);
  const shortlist = Array.from({ length: 13 }, (_, i) => candidate(i + 1));
  const jev = fakeJev(byTitle({ "Section 10": 0.99 }, 0.1));
  const out = await rerankShortlist({ situation: "x y", candidates: shortlist, variant: "noul", askJev: jev.ask });
  assert.equal(Object.keys(jev.requests[0].questions).length, 10);
  assert.deepEqual(out.order.map(c => c.section_key).slice(0, 1), ["s10"]);
  assert.deepEqual(out.order.slice(-3).map(c => c.section_key), ["s11", "s12", "s13"]);
  assert.equal(out.order.length, 13);
});

test("every failure falls back to the deterministic order without throwing", async () => {
  const shortlist = [candidate(1), candidate(2)];
  const cases = [
    [undefined, "jev_unavailable"],
    [async () => { throw new Error("network"); }, "jev_unavailable"],
    [async () => ({ model: "m", answers: { c00: { type: "noul", noul: 0.4 } }, usage: USAGE }), "invalid_jev_answer"],
    [async () => ({ model: "m", answers: { c00: { type: "noul", noul: 1.2 }, c01: { type: "noul", noul: 0.1 } }, usage: USAGE }), "invalid_jev_answer"],
    [async () => ({ model: "m", answers: { c00: { type: "noul", noul: true }, c01: { type: "noul", noul: 0.1 } }, usage: USAGE }), "invalid_jev_answer"],
    [async () => ({ model: "m", answers: { c00: { type: "score", score: 0.4 }, c01: { type: "noul", noul: 0.1 } }, usage: USAGE }), "invalid_jev_answer"],
    [async () => ({ answers: { c00: { type: "noul", noul: 0.4 }, c01: { type: "noul", noul: 0.1 } } }), "invalid_jev_answer"],
    [async () => null, "invalid_jev_answer"],
  ];
  for (const [askJev, reason] of cases) {
    const out = await rerankShortlist({ situation: "x y", candidates: shortlist, variant: "noul", askJev });
    assert.equal(out.judged, false);
    assert.equal(out.reason, reason);
    assert.deepEqual(out.order, shortlist);
  }
  const scoreOut = await rerankShortlist({ situation: "x y", candidates: shortlist, variant: "score",
    askJev: async () => ({ model: "m", usage: USAGE, answers: {
      c00: { type: "score", score: 9.5, confidence: 0.5 }, c01: { type: "score", score: 1, confidence: 0.5 } } }) });
  assert.equal(scoreOut.reason, "invalid_jev_answer");
  const empty = await rerankShortlist({ situation: "x y", candidates: [], variant: "noul", askJev: fakeJev(() => 1).ask });
  assert.equal(empty.judged, false);
  assert.equal(empty.reason, "empty_shortlist");
});

test("a single-candidate shortlist asks nothing", async () => {
  const jev = fakeJev(() => 0.5);
  const out = await rerankShortlist({ situation: "x y", candidates: [candidate(1)], variant: "score", askJev: jev.ask });
  assert.equal(jev.requests.length, 0);
  assert.equal(out.judged, false);
  assert.equal(out.reason, "single_candidate");
  assert.equal(out.requests, 0);
});

test("the taxonomy snapshot is pinned: its digest is the hash of its content", () => {
  const body = { classes: DOCTRINE_TAXONOMY_SNAPSHOT.classes,
    documents: DOCTRINE_TAXONOMY_SNAPSHOT.documents.map(d => ({ slug: d.slug, title: d.title, class: d.class })) };
  const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(",")}]`
    : value && typeof value === "object"
      ? `{${Object.keys(value).sort().map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(",")}}`
      : JSON.stringify(value);
  const digest = `sha256:${createHash("sha256").update(canonical(body)).digest("hex")}`;
  assert.equal(digest, DOCTRINE_TAXONOMY_DIGEST);
  assert.equal(DOCTRINE_TAXONOMY_SNAPSHOT.digest, DOCTRINE_TAXONOMY_DIGEST);
  assert.equal(DOCTRINE_TAXONOMY_SNAPSHOT_ID, "doctrine-taxonomy.2026-09-29.v1");
  assert.equal(Object.isFrozen(DOCTRINE_TAXONOMY_SNAPSHOT.documents), true);
  const slugs = DOCTRINE_TAXONOMY_SNAPSHOT.documents.map(d => d.slug);
  assert.equal(new Set(slugs).size, slugs.length);
  for (const d of DOCTRINE_TAXONOMY_SNAPSHOT.documents)
    assert.ok(Object.hasOwn(DOCTRINE_TAXONOMY_SNAPSHOT.classes, d.class), d.slug);
  assert.equal(DOCTRINE_TAXONOMY_SNAPSHOT.documents.some(d => d.class === "dossier_narrative"), false);
});

function tinyTaxonomy() {
  return DOCTRINE_TAXONOMY_SNAPSHOT;
}

test("beam refuses a taxonomy that is not the pinned snapshot", async () => {
  const jev = fakeJev(() => 0.5);
  const forged = { ...DOCTRINE_TAXONOMY_SNAPSHOT, documents: [] };
  const shortlist = [candidate(1), candidate(2)];
  for (const taxonomy of [undefined, forged, { ...DOCTRINE_TAXONOMY_SNAPSHOT, digest: "sha256:0" }]) {
    const out = await beamRerank({ situation: "x y", candidates: shortlist, askJev: jev.ask, taxonomy });
    assert.equal(out.judged, false);
    assert.equal(out.reason, "taxonomy_not_pinned");
    assert.deepEqual(out.order, shortlist);
  }
  assert.equal(jev.requests.length, 0);
});

test("beam refuses edited taxonomy content even when the id and digest are copied", async () => {
  const forged = structuredClone(DOCTRINE_TAXONOMY_SNAPSHOT);
  forged.documents[0].title = "An edited title";
  const jev = fakeJev(() => 0.5);
  const shortlist = [candidate(1), candidate(2)];
  const out = await beamRerank({ situation: "x y", candidates: shortlist, askJev: jev.ask, taxonomy: forged });
  assert.equal(out.judged, false);
  assert.equal(out.reason, "taxonomy_not_pinned");
  assert.deepEqual(out.order, shortlist);
  assert.equal(jev.requests.length, 0);
});

test("taxonomy snapshot titles contain no hostnames", () => {
  const hostname = /\b[a-z0-9-]+\.(?!(?:md|xlsx)\b)[a-z]{2,}(?:\.[a-z]{2,})*\b/i;
  assert.match("portal.example.health", hostname);
  assert.doesNotMatch("brokers.xlsx and notes.md", hostname);
  for (const doc of DOCTRINE_TAXONOMY_SNAPSHOT.documents)
    assert.doesNotMatch(doc.title, hostname, doc.slug);
});

test("flat reranking rejects missing or malformed token usage", async () => {
  const shortlist = [candidate(1), candidate(2)];
  for (const usage of [{}, { input_tokens: 3 }, { output_tokens: 2 },
    { input_tokens: 0, output_tokens: 0 }, { input_tokens: -1, output_tokens: 2 },
    { input_tokens: 1.5, output_tokens: 2 }, { input_tokens: "3", output_tokens: 2 }]) {
    const out = await rerankShortlist({ situation: "x y", candidates: shortlist, variant: "noul",
      askJev: async () => ({ model: "m", answers: {
        c00: { type: "noul", noul: 0.1 }, c01: { type: "noul", noul: 0.9 },
      }, usage }) });
    assert.equal(out.judged, false, JSON.stringify(usage));
    assert.equal(out.reason, "invalid_jev_answer");
    assert.deepEqual(out.order, shortlist);
  }
});

test("beam rejects empty token usage at a scored level", async () => {
  const shortlist = [candidate(1), candidate(2, { doc_slug: "neon-database-sop" })];
  const out = await beamRerank({ situation: "x y", candidates: shortlist,
    taxonomy: DOCTRINE_TAXONOMY_SNAPSHOT,
    askJev: async request => ({ model: "m", usage: {},
      answers: Object.fromEntries(Object.entries(request.questions).map(([k]) =>
        [k, { type: "noul", noul: 0.5 }])) }) });
  assert.equal(out.judged, false);
  assert.equal(out.reason, "invalid_jev_answer");
  assert.deepEqual(out.order, shortlist);
});

test("beam walks class, document, section with K=3, scoring paths in log space", async () => {
  assert.equal(BEAM_WIDTH, 3);
  const docs = [
    ["engineering-workflow-sop", "sop"], ["neon-database-sop", "sop"], ["cloudflare-edge-sop", "sop"],
    ["runbook", "sop"], ["carr-production-maturity-baseline", "reference"],
  ];
  const shortlist = docs.map(([slug, klass], i) => candidate(i + 1, { doc_slug: slug, content_class: klass }))
    .concat([candidate(6, { doc_slug: "not-in-snapshot", content_class: "sop" })]);
  const docTitle = slug => DOCTRINE_TAXONOMY_SNAPSHOT.documents.find(d => d.slug === slug).title;
  // Sections first: a section question also names its document, so the
  // most specific needle has to win.
  const answers = {
    // level 3: sections
    '"Section 1"': 0.5, '"Section 2"': 0.95, '"Section 3"': 0.99, '"Section 5"': 0.99,
    // level 2: documents (runbook is the fourth sop doc and must be pruned at K=3)
    [docTitle("engineering-workflow-sop")]: 0.8, [docTitle("neon-database-sop")]: 0.7,
    [docTitle("cloudflare-edge-sop")]: 0.3, [docTitle("runbook")]: 0.2,
    [docTitle("carr-production-maturity-baseline")]: 0.9,
    // level 1: classes
    "Standard operating procedures": 0.9, "Reference records": 0.4,
  };
  const jev = fakeJev(instructions => {
    for (const [needle, value] of Object.entries(answers)) if (instructions.includes(needle)) return value;
    return 0.01;
  });
  const out = await beamRerank({ situation: "writer password rotated", candidates: shortlist,
    askJev: jev.ask, taxonomy: tinyTaxonomy() });
  assert.equal(out.judged, true, out.reason);
  assert.equal(jev.requests.length, 3, "one request per level");
  assert.equal(Object.keys(jev.requests[0].questions).length, 2, "two classes");
  assert.equal(Object.keys(jev.requests[1].questions).length, 5, "every document under the kept classes");
  const level3 = Object.values(jev.requests[2].questions).map(q => q.instructions).join("\n");
  assert.match(level3, /"Section 1"/);
  assert.match(level3, /"Section 2"/);
  assert.match(level3, /"Section 5"/);
  assert.doesNotMatch(level3, /"Section 3"/, "cloudflare path fell out of the K=3 beam at the document level");
  assert.doesNotMatch(level3, /"Section 4"/);
  // Path scores: sop 0.9 * ews 0.8 * s1 0.5; sop 0.9 * neon 0.7 * s2 0.95; ref 0.4 * base 0.9 * s5 0.99
  const expect = {
    s2: Math.log(0.9) + Math.log(0.7) + Math.log(0.95),
    s1: Math.log(0.9) + Math.log(0.8) + Math.log(0.5),
    s5: Math.log(0.4) + Math.log(0.9) + Math.log(0.99),
  };
  assert.deepEqual(out.order.slice(0, 3).map(c => c.section_key), ["s2", "s1", "s5"]);
  for (const row of out.scores.slice(0, 3))
    assert.ok(Math.abs(row.log_score - expect[row.section_key]) < 1e-9, row.section_key);
  // Outside the beam: pruned and unmapped candidates keep deterministic order.
  assert.deepEqual(out.order.slice(3).map(c => c.section_key), ["s3", "s4", "s6"]);
  assert.deepEqual(out.unmapped_ranks, [6]);
  assert.equal(out.taxonomy_snapshot_id, DOCTRINE_TAXONOMY_SNAPSHOT_ID);
  // Top path is under twice as likely as the second (margin < ln 2 nats).
  assert.ok(Math.abs(out.ambiguity.margin - (expect.s2 - expect.s1)) < 1e-9);
  assert.equal(out.ambiguity.ambiguous, true);
  assert.equal(out.requests, 3);
  assert.deepEqual(out.usage, { input_tokens: 2700, output_tokens: 60 });
});

test("beam skips a level with one node and fails open when any level fails", async () => {
  const shortlist = [candidate(1), candidate(2)];
  const jev = fakeJev(byTitle({ "Section 2": 0.9 }, 0.2));
  const out = await beamRerank({ situation: "x y", candidates: shortlist, askJev: jev.ask, taxonomy: DOCTRINE_TAXONOMY_SNAPSHOT });
  assert.equal(jev.requests.length, 1, "one class and one document: only the section level is asked");
  assert.deepEqual(out.order.map(c => c.section_key), ["s2", "s1"]);
  let calls = 0;
  const broken = await beamRerank({ situation: "x y", candidates: [candidate(1), candidate(2, { doc_slug: "runbook" })],
    askJev: async request => { calls += 1; if (calls === 2) throw new Error("down"); return fakeJev(() => 0.5).ask(request); },
    taxonomy: DOCTRINE_TAXONOMY_SNAPSHOT });
  assert.equal(broken.judged, false);
  assert.equal(broken.reason, "jev_unavailable");
  assert.deepEqual(broken.order.map(c => c.section_key), ["s1", "s2"]);
});

test("jevRerank dispatches by mode and treats off as the deterministic order", async () => {
  const shortlist = [candidate(1), candidate(2)];
  const jev = fakeJev(byTitle({ "Section 2": 0.9 }, 0.1));
  const off = await jevRerank({ mode: "off", situation: "x y", candidates: shortlist, askJev: jev.ask });
  assert.equal(off.judged, false);
  assert.equal(off.reason, "flag_off");
  assert.deepEqual(off.order, shortlist);
  assert.equal(jev.requests.length, 0);
  for (const mode of ["noul", "beam"]) {
    const out = await jevRerank({ mode, situation: "x y", candidates: shortlist, askJev: jev.ask,
      taxonomy: DOCTRINE_TAXONOMY_SNAPSHOT });
    assert.equal(out.mode, mode);
    assert.equal(out.order[0].section_key, "s2");
  }
});
