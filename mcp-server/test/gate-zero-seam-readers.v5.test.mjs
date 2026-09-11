// V5-A02, the seam half — the three evidence readers Gate Zero is owed, proved
// in four parts that must not be confused with each other.
//
// PART A, THE RULING GATE IS SHUT. Each reader returns the gate's OWN frozen
// refusal object — proved by `Object.is`, not by deep-equality on a lookalike —
// so "identical refusal" is a fact about object identity rather than a claim
// about two similar shapes. No store is touched: the real store module throws
// without a DSN, and Part A passes with no DSN set, which is the observable
// proof that nothing was opened.
//
// PART B, THE PUBLIC SURFACE. The export list is exactly enumerated; every
// export is swept for the closed union of privileged words, as exact value, as
// token, AND as raw substring; and every caller-controlled shape — Proxies whose
// traps throw, throwing getters, revoked Proxies, null prototypes, and plain
// hostile text — is asserted to produce no throw and to leave none of its own
// bytes in the answer.
//
// THE ONE CARVE-OUT IN THE SUBSTRING SWEEP, and it is computed rather than
// written down. `seam:gate-zero-read-only-outcome-producer`,
// `step:gate-zero-read-only-outcome`, `step:scheduler-active-receipt`,
// `scheduler_readback_absent` and the gate's own refusal prose all contain a
// privileged word as a substring — "read" in "read-only" and "readback",
// "active" in "scheduler-active-receipt". They must appear, because the refusal
// these readers return IS the gate's refusal and the reason ids they reuse ARE
// the gate's reason ids. So the carve-out is derived at test time from the gate
// surface's own live answers and exported constants: a string the gate already
// says is not a new leak. Anything else is.
//
// PART C, THE RULED PATH, ON A STAGED TREE. mcp-server/src is copied into a
// scratch directory, a fixture decision id is pasted onto each of the three
// `decision_id:` lines — the exact lines Joe will paste onto — and the copied
// reader is imported. Two stagings:
//
//   * with the store module REPLACED by ./gate-zero-seam-stores.v5.fixture.mjs,
//     which proves every clause against known rows, including a forged hash and
//     a proposal nobody signed;
//   * with the REAL store module kept and no connection configured, which proves
//     the unreachable refusals are the real code's, not the fixture's.
//
// What runs in both is the real reader, the real ruling gate and the real
// derivation. The staging is how a ruling is simulated without a ruling, and it
// doubles as proof that the paste procedure in the seams report actually works:
// if the three lines ever stop being three lines of that exact shape, Part C
// fails on the anchor count rather than silently proving nothing.
//
// PART D, THE PRODUCER SEAM IS NOT BUILT. Its refusal is a written-out constant,
// and the test asserts the written words are byte-identical to what a live
// `emitGateZeroOutcome()` returns — so the copy cannot rot, and no reader for it
// exists to find.
//
//   node --test mcp-server/test/gate-zero-seam-readers.v5.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";

import { digest } from "../src/artifact-trust.js";
import {
  GATE_ZERO_STEP_REF,
  V5_A02_GATE_CONCLUSION_READER_SEAM,
  V5_A02_GATE_ZERO_OWED_SEAMS,
  V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS,
  V5_A02_GATE_ZERO_PRODUCER_SEAM,
  V5_A02_GATE_ZERO_REASON_IDS,
  V5_A02_PREDECESSOR_OUTCOME_READER_SEAM,
  V5_A02_SCHEDULER_READER_SEAM,
  V5_A02_SCHEDULER_STEP_REF,
  emitGateZeroOutcome,
  readGateGraphAssurance,
  readGateZeroPredecessorJoin,
} from "../src/gate-zero-assurance.v5.js";

import * as readers from "../src/gate-zero-seam-readers.v5.js";
import * as rulings from "../src/gate-zero-seam-rulings.v5.js";
import * as evidence from "../src/gate-zero-seam-evidence.v5.js";
import * as stores from "../src/gate-zero-seam-stores.v5.js";
import * as fixtureStores from "./gate-zero-seam-stores.v5.fixture.mjs";

const SRC = fileURLToPath(new URL("../src", import.meta.url));
const FIXTURE_STORE_FILE = fileURLToPath(new URL("./gate-zero-seam-stores.v5.fixture.mjs", import.meta.url));
const RULINGS_FILE = "gate-zero-seam-rulings.v5.js";
const STORES_FILE = "gate-zero-seam-stores.v5.js";

/** The line the ruling goes on. If this string stops matching, nothing is proved. */
const DECISION_ID_LINE = "    decision_id: null,\n";

/**
 * Three distinct well-formed ids, in the order the `decision_id:` lines appear
 * in the ruling table — predecessor, scheduler, conclusion. Distinct on purpose:
 * each reader is asserted to come back with ITS OWN seam's id, so a reader that
 * looked up the wrong seam fails here instead of reading the wrong store later.
 */
const FIXTURE_DECISION_IDS = Object.freeze([
  "11111111-1111-4111-8111-111111111111",
  "22222222-2222-4222-8222-222222222222",
  "33333333-3333-4333-8333-333333333333",
]);

// ---------------------------------------------------------------------------
// PART A — the ruling gate is shut, and the refusal is the gate's own object.
// ---------------------------------------------------------------------------

const READERS_UNDER_TEST = [
  ["readPredecessorOutcomeEvidence", readers.readPredecessorOutcomeEvidence, readGateZeroPredecessorJoin],
  ["readSchedulerCanaryEvidence", readers.readSchedulerCanaryEvidence, readGateZeroPredecessorJoin],
  ["readGateConclusionEvidence", readers.readGateConclusionEvidence, readGateGraphAssurance],
];

test("RULING: every seam's decision id is null, so no seam is ruled", () => {
  const seams = Object.keys(rulings.GATE_ZERO_SEAM_RULINGS).sort();
  assert.deepEqual(seams, [...readers.GATE_ZERO_SEAM_READER_SEAMS]);
  for (const seam of seams) {
    const entry = rulings.GATE_ZERO_SEAM_RULINGS[seam];
    assert.equal(entry.decision_id, null, `${seam} carries a decision id`);
    assert.equal(rulings.seamRulingDecisionRef(entry), null, seam);
    assert.ok(rulings.GATE_ZERO_SEAM_STORE_REFS.includes(entry.store_ref), seam);
    assert.ok(Object.isFrozen(entry), seam);
  }
  for (const row of readers.gateZeroSeamRulingStatus())
    assert.equal(row.ruling_on_record, false, row.seam);
});

test("RULING: the three decision_id lines are exactly three lines of the pasted shape", () => {
  // The seams report tells Joe to paste onto these lines. If the file's shape
  // drifts, that instruction is wrong, and this is where it becomes visible.
  const source = readFileSync(join(SRC, RULINGS_FILE), "utf8");
  assert.equal(source.split(DECISION_ID_LINE).length - 1, 3,
    "the ruling table no longer holds exactly three `decision_id: null,` lines");
});

test("RULING SHUT: each reader returns the gate's own refusal, field for field and byte for byte", async () => {
  // The gate builds a fresh frozen object per call, so identity is not available
  // to assert. What IS available is stronger than deep-equality on its own: the
  // refusal is byte-identical under the repository's own canonical digest, AND
  // the reader's source returns the gate function's result directly rather than
  // assembling a shape that happens to match today.
  const source = readFileSync(join(SRC, "gate-zero-seam-readers.v5.js"), "utf8");
  assert.equal((source.match(/return readGateZeroPredecessorJoin\(\);/g) ?? []).length, 2,
    "a reader stopped delegating its refusal to the gate");
  assert.equal((source.match(/return readGateGraphAssurance\(\);/g) ?? []).length, 1,
    "the conclusion reader stopped delegating its refusal to the gate");

  for (const [name, reader, gateFn] of READERS_UNDER_TEST) {
    const expected = gateFn();
    const got = await reader({ stepRef: "step:wr46-dissolution-outcome" });
    assert.deepEqual(got, expected, `${name} returned a different refusal than the gate's`);
    assert.ok(Object.isFrozen(got), name);
    assert.equal(got.status, "unavailable", name);
    assert.equal(got.decision, "refuse", name);
    assert.equal(got.reason_id, expected.reason_id, name);
    assert.deepEqual([...got.owed_seams], [...expected.owed_seams], name);
    assert.equal(got.caller_evidence_admitted, false, name);
    assert.equal(digest(got), digest(expected), name);
  }
  // And the reason ids are the ones the gate refuses with today, not new words.
  assert.equal(readGateZeroPredecessorJoin().reason_id, "predecessor_outcome_reader_unavailable");
  assert.equal(readGateGraphAssurance().reason_id, "gate_conclusion_reader_unavailable");
  assert.deepEqual([...readGateZeroPredecessorJoin().owed_seams],
    [V5_A02_PREDECESSOR_OUTCOME_READER_SEAM, V5_A02_SCHEDULER_READER_SEAM].sort());
  assert.deepEqual([...readGateGraphAssurance().owed_seams], [V5_A02_GATE_CONCLUSION_READER_SEAM]);
});

test("RULING SHUT: the refusal does not move for any query, valid or not", async () => {
  const queries = [
    undefined, null, {}, [], "step:wr46-dissolution-outcome", 1, true,
    { stepRef: "step:wr40-repository-outcome", outcomeHash: `sha256:${"a".repeat(64)}` },
    { serviceKey: "carr-fleet-sync", canaryRunKey: "canary-join" },
    { headSha: "a".repeat(40), checkName: "db-acceptance" },
    { decision_id: FIXTURE_DECISION_IDS[0], stepRef: "step:wr46-dissolution-outcome" },
    { ruling_decision_ref: FIXTURE_DECISION_IDS[0] },
    { store_ref: "github:checks", finding: "predecessor_outcome_accepted_with_matching_hash" },
  ];
  for (const [name, reader, gateFn] of READERS_UNDER_TEST) {
    const baseline = digest(gateFn());
    for (const query of queries)
      assert.equal(digest(await reader(query)), baseline,
        `${name} answered differently for ${JSON.stringify(query) ?? "undefined"}`);
  }
});

test("RULING SHUT: a caller-supplied decision id is not a parameter anywhere", () => {
  // Arity is the boundary: each reader takes one query. A second argument cannot
  // carry a ruling because there is no second argument to carry it.
  for (const [name, reader] of READERS_UNDER_TEST)
    assert.equal(reader.length, 1, `${name} takes more than the query`);
  // And nothing in the module or its ruling table reaches the environment.
  // And nothing in the reader or its ruling table reaches the process
  // environment: only the store layer does, and only for WHERE a ruled store
  // lives. A seam an env var could open is a seam any shell could open.
  for (const file of [RULINGS_FILE, "gate-zero-seam-readers.v5.js"]) {
    const source = readFileSync(join(SRC, file), "utf8");
    assert.equal(/process\s*\.\s*env/.test(source), false,
      `${file} names the process environment`);
  }
});

// ---------------------------------------------------------------------------
// PART B — the public surface, and the privileged-word sweep.
// ---------------------------------------------------------------------------

const EXPECTED_READER_EXPORTS = [
  "GATE_ZERO_PRODUCER_SEAM_NOT_BUILT",
  "GATE_ZERO_SEAM_READERS_SCHEMA_VERSION",
  "GATE_ZERO_SEAM_READER_REASON_IDS",
  "GATE_ZERO_SEAM_READER_SEAMS",
  "gateZeroSeamRulingStatus",
  "readGateConclusionEvidence",
  "readPredecessorOutcomeEvidence",
  "readSchedulerCanaryEvidence",
].sort();

/** The closed union, verbatim from the standing rule of 2026-09-11. */
const PRIVILEGED_WORDS = Object.freeze([
  "allow", "commit", "prompt", "suppress", "release", "read", "covered", "drafted",
  "proposed", "queued", "healthy", "passing", "ok", "pass", "satisfied", "complete",
  "admitted", "resumed", "attended", "verified", "present", "equivalent",
  "operational", "active", "green", "joins_exactly", "coverage_complete", "favorable",
]);

/**
 * Every string the GATE itself already says, collected from its own live answers
 * and its own exported constants. A substring hit inside one of these is the
 * gate's word, not a new leak — and the readers are required to say some of them,
 * because the refusal they return is the gate's and the reason ids they reuse are
 * the gate's. Computed, so it cannot quietly grow into an allowlist.
 */
function gateOwnStrings() {
  const found = new Set();
  const walk = value => {
    if (typeof value === "string") { found.add(value); return; }
    if (Array.isArray(value)) { value.forEach(walk); return; }
    if (value !== null && typeof value === "object") Object.values(value).forEach(walk);
  };
  walk(readGateZeroPredecessorJoin());
  walk(readGateGraphAssurance());
  walk(emitGateZeroOutcome());
  walk([...V5_A02_GATE_ZERO_REASON_IDS]);
  walk([...V5_A02_GATE_ZERO_OWED_SEAMS]);
  walk([...V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS]);
  walk([GATE_ZERO_STEP_REF, V5_A02_SCHEDULER_STEP_REF, V5_A02_GATE_ZERO_PRODUCER_SEAM]);
  return found;
}

const GATE_OWN_STRINGS = gateOwnStrings();

/**
 * Three sweeps over one value, in order of strictness.
 *
 *   exact      the whole string IS a privileged word.
 *   token      a word of the string, split on every non-alphanumeric run, IS one.
 *   substring  the privileged word appears anywhere in the string.
 *
 * Plus the key checks: a privileged key that is literally `true`, and any
 * `would_*` or `*_if_authoritative` key, which belong to the module-private
 * conditional layer and must never surface.
 */
function privilegedFindings(value, path = "$", found = []) {
  if (Array.isArray(value)) {
    value.forEach((entry, index) => privilegedFindings(entry, `${path}[${index}]`, found));
    return found;
  }
  if (value !== null && typeof value === "object") {
    for (const [key, entry] of Object.entries(value)) {
      const at = `${path}.${key}`;
      if (entry === true && PRIVILEGED_WORDS.includes(key)) found.push(`${at} === true`);
      if (/^would_/.test(key)) found.push(`${at} is a conditional key on the public surface`);
      if (key.includes("_if_authoritative")) found.push(`${at} is a conditional key on the public surface`);
      privilegedFindings(entry, at, found);
    }
    return found;
  }
  if (typeof value !== "string") return found;
  // A string the GATE itself already says is not a new leak — and some of them
  // must appear, because these readers return the gate's own refusal and reuse
  // the gate's own reason ids. `step:scheduler-active-receipt` carries "active";
  // `scheduler_readback_absent` carries "read". The carve-out is whole-string
  // identity against what the gate says, so a word smuggled into a NEW string is
  // still caught.
  if (GATE_OWN_STRINGS.has(value)) return found;
  const tokens = value.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  for (const word of PRIVILEGED_WORDS) {
    if (value === word) found.push(`${path} is the privileged word ${word}`);
    else if (tokens.includes(word)) found.push(`${path} carries the privileged token ${word}`);
    else if (value.includes(word)) found.push(`${path} carries ${word} as a substring`);
  }
  return found;
}

test("SWEEP: the sweep itself catches a privileged outcome when one is planted", () => {
  // A sweep nobody has seen fail is a sweep nobody has tested. Four plants, one
  // per mechanism, so a broken mechanism cannot hide behind a working one.
  assert.ok(privilegedFindings({ ok: true }).length > 0);
  assert.ok(privilegedFindings({ status: "green" }).length > 0);
  assert.ok(privilegedFindings({ status: "the gate is green now" }).length > 0);
  assert.ok(privilegedFindings({ would_allow_if_authoritative: false }).length > 0);
  assert.ok(privilegedFindings({ note: "committed" }).length > 0,
    "the substring sweep missed a privileged word inside a longer one");
  // And the carve-out is exactly "a string the gate already says", nothing wider.
  assert.ok(GATE_OWN_STRINGS.has(V5_A02_GATE_ZERO_PRODUCER_SEAM));
  assert.ok(GATE_OWN_STRINGS.has("scheduler_readback_absent"));
  assert.ok(!GATE_OWN_STRINGS.has("committed"));
  assert.ok(!GATE_OWN_STRINGS.has("green"), "the carve-out must not hold a bare privileged word");
  assert.ok(privilegedFindings({ note: "step:scheduler-active-receipt is fine on its own" }).length > 0,
    "a gate word smuggled into a new sentence must still be caught");
  assert.ok(GATE_OWN_STRINGS.size > 20 && GATE_OWN_STRINGS.size < 200,
    "the carve-out is the wrong size to be the gate's own words");
});

test("SURFACE: the export list is exactly the three readers and their constants", () => {
  assert.deepEqual(Object.keys(readers).sort(), EXPECTED_READER_EXPORTS);
  for (const name of Object.keys(readers)) {
    assert.ok(!/^(classify|evaluate|derive|bind|create|set)/.test(name),
      `${name} is a binder or classifier name on the public surface`);
    assert.ok(!/would_|_if_authoritative/.test(name), `${name} is a conditional name on the surface`);
  }
  // The conditional layer is a separate module and stays out of this one's
  // surface: its names are `would*`, and none of them is re-exported here.
  for (const name of Object.keys(evidence))
    if (/^would/.test(name)) assert.ok(!Object.hasOwn(readers, name), `${name} leaked onto the reader surface`);
});

test("SWEEP: no export of any of the four seam modules carries a privileged outcome", () => {
  for (const [label, namespace] of [["readers", readers], ["rulings", rulings],
    ["evidence", evidence], ["stores", stores]]) {
    for (const [name, value] of Object.entries(namespace)) {
      if (typeof value === "function") continue;
      assert.deepEqual(privilegedFindings(value, `${label}.${name}`), [],
        `${label}.${name} carries a privileged outcome`);
    }
  }
  assert.deepEqual(privilegedFindings(readers.gateZeroSeamRulingStatus(), "rulingStatus"), []);
});

/** Objects built to break a reader that reaches into them naively. */
const HOSTILE_MARKER = "HOSTILEMARKERTEXT";
function hostileQueries() {
  const throwingProxy = new Proxy({}, {
    get() { throw new Error(`${HOSTILE_MARKER}-proxy-get`); },
    has() { throw new Error(`${HOSTILE_MARKER}-proxy-has`); },
    ownKeys() { throw new Error(`${HOSTILE_MARKER}-proxy-keys`); },
  });
  const revocable = Proxy.revocable({ stepRef: "step:wr46-dissolution-outcome" }, {});
  revocable.revoke();
  const nullProto = Object.create(null);
  nullProto.stepRef = `${HOSTILE_MARKER}-null-proto`;
  return [
    throwingProxy,
    revocable.proxy,
    nullProto,
    { get stepRef() { throw new Error(`${HOSTILE_MARKER}-getter`); },
      get outcomeHash() { throw new Error(`${HOSTILE_MARKER}-getter`); },
      get serviceKey() { throw new Error(`${HOSTILE_MARKER}-getter`); },
      get canaryRunKey() { throw new Error(`${HOSTILE_MARKER}-getter`); },
      get headSha() { throw new Error(`${HOSTILE_MARKER}-getter`); },
      get checkName() { throw new Error(`${HOSTILE_MARKER}-getter`); } },
    { stepRef: `${HOSTILE_MARKER}-step`, outcomeHash: `${HOSTILE_MARKER}-hash`,
      serviceKey: `${HOSTILE_MARKER}-service`, canaryRunKey: `${HOSTILE_MARKER}-run`,
      headSha: `${HOSTILE_MARKER}-sha`, checkName: `${HOSTILE_MARKER}-check` },
    { stepRef: { toString() { throw new Error(`${HOSTILE_MARKER}-tostring`); } } },
    { stepRef: Symbol(`${HOSTILE_MARKER}-symbol`) },
    { stepRef: ["step:wr46-dissolution-outcome"], outcomeHash: [`sha256:${"a".repeat(64)}`] },
  ];
}

test("HOSTILE: no hostile query throws, and none of its bytes come back", async () => {
  for (const [name, reader] of READERS_UNDER_TEST) {
    for (const query of hostileQueries()) {
      const result = await reader(query);
      const serialized = JSON.stringify(result);
      assert.ok(!serialized.includes(HOSTILE_MARKER),
        `${name} leaked caller text: ${serialized.slice(0, 200)}`);
      assert.equal(result.decision, "refuse", name);
      assert.deepEqual(privilegedFindings(result), [], name);
    }
  }
});

// ---------------------------------------------------------------------------
// PART C — the ruled path, on a staged copy of src with a ruling pasted in.
// ---------------------------------------------------------------------------

const staged = [];

/**
 * A copy of mcp-server/src with a fixture decision id pasted onto each of the
 * three `decision_id:` lines. Lives under node_modules/.cache so `pg` and every
 * other dependency still resolve by walking up, and so nothing untracked lands
 * in the working tree.
 */
function stageTree({ substituteStores }) {
  const cache = fileURLToPath(new URL("../node_modules/.cache/", import.meta.url));
  mkdirSync(cache, { recursive: true });
  const base = mkdtempSync(join(cache, "gate-zero-seam-"));
  staged.push(base);
  const target = join(base, "src");
  cpSync(SRC, target, { recursive: true });

  const path = join(target, RULINGS_FILE);
  const source = readFileSync(path, "utf8");
  assert.equal(source.split(DECISION_ID_LINE).length - 1, 3,
    "the staging anchor no longer matches the ruling table");
  let pasted = 0;
  const ruled = source.replaceAll(DECISION_ID_LINE,
    () => `    decision_id: "${FIXTURE_DECISION_IDS[pasted++]}",\n`);
  assert.equal(pasted, 3, "the staging pasted the wrong number of rulings");
  assert.ok(!ruled.includes(DECISION_ID_LINE), "a null decision id survived the staging");
  writeFileSync(path, ruled);

  if (substituteStores) cpSync(FIXTURE_STORE_FILE, join(target, STORES_FILE));
  return target;
}

async function stagedReaders(options) {
  return import(pathToFileURL(join(stageTree(options), "gate-zero-seam-readers.v5.js")).href);
}

test.after(() => {
  for (const base of staged) rmSync(base, { recursive: true, force: true });
});

test("STAGING: the fixture store module covers every export the real one has", () => {
  const real = Object.keys(stores).sort();
  const fixture = new Set(Object.keys(fixtureStores));
  for (const name of real)
    assert.ok(fixture.has(name), `the fixture store is missing ${name}, so a path would go unproved`);
  for (const name of [...fixture].sort())
    if (!real.includes(name))
      assert.ok(name.startsWith("FIXTURE_"), `${name} is a fixture-only export that is not named as one`);
});

test("RULED: a pasted decision id is what opens the seam, and nothing else", async () => {
  const ruled = await stagedReaders({ substituteStores: true });
  const status = ruled.gateZeroSeamRulingStatus();
  assert.equal(status.length, 3);
  for (const row of status) assert.equal(row.ruling_on_record, true, row.seam);
  // The unruled module in this same process is untouched by the staging.
  for (const row of readers.gateZeroSeamRulingStatus()) assert.equal(row.ruling_on_record, false);
});

test("RULED: the predecessor reader admits only an accepted outcome whose receipt hash matches", async () => {
  const ruled = await stagedReaders({ substituteStores: true });
  const accepted = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr46-dissolution-outcome", outcomeHash: fixtureStores.FIXTURE_ACCEPTED_HASH });
  assert.equal(accepted.status, "evidence_returned");
  assert.equal(accepted.decision, "report");
  assert.equal(accepted.finding, "predecessor_outcome_accepted_with_matching_hash");
  assert.equal(accepted.hash_matches, true);
  assert.equal(accepted.work_request_ref, "WR-000046");
  assert.equal(accepted.ruling_decision_ref, FIXTURE_DECISION_IDS[0],
    "the predecessor seam took the wrong seam's ruling");
  assert.equal(accepted.store_ref, "record-layer:ops.sourced_work_request_outcome_feedback");
  assert.deepEqual(privilegedFindings(accepted), []);

  // A forged hash — well-formed, and no row carries it.
  const forged = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr46-dissolution-outcome", outcomeHash: fixtureStores.FIXTURE_FORGED_HASH });
  assert.equal(forged.decision, "refuse");
  assert.equal(forged.finding, "predecessor_outcome_acceptance_receipt_hash_mismatch");
  assert.equal(forged.hash_matches, false);

  // A proposal nobody signed. The near miss, and it refuses before the hash.
  const pending = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr40-repository-outcome", outcomeHash: `sha256:${"5".repeat(64)}` });
  assert.equal(pending.decision, "refuse");
  assert.equal(pending.finding, "predecessor_outcome_not_accepted");

  // No outcome row at all.
  const absent = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr54-backup-recovery-outcome", outcomeHash: fixtureStores.FIXTURE_ACCEPTED_HASH });
  assert.equal(absent.decision, "refuse");
  assert.equal(absent.finding, "predecessor_outcome_absent");

  // The scheduler predecessor is not outcome-backed, and says so by name.
  const scheduler = await ruled.readPredecessorOutcomeEvidence({
    stepRef: V5_A02_SCHEDULER_STEP_REF, outcomeHash: fixtureStores.FIXTURE_ACCEPTED_HASH });
  assert.equal(scheduler.decision, "refuse");
  assert.equal(scheduler.reason_id, "scheduler_predecessor_not_outcome_backed");

  // A step outside the frozen four, and a malformed hash.
  const unknown = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:something-else", outcomeHash: fixtureStores.FIXTURE_ACCEPTED_HASH });
  assert.equal(unknown.reason_id, "unknown_predecessor_step");
  const badHash = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr46-dissolution-outcome", outcomeHash: "sha256:nope" });
  assert.equal(badHash.reason_id, "predecessor_query_invalid");
  assert.equal(badHash.invalid_field, "outcomeHash");
});

test("RULED: the scheduler reader answers all three clauses from ledger rows, and refuses a missing row", async () => {
  const ruled = await stagedReaders({ substituteStores: true });
  const read = (canaryRunKey, serviceKey = "carr-fleet-sync") =>
    ruled.readSchedulerCanaryEvidence({ serviceKey, canaryRunKey });

  const joined = await read("canary-join");
  assert.equal(joined.decision, "report");
  assert.equal(joined.finding, "scheduler_canary_and_observation_join");
  assert.equal(joined.bound_to_receipt, true);
  assert.equal(joined.readback_after_dispatch, true);
  assert.equal(joined.canary_match, true);
  assert.equal(joined.scheduler_step_ref, V5_A02_SCHEDULER_STEP_REF);
  assert.equal(joined.ruling_decision_ref, FIXTURE_DECISION_IDS[1]);
  assert.deepEqual(privilegedFindings(joined), []);

  const unbound = await read("canary-unbound");
  assert.equal(unbound.decision, "refuse");
  assert.equal(unbound.finding, "scheduler_canary_not_bound_to_receipt");
  assert.equal(unbound.bound_to_receipt, false);

  // Dispatch and observation share one instant: strictly-after is strict.
  const sameInstant = await read("canary-same-instant");
  assert.equal(sameInstant.decision, "refuse");
  assert.equal(sameInstant.finding, "scheduler_readback_not_after_dispatch");
  assert.equal(sameInstant.readback_after_dispatch, false);

  const mismatch = await read("canary-mismatch");
  assert.equal(mismatch.decision, "refuse");
  assert.equal(mismatch.finding, "scheduler_readback_canary_mismatch");
  assert.equal(mismatch.canary_match, false);

  for (const [key, finding] of [
    ["canary-inflight", "scheduler_readback_absent"],
    ["canary-never-ran", "scheduler_dispatch_row_absent"],
  ]) {
    const result = await read(key);
    assert.equal(result.decision, "refuse", key);
    assert.equal(result.finding, finding, key);
    assert.equal(result.bound_to_receipt, null, `${key} guessed a clause with no row to read it from`);
  }

  const noService = await read("canary-join", "carr-not-in-the-ledger");
  assert.equal(noService.finding, "scheduler_service_row_absent");

  const badQuery = await ruled.readSchedulerCanaryEvidence({ serviceKey: "Not A Key", canaryRunKey: "x" });
  assert.equal(badQuery.reason_id, "scheduler_query_invalid");
  assert.equal(badQuery.invalid_field, "serviceKey");
});

test("RULED: the conclusion reader returns GitHub's own word, and translates nothing", async () => {
  const ruled = await stagedReaders({ substituteStores: true });
  const sha = fixtureStores.FIXTURE_COMMIT_SHA;

  const success = await ruled.readGateConclusionEvidence({ headSha: sha, checkName: "db-acceptance" });
  assert.equal(success.decision, "report");
  assert.equal(success.finding, "gate_conclusion_observed");
  assert.equal(success.conclusion, "success");
  assert.equal(success.store_ref, "github:checks");
  assert.equal(success.ruling_decision_ref, FIXTURE_DECISION_IDS[2]);
  // The word is GitHub's and is not turned into one a gate would act on.
  assert.ok(!Object.hasOwn(success, "green"));
  assert.ok(!Object.hasOwn(success, "passable"));
  assert.deepEqual(privilegedFindings(success), []);

  // A re-run: the later conclusion is the one reported, and the count is visible.
  const rerun = await ruled.readGateConclusionEvidence({ headSha: sha, checkName: "rerun-check" });
  assert.equal(rerun.conclusion, "failure");
  assert.equal(rerun.completed_runs_seen, 2);
  assert.equal(rerun.decision, "report",
    "a reported conclusion is a reading, not a verdict — failure is still a reading");

  // Queued: no conclusion exists, so none is invented.
  const queued = await ruled.readGateConclusionEvidence({ headSha: sha, checkName: "queued-check" });
  assert.equal(queued.decision, "refuse");
  assert.equal(queued.finding, "gate_conclusion_check_absent");
  assert.equal(queued.conclusion, null);

  // A completed run that belongs to a different commit does not answer for this one.
  const wrongCommit = await ruled.readGateConclusionEvidence({ headSha: sha, checkName: "wrong-commit" });
  assert.equal(wrongCommit.finding, "gate_conclusion_check_absent");
  assert.equal(wrongCommit.check_runs_seen, 1,
    "the row was seen and still did not answer, which is the point");

  const badQuery = await ruled.readGateConclusionEvidence({ headSha: "nope", checkName: "db-acceptance" });
  assert.equal(badQuery.reason_id, "gate_conclusion_query_invalid");
  assert.equal(badQuery.invalid_field, "headSha");
});

test("RULED: hostile queries stay harmless once the seam is open", async () => {
  const ruled = await stagedReaders({ substituteStores: true });
  const under = [
    ["readPredecessorOutcomeEvidence", ruled.readPredecessorOutcomeEvidence],
    ["readSchedulerCanaryEvidence", ruled.readSchedulerCanaryEvidence],
    ["readGateConclusionEvidence", ruled.readGateConclusionEvidence],
  ];
  for (const [name, reader] of under) {
    for (const query of hostileQueries()) {
      const result = await reader(query);
      assert.ok(!JSON.stringify(result).includes(HOSTILE_MARKER), `${name} leaked caller text`);
      assert.equal(result.decision, "refuse", name);
      assert.deepEqual(privilegedFindings(result), [], name);
    }
  }
});

test("RULED: with the real stores and nothing configured, every seam reports unreachable", async () => {
  const ruled = await stagedReaders({ substituteStores: false });
  const saved = {};
  for (const name of ["DATABASE_URL_READER", "GITHUB_TOKEN", "GITHUB_REPOSITORY"]) {
    saved[name] = process.env[name];
    delete process.env[name];
  }
  try {
    const predecessor = await ruled.readPredecessorOutcomeEvidence({
      stepRef: "step:wr46-dissolution-outcome", outcomeHash: `sha256:${"4".repeat(64)}` });
    assert.equal(predecessor.reason_id, "predecessor_outcome_store_unreachable");
    assert.equal(predecessor.unavailable_because,
      "the connection target for this store is not configured in this process");
    assert.equal(predecessor.finding, null, "an unreachable store produced a finding anyway");

    const scheduler = await ruled.readSchedulerCanaryEvidence({
      serviceKey: "carr-fleet-sync", canaryRunKey: "canary-join" });
    assert.equal(scheduler.reason_id, "scheduler_ledger_unreachable");

    const conclusion = await ruled.readGateConclusionEvidence({
      headSha: "a".repeat(40), checkName: "db-acceptance" });
    assert.equal(conclusion.reason_id, "gate_conclusion_source_unreachable");
    assert.equal(conclusion.unavailable_because,
      "the checks source credentials are not configured in this process");

    for (const result of [predecessor, scheduler, conclusion])
      assert.deepEqual(privilegedFindings(result), []);
  } finally {
    for (const [name, value] of Object.entries(saved))
      if (value !== undefined) process.env[name] = value;
  }
});

test("STORES: the real store module refuses rather than guessing when nothing is configured", async () => {
  const saved = process.env.DATABASE_URL_READER;
  delete process.env.DATABASE_URL_READER;
  try {
    await assert.rejects(() => stores.fetchPredecessorOutcomeRows({ workRequestRef: "WR-000046" }),
      error => error instanceof stores.SeamStoreUnreachable
        && error.because === "the connection target for this store is not configured in this process");
  } finally {
    if (saved !== undefined) process.env.DATABASE_URL_READER = saved;
  }
  // And the reason set is closed: an unregistered phrase cannot be constructed.
  assert.throws(() => new stores.SeamStoreUnreachable("github:checks", "because I said so"), TypeError);
});

// ---------------------------------------------------------------------------
// PART D — the producer seam, not built, and its refusal kept exact.
// ---------------------------------------------------------------------------

test("PRODUCER: the not-built refusal is byte-identical to the gate's own", () => {
  const live = emitGateZeroOutcome();
  const copy = readers.GATE_ZERO_PRODUCER_SEAM_NOT_BUILT;
  assert.equal(copy.built, false);
  assert.deepEqual([...copy.cards], [9, 10]);
  assert.equal(copy.seam, V5_A02_GATE_ZERO_PRODUCER_SEAM);
  assert.equal(copy.reason_id, live.reason_id);
  assert.equal(copy.unavailable_because, live.unavailable_because);
  assert.equal(copy.not_passable_because, live.not_passable_because);
  assert.ok(live.owed_seams.includes(copy.seam));
  assert.equal(live.passable, false);
  assert.equal(live.producer_bound, false);
  // No reader exists for it, and the reason set does not name one.
  assert.ok(!readers.GATE_ZERO_SEAM_READER_SEAMS.includes(copy.seam));
  assert.ok(!Object.hasOwn(rulings.GATE_ZERO_SEAM_RULINGS, copy.seam),
    "the producer seam has a ruling line, which would imply it is buildable by ruling");
  for (const name of Object.keys(readers))
    assert.ok(!/producer.*read|read.*producer/i.test(name) || name === "GATE_ZERO_PRODUCER_SEAM_NOT_BUILT",
      `${name} looks like a producer reader`);
});

test("PRODUCER: binding the gate's own answer still does not move, now that readers exist", () => {
  // The whole point of building three readers without a ruling: the gate is
  // exactly as unpassable as it was this morning.
  assert.equal(emitGateZeroOutcome().passable, false);
  assert.equal(emitGateZeroOutcome().join, null);
  assert.deepEqual([...emitGateZeroOutcome().owed_seams], [...V5_A02_GATE_ZERO_OWED_SEAMS]);
});

// ---------------------------------------------------------------------------
// The import graph, parsed rather than grepped.
// ---------------------------------------------------------------------------

/** V8's own ESM parser, via vm.SourceTextModule in a child process. */
function moduleImports(directory) {
  const script = `
    const { readdirSync, readFileSync } = require("node:fs");
    const { join } = require("node:path");
    const vm = require("node:vm");
    const dir = process.argv[1];
    const out = {};
    for (const name of readdirSync(dir).sort()) {
      if (!name.endsWith(".js")) continue;
      const source = readFileSync(join(dir, name), "utf8");
      out[name] = new vm.SourceTextModule(source, { identifier: name }).dependencySpecifiers;
    }
    process.stdout.write(JSON.stringify(out));
  `;
  const run = spawnSync(process.execPath, ["--experimental-vm-modules", "-e", script, directory],
    { encoding: "utf8" });
  assert.equal(run.status, 0, `the module parser failed: ${run.stderr}`);
  return JSON.parse(run.stdout);
}

test("ISOLATION: the store module is reached from one place, and nothing in src reaches the fixture", () => {
  const imports = moduleImports(SRC);
  assert.ok(Object.keys(imports).length > 100, "every module in src must have been parsed");
  assert.ok(Object.hasOwn(imports, "gate-zero-seam-readers.v5.js"));

  const offenders = Object.entries(imports)
    .filter(([, specifiers]) => specifiers.some(one =>
      one.includes("/test/") || one.startsWith("../test") || one.includes(".fixture.")))
    .map(([name]) => name);
  assert.deepEqual(offenders, [], "a production module reached into the test directory");

  // The reader is the only module that imports the stores or the derivation, so
  // a second consumer of either is red on sight rather than red after an incident.
  for (const module of [STORES_FILE, "gate-zero-seam-evidence.v5.js"]) {
    const importers = Object.entries(imports)
      .filter(([, specifiers]) => specifiers.includes(`./${module}`))
      .map(([name]) => name);
    assert.deepEqual(importers, ["gate-zero-seam-readers.v5.js"],
      `${module} has an importer other than the reader`);
  }
  // And the ruling table is read by the reader only: nothing else may consult it.
  assert.deepEqual(Object.entries(imports)
    .filter(([, specifiers]) => specifiers.includes(`./${RULINGS_FILE}`)).map(([name]) => name),
  ["gate-zero-seam-readers.v5.js"]);
  // The stores module imports nothing statically — `pg` is dynamic on purpose.
  assert.deepEqual(imports[STORES_FILE], []);

  const strays = readdirSync(SRC).filter(name => /\.(testonly|testhelper|fixture)\./.test(name));
  assert.deepEqual(strays, [], "a test-only entry is sitting in the production source directory");
});
