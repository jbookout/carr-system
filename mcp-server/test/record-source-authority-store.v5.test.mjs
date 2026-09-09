// V5-F01 phase 2 — the persistence tail, proved case by case.
//
// Everything here is synthetic and nothing reaches a real database, provider,
// network or file. The store is exercised against a SCRIPTED FAKE HANDLE that
// records every statement and every parameter, so these tests prove the things a
// Node suite can actually prove about a persistence layer:
//
//   * which values are DERIVED and which are refused from the caller,
//   * which envelopes are built and which are deliberately left null,
//   * that prior state, prior artifacts, prior links and holds are LOADED,
//   * that the compare-and-swap operands travel to the database,
//   * that the canonical bytes are exactly what the SQL fixtures will compare.
//
// The things a fake cannot prove — real concurrency, real append-only refusal,
// real direct-DML refusal, real corrupt-row readback — are the disposable-
// database gate's job, and ops/record-source-authority-local-pg-gate.py proves
// them there instead of being asserted twice here.
//
// NO FIXTURE NAMES A REAL THING. Every account, native id, object key, drive
// item, digest and retention period below is unmistakably test data, and the
// registries are TEST POLICY: what a caller might supply, never a claim about
// CARR's real field owners or retention periods.

import test from "node:test";
import assert from "node:assert/strict";

import { canonicalJson, digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_F01_FIELD_REGISTRY_SCHEMA_VERSION,
  V5_F01_RETENTION_REGISTRY_SCHEMA_VERSION,
  V5_F01_PROPOSAL_SCHEMA_VERSION,
  compileFieldAuthorityRegistry,
  compileRetentionRegistry,
  fieldAuthorityRegistryPreimage,
  retentionRegistryPreimage,
  v5F01PolicyDigest,
  v5F01DecisionSubsetDigest,
  V5F01Error,
} from "../src/record-source-authority.v5.js";
import {
  V5_F01_STORE_SCHEMA_VERSION,
  V5_F01_ENVELOPE_SCHEMA_VERSION,
  V5_F01_OPERATIONS,
  V5_F01_READ_KINDS,
  V5_F01_STORE_RECORD_KINDS,
  V5_F01_DERIVED_ONLY_FIELDS,
  V5F01StoreError,
  createRecordSourceAuthorityStore,
  v5F01StoreOperationSchemas,
  v5F01ToolRegistrations,
  v5F01StoreEnvelope,
  v5F01EnvelopeCanonicalBytes,
  storedPolicyRecord,
  storedFieldStateRecord,
  storedHoldRecord,
} from "../src/record-source-authority-store.v5.js";

// --- synthetic policy ------------------------------------------------------

const SERVER_NOW = "2026-09-09T12:00:00.000Z";
const T = {
  early: "2026-09-01T09:00:00Z",
  mid: "2026-09-05T09:00:00Z",
  late: "2026-09-08T09:00:00Z",
  future: "2026-09-10T09:00:00Z",
};
const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;
const VALUE_A = D(1);
const VALUE_B = D(2);

const FIELD_POLICY = Object.freeze({
  schema_version: V5_F01_FIELD_REGISTRY_SCHEMA_VERSION,
  registry_version: 1,
  tenant: ORGANIZATION_TENANT_ID,
  entries: [
    {
      entity: "deal", field: "commission_amount",
      authoritative_home: "salesforce", owner_source: "salesforce",
      permitted_sources: [
        { source_system: "salesforce", direction: "inbound" },
        { source_system: "neon_record_layer", direction: "outbound" },
      ],
      requires_account_identity: true, requires_native_identity: true,
      version_comparator: "integer_sequence",
      conflict_behavior: "reconcile", human_resolver_class: "deal_owner",
      readback_required: true, sensitivity_classes: ["lease_economics"],
      taint_class: "corporate_source_of_record",
    },
    {
      entity: "deal", field: "next_step",
      authoritative_home: "neon_record_layer", owner_source: "neon_record_layer",
      permitted_sources: [{ source_system: "neon_record_layer", direction: "bidirectional" }],
      requires_account_identity: false, requires_native_identity: false,
      version_comparator: "integer_sequence",
      conflict_behavior: "reconcile", human_resolver_class: "deal_owner",
      readback_required: false, sensitivity_classes: ["lease_economics"],
      taint_class: "first_party_record_layer",
    },
  ],
});

const RETENTION_POLICY = Object.freeze({
  schema_version: V5_F01_RETENTION_REGISTRY_SCHEMA_VERSION,
  registry_version: 1,
  tenant: ORGANIZATION_TENANT_ID,
  classes: [
    {
      artifact_class: "synthetic_test_lease",
      authoritative_home: "onedrive",
      default_retention_days: 1,
      governing_constraints: ["synthetic_test_constraint"],
      deletion_proof_required: true,
      surviving_derivatives: ["synthetic_test_abstract"],
    },
  ],
});

const FIELD_REGISTRY = compileFieldAuthorityRegistry(FIELD_POLICY);
const RETENTION_REGISTRY = compileRetentionRegistry(RETENTION_POLICY);
const FIELD_PREIMAGE = fieldAuthorityRegistryPreimage(FIELD_REGISTRY);
const RETENTION_PREIMAGE = retentionRegistryPreimage(RETENTION_REGISTRY);

const STORED_POLICY_RECORD = storedPolicyRecord({
  registry_version: 1,
  field_registry: FIELD_PREIMAGE,
  field_registry_digest: FIELD_REGISTRY.registry_digest,
  retention_registry: RETENTION_PREIMAGE,
  retention_registry_digest: RETENTION_REGISTRY.registry_digest,
  prior_policy_digest: null,
  installed_by: "joe",
  installed_at: SERVER_NOW,
});
const POLICY_DIGEST = digest(STORED_POLICY_RECORD);

const STORED_POLICY = Object.freeze({
  policy_seq: 1,
  registry_version: 1,
  policy_digest: POLICY_DIGEST,
  prior_policy_digest: null,
  field_registry: FIELD_PREIMAGE,
  field_registry_digest: FIELD_REGISTRY.registry_digest,
  retention_registry: RETENTION_PREIMAGE,
  retention_registry_digest: RETENTION_REGISTRY.registry_digest,
  domain_policy_digest: v5F01PolicyDigest(),
  installed_by: "joe",
  installed_at: SERVER_NOW,
  integrity: "recomputed_from_committed_row",
});

// --- actors ----------------------------------------------------------------

const JOE = Object.freeze({ slug: "joe", display: "Joe", human: true, via: "oauth-google" });
const AGENT = Object.freeze({
  slug: "codex", display: "Codex", human: false, via: "oauth-google",
  sponsoring_human_slug: "joe", human_slug: "joe",
});
const ctx = actor => ({ actor });

// --- the scripted fake handle ---------------------------------------------

class FakeDb {
  constructor(script = {}) {
    this.script = { ...script };
    this.calls = [];
    this.began = 0;
    this.committed = 0;
    this.rolledBack = 0;
  }

  set(key, value) { this.script[key] = value; return this; }

  async query(text, params = []) {
    this.calls.push({ text, params });
    if (text === "BEGIN") { this.began += 1; return { rows: [] }; }
    if (text === "COMMIT") { this.committed += 1; return { rows: [] }; }
    if (text === "ROLLBACK") { this.rolledBack += 1; return { rows: [] }; }

    if (text.includes("ops.f01_principal()") && text.includes("ops.f01_now_text()")) {
      return { rows: [{
        principal: this.script.principal ?? {
          actor_slug: "joe", human: true,
          authorization_class: "verified_partner",
          derived_by: "server_established_transaction_context",
        },
        server_now: this.script.server_now ?? SERVER_NOW,
      }] };
    }
    if (text.includes("ops.f01_replay_outcome(")) {
      if (this.script.replayError) throw this.script.replayError;
      return { rows: [{ outcome: this.script.replay_outcome ?? null }] };
    }
    if (text.includes("ops.f01_stored_derivatives(")) {
      return { rows: [{ derivatives: this.script.stored_derivatives ?? null }] };
    }
    if (text.includes("ops.f01_current_policy()")) {
      return { rows: [{ policy: this.script.policy ?? null }] };
    }
    if (text.includes("ops.f01_current_field_state(")) {
      return { rows: [{ state: this.script.field_state ?? null }] };
    }
    if (text.includes("ops.f01_stored_artifact_by_identity(")) {
      return { rows: [{ prior: this.script.prior_artifact ?? null }] };
    }
    if (text.includes("ops.f01_stored_artifact(")) {
      return { rows: [{ artifact: this.script.stored_artifact ?? null }] };
    }
    if (text.includes("ops.f01_hold_inventory(")) {
      return { rows: [{
        holds: this.script.holds ?? [],
        holds_digest: this.script.holds_digest ?? digest(this.script.holds ?? []),
      }] };
    }
    if (text.includes("ops.f01_read('document'")) {
      return { rows: [{ body: { body: this.script.document_current ?? null } }] };
    }
    if (text.includes("ops.f01_read('hold_history'")) {
      return { rows: [{ body: { body: this.script.hold_history ?? [] } }] };
    }
    if (text.includes("ops.f01_read(")) {
      return { rows: [{ body: this.script.read_body ?? { body: null } }] };
    }
    for (const fn of ["f01_install_policy", "f01_apply_observation", "f01_record_artifact",
      "f01_record_proposal", "f01_record_document", "f01_record_hold",
      "f01_record_deletion_evaluation"]) {
      if (text.includes(`ops.${fn}(`)) {
        this.lastWrite = { fn, params };
        if (this.script.writeError) throw this.script.writeError;
        const envelope = index => params[index] == null ? null : JSON.parse(params[index]);
        const verified = value => value && ({ record: value.record, record_digest: value.record_digest });
        const env = envelope(fn === "f01_apply_observation" ? 5 : 0);
        const rec = env?.record;
        const outcome = { actor_slug: "joe", outcome: "recorded" };
        switch (fn) {
          case "f01_install_policy":
            Object.assign(outcome, { operation: "register-record-source-authority-policy", outcome: "installed",
              readback: { ...rec, policy_digest: env.record_digest } }); break;
          case "f01_apply_observation":
            Object.assign(outcome, { operation: "record-source-observation",
              outcome: params[0] === "accept" ? "accepted" : params[0], entity: params[1], field: params[2],
              policy_digest: params[3], ...envelope(12),
              current_state_transition_digest: envelope(6)?.record_digest ?? null,
              event_digest: envelope(7)?.record_digest ?? null,
              mutation_receipt_digest: envelope(8)?.record_digest ?? null,
              reconciliation_item_digest: envelope(9)?.record_digest ?? null,
              readback: { current_state: rec } }); break;
          case "f01_record_artifact":
            Object.assign(outcome, { operation: "record-corporate-artifact", artifact_digest: env.record_digest,
              readback: { artifact: rec } }); break;
          case "f01_record_proposal":
            Object.assign(outcome, { operation: "record-parsed-proposal", proposal_digest: env.record_digest,
              link_digest: envelope(1).record_digest, readback: verified(envelope(1)) }); break;
          case "f01_record_document":
            Object.assign(outcome, { operation: "record-document-identity", document_digest: env.record_digest,
              official_filing_state: rec.official_filing_state, readback: verified(env) }); break;
          case "f01_record_hold":
            Object.assign(outcome, { operation: "record-artifact-preservation-hold", hold_digest: env.record_digest,
              hold_state: rec.state, readback: verified(env) }); break;
          case "f01_record_deletion_evaluation":
            Object.assign(outcome, { operation: "evaluate-artifact-deletion", outcome: rec.decision,
              reason_id: rec.reason_id, evaluation_digest: env.record_digest, readback: verified(env),
              hold_inventory: this.script.holds ?? [],
              surviving_derivatives: this.script.policy?.retention_registry.classes
                .find(c => c.artifact_class === rec.artifact_class)?.surviving_derivatives ?? [] }); break;
        }
        this.lastOutcome = outcome;
        return { rows: [{ outcome }] };
      }
    }
    throw new Error(`unscripted statement: ${text}`);
  }

  /** The parameters the last security-definer writer was called with. */
  write() {
    assert.ok(this.lastWrite, "expected a writer call");
    return this.lastWrite;
  }

  param(index) { return this.write().params[index]; }
  json(index) {
    const value = this.param(index);
    return value === null || value === undefined ? null : JSON.parse(value);
  }
}

function storeWith(script = {}) {
  const db = new FakeDb(script);
  return { db, store: createRecordSourceAuthorityStore({ db }) };
}

function policyScript(extra = {}) {
  return { policy: STORED_POLICY, ...extra };
}

async function refuses(promise, code) {
  await assert.rejects(promise, error => {
    assert.ok(error instanceof V5F01StoreError || error instanceof V5F01Error,
      `expected a store or kernel error, got ${error?.name}: ${error?.message}`);
    assert.equal(error.code, code, `expected code ${code}, got ${error.code}`);
    return true;
  });
}

// ===========================================================================
// The registration seam.
// ===========================================================================

test("the eight named operations are exactly the proposed tool surface", () => {
  assert.deepEqual([...V5_F01_OPERATIONS].sort(), [
    "evaluate-artifact-deletion",
    "read-record-source-authority",
    "record-artifact-preservation-hold",
    "record-corporate-artifact",
    "record-document-identity",
    "record-parsed-proposal",
    "record-source-observation",
    "register-record-source-authority-policy",
  ]);
  const registrations = v5F01ToolRegistrations();
  assert.equal(registrations.length, 8);
  for (const entry of registrations) {
    assert.ok(V5_F01_OPERATIONS.includes(entry.name));
    assert.ok(entry.role.length > 40, entry.name);
    // The parent still owns all four. Naming them false here is the point.
    assert.equal(entry.registered_in_scac, false);
    assert.equal(entry.registered_in_mutation_registry, false);
    assert.equal(entry.migration_bound, false);
    assert.equal(entry.accepted, false);
  }
});

test("only the policy and hold tools are humanOnly plus authorityOnly", () => {
  const schemas = v5F01StoreOperationSchemas();
  const authority = V5_F01_OPERATIONS.filter(op => schemas[op].authorityOnly);
  assert.deepEqual(authority.sort(),
    ["record-artifact-preservation-hold", "register-record-source-authority-policy"]);
  for (const op of authority) assert.equal(schemas[op].humanOnly, true, op);
  assert.equal(schemas["read-record-source-authority"].write, false);
  for (const op of V5_F01_OPERATIONS) {
    if (op === "read-record-source-authority") continue;
    assert.equal(schemas[op].write, true, op);
    assert.ok(schemas[op].required.includes("idempotency_key"), op);
  }
});

test("every write schema is closed and names no derived field", () => {
  const schemas = v5F01StoreOperationSchemas();
  for (const op of V5_F01_OPERATIONS) {
    for (const key of schemas[op].keys) {
      assert.ok(!V5_F01_DERIVED_ONLY_FIELDS.includes(key.toLowerCase()),
        `${op}.${key} collides with a derived-only field`);
    }
  }
  assert.ok(V5_F01_DERIVED_ONLY_FIELDS.includes("tenant"));
  assert.ok(V5_F01_DERIVED_ONLY_FIELDS.includes("now"));
  assert.ok(V5_F01_DERIVED_ONLY_FIELDS.includes("current_state"));
  assert.ok(V5_F01_DERIVED_ONLY_FIELDS.includes("prior_artifact"));
  assert.ok(V5_F01_DERIVED_ONLY_FIELDS.includes("prior_link"));
  assert.ok(V5_F01_DERIVED_ONLY_FIELDS.includes("holds"));
  assert.ok(V5_F01_DERIVED_ONLY_FIELDS.includes("created_at"));
});

// ===========================================================================
// Caller authority and derived-value injection.
// ===========================================================================

test("a caller cannot supply tenant, now, actor or an evaluated result", async () => {
  const { store } = storeWith(policyScript());
  const base = {
    idempotency_key: "syn-observation-0001",
    observation: {
      entity: "deal", field: "next_step", source_system: "neon_record_layer",
      value_digest: VALUE_A, version: 1, observed_at: T.mid,
      provenance: { adapter_kind: "synthetic_test_adapter",
        evidence_ref: "synthetic-evidence-0001", retrieval_class: "corporate_record_export" },
    },
  };
  for (const [key, value, code] of [
    ["tenant", ORGANIZATION_TENANT_ID, "caller_derived_field_refused"],
    ["now", SERVER_NOW, "caller_derived_field_refused"],
    ["current_state", {}, "caller_derived_field_refused"],
    ["decision", "accept", "caller_derived_field_refused"],
    ["policy_digest", POLICY_DIGEST, "caller_derived_field_refused"],
    ["actor", JOE, "caller_authority_field_refused"],
    ["authorized_by", "joe", "caller_authority_field_refused"],
    ["override", true, "caller_authority_field_refused"],
  ]) {
    await refuses(store.recordSourceObservation({ ...base, [key]: value }, ctx(JOE)), code);
  }
  await refuses(store.recordSourceObservation({ ...base, surprise: 1 }, ctx(JOE)), "unknown_field");
});

test("the observation payload itself refuses tenant, actor and prior-state injection", async () => {
  const { store } = storeWith(policyScript());
  const call = extra => store.recordSourceObservation({
    idempotency_key: "syn-observation-0002",
    observation: {
      entity: "deal", field: "next_step", source_system: "neon_record_layer",
      value_digest: VALUE_A, version: 1, observed_at: T.mid,
      provenance: { adapter_kind: "synthetic_test_adapter",
        evidence_ref: "synthetic-evidence-0002", retrieval_class: "corporate_record_export" },
      ...extra,
    },
  }, ctx(JOE));
  await refuses(call({ tenant: ORGANIZATION_TENANT_ID }), "caller_derived_field_refused");
  await refuses(call({ event_seq: 9 }), "caller_derived_field_refused");
  await refuses(call({ last_event_digest: D(9) }), "caller_derived_field_refused");
  await refuses(call({ owner_source: "salesforce" }), "caller_derived_field_refused");
  await refuses(call({ acting_as: "joe" }), "caller_authority_field_refused");
});

test("an accessor, a symbol key or an own __proto__ refuses rather than being read", async () => {
  const { store } = storeWith(policyScript());
  const withAccessor = { idempotency_key: "syn-accessor-0001" };
  Object.defineProperty(withAccessor, "observation", { get: () => ({}), enumerable: true });
  await refuses(store.recordSourceObservation(withAccessor, ctx(JOE)), "accessor_property_refused");

  const withSymbol = { idempotency_key: "syn-symbol-0001", observation: {} };
  withSymbol[Symbol("hidden")] = 1;
  await refuses(store.recordSourceObservation(withSymbol, ctx(JOE)), "symbol_key_refused");

  const withProto = JSON.parse('{"idempotency_key":"syn-proto-0001","__proto__":{"x":1}}');
  await refuses(store.recordSourceObservation(withProto, ctx(JOE)), "prototype_key_refused");
});

test("an unauthenticated or unknown actor refuses before any statement runs", async () => {
  const { db, store } = storeWith(policyScript());
  await refuses(store.readRecordSourceAuthority(
    { selector: { kind: "current_policy" } }, { actor: { slug: "nobody" } }), "unauthenticated_actor");
  await refuses(store.readRecordSourceAuthority(
    { selector: { kind: "current_policy" } }, {}), "missing_field");
  assert.equal(db.calls.length, 0, "no statement may run for an unauthenticated request");
});

test("humanOnly plus authorityOnly refuses an ordinary evidence writer before any write", async () => {
  const { db, store } = storeWith(policyScript());
  await refuses(store.registerRecordSourceAuthorityPolicy({
    idempotency_key: "syn-policy-0001",
    field_registry: FIELD_POLICY, retention_registry: RETENTION_POLICY,
    field_registry_digest: FIELD_REGISTRY.registry_digest,
    retention_registry_digest: RETENTION_REGISTRY.registry_digest,
  }, ctx(AGENT)), "human_only_operation_refused");
  await refuses(store.recordArtifactPreservationHold({
    idempotency_key: "syn-hold-0001",
    hold: { hold_id: "synthetic-hold-0001", artifact_digest: D(7), state: "active" },
  }, ctx(AGENT)), "human_only_operation_refused");
  assert.equal(db.calls.length, 0, "an authorityOnly refusal happens before the transaction");
});

test("a database-derived actor that differs from the handler's refuses the write", async () => {
  const { db, store } = storeWith(policyScript({
    principal: { actor_slug: "dell", human: true, authorization_class: "verified_partner" },
  }));
  await refuses(store.recordSourceObservation({
    idempotency_key: "syn-observation-0003",
    observation: {
      entity: "deal", field: "next_step", source_system: "neon_record_layer",
      value_digest: VALUE_A, version: 1, observed_at: T.mid,
      provenance: { adapter_kind: "synthetic_test_adapter",
        evidence_ref: "synthetic-evidence-0003", retrieval_class: "corporate_record_export" },
    },
  }, ctx(JOE)), "actor_context_mismatch");
  assert.equal(db.rolledBack, 1, "the transaction is rolled back, not left open");
  assert.equal(db.committed, 0);
});

// ===========================================================================
// register-record-source-authority-policy.
// ===========================================================================

test("a policy version installs with exact digests and a null prior on genesis", async () => {
  const { db, store } = storeWith({ policy: null, outcome: { outcome: "installed" } });
  const answer = await store.registerRecordSourceAuthorityPolicy({
    schema_version: V5_F01_STORE_SCHEMA_VERSION,
    idempotency_key: "syn-policy-genesis-0001",
    field_registry: FIELD_POLICY, retention_registry: RETENTION_POLICY,
    field_registry_digest: FIELD_REGISTRY.registry_digest,
    retention_registry_digest: RETENTION_REGISTRY.registry_digest,
  }, ctx(JOE));

  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "policy_version_installed");
  assert.equal(answer.entries_invented, 0);
  assert.equal(answer.prior_policy_digest, null);
  assert.equal(db.write().fn, "f01_install_policy");

  const envelope = db.json(0);
  assert.equal(envelope.record_kind, "stored_policy_version");
  assert.equal(envelope.tenant, ORGANIZATION_TENANT_ID);
  assert.equal(envelope.record_digest, digest(envelope.record));
  assert.equal(envelope.record.installed_by, "joe", "installed_by is derived, never supplied");
  assert.equal(envelope.record.installed_at, SERVER_NOW, "the instant comes from the server");
  assert.equal(envelope.record.field_registry_digest, digest(envelope.record.field_registry));
  assert.equal(envelope.record.retention_registry_digest,
    digest(envelope.record.retention_registry));
  assert.equal(envelope.record.domain_policy_digest, v5F01PolicyDigest());
  assert.equal(envelope.record.decision_subset_digest, v5F01DecisionSubsetDigest());
  assert.equal(db.param(1), null, "the CAS operand for genesis is null");
  assert.equal(db.param(2), "syn-policy-genesis-0001");
  assert.ok(/^sha256:[0-9a-f]{64}$/.test(db.param(3)), "the idempotency request digest travels");
});

test("a stated registry digest that is not the registry's own digest refuses", async () => {
  const { db, store } = storeWith({ policy: null });
  await refuses(store.registerRecordSourceAuthorityPolicy({
    idempotency_key: "syn-policy-0002",
    field_registry: FIELD_POLICY, retention_registry: RETENTION_POLICY,
    field_registry_digest: D(99),
    retention_registry_digest: RETENTION_REGISTRY.registry_digest,
  }, ctx(JOE)), "field_registry_digest_mismatch");
  await refuses(store.registerRecordSourceAuthorityPolicy({
    idempotency_key: "syn-policy-0003",
    field_registry: FIELD_POLICY, retention_registry: RETENTION_POLICY,
    field_registry_digest: FIELD_REGISTRY.registry_digest,
    retention_registry_digest: D(98),
  }, ctx(JOE)), "retention_registry_digest_mismatch");
  assert.equal(db.calls.length, 0, "the digest check precedes the transaction");
});

test("installing against a stale prior-current digest refuses before the write", async () => {
  const { db, store } = storeWith(policyScript());
  await refuses(store.registerRecordSourceAuthorityPolicy({
    idempotency_key: "syn-policy-0004",
    field_registry: FIELD_POLICY, retention_registry: RETENTION_POLICY,
    field_registry_digest: FIELD_REGISTRY.registry_digest,
    retention_registry_digest: RETENTION_REGISTRY.registry_digest,
    expected_prior_policy_digest: D(97),
  }, ctx(JOE)), "stale_policy_digest");
  assert.equal(db.lastWrite, undefined, "no writer runs on a stale CAS");
  assert.equal(db.rolledBack, 1);
});

test("a registry the kernel refuses never reaches the database", async () => {
  const { db, store } = storeWith({ policy: null });
  const contradictory = {
    ...FIELD_POLICY,
    entries: [{ ...FIELD_POLICY.entries[0], owner_source: "nobody_owns_this" }],
  };
  await assert.rejects(() => store.registerRecordSourceAuthorityPolicy({
    idempotency_key: "syn-policy-0005",
    field_registry: contradictory, retention_registry: RETENTION_POLICY,
    field_registry_digest: D(96), retention_registry_digest: D(95),
  }, ctx(JOE)), error => {
    assert.equal(error.code, "owner_source_not_permitted");
    return true;
  });
  assert.equal(db.calls.length, 0);
});

// ===========================================================================
// record-source-observation — the four separate records.
// ===========================================================================

const OWNER_OBSERVATION = Object.freeze({
  entity: "deal", field: "commission_amount", source_system: "salesforce",
  account: "synthetic-account-0001",
  native_identity: {
    source_system: "salesforce",
    native_id: "SYNTHETIC-NATIVE-0001",
    native_id_epoch: "synthetic-epoch-1",
  },
  value_digest: VALUE_A, version: 5, observed_at: T.mid,
  provenance: {
    adapter_kind: "synthetic_test_adapter",
    evidence_ref: "synthetic-evidence-0010",
    retrieval_class: "corporate_record_export",
  },
  taint_class: "corporate_source_of_record",
  readback: { confirmed: true, readback_at: T.late, readback_value_digest: VALUE_A },
});

function storedState(overrides = {}) {
  const record = storedFieldStateRecord({
    entity: "deal", field: "commission_amount",
    account: "synthetic-account-0001",
    native_identity: {
      source_system: "salesforce",
      native_id: "SYNTHETIC-NATIVE-0001",
      native_id_epoch: "synthetic-epoch-1",
    },
    value_digest: VALUE_B, version: 4, owner_source: "salesforce",
    authoritative_home: "salesforce",
    observed_at: T.early, event_seq: 7, last_event_digest: D(9),
    policy_digest: POLICY_DIGEST, updated_by: "joe", updated_at: SERVER_NOW,
    ...overrides,
  });
  return { state_digest: digest(record), current_state: record, integrity: "recomputed_from_committed_row" };
}

test("an accepted owner update writes state, transition, event and receipt and nothing else", async () => {
  const state = storedState();
  const { db, store } = storeWith(policyScript({ field_state: state }));
  const answer = await store.recordSourceObservation({
    idempotency_key: "syn-observation-accept-0001",
    observation: OWNER_OBSERVATION,
  }, ctx(JOE));

  assert.equal(answer.decision, "accept");
  assert.equal(answer.reason_id, "owner_value_updated");
  assert.equal(answer.silent_last_write_wins, false);
  assert.equal(answer.any_one_substitutes_for_another, false);
  assert.equal(db.write().fn, "f01_apply_observation");

  const [decision, entity, field, policyDigest, expectedState] = db.write().params;
  assert.equal(decision, "accept");
  assert.equal(entity, "deal");
  assert.equal(field, "commission_amount");
  assert.equal(policyDigest, POLICY_DIGEST, "the policy CAS operand is the stored digest");
  assert.equal(expectedState, state.state_digest, "the state CAS operand is the stored digest");

  const stateEnvelope = db.json(5);
  const transition = db.json(6);
  const event = db.json(7);
  const receipt = db.json(8);
  const reconciliation = db.json(9);

  assert.equal(reconciliation, null, "an accepted change is not a conflict");
  assert.equal(stateEnvelope.record_kind, "stored_field_state");
  assert.equal(transition.record_kind, "stored_state_transition");
  assert.equal(event.record_kind, "stored_source_event");
  assert.equal(receipt.record_kind, "stored_mutation_receipt");

  // The three domain records are DISTINCT and the receipt alone binds the other
  // two. That asymmetry is the checkable form of "none substitutes for another".
  const digests = new Set([transition.record_digest, event.record_digest, receipt.record_digest]);
  assert.equal(digests.size, 3);
  assert.equal(receipt.record.current_state_transition_digest, transition.record_digest);
  assert.equal(receipt.record.event_digest, event.record_digest);
  assert.equal(transition.record.mutation_receipt_digest, undefined);
  assert.equal(event.record.mutation_receipt_digest, undefined);
  assert.equal(receipt.record.actor, null, "the kernel records no actor");
  assert.equal(receipt.record.actor_derived_by, "authenticated_handler_context");

  // The event extends the stored chain rather than starting a new one.
  assert.equal(event.record.event_seq, 8);
  assert.equal(event.record.previous_event_digest, D(9));
  assert.equal(event.record.append_only, true);
  assert.equal(event.record.rewrites_prior_event, false);

  // Current state is bound to both.
  assert.equal(stateEnvelope.record.event_seq, 8);
  assert.equal(stateEnvelope.record.last_event_digest, event.record_digest);
  assert.equal(stateEnvelope.record.value_digest, transition.record.to_value_digest);
  assert.equal(stateEnvelope.record.updated_by, "joe");
  assert.equal(stateEnvelope.record.updated_at, SERVER_NOW);
});

test("a first observation by the owner establishes the field with a null prior chain", async () => {
  const { db, store } = storeWith(policyScript({ field_state: null }));
  const answer = await store.recordSourceObservation({
    idempotency_key: "syn-observation-establish-0001",
    observation: OWNER_OBSERVATION,
  }, ctx(JOE));
  assert.equal(answer.decision, "accept");
  assert.equal(answer.reason_id, "field_established");
  assert.equal(db.param(4), null, "no established state means a null CAS operand");
  const event = db.json(7);
  assert.equal(event.record.event_seq, 1);
  assert.equal(event.record.previous_event_digest, null);
  assert.equal(event.record.event_kind, "source_field_established");
  assert.equal(db.json(6).record.from_value_digest, null);
});

test("an equal-version contradiction writes the reconciliation item and no mutation record", async () => {
  const state = storedState({ version: 5, value_digest: VALUE_B });
  const { db, store } = storeWith(policyScript({ field_state: state }));
  const answer = await store.recordSourceObservation({
    idempotency_key: "syn-observation-conflict-0001",
    observation: OWNER_OBSERVATION,
  }, ctx(JOE));

  assert.equal(answer.decision, "reconcile");
  assert.equal(answer.reason_id, "equal_version_contradiction");
  assert.equal(answer.conflict_kind, "equal_version_contradiction");
  assert.equal(db.json(5), null, "no current state is written");
  assert.equal(db.json(6), null, "no transition is written");
  assert.equal(db.json(7), null, "no event is written");
  assert.equal(db.json(8), null, "no receipt is written");
  const item = db.json(9);
  assert.equal(item.record_kind, "stored_reconciliation_item");
  assert.equal(item.record.visible, true);
  assert.equal(item.record.applied, false);
  assert.equal(item.record.resolved_by_machine, false);
  assert.equal(item.record.human_resolver_class, "deal_owner");
});

test("a stale observation refuses and writes no record at all", async () => {
  const state = storedState({ version: 9 });
  const { db, store } = storeWith(policyScript({ field_state: state }));
  const answer = await store.recordSourceObservation({
    idempotency_key: "syn-observation-stale-0001",
    observation: OWNER_OBSERVATION,
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "stale_observation_refused");
  assert.equal(db.param(0), "refuse");
  for (const index of [5, 6, 7, 8, 9]) assert.equal(db.json(index), null);
});

test("an observation is judged against the LOADED state, never a caller's picture of it", async () => {
  const state = storedState();
  const { db, store } = storeWith(policyScript({ field_state: state }));
  await store.recordSourceObservation({
    idempotency_key: "syn-observation-loaded-0001",
    observation: OWNER_OBSERVATION,
  }, ctx(JOE));
  const loadCall = db.calls.find(c => c.text.includes("ops.f01_current_field_state("));
  assert.ok(loadCall, "the store loads the current state from the database");
  assert.deepEqual(loadCall.params, ["deal", "commission_amount"]);
});

test("an observation with no installed policy refuses rather than inventing a registry", async () => {
  const { db, store } = storeWith({ policy: null });
  await refuses(store.recordSourceObservation({
    idempotency_key: "syn-observation-nopolicy-0001",
    observation: OWNER_OBSERVATION,
  }, ctx(JOE)), "no_installed_policy");
  assert.equal(db.lastWrite, undefined);
});

test("a stored registry that no longer hashes to its digest refuses instead of being repaired", async () => {
  const tampered = {
    ...STORED_POLICY,
    field_registry: { ...FIELD_PREIMAGE, registry_version: 99 },
  };
  const { store } = storeWith({ policy: tampered });
  await refuses(store.recordSourceObservation({
    idempotency_key: "syn-observation-corrupt-0001",
    observation: OWNER_OBSERVATION,
  }, ctx(JOE)), "corrupt_stored_registry");
});

// ===========================================================================
// record-corporate-artifact.
// ===========================================================================

const ARTIFACT = Object.freeze({
  source_system: "salesforce",
  source_class: "synthetic_test_object",
  source_account: "synthetic-account-0001",
  native_identity: {
    source_system: "salesforce",
    native_id: "SYNTHETIC-ARTIFACT-0001",
    native_id_epoch: "synthetic-epoch-1",
  },
  native_version: "synthetic-version-1",
  content_digest: VALUE_A,
  byte_length: 1024,
  observed_at: T.mid,
  provenance: {
    adapter_kind: "synthetic_test_adapter",
    evidence_ref: "synthetic-evidence-0020",
    retrieval_class: "corporate_record_export",
  },
  evidence_class: "corporate_record_export",
  declared_data_classes: ["lease_economics"],
  taint_class: "corporate_source_of_record",
});

test("an artifact is admitted as immutable evidence and never as a fact", async () => {
  const { db, store } = storeWith({ prior_artifact: null, outcome: { outcome: "recorded" } });
  const answer = await store.recordCorporateArtifact({
    idempotency_key: "syn-artifact-0001", artifact: ARTIFACT,
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.is_fact, false);
  assert.equal(answer.makes_field_authoritative, false);
  assert.equal(answer.immutable, true);
  const envelope = db.json(0);
  assert.equal(envelope.record_kind, "stored_corporate_artifact");
  assert.equal(envelope.record_digest, digest(envelope.record));
  assert.equal(envelope.is_fact, false);
  assert.equal(answer.artifact_digest, envelope.record_digest);
});

test("the prior artifact is LOADED by identity and a caller's copy is refused by name", async () => {
  const priorRecord = { ...ARTIFACT, content_digest: VALUE_B, tenant: ORGANIZATION_TENANT_ID };
  const { db, store } = storeWith({ prior_artifact: { artifact: priorRecord } });
  const answer = await store.recordCorporateArtifact({
    idempotency_key: "syn-artifact-0002", artifact: ARTIFACT,
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "artifact_identity_conflict");
  assert.equal(db.lastWrite, undefined, "a conflicting identity never reaches the writer");

  const loadCall = db.calls.find(c => c.text.includes("ops.f01_stored_artifact_by_identity("));
  assert.deepEqual(loadCall.params, ["salesforce", "synthetic-account-0001",
    "SYNTHETIC-ARTIFACT-0001", "synthetic-epoch-1", "synthetic-version-1"]);

  await refuses(store.recordCorporateArtifact({
    idempotency_key: "syn-artifact-0003", artifact: ARTIFACT, prior_artifact: priorRecord,
  }, ctx(JOE)), "caller_derived_field_refused");
});

test("Tour-only evidence is refused as a generic corporate source", async () => {
  const { db, store } = storeWith({ prior_artifact: null });
  const answer = await store.recordCorporateArtifact({
    idempotency_key: "syn-artifact-tour-0001",
    artifact: { ...ARTIFACT, evidence_class: "tour_rights_receipt" },
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "tour_only_evidence_not_generic_authority");
  assert.equal(db.lastWrite, undefined);
});

test("an artifact observed after server time refuses", async () => {
  const { db, store } = storeWith({ prior_artifact: null });
  const answer = await store.recordCorporateArtifact({
    idempotency_key: "syn-artifact-future-0001",
    artifact: { ...ARTIFACT, observed_at: T.future },
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "observation_after_now");
  assert.equal(db.lastWrite, undefined);
});

// ===========================================================================
// record-parsed-proposal.
// ===========================================================================

const PROPOSAL = Object.freeze({
  artifact_digest: D(21),
  source_system: "salesforce",
  source_account: "synthetic-account-0001",
  proposed_bindings: [
    { entity: "deal", field: "commission_amount", value_digest: VALUE_A, version: 6 },
  ],
  confidence: 0.75,
  evidence_refs: ["synthetic-evidence-0030"],
  observed_at: T.mid,
});

test("a proposal is stored as reviewable and never as a fact", async () => {
  const { db, store } = storeWith(policyScript({
    stored_artifact: { artifact_digest: D(21), artifact: {}, created_at: T.early },
    outcome: { outcome: "recorded" },
  }));
  const answer = await store.recordParsedProposal({
    idempotency_key: "syn-proposal-0001", proposal: PROPOSAL,
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.becomes_fact, false);
  assert.equal(answer.advances_state, false);
  assert.equal(answer.carries_effect_authority, false);
  assert.equal(answer.requires_human_review, true);

  const proposalEnvelope = db.json(0);
  const linkEnvelope = db.json(1);
  assert.equal(proposalEnvelope.record_kind, "stored_parsed_proposal");
  assert.equal(linkEnvelope.record_kind, "stored_proposal_link");
  assert.equal(proposalEnvelope.record.schema_version, V5_F01_PROPOSAL_SCHEMA_VERSION);
  assert.equal(linkEnvelope.record.reversible, true);
  assert.equal(linkEnvelope.record.history_preserved, true);
  assert.notEqual(proposalEnvelope.record_digest, linkEnvelope.record_digest,
    "the proposal and its link are two records, not one");
});

test("a proposal naming an artifact the database does not hold refuses", async () => {
  const { db, store } = storeWith(policyScript({ stored_artifact: null }));
  const answer = await store.recordParsedProposal({
    idempotency_key: "syn-proposal-0002", proposal: PROPOSAL,
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "unknown_artifact_reference");
  assert.equal(db.lastWrite, undefined, "a proposal cannot assert an artifact into existence");
});

test("a proposal that reaches past review into effect refuses by name", async () => {
  const { store } = storeWith(policyScript({
    stored_artifact: { artifact_digest: D(21), artifact: {}, created_at: T.early },
  }));
  await refuses(store.recordParsedProposal({
    idempotency_key: "syn-proposal-0003",
    proposal: { ...PROPOSAL, apply_immediately: true },
  }, ctx(JOE)), "unknown_field");
});

// ===========================================================================
// record-document-identity.
// ===========================================================================

const DOCUMENT = Object.freeze({
  document_class: "synthetic_test_agreement",
  neon_identity: {
    document_id: "synthetic-document-0001", content_digest: VALUE_A, version_no: 1,
  },
  object_storage_identity: {
    object_key: "synthetic/test/object-0001", content_digest: VALUE_A,
    byte_length: 2048, sealed: true,
  },
  onedrive_identity: {
    drive_id: "synthetic-drive-0001", item_id: "synthetic-item-0001",
    content_digest: VALUE_A, filing_state: "filed",
  },
  preparation_state: "approved_for_delivery",
  delivery_state: "delivered",
  signature_state: "fully_executed",
  validity_state: "effective",
  version_state: "current",
});

test("a coherent, fully filed document round-trips every axis and identity", async () => {
  const { db, store } = storeWith({ document_current: null, outcome: { outcome: "recorded" } });
  const answer = await store.recordDocumentIdentity({
    idempotency_key: "syn-document-0001", document: DOCUMENT,
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.official_filing_state, "filed");
  assert.equal(answer.object_storage_success_implies_official_filing, false);
  assert.equal(answer.neon_success_implies_official_filing, false);

  const envelope = db.json(0);
  const record = envelope.record;
  assert.equal(record.preparation_state, "approved_for_delivery");
  assert.equal(record.delivery_state, "delivered");
  assert.equal(record.signature_state, "fully_executed");
  assert.equal(record.validity_state, "effective");
  assert.equal(record.version_state, "current");
  assert.deepEqual(record.neon_identity, DOCUMENT.neon_identity);
  assert.deepEqual(record.object_storage_identity, DOCUMENT.object_storage_identity);
  assert.deepEqual(record.onedrive_identity, DOCUMENT.onedrive_identity);
  assert.equal(record.homes.official_executed_copy, "onedrive");
  assert.equal(record.homes.working_and_sealed_bytes, "object_storage");
  assert.equal(record.homes.identity_and_state, "neon_record_layer");
  assert.equal(envelope.record_digest, digest(record));
});

test("full execution without a filed OneDrive copy is STORED as visibly incomplete", async () => {
  for (const onedrive of [
    null,
    { drive_id: "synthetic-drive-0001", item_id: "synthetic-item-0001",
      content_digest: VALUE_A, filing_state: "pending" },
    { drive_id: "synthetic-drive-0001", item_id: "synthetic-item-0001",
      content_digest: VALUE_A, filing_state: "failed" },
  ]) {
    const { db, store } = storeWith({ document_current: null, outcome: { outcome: "recorded" } });
    const answer = await store.recordDocumentIdentity({
      idempotency_key: `syn-document-incomplete-${onedrive?.filing_state ?? "absent"}`,
      document: { ...DOCUMENT, onedrive_identity: onedrive },
    }, ctx(JOE));
    assert.equal(answer.decision, "refuse");
    assert.equal(answer.reason_id, "incomplete_official_filing");
    assert.equal(answer.official_filing_state, "incomplete_official_filing");
    // The incompleteness is a RECORD, not an absence. An object-storage success
    // beside it must never read as an official filing.
    assert.equal(db.write().fn, "f01_record_document");
    assert.equal(db.json(0).record.official_filing_state, "incomplete_official_filing");
    assert.equal(db.json(0).object_storage_success_implies_official_filing, false);
  }
});

test("an incoherent document writes nothing at all", async () => {
  const { db, store } = storeWith({ document_current: null });
  const answer = await store.recordDocumentIdentity({
    idempotency_key: "syn-document-incoherent-0001",
    document: { ...DOCUMENT, delivery_state: "undelivered" },
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "document_state_incoherent");
  assert.equal(answer.violated_constraint, "signature_requires_delivery");
  assert.equal(db.calls.length, 0, "an incoherent document never opens a transaction");
});

test("a sealed-bytes digest that disagrees with Neon refuses rather than picking a winner", async () => {
  const { db, store } = storeWith({ document_current: null });
  const answer = await store.recordDocumentIdentity({
    idempotency_key: "syn-document-sealed-0001",
    document: {
      ...DOCUMENT,
      object_storage_identity: { ...DOCUMENT.object_storage_identity, content_digest: VALUE_B },
    },
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "sealed_bytes_digest_mismatch");
  assert.equal(db.calls.length, 0);
});

test("a document write against a stale current version refuses", async () => {
  const { db, store } = storeWith({ document_current: { record_digest: D(31) } });
  await refuses(store.recordDocumentIdentity({
    idempotency_key: "syn-document-stale-0001",
    document: DOCUMENT,
    expected_prior_document_digest: D(32),
  }, ctx(JOE)), "stale_document_digest");
  assert.equal(db.lastWrite, undefined);
});

// ===========================================================================
// record-artifact-preservation-hold.
// ===========================================================================

test("a hold is appended, never edited, and never deletes the artifact", async () => {
  const { db, store } = storeWith({
    stored_artifact: { artifact_digest: D(41), artifact: {}, created_at: T.early },
    hold_history: [], outcome: { outcome: "appended" },
  });
  const answer = await store.recordArtifactPreservationHold({
    idempotency_key: "syn-hold-active-0001",
    hold: {
      hold_id: "synthetic-hold-0001", artifact_digest: D(41),
      state: "active", reason: "synthetic test hold",
    },
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.deletes_artifact, false);
  assert.equal(answer.append_only, true);
  const envelope = db.json(0);
  assert.equal(envelope.record_kind, "stored_preservation_hold");
  assert.equal(envelope.deletes_artifact, false);
  assert.equal(envelope.record.state, "active");
  assert.equal(envelope.record.placed_at, SERVER_NOW, "placed_at comes from the server");
  assert.equal(envelope.record.released_at, null);
  assert.equal(envelope.record.prior_hold_digest, null);
  assert.equal(envelope.record.recorded_by, "joe");
});

test("a release carries the stored placed_at forward and names the state it replaces", async () => {
  const priorRecord = storedHoldRecord({
    hold_id: "synthetic-hold-0001", artifact_digest: D(41), state: "active",
    reason: "synthetic test hold", placed_at: T.early, released_at: null,
    prior_hold_digest: null, recorded_by: "joe", recorded_at: T.early,
  });
  const priorDigest = digest(priorRecord);
  const { db, store } = storeWith({
    stored_artifact: { artifact_digest: D(41), artifact: {}, created_at: T.early },
    hold_history: [{ record: priorRecord, record_digest: priorDigest }],
    outcome: { outcome: "appended" },
  });
  const answer = await store.recordArtifactPreservationHold({
    idempotency_key: "syn-hold-release-0001",
    hold: { hold_id: "synthetic-hold-0001", artifact_digest: D(41), state: "released" },
    expected_prior_hold_digest: priorDigest,
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  const record = db.json(0).record;
  assert.equal(record.state, "released");
  assert.equal(record.placed_at, T.early, "placed_at is loaded, not restated by the releaser");
  assert.equal(record.released_at, SERVER_NOW);
  assert.equal(record.prior_hold_digest, priorDigest);
  assert.equal(db.param(1), priorDigest, "the CAS operand travels to the database");
});

test("a hold append against a stale prior state refuses", async () => {
  const { db, store } = storeWith({
    stored_artifact: { artifact_digest: D(41), artifact: {}, created_at: T.early },
    hold_history: [{ record: { artifact_digest: D(41) }, record_digest: D(42) }],
  });
  await refuses(store.recordArtifactPreservationHold({
    idempotency_key: "syn-hold-stale-0001",
    hold: { hold_id: "synthetic-hold-0001", artifact_digest: D(41), state: "released" },
    expected_prior_hold_digest: D(43),
  }, ctx(JOE)), "stale_hold_digest");
  assert.equal(db.lastWrite, undefined);
});

test("a hold on an artifact the database does not hold refuses", async () => {
  const { db, store } = storeWith({ stored_artifact: null });
  const answer = await store.recordArtifactPreservationHold({
    idempotency_key: "syn-hold-unknown-0001",
    hold: { hold_id: "synthetic-hold-0002", artifact_digest: D(44), state: "active" },
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "unknown_artifact_reference");
  assert.equal(db.lastWrite, undefined);
});

test("all four hold states are writable and an unregistered one refuses", async () => {
  for (const state of ["active", "released", "expired", "unknown"]) {
    const { db, store } = storeWith({
      stored_artifact: { artifact_digest: D(41), artifact: {}, created_at: T.early },
      hold_history: [], outcome: { outcome: "appended" },
    });
    const answer = await store.recordArtifactPreservationHold({
      idempotency_key: `syn-hold-${state}-0001`,
      hold: { hold_id: `synthetic-hold-${state}`, artifact_digest: D(41), state },
    }, ctx(JOE));
    assert.equal(answer.decision, "allow", state);
    assert.equal(db.json(0).record.state, state);
  }
  const { store } = storeWith({
    stored_artifact: { artifact_digest: D(41), artifact: {}, created_at: T.early },
  });
  await refuses(store.recordArtifactPreservationHold({
    idempotency_key: "syn-hold-bogus-0001",
    hold: { hold_id: "synthetic-hold-bogus", artifact_digest: D(41), state: "lifted" },
  }, ctx(JOE)), "unknown_hold_state");
});

// ===========================================================================
// evaluate-artifact-deletion.
// ===========================================================================

const DELETION_SUBJECT = Object.freeze({
  artifact_class: "synthetic_test_lease",
  artifact_home: "onedrive",
  artifact_digest: D(51),
  satisfied_constraints: ["synthetic_test_constraint"],
  deletion_proof: {
    proof_ref: "synthetic-proof-0001",
    artifact_digest: D(51),
    proof_digest: D(52),
    executed_at: T.late,
  },
});

function deletionScript(holds) {
  return policyScript({
    stored_artifact: { artifact_digest: D(51), artifact: {}, created_at: T.early },
    holds, holds_digest: digest(holds),
    stored_derivatives: ["synthetic_test_abstract"],
    outcome: { outcome: "allow" },
  });
}

test("a permitted deletion persists an evaluation and performs no deletion", async () => {
  const { db, store } = storeWith(deletionScript([]));
  const answer = await store.evaluateArtifactDeletion({
    idempotency_key: "syn-deletion-allow-0001", subject: DELETION_SUBJECT,
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "deletion_permitted");
  assert.equal(answer.deletion_performed, false);
  assert.equal(answer.silent_purge, false);
  assert.equal(answer.bytes_deleted, false);
  assert.equal(answer.external_purge_performed, false);

  const envelope = db.json(0);
  assert.equal(envelope.record_kind, "stored_deletion_evaluation");
  assert.equal(envelope.bytes_deleted, false);
  assert.equal(envelope.external_purge_performed, false);
  assert.ok(envelope.record.deletion_receipt, "an allow carries a receipt");
  assert.deepEqual(envelope.record.deletion_receipt.surviving_derivatives,
    ["synthetic_test_abstract"]);
  assert.equal(db.param(1), digest([]), "the loaded hold-inventory digest travels as a CAS operand");
});

test("holds are LOADED and an active one blocks; a caller cannot supply its own", async () => {
  const holds = [{ hold_id: "synthetic-hold-0001", state: "active", placed_at: T.early,
    released_at: null }];
  const { db, store } = storeWith(deletionScript(holds));
  const answer = await store.evaluateArtifactDeletion({
    idempotency_key: "syn-deletion-hold-0001", subject: DELETION_SUBJECT,
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "active_hold_blocks_deletion");
  assert.deepEqual(answer.blocking_holds, ["synthetic-hold-0001"]);
  assert.equal(db.json(0).record.deletion_receipt, null, "a refusal carries no receipt");

  await refuses(store.evaluateArtifactDeletion({
    idempotency_key: "syn-deletion-hold-0002",
    subject: { ...DELETION_SUBJECT, holds: [] },
  }, ctx(JOE)), "caller_derived_field_refused");
  await refuses(store.evaluateArtifactDeletion({
    idempotency_key: "syn-deletion-hold-0003",
    subject: { ...DELETION_SUBJECT, created_at: T.early },
  }, ctx(JOE)), "caller_derived_field_refused");
});

test("an unknown hold state blocks exactly as hard as an active one", async () => {
  const holds = [{ hold_id: "synthetic-hold-0009", state: "unknown", placed_at: T.early,
    released_at: null }];
  const { store } = storeWith(deletionScript(holds));
  const answer = await store.evaluateArtifactDeletion({
    idempotency_key: "syn-deletion-unknown-0001", subject: DELETION_SUBJECT,
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "unknown_hold_state_blocks_deletion");
});

test("an omitted derivative inventory refuses rather than reading as verified-empty", async () => {
  const { store } = storeWith({ ...deletionScript([]), stored_derivatives: null });
  const withoutDerivatives = DELETION_SUBJECT;
  const answer = await store.evaluateArtifactDeletion({
    idempotency_key: "syn-deletion-derivatives-0001", subject: withoutDerivatives,
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "derivative_inventory_missing");
});

test("a deletion evaluation for an artifact the database does not hold refuses", async () => {
  const { db, store } = storeWith(policyScript({ stored_artifact: null }));
  const answer = await store.evaluateArtifactDeletion({
    idempotency_key: "syn-deletion-unknown-artifact-0001", subject: DELETION_SUBJECT,
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "unknown_artifact_reference");
  assert.equal(db.lastWrite, undefined);
});

// ===========================================================================
// read-record-source-authority.
// ===========================================================================

test("every registered read kind reaches the recomputing read function", async () => {
  for (const kind of V5_F01_READ_KINDS) {
    const { db, store } = storeWith({ read_body: { kind, body: null } });
    const answer = await store.readRecordSourceAuthority({ selector: { kind } }, ctx(JOE));
    assert.equal(answer.decision, "allow", kind);
    assert.equal(answer.integrity, "recomputed_not_trusted", kind);
    assert.equal(answer.stale_fallback_permitted, false, kind);
    const readCall = db.calls.find(c => c.text.includes("ops.f01_read("));
    assert.equal(readCall.params[0], kind);
  }
});

test("an unregistered read kind refuses and a read writes nothing", async () => {
  const { db, store } = storeWith({});
  await refuses(store.readRecordSourceAuthority(
    { selector: { kind: "everything" } }, ctx(JOE)), "unknown_read_kind");
  await refuses(store.readRecordSourceAuthority(
    { selector: { kind: "current_policy", tenant: ORGANIZATION_TENANT_ID } }, ctx(JOE)),
    "caller_derived_field_refused");
  assert.equal(db.lastWrite, undefined);
});

// ===========================================================================
// Idempotency binding.
// ===========================================================================

test("the idempotency request digest is bound to the operation, actor and exact payload", async () => {
  const payload = {
    idempotency_key: "syn-idempotency-0001",
    observation: { ...OWNER_OBSERVATION },
  };
  const first = storeWith(policyScript({ field_state: storedState() }));
  await first.store.recordSourceObservation(payload, ctx(JOE));
  const firstDigest = first.db.param(11);

  const second = storeWith(policyScript({ field_state: storedState() }));
  await second.store.recordSourceObservation(payload, ctx(JOE));
  assert.equal(second.db.param(11), firstDigest, "the same payload replays to the same digest");

  const third = storeWith(policyScript({ field_state: storedState() }));
  await third.store.recordSourceObservation({
    ...payload,
    observation: { ...OWNER_OBSERVATION, value_digest: VALUE_B,
      readback: { confirmed: true, readback_at: T.late, readback_value_digest: VALUE_B } },
  }, ctx(JOE));
  assert.notEqual(third.db.param(11), firstDigest,
    "a different payload under the same key must not reuse the binding");
});

test("a malformed or missing idempotency key refuses before any statement", async () => {
  const { db, store } = storeWith(policyScript());
  await refuses(store.recordSourceObservation(
    { observation: OWNER_OBSERVATION }, ctx(JOE)), "missing_field");
  await refuses(store.recordSourceObservation(
    { idempotency_key: "not a key", observation: OWNER_OBSERVATION }, ctx(JOE)),
    "invalid_idempotency_key");
  await refuses(store.recordSourceObservation(
    { idempotency_key: "x".repeat(201), observation: OWNER_OBSERVATION }, ctx(JOE)),
    "invalid_idempotency_key");
  assert.equal(db.calls.length, 0);
});

// ===========================================================================
// Canonical bytes — the exact preimages the SQL fixtures compare against.
// ===========================================================================

test("an envelope hashes to its own record digest and to stable canonical bytes", () => {
  const record = storedHoldRecord({
    hold_id: "synthetic-hold-0001", artifact_digest: D(41), state: "active",
    reason: "synthetic test hold", placed_at: T.early, released_at: null,
    prior_hold_digest: null, recorded_by: "joe", recorded_at: SERVER_NOW,
  });
  const envelope = v5F01StoreEnvelope("stored_preservation_hold", record, {
    deletes_artifact: false,
  });
  assert.equal(envelope.record_digest, digest(record));
  assert.equal(digest(envelope), digest(JSON.parse(v5F01EnvelopeCanonicalBytes(envelope))));
  assert.equal(envelope.schema_version, V5_F01_ENVELOPE_SCHEMA_VERSION);
  assert.equal(envelope.tenant, ORGANIZATION_TENANT_ID);
});

test("an unregistered record kind cannot be enveloped", () => {
  assert.throws(() => v5F01StoreEnvelope("stored_whatever", {}), error => {
    assert.equal(error.code, "unknown_record_kind");
    return true;
  });
  assert.equal(V5_F01_STORE_RECORD_KINDS.length, 12);
});

// The bytes below are the JS side of the byte-for-byte comparison the SQL
// fixtures make against ops.f01_canonical_json. They are asserted here so a
// change to either side is a failure in BOTH suites rather than a silent drift.
test("canonical bytes match the documented shape for Unicode and timestamp edges", () => {
  const cases = [
    [{ a: 1, b: "x" }, '{"a":1,"b":"x"}'],
    [{ b: 1, a: 2 }, '{"a":2,"b":1}'],
    [{ "é": "café" }, '{"é":"café"}'],
    [{ k: "line\nbreak" }, '{"k":"line\\nbreak"}'],
    [{ k: "quote\"and\\slash" }, '{"k":"quote\\"and\\\\slash"}'],
    [{ k: "" }, '{"k":""}'],
    [{ k: "\u{1f5c2}" }, '{"k":"\u{1f5c2}"}'],
    [{ k: null }, '{"k":null}'],
    [{ k: true, j: false }, '{"j":false,"k":true}'],
    [{ k: [1, 2, 3] }, '{"k":[1,2,3]}'],
    [{ k: 0.75 }, '{"k":0.75}'],
    [{ k: 9007199254740991 }, '{"k":9007199254740991}'],
    [{ observed_at: "2026-02-28T23:59:59.999Z" }, '{"observed_at":"2026-02-28T23:59:59.999Z"}'],
    [{ observed_at: "2028-02-29T00:00:00Z" }, '{"observed_at":"2028-02-29T00:00:00Z"}'],
    [{ observed_at: "2026-12-31T23:59:60+00:00" },
      '{"observed_at":"2026-12-31T23:59:60+00:00"}'],
    [{ observed_at: "2026-09-09T12:00:00-07:00" }, '{"observed_at":"2026-09-09T12:00:00-07:00"}'],
  ];
  for (const [value, expected] of cases) {
    assert.equal(canonicalJson(value), expected, JSON.stringify(value));
  }
  // Key order is UTF-16 code unit order, which is what the PostgreSQL sort key
  // reproduces. An astral key sorts BELOW a high BMP key, because JavaScript
  // compares the surrogate units (0xD800..) rather than the code point
  // (0x10000..). Plain code-point ordering would get this exactly backwards, so
  // the SQL side builds a surrogate-aware sort key rather than ordering by key.
  // Written as escapes rather than literals so the property under test is the
  // code points, not whatever an editor decided to paste.
  const astral = String.fromCodePoint(0x10000);   // units 0xD800 0xDC00
  const highBmp = String.fromCharCode(0xFF00);    // one unit, 0xFF00
  assert.ok(astral.codePointAt(0) > highBmp.codePointAt(0), "astral is the higher code point");
  assert.ok(astral.charCodeAt(0) < highBmp.charCodeAt(0), "astral is the lower code unit");
  assert.equal(canonicalJson({ [highBmp]: 2, [astral]: 1 }),
    `{"${astral}":1,"${highBmp}":2}`);
});

test("the store schema versions are stable identities", () => {
  assert.equal(V5_F01_STORE_SCHEMA_VERSION,
    "doctorcre-v5-f01-record-source-authority-store.v1");
  assert.equal(V5_F01_ENVELOPE_SCHEMA_VERSION,
    "doctorcre-v5-f01-stored-record-envelope.v1");
  assert.equal(STORED_POLICY_RECORD.tenant, ORGANIZATION_TENANT_ID);
  assert.equal(digest(STORED_POLICY_RECORD), POLICY_DIGEST);
});


test("replay returns persisted observation decision before reading changed state", async () => {
  const original = {
    operation: "record-source-observation", outcome: "accepted", actor_slug: "joe",
    entity: "deal", field: "commission_amount", reason_id: "field_established",
    version_ordering: null, conflict_kind: null, policy_digest: POLICY_DIGEST,
    current_state_transition_digest: D(71), event_digest: D(72),
    mutation_receipt_digest: D(73), reconciliation_item_digest: null,
    readback: { current_state: { entity: "deal", field: "commission_amount" } },
  };
  const { db, store } = storeWith({ replay_outcome: original });
  const answer = await store.recordSourceObservation({
    idempotency_key: "syn-replay-observation", observation: OWNER_OBSERVATION,
  }, ctx(JOE));
  assert.equal(answer.decision, "accept");
  assert.equal(answer.reason_id, "field_established");
  assert.equal(answer.event_digest, D(72));
  assert.deepEqual(answer.readback, original);
  assert.equal(db.lastWrite, undefined);
  assert.equal(db.calls.some(c => /f01_current_(policy|field_state)/.test(c.text)), false);
});

test("caller derivative inventory refuses before database work", async () => {
  const { db, store } = storeWith();
  await refuses(store.evaluateArtifactDeletion({
    idempotency_key: "syn-forged-derivatives",
    subject: { ...DELETION_SUBJECT, derivatives: ["synthetic_test_abstract"] },
  }, ctx(JOE)), "unknown_field");
  assert.equal(db.calls.length, 0);
});


const REPLAY_CASES = [
  ["registerRecordSourceAuthorityPolicy", {
    field_registry: FIELD_POLICY, retention_registry: RETENTION_POLICY,
    field_registry_digest: FIELD_REGISTRY.registry_digest,
    retention_registry_digest: RETENTION_REGISTRY.registry_digest,
  }, { policy: null }],
  ["recordSourceObservation", { observation: OWNER_OBSERVATION }, policyScript()],
  ["recordCorporateArtifact", { artifact: ARTIFACT }, {}],
  ["recordParsedProposal", { proposal: PROPOSAL }, policyScript({
    stored_artifact: { artifact_digest: D(21), artifact: {}, created_at: T.early },
  })],
  ["recordDocumentIdentity", { document: DOCUMENT }, {}],
  ["recordArtifactPreservationHold", { hold: {
    hold_id: "synthetic-replay-hold", artifact_digest: D(41), state: "active",
  } }, { stored_artifact: { artifact_digest: D(41), artifact: {}, created_at: T.early } }],
  ["evaluateArtifactDeletion", { subject: DELETION_SUBJECT }, deletionScript([])],
];

for (const [method, fields, script] of REPLAY_CASES) {
  test(`${method} first application and replay project the same committed outcome`, async () => {
    const payload = { idempotency_key: `syn-replay-${method}`, ...fields };
    const initial = storeWith(script);
    const answer = await initial.store[method](payload, ctx(JOE));
    assert.ok(initial.db.lastOutcome);
    // No current policy, artifact, document or hold is available on this retry:
    // replay must not resolve against newer or now-unavailable domain state.
    const replay = storeWith({ replay_outcome: initial.db.lastOutcome,
      server_now: "2026-10-01T00:00:00.000Z" });
    const repeated = await replay.store[method](payload, ctx(JOE));
    assert.deepEqual(repeated, answer);
    assert.equal(replay.db.lastWrite, undefined);
    assert.deepEqual(replay.db.calls.filter(c => c.text.startsWith("SELECT")).map(c => c.text), [
      "SELECT ops.f01_principal() AS principal, ops.f01_now_text() AS server_now",
      "SELECT ops.f01_replay_outcome($1::text, $2::text, $3::text) AS outcome",
    ]);
    const firstBinding = initial.db.calls.find(c => c.text.includes("f01_replay_outcome")).params;
    const replayBinding = replay.db.calls.find(c => c.text.includes("f01_replay_outcome")).params;
    assert.deepEqual(replayBinding, firstBinding);
  });
}

test("observation replay also preserves committed refusal and reconciliation diagnostics", async () => {
  for (const version of [3, 4]) {
    const payload = { idempotency_key: `syn-replay-observation-${version}`,
      observation: { ...OWNER_OBSERVATION, version } };
    const initial = storeWith(policyScript({ field_state: storedState() }));
    const answer = await initial.store.recordSourceObservation(payload, ctx(JOE));
    assert.notEqual(answer.decision, "accept");
    const replay = storeWith({ replay_outcome: initial.db.lastOutcome });
    assert.deepEqual(await replay.store.recordSourceObservation(payload, ctx(JOE)), answer);
    assert.equal(replay.db.lastWrite, undefined);
  }
});

test("authority operations require the database to attest human and authorization class", async () => {
  for (const principal of [
    { actor_slug: "joe", human: false, authorization_class: "verified_partner" },
    { actor_slug: "joe", human: true, authorization_class: "agent_internal" },
  ]) {
    const { db, store } = storeWith({ principal });
    await refuses(store.recordArtifactPreservationHold({ idempotency_key: "syn-db-authority",
      hold: { hold_id: "synthetic-hold", artifact_digest: D(41), state: "active" },
    }, ctx(JOE)), "actor_context_mismatch");
    assert.equal(db.calls.some(c => c.text.includes("f01_replay_outcome")), false);
    assert.equal(db.lastWrite, undefined);
  }
});

test("ordinary evidence requests do not infer human authority from a writer-role principal", async () => {
  const { store } = storeWith({ principal: {
    actor_slug: "joe", human: false, authorization_class: "agent_internal",
  } });
  const answer = await store.recordCorporateArtifact({ idempotency_key: "syn-ordinary-writer",
    artifact: ARTIFACT }, ctx(JOE));
  assert.equal(answer.decision, "allow");
});

test("SQL replay integrity or substitution refusal rolls back without evaluating or writing", async () => {
  for (const code of ["f01_idempotency_payload_mismatch", "f01_idempotency_actor_mismatch",
    "f01_idempotency_operation_mismatch", "f01_corrupt_stored_record", "f01_unsettled_idempotency"]) {
    const error = Object.assign(new Error(code), { code });
    const { db, store } = storeWith({ replayError: error });
    await assert.rejects(store.recordSourceObservation({ idempotency_key: "syn-replay-refusal",
      observation: OWNER_OBSERVATION }, ctx(JOE)), e => e === error);
    assert.equal(db.lastWrite, undefined);
    assert.equal(db.rolledBack, 1);
    assert.equal(db.calls.some(c => c.text.includes("f01_current_policy")), false);
  }
});

test("a mismatched returned replay operation or actor is not presented as a valid outcome", async () => {
  for (const replay_outcome of [
    { operation: "record-document-identity", actor_slug: "joe" },
    { operation: "record-source-observation", actor_slug: "dell" },
  ]) {
    const { store } = storeWith({ replay_outcome });
    await refuses(store.recordSourceObservation({ idempotency_key: "syn-replay-binding",
      observation: OWNER_OBSERVATION }, ctx(JOE)), "invalid_stored_outcome");
  }
});
