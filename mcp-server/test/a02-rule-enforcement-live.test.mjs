// V5-A02's second acceptance guard, written before the live reader.
//
// The older lifecycle suite proves the deterministic shape classifier, but the
// production surface still says `unavailable` because no authoritative reader
// is bound.  These tests describe the missing live seam: an empty-input read
// verb asks one record-layer function, and lifecycle-assurance reports only
// what that function derived from active rule, approval, control, test and
// fallback rows.  Caller-supplied coverage never enters the path.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import { readRuleEnforcementCoverage } from "../src/lifecycle-assurance.v5.js";

const VERB = "read-v5-a02-rule-enforcement-coverage";
const FALLBACK_VERB = "record-rule-enforcement-fallback";

function coverage(overrides = {}) {
  return {
    schema_version: "doctorcre-v5-a02-rule-enforcement-coverage.v1",
    observed_at: "2026-09-25T20:00:00Z",
    active_rule_count: 2,
    covered_rule_count: 2,
    gap_count: 0,
    coverage_complete: true,
    gaps: [],
    evidence_digest: `sha256:${"a".repeat(64)}`,
    ...overrides,
  };
}

class FakeDb {
  constructor(payload = coverage(), error = null) {
    this.payload = payload;
    this.error = error;
    this.calls = [];
  }
  async query(text, params = []) {
    this.calls.push({ text: text.replace(/\s+/g, " ").trim(), params });
    if (this.error) throw this.error;
    return { rows: [{ coverage: this.payload }] };
  }
}

test("LIVE: the empty-input verb is registered and delegates to the existing lifecycle seam", () => {
  const source = readFileSync(new URL("../src/tools.js", import.meta.url), "utf8");
  assert.match(source, new RegExp(`"${VERB}"\\s*:\\s*\\{`));
  assert.match(source,
    /"read-v5-a02-rule-enforcement-coverage"[\s\S]*?additionalProperties:\s*false[\s\S]*?properties:\s*\{\}[\s\S]*?readRuleEnforcementCoverage\(c\)/);
});

test("LIVE: fallback selection has an authority-only receipt verb, never a migration default", () => {
  const source = readFileSync(new URL("../src/tools.js", import.meta.url), "utf8");
  assert.match(source, new RegExp(`"${FALLBACK_VERB}"\\s*:\\s*\\{[\\s\\S]*?authorityOnly:\\s*true`));
  assert.match(source,
    /"record-rule-enforcement-fallback"[\s\S]*?fallback_kind[\s\S]*?procedure_ref[\s\S]*?ops\.record_rule_enforcement_fallback/);
});

test("LIVE: the authoritative record result is reported with exact counts and digest", async () => {
  const db = new FakeDb();
  const result = await readRuleEnforcementCoverage(db);
  assert.deepEqual(db.calls, [{
    text: "select ops.v5_a02_rule_enforcement_coverage() as coverage",
    params: [],
  }]);
  assert.equal(result.schema_version, "doctorcre-v5-a02-lifecycle-assurance.v1");
  assert.equal(result.answer, "rule_enforcement_coverage");
  assert.equal(result.decision, "report");
  assert.equal(result.coverage_complete, true);
  assert.equal(result.active_rule_count, 2);
  assert.equal(result.covered_rule_count, 2);
  assert.equal(result.gap_count, 0);
  assert.deepEqual(result.gaps, []);
  assert.equal(result.evidence_digest, `sha256:${"a".repeat(64)}`);
  assert.equal(result.evidence_source, "ops.v5_a02_rule_enforcement_coverage()");
  assert.equal(result.decided_by, "authoritative_record_reader");
  assert.equal(result.model_judgment_admitted, false);
  assert.equal(result.request_read, false);
  assert.equal(result.caller_evidence_admitted, false);
  assert.deepEqual(result.effects, V5_NO_EFFECTS);
});

// One planted bad row per part of the guard.  Each is a realistic false-green
// mutant the SQL function must surface rather than count as covered.
for (const [reason_id, detail] of [
  ["active_rule_control_unmapped", "no installed receipt-bound control"],
  ["rule_tests_not_passing", "the named behavioral test has no current passing receipt"],
  ["active_rule_fallback_absent", "no explicit fallback is recorded"],
]) {
  test(`MUTANT: ${reason_id} cannot claim complete coverage`, async () => {
    const gap = { rule_id: "11111111-1111-4111-8111-111111111111", reason_id, detail };
    const result = await readRuleEnforcementCoverage(new FakeDb(coverage({
      covered_rule_count: 1, gap_count: 1, coverage_complete: false, gaps: [gap],
      evidence_digest: `sha256:${"b".repeat(64)}`,
    })));
    assert.equal(result.status, "available");
    assert.equal(result.coverage_complete, false);
    assert.equal(result.gap_count, 1);
    assert.deepEqual(result.gaps, [gap]);
  });
}

test("MUTANT: a record-layer false green (complete plus gaps) is refused", async () => {
  const corrupt = coverage({
    covered_rule_count: 2,
    gap_count: 1,
    coverage_complete: true,
    gaps: [{ rule_id: "11111111-1111-4111-8111-111111111111",
      reason_id: "active_rule_fallback_absent", detail: "mutant" }],
  });
  const result = await readRuleEnforcementCoverage(new FakeDb(corrupt));
  assert.equal(result.status, "unavailable");
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "rule_coverage_record_invalid");
  assert.equal(result.coverage_complete, undefined);
});

test("FAIL CLOSED: an unreadable record layer leaks no driver text and claims no coverage", async () => {
  const result = await readRuleEnforcementCoverage(
    new FakeDb(null, new Error("postgres at secret-host said green")));
  assert.equal(result.status, "unavailable");
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "control_implementation_reader_unavailable");
  assert.equal(JSON.stringify(result).includes("secret-host"), false);
  assert.equal(result.coverage_complete, undefined);
});

test("MIGRATION CONTRACT: 0712 installs the universal record reader and fallback invariant", () => {
  const sql = readFileSync(new URL(
    "../../migrations/0712_a02_rule_enforcement_coverage.sql", import.meta.url), "utf8");
  for (const needle of [
    "ops.rule_enforcement_fallback_receipt",
    "ops.record_rule_enforcement_fallback",
    "ops.v5_a02_rule_enforcement_coverage()",
    "active_rule_control_unmapped",
    "rule_tests_not_passing",
    "active_rule_fallback_absent",
    "rule_coverage_false_green",
    "coverage_complete",
    "revoke all on function ops.record_rule_enforcement_fallback",
    "grant execute on function ops.record_rule_enforcement_fallback",
    "grant execute on function ops.v5_a02_rule_enforcement_coverage() to carr_reader",
  ]) assert.ok(sql.includes(needle), needle);
  assert.match(sql, /unique \(rule_id, rule_version, statement_hash\)/,
    "one exact rule version must not acquire conflicting fallback receipts");
  assert.doesNotMatch(sql, /update\s+ops\.rule_admission[\s\S]*fallback_kind/i,
    "migration must not invent a fallback for existing active rules");
});
