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
// by value, callables by CALLING them, and CONSTRUCTORS BY CONSTRUCTING THEM,
// with no argument and with each hostile argument in each position, and sweeping
// what comes back or what they throw. The sweep looks for the closed union of
// privileged words as exact value, as token, AND as raw substring; for any
// `would_*` or `*_if_authoritative` key; and for the boolean `true` anywhere at
// all, under any key.
//
// THE NAMED EXEMPTION THAT USED TO SIT HERE IS DELETED. An earlier draft swept
// `SeamStoreUnreachable` by constructing it with its OWN closed reasons and a
// well-formed store ref, which is the one input shape that could never leak, and
// skipped it otherwise. Under that exemption the constructor interpolated and
// retained whatever a caller handed it — a store ref, a reason, and the whole
// `cause` object — and the sweep never saw it. It is now constructed with the
// same hostile arguments as everything else, and its own properties, message,
// stack and cause are swept in turn.
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

/**
 * Values handed to an exported callable in EVERY argument position, including
 * the positions a constructor reads. hostileQueries() builds query OBJECTS,
 * which is the shape the three fetchers take; a constructor takes bare strings,
 * so the privileged words themselves are arguments here, alongside the object
 * shapes the amendment of 2026-09-11 puts in scope: a Proxy, a revoked Proxy, a
 * throwing getter, a throwing toString, a Symbol and an Error.
 */
function hostileArguments() {
  const throwingCoercion = {
    toString() { throw new Error(`${HOSTILE_MARKER}-tostring`); },
    valueOf() { throw new Error(`${HOSTILE_MARKER}-valueof`); },
  };
  const revocable = Proxy.revocable({ because: HOSTILE_MARKER }, {});
  revocable.revoke();
  return [
    undefined, null, 0, -1, true, false,
    HOSTILE_MARKER,
    `${HOSTILE_MARKER}: the checks source answer did not parse`,
    "the query did not finish ",
    ...PRIVILEGED_WORDS,
    "would_allow_if_authoritative",
    "the gate is green and the run is ok",
    Symbol(`${HOSTILE_MARKER}-symbol`),
    throwingCoercion,
    revocable.proxy,
    new Error(`${HOSTILE_MARKER}-cause`),
    { because: "green", store_ref: "allow", message: HOSTILE_MARKER, stack: HOSTILE_MARKER },
    ["allow", HOSTILE_MARKER],
    ...hostileQueries(),
  ];
}

/**
 * Argument lists for a constructor: each hostile value alone, in every position
 * at once, and behind a well-formed prefix — because a leak that needs the first
 * argument to be valid is still a leak, and the earlier special case only ever
 * tried the all-valid list.
 */
function constructorArgumentLists() {
  const lists = [[]];
  for (const hostile of hostileArguments()) {
    lists.push([hostile], [hostile, hostile], [hostile, hostile, hostile]);
    lists.push(["github:checks", hostile, hostile]);
    lists.push(["github:checks", "the query did not finish", hostile]);
  }
  return lists;
}

/**
 * Two structural questions, asked of a value without invoking it.
 *
 * EVERY ORDINARY FUNCTION IS A CONSTRUCTOR — that is the trap the first draft of
 * this sweep fell into. `Reflect.construct` accepts `seamRulingRef` as happily
 * as it accepts a class, so "is it constructible" cannot be the branch on its
 * own. What separates a class is its `prototype`: the engine installs it
 * non-writable on a class and writable on a function, and nothing in this file
 * has to trust a name or a `toString` to see it.
 */
function isConstructor(value) {
  try {
    Reflect.construct(String, [], value);
    return true;
  } catch {
    return false;
  }
}

function isClassConstructor(value) {
  const descriptor = Object.getOwnPropertyDescriptor(value, "prototype");
  return isConstructor(value) && descriptor !== undefined && descriptor.writable === false;
}

/**
 * One constructed object, against the whole contract this surface owes, in the
 * order a failure is most informative:
 *
 *   1. THE CAUSE IS NOT KEPT. A retained `cause` is the caller's whole object,
 *      handed back under a standard name.
 *   2. NOTHING THE CALLER WROTE, AND NO WORD OF THE CLOSED UNION, in the name,
 *      the message, the stack, the cause or any own property.
 *   3. THE SHAPE. Every own property is a non-writable, non-configurable DATA
 *      property — an accessor under one of these names runs caller code on
 *      every read, and a configurable property can become one afterwards — and
 *      an object carrying any own property at all is frozen, so nothing can be
 *      written over what it says.
 */
function assertSweptConstructed(at, built) {
  assert.ok(built !== null && typeof built === "object", `${at} did not construct an object`);
  const keys = Reflect.ownKeys(built);

  assert.ok(!Object.hasOwn(built, "cause"), `${at} carries an own cause`);
  assert.equal(built.cause, undefined, `${at} retained the caller's cause`);

  for (const key of ["name", "message", "stack", "cause"])
    assertSwept(`${at}.${key}`, built[key] ?? null);
  for (const key of keys) assertSwept(`${at}.own.${String(key)}`, built[key]);
  const readable = `${built.name}|${built.message}|${built.stack}|${safeLabel({ ...built })}`;
  assert.ok(!readable.includes(HOSTILE_MARKER), `${at} leaked caller text: ${readable.slice(0, 200)}`);

  for (const key of keys) {
    const where = `${at}.${String(key)}`;
    assert.ok(typeof key !== "symbol", `${where} is a symbol-keyed own property`);
    const descriptor = Object.getOwnPropertyDescriptor(built, key);
    assert.ok(Object.hasOwn(descriptor, "value"), `${where} is an accessor, not a data property`);
    assert.equal(descriptor.writable, false, `${where} is writable`);
    assert.equal(descriptor.configurable, false, `${where} is configurable`);
  }
  if (keys.length > 0) assert.ok(Object.isFrozen(built), `${at} is not frozen`);
}

/** Constructed with no argument, and with every hostile value in every position. */
function sweepConstruction(at, constructorUnderTest) {
  for (const argumentList of constructorArgumentLists()) {
    let built;
    try {
      built = Reflect.construct(constructorUnderTest, argumentList);
    } catch (error) {
      // A REFUSAL IS SWEPT TOO, and it may not quote the argument it refused:
      // the constructor that shipped threw ``${because} is not a registered
      // store-unreachable reason``, which handed a privileged word straight back
      // out of an exported callable.
      assertSwept(`${at}.throw.message`, error?.message ?? null);
      assertSwept(`${at}.throw.because`, error?.because ?? null);
      assert.ok(!String(error?.message ?? "").includes(HOSTILE_MARKER),
        `${at} quoted the caller back in its own refusal`);
      continue;
    }
    assertSweptConstructed(`${at}.new[${argumentList.length}]`, built);
  }
}

/**
 * A class, swept the two ways a class can be reached: constructed directly, and
 * constructed as somebody else's base. A SUBCLASS RUNS ITS OWN CONSTRUCTOR
 * AFTER THIS ONE and its instance passes `instanceof`, so a type whose fields a
 * consumer reads as facts has to refuse to be one.
 */
function sweepClassConstructor(at, classUnderTest) {
  assert.throws(() => classUnderTest(), TypeError, `${at} is callable without new`);
  sweepConstruction(at, classUnderTest);

  class Subclass extends classUnderTest {
    constructor() { super("github:checks", "the query did not finish"); }
  }
  let subclassed = "refused";
  try { subclassed = new Subclass(); } catch { /* refused, which is the point */ }
  assert.equal(subclassed, "refused", `${at} can be subclassed`);

  let foreign = "refused";
  try {
    foreign = Reflect.construct(classUnderTest,
      ["github:checks", "the query did not finish"], Object);
  } catch { /* refused */ }
  assert.equal(foreign, "refused", `${at} accepts a foreign new.target`);
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
        // EVERY WAY AN EXPORT CAN BE INVOKED IS SWEPT, and the branch is a
        // structural question about the value rather than its name: there is no
        // exempt export here. A class is constructed and asserted uncallable —
        // the engine refuses the call before any line of the module runs, and
        // that sentence is the engine's, not this surface's. An ordinary
        // function is constructible too, so it is BOTH constructed and called.
        if (isClassConstructor(value)) { sweepClassConstructor(at, value); continue; }
        if (isConstructor(value)) sweepConstruction(at, value);
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

test("STORE ERROR: the error carries registered codes only, and nothing the caller wrote", () => {
  const registered = "the query did not finish";
  const carried = new stores.SeamStoreUnreachable("github:checks", registered,
    new Error(`${HOSTILE_MARKER}-underlying`));
  assert.equal(carried.store_ref, "github:checks");
  assert.equal(carried.because, registered);
  assert.equal(carried.message, `github:checks: ${registered}`);
  assert.equal(carried.stack, `SeamStoreUnreachable: github:checks: ${registered}`,
    "the stack is a caller's frames again");
  assert.equal(carried.cause, undefined, "the underlying cause is retained again");
  assert.equal(carried.cause_kind, "an-error");

  // AN UNREGISTERED ARGUMENT IS REPLACED, NOT QUOTED. The old constructor threw
  // a TypeError reciting the word it had just refused, which is how a privileged
  // string left an exported callable.
  const refused = new stores.SeamStoreUnreachable("allow", "green", { ok: true });
  assert.equal(refused.store_ref, "a-store-this-file-does-not-serve");
  assert.equal(refused.because, "the reason this store was unreachable is not a registered one");
  assert.equal(refused.message,
    "a-store-this-file-does-not-serve: the reason this store was unreachable is not a registered one");
  assert.equal(refused.cause, undefined);
  assert.equal(refused.cause_kind, "not-an-error");
  assertSwept("refused", refused);
  assertSwept("refused.message", refused.message);
  assertSwept("refused.stack", refused.stack);

  // A revoked Proxy as the cause: the prototype walk throws, and the answer to
  // that is this file's own code rather than the engine's sentence.
  const revocable = Proxy.revocable({}, {});
  revocable.revoke();
  assert.equal(new stores.SeamStoreUnreachable("github:checks", registered, revocable.proxy).cause_kind,
    "undetermined");

  // And no field can be replaced after the fact.
  assert.throws(() => { refused.because = registered; }, TypeError);
  assert.throws(() => Object.defineProperty(refused, "because", { value: registered }), TypeError);
  assert.throws(() => Object.defineProperty(refused, "stack", { get: () => HOSTILE_MARKER }), TypeError);
});

test("STORE ERROR: a setter on the prototype chain cannot stand in for a field", () => {
  // The fields are installed with defineProperty rather than assigned, and the
  // difference is only visible when something is listening: a plain
  // `this.store_ref = store` would call a setter Error.prototype happened to
  // carry, the own property would never be installed, and every read of the
  // field would return whatever that setter's partner getter wanted to say.
  //
  // Planting one here is a MEASUREMENT, not a threat model — the amendment of
  // 2026-09-11 puts mutation of an intrinsic out of scope as an attack — and it
  // is what makes "installed as data properties" an observable requirement
  // rather than one that assigning-and-freezing satisfies by accident.
  const assigned = [];
  const fields = ["store_ref", "because", "cause_kind"];
  for (const key of fields)
    Object.defineProperty(Error.prototype, key, {
      configurable: true,
      set(value) { assigned.push(value); },
      get() { return `${HOSTILE_MARKER}-from-the-prototype`; },
    });
  try {
    const error = new stores.SeamStoreUnreachable("github:checks", "the query did not finish");
    assert.deepEqual(assigned, [], "a field was assigned through a prototype setter");
    for (const key of fields) assert.ok(Object.hasOwn(error, key), `${key} is not an own property`);
    assert.equal(error.store_ref, "github:checks");
    assert.equal(error.because, "the query did not finish");
    assert.equal(error.cause_kind, "none");
    assertSweptConstructed("prototype-setter", error);
  } finally {
    for (const key of fields) delete Error.prototype[key];
  }
  assert.equal(Object.hasOwn(Error.prototype, "store_ref"), false, "the plant was not removed");
});

test("SWEEP CONTROL: each assertion the constructor sweep makes has been seen to fail", () => {
  // A sweep nobody has seen fail is a sweep nobody has tested, and this one is
  // new. SEVEN PLANTS, each differing from the shipped class in exactly ONE way,
  // so no working assertion can cover for a broken one. Plant 1 is the code this
  // correction replaced, verbatim; the other six isolate one clause each.
  const REASONS = Object.freeze(["the query did not finish", "the checks source was not reachable",
    "the reason this store was unreachable is not a registered one"]);
  const TOKENS = Object.freeze(["github:checks", "a-store-this-file-does-not-serve"]);
  const fixed = (storeRef, because) => [
    TOKENS.includes(storeRef) ? storeRef : "a-store-this-file-does-not-serve",
    REASONS.includes(because) ? because : "the reason this store was unreachable is not a registered one",
  ];
  const install = (target, key, value, enumerable) =>
    Object.defineProperty(target, key, { value, writable: false, enumerable, configurable: false });
  const text = value => (typeof value === "string" ? value : "an argument of another type");

  // 1 — the constructor that shipped, exactly as the re-review found it.
  class Interpolating extends Error {
    constructor(storeRef, because, cause) {
      super(`${storeRef}: ${because}`, cause === undefined ? undefined : { cause });
      this.name = "SeamStoreUnreachable";
      this.store_ref = storeRef;
      this.because = because;
    }
  }

  // 2 — right shape, right filter, and it still echoes the caller's strings.
  class EchoesCaller extends Error {
    constructor(storeRef, because) {
      if (new.target !== EchoesCaller) throw new TypeError("final");
      const message = `${text(storeRef)}: ${text(because)}`;
      super(message);
      install(this, "name", "SeamStoreUnreachable", false);
      install(this, "message", message, false);
      install(this, "stack", `SeamStoreUnreachable: ${message}`, false);
      install(this, "store_ref", text(storeRef), true);
      install(this, "because", text(because), true);
      Object.freeze(this);
    }
  }

  // 3 — refuses an unregistered reason by quoting it back, which is what the
  // old TypeError did and what made a privileged word leave the module.
  class QuotesTheRefusal extends Error {
    constructor(storeRef, because) {
      if (new.target !== QuotesTheRefusal) throw new TypeError("final");
      if (!REASONS.includes(because))
        throw new TypeError(`${String(because)} is not a registered store-unreachable reason`);
      const [store, reason] = fixed(storeRef, because);
      super(`${store}: ${reason}`);
      install(this, "name", "SeamStoreUnreachable", false);
      install(this, "message", `${store}: ${reason}`, false);
      install(this, "stack", `SeamStoreUnreachable: ${store}: ${reason}`, false);
      install(this, "store_ref", store, true);
      install(this, "because", reason, true);
      Object.freeze(this);
    }
  }

  // 4 — filters both arguments, and hands the third one back untouched.
  class KeepsCause extends Error {
    constructor(storeRef, because, cause) {
      if (new.target !== KeepsCause) throw new TypeError("final");
      const [store, reason] = fixed(storeRef, because);
      super(`${store}: ${reason}`, cause === undefined ? undefined : { cause });
      install(this, "name", "SeamStoreUnreachable", false);
      install(this, "message", `${store}: ${reason}`, false);
      install(this, "stack", `SeamStoreUnreachable: ${store}: ${reason}`, false);
      install(this, "store_ref", store, true);
      install(this, "because", reason, true);
      Object.freeze(this);
    }
  }

  // 5 — a REAL engine stack: a list of the caller's file paths and frame names,
  // bytes this module did not write, swept like any other unfixed text.
  class EngineStack extends Error {
    constructor(storeRef, because) {
      if (new.target !== EngineStack) throw new TypeError("final");
      const [store, reason] = fixed(storeRef, because);
      super(`${store}: ${reason}`);
      const engineStack = this.stack;
      install(this, "name", "SeamStoreUnreachable", false);
      install(this, "message", `${store}: ${reason}`, false);
      install(this, "stack", engineStack, false);
      install(this, "store_ref", store, true);
      install(this, "because", reason, true);
      Object.freeze(this);
    }
  }

  // 6 — every value it yields is registered; the SHAPE is the defect, because an
  // accessor runs code that is not this module's on every read of the field.
  class AccessorField extends Error {
    constructor(storeRef, because) {
      if (new.target !== AccessorField) throw new TypeError("final");
      const [store, reason] = fixed(storeRef, because);
      super(`${store}: ${reason}`);
      install(this, "name", "SeamStoreUnreachable", false);
      install(this, "message", `${store}: ${reason}`, false);
      install(this, "stack", `SeamStoreUnreachable: ${store}: ${reason}`, false);
      install(this, "store_ref", store, true);
      Object.defineProperty(this, "because", { get: () => reason, enumerable: true });
      Object.freeze(this);
    }
  }

  // 7 — everything above it, minus the `new.target` check.
  class Subclassable extends Error {
    constructor(storeRef, because) {
      const [store, reason] = fixed(storeRef, because);
      super(`${store}: ${reason}`);
      install(this, "name", "SeamStoreUnreachable", false);
      install(this, "message", `${store}: ${reason}`, false);
      install(this, "stack", `SeamStoreUnreachable: ${store}: ${reason}`, false);
      install(this, "store_ref", store, true);
      install(this, "because", reason, true);
      Object.freeze(this);
    }
  }

  for (const [label, plant] of [["interpolating", Interpolating], ["echoes-caller", EchoesCaller],
    ["quotes-the-refusal", QuotesTheRefusal], ["keeps-cause", KeepsCause],
    ["engine-stack", EngineStack], ["accessor-field", AccessorField],
    ["subclassable", Subclassable]])
    assert.throws(() => sweepClassConstructor(`control.${label}`, plant), undefined,
      `the sweep passed the ${label} plant, so the assertion it is planted against proves nothing`);

  // The calibration: the shipped class goes through the same function untouched.
  assert.doesNotThrow(() => sweepClassConstructor("control.shipped", stores.SeamStoreUnreachable));

  // And the structural branch is what routes an export, so the routing is checked
  // too: the class takes the class path and nothing else on these three surfaces
  // does, while an ordinary exported function is still constructible and must
  // therefore be swept both ways.
  assert.equal(isClassConstructor(stores.SeamStoreUnreachable), true);
  for (const callable of [stores.fetchPredecessorOutcomeRows, stores.fetchSchedulerLedgerRows,
    stores.fetchCheckConclusionRows, readers.readGateConclusionEvidence,
    readers.readPredecessorOutcomeEvidence, readers.readSchedulerCanaryEvidence,
    rulings.seamRulingRef])
    assert.equal(isClassConstructor(callable), false);
  assert.equal(isConstructor(rulings.seamRulingRef), true,
    "an ordinary function is constructible, and the sweep must not assume otherwise");
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
