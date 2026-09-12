// Unit tests for the DoctorCRE v5 Gate Zero read-only outcome record — the
// gateway half of slice V5-A02 Step B.
//
// WHAT THIS FILE PROVES AND WHAT IT DOES NOT. Every test below drives the
// module and the registered verb in process. That is enough to prove WHO the
// authority gate admits, WHO it refuses, and WHAT the contract accepts — and it
// is not, and is not treated as, evidence that any row was written. The
// authority is enforced THREE times and only two of them are reachable from
// here: the `oracleSeatOnly` gate in tools.js, the handler's own derivation, and
// ops.gate_zero_producer_actor_id() in the record layer. The third is proved by
// migration 0503's own preflight and by the disposable-Postgres migration class,
// and it is the copy a handler bug cannot step around.
//
// NO GATE ZERO OUTCOME EXISTS ANYWHERE AS A RESULT OF RUNNING THIS FILE. The
// database is a recording mock; the receipts are fixtures; and the one fixture
// that passes every clause is a fixture precisely so the refusals below have
// something to be measured against.
//
// THE MUTATION CONTROLS ARE STAGED-SOURCE, NOT MONKEY-PATCHED, for the reason
// gate-zero-assurance.v5.test.mjs already uses that shape: the seat declaration
// is a module-private frozen literal, so the only honest way to ask "what does
// this module answer when the seat is unstaffed" is to stage a copy of src with
// the declaration put back to null and read what the whole module then says.
// Patching an export would test a function applied to a fixture rather than the
// constant a consumer actually gets.

import test from "node:test";
import assert from "node:assert/strict";
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { digest } from "../src/artifact-trust.js";
import {
  GATE_ZERO_RECEIPT_CONSTANTS, GATE_ZERO_RECEIPT_FIELDS, GATE_ZERO_RECEIPT_SCHEMA,
  GATE_ZERO_RECEIPT_STATUSES, assertGateZeroReceipt, deriveGateZeroProducerSeat,
  gateZeroOracleSeatLane, gateZeroOutcomeDigest,
} from "../src/gate-zero-outcome-store.v5.js";
import { V5_A02_GATE_ZERO_PRODUCER_REGISTRATION } from "../src/gate-zero-producer-registration.v5.js";
import { TOOLS, executeRegisteredTool } from "../src/tools.js";

const SRC = new URL("../src/", import.meta.url);
const VERB = "record-gate-zero-read-only-outcome";
const OUTCOME_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
const KEY = "11111111-2222-4333-8444-555555555555";

// --- actors, every one of them server-derived in production ------------------
//
// These objects are what index.js builds from a bearer match. `review: true` is
// set in exactly one place there, on a REVIEW_TOKENS match, and nothing a caller
// sends can set it — which is what makes the class derivation below an
// authority test rather than a caller field.
const ACTOR_ID = "cccccccc-dddd-4eee-8fff-000000000000";
const SEAT = { id: ACTOR_ID, slug: "codex-reviewer", human: false, review: true, via: "review-token" };
const OTHER_REVIEWER = { id: ACTOR_ID, slug: "grok-reviewer", human: false, review: true, via: "review-token" };
const PARTNER = { id: ACTOR_ID, slug: "joe", human: true, via: "oauth-google" };
const SPONSORED = { id: ACTOR_ID, slug: "claude", human: false, sponsoring_human_slug: "joe" };
const PROBE = { id: ACTOR_ID, slug: "smoke-probe", human: false, probe: true, via: "probe-token" };

function receipt(overrides = {}) {
  return {
    ...GATE_ZERO_RECEIPT_CONSTANTS,
    subject_digest: `sha256:${"1".repeat(64)}`,
    candidate_digest: `sha256:${"2".repeat(64)}`,
    policy_digest: `sha256:${"3".repeat(64)}`,
    environment_manifest_digest: `sha256:${"4".repeat(64)}`,
    fixture_set_digest: `sha256:${"5".repeat(64)}`,
    evidence_ref: "safe:gate-zero/run/2026-09-13",
    observed_at: "2026-09-13T00:00:00Z",
    ttl_expires_at: "2026-09-20T00:00:00Z",
    status: "pass",
    comparator: "compared the four accepted predecessor outcomes, the scheduler canary readback and the gate conclusion",
    negative_admission_result: "all_required_denials_observed",
    subject_maker_identity: {
      actor_id: "claude", session_ref: "session:builder-1", authority_class: "sponsored_agent",
    },
    producer_identity: {
      actor_id: "codex-reviewer", session_ref: "session:oracle-1", authority_class: "review_agent",
    },
    evaluator_identity: {
      actor_id: "codex-reviewer", session_ref: "session:oracle-1", authority_class: "review_agent",
    },
    ...overrides,
  };
}

function mockDatabase({ existing = null } = {}) {
  const calls = [];
  const rowsFor = r => ({
    id: OUTCOME_ID,
    outcome_digest: digest(r),
    candidate_digest: r.candidate_digest,
    status: r.status,
    producing_seat_ref: V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.oracle_seat_holder_ref,
    observed_at: r.observed_at,
    recorded_at: "2026-09-13T00:00:05Z",
  });
  let recorded = existing;
  return {
    calls,
    query: async (sql, params = []) => {
      calls.push({ sql, params });
      if (sql.includes("ops.gate_zero_record_read_only_outcome")) {
        // IDEMPOTENT ON THE CANDIDATE, exactly as the SQL writer is: a second
        // call for a candidate already recorded returns the row that exists
        // rather than minting a second id.
        const offered = JSON.parse(params[1]);
        if (recorded && recorded.candidate_digest === offered.candidate_digest)
          return { rows: [{ id: OUTCOME_ID }] };
        recorded = offered;
        return { rows: [{ id: OUTCOME_ID }] };
      }
      if (sql.includes("from ops.gate_zero_read_only_outcome"))
        return { rows: [rowsFor(recorded)] };
      // THE ENVELOPE'S OWN STATEMENTS, declared rather than swallowed by a
      // catch-all: the idempotency replay read finds nothing (each test uses a
      // fresh mock), and the two audit writes are accepted. Anything else still
      // throws, so a statement this module starts issuing shows up as a failure
      // here instead of passing silently.
      if (sql.includes("from tool_call where idempotency_key")) return { rows: [] };
      if (sql.includes("insert into tool_call")) return { rows: [] };
      if (sql.includes("insert into event")) return { rows: [] };
      throw new Error(`mock database has no declared response for: ${sql}`);
    },
  };
}

async function refusal(promise) {
  try {
    await promise;
  } catch (error) {
    if (error?.payload) return error.payload;
    if (error?.error) return error;
    throw error;
  }
  throw new Error("expected a refusal and got a result");
}

// A staged copy of src with one line changed, imported as a whole module graph.
// Committed before every mutation is the discipline elsewhere; here the staging
// directory makes the point moot, because the working tree is never edited.
function stageSrc(edit) {
  const dir = mkdtempSync(join(tmpdir(), "gate-zero-outcome-"));
  cpSync(SRC, join(dir, "src"), { recursive: true });
  const target = join(dir, "src", "gate-zero-producer-registration.v5.js");
  const before = readFileSync(target, "utf8");
  const after = edit(before);
  assert.notEqual(after, before, "the staged mutation changed nothing, so it proves nothing");
  writeFileSync(target, after);
  return { dir, url: pathToFileURL(join(dir, "src", "gate-zero-outcome-store.v5.js")).href };
}

// --- the contract ------------------------------------------------------------

test("the receipt contract is r7's, restated nowhere and closed at twenty-one fields", () => {
  assert.equal(GATE_ZERO_RECEIPT_SCHEMA, "consumer-gate-receipt.v1");
  assert.equal(GATE_ZERO_RECEIPT_FIELDS.length, 21);
  assert.equal(new Set(GATE_ZERO_RECEIPT_FIELDS).size, 21);
  // Every constant this producer fixes is one of the twenty-one, so a constant
  // cannot be a field the schema does not carry.
  for (const field of Object.keys(GATE_ZERO_RECEIPT_CONSTANTS))
    assert.ok(GATE_ZERO_RECEIPT_FIELDS.includes(field), field);
  assert.deepEqual([...GATE_ZERO_RECEIPT_STATUSES],
    ["pass", "fail", "unknown", "stale", "quarantined"]);
  // r7 gives negative_admission_result exactly one legal value.
  assert.equal(GATE_ZERO_RECEIPT_CONSTANTS.negative_admission_result,
    "all_required_denials_observed");
});

test("the staffed seat lane is derived from the registration, never restated here", () => {
  assert.equal(gateZeroOracleSeatLane(), "codex-reviewer");
  assert.equal(V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.oracle_seat_holder_ref,
    "seat:codex-reviewer:gpt-5.6-sol");
  assert.equal(V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.oracle_seat_bound, true);
});

test("the record layer and the module agree about which seat holds the oracle", () => {
  // TWO HALVES OF ONE FACT, ASSERTED EQUAL. migration 0502 carries the holder
  // ref as a SQL literal and this module carries it as a frozen JS literal.
  // Staffing a different seat in one half and not the other is a defect, and
  // this is the assertion that calls it one.
  const migration = readFileSync(
    new URL("../../migrations/0502_gate_zero_read_only_outcome.sql", import.meta.url), "utf8");
  assert.match(migration,
    /select 'seat:codex-reviewer:gpt-5\.6-sol'::text/,
    "the record layer's seat literal moved away from the module's");
  assert.ok(migration.includes(V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.oracle_seat_holder_ref));
});

// --- the authority gate: five refusals and one admission ---------------------

test("CONTROL 1 — a partner identity is refused", async () => {
  const refused = await refusal(
    executeRegisteredTool(mockDatabase(), PARTNER, VERB, { idempotency_key: KEY, receipt: receipt() }));
  assert.equal(refused.error, "oracle_seat_verb_requires_the_staffed_seat");
  assert.equal(refused.actor_class, "verified_partner");
  // The refusal says WHY a partner is the wrong signer, not merely that they are.
  assert.match(refused.hint, /independent control-plane oracle/);
});

test("CONTROL 2 — a sponsored agent is refused", async () => {
  const refused = await refusal(
    executeRegisteredTool(mockDatabase(), SPONSORED, VERB, { idempotency_key: KEY, receipt: receipt() }));
  assert.equal(refused.error, "oracle_seat_verb_requires_the_staffed_seat");
  assert.equal(refused.actor_class, "sponsored_agent");
});

test("CONTROL 3 — a review_agent on a DIFFERENT seat is refused", async () => {
  // THE CONTROL THAT MATTERS MOST, because it is the one a class-based gate
  // would pass. grok-reviewer authenticates through the same review-token door
  // and derives the same review_agent class; only the staffed lane may sign.
  const refused = await refusal(
    executeRegisteredTool(mockDatabase(), OTHER_REVIEWER, VERB, { idempotency_key: KEY, receipt: receipt() }));
  assert.equal(refused.error, "oracle_seat_verb_requires_the_staffed_seat");
  assert.equal(refused.actor_class, "review_agent", "the wrong lane derives the RIGHT class");
  assert.equal(refused.seat_staffed, true, "the seat is staffed; this lane simply does not hold it");
  // And the module-level derivation refuses it by its own name, separately.
  const direct = await refusal(Promise.reject(
    (() => { try { deriveGateZeroProducerSeat(OTHER_REVIEWER); } catch (e) { return e; } })()));
  assert.equal(direct.error, "gate_zero_oracle_seat_mismatch");
  assert.equal(direct.seat_lane, "codex-reviewer");
});

test("a probe seat is refused too, so the door is not open to machine identities generally", async () => {
  const refused = await refusal(
    executeRegisteredTool(mockDatabase(), PROBE, VERB, { idempotency_key: KEY, receipt: receipt() }));
  assert.equal(refused.error, "oracle_seat_verb_requires_the_staffed_seat");
  assert.equal(refused.actor_class, "probe_agent");
});

test("CONTROL 4 — the bound seat writes, and the row it reports is the record layer's", async () => {
  const c = mockDatabase();
  const result = await executeRegisteredTool(c, SEAT, VERB, { idempotency_key: KEY, receipt: receipt() });
  assert.equal(result.ok, true);
  assert.equal(result.outcome_id, OUTCOME_ID);
  assert.equal(result.step_ref, "step:gate-zero-read-only-outcome");
  assert.equal(result.gate_id, "gate-zero-read-only-accepted");
  assert.equal(result.status, "pass");
  assert.equal(result.producing_seat_ref, "seat:codex-reviewer:gpt-5.6-sol");
  // THE DIGEST IS THE DATABASE'S. The handler compares its own computation and
  // refuses a divergence rather than reporting the local value.
  assert.equal(result.outcome_digest, gateZeroOutcomeDigest(receipt()));
  // NOTHING WAS GRANTED. The result says so in a field rather than in prose.
  assert.equal(result.effects.creates_effect, false);
  assert.equal(result.effects.clock_started, false);
  assert.equal(result.effects.benchmark_accepted, false);
  // The writer got the receipt and an idempotency key, and NOTHING ELSE: no
  // digest, no seat, no actor. Everything else is derived in the database.
  const write = c.calls.find(call => call.sql.includes("gate_zero_record_read_only_outcome"));
  assert.equal(write.params.length, 2);
  assert.equal(write.params[0], KEY);
  assert.deepEqual(Object.keys(JSON.parse(write.params[1])).sort(),
    [...GATE_ZERO_RECEIPT_FIELDS].sort());
});

test("CONTROL 5 — a second write for the same candidate is idempotent", async () => {
  const c = mockDatabase();
  const first = await executeRegisteredTool(c, SEAT, VERB, { idempotency_key: KEY, receipt: receipt() });
  const second = await executeRegisteredTool(c, SEAT, VERB, {
    idempotency_key: "99999999-8888-4777-8666-555555555555", receipt: receipt(),
  });
  assert.equal(second.outcome_id, first.outcome_id, "a retry minted a second outcome");
  assert.equal(second.outcome_digest, first.outcome_digest);
  assert.equal(second.candidate_digest, first.candidate_digest);
});

// --- the contract's own refusals ---------------------------------------------

test("CONTROL 6 — a receipt naming a maker who is also the evaluator is denied", async () => {
  const selfMade = receipt({
    subject_maker_identity: {
      actor_id: "codex-reviewer", session_ref: "session:oracle-2", authority_class: "review_agent",
    },
  });
  const refused = await refusal(
    executeRegisteredTool(mockDatabase(), SEAT, VERB, { idempotency_key: KEY, receipt: selfMade }));
  assert.equal(refused.error, "gate_zero_receipt_self_review");
  // The same denial in the other dimension r7 names: one session, both roles.
  const sameSession = receipt({
    subject_maker_identity: {
      actor_id: "claude", session_ref: "session:oracle-1", authority_class: "sponsored_agent",
    },
  });
  const refused2 = await refusal(
    executeRegisteredTool(mockDatabase(), SEAT, VERB, { idempotency_key: KEY, receipt: sameSession }));
  assert.equal(refused2.error, "gate_zero_receipt_self_review_session");
});

test("an injected non-green conclusion records as fail — Q036.D1's own falsifier", async () => {
  // A GREEN-ONLY TEST WOULD MISS THIS. r7 requires truthful failure
  // propagation, so a failing run must be RECORDABLE and must not read as a
  // binding. The record accepts it; ops.benchmark_gate_zero_outcome() is what
  // refuses to bind it, and that half is proved in the migration class.
  const c = mockDatabase();
  const result = await executeRegisteredTool(c, SEAT, VERB, {
    idempotency_key: KEY, receipt: receipt({ status: "fail" }),
  });
  assert.equal(result.status, "fail");
  assert.equal(result.effects.benchmark_accepted, false);
  // And the reader in the record layer refuses a non-passing outcome by name.
  const migration = readFileSync(
    new URL("../../migrations/0502_gate_zero_read_only_outcome.sql", import.meta.url), "utf8");
  assert.match(migration, /where status = 'pass' and ttl_expires_at > now\(\)/);
  assert.match(migration, /every outcome recorded here is non-passing or past its expiry/);
});

test("the closed schema refuses an unknown field and a missing one, and names both", () => {
  const extra = { ...receipt(), invented_field: true };
  const thrown = (() => { try { assertGateZeroReceipt(extra, { lane: "codex-reviewer" }); } catch (e) { return e; } })();
  assert.equal(thrown.payload.error, "gate_zero_receipt_fields");
  assert.deepEqual(thrown.payload.unknown, ["invented_field"]);
  const short = receipt();
  delete short.comparator;
  const thrown2 = (() => { try { assertGateZeroReceipt(short, { lane: "codex-reviewer" }); } catch (e) { return e; } })();
  assert.deepEqual(thrown2.payload.missing, ["comparator"]);
});

test("every r7 constant is refused when renamed, one field at a time", () => {
  for (const [field, value] of Object.entries(GATE_ZERO_RECEIPT_CONSTANTS)) {
    const bent = receipt({ [field]: `${value}-not` });
    const thrown = (() => {
      try { assertGateZeroReceipt(bent, { lane: "codex-reviewer" }); } catch (e) { return e; }
    })();
    assert.equal(thrown?.payload?.error, "gate_zero_receipt_constant_mismatch", field);
    assert.equal(thrown.payload.field, field);
  }
});

test("a safe: ref with one capital is refused, by name rather than bare", () => {
  const thrown = (() => {
    try {
      assertGateZeroReceipt(receipt({ evidence_ref: "safe:Gate-Zero/run" }), { lane: "codex-reviewer" });
    } catch (e) { return e; }
  })();
  assert.equal(thrown.payload.error, "gate_zero_receipt_evidence_ref_malformed");
  assert.match(thrown.payload.hint, /lowercase only/);
});

test("an expiry at or before the observed instant is refused", () => {
  for (const ttl of ["2026-09-13T00:00:00Z", "2026-09-12T00:00:00Z"]) {
    const thrown = (() => {
      try { assertGateZeroReceipt(receipt({ ttl_expires_at: ttl }), { lane: "codex-reviewer" }); } catch (e) { return e; }
    })();
    assert.equal(thrown.payload.error, "gate_zero_receipt_expiry_not_after_observation", ttl);
  }
});

test("the verb declares the new authority shape and no other", () => {
  const tool = TOOLS[VERB];
  assert.equal(tool.write, true);
  assert.equal(tool.humanOnly, false, "this verb is deliberately NOT humanOnly");
  assert.equal(tool.oracleSeatOnly, true);
  assert.notEqual(tool.authorityOnly, true, "the oracle does not write on the partner authority connection");
  assert.equal(tool.inputSchema.additionalProperties, false);
  assert.deepEqual(Object.keys(tool.inputSchema.properties).sort(), ["idempotency_key", "receipt"]);
  // IT IS THE ONLY VERB CARRYING THE FLAG. A second one arriving without its own
  // coverage is exactly what this assertion is for.
  const flagged = Object.entries(TOOLS).filter(([, t]) => t.oracleSeatOnly === true).map(([n]) => n);
  assert.deepEqual(flagged, [VERB]);
});

test("the reviewer capability profile admits the verb, and admitting it is not the authority", async () => {
  const { PROFILES, allowedIn } = await import("../src/mcp.js");
  assert.ok(PROFILES.reviewer.has(VERB), "the seat cannot reach a verb its profile excludes");
  assert.equal(allowedIn("reviewer", VERB, TOOLS[VERB]), true);
  // ...and the profile alone would admit the WRONG lane too, which is why the
  // seat gate exists. CONTROL 3 above is what proves that lane is refused.
  assert.equal(allowedIn("full", VERB, TOOLS[VERB]), true);
});

// --- the staged-source mutation controls -------------------------------------

test("MUTATION — an unstaffed seat closes the verb to everyone, including the seat", async t => {
  const staged = stageSrc(source => source.replace(
    'holder_ref: "seat:codex-reviewer:gpt-5.6-sol",', "holder_ref: null,"));
  t.after(() => rmSync(staged.dir, { recursive: true, force: true }));
  const mutated = await import(staged.url);
  assert.equal(mutated.gateZeroOracleSeatLane(), null);
  const thrown = (() => {
    try { mutated.deriveGateZeroProducerSeat(SEAT); } catch (e) { return e; }
  })();
  assert.equal(thrown.payload.error, "gate_zero_oracle_seat_unstaffed");
  // The unstaffed answer is the same "no" this surface gave before any seat
  // existed: it refuses the holder as readily as it refuses a stranger.
  const strangerToo = (() => {
    try { mutated.deriveGateZeroProducerSeat(PARTNER); } catch (e) { return e; }
  })();
  assert.equal(strangerToo.payload.error, "gate_zero_oracle_seat_unstaffed");
});

test("MUTATION — staffing a DIFFERENT lane moves who may sign, and only that", async t => {
  const staged = stageSrc(source => source.replace(
    'holder_ref: "seat:codex-reviewer:gpt-5.6-sol",', 'holder_ref: "seat:grok-reviewer:grok-4.5",'));
  t.after(() => rmSync(staged.dir, { recursive: true, force: true }));
  const mutated = await import(staged.url);
  assert.equal(mutated.gateZeroOracleSeatLane(), "grok-reviewer");
  // The lane that holds it in the working tree is now refused, and the other
  // one admitted — which is the whole content of "the seat decides".
  const refusedNow = (() => {
    try { mutated.deriveGateZeroProducerSeat(SEAT); } catch (e) { return e; }
  })();
  assert.equal(refusedNow.payload.error, "gate_zero_oracle_seat_mismatch");
  assert.equal(mutated.deriveGateZeroProducerSeat(OTHER_REVIEWER).lane, "grok-reviewer");
});
