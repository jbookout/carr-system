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

import { canonicalJson, digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_J102_TRANSITION_IDS, V5_J102_DEAL_AXES, evaluateLifecycleTransition,
} from "../src/cre-lifecycle.v5.js";
import {
  V5_J102_ABSENT_EVIDENCE_READERS,
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
    "ownership_and_freshness_exposure",
    "property_negotiation_initialization",
    "relationship_prospect_initialization",
    "reconciliation_runtime_integration",
  ].sort());
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
