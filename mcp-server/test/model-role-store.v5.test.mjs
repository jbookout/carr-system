// Unit tests for the DoctorCRE v5 durable role-description store.
//
// THE DATABASE HERE IS A MOCK, AND NOTHING IN THIS FILE IS EVIDENCE THAT
// ANYTHING WAS STORED. `mockDatabase` records the statements the module issues
// and returns the rows the test hands it. That is enough to prove what the
// module SENDS, what it REFUSES, and what it refuses to send — and it is not,
// and is not treated as, a claim about durable behaviour. The
// transaction-scoped proofs against a real PostgreSQL live in
// model-role-store-postgres.sql, and ops/model-role-store.candidate.sql has not
// been applied to any database by this change.
//
// The pure half of this file is stronger than the mocked half, and deliberately
// so: the round trip, the digest binding, the readback revalidation and every
// refusal of a forged or edited role are properties of functions that touch
// nothing. The mock exists only to show which parameters cross the boundary and
// which calls never happen at all.
//
// TWO THINGS THIS FILE CANNOT REACH, named rather than left implicit:
//   * CONCURRENCY. A stale compare-and-swap losing a race, and a second
//     creation colliding with a first, are properties of the lock and the
//     ledger inside PostgreSQL. What is asserted here is that the module states
//     the expectation EXPLICITLY on every call and carries the creation
//     assertion as its own parameter rather than as a null the database has to
//     interpret. model-role-store-postgres.sql exercises the refusals.
//   * THE PRINCIPAL PAIRING. The in-process S01 derivation and the database's
//     session_user derivation are both server-held, and nothing binds them to
//     each other. The module says so on every result; a mock cannot make that
//     gap smaller and this file does not pretend it does.

import test from "node:test";
import assert from "node:assert/strict";
import { digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import { V5_SYSTEM_AUTHORITY_PARTNER } from "../src/global-boundaries.v5.js";
import {
  V5_ROLE_DESCRIPTION_SCHEMA_VERSION, V5_ROLE_KEYS, assignOccupant, defineRole,
} from "../src/model-routing.v5.js";
import {
  MODEL_ROLE_CONTENT_FIELDS, MODEL_ROLE_CURRENT_POINTER_ACTION,
  MODEL_ROLE_READBACK_SCHEMA, MODEL_ROLE_REF_FIELDS, MODEL_ROLE_REVISION_ENTRY_SCHEMA,
  MODEL_ROLE_REVISION_ROWS_SCHEMA, MODEL_ROLE_SCALAR_FIELDS, MODEL_ROLE_TEXT_FIELDS,
  ModelRoleStoreError, assertNoSelfAssertedAuthority, deriveRoleCurrentPointerAuthority,
  modelRoleCanonicalBytes, modelRoleFromPreimage, modelRoleFromRows, modelRolePreimage,
  modelRoleRevisionRows, modelRoleStorePrerequisites, modelRoleStoreTools,
  validateRoleDescription,
} from "../src/model-role-store.v5.js";

// --- fixtures ---------------------------------------------------------------

const copy = value => JSON.parse(JSON.stringify(value));

const REVISION_ID = "11111111-2222-4333-8444-555555555555";
const REVISION_ID_2 = "22222222-3333-4444-8555-666666666666";
const POINTER_ID = "33333333-4444-4555-8666-777777777777";
const KEY = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
const NOW = "2026-09-09T12:00:00.000Z";

/**
 * One synthetic role description. It is a FIXTURE for the mechanism and is not
 * a proposal of any real DoctorCRE role: nothing in the module or the candidate
 * SQL seeds, defaults or hardcodes a role, and this object exists only in this
 * test file.
 *
 * The reference sets are supplied UNSORTED and the text lists in a deliberate
 * order, because the two behave differently in the digest and the tests below
 * turn on that difference.
 */
function role(overrides = {}) {
  return {
    role_key: "reviewer",
    title: "Reviewer",
    mission: "Establish independently that delivered work meets its stated contract.",
    skills: [
      "read a diff against the contract it claims to satisfy",
      "reproduce a claimed finding from source rather than from a report",
    ],
    rules: [
      "never grade work you produced",
      "a refusal names the exact missing fact, authority or dependency",
    ],
    authority: {
      authority_class: "developer",
      capability_refs: ["source.read", "receipt.read", "evidence.read"],
    },
    evidence_requirements: [
      "the exact command run and its output",
      "the file and line a finding rests on",
    ],
    quality_floor_refs: ["floor.independent_review", "floor.evidence_bound"],
    minimum_strength_ref: "strength.high_risk_engineering",
    task_classes: ["review.source", "review.contract"],
    ...overrides,
  };
}

const SEALED = defineRole(role());

/** One revision entry shaped exactly as ops.model_role_revision_readback emits it. */
function entry(description = role(), overrides = {}) {
  const sealed = defineRole(description);
  return {
    schema_version: MODEL_ROLE_REVISION_ENTRY_SCHEMA,
    revision_id: REVISION_ID,
    role_key: sealed.role_key,
    revision_no: 1,
    role_digest: sealed.role_digest,
    recomputed_role_digest: sealed.role_digest,
    structure_error: null,
    recorded_at: "2026-09-09T11:59:00.000Z",
    preimage: copy(modelRolePreimage(description)),
    confers_authority: false,
    occupant_bound: false,
    measured_qualification_bound: false,
    integrity: "recomputed_from_committed_rows",
    ...overrides,
  };
}

/** One whole readback shaped exactly as ops.model_role_readback emits it. */
function readback({ current = null, history = [entry()], pointer_history = [] } = {}) {
  return {
    schema_version: MODEL_ROLE_READBACK_SCHEMA,
    role_schema_version: V5_ROLE_DESCRIPTION_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    role_key: "reviewer",
    current,
    history,
    pointer_history,
    integrity: "recomputed_from_committed_rows",
    signed: false,
    capability_token_issued: false,
    confers_authority: false,
    occupant_bound: false,
    measured_qualification_bound: false,
  };
}

/** The pointer fields ops.model_role_current_revision merges onto an entry. */
function currentEntry(description = role(), overrides = {}) {
  return {
    ...entry(description),
    pointer_id: POINTER_ID,
    pointer_no: 1,
    expected_prior_revision_no: null,
    set_by_partner_slug: V5_SYSTEM_AUTHORITY_PARTNER,
    authority_class: "system_authority",
    authority_grant_kind: "retained_system_authority",
    set_at: "2026-09-09T12:00:01.000Z",
    ...overrides,
  };
}

// --- actors -----------------------------------------------------------------
//
// Built the way identity.js's actorFromProps builds them, so the authority
// derivations under test read the same shapes the server hands a verb.

const JOE = Object.freeze({ slug: "joe", display: "Joe", human: true, via: "oauth-google" });
const DELL = Object.freeze({ slug: "dell", display: "Dell", human: true, via: "oauth-google" });
const SPONSORED_AGENT = Object.freeze({
  slug: "codex", display: "Codex", human: false, via: "oauth-google",
  sponsoring_human_slug: "joe", human_slug: "joe",
});

// --- the mock database and tool harness -------------------------------------

/**
 * A statement recorder. Responses are matched on a substring of the SQL, so a
 * test declares which function it expects to be called; a statement with no
 * declared response throws, which is how an unexpected query fails a test
 * rather than passing silently.
 */
function mockDatabase(responses = {}) {
  const calls = [];
  const events = [];
  return {
    calls, events,
    query: async (sql, params = []) => {
      calls.push({ sql, params });
      for (const [needle, rows] of Object.entries(responses)) {
        if (sql.includes(needle)) return { rows };
      }
      throw new Error(`mock database has no declared response for: ${sql}`);
    },
  };
}

class ToolError extends Error {
  constructor(payload) {
    super(payload.error);
    this.name = "ToolError";
    this.payload = payload;
  }
}

// The real withEnvelope opens a transaction and records a tool-call envelope.
// Here it only invokes the body: these tests are about what the handler does,
// and a mock pretending to be transactional would be claiming durability this
// file explicitly does not claim.
const withEnvelope = async (_c, _actor, _verb, _args, fn) => fn();
const writeEvent = async (c, event) => { c.events.push(event); };

const tools = modelRoleStoreTools({ withEnvelope, writeEvent, ToolError });

const toolError = async (promise) => {
  try { await promise; } catch (error) {
    assert.equal(error.name, "ToolError", `expected a ToolError, got ${error?.name}: ${error?.message}`);
    return error.payload;
  }
  assert.fail("expected a refusal");
};

// ---------------------------------------------------------------------------
// The role contract is the kernel's, and is not restated here.
// ---------------------------------------------------------------------------

test("the stored content fields are exactly the fields defineRole hashes", () => {
  const derived = ["effects", "occupant_bound", "occupants_replaceable", "role_digest",
    "role_confers_no_authority_by_itself"];
  const contentKeys = Object.keys(SEALED).filter(key => !derived.includes(key)).sort();
  // A live equality rather than a copied list: if defineRole ever gains or
  // loses a content field, this fails here instead of silently storing a role
  // the record layer could not rebuild.
  assert.deepEqual(contentKeys, [...MODEL_ROLE_CONTENT_FIELDS]);
});

test("every content field is reachable from a scalar, a text list, a ref set or a constant", () => {
  const persisted = new Set([
    ...MODEL_ROLE_SCALAR_FIELDS, ...MODEL_ROLE_TEXT_FIELDS, ...MODEL_ROLE_REF_FIELDS,
    "authority", "role_key", "schema_version", "tenant",
  ]);
  assert.deepEqual(MODEL_ROLE_CONTENT_FIELDS.filter(field => !persisted.has(field)), []);
});

test("validateRoleDescription reuses the kernel's refusals rather than restating them", () => {
  assert.throws(() => validateRoleDescription(role({ role_key: "chief_of_staff" })),
    error => error.name === "V5RoutingError" && error.code === "unknown_role_key");
  assert.throws(() => validateRoleDescription(role({ quality_floor_refs: [] })),
    error => error.name === "V5RoutingError" && error.code === "invalid_shape");
  assert.throws(() => validateRoleDescription(role({ task_classes: [] })),
    error => error.name === "V5RoutingError" && error.code === "invalid_shape");
  // A quality floor is a NAMED REFERENCE. A number is not one, and this rail
  // declares no numeric business floor of its own to fall back on.
  assert.throws(() => validateRoleDescription(role({ quality_floor_refs: [0.95] })),
    error => error.name === "V5RoutingError" && error.code === "invalid_shape");
});

test("a role occupied by a replaceable model may not declare retained system authority", () => {
  assert.throws(
    () => validateRoleDescription(role({
      authority: { authority_class: "system_authority", capability_refs: [] },
    })),
    error => error.name === "V5RoutingError" &&
      error.code === "role_authority_class_not_occupiable");
});

// ---------------------------------------------------------------------------
// Round trip, order, and the digest binding.
// ---------------------------------------------------------------------------

test("rows round-trip to the identical role digest", () => {
  const rows = modelRoleRevisionRows(role());
  assert.equal(rows.schema_version, MODEL_ROLE_REVISION_ROWS_SCHEMA);
  assert.equal(rows.role_digest, SEALED.role_digest);
  const rebuilt = modelRoleFromRows(rows);
  assert.equal(rebuilt.role_digest, SEALED.role_digest);
  assert.deepEqual(rebuilt.skills, SEALED.skills);
  assert.deepEqual(rebuilt.rules, SEALED.rules);
  assert.deepEqual(rebuilt.task_classes, SEALED.task_classes);
  assert.equal(rebuilt.authority.authority_class, SEALED.authority.authority_class);
  // The Q106 properties survive storage, because they are properties of the
  // kernel's own product rather than of anything recorded beside it.
  assert.equal(rebuilt.occupant_bound, false);
  assert.equal(rebuilt.occupants_replaceable, true);
  assert.equal(rebuilt.role_confers_no_authority_by_itself, true);
});

test("the preimage is the exact object defineRole hashes", () => {
  const preimage = modelRolePreimage(role());
  assert.deepEqual(Object.keys(preimage).sort(), [...MODEL_ROLE_CONTENT_FIELDS]);
  assert.equal(preimage.schema_version, V5_ROLE_DESCRIPTION_SCHEMA_VERSION);
  assert.equal(preimage.tenant, ORGANIZATION_TENANT_ID);
  assert.equal(digest(copy(preimage)), SEALED.role_digest);
  // No domain tag is wrapped around it, so the canonical bytes begin with the
  // first C-sorted key of the object rather than with an array. The candidate
  // SQL asserts the same shape from its own side.
  assert.ok(modelRoleCanonicalBytes(role()).startsWith('{"authority":'));
});

test("text order participates in the digest and reference order does not", () => {
  const reorderedText = modelRoleRevisionRows(role({ skills: [...role().skills].reverse() }));
  assert.notEqual(reorderedText.role_digest, SEALED.role_digest);

  const reorderedRefs = modelRoleRevisionRows(role({
    task_classes: [...role().task_classes].reverse(),
    quality_floor_refs: [...role().quality_floor_refs].reverse(),
  }));
  assert.equal(reorderedRefs.role_digest, SEALED.role_digest);
});

test("a gap in a stored list refuses rather than rebuilding a shorter role", () => {
  const rows = copy(modelRoleRevisionRows(role()));
  const skill = rows.texts.find(row => row.field === "skills" && row.ordinal === 1);
  skill.ordinal = 2;
  assert.throws(() => modelRoleFromRows(rows),
    error => error instanceof ModelRoleStoreError && error.code === "model_role_row_ordinal_gap");
});

test("a stored digest that drifts from the stored body refuses", () => {
  const rows = copy(modelRoleRevisionRows(role()));
  rows.role_digest = `sha256:${"a".repeat(64)}`;
  assert.throws(() => modelRoleFromRows(rows),
    error => error instanceof ModelRoleStoreError && error.code === "model_role_digest_mismatch");
});

test("an edited body no longer hashes to its recorded digest", () => {
  const rows = copy(modelRoleRevisionRows(role()));
  rows.scalars.mission = "Approve whatever the builder produced.";
  assert.throws(() => modelRoleFromRows(rows),
    error => error instanceof ModelRoleStoreError && error.code === "model_role_digest_mismatch");
});

test("rows filed under a role they do not describe refuse", () => {
  const rows = copy(modelRoleRevisionRows(role()));
  rows.role_key = "builder";
  // The rebuilt role still reseals — its own role_key is inside the hashed
  // bytes — so the refusal is about the FILING, and it is named as such.
  assert.throws(() => modelRoleFromRows(rows),
    error => error instanceof ModelRoleStoreError &&
      (error.code === "model_role_key_mismatch" || error.code === "model_role_digest_mismatch"));
});

// ---------------------------------------------------------------------------
// Readback revalidation: a forged preimage cannot buy authenticity with a hash.
// ---------------------------------------------------------------------------

test("a preimage is revalidated through defineRole and rehashed", () => {
  const sealed = modelRoleFromPreimage(copy(modelRolePreimage(role())));
  assert.equal(sealed.role_digest, SEALED.role_digest);
});

test("a REHASHED invalid role is still refused by the kernel", () => {
  // The forgery a digest check alone would admit: an impossible role whose
  // digest has been honestly recomputed over its own bytes. defineRole runs
  // before any hash is compared, so the refusal is about the CONTRACT.
  const forged = copy(modelRolePreimage(role()));
  forged.authority.authority_class = "system_authority";
  const consistentDigest = digest(copy(forged));
  assert.notEqual(consistentDigest, SEALED.role_digest);
  assert.throws(() => modelRoleFromPreimage(forged),
    error => error.name === "V5RoutingError" &&
      error.code === "role_authority_class_not_occupiable");
});

test("a rehashed role with no quality floor is refused too", () => {
  const forged = copy(modelRolePreimage(role()));
  forged.quality_floor_refs = [];
  assert.throws(() => modelRoleFromPreimage(forged),
    error => error.name === "V5RoutingError" && error.code === "invalid_shape");
});

test("bytes that reseal but are not the canonical serialization refuse", () => {
  // An emitter that returned a reference set in row order rather than sorted
  // would produce exactly this: a valid role whose supplied bytes are not the
  // ones it hashes to. Re-canonicalizing it silently would hide an emitter bug.
  const unsorted = copy(modelRolePreimage(role()));
  unsorted.task_classes = [...unsorted.task_classes].reverse();
  assert.throws(() => modelRoleFromPreimage(unsorted),
    error => error instanceof ModelRoleStoreError &&
      error.code === "model_role_preimage_not_canonical");
});

test("a preimage from another schema version or another tenant refuses", () => {
  const wrongSchema = copy(modelRolePreimage(role()));
  wrongSchema.schema_version = "role-description.v2";
  assert.throws(() => modelRoleFromPreimage(wrongSchema),
    error => error.code === "model_role_schema_version_invalid");

  const wrongTenant = copy(modelRolePreimage(role()));
  wrongTenant.tenant = "someone-else";
  assert.throws(() => modelRoleFromPreimage(wrongTenant),
    error => error.code === "model_role_tenant_mismatch");
});

// ---------------------------------------------------------------------------
// The authority derivation for a current-pointer change.
// ---------------------------------------------------------------------------

test("the current pointer is a retained, non-delegable system-authority act", () => {
  const derived = deriveRoleCurrentPointerAuthority({ actor: JOE, now: NOW });
  assert.equal(derived.partner_slug, V5_SYSTEM_AUTHORITY_PARTNER);
  assert.equal(derived.authority_class, "system_authority");
  assert.equal(derived.grant_kind, "retained_system_authority");
  assert.equal(derived.action, MODEL_ROLE_CURRENT_POINTER_ACTION);
  assert.equal(derived.delegable, false);
  assert.equal(derived.permanent_privilege_granted, false);
  assert.equal(derived.authority_source, "global-boundaries.v5.evaluateActorAuthority");
  // The scope of the claim rides on the result rather than being assumed.
  assert.equal(derived.principal_binding.cross_derivation_binding_recorded, false);
  assert.equal(derived.principal_binding.accepts_actor_from_payload, false);
});

test("the other verified partner is refused: system authority is not shared", () => {
  assert.throws(() => deriveRoleCurrentPointerAuthority({ actor: DELL, now: NOW }),
    error => error instanceof ModelRoleStoreError &&
      error.code === "role_current_pointer_authority_refused" &&
      error.detail.boundary_reason_id === "system_authority_reserved_to_joe" &&
      error.detail.delegable === false);
});

test("a sponsored agent is refused before any authority class is considered", () => {
  assert.throws(() => deriveRoleCurrentPointerAuthority({ actor: SPONSORED_AGENT, now: NOW }),
    error => error.code === "role_current_pointer_authority_refused" &&
      error.detail.boundary_reason_id === "actor_not_verified_partner" &&
      error.detail.derived_authorization_class === "sponsored_agent");
});

test("an actor that merely claims the right slug is not the partner", () => {
  // human:false makes identity.js's own predicate answer something other than
  // verified_partner, and S01 refuses on that rather than on the slug.
  assert.throws(
    () => deriveRoleCurrentPointerAuthority({ actor: { slug: "joe", human: false }, now: NOW }),
    error => error.code === "role_current_pointer_authority_refused");
});

test("the authority evaluation refuses a clock this module was handed loosely", () => {
  assert.throws(() => deriveRoleCurrentPointerAuthority({ actor: JOE, now: "yesterday" }),
    error => error.code === "server_instant_unavailable");
  assert.throws(() => deriveRoleCurrentPointerAuthority({ actor: JOE }),
    error => error.code === "server_instant_unavailable");
});

// ---------------------------------------------------------------------------
// The payload boundary.
// ---------------------------------------------------------------------------

test("a self-asserted actor, approval or verification is refused by name", () => {
  for (const injected of [
    { actor: "joe" }, { actor_slug: "joe" }, { acting_actor: "joe" },
    { approved: true }, { approved_by: "joe" }, { verified: true },
    { verified_human: true }, { human_approved: true }, { authority_granted: true },
    { principal: "carr_authority_joe" }, { session_user: "carr_authority_joe" },
    { set_by_partner_slug: "joe" }, { signed: true }, { system_authority: true },
  ]) {
    assert.throws(() => assertNoSelfAssertedAuthority({ role_key: "reviewer", ...injected }, "args"),
      error => error.code === "self_asserted_authority_refused",
      `expected ${Object.keys(injected)[0]} to be refused`);
  }
});

test("a legitimate role payload is not caught by the injection sweep", () => {
  // `authority`, `authority_class` and `capability_refs` are role CONTENT, and
  // a rule or mission may legitimately contain the words this sweep looks for.
  // The sweep is over KEYS, so free text about occupants and qualification
  // survives it — which is the whole point of a durable job description.
  const legitimate = role({
    mission: "Decide whether a qualified occupant met the role's evidence bar.",
    rules: ["an occupant is replaceable; the job description is not"],
  });
  assert.doesNotThrow(() => assertNoSelfAssertedAuthority({ role: legitimate }, "args"));
  assert.equal(modelRoleRevisionRows(legitimate).role_key, "reviewer");
});

test("an occupancy or qualification claim meets the fail-closed gap, not a generic refusal", () => {
  const db = mockDatabase();
  return (async () => {
    for (const injected of [
      { occupant: { model_key: "claude-opus-5" } },
      { occupant_key: "claude-opus-5@1/high" },
      { qualified: true },
      { qualification_id: "q-1" },
    ]) {
      const payload = await toolError(tools["record-model-role-revision"].handler(
        db, JOE, { idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
          role_digest: SEALED.role_digest, role: role(), ...injected }));
      assert.equal(payload.error, "model_role_occupancy_unbound");
      assert.equal(payload.detail.resolved, false);
      assert.equal(payload.detail.binding_ref, "binding:model-role-occupant-qualification");
    }
    // The strongest assertion here is the smallest: nothing reached the
    // database. A refusal after a query is one that can be mistaken for a write
    // that nearly worked.
    assert.equal(db.calls.length, 0);
  })();
});

test("an occupant assigned in the routing kernel is never persisted by this rail", () => {
  // The kernel's own answer, reused rather than restated: assigning an occupant
  // leaves the role digest untouched and grants the occupant nothing. This rail
  // stores the role and has no relation, argument or verb for the assignment.
  const assignment = assignOccupant(SEALED, {
    model_key: "claude-opus-5", model_version: "2026-05", effort: "high", backend_key: "cloud-anthropic",
  });
  assert.equal(assignment.role_digest, SEALED.role_digest);
  assert.equal(assignment.occupant_grants_no_authority, true);
  assert.equal(Object.keys(tools).some(name => /occupan/i.test(name)), false);
  const prerequisites = modelRoleStorePrerequisites();
  assert.equal(prerequisites.occupancy_binding.resolved, false);
  assert.equal(prerequisites.occupancy_bound, false);
  assert.equal(prerequisites.measured_qualification_bound, false);
  assert.equal(prerequisites.role_confers_authority, false);
});

// ---------------------------------------------------------------------------
// record-model-role-revision.
// ---------------------------------------------------------------------------

test("recording a revision sends the rows, then revalidates the readback", async () => {
  const db = mockDatabase({
    "model_role_record_revision": [{ id: REVISION_ID }],
    "model_role_revision_readback": [{ revision: entry() }],
  });
  const result = await tools["record-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, role: role(),
  });
  assert.equal(result.ok, true);
  assert.equal(result.revision_id, REVISION_ID);
  assert.equal(result.role_digest, SEALED.role_digest);
  assert.equal(result.is_current, false);
  assert.equal(result.confers_authority, false);
  assert.equal(result.occupant_bound, false);
  assert.equal(result.measured_qualification_bound, false);
  assert.equal(result.revision.revalidated_through_define_role, true);
  assert.equal(result.effects.grants_authority, false);
  assert.equal(result.effects.binds_occupant, false);
  assert.equal(result.effects.clock_started, false);

  const [write, readbackCall] = db.calls;
  assert.ok(write.sql.includes("ops.model_role_record_revision"));
  // The parameters that bind the write: role, version, key, and the digest this
  // module computed rather than the one the caller sent.
  assert.equal(write.params[0], "reviewer");
  assert.equal(write.params[1], 1);
  assert.equal(write.params[2], KEY);
  assert.equal(write.params[3], SEALED.role_digest);
  const texts = JSON.parse(write.params[5]);
  const refs = JSON.parse(write.params[6]);
  assert.deepEqual([...new Set(texts.map(row => row.field))].sort(), [...MODEL_ROLE_TEXT_FIELDS]);
  assert.deepEqual([...new Set(refs.map(row => row.field))].sort(), [...MODEL_ROLE_REF_FIELDS]);
  // No actor, tenant, instant or authority crosses the boundary.
  assert.equal(write.params.some(p => typeof p === "string" && p.includes("joe")), false);
  assert.ok(readbackCall.sql.includes("ops.model_role_revision_readback"));
  assert.equal(db.events.length, 1);
  assert.equal(db.events[0].verb, "record-model-role-revision");
});

test("a caller's digest is compared, never used, and a mismatch never reaches the database", async () => {
  const db = mockDatabase();
  const payload = await toolError(tools["record-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: `sha256:${"b".repeat(64)}`, role: role(),
  }));
  assert.equal(payload.error, "model_role_digest_mismatch");
  assert.equal(payload.expected, SEALED.role_digest);
  assert.equal(db.calls.length, 0);
});

test("a role filed under the wrong role key never reaches the database", async () => {
  const db = mockDatabase();
  const payload = await toolError(tools["record-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "builder", revision_no: 1,
    role_digest: SEALED.role_digest, role: role(),
  }));
  assert.equal(payload.error, "model_role_key_mismatch");
  assert.equal(db.calls.length, 0);
});

test("a malformed idempotency key or version is refused before any statement", async () => {
  const db = mockDatabase();
  assert.equal((await toolError(tools["record-model-role-revision"].handler(db, JOE, {
    idempotency_key: "not-a-uuid", role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, role: role(),
  }))).error, "invalid_uuid");
  assert.equal((await toolError(tools["record-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 0,
    role_digest: SEALED.role_digest, role: role(),
  }))).error, "invalid_revision_no");
  assert.equal(db.calls.length, 0);
});

test("an idempotency mismatch raised by the record layer is surfaced, not swallowed", async () => {
  // The database owns the replay comparison — content, role, version and the
  // derived writer — because only it holds the earlier row. What is asserted
  // here is that the module does not turn that refusal into a success.
  const db = {
    calls: [], events: [],
    query: async (sql) => {
      if (sql.includes("model_role_record_revision")) {
        throw new Error("model role idempotency key was already used for a different revision, role, version or writer");
      }
      throw new Error(`unexpected: ${sql}`);
    },
  };
  await assert.rejects(() => tools["record-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, role: role(),
  }), /idempotency key/);
});

test("a readback that does not revalidate refuses after the write", async () => {
  const drifted = entry(role(), { recomputed_role_digest: `sha256:${"c".repeat(64)}` });
  const db = mockDatabase({
    "model_role_record_revision": [{ id: REVISION_ID }],
    "model_role_revision_readback": [{ revision: drifted }],
  });
  const payload = await toolError(tools["record-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, role: role(),
  }));
  assert.equal(payload.error, "model_role_stored_digest_drift");
});

test("a readback carrying another reader's shape refuses", async () => {
  const db = mockDatabase({
    "model_role_record_revision": [{ id: REVISION_ID }],
    "model_role_revision_readback": [{ revision: { ...entry(), schema_version: "something-else.v1" } }],
  });
  const payload = await toolError(tools["record-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, role: role(),
  }));
  assert.equal(payload.error, "model_role_revision_entry_schema_invalid");
});

// ---------------------------------------------------------------------------
// read-model-role.
// ---------------------------------------------------------------------------

test("a read rebuilds and revalidates every revision it returns", async () => {
  const second = role({ mission: "Establish independently that delivered work meets its contract, and say what it does not." });
  const history = [
    entry(role()),
    { ...entry(second), revision_id: REVISION_ID_2, revision_no: 2 },
  ];
  const db = mockDatabase({
    "model_role_readback": [{ readback: readback({
      history,
      current: currentEntry(second, { revision_id: REVISION_ID_2, revision_no: 2, pointer_no: 2, expected_prior_revision_no: 1 }),
      pointer_history: [
        { pointer_no: 1, revision_no: 1, expected_prior_revision_no: null, set_by_partner_slug: "joe" },
        { pointer_no: 2, revision_no: 2, expected_prior_revision_no: 1, set_by_partner_slug: "joe" },
      ],
    }) }],
  });
  const result = await tools["read-model-role"].handler(db, JOE, { role_key: "reviewer" });
  assert.equal(result.ok, true);
  assert.equal(result.history.length, 2);
  // HISTORY IS PRESERVED AND EACH ENTRY IS RE-DERIVED HERE. Both revisions are
  // rebuilt through defineRole, and the superseded one is still readable.
  assert.equal(result.history[0].revalidated_through_define_role, true);
  assert.equal(result.history[1].revalidated_through_define_role, true);
  assert.notEqual(result.history[0].role_digest, result.history[1].role_digest);
  assert.equal(result.history[0].role.role_digest, result.history[0].role_digest);
  assert.equal(result.current.revision_no, 2);
  assert.equal(result.pointer_history.length, 2);
  assert.equal(result.pointer_history[1].expected_prior_revision_no, 1);
  // The authenticity claim is scoped on the result rather than assumed.
  assert.equal(result.current.authenticity.claim,
    "re_derived_from_committed_rows_and_resealed_here_through_defineRole");
  assert.ok(result.current.authenticity.does_not_prove.some(line => line.includes("signed")));
  assert.ok(result.current.authenticity.does_not_prove.some(line => line.includes("qualified")));
});

test("a role nobody has recorded reads back as absent rather than as a default", async () => {
  const db = mockDatabase({
    "model_role_readback": [{ readback: readback({ current: null, history: [] }) }],
  });
  const result = await tools["read-model-role"].handler(db, JOE, { role_key: "reviewer" });
  assert.equal(result.current, null);
  assert.deepEqual(result.history, []);
});

test("a read refuses a tampered persisted body instead of reporting it", async () => {
  const tampered = entry();
  tampered.preimage = copy(tampered.preimage);
  tampered.preimage.mission = "Approve whatever the builder produced.";
  const db = mockDatabase({ "model_role_readback": [{ readback: readback({ history: [tampered] }) }] });
  const payload = await toolError(tools["read-model-role"].handler(db, JOE, { role_key: "reviewer" }));
  assert.equal(payload.error, "model_role_readback_digest_mismatch");
});

test("a read refuses an unsettled role key before any statement", async () => {
  const db = mockDatabase();
  const payload = await toolError(tools["read-model-role"].handler(db, JOE, { role_key: "chief_of_staff" }));
  assert.equal(payload.error, "unknown_role_key");
  assert.deepEqual(payload.detail.registered, [...V5_ROLE_KEYS]);
  assert.equal(db.calls.length, 0);
});

// ---------------------------------------------------------------------------
// set-current-model-role-revision.
// ---------------------------------------------------------------------------

test("the current-pointer verb is human-only and authority-only", () => {
  assert.equal(tools["set-current-model-role-revision"].humanOnly, true);
  assert.equal(tools["set-current-model-role-revision"].authorityOnly, true);
  assert.equal(tools["record-model-role-revision"].humanOnly, undefined);
});

test("a creation states its expectation as an explicit null and its shape as a parameter", async () => {
  const db = mockDatabase({
    "model_role_server_instant": [{ now: NOW }],
    "model_role_set_current_revision": [{ id: POINTER_ID }],
    "model_role_readback": [{ readback: readback({ current: currentEntry() }) }],
  });
  const result = await tools["set-current-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, expected_current_revision_no: null,
  });
  assert.equal(result.ok, true);
  assert.equal(result.pointer_id, POINTER_ID);
  assert.equal(result.history_preserved, true);
  assert.equal(result.confers_authority, false);
  assert.equal(result.effects.current_role_description_changed, true);
  assert.equal(result.effects.grants_authority, false);
  assert.equal(result.effects.binds_occupant, false);
  assert.equal(result.effects.dispatches_model, false);
  assert.equal(result.authority.grant_kind, "retained_system_authority");
  assert.equal(result.record_layer_principal, V5_SYSTEM_AUTHORITY_PARTNER);
  assert.equal(result.principal_binding.cross_derivation_binding_recorded, false);

  const write = db.calls.find(call => call.sql.includes("model_role_set_current_revision"));
  // p_expect_creation is its OWN parameter, so the database never has to infer
  // "create" from a null somebody may simply have omitted.
  assert.equal(write.params[4], true);
  assert.equal(write.params[5], null);
});

test("a replacement carries the exact expected version through to the database", async () => {
  const db = mockDatabase({
    "model_role_server_instant": [{ now: NOW }],
    "model_role_set_current_revision": [{ id: POINTER_ID }],
    "model_role_readback": [{ readback: readback({
      current: currentEntry(role(), { revision_no: 1, pointer_no: 2, expected_prior_revision_no: 3 }),
    }) }],
  });
  await tools["set-current-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, expected_current_revision_no: 3,
  });
  const write = db.calls.find(call => call.sql.includes("model_role_set_current_revision"));
  assert.equal(write.params[2], 1);
  assert.equal(write.params[3], SEALED.role_digest);
  assert.equal(write.params[4], false);
  assert.equal(write.params[5], 3);
});

test("omitting the expected version refuses; it is never inferred", async () => {
  const db = mockDatabase({ "model_role_server_instant": [{ now: NOW }] });
  const payload = await toolError(tools["set-current-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 2, role_digest: SEALED.role_digest,
  }));
  assert.equal(payload.error, "model_role_expected_revision_required");
  assert.equal(db.calls.length, 0);
});

test("a stale compare-and-swap refused by the record layer is surfaced", async () => {
  const db = {
    calls: [], events: [],
    query: async (sql) => {
      if (sql.includes("model_role_server_instant")) return { rows: [{ now: NOW }] };
      if (sql.includes("model_role_set_current_revision")) {
        throw new Error("model role reviewer current revision is 4, not the expected 3");
      }
      throw new Error(`unexpected: ${sql}`);
    },
  };
  await assert.rejects(() => tools["set-current-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 5,
    role_digest: SEALED.role_digest, expected_current_revision_no: 3,
  }), /current revision is 4, not the expected 3/);
});

test("a creation colliding with an existing pointer is surfaced", async () => {
  const db = {
    calls: [], events: [],
    query: async (sql) => {
      if (sql.includes("model_role_server_instant")) return { rows: [{ now: NOW }] };
      if (sql.includes("model_role_set_current_revision")) {
        throw new Error("model role reviewer already has current revision 1; a creation compare-and-swap cannot move it");
      }
      throw new Error(`unexpected: ${sql}`);
    },
  };
  await assert.rejects(() => tools["set-current-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 2,
    role_digest: SEALED.role_digest, expected_current_revision_no: null,
  }), /creation compare-and-swap cannot move it/);
});

test("a non-system-authority partner reaches no writer", async () => {
  for (const actor of [DELL, SPONSORED_AGENT]) {
    const db = mockDatabase({ "model_role_server_instant": [{ now: NOW }] });
    const payload = await toolError(tools["set-current-model-role-revision"].handler(db, actor, {
      idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
      role_digest: SEALED.role_digest, expected_current_revision_no: null,
    }));
    assert.equal(payload.error, "role_current_pointer_authority_refused");
    // One read-only statement precedes the derivation, because the instant is
    // the record layer's rather than this module's. No writer is reached.
    assert.equal(db.calls.length, 1);
    assert.ok(db.calls[0].sql.includes("model_role_server_instant"));
    assert.equal(db.events.length, 0);
  }
});

test("the instant comes from the record layer, and an unusable one refuses", async () => {
  const db = mockDatabase({ "model_role_server_instant": [{ now: "whenever" }] });
  const payload = await toolError(tools["set-current-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, expected_current_revision_no: null,
  }));
  assert.equal(payload.error, "server_instant_unavailable");
});

test("a pointer readback that does not name the requested revision refuses", async () => {
  const db = mockDatabase({
    "model_role_server_instant": [{ now: NOW }],
    "model_role_set_current_revision": [{ id: POINTER_ID }],
    "model_role_readback": [{ readback: readback({
      current: currentEntry(role(), { revision_no: 9 }),
    }) }],
  });
  const payload = await toolError(tools["set-current-model-role-revision"].handler(db, JOE, {
    idempotency_key: KEY, role_key: "reviewer", revision_no: 1,
    role_digest: SEALED.role_digest, expected_current_revision_no: null,
  }));
  assert.equal(payload.error, "model_role_current_pointer_drift");
});

// ---------------------------------------------------------------------------
// The projection: what is claimed, and what is named as missing.
// ---------------------------------------------------------------------------

test("the projection names the gaps it does not fill and claims nothing it cannot", () => {
  const p = modelRoleStorePrerequisites();
  assert.equal(p.second_role_contract_declared, false);
  assert.equal(p.second_quality_floor_declared, false);
  assert.equal(p.numeric_policy_default_declared, false);
  assert.equal(p.signed_capability_token_issued, false);
  assert.equal(p.provenance_survives_serialization, false);
  assert.equal(p.candidate_sql_applied_as_migration, false);
  assert.equal(p.tools_registered, false);
  assert.equal(p.current_pointer_authority_class, "system_authority");
  assert.equal(p.current_pointer_delegable, false);
  assert.deepEqual(p.role_keys, [...V5_ROLE_KEYS]);
  assert.equal(p.occupiable_authority_classes.includes("system_authority"), false);
  // The routing kernel's own gap list is carried rather than restated, so the
  // qualification producer, the dispatch path and the backend health source
  // stay named in exactly one place — and stay named.
  const kernelGaps = p.routing_kernel_unimplemented_dependencies.join(" ");
  assert.ok(kernelGaps.includes("route-qualification.v1 producer"));
  assert.ok(kernelGaps.includes("model dispatch"));
  assert.ok(kernelGaps.includes("live backend health source"));
  assert.equal(p.effects.database_writes, 0);
});

test("the principal binding states both derivations and the seam between them", () => {
  const scope = modelRoleStorePrerequisites().principal_binding;
  assert.ok(scope.in_process_derivation.includes("evaluateActorAuthority"));
  assert.ok(scope.record_layer_derivation.includes("ops.authority_actor_slug()"));
  assert.equal(scope.both_are_server_derived, true);
  assert.equal(scope.accepts_actor_from_payload, false);
  assert.equal(scope.cross_derivation_binding_recorded, false);
  assert.ok(scope.unbound_seam.includes("session_user"));
});

test("this rail registers nothing and activates nothing", () => {
  assert.deepEqual(Object.keys(tools).sort(),
    ["read-model-role", "record-model-role-revision", "set-current-model-role-revision"]);
  for (const name of Object.keys(tools)) {
    assert.equal(/register|activate|deploy|dispatch|retire|delete/i.test(name), false);
  }
});
