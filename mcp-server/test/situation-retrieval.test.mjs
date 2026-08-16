import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  normalizeSituationPhrase,
  rankSituationCandidates,
  situationRetrievalTools,
} from "../src/situation-retrieval.js";


test("normalization is shared, deterministic, and deliberately small", () => {
  assert.equal(normalizeSituationPhrase("  Record\n layer\tOUTAGE  "), "record layer outage");
  assert.equal(normalizeSituationPhrase("Landlord—went quiet"), "landlord—went quiet");
  assert.equal(normalizeSituationPhrase("   "), "");
});


test("lexical-dominant-v1 preserves lexical hits and lets a concept-only miss win", () => {
  const ranked = rankSituationCandidates([
    { section_key: "lexical", lexical_score: 0.2, concept_matches: [] },
    { section_key: "concept", lexical_score: 0, concept_matches: [
      { phrase_id: "p1", concept_id: "c1", mapping_id: "m1", phrase_strength: 1,
        phrase_weight: 1, mapping_weight: 1 },
    ] },
  ], "lexical-dominant-v1");
  assert.equal(ranked[0].section_key, "concept");
  assert.equal(ranked[0].concept_score, 1);
  assert.ok(ranked.find(hit => hit.section_key === "lexical").final_score > 0,
            "concept curation must never shadow a raw FTS hit to zero");
});


test("synonyms use MAX rather than SUM and provenance reconstructs the arithmetic", () => {
  const [hit] = rankSituationCandidates([{
    section_key: "diagnosis-checklist",
    lexical_score: 0,
    concept_matches: [
      { phrase_id: "p-weak", concept_id: "c1", mapping_id: "m1", phrase_strength: 0.5,
        phrase_weight: 1, mapping_weight: 1 },
      { phrase_id: "p-strong", concept_id: "c1", mapping_id: "m1", phrase_strength: 1,
        phrase_weight: 0.8, mapping_weight: 1 },
    ],
  }], "lexical-dominant-v1");
  assert.equal(hit.concept_score, 0.8);
  assert.deepEqual(hit.provenance.contributors.map(row => row.phrase_id), ["p-strong", "p-weak"]);
  assert.equal(hit.provenance.policy_id, "lexical-dominant-v1");
  assert.equal(hit.final_score, 0.2);
});


test("coequal policy normalizes per query, awards dual evidence, and replays byte-identically", () => {
  const input = [
    { section_key: "b", lexical_score: 0.5, concept_matches: [
      { phrase_id: "p2", concept_id: "c2", mapping_id: "m2", phrase_strength: 1,
        phrase_weight: 0.5, mapping_weight: 1 },
    ] },
    { section_key: "a", lexical_score: 1, concept_matches: [] },
  ];
  const first = rankSituationCandidates(input, "coequal-normalized-v1");
  const second = rankSituationCandidates(structuredClone(input), "coequal-normalized-v1");
  assert.deepEqual(first, second);
  assert.equal(JSON.stringify(first), JSON.stringify(second));
  assert.equal(first.find(hit => hit.section_key === "b").final_score, 1.65);
});


test("stable tie order is final, concept, lexical, then section key", () => {
  const ranked = rankSituationCandidates([
    { section_key: "z", lexical_score: 0, concept_matches: [] },
    { section_key: "a", lexical_score: 0, concept_matches: [] },
  ], "lexical-dominant-v1");
  assert.deepEqual(ranked.map(hit => hit.section_key), ["a", "z"]);
});


test("registry exposes machine proposals and human-only approval/retirement", () => {
  class ToolError extends Error { constructor(payload) { super(payload.error); this.payload = payload; } }
  const tools = situationRetrievalTools({
    withEnvelope: async (_c, _a, _n, _args, fn) => fn(),
    writeEvent: async () => {},
    ToolError,
  });
  for (const name of [
    "propose-retrieval-concept", "propose-retrieval-phrase",
    "propose-retrieval-mapping", "propose-retrieval-retirement",
  ]) {
    assert.equal(tools[name].write, true, name);
    assert.notEqual(tools[name].humanOnly, true, name);
  }
  for (const name of ["approve-retrieval-proposals", "retire-retrieval-curation"])
    assert.equal(tools[name].humanOnly, true, name);
});


test("one-word phrases are refused before any database write", async () => {
  class ToolError extends Error { constructor(payload) { super(payload.error); this.payload = payload; } }
  const tools = situationRetrievalTools({
    withEnvelope: async (_c, _a, _n, _args, fn) => fn(),
    writeEvent: async () => {},
    ToolError,
  });
  let queried = false;
  const client = { query: async () => { queried = true; return { rows: [] }; } };
  await assert.rejects(
    tools["propose-retrieval-phrase"].handler(client, { id: "actor" }, {
      idempotency_key: "00000000-0000-4000-8000-000000000001",
      concept_key: "record-layer-outage", phrase: "outage", reason: "RET-002",
      source: "golden_miss", source_ref: "RET-002", match_mode: "exact",
      weight: 1,
    }),
    error => error.payload?.error === "retrieval_phrase_too_short",
  );
  assert.equal(queried, false);
});


test("migration preserves revision bytes and installs every governed database contract", () => {
  const here = path.dirname(fileURLToPath(import.meta.url));
  const migration = fs.readFileSync(path.join(here, "../../migrations/0135_situation_retrieval.sql"), "utf8");
  for (const table of [
    "retrieval_concept", "retrieval_phrase", "doctrine_concept_mapping",
    "retrieval_proposal", "retrieval_ranking_policy", "retrieval_query_log",
  ]) assert.match(migration, new RegExp(`create table ${table}\\b`, "i"), table);
  assert.match(migration, /content_hash[\s\S]+is distinct from/i);
  assert.match(migration, /setweight\([^)]*title_vector[^)]*,\s*'A'/i);
  assert.match(migration, /setweight\([^)]*search_vector[^)]*,\s*'B'/i);
  assert.match(migration, /max\s*\(/i, "synonym contribution must not SUM");
  assert.match(migration, /digest\([^)]*sha256/i);
  assert.doesNotMatch(migration, /raw_query/i);
  assert.match(migration, /needs_repair/i);
  assert.match(migration, /grant select, insert, update[\s\S]+retrieval_proposal/i);
  assert.match(migration, /p_policy_id[\s\S]+is_default[\s\S]+status='active'/i,
               "normal retrieval must select the default policy row");
  assert.match(migration, /RET-001[\s\S]+RET-AMB-001/i,
               "approval reruns the complete grown doctrine suite");
  assert.match(migration,
               /review cycle after a record layer outage diagnosis[\s\S]+review cycle after a record layer outage playbook/i,
               "seed proposals must make both governed targets reachable for the ambiguous golden");
  assert.match(migration,
               /v_retrieval_concepts_without_targets[\s\S]+s\.status\s*=\s*'active'[\s\S]{0,160}s\.current_revision_id\s+is\s+not\s+null/i,
               "zero-target health must use the same active/current lifecycle predicate as retrieval");
  assert.match(migration, /revoke all on function promote_retrieval_proposal/i);
});


test("batch approval orders dependencies before promotion", async () => {
  class ToolError extends Error { constructor(payload) { super(payload.error); this.payload = payload; } }
  let selectSql = "";
  const client = { query: async sql => {
    if (/from retrieval_proposal/.test(sql)) { selectSql = sql; return { rows: [] }; }
    return { rows: [] };
  } };
  const tools = situationRetrievalTools({
    withEnvelope: async (_c, _a, _n, _args, fn) => fn(), writeEvent: async () => {}, ToolError,
  });
  await tools["approve-retrieval-proposals"].handler(client, { id: "human" }, {
    idempotency_key: "00000000-0000-4000-8000-000000000002",
    proposal_ids: [], base_versions: {}, golden_suite_digest: "a".repeat(64),
  });
  assert.match(selectSql, /when 'concept' then 1[\s\S]+when 'phrase' then 2[\s\S]+when 'mapping' then 3/i);
});
