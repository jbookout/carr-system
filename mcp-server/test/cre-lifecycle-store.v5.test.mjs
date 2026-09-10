// V5-J102 — the persistence tail, proved case by case.
//
// Everything here is synthetic and nothing reaches a real database, provider,
// network or filesystem. The store runs against a SCRIPTED FAKE HANDLE that
// records every statement and every parameter in order, so these tests prove the
// things a Node suite can actually prove about a persistence layer:
//
//   * which values are DERIVED and which are refused from the caller,
//   * WHICH STATEMENTS RUN, IN WHAT ORDER — the replay-before-state ordering in
//     particular, which is load-bearing rather than incidental,
//   * that subjects and evidence are LOADED and that caller pins are CHECKED
//     against the readback rather than believed,
//   * that the compare-and-swap operands and the evidence recheck manifest
//     actually travel to the database,
//   * that a failing write ROLLS BACK and does not report success,
//   * that a replay reports what LANDED rather than what a fresh evaluation
//     would say now.
//
// NOTHING HERE MIRRORS A RETURN VALUE BACK AT ITSELF. Every assertion is either
// about a statement the store issued, a parameter it bound, the order it did so
// in, or a refusal it produced without issuing a write — never "the fake said X
// and the store returned X".
//
// The things a fake cannot prove — real concurrency, real append-only refusal,
// real direct-DML refusal, real evidence movement under a lock — are the SQL
// fixture's job, and mcp-server/test/cre-lifecycle-postgres.sql asserts them
// there instead of them being claimed twice here.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { canonicalJson, digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_J102_TRANSITION_IDS, V5_J102_DEAL_AXES, V5_J102_EVIDENCE_KINDS,
  V5_J102_EVIDENCE_INTEGRITY, V5_J102_EVIDENCE_LOADER,
  V5_J102_SUBJECT_KINDS,
  assertLifecycleSubject,
  evaluateLifecycleTransition, v5J102EvidenceContract, v5J102TransitionContract,
} from "../src/cre-lifecycle.v5.js";
import {
  V5_J102_ABSENT_EVIDENCE_READERS,
  V5_J102_AXIS_TRANSITIONS,
  V5_J102_INSTRUMENT_TRANSITIONS,
  V5_J102_DECLARED_DOMAIN_FIELDS,
  V5_J102_DECLARED_SELECTOR_FIELDS,
  V5_J102_DERIVED_ONLY_FIELDS,
  V5_J102_ENVELOPE_SCHEMA_VERSION,
  V5_J102_OPERATIONS,
  V5_J102_READ_KINDS,
  V5_J102_STORE_RECORD_KINDS,
  V5_J102_STORE_SCHEMA_VERSION,
  V5_J102_UNWIRED_CAPABILITIES,
  V5J102StoreError,
  createCreLifecycleStore,
  storedSubjectRecord,
  v5J102EnvelopeCanonicalBytes,
  v5J102StoreEnvelope,
  v5J102StoreOperationSchemas,
  v5J102ToolRegistrations,
} from "../src/cre-lifecycle-store.v5.js";

// --- synthetic fixtures ----------------------------------------------------

const SERVER_NOW = "2026-09-09T12:00:00.000Z";
const T = { early: "2026-09-01T09:00:00Z", mid: "2026-09-05T09:00:00Z", late: "2026-09-08T09:00:00Z" };
const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;

const JOE = Object.freeze({ slug: "joe", display: "Joe", human: true, via: "oauth-google" });
const AGENT = Object.freeze({
  slug: "codex", display: "Codex", human: false, via: "oauth-google",
  sponsoring_human_slug: "joe", human_slug: "joe",
});
const ctx = actor => ({ actor });

const relationshipState = (over = {}) => ({
  subject_kind: "relationship", subject_id: "rel-synthetic-1",
  relationship_state: "prospect", active_engagement_count: 0, ...over,
});
const assignmentState = (over = {}) => ({
  subject_kind: "assignment", subject_id: "asg-synthetic-1",
  engagement_id: "eng-synthetic-1", assignment_phase: "search",
  open_negotiation_count: 0, selected_property_id: null,
  active_lease_draft_target_id: null, pending_deal_id: null,
  multi_target_exception_ref: null, ...over,
});
const dealState = (over = {}) => ({
  subject_kind: "deal", subject_id: "deal-synthetic-1",
  assignment_id: "asg-synthetic-1", property_id: "prop-synthetic-1",
  instrument_kind: "lease", deal_state: "pending", execution_state: "unexecuted",
  diligence_state: "not_applicable", closing_state: "not_reached",
  commission_agreement_state: "absent", invoice_state: "not_invoiced",
  payment_state: "unpaid", completion_state: "open",
  cancellation_reason: null, closing_date: null, ...over,
});

/** The shape ops.j102_subject returns: the state plus its recomputed digest. */
const storedSubject = state => ({
  subject_kind: state.subject_kind,
  subject_id: state.subject_id,
  state,
  state_digest: digest(state),
  established_by_transition: "synthetic-fixture",
  updated_by: "joe",
  updated_at: T.late,
  integrity: "recomputed_from_committed_row",
});

/** The shape ops.f01_read('document', ...) returns: a verified F01 envelope. */
const storedDocument = (over = {}) => {
  const record = {
    schema_version: "doctorcre-v5-f01-stored-document-version.v1",
    document_class: "synthetic_agreement",
    neon_identity: { document_id: "doc-synthetic-1", content_digest: D(1), version_no: 1 },
    preparation_state: "approved_for_delivery", delivery_state: "delivered",
    signature_state: "fully_executed", validity_state: "effective", version_state: "current",
    official_filing_state: "complete_official_filing",
    ...over,
  };
  return { body: { record, record_digest: digest(record), integrity: "recomputed_from_committed_row" } };
};

/**
 * The shape ops.j102_first_party_record returns.
 *
 * IT NAMES ITS SUBJECT. The binding lives on the stored row, not on the request
 * that later reads it, so these fixtures carry the subject each record kind is
 * about — and the adversarial cases below hand back a record about somebody
 * else's deal without changing anything else on it.
 */
const FACT_SUBJECT = Object.freeze({
  assignment_mandate: { subject_kind: "assignment", subject_id: "asg-synthetic-1" },
  winning_property_commitment: { subject_kind: "assignment", subject_id: "asg-synthetic-1" },
});
const storedFact = (record_kind, over = {}) => {
  const bound = FACT_SUBJECT[record_kind] ??
    { subject_kind: "deal", subject_id: "deal-synthetic-1" };
  const record = {
    schema_version: "doctorcre-v5-j102-stored-first-party-record.v1",
    tenant: ORGANIZATION_TENANT_ID, record_kind, record_id: "rec-synthetic-1",
    subject_kind: bound.subject_kind, subject_id: bound.subject_id,
    reason: null, detail: null, closing_date: null, supporting_document_id: null,
    recorded_by: "joe", recorded_by_authorization_class: "verified_partner",
    recorded_at: T.mid, advances_lifecycle_state: false, ...over,
  };
  return { record, record_digest: digest(record), integrity: "recomputed_from_committed_row" };
};

/** The shape ops.j102_evidence_subject_link returns when the pin IS associated. */
const storedLink = (over = {}) => {
  const record = {
    schema_version: "doctorcre-v5-j102-stored-evidence-subject-link.v1",
    tenant: ORGANIZATION_TENANT_ID,
    evidence_source: "f01_document", evidence_ref: "doc-synthetic-1",
    version_no: 1, content_digest: D(1),
    subject_kind: "deal", subject_id: "deal-synthetic-1",
    associated_by: "joe", associated_by_authorization_class: "verified_partner",
    associated_at: T.early,
    advances_lifecycle_state: false, creates_document: false,
    asserts_document_state: false, ...over,
  };
  return { record, link_digest: digest(record), integrity: "recomputed_from_committed_row" };
};

// --- the scripted fake handle ---------------------------------------------

class FakeDb {
  constructor(script = {}) {
    this.script = { ...script };
    this.calls = [];
    this.began = 0;
    this.committed = 0;
    this.rolledBack = 0;
    this.lastWrite = null;
  }

  set(key, value) { this.script[key] = value; return this; }

  /** The ordered list of logical statements, for the ordering assertions. */
  get sequence() {
    return this.calls.map(({ text }) => {
      if (["BEGIN", "COMMIT", "ROLLBACK"].includes(text)) return text;
      const match = /ops\.(f01_[a-z_]+|j102_[a-z_]+)\(/.exec(text);
      return match ? match[1] : "unknown";
    });
  }

  paramsFor(fn) {
    const call = this.calls.find(c => c.text.includes(`ops.${fn}(`));
    return call ? call.params : null;
  }

  callsTo(fn) {
    return this.calls.filter(c => c.text.includes(`ops.${fn}(`));
  }

  async query(text, params = []) {
    this.calls.push({ text, params });
    if (text === "BEGIN") { this.began += 1; return { rows: [] }; }
    if (text === "COMMIT") { this.committed += 1; return { rows: [] }; }
    if (text === "ROLLBACK") { this.rolledBack += 1; return { rows: [] }; }

    if (text.includes("ops.f01_principal()") && text.includes("ops.f01_now_text()")) {
      return { rows: [{
        principal: this.script.principal ?? {
          actor_slug: "joe", human: true, authorization_class: "verified_partner",
          derived_by: "server_established_transaction_context",
        },
        server_now: this.script.server_now ?? SERVER_NOW,
      }] };
    }
    if (text.includes("ops.j102_replay_outcome(")) {
      return { rows: [{ outcome: this.script.replay_outcome ?? null }] };
    }
    if (text.includes("ops.j102_subject(")) {
      const [subject_kind, subject_id] = params;
      const subjects = this.script.subjects ?? {};
      return { rows: [{ subject: subjects[`${subject_kind}:${subject_id}`] ?? null }] };
    }
    if (text.includes("ops.f01_read('document'")) {
      return { rows: [{ body: this.script.document ?? null }] };
    }
    if (text.includes("ops.f01_stored_artifact(")) {
      return { rows: [{ artifact: this.script.artifact ?? null }] };
    }
    if (text.includes("ops.j102_first_party_record(")) {
      const [record_kind] = params;
      const facts = this.script.facts ?? {};
      return { rows: [{ record: facts[record_kind] ?? null }] };
    }
    // THE ASSOCIATION READER ASKS A YES/NO QUESTION, and the fake answers it the
    // same way: keyed on the SUBJECT as well as the pin, so a script that
    // associates a document with one deal answers null for every other deal
    // rather than handing the association back with somebody else's subject on it.
    if (text.includes("ops.j102_evidence_subject_link(")) {
      const [evidence_source, evidence_ref, version_no, content_digest,
        subject_kind, subject_id] = params;
      const links = this.script.links ?? {};
      const key = [evidence_source, evidence_ref, version_no, content_digest,
        subject_kind, subject_id].join("|");
      return { rows: [{ link: links[key] ?? null }] };
    }
    if (text.includes("ops.j102_read(")) {
      return { rows: [{ body: this.script.read_body ?? { body: null } }] };
    }

    for (const fn of ["j102_apply_transition", "j102_record_first_party_fact",
      "j102_record_evidence_subject_link", "j102_record_salesforce_reference",
      "j102_record_correction"]) {
      if (text.includes(`ops.${fn}(`)) {
        this.lastWrite = { fn, params };
        if (this.script.writeError) throw this.script.writeError;
        return { rows: [{ outcome: this.outcomeFor(fn, params) }] };
      }
    }
    throw new Error(`unscripted statement: ${text}`);
  }

  /**
   * The outcome shape each writer builds, reconstructed FROM THE PARAMETERS the
   * store actually bound. A fake that returned a canned blob would pass whatever
   * the store sent, including nothing at all; reading the parameters is what
   * makes these assertions about the store rather than about the fake.
   */
  outcomeFor(fn, params) {
    const envelope = index => (params[index] == null ? null : JSON.parse(params[index]));
    // The actor the SCRIPTED principal names, not a literal, so a fixture running
    // as a sponsored agent gets an outcome attributed to that agent — which is
    // what resultFromOutcome checks before it reports anything.
    const actor_slug = this.script.principal?.actor_slug ?? "joe";
    if (fn === "j102_apply_transition") {
      const subjects = envelope(2);
      const events = envelope(3);
      const diagnostics = envelope(7);
      return {
        operation: diagnostics.operation,
        decision: "allow",
        outcome: "applied",
        actor_slug,
        transition_id: params[0],
        reason_id: diagnostics.reason_id,
        coupled_facts_committed: diagnostics.coupled_facts,
        subject_digests: Object.fromEntries(subjects.map(e =>
          [`${e.record.subject_kind}:${e.record.subject_id}`, digest(e.record.state)])),
        event_digests: events.map(e => e.record_digest),
        evidence_rechecked_under_lock: true,
        readback: { subjects: subjects.map(e => e.record.state) },
      };
    }
    const record = envelope(0).record;
    if (fn === "j102_record_first_party_fact") {
      return { operation: "record-lifecycle-fact", decision: "allow",
        reason_id: "first_party_record_appended", actor_slug,
        record_kind: record.record_kind, record_id: record.record_id,
        record_digest: envelope(0).record_digest,
        bound_subject_kind: record.subject_kind, bound_subject_id: record.subject_id,
        readback: { record } };
    }
    if (fn === "j102_record_evidence_subject_link") {
      return { operation: "record-evidence-subject-link", decision: "allow",
        reason_id: "evidence_subject_association_appended", actor_slug,
        link_digest: envelope(0).record_digest,
        evidence_source: record.evidence_source,
        bound_subject_kind: record.subject_kind, bound_subject_id: record.subject_id,
        readback: { record } };
    }
    if (fn === "j102_record_salesforce_reference") {
      return { operation: "link-salesforce-reference", decision: "allow",
        reason_id: record.linked_subject_kind == null
          ? "external_reference_recorded_unlinked" : "external_reference_progressively_linked",
        actor_slug, opportunity_id: record.opportunity_id,
        linked_subject_kind: record.linked_subject_kind,
        reference_digest: envelope(0).record_digest, readback: { record } };
    }
    return { operation: "record-lifecycle-correction", decision: "allow",
      reason_id: "correction_receipt_appended", actor_slug,
      receipt_digest: envelope(0).record_digest,
      corrected_fields: record.corrected_fields, readback: { record } };
  }
}

// The evidence references a caller supplies. Note what is NOT here: no document
// state, no signature, no closing date, no record kind.
const documentRef = (evidence_kind, over = {}) => ({
  evidence_kind, document_id: "doc-synthetic-1",
  expected_version_no: 1, expected_content_digest: D(1), ...over,
});
const recordRef = (evidence_kind, over = {}) => ({
  evidence_kind, record_id: "rec-synthetic-1", ...over,
});

// --- registration surface --------------------------------------------------

test("the registration description is a description, and it says what the parent still owes", () => {
  const registrations = v5J102ToolRegistrations();
  assert.equal(registrations.length, V5_J102_OPERATIONS.length);
  for (const entry of registrations) {
    assert.ok(V5_J102_OPERATIONS.includes(entry.name));
    assert.ok(entry.role.length > 0, `${entry.name} states its role`);
    assert.ok(entry.handler.length > 0);
    // Four things this module deliberately did NOT do.
    assert.equal(entry.registered_in_scac, false);
    assert.equal(entry.registered_in_mutation_registry, false);
    assert.equal(entry.migration_bound, false);
    assert.equal(entry.accepted, false);
  }
  const schemas = v5J102StoreOperationSchemas();
  // Four acts need partner authority: committing to a winner, closing a deal,
  // cancelling one, and correcting the record. Everything else an authenticated
  // sponsored agent may do, because the evidence is what decides.
  // Five acts need partner authority: committing to a winner, closing a deal,
  // cancelling one, correcting the record, and — H5's shape applied to BLOCK-2's
  // producer — saying which transaction a document belongs to. Everything else an
  // authenticated sponsored agent may do, because the evidence is what decides.
  const authorityOnly = V5_J102_OPERATIONS.filter(n => schemas[n].authorityOnly).sort();
  assert.deepEqual(authorityOnly,
    ["cancel-pending-deal", "commit-winning-property", "record-deal-closing",
      "record-evidence-subject-link", "record-lifecycle-correction"]);
  assert.deepEqual(V5_J102_OPERATIONS.filter(n => schemas[n].humanOnly),
    ["record-lifecycle-correction"]);
});

test("the absent-reader registry names real evidence kinds and both missing facts", () => {
  assert.deepEqual(Object.keys(V5_J102_ABSENT_EVIDENCE_READERS).sort(),
    ["approved_representation_equivalent", "multi_target_exception_approval"]);
  for (const entry of Object.values(V5_J102_ABSENT_EVIDENCE_READERS)) {
    assert.ok(entry.missing_fact.length > 0);
    assert.ok(entry.why.length > 0);
    assert.equal(entry.produced_by, "not_produced_by_this_slice");
  }
});

test("an envelope hashes to its own claim, and the canonical bytes are reproducible", () => {
  const record = storedSubjectRecord({
    subject: dealState(), transition_id: "record-lease-execution",
    prior_state_digest: D(9), updated_by: "joe", updated_at: SERVER_NOW,
  });
  const envelope = v5J102StoreEnvelope("stored_lifecycle_subject", record);
  assert.equal(envelope.schema_version, V5_J102_ENVELOPE_SCHEMA_VERSION);
  assert.equal(envelope.record_digest, digest(record));
  assert.equal(v5J102EnvelopeCanonicalBytes(envelope), canonicalJson(envelope));
  assert.throws(() => v5J102StoreEnvelope("stored_invented_kind", record),
    e => e instanceof V5J102StoreError && e.code === "unknown_record_kind");
  assert.equal(V5_J102_STORE_RECORD_KINDS.length, 6);
  assert.ok(V5_J102_STORE_RECORD_KINDS.includes("stored_evidence_subject_link"));
});

// --- derived-field and authority guards ------------------------------------

test("a caller cannot supply the tenant, the clock, the subject, the evidence or a state axis", async () => {
  const db = new FakeDb();
  const store = createCreLifecycleStore({ db });
  const base = {
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  };
  for (const field of ["tenant", "now", "subject", "evidence", "recorded_by",
    "deal_state", "closing_state", "closing_date", "state_digest",
    "relationship_state", "assignment_phase",
    // BLOCK-2 and H5: which subject an authentic record is about, and in what
    // capacity its author wrote it, are read off stored rows and are not a
    // caller's to state on the request that consumes them.
    "subject_binding", "bound_by", "binding_digest", "link_digest",
    "bound_subject_kind", "bound_subject_id"]) {
    await assert.rejects(() => store.recordDealExecution({ ...base, [field]: "x" }, ctx(JOE)),
      e => e instanceof V5J102StoreError && e.code === "caller_derived_field_refused",
      `${field} must be refused as a derived field`);
  }
  // And nothing was attempted against the database.
  assert.equal(db.calls.length, 0);
});

test("a caller cannot assert the outcome or inject authority, and the refusal names which", async () => {
  const db = new FakeDb();
  const store = createCreLifecycleStore({ db });
  const base = {
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  };
  for (const field of ["is_signed", "already_executed", "counterparty_accepted",
    // A state axis whose NAME contains an asserted fragment is caught by the
    // asserted guard before the derived one. Both refuse; the reason differs,
    // and the more specific one is the more useful thing to tell a caller.
    "execution_state"]) {
    await assert.rejects(() => store.recordDealExecution({ ...base, [field]: true }, ctx(JOE)),
      e => e instanceof V5J102StoreError && e.code === "caller_asserted_fact_refused",
      `${field} must be refused as an asserted fact`);
  }
  for (const field of ["acting_as", "override_prerequisites", "authorized_by"]) {
    await assert.rejects(() => store.recordDealExecution({ ...base, [field]: "joe" }, ctx(JOE)),
      e => e instanceof V5J102StoreError && e.code === "caller_authority_field_refused",
      `${field} must be refused as an authority claim`);
  }
  assert.equal(db.calls.length, 0);
});

test("the derived-only list covers every lifecycle state axis, so none can be caller-set", () => {
  for (const axis of V5_J102_DEAL_AXES) {
    assert.ok(V5_J102_DERIVED_ONLY_FIELDS.includes(axis), `${axis} must be derived-only`);
  }
  for (const axis of ["relationship_state", "engagement_state", "assignment_phase",
    "negotiation_state", "representation_basis"]) {
    assert.ok(V5_J102_DERIVED_ONLY_FIELDS.includes(axis), `${axis} must be derived-only`);
  }
});

test("an unauthenticated actor, an inadmissible class, and a non-partner on an authorityOnly op all refuse", async () => {
  const db = new FakeDb();
  const store = createCreLifecycleStore({ db });
  const payload = {
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [recordRef("final_closing_settlement")],
  };
  await assert.rejects(() => store.recordDealClosing(payload, ctx({ slug: "nobody", human: true })),
    e => e instanceof V5J102StoreError && e.code === "unauthenticated_actor");
  // identity.js knows classes this lifecycle does not admit; the boundary is
  // drawn here by name rather than surfacing as an unknown-vocabulary throw
  // from the kernel about a class that simply is not entitled to move a record.
  await assert.rejects(() => store.recordDealClosing(payload,
    ctx({ slug: "codex", human: false, probe: true })),
  e => e instanceof V5J102StoreError && e.code === "actor_class_not_admitted_for_lifecycle");
  await assert.rejects(() => store.recordDealClosing(payload, ctx(AGENT)),
    e => e instanceof V5J102StoreError && e.code === "authority_only_operation_refused");
  await assert.rejects(() => store.recordLifecycleCorrection({
    idempotency_key: "j102-fixture-2",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    correction_record_id: "rec-synthetic-1", corrected_fields: ["field_name"],
    reason: "synthetic fixture reason",
  }, ctx(AGENT)), e => e instanceof V5J102StoreError && e.code === "human_only_operation_refused");
  assert.equal(db.calls.length, 0, "no authority failure reaches the database");
});

// --- ordering and bindings -------------------------------------------------

/** The association a lease document needs before it can advance THIS deal. */
const LEASE_LINK_KEY =
  ["f01_document", "doc-synthetic-1", 1, D(1), "deal", "deal-synthetic-1"].join("|");
/** The same, for the engagement letter that establishes THIS client. */
const ETL_LINK_KEY =
  ["f01_document", "doc-synthetic-1", 1, D(1), "relationship", "rel-synthetic-1"].join("|");

function leaseExecutionDb(extra = {}) {
  return new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState()) },
    document: storedDocument({ document_class: "lease" }),
    links: { [LEASE_LINK_KEY]: storedLink() },
    ...extra,
  });
}

test("replay is claimed BEFORE any state is read, and the whole statement order is exact", async () => {
  const db = leaseExecutionDb();
  const store = createCreLifecycleStore({ db });
  const answer = await store.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1",
      expected_state_digest: digest(dealState()) },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  // THE ORDER IS THE ASSERTION. A replay that ran after the compare-and-swap
  // would refuse a request that had already succeeded, so the claim must come
  // before the first subject read.
  assert.deepEqual(db.sequence, [
    "BEGIN",
    "f01_principal",
    "j102_replay_outcome",
    "j102_subject",
    "f01_read",
    // BLOCK-2: the document is pinned AND its association with this deal is
    // read, both before the writer is called at all.
    "j102_evidence_subject_link",
    "j102_apply_transition",
    "COMMIT",
  ]);
  assert.equal(db.began, 1);
  assert.equal(db.committed, 1);
  assert.equal(db.rolledBack, 0);
});

test("the transition writer receives the CAS digests, the envelopes and the recheck manifest", async () => {
  const db = leaseExecutionDb();
  const store = createCreLifecycleStore({ db });
  await store.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE));

  const [transition_id, casJson, subjectsJson, eventsJson, recheckJson, key, requestDigest,
    diagnosticsJson] = db.paramsFor("j102_apply_transition");

  // The transition chosen from the STORED instrument kind, not from the caller.
  assert.equal(transition_id, "record-lease-execution");
  assert.ok(V5_J102_TRANSITION_IDS.includes(transition_id));

  // The compare-and-swap operand is the digest of the LOADED state.
  const cas = JSON.parse(casJson);
  assert.deepEqual(Object.keys(cas), ["deal:deal-synthetic-1"]);
  assert.equal(cas["deal:deal-synthetic-1"], digest(dealState()));

  // One subject envelope, carrying the SERVER's actor and instant, hashing to
  // its own claim, and naming the digest it was decided against.
  const subjects = JSON.parse(subjectsJson);
  assert.equal(subjects.length, 1);
  assert.equal(subjects[0].record_kind, "stored_lifecycle_subject");
  assert.equal(subjects[0].tenant, ORGANIZATION_TENANT_ID);
  assert.equal(subjects[0].record.updated_by, "joe");
  assert.equal(subjects[0].record.updated_at, SERVER_NOW);
  assert.equal(subjects[0].record.prior_state_digest, digest(dealState()));
  assert.equal(subjects[0].record.state.execution_state, "executed");
  assert.equal(subjects[0].record_digest, digest(subjects[0].record));

  // One event envelope, naming the transition and the evidence it rested on.
  const events = JSON.parse(eventsJson);
  assert.equal(events.length, 1);
  assert.equal(events[0].record.event.event_kind, "lease_executed");
  assert.equal(events[0].record.transition_id, "record-lease-execution");
  // The history says WHICH deal each piece of evidence was about, not only which
  // document it was.
  assert.deepEqual(events[0].record.evidence_references, [{
    evidence_kind: "executed_lease", source: "f01_document", reference: "doc-synthetic-1",
    subject_binding: {
      subject_kind: "deal", subject_id: "deal-synthetic-1",
      bound_by: "stored_evidence_subject_link",
      binding_digest: storedLink().link_digest,
    },
  }]);
  assert.equal(events[0].record_digest, digest(events[0].record));

  // THE RECHECK MANIFEST, which is what lets the database re-read the exact pin
  // AND the exact binding under its own lock. A manifest carrying only the
  // document id would let a newer version satisfy it; one carrying no binding
  // would let somebody else's lease satisfy it.
  const recheck = JSON.parse(recheckJson);
  assert.deepEqual(recheck, [{
    evidence_kind: "executed_lease", source: "f01_document",
    reader: "ops.f01_read.document",
    selector: { document_id: "doc-synthetic-1" },
    expected_version_no: 1, expected_content_digest: D(1),
    binding: {
      evidence_source: "f01_document", evidence_ref: "doc-synthetic-1",
      version_no: 1, content_digest: D(1),
      subject_kind: "deal", subject_id: "deal-synthetic-1",
    },
    expected_link_digest: storedLink().link_digest,
  }]);

  assert.equal(key, "j102-fixture-1");
  assert.match(requestDigest, /^sha256:[0-9a-f]{64}$/);
  const diagnostics = JSON.parse(diagnosticsJson);
  assert.equal(diagnostics.operation, "record-deal-execution");
  assert.equal(diagnostics.reason_id, "lease_execution_marks_executed_lease");
  assert.deepEqual(diagnostics.coupled_facts, ["deal.execution_state"]);
});

test("the executed instrument is chosen from the stored deal, so a purchase cannot borrow lease semantics", async () => {
  const db = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState({ instrument_kind: "purchase" })) },
    document: storedDocument({ document_class: "purchase_contract", validity_state: "draft" }),
    links: { [LEASE_LINK_KEY]: storedLink() },
  });
  const store = createCreLifecycleStore({ db });
  await store.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("signed_purchase_contract")],
  }, ctx(JOE));
  const params = db.paramsFor("j102_apply_transition");
  assert.equal(params[0], "record-purchase-contract-execution");
  const subjects = JSON.parse(params[2]);
  // Q094: executed on the execution axis, pending on the business axis.
  assert.equal(subjects[0].record.state.execution_state, "executed");
  assert.equal(subjects[0].record.state.deal_state, "pending");
  assert.equal(subjects[0].record.state.diligence_state, "in_progress");
});

test("a declared axis selects exactly one transition and the others stay put", async () => {
  const db = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState()) },
    facts: { payment: storedFact("payment") },
  });
  const store = createCreLifecycleStore({ db });
  await store.recordDealAxis({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [recordRef("payment_received")],
    declared: { axis: "payment_state", payment_level: "paid" },
  }, ctx(JOE));
  const params = db.paramsFor("j102_apply_transition");
  assert.equal(params[0], "record-payment");
  const state = JSON.parse(params[2])[0].record.state;
  assert.equal(state.payment_state, "paid");
  for (const other of V5_J102_DEAL_AXES.filter(a => a !== "payment_state")) {
    assert.equal(state[other], dealState()[other], `${other} must not move`);
  }
});

test("an unregistered declared axis determines no transition and nothing is written", async () => {
  const db = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState()) },
    facts: { payment: storedFact("payment") },
  });
  const store = createCreLifecycleStore({ db });
  const answer = await store.recordDealAxis({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [recordRef("payment_received")],
    declared: { axis: "deal_state", payment_level: "paid" },
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "transition_not_determined");
  assert.equal(db.callsTo("j102_apply_transition").length, 0);
});

test("the declared axis selects a verb HERE and never enters the kernel's declared contract", () => {
  // The axis names which of the four orthogonal deal verbs is being asked for.
  // It is this module's dispatch input, not a fact about a deal, and the
  // kernel's `request.declared` is closed and does not carry it — so the store
  // forwards the domain half and drops the selector. This asks the kernel
  // DIRECTLY which keys it accepts, so the two halves cannot drift apart
  // without the suite naming the key that moved.
  const probe = declared => {
    try {
      evaluateLifecycleTransition({
        tenant: ORGANIZATION_TENANT_ID,
        transition_id: "record-payment",
        subject: dealState(),
        actor: { slug: "joe", human: true, authorization_class: "verified_partner",
          derived_by: "authenticated_handler_context" },
        declared,
        // Deliberately empty: the declared surface is validated before the
        // evidence array's length is, so the probe never has to build evidence
        // to learn whether a key got past that surface.
        evidence: [],
        now: SERVER_NOW,
      });
    } catch (error) { return error.code; }
    return "accepted";
  };
  for (const key of V5_J102_DECLARED_DOMAIN_FIELDS) {
    assert.notEqual(probe({ [key]: "synthetic" }), "unknown_field",
      `${key} is forwarded verbatim, so the kernel must accept it`);
  }
  assert.deepEqual([...V5_J102_DECLARED_SELECTOR_FIELDS], ["axis"]);
  for (const key of V5_J102_DECLARED_SELECTOR_FIELDS) {
    assert.equal(probe({ [key]: "payment_state" }), "unknown_field",
      `${key} is a store selector; forwarding it would ask the kernel to widen a closed surface`);
  }
});

test("a commitment loads the related negotiation and binds BOTH compare-and-swap operands", async () => {
  const asg = assignmentState({ assignment_phase: "negotiation", open_negotiation_count: 3 });
  const neg = {
    subject_kind: "property_negotiation", subject_id: "neg-synthetic-1",
    assignment_id: "asg-synthetic-1", property_id: "prop-synthetic-1",
    negotiation_state: "loi_accepted",
  };
  const db = new FakeDb({
    subjects: {
      "assignment:asg-synthetic-1": storedSubject(asg),
      "property_negotiation:neg-synthetic-1": storedSubject(neg),
    },
    facts: { winning_property_commitment: storedFact("winning_property_commitment") },
  });
  const store = createCreLifecycleStore({ db });
  const answer = await store.commitWinningProperty({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "assignment", subject_id: "asg-synthetic-1" },
    related_refs: { property_negotiation: { subject_kind: "property_negotiation",
      subject_id: "neg-synthetic-1" } },
    evidence_refs: [recordRef("winner_selection_commitment")],
    declared: { instrument_kind: "lease", new_deal_id: "deal-synthetic-1" },
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");

  const params = db.paramsFor("j102_apply_transition");
  // BLOCK-1. EVERY subject the decision touches is a compare-and-swap operand:
  // the ones that were READ, including the negotiation, and the one that is
  // CREATED. A prerequisite that moved invalidates the decision exactly as a
  // target that moved does, and a created id that is already taken must not be
  // upserted over.
  const cas = JSON.parse(params[1]);
  assert.deepEqual(Object.keys(cas).sort(),
    ["assignment:asg-synthetic-1", "deal:deal-synthetic-1",
      "property_negotiation:neg-synthetic-1"]);
  assert.equal(cas["property_negotiation:neg-synthetic-1"], digest(neg));
  // AN EXPLICIT null, present as a key. "This subject must be ABSENT" and "I
  // have no opinion about this subject" used to be the same bytes — no key at
  // all — and the writer could only read the second.
  assert.ok(Object.prototype.hasOwnProperty.call(cas, "deal:deal-synthetic-1"));
  assert.equal(cas["deal:deal-synthetic-1"], null);

  // Three subjects land together: the negotiation, the assignment and the NEW
  // pending deal. Q082's coupled facts, as parameters.
  const subjects = JSON.parse(params[2]);
  assert.deepEqual(subjects.map(e => e.record.subject_kind).sort(),
    ["assignment", "deal", "property_negotiation"]);
  const dealEnvelope = subjects.find(e => e.record.subject_kind === "deal");
  assert.equal(dealEnvelope.record.state.deal_state, "pending");
  assert.equal(dealEnvelope.record.state.execution_state, "unexecuted");
  // A newly created subject declares a null prior digest, and it is the SAME
  // null the operand carries — the writer refuses the pair if they disagree.
  assert.equal(dealEnvelope.record.prior_state_digest, null);
  assert.equal(JSON.parse(params[3]).length, 3, "three events accompany three facts");
});

test("BLOCK-1: a created subject id that is already taken refuses instead of overwriting", async () => {
  // THE REPRODUCER, at the store. deal-A exists, closed, with its closing date,
  // under its own assignment. A verified partner commits a winner on a DIFFERENT
  // assignment and names deal-A as the new deal id. Every kernel check passes,
  // because the kernel has no view of existing deals.
  const asg = assignmentState({ subject_id: "asg-synthetic-2",
    assignment_phase: "negotiation", open_negotiation_count: 1 });
  const neg = {
    subject_kind: "property_negotiation", subject_id: "neg-synthetic-2",
    assignment_id: "asg-synthetic-2", property_id: "prop-synthetic-2",
    negotiation_state: "loi_accepted",
  };
  const existing = dealState({ subject_id: "deal-synthetic-1", deal_state: "closed",
    execution_state: "executed", closing_state: "closed", closing_date: T.late });
  const db = new FakeDb({
    subjects: {
      "assignment:asg-synthetic-2": storedSubject(asg),
      "property_negotiation:neg-synthetic-2": storedSubject(neg),
      "deal:deal-synthetic-1": storedSubject(existing),
    },
    facts: { winning_property_commitment: storedFact("winning_property_commitment",
      { subject_kind: "assignment", subject_id: "asg-synthetic-2" }) },
  });
  const store = createCreLifecycleStore({ db });
  const answer = await store.commitWinningProperty({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "assignment", subject_id: "asg-synthetic-2" },
    related_refs: { property_negotiation: { subject_kind: "property_negotiation",
      subject_id: "neg-synthetic-2" } },
    evidence_refs: [recordRef("winner_selection_commitment")],
    declared: { instrument_kind: "lease", new_deal_id: "deal-synthetic-1" },
  }, ctx(JOE));

  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "created_subject_id_already_exists");
  assert.equal(answer.subject_id, "deal-synthetic-1");
  assert.equal(answer.overwrote_existing_subject, false);
  assert.equal(answer.records_written, 0);
  assert.equal(db.callsTo("j102_apply_transition").length, 0,
    "the writer is never reached, so the closed deal cannot be replaced");
});

test("BLOCK-1: a valid new creation still lands, and its operand is an explicit null", async () => {
  const asg = assignmentState({ assignment_phase: "negotiation", open_negotiation_count: 1 });
  const neg = {
    subject_kind: "property_negotiation", subject_id: "neg-synthetic-1",
    assignment_id: "asg-synthetic-1", property_id: "prop-synthetic-1",
    negotiation_state: "loi_accepted",
  };
  const db = new FakeDb({
    subjects: {
      "assignment:asg-synthetic-1": storedSubject(asg),
      "property_negotiation:neg-synthetic-1": storedSubject(neg),
    },
    facts: { winning_property_commitment: storedFact("winning_property_commitment") },
  });
  const store = createCreLifecycleStore({ db });
  const answer = await store.commitWinningProperty({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "assignment", subject_id: "asg-synthetic-1" },
    related_refs: { property_negotiation: { subject_kind: "property_negotiation",
      subject_id: "neg-synthetic-1" } },
    evidence_refs: [recordRef("winner_selection_commitment")],
    declared: { instrument_kind: "lease", new_deal_id: "deal-synthetic-fresh" },
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  const cas = JSON.parse(db.paramsFor("j102_apply_transition")[1]);
  assert.equal(cas["deal:deal-synthetic-fresh"], null);
  // The absence was CHECKED rather than assumed: the store asked for the id it
  // was about to create.
  assert.ok(db.callsTo("j102_subject").some(c => c.params[1] === "deal-synthetic-fresh"));
});

// --- refusals that never reach a write -------------------------------------

test("a stale subject digest refuses and issues no write", async () => {
  const db = leaseExecutionDb();
  const store = createCreLifecycleStore({ db });
  const answer = await store.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1",
      expected_state_digest: D(99) },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "stale_subject_digest");
  assert.equal(answer.stored_state_digest, digest(dealState()));
  assert.equal(answer.expected_state_digest, D(99));
  assert.equal(db.callsTo("j102_apply_transition").length, 0);
  assert.equal(db.callsTo("f01_read").length, 0, "evidence is not even loaded");
});

test("a document pin that does not match the readback refuses, and names which pin moved", async () => {
  const movedVersion = leaseExecutionDb({ document: storedDocument({
    document_class: "lease",
    neon_identity: { document_id: "doc-synthetic-1", content_digest: D(1), version_no: 4 },
  }) });
  const store1 = createCreLifecycleStore({ db: movedVersion });
  const versionAnswer = await store1.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE));
  assert.equal(versionAnswer.decision, "refuse");
  assert.equal(versionAnswer.missing_fact, "f01_document_version_moved");
  assert.equal(movedVersion.callsTo("j102_apply_transition").length, 0);

  const movedBytes = leaseExecutionDb({ document: storedDocument({
    document_class: "lease",
    neon_identity: { document_id: "doc-synthetic-1", content_digest: D(8), version_no: 1 },
  }) });
  const store2 = createCreLifecycleStore({ db: movedBytes });
  const digestAnswer = await store2.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE));
  assert.equal(digestAnswer.missing_fact, "f01_document_content_digest_mismatch");
  assert.equal(movedBytes.callsTo("j102_apply_transition").length, 0);
});

test("an absent evidence reader fails closed with the missing fact named and no reader called", async () => {
  const db = new FakeDb({
    subjects: { "relationship:rel-synthetic-1": storedSubject(relationshipState()) },
  });
  const store = createCreLifecycleStore({ db });
  const answer = await store.recordRepresentationAgreement({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "relationship", subject_id: "rel-synthetic-1" },
    evidence_refs: [{ evidence_kind: "approved_representation_equivalent",
      approval_ref: "appr-synthetic-1" }],
    declared: { new_subject_id: "eng-synthetic-1" },
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "required_evidence_unavailable");
  assert.equal(answer.missing_fact, "authenticated_representation_equivalence_approval");
  assert.equal(answer.produced_by, "not_produced_by_this_slice");
  assert.equal(answer.fabricated_authority, false);
  assert.ok(answer.missing_fact_reason.length > 0);
  // No approval reader is called and no write is attempted: the refusal is a
  // policy answer, not a database error.
  assert.equal(db.callsTo("j102_typed_approval").length, 0);
  assert.equal(db.callsTo("j102_apply_transition").length, 0);
  // And the ETL path through the SAME operation still works, so the fail-closed
  // branch is narrow rather than a blanket outage.
  const working = new FakeDb({
    subjects: { "relationship:rel-synthetic-1": storedSubject(relationshipState()) },
    document: storedDocument({ document_class: "engagement_letter" }),
    links: { [ETL_LINK_KEY]: storedLink({ subject_kind: "relationship",
      subject_id: "rel-synthetic-1" }) },
  });
  const ok = await createCreLifecycleStore({ db: working }).recordRepresentationAgreement({
    idempotency_key: "j102-fixture-2",
    subject_ref: { subject_kind: "relationship", subject_id: "rel-synthetic-1" },
    evidence_refs: [documentRef("signed_engagement_letter")],
    declared: { new_subject_id: "eng-synthetic-1" },
  }, ctx(JOE));
  assert.equal(ok.decision, "allow");
});

test("a first-party record that does not exist refuses and names who would produce it", async () => {
  const db = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState({ execution_state: "executed" })) },
  });
  const store = createCreLifecycleStore({ db });
  const answer = await store.recordDealClosing({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [recordRef("final_closing_settlement")],
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.missing_fact, "first_party_record_not_found");
  assert.equal(answer.produced_by, "j102_record_first_party_fact");
  assert.equal(db.callsTo("j102_apply_transition").length, 0);
});

test("a kernel refusal is reported with its reason and detail, and nothing is written", async () => {
  const db = new FakeDb({
    // Already executed: the transition's own prerequisite refuses.
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState({ execution_state: "executed" })) },
    document: storedDocument({ document_class: "lease" }),
    links: { [LEASE_LINK_KEY]: storedLink() },
  });
  const store = createCreLifecycleStore({ db });
  const answer = await store.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "prerequisite_not_met");
  assert.equal(answer.refusal_detail.unmet_axis, "execution_state");
  assert.equal(answer.refusal_detail.observed, "executed");
  assert.equal(db.callsTo("j102_apply_transition").length, 0);
  assert.equal(db.committed, 1, "a refusal is still a completed transaction, not a rollback");
});

test("the database's own actor must match the handler's, and a mismatch refuses before any write", async () => {
  const db = leaseExecutionDb({
    principal: { actor_slug: "dell", human: true, authorization_class: "verified_partner" },
  });
  const store = createCreLifecycleStore({ db });
  await assert.rejects(() => store.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE)), e => e instanceof V5J102StoreError && e.code === "actor_context_mismatch");
  assert.equal(db.callsTo("j102_apply_transition").length, 0);
  assert.equal(db.rolledBack, 1, "the failed transaction rolls back");
});

test("a corrupt stored subject is refused rather than repaired", async () => {
  const corrupt = storedSubject(dealState());
  corrupt.state_digest = D(77);
  const db = leaseExecutionDb({ subjects: { "deal:deal-synthetic-1": corrupt } });
  const store = createCreLifecycleStore({ db });
  await assert.rejects(() => store.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE)), e => e instanceof V5J102StoreError && e.code === "corrupt_stored_subject");
  assert.equal(db.rolledBack, 1);
});

// --- rollback and replay ---------------------------------------------------

test("a failing write rolls back, reports nothing as applied, and rethrows the original error", async () => {
  const failure = Object.assign(new Error("j102_stale_subject_digest"), { code: "40001" });
  const db = leaseExecutionDb({ writeError: failure });
  const store = createCreLifecycleStore({ db });
  await assert.rejects(() => store.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE)), e => e === failure);
  assert.equal(db.began, 1);
  assert.equal(db.committed, 0, "a failed write never commits");
  assert.equal(db.rolledBack, 1);
  assert.equal(db.sequence.at(-1), "ROLLBACK");
});

test("a replay reports what LANDED, and re-evaluates nothing", async () => {
  const stored = {
    operation: "record-deal-execution", decision: "allow",
    reason_id: "lease_execution_marks_executed_lease", actor_slug: "joe",
    transition_id: "record-lease-execution",
    subject_digests: { "deal:deal-synthetic-1": D(42) },
    event_digests: [D(43)], coupled_facts_committed: ["deal.execution_state"],
    evidence_rechecked_under_lock: true, readback: { subjects: [] },
  };
  // The stored deal is now CLOSED — a fresh evaluation would refuse. The replay
  // must still return the outcome that was committed, which is the whole point.
  const db = leaseExecutionDb({
    replay_outcome: stored,
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState({ deal_state: "closed" })) },
  });
  const store = createCreLifecycleStore({ db });
  const answer = await store.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "lease_execution_marks_executed_lease");
  assert.equal(answer.transition_id, "record-lease-execution");
  assert.equal(answer.evidence_rechecked_under_lock, true);
  assert.equal(answer.partial_application, false);
  // No state was read and no write was attempted after the replay hit.
  assert.deepEqual(db.sequence, ["BEGIN", "f01_principal", "j102_replay_outcome", "COMMIT"]);
});

test("a stored outcome attributed to another actor is refused rather than replayed", async () => {
  const db = leaseExecutionDb({
    replay_outcome: { operation: "record-deal-execution", actor_slug: "dell",
      reason_id: "lease_execution_marks_executed_lease" },
  });
  const store = createCreLifecycleStore({ db });
  await assert.rejects(() => store.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE)), e => e instanceof V5J102StoreError && e.code === "invalid_stored_outcome");
});

// --- the non-transition writers --------------------------------------------

const dealSubjectDb = (extra = {}) => new FakeDb({
  subjects: { "deal:deal-synthetic-1": storedSubject(dealState()) }, ...extra,
});

test("a first-party fact is appended with the server's actor, instant, binding and author class", async () => {
  const db = dealSubjectDb();
  const store = createCreLifecycleStore({ db });
  const answer = await store.recordLifecycleFact({
    idempotency_key: "j102-fixture-1",
    fact: { record_kind: "closing_settlement", record_id: "rec-synthetic-1",
      subject_kind: "deal", subject_id: "deal-synthetic-1",
      closing_date: T.late, supporting_document_id: "doc-synthetic-1" },
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.advances_lifecycle_state, false);
  const envelope = JSON.parse(db.paramsFor("j102_record_first_party_fact")[0]);
  assert.equal(envelope.record.recorded_by, "joe");
  assert.equal(envelope.record.recorded_at, SERVER_NOW);
  assert.equal(envelope.record.closing_date, T.late);
  // BLOCK-2: the record says which deal it is about, once, at authoring time.
  assert.equal(envelope.record.subject_kind, "deal");
  assert.equal(envelope.record.subject_id, "deal-synthetic-1");
  // H5: and who — in what capacity — wrote it, derived rather than supplied.
  assert.equal(envelope.record.recorded_by_authorization_class, "verified_partner");
  assert.equal(envelope.record.advances_lifecycle_state, false);
  assert.equal(envelope.record_digest, digest(envelope.record));

  // A record about a subject nobody holds is a fact waiting for whatever later
  // takes that id.
  const dangling = new FakeDb({ subjects: {} });
  const answer2 = await createCreLifecycleStore({ db: dangling }).recordLifecycleFact({
    idempotency_key: "j102-fixture-2",
    fact: { record_kind: "closing_settlement", record_id: "rec-synthetic-2",
      subject_kind: "deal", subject_id: "deal-nonexistent", closing_date: T.late },
  }, ctx(JOE));
  assert.equal(answer2.reason_id, "bound_subject_not_found");
  assert.equal(dangling.callsTo("j102_record_first_party_fact").length, 0);
});

test("H5: a sponsored agent cannot AUTHOR a partner-only business record", async () => {
  const db = dealSubjectDb();
  const store = createCreLifecycleStore({ db });
  for (const record_kind of ["closing_settlement", "winning_property_commitment",
    "deal_failure", "lifecycle_correction"]) {
    await assert.rejects(() => store.recordLifecycleFact({
      idempotency_key: `j102-fixture-${record_kind}`,
      fact: {
        record_kind, record_id: "rec-synthetic-1",
        subject_kind: record_kind === "winning_property_commitment" ? "assignment" : "deal",
        subject_id: record_kind === "winning_property_commitment"
          ? "asg-synthetic-1" : "deal-synthetic-1",
        closing_date: record_kind === "closing_settlement" ? T.late : undefined,
        reason: "synthetic fixture reason",
      },
    }, ctx(AGENT)),
    e => e instanceof V5J102StoreError && e.code === "partner_authored_record_kind_refused",
    `${record_kind} must not be agent-authored`);
  }
  assert.equal(db.calls.length, 0, "no partner-only authoring attempt reaches the database");

  // The agent-recordable kinds are untouched, so this is the four and not a
  // blanket suspicion of agents.
  const agentDb = dealSubjectDb({
    principal: { actor_slug: "codex", human: false, authorization_class: "sponsored_agent" },
  });
  const ok = await createCreLifecycleStore({ db: agentDb }).recordLifecycleFact({
    idempotency_key: "j102-fixture-invoice",
    fact: { record_kind: "invoice", record_id: "rec-synthetic-1",
      subject_kind: "deal", subject_id: "deal-synthetic-1" },
  }, ctx(AGENT));
  assert.equal(ok.decision, "allow");
  const envelope = JSON.parse(agentDb.paramsFor("j102_record_first_party_fact")[0]);
  // The class is DERIVED from the principal that wrote the row, so the record
  // itself carries the fact a later transition checks.
  assert.equal(envelope.record.recorded_by_authorization_class, "sponsored_agent");
});

test("M1: the typed fact fields are validated BEFORE the durable write", async () => {
  const store = createCreLifecycleStore({ db: dealSubjectDb() });
  const bad = (fact, code) => assert.rejects(
    () => store.recordLifecycleFact({ idempotency_key: "j102-fixture-1", fact }, ctx(JOE)),
    e => e instanceof V5J102StoreError && e.code === code, JSON.stringify(fact));
  const base = { record_id: "rec-synthetic-1", subject_kind: "deal",
    subject_id: "deal-synthetic-1" };

  // A non-string reason used to store durably and then make the record
  // UNREADABLE as evidence, at which point the kernel THREW instead of refusing.
  await bad({ ...base, record_kind: "deal_failure", reason: { text: "no" } }, "invalid_shape");
  await bad({ ...base, record_kind: "invoice", detail: 17 }, "invalid_shape");
  await bad({ ...base, record_kind: "closing_settlement", closing_date: "2026-02-31T00:00:00Z" },
    "invalid_timestamp");
  await bad({ ...base, record_kind: "closing_settlement", closing_date: "yesterday" },
    "invalid_timestamp");
  await bad({ ...base, record_kind: "invoice", supporting_document_id: "not a document id" },
    "invalid_identifier");
  // The two mandatory fields, taken from the evidence contracts rather than
  // restated: a dateless closing and a reasonless failure.
  await bad({ ...base, record_kind: "closing_settlement" }, "missing_field");
  await bad({ ...base, record_kind: "deal_failure" }, "missing_field");
  // And the binding must be the kind the record's own evidence contract names.
  await bad({ ...base, record_kind: "winning_property_commitment", subject_kind: "deal" },
    "first_party_record_subject_kind_mismatch");
});

test("a first-party record kind no evidence contract consumes is refused", async () => {
  const db = new FakeDb();
  const store = createCreLifecycleStore({ db });
  await assert.rejects(() => store.recordLifecycleFact({
    idempotency_key: "j102-fixture-1",
    fact: { record_kind: "freeform_note", record_id: "rec-synthetic-1",
      subject_kind: "deal", subject_id: "deal-synthetic-1" },
  }, ctx(JOE)), e => e instanceof V5J102StoreError && e.code === "unknown_first_party_record_kind");
  assert.equal(db.calls.length, 0);
});

test("BLOCK-2: an unbound document, and one bound to another subject, both refuse", async () => {
  // NO ASSOCIATION AT ALL. The document is authentic, current, fully executed and
  // pinned exactly; nothing says it is this deal's lease.
  const unbound = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState()) },
    document: storedDocument({ document_class: "lease" }),
    links: {},
  });
  const answer = await createCreLifecycleStore({ db: unbound }).recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "required_evidence_unavailable");
  assert.equal(answer.missing_fact, "j102_evidence_subject_association");
  assert.equal(answer.produced_by, "j102_record_evidence_subject_link");
  assert.equal(unbound.callsTo("j102_apply_transition").length, 0);

  // ASSOCIATED WITH SOMEBODY ELSE'S DEAL. The reader is asked about THIS deal and
  // answers null, so a lease executed for one client cannot mark another's.
  const elsewhere = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState()) },
    document: storedDocument({ document_class: "lease" }),
    links: {
      [["f01_document", "doc-synthetic-1", 1, D(1), "deal", "deal-other-client"].join("|")]:
        storedLink({ subject_id: "deal-other-client" }),
    },
  });
  const answer2 = await createCreLifecycleStore({ db: elsewhere }).recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE));
  assert.equal(answer2.missing_fact, "j102_evidence_subject_association");
  assert.equal(elsewhere.callsTo("j102_apply_transition").length, 0);
});

test("BLOCK-2: a stored record bound to a different deal cannot close this one", async () => {
  const db = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState({ execution_state: "executed" })) },
    facts: { closing_settlement: storedFact("closing_settlement",
      { subject_kind: "deal", subject_id: "deal-somebody-else", closing_date: T.late }) },
  });
  const answer = await createCreLifecycleStore({ db }).recordDealClosing({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [recordRef("final_closing_settlement")],
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.missing_fact, "first_party_record_bound_to_a_different_subject");
  assert.equal(db.callsTo("j102_apply_transition").length, 0);

  // A record predating the binding is refused rather than read as binding to
  // whatever it is asked about.
  const legacy = storedFact("closing_settlement", { closing_date: T.late });
  delete legacy.record.subject_id;
  legacy.record_digest = digest(legacy.record);
  const old = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState({ execution_state: "executed" })) },
    facts: { closing_settlement: legacy },
  });
  const answer2 = await createCreLifecycleStore({ db: old }).recordDealClosing({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [recordRef("final_closing_settlement")],
  }, ctx(JOE));
  assert.equal(answer2.missing_fact, "first_party_record_subject_binding");
});

test("M1: an unreadable stored field refuses as a policy answer, not as a thrown violation", async () => {
  const broken = storedFact("closing_settlement", { closing_date: T.late, reason: 17 });
  broken.record_digest = digest(broken.record);
  const db = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState({ execution_state: "executed" })) },
    facts: { closing_settlement: broken },
  });
  const answer = await createCreLifecycleStore({ db }).recordDealClosing({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [recordRef("final_closing_settlement")],
  }, ctx(JOE));
  assert.equal(answer.decision, "refuse", "a refusal, and not a V5J102Error out of the kernel");
  assert.equal(answer.missing_fact, "readable_first_party_record");
  assert.match(answer.missing_fact_reason, /reason/);
});

test("the association producer checks BOTH ends and is partner-only", async () => {
  const db = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState()) },
    document: storedDocument({ document_class: "lease" }),
  });
  const store = createCreLifecycleStore({ db });
  const answer = await store.recordEvidenceSubjectLink({
    idempotency_key: "j102-fixture-1",
    link: { evidence_source: "f01_document", document_id: "doc-synthetic-1",
      expected_version_no: 1, expected_content_digest: D(1),
      subject_kind: "deal", subject_id: "deal-synthetic-1" },
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.advances_lifecycle_state, false);
  assert.equal(answer.creates_document, false);
  assert.equal(answer.asserts_document_state, false);
  const envelope = JSON.parse(db.paramsFor("j102_record_evidence_subject_link")[0]);
  assert.equal(envelope.record.evidence_ref, "doc-synthetic-1");
  assert.equal(envelope.record.version_no, 1);
  assert.equal(envelope.record.content_digest, D(1));
  assert.equal(envelope.record.associated_by, "joe");
  assert.equal(envelope.record.associated_at, SERVER_NOW);
  assert.equal(envelope.record_digest, digest(envelope.record));

  // A pin F01 does not hold cannot be associated with anything.
  const movedPin = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState()) },
    document: storedDocument({ document_class: "lease",
      neon_identity: { document_id: "doc-synthetic-1", content_digest: D(1), version_no: 4 } }),
  });
  const answer2 = await createCreLifecycleStore({ db: movedPin }).recordEvidenceSubjectLink({
    idempotency_key: "j102-fixture-2",
    link: { evidence_source: "f01_document", document_id: "doc-synthetic-1",
      expected_version_no: 1, expected_content_digest: D(1),
      subject_kind: "deal", subject_id: "deal-synthetic-1" },
  }, ctx(JOE));
  assert.equal(answer2.reason_id, "evidence_pin_not_held");
  assert.equal(movedPin.callsTo("j102_record_evidence_subject_link").length, 0);

  // A subject this rail does not hold cannot be associated with anything either.
  const noSubject = new FakeDb({ subjects: {}, document: storedDocument() });
  const answer3 = await createCreLifecycleStore({ db: noSubject }).recordEvidenceSubjectLink({
    idempotency_key: "j102-fixture-3",
    link: { evidence_source: "f01_document", document_id: "doc-synthetic-1",
      expected_version_no: 1, expected_content_digest: D(1),
      subject_kind: "deal", subject_id: "deal-nonexistent" },
  }, ctx(JOE));
  assert.equal(answer3.reason_id, "subject_not_found");

  // And an agent may not say which transaction a document belongs to.
  await assert.rejects(() => store.recordEvidenceSubjectLink({
    idempotency_key: "j102-fixture-4",
    link: { evidence_source: "f01_document", document_id: "doc-synthetic-1",
      expected_version_no: 1, expected_content_digest: D(1),
      subject_kind: "deal", subject_id: "deal-synthetic-1" },
  }, ctx(AGENT)),
  e => e instanceof V5J102StoreError && e.code === "authority_only_operation_refused");
});

test("H1/H3: the capabilities this store does not wire are named, not implied", async () => {
  const named = V5_J102_UNWIRED_CAPABILITIES.map(c => c.capability).sort();
  assert.deepEqual(named, [
    "assignment_initialization",
    // The bootstrap gap, and it is now a gap at EVERY layer rather than a
    // direct-writer loophole: the SQL writer refuses to create the primary
    // subject a transition advances, so nothing anywhere seeds the first
    // relationship, assignment or negotiation. Named rather than worked around.
    "lifecycle_rail_has_no_bootstrap_at_any_layer",
    "ownership_and_freshness_exposure",
    "property_negotiation_initialization",
    "relationship_prospect_initialization",
    "reconciliation_runtime_integration",
  ].sort());
  // And the registration surface says the same thing per operation, so a reader
  // of `capabilities()` does not have to reach the registry to learn that every
  // write transition needs a subject nothing here creates.
  for (const entry of v5J102ToolRegistrations()) {
    if (!entry.write || v5J102StoreOperationSchemas()[entry.name].transition === null) continue;
    assert.equal(entry.requires_existing_primary_subject, true,
      `${entry.name} advances a subject that must already exist`);
    assert.equal(entry.primary_subject_created_by_operation, null,
      `${entry.name}'s primary subject is created by no operation in this slice`);
  }
  for (const entry of V5_J102_UNWIRED_CAPABILITIES) {
    assert.equal(entry.produced_by, "not_produced_by_this_slice");
    assert.ok(entry.why.length > 0);
  }

  // AND THE GAPS ARE REAL, asserted against behaviour rather than against the
  // list. Journey 1's first step cannot be taken here: the transition refuses
  // `subject_not_found` and creates nothing.
  for (const [handler, subject_kind, subject_id] of [
    ["openCreAssignment", "assignment", "asg-nonexistent"],
    ["recordLoiSubmission", "property_negotiation", "neg-nonexistent"],
  ]) {
    const db = new FakeDb({ subjects: {} });
    const answer = await createCreLifecycleStore({ db })[handler]({
      idempotency_key: "j102-fixture-1",
      subject_ref: { subject_kind, subject_id },
      evidence_refs: [recordRef("search_initiation")],
      declared: { mandate_scope: "search" },
    }, ctx(JOE));
    assert.equal(answer.reason_id, "subject_not_found");
    assert.equal(answer.records_written, 0);
    assert.equal(db.callsTo("j102_apply_transition").length, 0);
  }

  // No operation in the registered surface writes a reconciliation item or
  // returns an ownership projection, and the read kinds do not offer one.
  const store = createCreLifecycleStore({ db: new FakeDb() });
  assert.equal(typeof store.evaluateConcurrentEdit, "undefined");
  assert.equal(typeof store.projectOwnershipAndFreshness, "undefined");
  assert.equal(V5_J102_READ_KINDS.includes("ownership"), false);
  assert.equal(V5_J102_READ_KINDS.includes("automation"), false);
});

test("a Salesforce reference keeps its own labels, requires a real link target, and sets no state", async () => {
  const db = new FakeDb({
    subjects: { "assignment:asg-synthetic-1": storedSubject(assignmentState()) },
  });
  const store = createCreLifecycleStore({ db });
  const answer = await store.linkSalesforceReference({
    idempotency_key: "j102-fixture-1",
    opportunity_id: "006SYNTHETIC001",
    opportunity_name: "Synthetic Medical Group - New Location",
    opportunity_phase: "Pending Deal",
    observed_at: T.mid,
    linked_subject_kind: "assignment", linked_subject_id: "asg-synthetic-1",
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.sets_lifecycle_state, false);
  assert.equal(answer.phase_label_is_doctorcre_state, false);
  const envelope = JSON.parse(db.paramsFor("j102_record_salesforce_reference")[0]);
  assert.equal(envelope.record.opportunity_phase, "Pending Deal");
  assert.equal(envelope.record.opportunity_name, "Synthetic Medical Group - New Location");
  assert.equal(envelope.record.phase_label_is_doctorcre_state, false);

  // A link to a subject nobody holds is a dangling reference wearing the shape
  // of provenance.
  const dangling = new FakeDb({ subjects: {} });
  const answer2 = await createCreLifecycleStore({ db: dangling }).linkSalesforceReference({
    idempotency_key: "j102-fixture-2",
    opportunity_id: "006SYNTHETIC002", opportunity_name: "Synthetic",
    opportunity_phase: "Prospecting", observed_at: T.mid,
    linked_subject_kind: "deal", linked_subject_id: "deal-nonexistent",
  }, ctx(JOE));
  assert.equal(answer2.reason_id, "link_target_not_found");
  assert.equal(dangling.callsTo("j102_record_salesforce_reference").length, 0);
});

test("a correction binds the prior state digest and refuses without a durable correction record", async () => {
  const withRecord = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState()) },
    facts: { lifecycle_correction: storedFact("lifecycle_correction",
      { reason: "synthetic fixture: mis-keyed instrument kind" }) },
  });
  const store = createCreLifecycleStore({ db: withRecord });
  const answer = await store.recordLifecycleCorrection({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    correction_record_id: "rec-synthetic-1",
    corrected_fields: ["instrument_kind"],
    reason: "synthetic fixture: mis-keyed instrument kind",
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.append_only, true);
  assert.equal(answer.prior_state_preserved, true);
  assert.equal(answer.derived_from_assistant_text, false);
  const envelope = JSON.parse(withRecord.paramsFor("j102_record_correction")[0]);
  assert.equal(envelope.record.prior_state_digest, digest(dealState()));
  assert.equal(envelope.record.corrected_by, "joe");
  assert.equal(envelope.record.derived_from_assistant_text, false);

  // No durable record: an approval that lives only in a transcript is not one.
  const withoutRecord = new FakeDb({
    subjects: { "deal:deal-synthetic-1": storedSubject(dealState()) },
  });
  const answer2 = await createCreLifecycleStore({ db: withoutRecord }).recordLifecycleCorrection({
    idempotency_key: "j102-fixture-2",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    correction_record_id: "rec-nonexistent",
    corrected_fields: ["instrument_kind"],
    reason: "synthetic fixture reason",
  }, ctx(JOE));
  assert.equal(answer2.reason_id, "correction_record_not_found");
  assert.equal(answer2.missing_fact, "first_party_lifecycle_correction_record");
  assert.equal(withoutRecord.callsTo("j102_record_correction").length, 0);
});

// --- reads -----------------------------------------------------------------

test("a read names its kind, recomputes integrity, and refuses an unregistered kind", async () => {
  const db = new FakeDb({ read_body: { kind: "subject", body: { state: dealState() } } });
  const store = createCreLifecycleStore({ db });
  const answer = await store.readCreLifecycle({
    selector: { kind: "subject", subject_kind: "deal", subject_id: "deal-synthetic-1" },
  }, ctx(JOE));
  assert.equal(answer.decision, "allow");
  assert.equal(answer.integrity, "recomputed_not_trusted");
  assert.equal(answer.stale_fallback_permitted, false);
  const [kind, selectorJson] = db.paramsFor("j102_read");
  assert.equal(kind, "subject");
  assert.deepEqual(JSON.parse(selectorJson),
    { subject_kind: "deal", subject_id: "deal-synthetic-1" });

  await assert.rejects(() => store.readCreLifecycle({ selector: { kind: "everything" } }, ctx(JOE)),
    e => e instanceof V5J102StoreError && e.code === "unknown_read_kind");
});

test("every result states that nothing left the record layer", async () => {
  const db = leaseExecutionDb();
  const store = createCreLifecycleStore({ db });
  const answer = await store.recordDealExecution({
    idempotency_key: "j102-fixture-1",
    subject_ref: { subject_kind: "deal", subject_id: "deal-synthetic-1" },
    evidence_refs: [documentRef("executed_lease")],
  }, ctx(JOE));
  assert.equal(answer.schema_version, V5_J102_STORE_SCHEMA_VERSION);
  assert.equal(answer.tenant, ORGANIZATION_TENANT_ID);
  assert.equal(answer.provider_calls, 0);
  assert.equal(answer.salesforce_calls, 0);
  assert.equal(answer.documents_sent, 0);
  assert.equal(answer.creates_or_activates_tour, false);
  assert.equal(answer.effects.creates_effect, false);
  assert.equal(answer.effects.network_calls, 0);
});

test("the store requires an injected handle and opens no connection of its own", () => {
  assert.throws(() => createCreLifecycleStore({}),
    e => e instanceof V5J102StoreError && e.code === "database_handle_required");
  assert.throws(() => createCreLifecycleStore({ db: { notQuery: true } }),
    e => e instanceof V5J102StoreError && e.code === "database_handle_required");
});

// ---------------------------------------------------------------------------
// THE SQL ADMISSION PARITY SUITE.
//
// ops.j102_apply_transition is granted to carr_writer as well as carr_authority,
// and carr_writer always resolves to a sponsored agent. Everything the kernel and
// this store decide about WHO may perform a transition, WHICH subjects and fields
// it may write and WHICH evidence it requires is therefore a control on this
// caller and on no other, so ops/cre-lifecycle.candidate.sql carries the same
// contracts in ops.j102_admission_policy().
//
// TWO COPIES WITH NOTHING BETWEEN THEM ARE A FUTURE CONTRADICTION. These tests
// are the thing between them: they read the map out of the candidate SQL as data
// and assert it EQUALS the kernel's exports, contract by contract and field by
// field, and then assert that the writer actually calls the checks the map exists
// to feed. A map that is correct and unused would pass the first half and fail
// the second.
//
// NOTHING HERE EXECUTES SQL. The candidate is source, has never been applied, and
// these assertions are about its bytes.
// ---------------------------------------------------------------------------

const CANDIDATE_SQL = readFileSync(
  new URL("../../ops/cre-lifecycle.candidate.sql", import.meta.url), "utf8");

/** The admission map, read out of the candidate SQL's dollar-quoted JSON. */
function admissionPolicy() {
  const open = CANDIDATE_SQL.indexOf("$policy$");
  assert.ok(open > 0, "the candidate SQL carries a $policy$-quoted admission map");
  const close = CANDIDATE_SQL.indexOf("$policy$", open + "$policy$".length);
  assert.ok(close > open, "the admission map is closed by its own dollar quote");
  assert.equal(CANDIDATE_SQL.indexOf("$policy$", close + "$policy$".length), -1,
    "there is exactly ONE admission map in the file, so there is one thing to check");
  return JSON.parse(CANDIDATE_SQL.slice(open + "$policy$".length, close));
}

/** Which transitions each write operation may perform, from the store's own tables. */
function operationsForTransition() {
  const schemas = v5J102StoreOperationSchemas();
  const byTransition = new Map(V5_J102_TRANSITION_IDS.map(id => [id, []]));
  for (const operation of V5_J102_OPERATIONS) {
    const declared = schemas[operation].transition;
    if (declared === null) continue;
    const resolved = declared === "dispatch_on_instrument_kind"
      ? Object.values(V5_J102_INSTRUMENT_TRANSITIONS)
      : declared === "dispatch_on_declared_axis"
        ? Object.values(V5_J102_AXIS_TRANSITIONS)
        : [declared];
    for (const id of new Set(resolved)) {
      assert.ok(byTransition.has(id), `${operation} dispatches to a real transition (${id})`);
      byTransition.get(id).push(operation);
    }
  }
  return byTransition;
}

test("SQL parity: the admission map's transitions ARE the kernel's transition contracts", () => {
  const policy = admissionPolicy();
  assert.equal(policy.invented_business_policy, false);
  assert.deepEqual(Object.keys(policy.transitions).sort(), [...V5_J102_TRANSITION_IDS]);

  const operations = operationsForTransition();
  for (const id of V5_J102_TRANSITION_IDS) {
    const contract = v5J102TransitionContract(id);
    const admitted = policy.transitions[id];
    assert.equal(admitted.subject_kind, contract.subject_kind, `${id} subject_kind`);
    assert.deepEqual(admitted.permitted_actor_classes, contract.permitted_actor_classes,
      `${id} permitted_actor_classes`);
    assert.deepEqual(admitted.prerequisites, contract.prerequisites, `${id} prerequisites`);
    assert.deepEqual(admitted.instrument_kinds, contract.instrument_kinds,
      `${id} instrument_kinds`);
    assert.deepEqual(admitted.required_evidence_alternatives,
      contract.required_evidence_alternatives, `${id} required evidence`);
    assert.deepEqual(admitted.coupled_facts, contract.coupled_facts, `${id} coupled_facts`);
    assert.equal(admitted.creates_deal, contract.creates_deal, `${id} creates_deal`);
    // The operation pairing, from the store's own schemas and dispatch tables
    // rather than restated here, so the SQL map cannot pair an operation with a
    // transition this module would never route to it.
    assert.deepEqual([...admitted.operations].sort(), operations.get(id).sort(),
      `${id} is performed by exactly the operations this store routes to it`);
    assert.ok(admitted.operations.length > 0, `${id} is reachable from some operation`);
  }
});

test("SQL parity: `writes` is DERIVED from the coupled facts, plus three named derived fields", () => {
  const policy = admissionPolicy();
  // The escape hatch is exactly three fields wide, and every one of them is a
  // counter or a mirror the kernel moves and does not list as a coupled fact. If
  // it ever grows, this line is what makes that visible.
  assert.deepEqual([...policy.derived_fields].sort(), [
    "assignment.active_lease_draft_target_id",
    "assignment.open_negotiation_count",
    "relationship.active_engagement_count",
  ]);
  const allCoupled = new Set(
    V5_J102_TRANSITION_IDS.flatMap(id => v5J102TransitionContract(id).coupled_facts));
  for (const derived of policy.derived_fields) {
    assert.equal(allCoupled.has(derived), false,
      `${derived} is a derived field precisely because no transition couples it`);
  }

  for (const id of V5_J102_TRANSITION_IDS) {
    const contract = v5J102TransitionContract(id);
    const admitted = policy.transitions[id];
    // WHICH SUBJECTS: exactly the primary one plus the prefixes of the coupled
    // facts. Nothing else may be written by this transition, which is what stops
    // an allowed operation carrying an unrelated row beside its real one.
    const expectedKinds = [...new Set([
      contract.subject_kind,
      ...contract.coupled_facts.map(fact => fact.split(".")[0]),
    ])].sort();
    assert.deepEqual(Object.keys(admitted.writes).sort(), expectedKinds,
      `${id} writes exactly its primary subject and its coupled subjects`);

    for (const [kind, fields] of Object.entries(admitted.writes)) {
      const coupled = contract.coupled_facts
        .filter(fact => fact.startsWith(`${kind}.`))
        .map(fact => fact.slice(kind.length + 1));
      for (const fact of coupled) {
        assert.ok(fields.includes(fact),
          `${id} must be able to move the coupled fact ${kind}.${fact}`);
      }
      for (const field of fields) {
        assert.ok(coupled.includes(field) || policy.derived_fields.includes(`${kind}.${field}`),
          `${id} declares ${kind}.${field} movable, and it is neither a coupled fact nor a named derived field`);
      }
    }
  }
});

test("SQL parity: the admission map's evidence table IS the kernel's evidence contracts", () => {
  const policy = admissionPolicy();
  const kinds = Object.keys(policy.evidence).sort();
  assert.deepEqual(kinds, [...V5_J102_EVIDENCE_KINDS]);
  for (const kind of kinds) {
    const contract = v5J102EvidenceContract(kind);
    const admitted = policy.evidence[kind];
    assert.equal(admitted.source, contract.source, `${kind} source`);
    assert.equal(admitted.binds_subject_kind, contract.binds_subject_kind ?? null,
      `${kind} binds_subject_kind`);
    assert.equal(admitted.record_kind, contract.record_kind ?? null, `${kind} record_kind`);
    assert.equal(admitted.requires_author_class, contract.requires_author_class ?? null,
      `${kind} requires_author_class`);
    assert.equal(admitted.requires_closing_date, contract.requires_closing_date === true,
      `${kind} requires_closing_date`);
    assert.deepEqual(admitted.permitted_actor_classes, [...contract.permitted_actor_classes],
      `${kind} permitted_actor_classes`);
    // THE DOCUMENT'S STATE AXES. The pin proves the document has not moved and
    // says nothing about whether it is signed, delivered, effective or current --
    // so a lease that is authentically pinned, correctly associated and NOT
    // EXECUTED would have marked a deal executed on a direct call. Q077's active
    // signed ETL and Q078's lease signing are exactly these axes.
    assert.deepEqual(admitted.document_states ?? null, contract.document_states ?? null,
      `${kind} document_states`);
  }
  // And the four document-backed kinds really do carry axes, so the check above
  // is not vacuously satisfied by a table of nulls.
  const withStates = Object.keys(policy.evidence)
    .filter(kind => policy.evidence[kind].document_states != null).sort();
  assert.deepEqual(withStates, ["commission_agreement", "executed_lease",
    "signed_engagement_letter", "signed_purchase_contract", "submitted_loi"]);
});

test("SQL parity: the parent-reference fields cover every kind and close every coupled chain", () => {
  const policy = admissionPolicy();
  assert.deepEqual(Object.keys(policy.parent_reference_fields).sort(),
    [...V5_J102_SUBJECT_KINDS].sort());
  // Every transition that writes more than one subject must be EXPRESSIBLE as a
  // chain, or the writer's chain check would refuse a legitimate walk.
  for (const id of V5_J102_TRANSITION_IDS) {
    const contract = v5J102TransitionContract(id);
    const kinds = Object.keys(policy.transitions[id].writes);
    for (const kind of kinds) {
      if (kind === contract.subject_kind) continue;
      assert.ok(
        policy.parent_reference_fields[contract.subject_kind] !== null ||
        policy.parent_reference_fields[kind] !== null,
        `${id} couples ${kind} to a ${contract.subject_kind} and neither kind can name the other`);
    }
  }
});

test("BLOCK-1: the three partner-only transitions are partner-only in the SQL map too", () => {
  const policy = admissionPolicy();
  const schemas = v5J102StoreOperationSchemas();
  const partnerOnly = V5_J102_TRANSITION_IDS
    .filter(id => v5J102TransitionContract(id).permitted_actor_classes.length === 1 &&
      v5J102TransitionContract(id).permitted_actor_classes[0] === "verified_partner")
    .sort();
  assert.deepEqual(partnerOnly,
    ["cancel-pending-deal", "commit-winning-property", "record-deal-closing"]);
  for (const id of partnerOnly) {
    assert.deepEqual(policy.transitions[id].permitted_actor_classes, ["verified_partner"],
      `${id} is partner-only where a direct caller reaches it`);
    // And every operation that could name it is authorityOnly here, so the two
    // layers agree rather than one covering for the other.
    for (const operation of policy.transitions[id].operations) {
      assert.equal(schemas[operation].authorityOnly, true,
        `${operation} performs ${id} and must be authorityOnly in this store`);
    }
  }
});

test("BLOCK-1/BLOCK-2/HIGH-1: the writer actually USES the map, refusal by refusal", () => {
  // A correct map that nothing consults would pass every assertion above. These
  // are the call sites, named by the exact refusal each one raises.
  const writer = CANDIDATE_SQL.slice(
    CANDIDATE_SQL.indexOf("create or replace function ops.j102_apply_transition("));
  assert.ok(writer.length > 0, "the transition writer is in the candidate");
  for (const required of [
    // BLOCK-1: the class is DERIVED, and the three admission questions are asked.
    "ops.f01_principal() ->> 'authorization_class'",
    "j102_unknown_transition",
    "j102_operation_transition_mismatch",
    "j102_actor_class_not_permitted",
    // ROOT BLOCKER 1: the primary subject is loaded, never created, and only the
    // two coupled subjects the kernel creates may be created at all.
    "j102_primary_subject_creation_refused",
    "j102_subject_creation_not_permitted",
    "j102_primary_subject_not_found",
    "j102_coupled_subject_not_found",
    // BLOCK-2: which subjects, which fields, which chain, which prerequisites.
    "j102_subject_kind_not_written_by_transition",
    "j102_duplicate_proposed_subject_kind",
    "j102_primary_subject_not_proposed",
    "j102_coupled_subject_not_in_chain",
    "j102_prerequisite_not_met",
    "j102_instrument_kind_not_permitted",
    "j102_field_not_movable_by_transition",
    // ROOT BLOCKER 2: the whole coupled set, the evaluator's own prior
    // conditions, the exact target of every moved field, and the exact event set.
    "j102_required_subject_not_proposed",
    "j102_prior_condition_not_met",
    "j102_transition_effect_not_canonical",
    "j102_transition_effect_missing",
    "j102_created_subject_shape_mismatch",
    "j102_created_subject_field_not_canonical",
    "j102_required_context_not_met",
    "j102_event_set_mismatch",
    "j102_event_missing_or_wrong",
    "j102_event_not_produced_by_transition",
    // HIGH-1: the event's own claim and the subject it names.
    "j102_event_digest_mismatch",
    "j102_event_transition_mismatch",
    "j102_event_subject_not_advanced",
  ]) {
    assert.ok(writer.includes(required), `the writer raises or derives ${required}`);
  }
  // The effect interpreter is CALLED, in all three places the map declares a
  // value: a creation's shape, a prior condition's comparand, and a moved field.
  assert.ok(writer.split("ops.j102_expected_value(").length - 1 >= 4,
    "the writer computes its expected values rather than carrying a second opinion");
  // And the receipt no longer reports a bypass as a caveat.
  assert.ok(writer.includes("'primary_subject_created', false"));
  assert.ok(writer.includes("'prerequisites_checked', true"));
  assert.ok(writer.includes("'transition_effects_enforced', true"));
  assert.ok(writer.includes("'required_event_set_enforced', true"));
  // The recheck is handed the primary subject rather than left to check the
  // manifest against itself. This is the whole of BLOCKER-2's close.
  assert.match(writer,
    /ops\.j102_recheck_evidence\(\s*p_evidence_recheck,\s*p_transition_id,\s*v_primary_kind,\s*v_primary_id\)/);
  // And the receipt no longer claims more than the code enforces.
  assert.ok(writer.includes("'evidence_bound_to_primary_subject', true"));
  assert.ok(writer.includes("'request_digest_scope', 'caller_supplied_intent_digest_not_recomputed_here'"),
    "M-b: the caller's intent digest is named as caller-supplied, not as a database proof");
  assert.ok(writer.includes("'committed_content_digest_source', 'recomputed_from_committed_rows'"),
    "M-b: what the database DOES vouch for is recomputed and reported separately");
});

test("BLOCK-2: the evidence recheck binds to the primary subject and closes the manifest", () => {
  const start = CANDIDATE_SQL.indexOf("create or replace function ops.j102_recheck_evidence(");
  assert.ok(start > 0);
  const recheck = CANDIDATE_SQL.slice(start,
    CANDIDATE_SQL.indexOf("create or replace function ops.j102_apply_transition("));
  for (const required of [
    "j102_primary_subject_unresolved",
    "j102_unknown_evidence_kind",
    "j102_duplicate_evidence_kind",
    "j102_evidence_source_mismatch",
    "j102_actor_class_not_permitted_for_evidence",
    "j102_evidence_binding_required",
    "j102_evidence_bound_to_wrong_subject_kind",
    // Membership in the lock set is NOT the check; identity with the advancing
    // subject is. This is the refusal that stops "add deal B to the CAS map and
    // propose deal A".
    "j102_evidence_not_bound_to_primary_subject",
    "j102_evidence_record_kind_mismatch",
    "j102_evidence_author_class_not_permitted",
    "j102_evidence_alternative_not_satisfied",
    "j102_closing_date_in_the_future",
    // The document's own state axes, which the pin does not cover.
    "j102_document_state_not_met",
  ]) {
    assert.ok(recheck.includes(required), `the recheck raises ${required}`);
  }
  // The one-argument form is gone, so nothing can establish "the evidence was
  // exact" without being told which subject it was exact FOR.
  assert.ok(CANDIDATE_SQL.includes("drop function if exists ops.j102_recheck_evidence(jsonb);"));
  assert.equal(CANDIDATE_SQL.includes("ops.j102_recheck_evidence(p_recheck jsonb)\n"), false);
});

test("HIGH-2: the candidate refuses an incompatible existing shape and alters nothing", () => {
  // No ALTER, no backfill, no ordinal. The remedy the file names is a fresh
  // database or an already-exact one, and it says so instead of no-opping.
  assert.equal(/\balter\s+table\b/i.test(CANDIDATE_SQL), false,
    "the candidate contains no ALTER TABLE; it is source, not a migration");
  assert.equal(/\bdrop\s+table\b/i.test(CANDIDATE_SQL), false,
    "and it drops no relation, so nothing it may be applied beside loses data");
  // The only UPDATE anywhere is the idempotency settle and the upsert's own
  // ON CONFLICT clause. Neither touches a lifecycle row that was already there,
  // which is what "no backfill program" means concretely.
  const updates = CANDIDATE_SQL.match(/\bupdate\s+ops\.j102_[a-z_]+/gi) ?? [];
  assert.deepEqual([...new Set(updates.map(u => u.toLowerCase().replace(/\s+/g, " ")))],
    ["update ops.j102_idempotency"]);
  assert.ok(CANDIDATE_SQL.includes("j102_incompatible_existing_schema"));
  for (const column of ["bound_subject_kind", "bound_subject_id", "recorded_by_class"]) {
    // The three columns the corrections added are exactly the ones an earlier
    // candidate's table would be missing, so they must be in the preflight list.
    const preflight = CANDIDATE_SQL.slice(0, CANDIDATE_SQL.indexOf("-- Guards."));
    assert.ok(preflight.includes(`'${column}'`),
      `the preflight names ${column}, which an earlier candidate's table would lack`);
  }
});

test("M-c/M-f: the association pins its version, and a future Salesforce observation refuses", () => {
  assert.ok(CANDIDATE_SQL.includes("j102_link_version_matches_envelope"),
    "M-c: version_no is CHECK-bound to the envelope like every other binding column");
  assert.ok(CANDIDATE_SQL.includes("j102_link_pin_not_held: F01 holds document % at version %"),
    "M-c: the association's version is compared to F01's, not only its content digest");
  assert.ok(CANDIDATE_SQL.includes("j102_observed_at_in_the_future"),
    "M-f: Salesforce cannot claim to have observed its own record after our clock");
});

test("M-a: capabilities report the association prerequisite that actually binds", () => {
  const registrations = v5J102ToolRegistrations();
  const byName = Object.fromEntries(registrations.map(entry => [entry.name, entry]));

  // The document-backed transitions: `authorityOnly: false` and yet not completable
  // by an agent alone, because the association their evidence needs is
  // partner-written. Saying only the first was the misreport.
  for (const name of ["record-representation-agreement", "record-deal-execution",
    "record-loi-submission", "record-loi-acceptance"]) {
    const entry = byName[name];
    assert.equal(entry.authorityOnly, false, `${name} is not itself authorityOnly`);
    assert.equal(entry.requires_partner_written_evidence_association, true,
      `${name} rests on evidence that needs a partner-written association`);
    assert.equal(entry.association_prerequisite.written_by_operation,
      "record-evidence-subject-link");
    assert.equal(entry.association_prerequisite.written_by_authorization_class,
      "verified_partner");
    assert.ok(entry.association_prerequisite.pinned_on.includes("version_no"),
      `${name}'s prerequisite states the version pin, which is the sharp edge`);
    assert.equal(v5J102StoreOperationSchemas()["record-evidence-subject-link"].authorityOnly,
      true, "and the operation that writes one really is authorityOnly");
  }
  // The record-backed ones need no association at all: a first-party record
  // carries its own typed binding.
  for (const name of ["open-cre-assignment", "record-diligence-outcome",
    "record-deal-closing", "cancel-pending-deal"]) {
    assert.equal(byName[name].requires_partner_written_evidence_association, false,
      `${name} rests on first-party records, which bind themselves`);
    assert.equal(byName[name].association_prerequisite, null);
  }
  // Nothing was widened: the authorityOnly set is unchanged.
  const schemas = v5J102StoreOperationSchemas();
  assert.deepEqual(V5_J102_OPERATIONS.filter(n => schemas[n].authorityOnly).sort(),
    ["cancel-pending-deal", "commit-winning-property", "record-deal-closing",
      "record-evidence-subject-link", "record-lifecycle-correction"]);
  // A dispatching operation reports BOTH of the transitions it can reach.
  assert.deepEqual(byName["record-deal-execution"].dispatched_transitions,
    ["record-lease-execution", "record-purchase-contract-execution"]);
  assert.deepEqual(byName["record-deal-axis"].dispatched_transitions,
    ["record-commission-agreement", "record-completion", "record-invoice-issued",
      "record-payment"]);
});

test("the SQL fixture stays UNEXECUTED source, and rolls every synthetic row back", () => {
  const fixture = readFileSync(
    new URL("./cre-lifecycle-postgres.sql", import.meta.url), "utf8");
  assert.ok(fixture.trimEnd().endsWith("rollback;"),
    "the fixture ends in a rollback, so running it leaves nothing behind");
  assert.equal(/^\s*commit\s*;/im.test(fixture), false,
    "the fixture never commits");
  assert.ok(fixture.includes("THIS FILE HAS NOT BEEN EXECUTED"),
    "the fixture says plainly that it is source and not a result");
  // The adversarial groups, named so a future edit that drops one is visible
  // here rather than only in a diff.
  for (const group of ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10"]) {
    assert.ok(fixture.includes(`=== ${group}:`), `the fixture carries adversarial group ${group}`);
  }
  // AND IT CLAIMS NO POSITIVE WALK. Nothing seeds the first lifecycle subject,
  // the writer refuses to create one, and the fixture says that rather than
  // showing a walk that cannot run.
  assert.ok(fixture.includes("=== P0:"),
    "the fixture carries the group that NAMES the missing bootstrap");
  assert.ok(fixture.includes("j102_fixture_bootstrap_absent"),
    "and it names the prerequisite by the same word everywhere");
  assert.equal(/=== P[12]:/.test(fixture), false,
    "the positive walks are gone rather than left in place unable to run");
});

// ---------------------------------------------------------------------------
// THE TRANSITION-EFFECT PARITY SUITE.
//
// The map above says which FIELDS a transition may move. That is not the same
// question as what it moves them TO, and the difference is the whole of the
// second root correction: a routine `open-assignment` may write
// `assignment_phase`, and `assignment_phase: "committed"` is a commitment
// performed without any of a commitment's evidence.
//
// So `subjects` and `events` in the admission map carry the EXACT result, and
// these tests prove the transcription by running the kernel and requiring the
// map to PREDICT its answer:
//
//   1. the kernel evaluates a real transition and returns proposed_state/events,
//   2. the map's own rules are interpreted against the same inputs,
//   3. the two must agree field by field and event by event.
//
// NOTHING HERE MIRRORS THE MAP AT ITSELF. The oracle is the kernel's evaluator,
// which knows nothing about the SQL; a map that drifts from it fails here.
//
// WHAT THIS CANNOT PROVE, said plainly: the interpreter below is a
// TRANSCRIPTION of ops.j102_expected_value and of the writer's comparison loop,
// written in JavaScript because no database is reachable from this suite. It
// proves the MAP is right. That the PL/pgSQL implementation of the same
// algorithm behaves identically is unexecuted, and is the SQL fixture's job.
// ---------------------------------------------------------------------------

const MISSING = Symbol("missing");
const at = (object, field) =>
  object !== undefined && object !== null &&
  Object.prototype.hasOwnProperty.call(object, field) ? object[field] : MISSING;
const orNull = value => (value === MISSING ? null : value);
const show = value => (value === MISSING ? "absent" : JSON.stringify(value));

/** ops.j102_expected_value, transcribed. Same three answer shapes. */
function expectedValue(effect, ctx) {
  const from = source =>
    source === "prior" ? ctx.prior : source === "proposed" ? ctx.proposed : ctx.context;
  switch (effect.op) {
    case "const":
      return { kind: "exact", value: orNull(at(effect, "value")) };
    case "unbound":
      return { kind: "unbound" };
    case "proposed_subject_id":
      return { kind: "exact", value: orNull(at(ctx.ids, effect.subject)) };
    case "subject_field":
      return { kind: "exact", value: orNull(at(at(from(effect.source), effect.subject), effect.field)) };
    case "evidence_fact":
      return { kind: "exact",
        value: orNull(at(at(ctx.facts, effect.evidence_kind), effect.fact)) };
    case "supplied_evidence_kind": {
      const kinds = Object.keys(ctx.facts);
      return kinds.length === 0
        ? { kind: "any_of", values: [] } : { kind: "exact", value: kinds[0] };
    }
    case "prior_plus": {
      const observed = at(at(ctx.prior, effect.subject), effect.field);
      if (typeof observed !== "number") return { kind: "any_of", values: [] };
      return { kind: "exact", value: observed + effect.add };
    }
    case "prior_plus_conditional": {
      const observed = at(at(ctx.prior, effect.subject), effect.field);
      if (typeof observed !== "number") return { kind: "any_of", values: [] };
      const when = effect.when;
      const seen = at(at(from(when.source), when.subject), when.field);
      return { kind: "exact",
        value: seen === when.equals ? observed + effect.add : observed };
    }
    case "case_on_field": {
      const observed = at(at(from(effect.source), effect.subject), effect.field);
      for (const [value, branch] of Object.entries(effect.cases)) {
        if (observed === value) return expectedValue(branch, ctx);
      }
      return expectedValue(effect.default, ctx);
    }
    case "case_on_evidence": {
      for (const kind of Object.keys(ctx.facts)) {
        if (effect.cases[kind] !== undefined) return expectedValue(effect.cases[kind], ctx);
      }
      return { kind: "any_of", values: [] };
    }
    case "one_of": {
      const values = effect.values.filter(candidate => {
        for (const guard of effect.guards ?? []) {
          if (guard.value !== candidate) continue;
          const requires = guard.requires;
          const observed = at(at(from(requires.source), requires.subject), requires.field);
          if (Object.prototype.hasOwnProperty.call(requires, "equals") &&
              observed !== requires.equals) return false;
          if (Object.prototype.hasOwnProperty.call(requires, "at_least") &&
              (typeof observed !== "number" || observed < requires.at_least)) return false;
        }
        if (effect.differs_from_prior === true) {
          const before = at(at(ctx.prior, effect.prior_subject), effect.prior_field);
          if (before === candidate) return false;
        }
        return true;
      });
      return { kind: "any_of", values };
    }
    default:
      throw new Error(`the admission map uses an effect op this suite cannot read: ${effect.op}`);
  }
}

/** The writer's comparison loop, transcribed. Returns every complaint it makes. */
function complaintsAgainstPolicy(policy, transition_id, proposed_state, ctx) {
  const rules = policy.transitions[transition_id].subjects;
  const complaints = [];
  for (const kind of Object.keys(rules)) {
    if (proposed_state[kind] === undefined) {
      complaints.push(`j102_required_subject_not_proposed: ${kind}`);
    }
  }
  for (const kind of Object.keys(proposed_state)) {
    if (rules[kind] === undefined) {
      complaints.push(`j102_subject_kind_not_written_by_transition: ${kind}`);
    }
  }
  for (const [kind, rule] of Object.entries(rules)) {
    const state = proposed_state[kind];
    if (state === undefined) continue;
    if (rule.mode === "create") {
      for (const field of new Set([...Object.keys(rule.creation_shape), ...Object.keys(state)])) {
        if (rule.creation_shape[field] === undefined || at(state, field) === MISSING) {
          complaints.push(`j102_created_subject_shape_mismatch: ${kind}.${field}`);
          continue;
        }
        const expected = expectedValue(rule.creation_shape[field], ctx);
        const actual = at(state, field);
        if (expected.kind === "exact" && actual !== expected.value) {
          complaints.push(
            `j102_created_subject_field_not_canonical: ${kind}.${field} is ${show(actual)}, not ${show(expected.value)}`);
        } else if (expected.kind === "any_of" && !expected.values.includes(actual)) {
          complaints.push(`j102_created_subject_field_not_canonical: ${kind}.${field} is ${show(actual)}`);
        }
      }
      continue;
    }
    const prior = ctx.prior[kind];
    for (const condition of rule.prior_conditions ?? []) {
      const observed = at(prior, condition.field);
      if (condition.must_be_null === true && observed !== null) {
        complaints.push(`j102_prior_condition_not_met: ${kind}.${condition.field}`);
      }
      if (Object.prototype.hasOwnProperty.call(condition, "equals") &&
          observed !== condition.equals) {
        complaints.push(`j102_prior_condition_not_met: ${kind}.${condition.field}`);
      }
      if (condition.in !== undefined && !condition.in.includes(observed)) {
        complaints.push(`j102_prior_condition_not_met: ${kind}.${condition.field}`);
      }
      if (condition.not_in !== undefined && condition.not_in.includes(observed)) {
        complaints.push(`j102_prior_condition_not_met: ${kind}.${condition.field}`);
      }
      if (condition.equals_subject_id !== undefined &&
          observed !== orNull(at(ctx.ids, condition.equals_subject_id))) {
        complaints.push(`j102_prior_condition_not_met: ${kind}.${condition.field}`);
      }
      if (condition.null_or_matches !== undefined) {
        const expected = expectedValue(condition.null_or_matches, ctx);
        if (observed !== null && observed !== expected.value) {
          complaints.push(`j102_prior_condition_not_met: ${kind}.${condition.field}`);
        }
      }
    }
    for (const field of new Set([...Object.keys(prior ?? {}), ...Object.keys(state)])) {
      const effect = rule.effects[field];
      const actual = at(state, field);
      if (effect === undefined) {
        if (actual !== at(prior, field)) {
          complaints.push(`j102_field_not_movable_by_transition: ${kind}.${field}`);
        }
        continue;
      }
      const expected = expectedValue(effect, ctx);
      if (expected.kind === "unbound") continue;
      if (expected.kind === "exact" && actual !== expected.value) {
        complaints.push(
          `j102_transition_effect_not_canonical: ${kind}.${field} is ${show(actual)}, not ${show(expected.value)}`);
      } else if (expected.kind === "any_of" && !expected.values.includes(actual)) {
        complaints.push(
          `j102_transition_effect_not_canonical: ${kind}.${field} is ${show(actual)}, not one of ${JSON.stringify(expected.values)}`);
      }
    }
    for (const field of Object.keys(rule.effects)) {
      if (at(state, field) === MISSING) {
        complaints.push(`j102_transition_effect_missing: ${kind}.${field}`);
      }
    }
  }
  return complaints;
}

/** The writer's event-set check, transcribed. */
function eventComplaints(policy, transition_id, events, ctx) {
  const required = policy.transitions[transition_id].events.map(spec => ({
    event_kind: spec.event_kind_from === undefined
      ? spec.event_kind
      : spec.event_kind_from.prefix +
        String(orNull(at(at(ctx.proposed, spec.event_kind_from.subject),
          spec.event_kind_from.field))),
    subject_kind: spec.subject,
    subject_id: orNull(at(ctx.ids, spec.subject)),
  }));
  const supplied = events.map(event => ({
    event_kind: event.event_kind, subject_kind: event.subject_kind, subject_id: event.subject_id,
  }));
  const complaints = [];
  if (supplied.length !== required.length) {
    complaints.push(`j102_event_set_mismatch: ${supplied.length} for ${required.length}`);
  }
  const remaining = [...supplied];
  for (const want of required) {
    const index = remaining.findIndex(have =>
      have.event_kind === want.event_kind && have.subject_kind === want.subject_kind &&
      have.subject_id === want.subject_id);
    if (index === -1) {
      complaints.push(`j102_event_missing_or_wrong: ${want.event_kind} on ${want.subject_kind}`);
      continue;
    }
    remaining.splice(index, 1);
  }
  for (const extra of remaining) {
    complaints.push(`j102_event_not_produced_by_transition: ${extra.event_kind}`);
  }
  return complaints;
}

// --- the walks the kernel is actually run through --------------------------

// The kernel's own actor shape, which is not the store's caller context: this is
// what evaluateLifecycleTransition takes, server-derived class and all.
const KERNEL_PARTNER = Object.freeze({
  slug: "joe", human: true, authorization_class: "verified_partner",
  derived_by: "authenticated_handler_context",
});
const KERNEL_AGENT = Object.freeze({
  slug: "cre-agent", human: false, authorization_class: "sponsored_agent",
  derived_by: "server_established_transaction_context",
});
const NOW = "2026-09-09T12:00:00.000Z";

const provenance = reader => ({
  loaded_by: V5_J102_EVIDENCE_LOADER, reader, loaded_at: T.mid,
  integrity: V5_J102_EVIDENCE_INTEGRITY,
});

const kernelDocument = (evidence_kind, bound, over = {}) => ({
  evidence_kind, source: "f01_document", reference: "j102-doc-1",
  subject_binding: { ...bound, bound_by: "stored_evidence_subject_link", binding_digest: D(21) },
  document: {
    document_id: "j102-doc-1", document_class: "agreement", version_no: 3, content_digest: D(22),
    preparation_state: "prepared", delivery_state: "delivered",
    signature_state: "fully_executed", validity_state: "effective", version_state: "current",
    effective_from: null, effective_to: null, ...over,
  },
  provenance: provenance("ops.f01_read.document"),
});

const kernelArtifact = (evidence_kind, bound) => ({
  evidence_kind, source: "f01_corporate_artifact", reference: D(23),
  subject_binding: { ...bound, bound_by: "stored_evidence_subject_link", binding_digest: D(24) },
  artifact: {
    artifact_digest: D(23), content_digest: D(23), source_system: "counterparty-mail",
    evidence_class: "counterparty_acceptance", observed_at: T.mid,
  },
  provenance: provenance("ops.f01_stored_artifact"),
});

const kernelRecord = (evidence_kind, record_kind, bound, over = {}) => ({
  evidence_kind, source: "first_party_record", reference: "j102-record-1",
  subject_binding: { ...bound, bound_by: "first_party_record", binding_digest: D(25) },
  record: {
    record_kind, record_id: "j102-record-1", content_digest: D(25),
    recorded_by: "joe", recorded_by_authorization_class: "verified_partner",
    recorded_at: T.mid, reason: null, detail: null, closing_date: null,
    supporting_document_id: null, ...over,
  },
  provenance: provenance("ops.j102_first_party_record"),
});

/** The facts the SQL recheck returns for one evidence item, from the same row. */
const factsFor = evidence => {
  const facts = {};
  for (const item of evidence) {
    if (item.source === "first_party_record") {
      facts[item.evidence_kind] = {
        record_kind: item.record.record_kind, record_id: item.record.record_id,
        closing_date: item.record.closing_date, reason: item.record.reason,
        subject_kind: item.subject_binding.subject_kind,
        subject_id: item.subject_binding.subject_id,
      };
    } else if (item.source === "f01_document") {
      facts[item.evidence_kind] = {
        document_id: item.document.document_id, version_no: item.document.version_no,
        content_digest: item.document.content_digest,
      };
    } else {
      facts[item.evidence_kind] = { artifact_digest: item.artifact.artifact_digest };
    }
  }
  return facts;
};

const REL = { subject_kind: "relationship", subject_id: "j102-rel-1",
  relationship_state: "prospect", active_engagement_count: 0 };
const CLIENT = { ...REL, relationship_state: "client", active_engagement_count: 1 };
const ENG = { subject_kind: "engagement", subject_id: "j102-eng-1",
  relationship_id: "j102-rel-1", engagement_state: "active",
  representation_basis: "signed_engagement_letter", effective_from: null, effective_to: null };
const ASG = (over = {}) => ({ subject_kind: "assignment", subject_id: "j102-asg-1",
  engagement_id: "j102-eng-1", assignment_phase: "research", open_negotiation_count: 0,
  selected_property_id: null, active_lease_draft_target_id: null, pending_deal_id: null,
  multi_target_exception_ref: null, ...over });
const NEG = (over = {}) => ({ subject_kind: "property_negotiation", subject_id: "j102-neg-1",
  assignment_id: "j102-asg-1", property_id: "j102-prop-1", negotiation_state: "loi_drafted",
  ...over });
const DEAL = (over = {}) => ({ subject_kind: "deal", subject_id: "j102-deal-1",
  assignment_id: "j102-asg-1", property_id: "j102-prop-1", instrument_kind: "lease",
  deal_state: "pending", execution_state: "unexecuted", diligence_state: "not_applicable",
  closing_state: "not_reached", commission_agreement_state: "absent",
  invoice_state: "not_invoiced", payment_state: "unpaid", completion_state: "open",
  cancellation_reason: null, closing_date: null, ...over });

const relationshipBinding = { subject_kind: "relationship", subject_id: "j102-rel-1" };
const assignmentBinding = { subject_kind: "assignment", subject_id: "j102-asg-1" };
const negotiationBinding = { subject_kind: "property_negotiation", subject_id: "j102-neg-1" };
const dealBinding = { subject_kind: "deal", subject_id: "j102-deal-1" };

/**
 * One walk per transition the kernel can be driven through here.
 *
 * `approved_representation_equivalent` has no walk on purpose: no producer
 * writes the typed approval it rests on, ops.j102_typed_approval is granted to
 * nobody and always raises, and the store refuses the path with the missing fact
 * named. Manufacturing one here to widen the table would be inventing exactly
 * the authority that record is supposed to carry.
 */
const WALKS = [
  { transition_id: "establish-client-and-engagement", actor: KERNEL_PARTNER,
    subject: REL, related: {}, declared: { new_subject_id: "j102-eng-1" },
    evidence: [kernelDocument("signed_engagement_letter", relationshipBinding)],
    prior: { relationship: REL }, context: {} },
  { transition_id: "open-assignment", actor: KERNEL_AGENT,
    subject: ASG(), related: { engagement: ENG, relationship: CLIENT },
    declared: { mandate_scope: "search" },
    evidence: [kernelRecord("search_initiation", "assignment_mandate", assignmentBinding,
      { recorded_by_authorization_class: "sponsored_agent" })],
    prior: { assignment: ASG() }, context: { engagement: ENG, relationship: CLIENT } },
  { transition_id: "record-loi-submission", actor: KERNEL_AGENT,
    subject: NEG(), related: { assignment: ASG({ assignment_phase: "search" }) }, declared: {},
    evidence: [kernelDocument("submitted_loi", negotiationBinding)],
    prior: { property_negotiation: NEG(), assignment: ASG({ assignment_phase: "search" }) },
    context: {} },
  { transition_id: "record-loi-acceptance", actor: KERNEL_AGENT,
    subject: NEG({ negotiation_state: "loi_submitted" }), related: {}, declared: {},
    evidence: [kernelArtifact("counterparty_loi_acceptance", negotiationBinding)],
    prior: { property_negotiation: NEG({ negotiation_state: "loi_submitted" }) }, context: {} },
  { transition_id: "commit-winning-property", actor: KERNEL_PARTNER,
    subject: ASG({ assignment_phase: "negotiation", open_negotiation_count: 2 }),
    related: { property_negotiation: NEG({ negotiation_state: "loi_accepted" }) },
    declared: { instrument_kind: "lease", new_deal_id: "j102-deal-1" },
    evidence: [kernelRecord("winner_selection_commitment", "winning_property_commitment",
      assignmentBinding)],
    prior: { assignment: ASG({ assignment_phase: "negotiation", open_negotiation_count: 2 }),
      property_negotiation: NEG({ negotiation_state: "loi_accepted" }) },
    context: {} },
  { transition_id: "record-lease-execution", actor: KERNEL_AGENT,
    subject: DEAL(), related: {}, declared: {},
    evidence: [kernelDocument("executed_lease", dealBinding)],
    prior: { deal: DEAL() }, context: {} },
  { transition_id: "record-purchase-contract-execution", actor: KERNEL_AGENT,
    subject: DEAL({ instrument_kind: "purchase" }), related: {}, declared: {},
    evidence: [kernelDocument("signed_purchase_contract", dealBinding)],
    prior: { deal: DEAL({ instrument_kind: "purchase" }) }, context: {} },
  { transition_id: "record-diligence-outcome", actor: KERNEL_AGENT,
    subject: DEAL({ instrument_kind: "purchase", execution_state: "executed",
      diligence_state: "in_progress" }),
    related: {}, declared: { diligence_result: "satisfied" },
    evidence: [kernelRecord("diligence_outcome", "diligence_outcome", dealBinding,
      { recorded_by_authorization_class: "sponsored_agent" })],
    prior: { deal: DEAL({ instrument_kind: "purchase", execution_state: "executed",
      diligence_state: "in_progress" }) }, context: {} },
  { transition_id: "record-deal-closing", actor: KERNEL_PARTNER,
    subject: DEAL({ execution_state: "executed", diligence_state: "satisfied" }),
    related: {}, declared: {},
    evidence: [kernelRecord("final_closing_settlement", "closing_settlement", dealBinding,
      { closing_date: T.early })],
    prior: { deal: DEAL({ execution_state: "executed", diligence_state: "satisfied" }) },
    context: {} },
  { transition_id: "cancel-pending-deal", actor: KERNEL_PARTNER,
    subject: DEAL(),
    related: { assignment: ASG({ assignment_phase: "committed", open_negotiation_count: 1,
      selected_property_id: "j102-prop-1", active_lease_draft_target_id: "j102-prop-1",
      pending_deal_id: "j102-deal-1" }) },
    declared: { return_phase: "negotiation" },
    evidence: [kernelRecord("deal_failure_record", "deal_failure", dealBinding,
      { reason: "the counterparty withdrew before lease signature" })],
    prior: { deal: DEAL(), assignment: ASG({ assignment_phase: "committed",
      open_negotiation_count: 1, selected_property_id: "j102-prop-1",
      active_lease_draft_target_id: "j102-prop-1", pending_deal_id: "j102-deal-1" }) },
    context: {} },
  { transition_id: "record-commission-agreement", actor: KERNEL_AGENT,
    subject: DEAL(), related: {}, declared: {},
    evidence: [kernelDocument("commission_agreement", dealBinding)],
    prior: { deal: DEAL() }, context: {} },
  { transition_id: "record-invoice-issued", actor: KERNEL_AGENT,
    subject: DEAL(), related: {}, declared: {},
    evidence: [kernelRecord("invoice_issued", "invoice", dealBinding,
      { recorded_by_authorization_class: "sponsored_agent" })],
    prior: { deal: DEAL() }, context: {} },
  { transition_id: "record-payment", actor: KERNEL_AGENT,
    subject: DEAL({ payment_state: "partially_paid" }), related: {},
    declared: { payment_level: "paid" },
    evidence: [kernelRecord("payment_received", "payment", dealBinding,
      { recorded_by_authorization_class: "sponsored_agent" })],
    prior: { deal: DEAL({ payment_state: "partially_paid" }) }, context: {} },
  { transition_id: "record-completion", actor: KERNEL_AGENT,
    subject: DEAL(), related: {}, declared: {},
    evidence: [kernelRecord("completion_recorded", "completion", dealBinding,
      { recorded_by_authorization_class: "sponsored_agent" })],
    prior: { deal: DEAL() }, context: {} },
];

function runWalk(walk) {
  const answer = evaluateLifecycleTransition({
    tenant: ORGANIZATION_TENANT_ID,
    transition_id: walk.transition_id,
    subject: walk.subject,
    related: walk.related,
    evidence: walk.evidence,
    actor: walk.actor,
    declared: walk.declared,
    now: NOW,
  });
  assert.equal(answer.decision, "allow",
    `${walk.transition_id} must be an ALLOW for the parity check to mean anything (${answer.reason_id})`);
  const ids = Object.fromEntries(
    Object.entries(answer.proposed_state).map(([kind, state]) => [kind, state.subject_id]));
  const ctx = {
    prior: walk.prior, proposed: answer.proposed_state, context: walk.context,
    ids, facts: factsFor(walk.evidence),
  };
  return { answer, ctx };
}

test("SQL parity: every transition the map declares has a subject rule set and an event set", () => {
  const policy = admissionPolicy();
  assert.equal(policy.subject_creation, "coupled_only_never_the_primary_subject");
  for (const id of V5_J102_TRANSITION_IDS) {
    const contract = v5J102TransitionContract(id);
    const admitted = policy.transitions[id];
    // The subject rules cover EXACTLY the subjects `writes` covers, which the
    // test above has already tied to the kernel's coupled facts.
    assert.deepEqual(Object.keys(admitted.subjects).sort(), Object.keys(admitted.writes).sort(),
      `${id} declares a rule for every subject it writes`);
    assert.equal(admitted.subjects[contract.subject_kind].role, "primary",
      `${id}'s primary subject is the kernel's subject_kind`);
    assert.equal(admitted.subjects[contract.subject_kind].mode, "update",
      `${id} advances a ${contract.subject_kind} that already exists; the kernel never creates one`);
    for (const [kind, rule] of Object.entries(admitted.subjects)) {
      if (kind === contract.subject_kind) continue;
      assert.equal(rule.role, "coupled");
      assert.ok(["update", "create"].includes(rule.mode));
    }
    // Every field `writes` names is a field with a declared TARGET, and every
    // field with a target is a field `writes` names. One is the permission, the
    // other is the value; neither may exist without the other.
    for (const [kind, fields] of Object.entries(admitted.writes)) {
      const rule = admitted.subjects[kind];
      if (rule.mode === "create") continue;
      assert.deepEqual(Object.keys(rule.effects).sort(), [...fields].sort(),
        `${id} moves exactly the ${kind} fields it declares, to declared values`);
    }
    assert.ok(admitted.events.length >= 1, `${id} appends at least one event`);
  }
  // The two created subjects, and there are exactly two.
  const creating = V5_J102_TRANSITION_IDS.flatMap(id =>
    Object.entries(policy.transitions[id].subjects)
      .filter(([, rule]) => rule.mode === "create")
      .map(([kind]) => `${id}:${kind}`)).sort();
  assert.deepEqual(creating, [
    "commit-winning-property:deal",
    "establish-client-and-engagement:engagement",
  ]);
});

test("SQL parity: a created subject's shape IS the kernel's declared shape for that kind", () => {
  const policy = admissionPolicy();
  // Read off the kernel rather than restated: assertLifecycleSubject returns
  // every declared key for a kind, null where absent, so its output keys are the
  // canonical shape a creation must carry exactly.
  const canonicalKeys = subject => Object.keys(assertLifecycleSubject(subject)).sort();
  assert.deepEqual(
    Object.keys(policy.transitions["establish-client-and-engagement"].subjects.engagement
      .creation_shape).sort(),
    canonicalKeys(ENG));
  assert.deepEqual(
    Object.keys(policy.transitions["commit-winning-property"].subjects.deal.creation_shape).sort(),
    canonicalKeys(DEAL()));
});

test("SQL parity: the map PREDICTS the kernel's proposed state and events, walk by walk", () => {
  const policy = admissionPolicy();
  const covered = new Set();
  for (const walk of WALKS) {
    const { answer, ctx } = runWalk(walk);
    covered.add(walk.transition_id);
    assert.deepEqual(complaintsAgainstPolicy(policy, walk.transition_id, answer.proposed_state, ctx),
      [], `${walk.transition_id}: the admission map admits the kernel's own answer`);
    assert.deepEqual(eventComplaints(policy, walk.transition_id, answer.events, ctx),
      [], `${walk.transition_id}: the admission map admits the kernel's own events`);
    // And the coupled subject set is the same set, in both directions.
    assert.deepEqual(Object.keys(answer.proposed_state).sort(),
      Object.keys(policy.transitions[walk.transition_id].subjects).sort(),
      `${walk.transition_id}: the required subject set is the set the kernel writes`);
    // WHICH SUBJECTS THE KERNEL CREATES, read off its own answer: a proposed kind
    // that was not among the loaded ones is a creation. The map's create-mode
    // subjects must be exactly those, and so must the store's description of
    // them -- so neither can drift from the evaluator.
    const created = Object.keys(answer.proposed_state)
      .filter(kind => walk.prior[kind] === undefined).sort();
    assert.deepEqual(created,
      Object.entries(policy.transitions[walk.transition_id].subjects)
        .filter(([, rule]) => rule.mode === "create").map(([kind]) => kind).sort(),
      `${walk.transition_id}: the map creates exactly what the kernel creates`);
    const operation = policy.transitions[walk.transition_id].operations[0];
    const registration = v5J102ToolRegistrations().find(entry => entry.name === operation);
    for (const kind of created) {
      assert.ok(registration.creates_coupled_subject_kinds.includes(kind),
        `${operation} reports that it creates a ${kind}`);
    }
    assert.equal(registration.requires_existing_primary_subject, true,
      `${operation} advances a subject that must already exist`);
  }
  // EVERY transition in the table is walked. A map checked against twelve of
  // fourteen would leave the two unchecked ones free to say anything.
  const unwalked = V5_J102_TRANSITION_IDS.filter(id => !covered.has(id));
  assert.deepEqual(unwalked, [],
    "every transition in the table is walked at least once");
});

test("SQL parity: the required context IS the context the kernel refuses without", () => {
  const policy = admissionPolicy();
  const declared = policy.transitions["open-assignment"].required_context;
  assert.deepEqual(declared.map(entry => `${entry.subject}.${entry.conditions[0].field}`),
    ["engagement.engagement_state", "relationship.relationship_state"]);
  assert.deepEqual(declared.map(entry => entry.conditions[0].equals), ["active", "client"]);

  // AND THE KERNEL REALLY DOES REFUSE WITHOUT THEM, which is what makes the two
  // entries a transcription rather than a policy this map invented. The SQL reads
  // both subjects out of the compare-and-swap operand set, so they are locked and
  // digest-checked; a direct caller cannot open an assignment under a lapsed
  // engagement by declining to mention it.
  const walk = WALKS.find(entry => entry.transition_id === "open-assignment");
  const refusal = (related, reason) => {
    const answer = evaluateLifecycleTransition({
      tenant: ORGANIZATION_TENANT_ID, transition_id: "open-assignment",
      subject: walk.subject, related, evidence: walk.evidence, actor: walk.actor,
      declared: walk.declared, now: NOW,
    });
    assert.equal(answer.decision, "refuse");
    assert.equal(answer.reason_id, reason);
  };
  refusal({ engagement: { ...ENG, engagement_state: "expired" }, relationship: CLIENT },
    "engagement_not_active");
  refusal({ engagement: ENG, relationship: { ...CLIENT, relationship_state: "prospect" } },
    "client_status_required");
  refusal({ relationship: CLIENT }, "engagement_not_loaded");
});

test("ROOT BLOCKER 2: the map REFUSES a permitted field carrying an unpermitted value", () => {
  const policy = admissionPolicy();
  const walkFor = id => WALKS.find(walk => walk.transition_id === id);
  const mutate = (id, change) => {
    const { answer, ctx } = runWalk(walkFor(id));
    const proposed = Object.fromEntries(
      Object.entries(answer.proposed_state).map(([kind, state]) => [kind, { ...state }]));
    change(proposed);
    return complaintsAgainstPolicy(policy, id, proposed, ctx);
  };
  const complains = (id, change, fragment) => {
    const complaints = mutate(id, change);
    assert.ok(complaints.some(complaint => complaint.includes(fragment)),
      `${id} must refuse this payload with ${fragment}; it said ${JSON.stringify(complaints)}`);
  };

  // THE ROUTINE CALL THAT COMMITS AN ASSIGNMENT. `assignment_phase` is a field
  // open-assignment moves; `committed` is not a value it moves it to.
  complains("open-assignment", p => { p.assignment.assignment_phase = "committed"; },
    "j102_transition_effect_not_canonical");
  // The same field, DELETED. A key that is not there is not the value either.
  complains("open-assignment", p => { delete p.assignment.assignment_phase; },
    "j102_transition_effect_missing");
  // A pending deal id invented on a permitted subject through an unpermitted field.
  complains("open-assignment", p => { p.assignment.pending_deal_id = "j102-deal-9"; },
    "j102_field_not_movable_by_transition");
  // A counter set to an arbitrary number rather than to its derived successor.
  complains("record-loi-submission", p => { p.assignment.open_negotiation_count = 40; },
    "j102_transition_effect_not_canonical");
  // THE MISSING COUPLED SUBJECT: the deal is cancelled and the assignment is not
  // returned, which is the subset of a coupled write Q082 refuses.
  complains("cancel-pending-deal", p => { delete p.assignment; },
    "j102_required_subject_not_proposed");
  // The cleared references, kept instead of cleared.
  complains("cancel-pending-deal", p => { p.assignment.pending_deal_id = "j102-deal-1"; },
    "j102_transition_effect_not_canonical");
  complains("cancel-pending-deal", p => { p.assignment.selected_property_id = "j102-prop-1"; },
    "j102_transition_effect_not_canonical");
  // A reason that is not the reason the stored failure record gives.
  complains("cancel-pending-deal", p => { p.deal.cancellation_reason = "a reason nobody recorded"; },
    "j102_transition_effect_not_canonical");
  // A closing date that is not the settlement's date -- BLOCK-2's original
  // consequence, now refused on the VALUE as well as on the binding.
  complains("record-deal-closing", p => { p.deal.closing_date = "2027-01-01T00:00:00.000Z"; },
    "j102_transition_effect_not_canonical");
  // A deal born closed, a deal born under another assignment, a deal missing an
  // axis, and a deal carrying a field the kernel never creates.
  complains("commit-winning-property", p => { p.deal.deal_state = "closed"; },
    "j102_created_subject_field_not_canonical");
  complains("commit-winning-property", p => { p.deal.assignment_id = "j102-asg-9"; },
    "j102_created_subject_field_not_canonical");
  complains("commit-winning-property", p => { delete p.deal.invoice_state; },
    "j102_created_subject_shape_mismatch");
  complains("commit-winning-property", p => { p.deal.smuggled_field = "anything"; },
    "j102_created_subject_shape_mismatch");
  // The assignment pointing at a deal this call did not create.
  complains("commit-winning-property", p => { p.assignment.pending_deal_id = "j102-deal-9"; },
    "j102_transition_effect_not_canonical");
  // An unrelated subject travelling beside a legitimate request.
  complains("record-completion", p => { p.relationship = { ...CLIENT }; },
    "j102_subject_kind_not_written_by_transition");
  // Q072's payment level that would not change the state: prior is
  // partially_paid, so `partially_paid` is not among the values on THIS path.
  complains("record-payment", p => { p.deal.payment_state = "partially_paid"; },
    "j102_transition_effect_not_canonical");
});

test("ROOT BLOCKER 2: the map REFUSES a wrong, missing or extra event", () => {
  const policy = admissionPolicy();
  const walkFor = id => WALKS.find(walk => walk.transition_id === id);
  const complainsAbout = (id, change, fragment) => {
    const { answer, ctx } = runWalk(walkFor(id));
    const events = answer.events.map(event => ({ ...event }));
    const mutated = change(events) ?? events;
    const complaints = eventComplaints(policy, id, mutated, ctx);
    assert.ok(complaints.some(complaint => complaint.includes(fragment)),
      `${id} must refuse this history with ${fragment}; it said ${JSON.stringify(complaints)}`);
  };

  // THE EMPTY ARRAY. A transition that appends nothing is a state change nobody
  // can audit; "at least one event" would have caught this one and none of the
  // three below.
  complainsAbout("record-completion", () => [], "j102_event_set_mismatch");
  // ONE OF TWO. The deal is cancelled in the history and the assignment never
  // came back to the market.
  complainsAbout("cancel-pending-deal", events => events.slice(0, 1),
    "j102_event_set_mismatch");
  // A PLAUSIBLE WRONG KIND on a correctly bound subject -- the residual the
  // previous correction acknowledged and left open.
  complainsAbout("record-lease-execution", events => { events[0].event_kind = "deal_closed"; },
    "j102_event_missing_or_wrong");
  // AN EXTRA EVENT for a subject this call does advance, which the subject check
  // alone would not catch.
  complainsAbout("record-completion", events => [...events, { ...events[0] }],
    "j102_event_set_mismatch");
  // AN EVENT FOR AN UNRELATED SUBJECT.
  complainsAbout("record-completion",
    events => [{ ...events[0], subject_id: "j102-deal-c" }], "j102_event_missing_or_wrong");
  // THE DERIVED KIND, which must agree with the payment level the same call
  // writes: `payment_partially_paid` beside a `paid` state is a history that
  // contradicts its own state row.
  complainsAbout("record-payment",
    events => { events[0].event_kind = "payment_partially_paid"; }, "j102_event_missing_or_wrong");
});

test("SQL parity: the effect vocabulary in the map is exactly the one the SQL implements", () => {
  // The interpreter above is a transcription, so the two could drift by one op
  // and nothing else would notice. This reads the ops the PL/pgSQL function
  // branches on, and the ops the map actually uses, and requires both to sit
  // inside what this suite can evaluate.
  const start = CANDIDATE_SQL.indexOf("create or replace function ops.j102_expected_value(");
  assert.ok(start > 0, "the candidate carries the effect interpreter");
  const source = CANDIDATE_SQL.slice(start,
    CANDIDATE_SQL.indexOf("comment on function ops.j102_expected_value"));
  const sqlOps = [...new Set([...source.matchAll(/v_op = '([a-z_]+)'/g)].map(m => m[1]))].sort();
  const readable = ["case_on_evidence", "case_on_field", "const", "evidence_fact", "one_of",
    "prior_plus", "prior_plus_conditional", "proposed_subject_id", "subject_field",
    "supplied_evidence_kind", "unbound"];
  assert.deepEqual(sqlOps, readable,
    "the SQL interpreter implements exactly the ops this suite can evaluate");

  const policy = admissionPolicy();
  const used = new Set();
  const walk = node => {
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (node === null || typeof node !== "object") return;
    if (typeof node.op === "string") used.add(node.op);
    Object.values(node).forEach(walk);
  };
  walk(policy.transitions);
  for (const op of used) {
    assert.ok(readable.includes(op), `the map uses the effect op ${op}, which the SQL implements`);
  }
  // The ONE unbound value in the whole map, named here so a second one cannot
  // arrive quietly. It sits on the approved-representation-equivalence branch,
  // which no manifest can reach because the typed approval reader is private and
  // always raises.
  const unbound = [];
  const findUnbound = (node, path) => {
    if (Array.isArray(node)) { node.forEach((item, i) => findUnbound(item, `${path}[${i}]`)); return; }
    if (node === null || typeof node !== "object") return;
    if (node.op === "unbound") { unbound.push(path); return; }
    for (const [key, value] of Object.entries(node)) findUnbound(value, `${path}.${key}`);
  };
  findUnbound(policy.transitions, "transitions");
  assert.deepEqual(unbound, [
    "transitions.establish-client-and-engagement.subjects.engagement.creation_shape.effective_from.cases.approved_representation_equivalent",
  ]);
  assert.ok(Object.keys(V5_J102_ABSENT_EVIDENCE_READERS)
    .includes("approved_representation_equivalent"),
    "and that branch is unreachable because its reader does not exist");
});
