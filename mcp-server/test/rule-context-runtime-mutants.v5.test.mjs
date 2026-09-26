// V5-F05 planted-bug mutants for the guards the live adapter introduces.
//
// Each mutant is KILLED here the honest way: the same behavioural expectation
// the main suite (rule-context-runtime.v5.test.mjs) holds the real module to
// is run against the mutated module, and it has to FAIL there while it passes
// on the real module. A mutant that merely "behaves differently" is not a
// kill; an expectation the real module also fails is not a kill either.
//
// Source anchors are exact and unique: a refactor that makes a mutant a no-op
// fails here instead of reporting a meaningless green mutation run.

import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import * as real from "../src/rule-context-runtime.v5.js";

const SOURCE = new URL("../src/rule-context-runtime.v5.js", import.meta.url);
const SOURCE_DIR = fileURLToPath(new URL("../src/", import.meta.url));
const WORK = mkdtempSync(join(tmpdir(), "v5-f05-runtime-mutants-"));

function mutate(id, anchor, replacement) {
  const source = readFileSync(SOURCE, "utf8");
  assert.equal(source.split(anchor).length - 1, 1,
    `mutant ${id}: anchor must occur exactly once`);
  const path = join(WORK, `${id}-${Date.now()}.mjs`);
  const relinked = source.replace(anchor, replacement).replace(
    /from "\.\/([^"]+)"/g,
    (_, file) => `from "${pathToFileURL(join(SOURCE_DIR, file)).href}"`,
  );
  writeFileSync(path, relinked);
  return import(`${pathToFileURL(path).href}?mutant=${id}-${Date.now()}`);
}

const ACTOR = { id: "10000000-0000-4000-8000-000000000031", slug: "joe-local",
  human: false, sponsoring_human_slug: "joe" };
const FACTS = {
  action: "repo.commit", audience: "internal",
  environment: "isolated_worktree", lifecycle_transition: "create",
  resource_class: "repository", risk_tier: "consequential",
};

const RULE = {
  rule_id: "r1", version: 1, rule_class: "workflow", scope: "shared", owner: "joe",
  mandatory: true, trigger: { action: ["repo.commit"] },
  control_effect: { control_key: "isolation", effect: "require" },
  binding_text: "Use an isolated worktree before changing tracked source.",
  tests: ["check:isolation"], retirement: { behavior: "permanent_until_superseded" },
  provenance: { source_record_id: "rule:r1", source_version: 1,
    source_content_digest: `sha256:${"a".repeat(64)}`,
    retrieved_at: "2026-09-26T06:00:00.000Z" },
};

function client(overrides = {}) {
  return { query: async () => ({ rows: [{ result: {
    observed_at: "2026-09-26T06:30:00.000Z",
    active_rule_count: 1, projected_rule_count: 1, missing_rule_ids: [],
    policy: { schema_version: "doctorcre-v5-f05-rule-universe.v1", universe_version: 82,
      tenant: "carr-internal", completeness: "complete_authoritative_universe",
      declared_actions: ["repo.commit"], declared_resource_classes: ["repository"],
      rules: [RULE] }, ...overrides,
  } }] }) };
}

// A behavioural expectation, stated once, run against real and mutant alike.
// It resolves when the module under test behaves correctly and rejects when
// it does not.
const EXPECTATIONS = {
  // Main suite: "a missing possible binding rule makes the universe partial and blocks the write".
  async missingRuleBlocksWrite(module) {
    const result = await module.readActionContext(client({
      active_rule_count: 2, projected_rule_count: 1, missing_rule_ids: ["r2"],
    }), ACTOR, { facts: FACTS });
    assert.equal(result.consequential_action_permitted, false);
  },
  // Main suite: "a census whose counts, projected rules and missing list disagree is refused".
  async inconsistentCensusRefuses(module) {
    await assert.rejects(
      () => module.readActionContext(client({
        active_rule_count: 2, projected_rule_count: 1, missing_rule_ids: [],
      }), ACTOR, { facts: FACTS }),
      error => error?.code === "runtime_census_mismatch");
  },
  // Main suite: "server-derived identity, time, completeness ... cannot be supplied by the caller".
  async callerTimeRefuses(module) {
    await assert.rejects(
      () => module.readActionContext(client(), ACTOR,
        { facts: FACTS, now: "1999-01-01T00:00:00.000Z" }),
      error => error?.code === "runtime_authority_injection");
  },
};

async function assertKilled(mutant, expectation) {
  await EXPECTATIONS[expectation](real);
  await assert.rejects(() => EXPECTATIONS[expectation](mutant),
    `expectation ${expectation} must fail on the mutant, or the mutant survives`);
}

test("M1a killed: trusting the store's completeness lets an unprojected rule disappear", async () => {
  const mutant = await mutate("M1a-trust-completeness",
    "const completeness = censusComplete",
    "const completeness = true");
  await assertKilled(mutant, "missingRuleBlocksWrite");
});

test("M1b killed: dropping the census consistency check reads a lying census as partial", async () => {
  const mutant = await mutate("M1b-drop-census-check",
    "      missingRuleIds.length !== active - projected) {",
    "      missingRuleIds.length !== active - projected && false) {");
  await assertKilled(mutant, "inconsistentCensusRefuses");
});

test("M2 killed: dropping the authority-injection guard accepts a caller-supplied clock", async () => {
  const mutant = await mutate("M2-drop-authority-guard",
    "assertNoRuntimeAuthorityInjection(request);",
    "// mutant: caller authority fields are not rejected");
  await assertKilled(mutant, "callerTimeRefuses");
});
