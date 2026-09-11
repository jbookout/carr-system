// V5-A02, the seam half — the three evidence readers Gate Zero is owed, proved
// in four parts that must not be confused with each other.
//
// PART A, THE RULING GATE IS SHUT. Each reader returns the gate's OWN answer,
// pinned by the repository's canonical digest rather than by a field-by-field
// comparison against a shape that happens to match today. No store is touched:
// the real store module throws without a DSN, and Part A passes with no DSN set,
// which is the observable proof that nothing was opened.
//
// PART B, THE PUBLIC SURFACE, AND THE SWEEP HAS NO EXEMPTIONS. The export list
// of all three modules is exactly enumerated. EVERY export is swept — constants
// by value, and callables by CALLING them, with no argument and with each
// hostile argument, and sweeping what comes back or what they throw. The sweep
// looks for the closed union of privileged words as exact value, as token, AND
// as raw substring; for any `would_*` or `*_if_authoritative` key; and for the
// boolean `true` anywhere at all, under any key.
//
// THE STRING ALLOWLIST THE EARLIER DRAFT CARRIED IS DELETED. It existed because
// the reader module reused the gate's own seam names and reason ids verbatim,
// and those contain "read" ("read-only", "readback") and "active"
// ("scheduler-active-receipt"). The module no longer emits any of them: seams
// are addressed by opaque card tokens, and its findings are its own words. So
// there is nothing left to exempt and nothing is exempted.
//
// WHAT REPLACES IT IS NOT AN ALLOWLIST BUT AN IDENTITY. While no seam is ruled,
// a reader's answer IS the gate's answer — the object `readGateZeroPredecessorJoin`
// or `readGateGraphAssurance` built, returned unmodified. That object is main's,
// not this slice's, and its strings are Gate Zero's live refusal: renaming them
// would change what Gate Zero says to every caller, which this PR must not do.
// So a returned value that is digest-identical to a gate answer is asserted to
// BE that answer rather than swept for words. Identity is the stronger check —
// a word sweep permits any new string that dodges the union, and identity
// permits no new string at all.
//
// PART C, THE RULED PATH, ON A STAGED TREE. mcp-server/src is copied into a
// scratch directory, a fixture decision id is pasted onto each of the three
// `decision_id:` lines — the exact lines Joe will paste onto — and the copied
// reader is imported. Four stagings:
//
//   * with the store module REPLACED by ./gate-zero-seam-stores.v5.fixture.mjs,
//     whose rows are shaped exactly as production writes them, which proves
//     every clause including one negative row per field of card 12's
//     receipt-binding clause;
//   * with ./gate-zero-seam-stores.v5.receipt-fixture.mjs, which pulls the
//     acceptance receipt's hash apart from the proposal's — the one thing no
//     production row can do, and the clause mutation testing found unproved;
//   * with card 11's `store_ref:` line changed to a store its reader does not
//     serve, which must refuse with the gate's own answer and must NOT fetch;
//   * with the REAL store module kept and no connection configured, which proves
//     the unreachable refusals are the real code's, not a fixture's.
//
// What runs in all four is the real reader, the real ruling gate and the real
// derivation. The staging is how a ruling is simulated without a ruling, and it
// doubles as proof that the paste procedure in the seams report actually works:
// if the three lines ever stop being three lines of that exact shape, Part C
// fails on the anchor count rather than silently proving nothing.
//
// PART D, THE PRODUCER SEAM IS NOT BUILT, AND IS NOT COPIED. Cards 9 and 10 have
// no ruling line, no reader and no restatement of their refusal anywhere in this
// slice — `emitGateZeroOutcome()` still says it, once.
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
  V5_A02_GATE_ZERO_OWED_SEAMS,
  V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS,
  V5_A02_GATE_ZERO_PRODUCER_SEAM,
  V5_A02_SCHEDULER_STEP_REF,
  emitGateZeroOutcome,
  readGateGraphAssurance,
  readGateZeroPredecessorJoin,
} from "../src/gate-zero-assurance.v5.js";

import * as readers from "../src/gate-zero-seam-readers.v5.js";
import * as rulings from "../src/gate-zero-seam-rulings.v5.js";
import * as stores from "../src/gate-zero-seam-stores.v5.js";
import * as fixtureStores from "./gate-zero-seam-stores.v5.fixture.mjs";
import * as receiptStores from "./gate-zero-seam-stores.v5.receipt-fixture.mjs";

const SRC = fileURLToPath(new URL("../src", import.meta.url));
const FIXTURE_STORE_FILE = fileURLToPath(new URL("./gate-zero-seam-stores.v5.fixture.mjs", import.meta.url));
const RECEIPT_STORE_FILE = fileURLToPath(new URL("./gate-zero-seam-stores.v5.receipt-fixture.mjs", import.meta.url));
const RULINGS_FILE = "gate-zero-seam-rulings.v5.js";
const READERS_FILE = "gate-zero-seam-readers.v5.js";
const STORES_FILE = "gate-zero-seam-stores.v5.js";

/** The line the ruling goes on. If this string stops matching, nothing is proved. */
const DECISION_ID_LINE = "    decision_id: null,\n";

/** Card 11's store line, the anchor for the wrong-store staging. */
const CARD_11_STORE_LINE = `    store_ref: "record-layer:work-request-outcome-feedback",\n`;

/**
 * Three distinct well-formed ids, in the order the `decision_id:` lines appear
 * in the ruling table — card 11, card 12, card 13. Distinct on purpose: each
 * reader is asserted to come back with ITS OWN card's id, so a reader that
 * looked up the wrong card fails here instead of reading the wrong store later.
 */
const FIXTURE_DECISION_IDS = Object.freeze([
  "11111111-1111-4111-8111-111111111111",
  "22222222-2222-4222-8222-222222222222",
  "33333333-3333-4333-8333-333333333333",
]);

// ---------------------------------------------------------------------------
// PART A — the ruling gate is shut, and the refusal is the gate's own answer.
// ---------------------------------------------------------------------------

const READERS_UNDER_TEST = [
  ["readPredecessorOutcomeEvidence", readers.readPredecessorOutcomeEvidence, readGateZeroPredecessorJoin],
  ["readSchedulerCanaryEvidence", readers.readSchedulerCanaryEvidence, readGateZeroPredecessorJoin],
  ["readGateConclusionEvidence", readers.readGateConclusionEvidence, readGateGraphAssurance],
];

/** The two answers main's gate gives. Nothing in this slice may alter either. */
const GATE_ANSWER_DIGESTS = new Set([
  digest(readGateZeroPredecessorJoin()),
  digest(readGateGraphAssurance()),
]);

test("RULING: no card is ruled, and the lookup is the only way to ask", () => {
  // The table is not exported: `seamRulingRef` is the whole surface, and today
  // it answers null for every card because every decision id is null.
  assert.deepEqual(Object.keys(rulings).sort(), ["seamRulingRef"]);
  for (const card of ["card:11", "card:12", "card:13"])
    assert.equal(rulings.seamRulingRef(card), null, card);
});

test("RULING: the lookup takes a card token and never a decision id", () => {
  // Arity is the boundary, and so is the vocabulary: there is no argument that
  // could carry a ruling, and a well-formed decision id handed in as the card
  // token is not a card token.
  assert.equal(rulings.seamRulingRef.length, 1);
  const hostiles = [undefined, null, {}, [], 0, true, Symbol("x"),
    FIXTURE_DECISION_IDS[0], "card:14", "__proto__", "constructor", "toString",
    { decision_id: FIXTURE_DECISION_IDS[0] }, ...hostileQueries()];
  // Indexed, not stringified: one of these throws from its own toString.
  hostiles.forEach((hostile, index) =>
    assert.equal(rulings.seamRulingRef(hostile), null, `hostile argument ${index}`));
});

test("RULING: the three decision_id lines are exactly three lines of the pasted shape", () => {
  // The seams report tells Joe to paste onto these lines. If the file's shape
  // drifts, that instruction is wrong, and this is where it becomes visible.
  const source = readFileSync(join(SRC, RULINGS_FILE), "utf8");
  assert.equal(source.split(DECISION_ID_LINE).length - 1, 3,
    "the ruling table no longer holds exactly three `decision_id: null,` lines");
  assert.equal(source.split(CARD_11_STORE_LINE).length - 1, 1,
    "card 11's store_ref line is no longer the single line the staging edits");
});

test("RULING SHUT: each reader returns the gate's own answer, byte for byte", async () => {
  // The gate builds a fresh frozen object per call, so identity is not available
  // to assert. What IS available is stronger than deep-equality on its own: the
  // answer is byte-identical under the repository's own canonical digest, AND
  // the reader's source returns the gate function's result directly rather than
  // assembling a shape that happens to match today.
  const source = readFileSync(join(SRC, READERS_FILE), "utf8");
  assert.equal((source.match(/return readGateZeroPredecessorJoin\(\);/g) ?? []).length, 2,
    "a reader stopped delegating its refusal to the gate");
  assert.equal((source.match(/return readGateGraphAssurance\(\);/g) ?? []).length, 1,
    "the conclusion reader stopped delegating its refusal to the gate");

  for (const [name, reader, gateFn] of READERS_UNDER_TEST) {
    const expected = gateFn();
    const got = await reader({ stepRef: "step:wr46-dissolution-outcome" });
    assert.deepEqual(got, expected, `${name} returned a different refusal than the gate's`);
    assert.ok(Object.isFrozen(got), name);
    assert.equal(digest(got), digest(expected), name);
    assert.equal(got.status, "unavailable", name);
    assert.equal(got.decision, "refuse", name);
    assert.equal(got.caller_evidence_admitted, false, name);
  }
  // And the reason ids are the ones the gate refuses with today, not new words.
  assert.equal(readGateZeroPredecessorJoin().reason_id, "predecessor_outcome_reader_unavailable");
  assert.equal(readGateGraphAssurance().reason_id, "gate_conclusion_reader_unavailable");
});

/**
 * REQUIREMENT (4), AND IT IS THE WHOLE POINT OF SHIPPING THIS UNRULED: with the
 * three decision ids null, every caller of every reader gets, byte for byte, the
 * JSON main's Gate Zero already returns. There is no input — well formed,
 * malformed, hostile or absent — for which that is not true.
 */
test("BYTE-IDENTICAL TO MAIN: the answer does not move for any query, valid or not", async () => {
  const queries = [
    undefined, null, {}, [], "step:wr46-dissolution-outcome", 1, true,
    { stepRef: "step:wr40-repository-outcome", outcomeHash: `sha256:${"a".repeat(64)}` },
    { serviceKey: "carr-fleet-sync", canaryRunKey: "canary-join" },
    { headSha: "a".repeat(40), checkName: "db-acceptance" },
    { decision_id: FIXTURE_DECISION_IDS[0], stepRef: "step:wr46-dissolution-outcome" },
    { ruling_decision_ref: FIXTURE_DECISION_IDS[0] },
    { card_ref: "card:11", store_ref: "github:checks", finding: "gate_conclusion_observed" },
    ...hostileQueries(),
  ];
  for (const [name, reader, gateFn] of READERS_UNDER_TEST) {
    const baseline = digest(gateFn());
    for (const query of queries) {
      const got = await reader(query);
      assert.equal(digest(got), baseline,
        `${name} answered differently for ${safeLabel(query)}`);
      assert.ok(GATE_ANSWER_DIGESTS.has(digest(got)), name);
    }
  }
});

test("RULING SHUT: nothing in the reader or the ruling table reaches the environment", () => {
  // A seam an env var could open is a seam any shell could open. Only the store
  // layer reads the environment, and only for WHERE a ruled store lives.
  for (const file of [RULINGS_FILE, READERS_FILE]) {
    const source = readFileSync(join(SRC, file), "utf8");
    assert.equal(/process\s*\.\s*env/.test(source), false, `${file} names the process environment`);
  }
  // And each reader takes one query. A second argument cannot carry a ruling
  // because there is no second argument to carry it.
  for (const [name, reader] of READERS_UNDER_TEST)
    assert.equal(reader.length, 1, `${name} takes more than the query`);
});

// ---------------------------------------------------------------------------
// PART B — the public surface, and the privileged-word sweep.
// ---------------------------------------------------------------------------

const EXPECTED_EXPORTS = Object.freeze({
  readers: [
    "GATE_ZERO_SEAM_READERS_SCHEMA_VERSION",
    "readGateConclusionEvidence",
    "readPredecessorOutcomeEvidence",
    "readSchedulerCanaryEvidence",
  ],
  rulings: ["seamRulingRef"],
  stores: [
    "SEAM_STORE_UNREACHABLE_REASONS",
    "SeamStoreUnreachable",
    "fetchCheckConclusionRows",
    "fetchPredecessorOutcomeRows",
    "fetchSchedulerLedgerRows",
  ],
});

/** The closed union, verbatim from the standing rule of 2026-09-11. */
const PRIVILEGED_WORDS = Object.freeze([
  "allow", "commit", "prompt", "suppress", "release", "read", "covered", "drafted",
  "proposed", "queued", "healthy", "passing", "ok", "pass", "satisfied", "complete",
  "admitted", "resumed", "attended", "verified", "present", "equivalent",
  "operational", "active", "green", "joins_exactly", "coverage_complete", "favorable",
]);

/**
 * Four sweeps over one value, in order of strictness, AND NO CARVE-OUT OF ANY
 * KIND. There is no set of blessed strings in this file.
 *
 *   exact      the whole string IS a privileged word.
 *   token      a word of the string, split on every non-alphanumeric run, IS one.
 *   substring  the privileged word appears anywhere in the string.
 *   shape      any `would_*` or `*_if_authoritative` key, and the boolean `true`
 *              anywhere at all, under any key, at any depth.
 *
 * The boolean rule is wider than the standing rule's "a privileged key that is
 * literally true", on purpose: a clause result is reported as "held", "failed" or
 * "unknown", so a `true` on this surface means someone reintroduced a yes/no
 * answer under a name the union does not happen to list.
 */
function privilegedFindings(value, path = "$", found = []) {
  if (value === true) { found.push(`${path} is the boolean true`); return found; }
  if (Array.isArray(value)) {
    value.forEach((entry, index) => privilegedFindings(entry, `${path}[${index}]`, found));
    return found;
  }
  if (value !== null && typeof value === "object") {
    for (const [key, entry] of Object.entries(value)) {
      const at = `${path}.${key}`;
      if (/^would_/.test(key) || key.includes("_if_authoritative"))
        found.push(`${at} is a conditional key on the public surface`);
      privilegedFindings(entry, at, found);
    }
    return found;
  }
  if (typeof value !== "string") return found;
  const tokens = value.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  for (const word of PRIVILEGED_WORDS) {
    if (value === word) found.push(`${path} is the privileged word ${word}`);
    else if (tokens.includes(word)) found.push(`${path} carries the privileged token ${word}`);
    else if (value.includes(word)) found.push(`${path} carries ${word} as a substring`);
  }
  return found;
}

/**
 * The sweep, applied to one value.
 *
 * THE ONE STRUCTURAL FACT, and it is an identity rather than an allowlist: while
 * no seam is ruled, a reader returns the GATE'S answer, unmodified. That object
 * belongs to gate-zero-assurance.v5.js on main; its strings are Gate Zero's live
 * refusal, and renaming them would change what the gate says to every caller,
 * which this change must not do. So a value that is digest-identical to a gate
 * answer is asserted to BE one — which admits no new string at all, privileged or
 * otherwise — and everything else is swept with no exemption whatsoever.
 */
function assertSwept(label, value) {
  if (value !== null && typeof value === "object" && GATE_ANSWER_DIGESTS.has(digest(value))) return;
  assert.deepEqual(privilegedFindings(value, label), [], `${label} carries a privileged outcome`);
}

function safeLabel(value) {
  try { return JSON.stringify(value) ?? String(value); } catch { return "<unserializable>"; }
}

test("SWEEP: the sweep itself catches a privileged outcome when one is planted", () => {
  // A sweep nobody has seen fail is a sweep nobody has tested. One plant per
  // mechanism, so a broken mechanism cannot hide behind a working one.
  assert.ok(privilegedFindings({ ok: true }).length > 0);
  assert.ok(privilegedFindings({ receipt_binding: true }).length > 0,
    "the boolean rule missed a true under a name the union does not list");
  assert.ok(privilegedFindings({ status: "green" }).length > 0);
  assert.ok(privilegedFindings({ status: "the gate is green now" }).length > 0);
  assert.ok(privilegedFindings({ would_allow_if_authoritative: false }).length > 0);
  assert.ok(privilegedFindings({ note: "committed" }).length > 0,
    "the substring sweep missed a privileged word inside a longer one");
  // And the gate's own strings are NOT blessed as strings: only a whole object
  // that IS a gate answer is. A gate word lifted into a new sentence is caught.
  assert.ok(privilegedFindings({ note: V5_A02_GATE_ZERO_PRODUCER_SEAM }).length > 0,
    "a gate seam name pasted into a new value must still be caught");
  assert.ok(privilegedFindings({ note: V5_A02_SCHEDULER_STEP_REF }).length > 0);
  assert.throws(() => assertSwept("planted", { conclusion: "green" }));
});

test("SURFACE: the export list of all three modules is exactly enumerated", () => {
  for (const [label, namespace] of [["readers", readers], ["rulings", rulings], ["stores", stores]])
    assert.deepEqual(Object.keys(namespace).sort(), [...EXPECTED_EXPORTS[label]].sort(), label);
  // No classifier, no binder, no conditional name anywhere on the surface.
  for (const [label, namespace] of [["readers", readers], ["rulings", rulings], ["stores", stores]])
    for (const name of Object.keys(namespace)) {
      assert.ok(!/^(classify|evaluate|derive|bind|create|set|would)/.test(name),
        `${label}.${name} is a binder or classifier name on the public surface`);
      assert.ok(!/would_|_if_authoritative/.test(name), `${label}.${name} is a conditional name`);
    }
  // The ruling table, the findings vocabulary, the reason ids, the seam list and
  // the producer restatement are all gone from the surface. Naming them here
  // means a future edit that re-exports one fails on this line.
  for (const gone of ["GATE_ZERO_SEAM_RULINGS", "GATE_ZERO_SEAM_STORE_REFS", "seamRulingDecisionRef",
    "DECISION_ID", "GATE_ZERO_SEAM_FINDINGS", "GATE_ZERO_SEAM_READER_REASON_IDS",
    "GATE_ZERO_SEAM_READER_SEAMS", "GATE_ZERO_PRODUCER_SEAM_NOT_BUILT", "gateZeroSeamRulingStatus",
    "wouldAdmitPredecessorOutcome", "wouldReportSchedulerCanary", "wouldReportGateConclusion"]) {
    assert.ok(!Object.hasOwn(readers, gone), `${gone} is back on the reader surface`);
    assert.ok(!Object.hasOwn(rulings, gone), `${gone} is back on the ruling surface`);
  }
  // And the module that held the exported classifiers is gone, not hidden.
  assert.ok(!readdirSync(SRC).includes("gate-zero-seam-evidence.v5.js"),
    "the exported-classifier module is still in src");
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
      get checkName() { throw new Error(`${HOSTILE_MARKER}-getter`); },
      get workRequestRef() { throw new Error(`${HOSTILE_MARKER}-getter`); },
      get commitSha() { throw new Error(`${HOSTILE_MARKER}-getter`); } },
    { stepRef: `${HOSTILE_MARKER}-step`, outcomeHash: `${HOSTILE_MARKER}-hash`,
      serviceKey: `${HOSTILE_MARKER}-service`, canaryRunKey: `${HOSTILE_MARKER}-run`,
      headSha: `${HOSTILE_MARKER}-sha`, checkName: `${HOSTILE_MARKER}-check`,
      workRequestRef: `${HOSTILE_MARKER}-wr`, commitSha: `${HOSTILE_MARKER}-commit` },
    { stepRef: { toString() { throw new Error(`${HOSTILE_MARKER}-tostring`); } } },
    { stepRef: Symbol(`${HOSTILE_MARKER}-symbol`) },
    { stepRef: ["step:wr46-dissolution-outcome"], outcomeHash: [`sha256:${"a".repeat(64)}`] },
    { stepRef: "green", outcomeHash: "ok", serviceKey: "allow", canaryRunKey: "passing",
      headSha: "complete", checkName: "verified" },
  ];
}

test("SWEEP: every export of every seam module, constants and callables alike", async () => {
  const saved = {};
  for (const name of ["DATABASE_URL_READER", "GITHUB_TOKEN", "GITHUB_REPOSITORY"]) {
    saved[name] = process.env[name];
    delete process.env[name];
  }
  try {
    for (const [label, namespace] of [["readers", readers], ["rulings", rulings], ["stores", stores]]) {
      for (const [name, value] of Object.entries(namespace)) {
        const at = `${label}.${name}`;
        if (typeof value !== "function") { assertSwept(at, value); continue; }
        // A CALLABLE IS SWEPT BY CALLING IT — with no argument, and with every
        // hostile shape — and what it returns or throws is swept in turn. A
        // constructor is swept by constructing it with its own closed reasons.
        if (name === "SeamStoreUnreachable") {
          for (const because of stores.SEAM_STORE_UNREACHABLE_REASONS) {
            const error = new value("github:checks", because);
            assertSwept(`${at}.message`, error.message);
            assertSwept(`${at}.because`, error.because);
            assertSwept(`${at}.store_ref`, error.store_ref);
          }
          assert.throws(() => new value("github:checks", "because I said so"), TypeError);
          continue;
        }
        for (const argument of [undefined, ...hostileQueries()]) {
          let outcome;
          try {
            outcome = await value(argument);
          } catch (error) {
            assertSwept(`${at}.throw.message`, error?.message ?? null);
            assertSwept(`${at}.throw.because`, error?.because ?? null);
            continue;
          }
          assertSwept(`${at}(${safeLabel(argument)})`, outcome);
          assert.ok(!JSON.stringify(outcome ?? null).includes(HOSTILE_MARKER),
            `${at} leaked caller text`);
        }
      }
    }
  } finally {
    for (const [name, value] of Object.entries(saved))
      if (value !== undefined) process.env[name] = value;
  }
});

test("HOSTILE: no hostile query throws out of a reader, and none of its bytes come back", async () => {
  for (const [name, reader] of READERS_UNDER_TEST)
    for (const query of hostileQueries()) {
      const result = await reader(query);
      const serialized = JSON.stringify(result);
      assert.ok(!serialized.includes(HOSTILE_MARKER),
        `${name} leaked caller text: ${serialized.slice(0, 200)}`);
      assert.equal(result.decision, "refuse", name);
      assert.ok(GATE_ANSWER_DIGESTS.has(digest(result)), name);
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
function stageTree({ storeFile = null, card11StoreRef = null } = {}) {
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
  let ruled = source.replaceAll(DECISION_ID_LINE,
    () => `    decision_id: "${FIXTURE_DECISION_IDS[pasted++]}",\n`);
  assert.equal(pasted, 3, "the staging pasted the wrong number of rulings");
  assert.ok(!ruled.includes(DECISION_ID_LINE), "a null decision id survived the staging");
  if (card11StoreRef !== null) {
    assert.ok(ruled.includes(CARD_11_STORE_LINE), "card 11's store line moved");
    ruled = ruled.replace(CARD_11_STORE_LINE, `    store_ref: "${card11StoreRef}",\n`);
  }
  writeFileSync(path, ruled);

  if (storeFile !== null) cpSync(storeFile, join(target, STORES_FILE));
  return target;
}

async function stagedReaders(options) {
  return import(pathToFileURL(join(stageTree(options), READERS_FILE)).href);
}

test.after(() => {
  for (const base of staged) rmSync(base, { recursive: true, force: true });
});

test("STAGING: both fixture store modules cover every export the real one has", () => {
  const real = Object.keys(stores).sort();
  for (const [label, fixture] of [["fixture", fixtureStores], ["receipt", receiptStores]]) {
    const names = new Set(Object.keys(fixture));
    for (const name of real)
      assert.ok(names.has(name), `the ${label} store is missing ${name}, so a path would go unproved`);
    for (const name of [...names].sort())
      if (!real.includes(name))
        assert.ok(name.startsWith("FIXTURE_"), `${label}.${name} is a fixture-only export not named as one`);
  }
});

test("RULED: a pasted decision id is what opens the seam, and nothing else", async () => {
  const ruled = await stagedReaders({ storeFile: FIXTURE_STORE_FILE });
  const opened = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr46-dissolution-outcome", outcomeHash: fixtureStores.FIXTURE_ACCEPTED_HASH });
  assert.ok(!GATE_ANSWER_DIGESTS.has(digest(opened)), "the staged ruling did not open the seam");
  // The unruled module in this same process is untouched by the staging.
  const shut = await readers.readPredecessorOutcomeEvidence({
    stepRef: "step:wr46-dissolution-outcome", outcomeHash: fixtureStores.FIXTURE_ACCEPTED_HASH });
  assert.equal(digest(shut), digest(readGateZeroPredecessorJoin()));
});

test("RULED: the predecessor reader admits only an accepted outcome whose receipt hash matches", async () => {
  const ruled = await stagedReaders({ storeFile: FIXTURE_STORE_FILE });
  const accepted = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr46-dissolution-outcome", outcomeHash: fixtureStores.FIXTURE_ACCEPTED_HASH });
  assert.equal(accepted.status, "evidence_returned");
  assert.equal(accepted.decision, "report");
  assert.equal(accepted.finding, "predecessor_outcome_accepted_with_matching_hash");
  assert.equal(accepted.hash_match, "held");
  assert.equal(accepted.work_request_ref, "WR-000046");
  assert.equal(accepted.card_ref, "card:11");
  assert.equal(accepted.ruling_decision_ref, FIXTURE_DECISION_IDS[0],
    "card 11 took the wrong card's ruling");
  assert.equal(accepted.store_ref, "record-layer:work-request-outcome-feedback");
  assertSwept("card11.accepted", accepted);
  // No caller byte and no store byte: the step ref the caller named is not in
  // the answer, and neither is a feedback ref, an outcome or a timestamp.
  assert.ok(!Object.hasOwn(accepted, "step_ref"));
  for (const gone of ["feedback_ref", "stored_outcome", "accepted_at", "outcome"])
    assert.ok(!Object.hasOwn(accepted, gone), `${gone} is echoed into the answer`);

  // A forged hash — well-formed, and no row carries it.
  const forged = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr46-dissolution-outcome", outcomeHash: fixtureStores.FIXTURE_FORGED_HASH });
  assert.equal(forged.decision, "refuse");
  assert.equal(forged.finding, "predecessor_outcome_acceptance_receipt_hash_mismatch");
  assert.equal(forged.hash_match, "failed");

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
  assertSwept("card11.scheduler", scheduler);

  // A step outside the frozen four, and a malformed hash.
  const unknown = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:something-else", outcomeHash: fixtureStores.FIXTURE_ACCEPTED_HASH });
  assert.equal(unknown.reason_id, "unknown_predecessor_step");
  const badHash = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr46-dissolution-outcome", outcomeHash: "sha256:nope" });
  assert.equal(badHash.reason_id, "predecessor_query_invalid");
  assert.equal(badHash.invalid_field, "outcomeHash");
});

test("RULED: the receipt's hash is the one consulted, not the proposal's", async () => {
  // Found by mutation: in production ops.accept_sourced_work_request_outcome_feedback
  // writes the proposal hash and the receipt hash equal, so swapping the two
  // fields changed nothing any test could see, and the most load-bearing clause
  // in the slice was unproved. This staging's rows separate them, which no
  // production row does.
  const ruled = await stagedReaders({ storeFile: RECEIPT_STORE_FILE });
  const asked = receiptStores.FIXTURE_ASKED_HASH;

  // The receipt carries the asked-about hash; the proposal carries another.
  const receiptMatches = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr46-dissolution-outcome", outcomeHash: asked });
  assert.equal(receiptMatches.finding, "predecessor_outcome_accepted_with_matching_hash",
    "the reader stopped consulting the acceptance receipt's hash");
  assert.equal(receiptMatches.hash_match, "held");

  // The mirror: the PROPOSAL carries the asked-about hash and no receipt does.
  const proposalOnly = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr40-repository-outcome", outcomeHash: asked });
  assert.equal(proposalOnly.finding, "predecessor_outcome_acceptance_receipt_hash_mismatch",
    "a row matched on its proposal hash instead of its receipt hash");
  assert.equal(proposalOnly.decision, "refuse");

  // A receipt whose work_request_card detail is absent is a MISSING ROW. The
  // earlier store synthesized it as accepted with a null outcome and it could
  // still be admitted.
  const detailAbsent = await ruled.readPredecessorOutcomeEvidence({
    stepRef: "step:wr54-backup-recovery-outcome", outcomeHash: asked });
  assert.equal(detailAbsent.finding, "predecessor_outcome_detail_absent");
  assert.equal(detailAbsent.decision, "refuse");
  for (const result of [receiptMatches, proposalOnly, detailAbsent]) assertSwept("receipt", result);
});

test("RULED: card 12 reads the rows bin/run-scheduled.sh actually writes", async () => {
  const ruled = await stagedReaders({ storeFile: FIXTURE_STORE_FILE });
  const read = (canaryRunKey, serviceKey = "carr-fleet-sync") =>
    ruled.readSchedulerCanaryEvidence({ serviceKey, canaryRunKey });

  const joined = await read("canary-join");
  assert.equal(joined.decision, "report");
  assert.equal(joined.finding, "scheduler_canary_and_observation_join");
  assert.equal(joined.receipt_binding, "held");
  assert.equal(joined.observation_after_dispatch, "held");
  assert.equal(joined.canary_match, "held");
  assert.equal(joined.card_ref, "card:12");
  assert.equal(joined.ruling_decision_ref, FIXTURE_DECISION_IDS[1]);
  assert.equal(joined.store_ref, "control-plane:ops.service+ops.run");
  assertSwept("card12.joined", joined);
  for (const gone of ["dispatched_at", "observed_at", "scheduler_step_ref"])
    assert.ok(!Object.hasOwn(joined, gone), `${gone} is echoed into the answer`);

  // ONE NEGATIVE ROW PER FIELD the receipt-binding clause reads. Each changes a
  // single field of the row the wrapper writes, so a clause that stopped reading
  // one field fails on exactly one case.
  for (const key of ["canary-today", "canary-hand-run", "canary-probe", "canary-foreign-wrapper"]) {
    const result = await read(key);
    assert.equal(result.decision, "refuse", key);
    assert.equal(result.finding, "scheduler_canary_not_bound_to_receipt", key);
    assert.equal(result.receipt_binding, "failed", key);
    assertSwept(`card12.${key}`, result);
  }

  // Dispatch and observation share one instant: strictly-after is strict.
  const sameInstant = await read("canary-same-instant");
  assert.equal(sameInstant.decision, "refuse");
  assert.equal(sameInstant.finding, "scheduler_observation_not_after_dispatch");
  assert.equal(sameInstant.observation_after_dispatch, "failed");

  const mismatch = await read("canary-mismatch");
  assert.equal(mismatch.decision, "refuse");
  assert.equal(mismatch.finding, "scheduler_observation_canary_mismatch");
  assert.equal(mismatch.canary_match, "failed");

  for (const [key, expected] of [
    ["canary-inflight", "scheduler_observation_absent"],
    ["canary-never-ran", "scheduler_dispatch_row_absent"],
  ]) {
    const result = await read(key);
    assert.equal(result.decision, "refuse", key);
    assert.equal(result.finding, expected, key);
    assert.equal(result.receipt_binding, "unknown",
      `${key} guessed a clause with no row to read it from`);
  }

  // A store that answers about a DIFFERENT store than the one the ruling names
  // is not answered over. These rows would otherwise join cleanly.
  const wrongStore = await read(fixtureStores.FIXTURE_WRONG_STORE);
  assert.equal(wrongStore.decision, "refuse");
  assert.equal(wrongStore.reason_id, "store_ref_not_the_ruled_one");
  assert.equal(wrongStore.finding, null);
  assert.equal(wrongStore.store_ref, "control-plane:ops.service+ops.run",
    "the answer reported the store that replied instead of the store that was ruled");
  assertSwept("card12.wrongStore", wrongStore);

  const noService = await read("canary-join", "carr-not-in-the-ledger");
  assert.equal(noService.finding, "scheduler_service_row_absent");

  const badQuery = await ruled.readSchedulerCanaryEvidence({ serviceKey: "Not A Key", canaryRunKey: "x" });
  assert.equal(badQuery.reason_id, "scheduler_query_invalid");
  assert.equal(badQuery.invalid_field, "serviceKey");
});

test("SCHEMA: the source_kind the clause requires is one db/schema.sql permits", () => {
  // The defect this replaces: the clause required `source_kind === "scheduler"`,
  // which ops.run's own check constraint forbids, so it could never have matched
  // a real row. Both halves are read from the files that define them.
  const repo = fileURLToPath(new URL("../..", import.meta.url));
  const schema = readFileSync(join(repo, "db/schema.sql"), "utf8");
  const constraint = /run_source_kind_check CHECK \(\(source_kind = ANY \(ARRAY\[([^\]]*)\]\)\)\)/.exec(schema);
  assert.ok(constraint, "ops.run's source_kind constraint is no longer where this test reads it");
  const permitted = [...constraint[1].matchAll(/'([a-z_]+)'::text/g)].map(one => one[1]);
  assert.deepEqual(permitted.sort(), ["collector", "operator", "registry", "wrapper"]);
  assert.ok(!permitted.includes("scheduler"), "ops.run permits a scheduler source_kind after all");

  const wrapper = readFileSync(join(repo, "bin/run-scheduled.sh"), "utf8");
  assert.ok(/--source-kind wrapper --source-ref bin\/run-scheduled\.sh/.test(wrapper),
    "bin/run-scheduled.sh no longer writes the source kind and ref the clause binds to");
  // And the clause binds to those exact two strings, not to an invented one.
  const source = readFileSync(join(SRC, READERS_FILE), "utf8");
  assert.ok(/const SCHEDULER_SOURCE_KIND = "wrapper";/.test(source));
  assert.ok(/const SCHEDULER_SOURCE_REF = "bin\/run-scheduled\.sh";/.test(source));
  assert.equal(/source_kind[^\n]*===\s*"scheduler"/.test(source), false,
    "the reader still compares source_kind against an impossible value");
});

test("RULED: the conclusion reader returns GitHub's own word, and translates nothing", async () => {
  const ruled = await stagedReaders({ storeFile: FIXTURE_STORE_FILE });
  const sha = fixtureStores.FIXTURE_COMMIT_SHA;

  const success = await ruled.readGateConclusionEvidence({ headSha: sha, checkName: "db-acceptance" });
  assert.equal(success.decision, "report");
  assert.equal(success.finding, "gate_conclusion_observed");
  assert.equal(success.conclusion, "success");
  assert.equal(success.store_ref, "github:checks");
  assert.equal(success.card_ref, "card:13");
  assert.equal(success.ruling_decision_ref, FIXTURE_DECISION_IDS[2]);
  assertSwept("card13.success", success);

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

  // A word GitHub does not document is not passed through: the only conclusion
  // strings a consumer sees are constants out of the reader module.
  const invented = await ruled.readGateConclusionEvidence({ headSha: sha, checkName: "invented-conclusion" });
  assert.equal(invented.finding, "gate_conclusion_unrecognized");
  assert.equal(invented.conclusion, null);
  assert.equal(invented.decision, "refuse");
  assertSwept("card13.invented", invented);

  const badQuery = await ruled.readGateConclusionEvidence({ headSha: "nope", checkName: "db-acceptance" });
  assert.equal(badQuery.reason_id, "gate_conclusion_query_invalid");
  assert.equal(badQuery.invalid_field, "headSha");
});

test("RULED: a ruling naming a store this reader does not serve opens nothing", async () => {
  // Criterion (c). The earlier reader checked only that the ruled ref was a
  // member of a global union, then called its own hard-coded fetcher and
  // reported the ruled ref regardless — so a ruling naming `github:checks` for
  // card 11 still queried the database and said github:checks.
  //
  // The REAL store module is staged here with no DSN configured: if a row were
  // fetched the answer would be `predecessor_outcome_store_unreachable`. It is
  // the gate's own refusal instead, so nothing was opened.
  const ruled = await stagedReaders({ card11StoreRef: "github:checks" });
  const saved = process.env.DATABASE_URL_READER;
  delete process.env.DATABASE_URL_READER;
  try {
    const result = await ruled.readPredecessorOutcomeEvidence({
      stepRef: "step:wr46-dissolution-outcome", outcomeHash: `sha256:${"4".repeat(64)}` });
    assert.equal(digest(result), digest(readGateZeroPredecessorJoin()),
      "a ruling naming the wrong store opened the seam anyway");
    // The other two cards are ruled normally in this same staging and do open.
    const scheduler = await ruled.readSchedulerCanaryEvidence({
      serviceKey: "carr-fleet-sync", canaryRunKey: "canary-join" });
    assert.equal(scheduler.reason_id, "scheduler_ledger_unreachable",
      "card 12 did not open, so the staging proved nothing about card 11");
  } finally {
    if (saved !== undefined) process.env.DATABASE_URL_READER = saved;
  }
});

test("RULED: with the real stores and nothing configured, every seam reports unreachable", async () => {
  const ruled = await stagedReaders({});
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

    for (const result of [predecessor, scheduler, conclusion]) assertSwept("unreachable", result);
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
});

// ---------------------------------------------------------------------------
// PART D — the producer seam, not built, and said in exactly one place.
// ---------------------------------------------------------------------------

test("PRODUCER: cards 9 and 10 have no ruling line, no reader and no restatement", () => {
  const live = emitGateZeroOutcome();
  assert.equal(live.passable, false);
  assert.equal(live.producer_bound, false);
  assert.ok(live.owed_seams.includes(V5_A02_GATE_ZERO_PRODUCER_SEAM));

  // No ruling line: the lookup has no entry for it, so no paste could open it.
  assert.equal(rulings.seamRulingRef(V5_A02_GATE_ZERO_PRODUCER_SEAM), null);
  const rulingSource = readFileSync(join(SRC, RULINGS_FILE), "utf8");
  assert.equal(/^\s*"seam:/m.test(rulingSource), false,
    "the ruling table keys on seam names again, so the producer seam could take a line");

  // No restatement: the earlier draft exported a written-out copy of this
  // refusal, which had to be kept in sync. The gate says it once.
  const readerSource = readFileSync(join(SRC, READERS_FILE), "utf8");
  for (const phrase of [live.unavailable_because, live.not_passable_because])
    assert.ok(!readerSource.includes(phrase), "the producer refusal is copied into the reader module");
  for (const name of Object.keys(readers))
    assert.ok(!/producer/i.test(name), `${name} names the producer seam on the reader surface`);
});

test("PRODUCER: binding the gate's own answer still does not move, now that readers exist", () => {
  // The whole point of building three readers without a ruling: the gate is
  // exactly as unpassable as it was this morning.
  assert.equal(emitGateZeroOutcome().passable, false);
  assert.equal(emitGateZeroOutcome().join, null);
  assert.deepEqual([...emitGateZeroOutcome().owed_seams], [...V5_A02_GATE_ZERO_OWED_SEAMS]);
  assert.deepEqual([...V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS].length > 0, true);
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

test("ISOLATION: the store module is reached from one place, and nothing in src reaches a fixture", () => {
  const imports = moduleImports(SRC);
  assert.ok(Object.keys(imports).length > 100, "every module in src must have been parsed");
  assert.ok(Object.hasOwn(imports, READERS_FILE));

  const offenders = Object.entries(imports)
    .filter(([, specifiers]) => specifiers.some(one =>
      one.includes("/test/") || one.startsWith("../test") || one.includes(".fixture.")))
    .map(([name]) => name);
  assert.deepEqual(offenders, [], "a production module reached into the test directory");

  // The reader is the only module that imports the stores or the ruling table,
  // so a second consumer of either is red on sight rather than red after an
  // incident.
  for (const module of [STORES_FILE, RULINGS_FILE]) {
    const importers = Object.entries(imports)
      .filter(([, specifiers]) => specifiers.includes(`./${module}`))
      .map(([name]) => name);
    assert.deepEqual(importers, [READERS_FILE], `${module} has an importer other than the reader`);
  }
  // The stores module statically imports ONE thing, the tenant constant. `pg`
  // is dynamic on purpose, so the Worker bundle never pulls it in through here.
  assert.deepEqual(imports[STORES_FILE], ["./identity.js"]);

  const strays = readdirSync(SRC).filter(name => /\.(testonly|testhelper|fixture)\./.test(name));
  assert.deepEqual(strays, [], "a test-only entry is sitting in the production source directory");
});
