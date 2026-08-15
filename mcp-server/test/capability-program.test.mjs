import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { TOOLS } from "../src/tools.js";
import {
  COMPLETION_KINDS,
  completionEvidenceError,
  nextProjectState,
} from "../src/capability-program.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "../..");
const MIGRATION = path.join(REPO, "migrations/0125_ai_capability_program.sql");

test("the capability portfolio is seeded once in the approved usefulness order", () => {
  const sql = fs.readFileSync(MIGRATION, "utf8");
  const rows = [...sql.matchAll(/\(\s*(\d+)\s*,\s*'carr-ai-engineering-suite-v1'\s*,\s*'(WR-AI-\d+)'\s*,\s*'([^']+)'\s*,\s*'(build|extend|adopt|decline)'\s*,/g)]
    .map(([, sequence, ref, title, disposition]) => ({
      sequence: Number(sequence), ref, title, disposition,
    }));

  assert.equal(rows.length, 51);
  assert.deepEqual(rows.map(row => row.sequence), Array.from({ length: 51 }, (_, i) => i + 1));
  assert.deepEqual(rows.slice(0, 8).map(row => row.title), [
    "LLM evaluation harness",
    "Structured-output parser",
    "Function-calling router",
    "Guardrails system",
    "AI gateway",
    "RAG pipeline",
    "Agent loop / ReAct",
    "Data-curation and deduplication pipeline",
  ]);
  assert.equal(rows.at(-1).title, "Neural Architecture Search");
});

test("completion evidence is conditional on what completion means", () => {
  assert.deepEqual(COMPLETION_KINDS, ["built", "extended", "adopted", "declined"]);

  assert.equal(completionEvidenceError("built", {
    artifact_ref: "pr:201",
    acceptance_test_refs: ["ci:run:501"],
    independent_verifier_ref: "finding:review-9",
  }), null);

  assert.equal(completionEvidenceError("extended", {
    artifact_ref: "commit:abc",
    acceptance_test_refs: ["test:contract"],
    independent_verifier_ref: "review:fresh-context",
  }), null);

  assert.equal(completionEvidenceError("adopted", {
    artifact_ref: "manifest:runtime-v1",
    acceptance_test_refs: ["rehearsal:12"],
    independent_verifier_ref: "review:13",
    rollback_ref: "runbook:remove-runtime",
    decision_ref: "decision:adopt-runtime",
  }), null);

  assert.equal(completionEvidenceError("declined", {
    decision_ref: "decision:decline-transformer",
    independent_verifier_ref: "review:cost-case",
  }), null);

  assert.equal(completionEvidenceError("built", {
    artifact_ref: "pr:201",
    acceptance_test_refs: [],
    independent_verifier_ref: "review:9",
  }).error, "completion_evidence_incomplete");

  assert.equal(completionEvidenceError("adopted", {
    artifact_ref: "manifest:runtime-v1",
    acceptance_test_refs: ["rehearsal:12"],
    independent_verifier_ref: "review:13",
  }).missing.includes("rollback_ref"), true);

  assert.equal(completionEvidenceError("declined", {
    decision_ref: "decision:decline-transformer",
  }).missing.includes("independent_verifier_ref"), true);
});

test("advancement activates exactly one successor and never skips a queued row", () => {
  assert.deepEqual(nextProjectState(1, [
    { sequence: 2, state: "ready" },
    { sequence: 3, state: "ready" },
  ]), { completeProgram: false, nextSequence: 2 });

  assert.deepEqual(nextProjectState(50, [
    { sequence: 51, state: "ready" },
  ]), { completeProgram: false, nextSequence: 51 });

  assert.deepEqual(nextProjectState(51, []), {
    completeProgram: true,
    nextSequence: null,
  });

  assert.throws(() => nextProjectState(1, [
    { sequence: 3, state: "ready" },
  ]), /queue_gap/);
  assert.throws(() => nextProjectState(1, [
    { sequence: 2, state: "in_progress" },
  ]), /successor_not_ready/);
});

test("the registry exposes a read context and only human-governed lifecycle writes", () => {
  assert.equal(Boolean(TOOLS["capability-program"]?.write), false);
  assert.equal(Boolean(TOOLS["start-capability-project"]?.write), true);
  assert.equal(Boolean(TOOLS["complete-capability-project"]?.write), true);
  assert.equal(TOOLS["complete-capability-project"].humanOnly, true);
  assert.equal(TOOLS["start-capability-project"].humanOnly, true);

  const completion = TOOLS["complete-capability-project"].inputSchema;
  assert.equal(completion.additionalProperties, false);
  assert.equal(completion.required.includes("base_version"), true);
  assert.equal(completion.required.includes("completion_evidence"), true);
  assert.deepEqual(completion.properties.completion_kind.enum, COMPLETION_KINDS);
});

test("the scheduled builder definition cannot certify, merge, deploy, or communicate", () => {
  const prompt = fs.readFileSync(
    path.join(REPO, "ops/scheduled-tasks/ai-capability-builder.SKILL.md"), "utf8");
  for (const boundary of [
    "NEVER mark a project complete",
    "NEVER merge",
    "NEVER deploy",
    "NEVER communicate externally",
    "one current project",
  ]) assert.match(prompt, new RegExp(boundary, "i"));
});
