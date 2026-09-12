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
import { dirname, join } from "node:path";
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
const FAKE_PG_FILE = fileURLToPath(new URL("./gate-zero-seam-pg.v5.fake.cjs", import.meta.url));

/** The repository the checks store serves, and the only one it will serve. */
const AUTHORITATIVE_REPOSITORY = "jbookout/carr-system";

/**
 * THE STORE'S CLOSED REASON SET, restated here because it is a CONTRACT rather
 * than an implementation detail: a reader puts one of these straight into an
 * answer, and the guarded boundary may re-throw nothing else. The test below
 * reads the module's own registry back out of its source and asserts the two
 * agree, so adding a reason in src without adding it here is red.
 */
const STORE_UNREACHABLE_REASONS = Object.freeze([
  "the checks source answer did not parse",
  "the checks source credentials are not configured in this process",
  "the checks source refused the request",
  "the checks source was not reachable",
  "the database client is not available in this process",
  "the connection target for this store is not configured in this process",
  "the query did not finish",
  "the query did not address a row",
  "the configured checks repository is not the one this file serves",
  "the call did not finish",
  "the addressed head sha is not the shape this file serves",
  "the reason this store was unreachable is not a registered one",
]);

const SWEPT_NAMESPACES = () => [["readers", readers], ["rulings", rulings], ["stores", stores]];

const STORE_CREDENTIALS = ["DATABASE_URL_READER", "GITHUB_TOKEN", "GITHUB_REPOSITORY"];

function saveEnv(names) {
  const saved = {};
  for (const name of names) {
    saved[name] = process.env[name];
    delete process.env[name];
  }
  return saved;
}

function restoreEnv(saved) {
  for (const [name, value] of Object.entries(saved)) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
}

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
    "fetchCheckConclusionRows",
    "fetchPredecessorOutcomeRows",
    "fetchSchedulerLedgerRows",
    "isSeamStoreUnreachable",
    "seamStoreUnreachable",
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

// ---------------------------------------------------------------------------
// THE THROWN VALUE IS SWEPT AS A WHOLE, not as a `.message` and a `.because`.
//
// The third review round found the hole with a probe worth restating: it called
// an export from a caller function named `green`, the export threw a native
// TypeError, and the error's STACK — a list of the caller's own frame names —
// came back carrying `at green`. The sweep at the time read `.message` and
// `.because` and nothing else, so it saw nothing. It also could not have seen
// `throw "allow"`, `throw true`, or an object built to read as a verdict,
// because none of those has either field.
//
// So everything about the thrown value is inspected: what it IS, every own
// property including the non-enumerable ones Error keeps, and the five names a
// consumer would reach for. And the SHAPE is asserted as well as the words: a
// value that leaves by the throwing door has to be one of the module's own
// refusals, which means a fixed `stack` of exactly `${name}: ${message}`, no
// retained cause, non-writable non-configurable data properties, frozen, and a
// code out of the module's closed registry. A stack with frames in it fails on
// the equality before the word sweep ever has to catch the frame name.
// ---------------------------------------------------------------------------

function safeRead(holder, key) {
  try { return Reflect.get(Object(holder), key); } catch { return "<threw on read>"; }
}

function safeOwnKeys(value) {
  try { return Reflect.ownKeys(Object(value)); } catch { return []; }
}

function thrownFacets(thrown) {
  const facets = [["value", thrown]];
  if (thrown === null || (typeof thrown !== "object" && typeof thrown !== "function"))
    return facets;
  for (const key of safeOwnKeys(thrown))
    facets.push([`own.${String(key)}`,
      typeof key === "symbol" ? String(key) : safeRead(thrown, key)]);
  for (const key of ["name", "message", "stack", "cause", "code", "because", "store_ref"])
    facets.push([key, safeRead(thrown, key)]);
  return facets;
}

/** One thrown value, against every clause the surface owes for one. */
function assertNothingRaw(at, thrown) {
  for (const [facet, value] of thrownFacets(thrown)) assertSwept(`${at}.${facet}`, value);
  assert.ok(!safeLabel(thrownFacets(thrown).map(([, value]) => value)).includes(HOSTILE_MARKER),
    `${at} threw the caller's own text back`);

  assert.ok(thrown !== null && typeof thrown === "object",
    `${at} threw a bare ${typeof thrown}: ${safeLabel(thrown)}`);
  const name = safeRead(thrown, "name");
  const message = safeRead(thrown, "message");
  assert.equal(typeof name, "string", `${at} threw a value with no name`);
  assert.equal(typeof message, "string", `${at} threw a value with no message`);
  assert.equal(safeRead(thrown, "stack"), `${name}: ${message}`,
    `${at} carries an engine-built stack, which is the caller's own frames`);
  assert.equal(safeRead(thrown, "cause"), undefined, `${at} retained a cause`);
  assert.ok(Object.isFrozen(thrown), `${at} is not frozen`);
  for (const key of safeOwnKeys(thrown)) {
    const where = `${at}.${String(key)}`;
    const descriptor = Object.getOwnPropertyDescriptor(thrown, key);
    assert.ok(Object.hasOwn(descriptor, "value"), `${where} is an accessor, not a data property`);
    assert.equal(descriptor.writable, false, `${where} is writable`);
    assert.equal(descriptor.configurable, false, `${where} is configurable`);
  }
  assert.ok(STORE_UNREACHABLE_REASONS.includes(safeRead(thrown, "because")),
    `${at} carries a code the module never registered: ${safeLabel(safeRead(thrown, "because"))}`);
}

/**
 * Callers whose FRAME NAME is a privileged word. A named function expression is
 * the only way to put a chosen name in an engine stack, and that is exactly the
 * probe the re-review used.
 */
function privilegedCallers() {
  return [
    function green(call, argument) { return call(argument); },
    function allow(call, argument) { return call(argument); },
    function ok(call, argument) { return call(argument); },
    function verified(call, argument) { return call(argument); },
  ];
}

/** One invocation: whatever it returns is swept, whatever it throws is swept harder. */
async function assertNothingRawEscapes(at, invoke) {
  let outcome;
  try {
    outcome = await invoke();
  } catch (thrown) {
    assertNothingRaw(at, thrown);
    return;
  }
  assertSwept(`${at}.returned`, outcome);
  assert.ok(!safeLabel(outcome).includes(HOSTILE_MARKER), `${at} returned the caller's own text`);
}

/**
 * Every export, invoked every way it can be invoked, from every privileged
 * caller, with every hostile argument. ONE function, so the credential-less run
 * and the run over real rows ask exactly the same question.
 */
async function sweepEveryInvocation(label, namespace) {
  for (const [name, value] of Object.entries(namespace)) {
    const at = `${label}.${name}`;
    if (typeof value !== "function") { assertSwept(at, value); continue; }
    // CONSTRUCTION IS NO LONGER A CALL INTO THE MODULE, so it is no longer swept
    // as one. Amendment 2 of 2026-09-12: every export is a bound arrow, the
    // engine refuses `Reflect.construct` on one before any line of the module
    // runs, and that refusal is the engine's own — not a finding, and not a
    // value this surface produced. What IS asked, once per export and in full,
    // is that the shape holds: non-constructable, no prototype, no chain walked
    // on a caller's operand, and no module code reached by any newTarget a
    // caller supplies.
    assertClosedCallable(at, value);
    for (const caller of privilegedCallers())
      for (const argument of [undefined, ...hostileArguments()])
        await assertNothingRawEscapes(`${at}<-${caller.name}`, () => caller(value, argument));
  }
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

test("SURFACE: every exported callable of all three modules is a guarded one", () => {
  // THE STRUCTURAL HALF OF INVARIANT (2), and it is here because the behavioural
  // half cannot reach all of it. The ruling lookup has no throwing path today —
  // `Object.hasOwn` on a frozen literal and a pattern over a string — so removing
  // its boundary changes nothing any input can observe, and a guard that no test
  // can see removed is a guard that quietly goes away. The invariant is that
  // EVERY export passes through one, whether today's implementation needs it or
  // not, so the invariant is what is checked.
  const stores_ = readFileSync(join(SRC, STORES_FILE), "utf8");
  const readers_ = readFileSync(join(SRC, READERS_FILE), "utf8");
  const rulings_ = readFileSync(join(SRC, RULINGS_FILE), "utf8");

  // Each module declares exactly one boundary, and it catches everything.
  for (const [name, source] of [["stores", stores_], ["readers", readers_]])
    assert.equal((source.match(/^function guarded\(/gm) ?? []).length, 1,
      `${name} has no single guarded boundary, or has more than one`);

  // Every fetcher and every reader is that boundary applied to a private
  // implementation — never the implementation exported directly.
  const guardedExports = source => [...source.matchAll(/^export const (\w+) =\s*\n?\s*([\s\S]{0,40})/gm)]
    .map(([, name, tail]) => [name, tail.trimStart().startsWith("guarded(")]);
  const storeExports = guardedExports(stores_);
  assert.deepEqual(storeExports.filter(([name]) => name.startsWith("fetch")).map(([, ok]) => ok),
    [true, true, true], "a store fetcher is exported without its boundary");
  assert.deepEqual(guardedExports(readers_).filter(([name]) => name.startsWith("read"))
    .map(([, ok]) => ok), [true, true, true], "a reader is exported without its boundary");

  // And the ruling lookup, whose boundary no input can reach, is asserted whole.
  assert.ok(/try \{\s*return seamRulingRefOf\(cardRef\);\s*\} catch \{\s*return null;\s*\}\s*\}/
    .test(rulings_), "the ruling lookup is exported without its boundary");

  // AND THE CLOSED SHAPE IS STRUCTURAL TOO, because the behavioural half cannot
  // see a shape that is not there. Amendment 2 of 2026-09-12, asked of the
  // source: one `closedCallable` per module, EVERY exported const passing
  // through it or through `guarded` (which returns one) or a plain string
  // constant, and NO Proxy anywhere — the wrapper the fourth correction shipped
  // is what forwarded `get` to a raw target, and it is deleted rather than
  // tightened.
  const sources = [["stores", stores_], ["readers", readers_], ["rulings", rulings_]];
  for (const [name, source] of sources) {
    assert.equal((source.match(/^function closedCallable\(/gm) ?? []).length, 1,
      `${name} has no single closed-callable helper, or has more than one`);
    assert.equal(/new Proxy\(/.test(source), false, `${name} wraps a value in a Proxy again`);
    assert.equal(/^export (function|class) /m.test(source), false,
      `${name} exports a function declaration or a class, which are both constructors`);
  }
  const exportedConsts = source => [...source.matchAll(/^export const (\w+) =\s*\n?\s*([\s\S]{0,20})/gm)]
    .map(([, name, tail]) => [name, tail.trimStart()]);
  for (const [name, source] of sources)
    for (const [exported, tail] of exportedConsts(source))
      assert.ok(tail.startsWith("closedCallable(") || tail.startsWith("guarded(")
        || tail.startsWith('"'), `${name}.${exported} is exported without the closed shape`);
  // The ruling lookup by name, because it is the one export with no boundary
  // wrapper of its own in the line that exports it.
  assert.ok(/^export const seamRulingRef = closedCallable\(seamRulingRefLookup\);$/m.test(rulings_),
    "the ruling lookup is exported as something other than a closed callable");
  // And the store's type is not on the surface at all: a factory and a predicate
  // are, and the class they build is private.
  assert.ok(/^class SeamStoreUnreachableType extends Error \{$/m.test(stores_),
    "the store's error type is no longer the module-private class it must be");
  assert.equal(/^export (const|class) SeamStoreUnreachable\b/m.test(stores_), false,
    "the store's error type is exported again");
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
 * new.targets A CALLER CHOOSES, which is the half of construction the earlier
 * sweep never supplied. `Reflect.construct(x, args, newTarget)` makes the ENGINE
 * read `newTarget.prototype` — so a caller's object is read on the way in, and
 * whatever the constructed value inherits comes from the caller.
 *
 *   throws-on-prototype  the read itself throws the caller's own text. A plain
 *                        function's `prototype` is non-configurable, so the
 *                        accessor cannot be planted directly; a Proxy over a
 *                        function IS a constructor, and its `get` trap is
 *                        exactly where the engine's read lands.
 *   marker-prototype     the read succeeds and hands back an object carrying the
 *                        caller's marker, so anything built against it carries
 *                        the marker on its chain.
 *   foreign              `Object`, the probe the fourth round used.
 */
function hostileNewTargets() {
  const throwsOnPrototype = new Proxy(function () {}, {
    get(target, key, receiver) {
      if (key === "prototype") throw new Error(`${HOSTILE_MARKER}-new-target-prototype`);
      return Reflect.get(target, key, receiver);
    },
  });
  function markerTarget() {}
  markerTarget.prototype = Object.freeze({ marker: `${HOSTILE_MARKER}-new-target-prototype` });
  return [["throws-on-prototype", throwsOnPrototype], ["marker-prototype", markerTarget],
    ["foreign", Object]];
}

/** Left operands built to make `instanceof` run the caller's own code. */
function hostileInstanceOperands() {
  const selfReferencing = new Proxy({}, { getPrototypeOf() { return selfProxy; } });
  const selfProxy = selfReferencing;
  const revocable = Proxy.revocable({}, {});
  revocable.revoke();
  return [
    ["throws-a-string", new Proxy({}, { getPrototypeOf() { throw "allow"; } })],
    ["throws-the-caller-text", new Proxy({}, {
      getPrototypeOf() { throw new Error(`${HOSTILE_MARKER}-get-prototype-of`); } })],
    ["throws-a-true", new Proxy({}, { getPrototypeOf() { throw true; } })],
    ["revoked", revocable.proxy],
    // A chain that never ends: an unbounded walk over it does not return at all,
    // and a hang is a refusal the caller chose rather than one a module wrote.
    ["never-ends", selfReferencing],
    ["a-plain-object", {}],
    ["a-real-error", new Error("plain")],
    ["a-null-prototype", Object.create(null)],
  ];
}

/**
 * The marker is the whole question amendment 2 of 2026-09-12 leaves open for
 * construction. The ENGINE'S refusal of a non-constructor — its TypeError, its
 * message and its stack of the caller's own frames — is the engine's and is NOT
 * a finding, because no line of the module ran to produce it. What must still be
 * true is that no line of the module ran AT ALL: nothing read the caller's
 * newTarget, nothing built against its prototype, and nothing carries its text.
 */
function markerInChain(value) {
  try {
    let walked = value;
    for (let step = 0; step < 8 && walked !== null && walked !== undefined; step += 1) {
      for (const key of safeOwnKeys(walked))
        if (safeLabel(safeRead(walked, key)).includes(HOSTILE_MARKER)) return true;
      walked = Reflect.getPrototypeOf(Object(walked));
    }
    return false;
  } catch {
    return true;
  }
}

function assertNoModuleCodeRan(at, value) {
  assert.ok(!String(safeRead(value, "message") ?? "").includes(HOSTILE_MARKER),
    `${at}: module code ran and carried the caller's own text back`);
  assert.equal(markerInChain(value), false,
    `${at}: the caller's newTarget was read, and what came back is built on it`);
}

/**
 * THE CLOSED SHAPE, asked of one exported callable, clause by clause. Amendment
 * 2 of 2026-09-12 after the fifth review round on PR 1001:
 *
 *   (a) an arrow or a bound function — non-constructable, no `.prototype` — so
 *       no `newTarget.prototype` is ever read and no raw target is reachable
 *       through `prototype.constructor`;
 *   (b) `Symbol.hasInstance` as a non-writable, non-configurable DATA property
 *       whose function returns false WITHOUT READING its argument, so the
 *       intrinsic OrdinaryHasInstance never walks a caller's chain.
 *
 * The guard function itself is asked the same question the fifth round asked of
 * the one on the error type: a retrievable guard that is constructable is
 * another callable on the surface, reachable by a route nobody enumerated.
 */
function assertClosedCallable(at, exported) {
  assert.equal(typeof exported, "function", `${at} is not callable`);

  assert.equal(Object.hasOwn(exported, "prototype"), false,
    `${at} carries an own prototype, so a foreign new.target has something to be read against`);
  assert.equal(exported.prototype, undefined, `${at} reaches a prototype through its chain`);
  assert.equal(isConstructor(exported), false, `${at} is still a constructor`);
  for (const [label, newTarget] of hostileNewTargets()) {
    let built;
    try {
      built = Reflect.construct(exported, [HOSTILE_MARKER, "green", { ok: true }], newTarget);
    } catch (thrown) {
      assertNoModuleCodeRan(`${at}.${label}.threw`, thrown);
      continue;
    }
    assertNoModuleCodeRan(`${at}.${label}.returned`, built);
    assert.fail(`${at}.${label} constructed a value out of something that is not a constructor`);
  }

  const guard = Object.getOwnPropertyDescriptor(exported, Symbol.hasInstance);
  assert.ok(guard !== undefined,
    `${at} answers instanceof with the intrinsic, which walks the operand's chain`);
  assert.ok(Object.hasOwn(guard, "value"), `${at}'s hasInstance is an accessor, not a data property`);
  assert.equal(guard.writable, false, `${at}'s hasInstance is writable`);
  assert.equal(guard.configurable, false, `${at}'s hasInstance is configurable`);
  assert.equal(isConstructor(guard.value), false, `${at}'s hasInstance guard is itself constructable`);
  assert.equal(Object.hasOwn(guard.value, "prototype"), false,
    `${at}'s hasInstance guard carries a prototype of its own`);

  for (const [label, hostile] of hostileInstanceOperands()) {
    let outcome = "threw";
    try { outcome = hostile instanceof exported; } catch (thrown) { outcome = thrown; }
    assert.equal(outcome, false, `${at}.${label}: instanceof threw, or answered true`);
  }
  // AND IT DOES NOT LOOK AT THE OPERAND AT ALL, which is stronger than answering
  // false about it: a guard that walks a chain to decide can be steered by what
  // it walks.
  let looked = false;
  const watched = new Proxy({}, { getPrototypeOf() { looked = true; return null; } });
  assert.equal(watched instanceof exported, false, `${at} answered a membership question true`);
  assert.equal(looked, false, `${at}'s hasInstance read the left operand`);
}

/**
 * The error factory, swept the way the class used to be: every hostile value in
 * every argument position, and every produced error checked for a route back to
 * a constructor the caller could reach.
 */
function sweepErrorFactory(at, factory) {
  assertClosedCallable(at, factory);
  for (const argumentList of constructorArgumentLists()) {
    let built;
    try {
      built = factory(...argumentList);
    } catch (error) {
      assertSwept(`${at}.throw.message`, error?.message ?? null);
      assertSwept(`${at}.throw.because`, error?.because ?? null);
      assert.ok(!String(error?.message ?? "").includes(HOSTILE_MARKER),
        `${at} quoted the caller back in its own refusal`);
      continue;
    }
    assertSweptConstructed(`${at}.made[${argumentList.length}]`, built);
    assertNoRawRouteFrom(`${at}.made`, built, factory);
  }
}

/**
 * A PRODUCED ERROR IS NOT A ROUTE BACK TO ITS CLASS. `prototype.constructor`
 * carries the class every engine installs there, and the fourth round reached it
 * from an instance — so the chain is walked and every `constructor` on it has to
 * be the factory or an intrinsic. An intrinsic is not a route to THIS module's
 * type; anything else is.
 */
function assertNoRawRouteFrom(at, built, factory) {
  let walked = built;
  for (let step = 0; step < 8 && walked !== null && walked !== undefined; step += 1) {
    const descriptor = Object.getOwnPropertyDescriptor(Object(walked), "constructor");
    if (descriptor !== undefined) {
      assert.ok(Object.hasOwn(descriptor, "value"), `${at} reaches an accessor at .constructor`);
      const reached = descriptor.value;
      assert.ok(reached === factory || reached === Error || reached === Object,
        `${at} reaches a constructor this module owns through .constructor`);
      if (reached === factory) {
        assert.equal(descriptor.writable, false, `${at}.constructor is writable`);
        assert.equal(descriptor.configurable, false, `${at}.constructor is configurable`);
      }
    }
    walked = Reflect.getPrototypeOf(Object(walked));
  }
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
  // THE SAME IDENTITY THE VALUE SWEEP USES, and it is here because construction
  // now reaches the readers: `new readGateConclusionEvidence()` answers with the
  // GATE'S OWN object, which is main's and carries main's strings. Identity
  // admits no new string at all, which is stronger than sweeping its words.
  if (GATE_ANSWER_DIGESTS.has(digest(built))) return;
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
        // EVERY WAY AN EXPORT CAN BE INVOKED IS SWEPT, and after the fifth round
        // there is exactly one way: a CALL. No export is a constructor — the
        // class is module-private, and every reader, fetcher, factory and lookup
        // is a bound arrow — so the branch that used to route a class one way
        // and a function another is replaced by the assertion that neither route
        // exists. The error factory is swept through its own door below, with
        // every hostile value in every argument position.
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
    sweepErrorFactory("stores.seamStoreUnreachable", stores.seamStoreUnreachable);
  } finally {
    for (const [name, value] of Object.entries(saved))
      if (value !== undefined) process.env[name] = value;
  }
});

test("STORE ERROR: the error carries registered codes only, and nothing the caller wrote", () => {
  const registered = "the query did not finish";
  const carried = stores.seamStoreUnreachable("github:checks", registered,
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
  const refused = stores.seamStoreUnreachable("allow", "green", { ok: true });
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
  assert.equal(stores.seamStoreUnreachable("github:checks", registered, revocable.proxy).cause_kind,
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
    const error = stores.seamStoreUnreachable("github:checks", "the query did not finish");
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

  // EACH PLANT IS REACHED THE WAY THE MODULE IS NOW REACHED: through a bound
  // arrow factory, because a class is no longer something a caller can hold.
  // `closed` is this file's own restatement of the module's `closedCallable` —
  // bind, then deny `instanceof` — and it is here so that a plant differs from
  // the shipped shape in exactly ONE way rather than also failing the clauses
  // every unclosed value would fail.
  const closed = callable => {
    const bound = callable.bind(null);
    Object.defineProperty(bound, Symbol.hasInstance,
      { value: () => false, writable: false, enumerable: false, configurable: false });
    return bound;
  };
  const factoryFor = plant => {
    const exported = closed((...args) => new plant(...args));
    Object.defineProperty(plant.prototype, "constructor",
      { value: exported, writable: false, enumerable: false, configurable: false });
    return exported;
  };

  for (const [label, factory] of [
    ["interpolating", factoryFor(Interpolating)],
    ["echoes-caller", factoryFor(EchoesCaller)],
    ["quotes-the-refusal", factoryFor(QuotesTheRefusal)],
    ["keeps-cause", factoryFor(KeepsCause)],
    ["engine-stack", factoryFor(EngineStack)],
    ["accessor-field", factoryFor(AccessorField)],
    // 8 — every value it yields is registered and every shape clause holds. The
    //     ROUTE is the defect: `prototype.constructor` is left as the class the
    //     engine installed, so any refusal it produces hands a caller back a
    //     constructor to aim a new.target at. That is the fourth round's finding
    //     (2), and nothing about the VALUES it returns is wrong.
    ["leaks-the-class", closed((...args) => new Subclassable(...args))],
    // 9 — the class itself, exported the way the first draft exported it:
    //     constructable, carrying a prototype, callable by anybody.
    ["the-class-itself", Subclassable],
  ])
    assert.throws(() => sweepErrorFactory(`control.${label}`, factory), undefined,
      `the sweep passed the ${label} plant, so the assertion it is planted against proves nothing`);

  // The calibration: the shipped factory goes through the same function untouched.
  assert.doesNotThrow(() => sweepErrorFactory("control.shipped", stores.seamStoreUnreachable));

  // And the structural fact the whole sweep now rests on: NOTHING on these three
  // surfaces is a constructor at all, so there is no second route to sweep.
  for (const callable of [stores.seamStoreUnreachable, stores.isSeamStoreUnreachable,
    stores.fetchPredecessorOutcomeRows, stores.fetchSchedulerLedgerRows,
    stores.fetchCheckConclusionRows, readers.readGateConclusionEvidence,
    readers.readPredecessorOutcomeEvidence, readers.readSchedulerCanaryEvidence,
    rulings.seamRulingRef]) {
    assert.equal(isClassConstructor(callable), false);
    assert.equal(isConstructor(callable), false, "an exported callable is constructible again");
  }
});

test("SWEEP: the module's registered reason set is the one this file restates", () => {
  // The reason set is module-private on purpose — an importer that could
  // enumerate it could assemble a message out of it and hand it back in. So the
  // contract is restated here and checked against the source, which is the only
  // honest way to have both.
  const source = readFileSync(join(SRC, STORES_FILE), "utf8");
  const registry = /const UNREACHABLE_REASONS = Object\.freeze\(\{([\s\S]*?)\}\);/.exec(source);
  assert.ok(registry, "the store's reason registry is no longer where this test reads it");
  const declared = [...registry[1].matchAll(/"([^"]+)"/g)].map(one => one[1]);
  assert.deepEqual([...declared].sort(), [...STORE_UNREACHABLE_REASONS].sort(),
    "a store-unreachable reason was added or removed without the contract following it");
});

test("SWEEP: a native error or a raw thrown value never leaves an export", async () => {
  // FINDING 1 OF THE THIRD RE-REVIEW. Nothing here is new about the arguments —
  // it is the same hostile set — and everything is new about what is looked at:
  // the call goes through a caller whose frame name is a privileged word, and
  // whatever comes back out of the throwing door is inspected whole.
  const saved = saveEnv(STORE_CREDENTIALS);
  try {
    for (const [label, namespace] of SWEPT_NAMESPACES())
      await sweepEveryInvocation(label, namespace);
  } finally {
    restoreEnv(saved);
  }
});

test("SURFACE: no export can be constructed, and none reads a caller's newTarget", async () => {
  // FINDING 1 OF THE FIFTH RE-REVIEW, and the shape amendment 2 of 2026-09-12
  // rules for it. The fourth correction answered construction from INSIDE the
  // module — a proxy construct trap, a `new.target` branch in the ruling lookup —
  // and that is already too late: the engine reads `newTarget.prototype` BEFORE
  // any body runs, so a caller's object was read on the way in, and a proxy
  // forwards `get`, so `exported.prototype.constructor` handed the raw target
  // back to be constructed with a new.target of the caller's choosing.
  //
  // The closed shape is the answer: a bound arrow has no [[Construct]] and no
  // `prototype`, so the ENGINE refuses in the caller's own frame having run no
  // line of the module. That refusal is the engine's and is not a finding. What
  // is asserted is what the amendment leaves the author owing — the shape holds,
  // and no module code ran against the caller's newTarget.
  const saved = saveEnv(STORE_CREDENTIALS);
  try {
    let callables = 0;
    for (const [label, namespace] of SWEPT_NAMESPACES())
      for (const [name, value] of Object.entries(namespace)) {
        if (typeof value !== "function") continue;
        callables += 1;
        assertClosedCallable(`${label}.${name}`, value);
      }
    // The six readers and fetchers, the factory, the predicate and the lookup:
    // an export that stopped being one of them fails here rather than quietly
    // skipping the loop above.
    assert.equal(callables, 9, "the callable surface moved without this count following it");

    // AND THE CALLING DOOR STILL ANSWERS, which is the thing the construction
    // door must not have cost: every reader returns the gate's own object and
    // every fetcher still refuses with its own registered reason.
    for (const [name, reader, gateAnswer] of READERS_UNDER_TEST)
      assert.equal(digest(await reader({ headSha: HOSTILE_MARKER })), digest(gateAnswer()), name);
  } finally {
    restoreEnv(saved);
  }
});

test("SURFACE CONTROL: each clause of the closed-callable check has been seen to fail", () => {
  // A check nobody has seen fail is a check nobody has tested, and every clause
  // here is new. ONE PLANT PER CLAUSE, each differing from the shipped shape in
  // exactly one way.
  const deny = (callable, value) => {
    Object.defineProperty(callable, Symbol.hasInstance, value);
    return callable;
  };
  const flat = { value: () => false, writable: false, enumerable: false, configurable: false };
  const closed = callable => deny(callable.bind(null), flat);

  const plants = [
    // 1 — an ordinary function: constructable, and carrying the `prototype` a
    //     foreign new.target is read against.
    ["a-plain-function", deny(function plain() {}, flat)],
    // 2 — a class: the shape the first draft exported.
    ["a-class", deny(class Plain {}, flat)],
    // 3 — the proxy wrapper the fourth correction shipped: a proxy over a plain
    //     function IS a constructor, and it forwards `get`.
    ["a-proxy-over-a-function", deny(new Proxy(function () {}, {}), flat)],
    // 4 — bound and non-constructable, and it answers instanceof with the
    //     intrinsic, which walks the caller's own prototype chain.
    ["no-hasInstance", (() => {}).bind(null)],
    // 5 — a guard that is an accessor: it runs code on every read of the slot.
    ["hasInstance-accessor", deny((() => {}).bind(null), { get: () => () => false, configurable: true })],
    // 6 — a guard that can be replaced.
    ["hasInstance-writable", deny((() => {}).bind(null), { ...flat, writable: true })],
    // 7 — a guard that can be redefined as an accessor afterwards.
    ["hasInstance-configurable", deny((() => {}).bind(null), { ...flat, configurable: true })],
    // 8 — a guard that is itself a constructable callable, which is the fifth
    //     round's second standards finding: the retrieved function was another
    //     door nobody had enumerated.
    ["hasInstance-constructable", deny((() => {}).bind(null), { ...flat, value: function () { return false; } })],
    // 9 — a guard that answers the membership question yes.
    ["hasInstance-answers-true", deny((() => {}).bind(null), { ...flat, value: () => true })],
    // 10 — a guard that LOOKS at the operand: it answers correctly for innocent
    //      values and runs the caller's getPrototypeOf trap for hostile ones.
    ["hasInstance-reads-the-operand",
      deny((() => {}).bind(null), { ...flat, value: x => x instanceof Error })],
  ];
  for (const [label, plant] of plants)
    assert.throws(() => assertClosedCallable(`control.${label}`, plant), undefined,
      `the check passed the ${label} plant, so the clause it is planted against proves nothing`);

  // AND THE MARKER CLAUSE, asked directly, because it is the one clause no
  // non-constructor can reach: a value that DID run module code against the
  // caller's newTarget carries the caller's text, either in what it says or on
  // the chain it was built against.
  assert.throws(() => assertNoModuleCodeRan("control.said-it",
    new Error(`${HOSTILE_MARKER}-from-a-getter`)));
  assert.throws(() => assertNoModuleCodeRan("control.built-on-it",
    Object.create(Object.freeze({ marker: `${HOSTILE_MARKER}-new-target-prototype` }))));
  assert.doesNotThrow(() => assertNoModuleCodeRan("control.engine-refusal",
    new TypeError("function () { [native code] } is not a constructor")));

  // The calibration: a shipped export goes through the same check untouched.
  for (const [label, exported] of [["stores.seamStoreUnreachable", stores.seamStoreUnreachable],
    ["stores.isSeamStoreUnreachable", stores.isSeamStoreUnreachable],
    ["stores.fetchCheckConclusionRows", stores.fetchCheckConclusionRows],
    ["readers.readGateConclusionEvidence", readers.readGateConclusionEvidence],
    ["rulings.seamRulingRef", rulings.seamRulingRef]])
    assert.doesNotThrow(() => assertClosedCallable(label, exported));

  // And the probes themselves are real: a target that DOES read its newTarget
  // gets the caller's object, which is what the plants above stand in for.
  function readsTheNewTarget() { return Object.create(new.target.prototype); }
  const [, markerTarget] = hostileNewTargets()[1];
  assert.equal(markerInChain(Reflect.construct(readsTheNewTarget, [], markerTarget)), true,
    "the marker newTarget no longer reaches what is built against it, so the probe proves nothing");
  const [, throwsOnPrototype] = hostileNewTargets()[0];
  assert.throws(() => Reflect.construct(readsTheNewTarget, [], throwsOnPrototype),
    error => String(error.message).includes(HOSTILE_MARKER),
    "the throwing-prototype newTarget no longer throws the caller's text");
});

test("STORE ERROR: the class is unreachable, and membership is a predicate rather than instanceof",
  () => {
    // FINDING 2 OF THE FIFTH RE-REVIEW, and the two clauses of amendment 2 that
    // answer it: the class is module-private and what is exported is a factory,
    // so there is nothing to construct, nothing to subclass and no raw class to
    // reach — and `instanceof` against an exported callable answers false without
    // walking anything, so the honest membership question needs its own name.
    const registered = "the query did not finish";
    const built = stores.seamStoreUnreachable("github:checks", registered);
    assert.equal(built.because, registered);
    assert.equal(built.store_ref, "github:checks");

    // (a) NO ROUTE BACK TO A CONSTRUCTOR, from the factory or from an instance.
    assertClosedCallable("stores.seamStoreUnreachable", stores.seamStoreUnreachable);
    assertNoRawRouteFrom("stores.seamStoreUnreachable.made", built, stores.seamStoreUnreachable);
    assert.equal(built.constructor, stores.seamStoreUnreachable,
      "an instance reaches something other than the factory at .constructor");
    const prototype = Object.getPrototypeOf(built);
    assert.equal(Object.isFrozen(prototype), true, "the type's prototype can be written again");
    const descriptor = Object.getOwnPropertyDescriptor(prototype, "constructor");
    assert.ok(Object.hasOwn(descriptor, "value"), "constructor is an accessor on the prototype");
    assert.equal(descriptor.writable, false, "prototype.constructor is writable");
    assert.equal(descriptor.configurable, false, "prototype.constructor is configurable");
    assert.equal(isConstructor(descriptor.value), false,
      "prototype.constructor is a constructor again, which is the raw route");

    // (b) THE PREDICATE IS THE MEMBERSHIP QUESTION, and `instanceof` is not.
    assert.equal(stores.isSeamStoreUnreachable(built), true,
      "a real refusal stopped answering the predicate");
    assert.equal(built instanceof stores.seamStoreUnreachable, false,
      "the factory answered a membership question about a value");
    for (const innocent of [{}, null, undefined, 0, "a string", new Error("plain"),
      Object.create(null), stores.seamStoreUnreachable])
      assert.equal(stores.isSeamStoreUnreachable(innocent), false,
        `${safeLabel(innocent)} answered the predicate`);
    // A LOOKALIKE IS NOT ONE, which is what makes the predicate a type question
    // rather than a name check a caller could satisfy by writing the name.
    assert.equal(stores.isSeamStoreUnreachable(Object.freeze({
      name: "SeamStoreUnreachable", because: "green", store_ref: "github:checks",
      stack: "SeamStoreUnreachable: github:checks: green",
    })), false, "the predicate reads a name a caller wrote");

    // And nothing a caller can shape makes it throw, or makes it hang.
    for (const [label, hostile] of hostileInstanceOperands()) {
      let outcome = "threw";
      try { outcome = stores.isSeamStoreUnreachable(hostile); } catch (error) { outcome = error; }
      assert.equal(outcome, false, `${label}: the predicate threw, or answered true`);
    }
  });

test("STORES: a head sha that is not one never reaches the URL, and the path stays here", async () => {
  // FINDING 3 OF THE FOURTH RE-REVIEW. `addressed` asked only for a nonempty
  // string, and the value went into a URL PATH — so a caller that named no
  // repository at all still chose one, by traversing out of this one.
  const sha = "a".repeat(40);
  const traversal = `../../../foreign-owner/foreign-repo/commits/${sha}`;

  // THE PROBE IS REAL, and this line is what keeps it real: the template the
  // store used to build normalizes to a foreign repository's check runs. If a
  // future URL library stopped normalizing, this test would prove nothing, and
  // it would say so here rather than passing quietly.
  assert.equal(
    new URL(`https://api.github.com/repos/${AUTHORITATIVE_REPOSITORY}/commits/${traversal}/check-runs`)
      .pathname,
    `/repos/foreign-owner/foreign-repo/commits/${sha}/check-runs`,
    "the traversal probe no longer escapes, so this test proves nothing");

  const refused = "the addressed head sha is not the shape this file serves";
  const savedEnv = saveEnv(STORE_CREDENTIALS);
  const realFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = fakeChecksSource(HOSTILE_CHECK_RUNS, calls);
  process.env.GITHUB_TOKEN = "a-token-this-test-wrote";
  try {
    for (const [label, headSha] of [
      ["traversal", traversal],
      ["traversal-with-encoded-slashes", `..%2F..%2Fforeign-owner/foreign-repo/commits/${sha}`],
      ["an-absolute-url", `https://api.github.com/repos/foreign-owner/foreign-repo/commits/${sha}`],
      ["an-appended-query", `${sha}/check-runs?check_name=x&per_page=1&`],
      ["a-fragment", `${sha}#/../../foreign-owner/foreign-repo`],
      ["upper-case-hex", "A".repeat(40)],
      ["one-character-too-many", `${sha}a`],
      ["one-character-short", sha.slice(1)],
      ["not-hexadecimal", "g".repeat(40)],
      ["forty-spaces", " ".repeat(40)],
      ["a-privileged-word", "green"],
    ])
      await assert.rejects(
        () => stores.fetchCheckConclusionRows({ headSha, checkName: "db-acceptance" }),
        error => stores.isSeamStoreUnreachable(error)
          && error.store_ref === "github:checks" && error.because === refused, label);
    assert.deepEqual(calls, [], "a sha this file will not serve reached the source anyway");

    // AND THE ONE SHAPE IT DOES SERVE LANDS INSIDE THE MODULE-PRIVATE REPOSITORY,
    // asserted on the NORMALIZED path rather than on a substring of the string
    // that was built — a `includes("/repos/jbookout/carr-system/")` passes just
    // as happily on a URL that then traverses back out of it.
    await stores.fetchCheckConclusionRows({ headSha: sha, checkName: "db-acceptance" });
    assert.equal(calls.length, 1, "the checks store never called its source");
    const url = new URL(calls[0].url);
    assert.equal(url.origin, "https://api.github.com");
    assert.equal(url.pathname, `/repos/${AUTHORITATIVE_REPOSITORY}/commits/${sha}/check-runs`);
    assert.equal(url.searchParams.get("check_name"), "db-acceptance");

    // A check name is not a path segment, and it may not become one.
    calls.length = 0;
    await stores.fetchCheckConclusionRows({ headSha: sha, checkName: "../../foreign/name" });
    assert.equal(new URL(calls[0].url).pathname,
      `/repos/${AUTHORITATIVE_REPOSITORY}/commits/${sha}/check-runs`,
      "a check name traversed out of the path");
  } finally {
    globalThis.fetch = realFetch;
    restoreEnv(savedEnv);
  }

  // AND THE ENCODING IS ASSERTED STRUCTURALLY, because no input can make it
  // observable: the sha has already matched forty hex characters and the
  // repository is the module's own constant, so both encodings are no-ops on
  // every value that can reach them. That is the point of them — the next
  // segment added to this path inherits an encoded template rather than a
  // trusting one — and a requirement no test can see removed is one that goes
  // away quietly. What a test can hold is that they are there.
  const source = readFileSync(join(SRC, STORES_FILE), "utf8");
  assert.ok(source.includes("/commits/${encodeURIComponent(headSha)}/check-runs"),
    "the head sha goes into the URL path unencoded");
  assert.ok(source.includes('AUTHORITATIVE_REPOSITORY.split("/").map(segment => encodeURIComponent(segment)).join("/")'),
    "the repository goes into the URL path unencoded");
  assert.equal(/repos\/\$\{repository\}/.test(source), false,
    "the repository is interpolated into the path again");
  assert.ok(/const headSha = addressedShape\(storeRef, query, "headSha", HEAD_SHA,/.test(source),
    "the head sha is no longer validated before the URL is built");
});

test("STORES: card 11's statements are read from ONE snapshot, not one each", async () => {
  // FINDING 4 OF THE FOURTH RE-REVIEW. `begin read only` keeps PostgreSQL's
  // DEFAULT isolation — read committed — and read committed takes a fresh
  // snapshot for every statement. The transaction was one transaction and three
  // moments, so an acceptance another session committed between the receipt
  // statement and the card statement was invisible to the first and visible to
  // the second, and the join of the two described a state that never existed.
  //
  // The fake `pg` models the database rather than the answer: it reads the
  // isolation level off the BEGIN the store actually sends, copies the world at
  // the first statement when that level is a snapshot one, and lets a concurrent
  // writer commit right after the first statement returns. What is asserted here
  // is which of the two documented behaviours the store's own statement gets.
  const target = stageWithFakePg({});
  const staged = await import(pathToFileURL(join(target, STORES_FILE)).href);
  const fakePg = await import(
    pathToFileURL(join(dirname(target), "node_modules", "pg", "index.js")).href);
  const savedEnv = saveEnv(STORE_CREDENTIALS);
  process.env.DATABASE_URL_READER = "postgres://fake/rows";
  try {
    const begins = fakePg.default.FAKE_BEGINS;
    begins.length = 0;
    const answer = await staged.fetchPredecessorOutcomeRows(
      { workRequestRef: fakePg.default.FAKE_CONCURRENT });

    assert.equal(begins.length, 1, "the fake saw no begin at all, so nothing here was measured");
    assert.match(begins[0], /isolation level\s+(repeatable read|serializable)/i,
      `the transaction runs at the default isolation: ${begins[0]}`);
    assert.match(begins[0], /read only/i, `the transaction is not read only: ${begins[0]}`);

    assert.equal(answer.rows.length, 1, "the concurrent scenario returned no row to judge");
    assert.equal(answer.rows[0].detail_row_count, 0,
      "a row committed between the two statements was observed, so they are not one snapshot");
    assertSwept("snapshot.rows", answer);
  } finally {
    restoreEnv(savedEnv);
  }
});

test("GUARD CONTROL: each clause of the thrown-value sweep has been seen to fail", async () => {
  // A sweep nobody has seen fail is a sweep nobody has tested. SEVEN THROWS,
  // each of which the shipped boundary converts and each of which must trip
  // exactly the clause it is planted against.
  const registered = "the query did not finish";
  const conforming = () => stores.seamStoreUnreachable("github:checks", registered);

  const unconforming = value => {
    const built = new Error("a plant");
    for (const [key, entry] of Object.entries(value))
      Object.defineProperty(built, key, { value: entry, writable: false, configurable: false });
    return Object.freeze(built);
  };

  const plants = [
    // 1 — a bare string, and it is a privileged word.
    ["raw-string", () => { throw "allow"; }],
    // 2 — a bare boolean, the shape the union does not have to list.
    ["raw-true", () => { throw true; }],
    // 3 — a non-privileged bare string: swept clean, and still not a refusal.
    ["raw-innocent-string", () => { throw "something went wrong"; }],
    // 4 — an object built to read as a verdict from a consumer's side.
    ["hostile-object", () => {
      throw Object.freeze({ name: "green", message: "the gate is green",
        stack: "green: the gate is green", because: "green" });
    }],
    // 5 — a native TypeError, raised from a caller named with a privileged word,
    //     so its stack carries `at green` and its message carries `read`.
    ["native-error", () => { const absent = null; return absent.green; }],
    // 6 — conforming in every way except that its code is not registered.
    ["unregistered-code", () => {
      throw unconforming({ name: "SeamStoreUnreachable", message: "github:checks: invented",
        stack: "SeamStoreUnreachable: github:checks: invented", because: "invented" });
    }],
    // 7 — registered code, fixed stack, and it kept the caller's cause.
    ["kept-cause", () => {
      throw unconforming({
        name: "SeamStoreUnreachable",
        message: `github:checks: ${registered}`,
        stack: `SeamStoreUnreachable: github:checks: ${registered}`,
        because: registered,
        cause: { retained: 1 },
      });
    }],
  ];

  for (const [name, plant] of plants) {
    let failed = false;
    try {
      await assertNothingRawEscapes(`control.${name}`, plant);
    } catch {
      failed = true;
    }
    assert.ok(failed, `the sweep passed the ${name} plant, so its assertion proves nothing`);
  }

  // The calibration: a real refusal, raised from a privileged caller, passes.
  await assertNothingRawEscapes("control.shipped",
    () => privilegedCallers()[0](() => { throw conforming(); }, undefined));
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

/**
 * A staged tree with a `pg` OF ITS OWN, written into `<base>/node_modules/pg`.
 *
 * The store module opens its own connection — no handle parameter, no injectable
 * opener, no env var that points it elsewhere — which is the property the whole
 * slice rests on and the reason its successful row-shaping path had never been
 * run by a test. Node resolves a bare `import("pg")` by walking up from the
 * importing file, so a package placed one directory above the staged src is
 * found before mcp-server/node_modules. The REAL store module runs, over rows a
 * hostile database would hand it. Nothing in src is edited and nothing is
 * monkeypatched.
 */
function stageWithFakePg(options = {}) {
  const target = stageTree(options);
  const module = join(dirname(target), "node_modules", "pg");
  mkdirSync(module, { recursive: true });
  writeFileSync(join(module, "package.json"),
    JSON.stringify({ name: "pg", version: "0.0.0", main: "index.js" }));
  cpSync(FAKE_PG_FILE, join(module, "index.js"));
  return target;
}

/**
 * A `fetch` that answers the checks call with wire text nobody would want in an
 * answer. Replacing a global in a TEST is a measurement, not a threat model —
 * the amendment of 2026-09-11 puts interpreter-level substitution out of scope
 * as an attack — and it is the only seam the checks store has, for the same
 * reason the database one had none.
 */
function fakeChecksSource(runs, calls = []) {
  return async (url, init) => {
    calls.push({ url: String(url), init });
    return {
      ok: true,
      status: 200,
      async json() { return { check_runs: runs, total_count: runs.length, ok: true }; },
    };
  };
}

const HOSTILE_CHECK_RUNS = Object.freeze([
  Object.freeze({
    name: "allow-the-commit",
    head_sha: "a".repeat(40),
    status: "completed",
    conclusion: "success",
    started_at: "2026-09-11T17:00:00.000Z",
    completed_at: "2026-09-11T17:00:30.000Z",
    html_url: `a-link-this-store-drops-green-passing-${HOSTILE_MARKER}`,
    check_suite: { conclusion: "green", ok: true },
  }),
]);

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

  // A LEDGER WHOSE EVERY IDENTIFIER IS A PRIVILEGED WORD, and all three clauses
  // hold anyway. Before the store reduced identifiers to digests, this row's
  // service key, run key and evidence ref went into the answer verbatim.
  const greenNames = await read("canary-green-names", "release-canary");
  assert.equal(greenNames.decision, "report");
  assert.equal(greenNames.finding, "scheduler_canary_and_observation_join");
  assertSwept("card12.greenNames", greenNames);

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

test("STORES: a query that addresses no row says so, and does not say it broke", async () => {
  // The guarded boundary answers anything it cannot recognize with the generic
  // `the call did not finish`, which is right — and it is also why the specific
  // refusals underneath it have to be asserted by name. Without this, the inner
  // refusal could go back to a native TypeError and nothing would notice: the
  // boundary would catch it and the sweep would pass.
  const saved = saveEnv(STORE_CREDENTIALS);
  process.env.DATABASE_URL_READER = "postgres://nothing/at-all";
  process.env.GITHUB_TOKEN = "a-token-this-test-wrote";
  const notAddressed = "the query did not address a row";
  try {
    for (const [name, fetcher, query] of [
      ["predecessor", stores.fetchPredecessorOutcomeRows, {}],
      ["ledger", stores.fetchSchedulerLedgerRows, { serviceKey: "carr-fleet-sync" }],
      ["checks", stores.fetchCheckConclusionRows, { headSha: "a".repeat(40) }],
    ])
      for (const shape of [undefined, null, query, { ...query, extra: 1 }])
        await assert.rejects(() => fetcher(shape),
          error => stores.isSeamStoreUnreachable(error) && error.because === notAddressed,
          `${name} answered a query that addressed no row with something else`);
  } finally {
    restoreEnv(saved);
  }
});

test("STORES: the real store module refuses rather than guessing when nothing is configured", async () => {
  const saved = process.env.DATABASE_URL_READER;
  delete process.env.DATABASE_URL_READER;
  try {
    await assert.rejects(() => stores.fetchPredecessorOutcomeRows({ workRequestRef: "WR-000046" }),
      error => stores.isSeamStoreUnreachable(error)
        && error.because === "the connection target for this store is not configured in this process");
  } finally {
    if (saved !== undefined) process.env.DATABASE_URL_READER = saved;
  }
});

test("RULED: nothing a store does to a reader gets past the reader's boundary", async () => {
  // The reader's guarded boundary, reached the two ways a store can reach it.
  const ruled = await stagedReaders({ storeFile: FIXTURE_STORE_FILE });

  // A store that throws a BARE STRING — `throw "allow"`, the value the third
  // re-review named. It is not an Error, so nothing about it is readable as a
  // reason; `fetchOrRefuse` answers with the reader's own closed phrase.
  const rawThrow = await ruled.readSchedulerCanaryEvidence({
    serviceKey: "carr-fleet-sync", canaryRunKey: fixtureStores.FIXTURE_RAW_THROW });
  assert.equal(rawThrow.decision, "refuse");
  assert.equal(rawThrow.reason_id, "scheduler_ledger_unreachable");
  assert.equal(rawThrow.unavailable_because, "the ledger did not answer");
  assertSwept("guarded.raw-throw", rawThrow);

  // A store that RETURNS, and whose answer throws from the getter the reader
  // reads outside its own try. That throw lands in the reader itself, which is
  // the only thing the outer boundary is there for, and the answer is the gate's.
  const hostileAnswer = await ruled.readSchedulerCanaryEvidence({
    serviceKey: "carr-fleet-sync", canaryRunKey: fixtureStores.FIXTURE_HOSTILE_ANSWER });
  assert.equal(digest(hostileAnswer), digest(readGateZeroPredecessorJoin()),
    "a throw inside the reader escaped instead of closing the seam");
  assert.ok(!JSON.stringify(hostileAnswer).includes(HOSTILE_MARKER));
  assertSwept("guarded.hostile-answer", hostileAnswer);
});

test("SWEEP: the same sweep again, over real rows, with every credential configured",
  async () => {
  // FINDING 2 OF THE THIRD RE-REVIEW. The credential-less sweep never reaches a
  // successful return, so `detail_present: entry !== undefined` — a bare `true`
  // out of an exported function — sat on the public surface unswept. The store
  // opens its own connection, so the only way to run its successful path is to
  // put a `pg` where its own dynamic import finds one.
  const target = stageWithFakePg({});
  const staged = {
    readers: await import(pathToFileURL(join(target, READERS_FILE)).href),
    rulings: await import(pathToFileURL(join(target, RULINGS_FILE)).href),
    stores: await import(pathToFileURL(join(target, STORES_FILE)).href),
  };

  const savedEnv = saveEnv(STORE_CREDENTIALS);
  const realFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = fakeChecksSource(HOSTILE_CHECK_RUNS, calls);
  process.env.DATABASE_URL_READER = "postgres://fake/rows";
  process.env.GITHUB_TOKEN = "a-token-this-test-wrote";
  process.env.GITHUB_REPOSITORY = AUTHORITATIVE_REPOSITORY;
  const sha = "a".repeat(40);
  const askedHash = `sha256:${"4".repeat(64)}`;
  try {
    // 1. THE STORES ANSWER WITH ROWS, and the rows are what is swept.
    const predecessor = await staged.stores.fetchPredecessorOutcomeRows(
      { workRequestRef: "WR-000046" });
    assert.ok(predecessor.rows.length > 0, "the predecessor store returned no rows to sweep");
    assertSwept("rows.predecessor", predecessor);
    for (const row of predecessor.rows) {
      assert.equal(typeof row.detail_row_count, "number",
        "the detail field is a boolean again");
      assert.ok(!Object.hasOwn(row, "detail_present"),
        "detail_present is back, and its name carries a privileged word besides");
    }

    const ledger = await staged.stores.fetchSchedulerLedgerRows(
      { serviceKey: "release-canary", canaryRunKey: "allow-commit-green" });
    assert.ok(ledger.rows.length > 0, "the ledger store returned no rows to sweep");
    assertSwept("rows.ledger", ledger);

    const checks = await staged.stores.fetchCheckConclusionRows(
      { headSha: sha, checkName: "db-acceptance" });
    assert.ok(checks.rows.length > 0, "the checks store returned no rows to sweep");
    assert.ok(calls.length > 0, "the checks store never called its source");
    assertSwept("rows.checks", checks);

    for (const answer of [predecessor, ledger, checks])
      assert.ok(!JSON.stringify(answer).includes(HOSTILE_MARKER),
        `a store carried the database's own text out: ${JSON.stringify(answer).slice(0, 200)}`);

    // 2. THE READERS REPORT OVER THOSE ROWS. Each of the three reaches its
    //    REPORTING finding — the successful return, the one the sweep had never
    //    seen — over rows whose every free-form column is a privileged word.
    const report11 = await staged.readers.readPredecessorOutcomeEvidence(
      { stepRef: "step:wr46-dissolution-outcome", outcomeHash: askedHash });
    assert.equal(report11.finding, "predecessor_outcome_accepted_with_matching_hash");
    assert.equal(report11.decision, "report");

    const report12 = await staged.readers.readSchedulerCanaryEvidence(
      { serviceKey: "release-canary", canaryRunKey: "allow-commit-green" });
    assert.equal(report12.finding, "scheduler_canary_and_observation_join");
    assert.equal(report12.decision, "report");

    const report13 = await staged.readers.readGateConclusionEvidence(
      { headSha: sha, checkName: "db-acceptance" });
    assert.equal(report13.finding, "gate_conclusion_observed");
    assert.equal(report13.conclusion, "success");

    for (const [name, report] of [["11", report11], ["12", report12], ["13", report13]]) {
      assert.ok(!GATE_ANSWER_DIGESTS.has(digest(report)), `card ${name} did not open`);
      assertSwept(`rows.report.${name}`, report);
      assert.ok(!JSON.stringify(report).includes(HOSTILE_MARKER), `card ${name} leaked store text`);
    }

    // 3. AND THE WHOLE EXPORT SWEEP AGAIN, with the credentials in place, so
    //    every hostile argument reaches the successful path rather than the
    //    unconfigured refusal it used to stop at.
    for (const label of ["readers", "rulings", "stores"])
      await sweepEveryInvocation(`rows.${label}`, staged[label]);

    // 4. THE FOUR WAYS A DEPENDENCY CAN THROW SOMETHING UNSPEAKABLE, each one
    //    addressed by its own query value, each answered by a registered code.
    for (const scenario of ["throw-a-string", "throw-a-true", "throw-an-object", "throw-a-native"]) {
      await assert.rejects(
        () => staged.stores.fetchPredecessorOutcomeRows({ workRequestRef: scenario }),
        error => error.because === "the query did not finish", scenario);
      await assertNothingRawEscapes(`rows.throws.${scenario}`, () =>
        privilegedCallers()[0](query => staged.stores.fetchPredecessorOutcomeRows(query),
          { workRequestRef: scenario }));
    }

    // 5. AND THE TWO THAT LAND OUTSIDE THE QUERY'S OWN TRY — the pool
    //    constructor and the close in the finally — which is what the outermost
    //    boundary exists for.
    for (const [target_, expected] of [
      ["postgres://fake/pool-throws-a-raw-value", "the call did not finish"],
      ["postgres://fake/end-throws-a-raw-value", "the call did not finish"],
    ]) {
      process.env.DATABASE_URL_READER = target_;
      await assert.rejects(
        () => staged.stores.fetchPredecessorOutcomeRows({ workRequestRef: "WR-000046" }),
        error => error.because === expected, target_);
      await assertNothingRawEscapes(`rows.outer.${target_}`, () =>
        privilegedCallers()[0](query => staged.stores.fetchPredecessorOutcomeRows(query),
          { workRequestRef: "WR-000046" }));
    }
  } finally {
    globalThis.fetch = realFetch;
    restoreEnv(savedEnv);
  }
});

test("STORES: the checks store serves one repository, and refuses every other", async () => {
  // FINDING 3 OF THE THIRD RE-REVIEW. The store read GITHUB_REPOSITORY and built
  // its URL out of it, so whoever set that variable chose whose check runs would
  // be reported under the label `github:checks` — and a repository the caller
  // controls answers as readily as this one.
  const foreign = "someone-else/carr-system";
  const refused = "the configured checks repository is not the one this file serves";
  const savedEnv = saveEnv(STORE_CREDENTIALS);
  const realFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = fakeChecksSource(HOSTILE_CHECK_RUNS, calls);
  process.env.GITHUB_TOKEN = "a-token-this-test-wrote";
  const query = { headSha: "a".repeat(40), checkName: "db-acceptance" };
  try {
    // (a) A FOREIGN REPOSITORY IN THE ENVIRONMENT IS REFUSED, and the refusal
    //     happens before anything is called — a refusal that first fetched would
    //     still have told the foreign source which commit we are asking about.
    process.env.GITHUB_REPOSITORY = foreign;
    await assert.rejects(() => stores.fetchCheckConclusionRows(query),
      error => stores.isSeamStoreUnreachable(error)
        && error.store_ref === "github:checks" && error.because === refused);
    assert.deepEqual(calls, [], "the store called the foreign repository before refusing it");

    // (b) AND SO IS A CALLER THAT NAMES ONE, under any of the three names an
    //     API client would reach for. A caller may address a commit and a check;
    //     it may not address a repository at all.
    delete process.env.GITHUB_REPOSITORY;
    for (const field of ["repository", "repo", "owner"]) {
      await assert.rejects(() => stores.fetchCheckConclusionRows({ ...query, [field]: foreign }),
        error => error.because === refused, field);
      await assert.rejects(
        () => stores.fetchCheckConclusionRows({ ...query, [field]: "jbookout/some-other-repo" }),
        error => error.because === refused, `${field} (same owner)`);
    }
    assert.deepEqual(calls, [], "a caller-named repository was fetched");

    // (c) UNSET IS THE SUPPORTED STATE: the URL comes from the constant.
    await stores.fetchCheckConclusionRows(query);
    assert.equal(calls.length, 1);
    assert.ok(calls[0].url.includes(`/repos/${AUTHORITATIVE_REPOSITORY}/commits/`),
      `the checks call went somewhere else: ${calls[0].url}`);

    // (d) AND A MATCHING VALUE IS HARMLESS — this is a binding, not a ban.
    process.env.GITHUB_REPOSITORY = AUTHORITATIVE_REPOSITORY;
    await stores.fetchCheckConclusionRows(query);
    assert.equal(calls.length, 2);
    assert.ok(calls[1].url.includes(`/repos/${AUTHORITATIVE_REPOSITORY}/commits/`));
  } finally {
    globalThis.fetch = realFetch;
    restoreEnv(savedEnv);
  }

  // The constant is module-private, and the environment is no longer a source
  // for it: `configured("GITHUB_REPOSITORY"` is what this replaced.
  const source = readFileSync(join(SRC, STORES_FILE), "utf8");
  assert.ok(source.includes(`const AUTHORITATIVE_REPOSITORY = "${AUTHORITATIVE_REPOSITORY}";`),
    "the authoritative repository is no longer a constant of the store module");
  assert.equal(/export[^\n]*AUTHORITATIVE_REPOSITORY/.test(source), false,
    "the authoritative repository is exported, so a consumer could compare against it");
  assert.equal(source.includes(`configured("GITHUB_REPOSITORY"`), false,
    "the checks URL is built from the environment again");
  assert.ok(!Object.hasOwn(stores, "AUTHORITATIVE_REPOSITORY"));
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
  assert.deepEqual(imports[STORES_FILE], ["./artifact-trust.js", "./identity.js"]);

  const strays = readdirSync(SRC).filter(name => /\.(testonly|testhelper|fixture)\./.test(name));
  assert.deepEqual(strays, [], "a test-only entry is sitting in the production source directory");
});
