import test from "node:test";
import assert from "node:assert/strict";
import {
  ASSESSMENT_OUTCOMES,
  canonicalDigest,
  investigationMethodTools,
  reconcileAssessmentRows,
  validateCandidateBatch,
  validateMatcherDefinition,
} from "../src/investigation-methods.js";

const digest = "a".repeat(64);

test("method ledger exposes bounded reads and append-only writes, never a generic executor", () => {
  const tools = investigationMethodTools({
    withEnvelope: () => {}, writeEvent: () => {}, ToolError: class extends Error {},
  });
  const expectedWrites = [
    "register-investigation-surface", "register-investigation-matcher",
    "set-investigation-method", "record-investigation-reservation", "record-investigation-wave",
    "record-investigation-candidates", "record-candidate-assessment",
    "record-investigation-checkpoint", "release-validated-finding",
  ];
  for (const name of expectedWrites) assert.equal(tools[name]?.write, true, name);
  for (const name of ["list-investigation-surfaces", "investigation-coverage",
    "investigation-checkpoints"]) assert.equal(tools[name]?.write, undefined, name);
  assert.equal(tools["execute-investigation-route"], undefined);
  assert.equal(Object.keys(tools).some(name => /sql|prompt|target-verb/.test(name)), false);
  assert.equal(tools["release-validated-finding"].inputSchema.properties.finding_id, undefined);
  assert.match(tools["release-validated-finding"].description, /same transaction/);
});

test("agent-proposed regex matchers are data with bounded scope and firing examples", () => {
  assert.equal(validateMatcherDefinition({
    matcher_key: "raw-sql-concat",
    version: 1,
    matcher_type: "regex",
    spec: { pattern: "query\\(`[^`]*\\$\\{", flags: "m", file_globs: ["mcp-server/src/**/*.js"] },
    examples: [
      { text: "query(`select ${unsafe}`)", should_match: true },
      { text: "query('select * from safe where id=$1')", should_match: false },
    ],
  }), null);

  assert.deepEqual(validateMatcherDefinition({
    matcher_key: "everything", version: 1, matcher_type: "path",
    spec: { file_globs: ["**/*"] }, examples: [],
  }), { error: "matcher_glob_catch_all", glob: "**/*" });

  assert.deepEqual(validateMatcherDefinition({
    matcher_key: "catastrophic", version: 1, matcher_type: "regex",
    spec: { pattern: "(a+)+$", file_globs: ["mcp-server/src/**/*.js"] },
    examples: [{ text: "aaaa", should_match: true }, { text: "b", should_match: false }],
  }), { error: "matcher_regex_unsafe", reason: "nested_repeat" });

  assert.deepEqual(validateMatcherDefinition({
    matcher_key: "backref", version: 1, matcher_type: "regex",
    spec: { pattern: "(token)\\1", file_globs: ["mcp-server/src/**/*.js"] },
    examples: [{ text: "tokentoken", should_match: true }, { text: "token", should_match: false }],
  }), { error: "matcher_regex_unsafe", reason: "backreference" });
});

test("candidate batches conserve their declared universe and stable digest", async () => {
  const inventory = [{
    surface_key: "worker-auth", item_count: 2, scanned_count: 2,
    matcher_refs: ["auth-boundary@1"], evidence_refs: ["git:abc:files"], input_digest: digest,
    representative: true, sensitive: true,
  }];
  const candidates = [{
    candidate_key: "mcp.js:42:auth-boundary", surface_key: "worker-auth", ordinal: 1,
    subject_type: "commit", subject_id: "11111111-1111-4111-8111-111111111111",
    evidence_refs: ["mcp.js:42"], input_digest: digest,
    matcher_key: "auth-boundary", matcher_version: "1",
  }];
  const batchDigest = await canonicalDigest({ inventory, candidates });
  assert.equal(await validateCandidateBatch({
    inventory, candidates, declared_inventory_count: 1, declared_candidate_count: 1,
    batch_digest: batchDigest,
  }), null);

  assert.deepEqual(await validateCandidateBatch({
    inventory, candidates, declared_inventory_count: 1, declared_candidate_count: 2,
    batch_digest: batchDigest,
  }), { error: "candidate_count_mismatch", declared: 2, received: 1 });

  assert.deepEqual(await validateCandidateBatch({
    inventory, candidates: [{ ...candidates[0], ordinal: 2 }],
    declared_inventory_count: 1, declared_candidate_count: 1, batch_digest: batchDigest,
  }), { error: "candidate_ordinals_not_contiguous", expected: [1], received: [2] });

  const duplicate = [candidates[0], { ...candidates[0], ordinal: 2 }];
  assert.deepEqual(await validateCandidateBatch({
    inventory, candidates: duplicate, declared_inventory_count: 1,
    declared_candidate_count: 2, batch_digest: await canonicalDigest({ inventory, candidates: duplicate }),
  }), { error: "candidate_key_duplicate", candidate_key: candidates[0].candidate_key });
});

test("checkpoint reconciliation accounts for every explicit refusal and never calls silence clean", () => {
  assert.deepEqual(ASSESSMENT_OUTCOMES,
    ["pending", "validated", "rejected", "error", "skipped", "refused"]);

  assert.deepEqual(reconcileAssessmentRows(3, [
    { candidate_id: "a", outcome: "validated" },
    { candidate_id: "b", outcome: "refused" },
  ]), {
    counts: { unattempted: 1, pending: 0, validated: 1, rejected: 0,
      error: 0, skipped: 0, refused: 1 },
    accounted: 3,
    verdict: "blocked",
  });

  assert.deepEqual(reconcileAssessmentRows(3, [
    { candidate_id: "a", outcome: "validated" },
    { candidate_id: "b", outcome: "refused" },
    { candidate_id: "c", outcome: "rejected" },
  ]), {
    counts: { unattempted: 0, pending: 0, validated: 1, rejected: 1,
      error: 0, skipped: 0, refused: 1 },
    accounted: 3,
    verdict: "degraded",
  });

  assert.equal(reconcileAssessmentRows(2, [
    { candidate_id: "a", outcome: "validated" },
    { candidate_id: "b", outcome: "rejected" },
  ]).verdict, "complete");
});
