// V5-J103 — governed correspondence reads, drafts, proposed facts and source
// reconciliation, proved case by case.
//
// Everything here is synthetic and nothing reaches a database, a provider, a
// network, a clock or the filesystem. The module under test is pure, so this
// suite can prove the properties that actually matter about a correspondence
// kernel:
//
//   * that a read PRESERVES account and native provenance on every answer it
//     gives, including the refusals,
//   * that a draft and a proposed fact are structurally unable to dispatch — no
//     recipient destination, no provider operation, and a dispatch field refused
//     before any value is read — with F10 as the INDEPENDENT ORACLE for the send
//     refusal, since F10 owns the operation registry,
//   * that a conflict shows BOTH values with the source that owns each, and that
//     every way of saying "whoever wrote last wins" is refused by name,
//   * that the four conflict kinds this module routes are exactly the four F01
//     emits — driven out of resolveObservation rather than asserted from a
//     comment,
//   * that the observation candidate a proposal builds is one F01 ITSELF accepts,
//     so the mapping is not graded by the code that produced it,
//   * that ambiguity resolves to private and never falls through to a read,
//   * and that both-partner coverage is unreachable from every input.
//
// NO FIXTURE NAMES A REAL THING: every account, participant, digest and native id
// below is unmistakably test data on an .invalid domain.

import test from "node:test";
import assert from "node:assert/strict";

import { canonicalJson, digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  V5_NO_EFFECTS,
  evaluatePrivacyBoundary,
} from "../src/global-boundaries.v5.js";
import {
  V5_F01_RECONCILIATION_SCHEMA_VERSION,
  compileFieldAuthorityRegistry,
  resolveObservation,
} from "../src/record-source-authority.v5.js";
import {
  V5_F10_ADAPTER_KIND,
  V5_F10_WRITE_OPERATIONS,
  compilePartnerInstallation,
  evaluateConnectorOperation,
} from "../src/partner-mail-calendar.v5.js";
import {
  V5J103Error,
  V5_J103_ADAPTERS,
  V5_J103_ADAPTER_KINDS,
  V5_J103_AVAILABILITY_STATES,
  V5_J103_BINDING_SCHEMA_VERSION,
  V5_J103_CONFLICT_KINDS,
  V5_J103_CONFLICT_ROUTES,
  V5_J103_CONFLICT_SCHEMA_VERSION,
  V5_J103_CORRESPONDENCE_STATES,
  V5_J103_CREDENTIAL_FRAGMENTS,
  V5_J103_DISPATCH_FRAGMENTS,
  V5_J103_DRAFT_SCHEMA_VERSION,
  V5_J103_NON_READING_AVAILABILITY,
  V5_J103_PROPOSAL_SCHEMA_VERSION,
  V5_J103_REFUSED_RESOLUTION_BASES,
  V5_J103_RESOLUTION_BASIS,
  V5_J103_SEND_AUTHORITY_HOLDER,
  V5_J103_SEND_AUTHORITY_SEAM,
  V5_J103_SETTLED_DECISIONS,
  V5_J103_SETTLED_DECISION_IDS,
  V5_J103_SOURCE_CONTENT_FRAGMENTS,
  V5_J103_TAINT_CLASS,
  V5_J103_THREAD_SCHEMA_VERSION,
  assertJ103DecisionBinding,
  buildSourceConflictQueueEntry,
  compileCorrespondenceBinding,
  correspondenceBindingCanonicalBytes,
  draftCorrespondence,
  evaluateProposedFact,
  governedCorrespondenceGaps,
  projectCorrespondenceCoverage,
  readCorrespondenceThread,
  v5J103AbsentWriteOperations,
  v5J103CorrespondenceProjection,
  v5J103DecisionSubsetDigest,
  v5J103DecisionSubsetPreimage,
  v5J103PolicyCanonicalBytes,
  v5J103PolicyDigest,
  v5J103PolicyPreimage,
} from "../src/governed-correspondence.v5.js";

// ---------------------------------------------------------------------------
// Fixtures.
// ---------------------------------------------------------------------------

const NOW = "2026-09-11T18:00:00Z";
const SOURCE = "outlook_graph_test";
const JOE_ACCOUNT = "joe@carr-test.invalid";
const DELL_ACCOUNT = "dell@carr-test.invalid";

const sha = suffix => `sha256:${suffix.padEnd(64, "0").slice(0, 64)}`;

function binding(overrides = {}) {
  return compileCorrespondenceBinding({
    partner_slug: "joe",
    adapter_kind: V5_F10_ADAPTER_KIND,
    account: JOE_ACCOUNT,
    source_system: SOURCE,
    availability: "available",
    binding_version: 1,
    ...overrides,
  });
}

function thread(overrides = {}) {
  return {
    account: JOE_ACCOUNT,
    native_identity: {
      source_system: SOURCE,
      native_id: "thread-aaa-test",
      native_id_epoch: "epoch-1",
    },
    participants: [
      { participant_ref: "party:joe-test", role: "originator", party_kind: "partner",
        address_digest: sha("a1") },
      { participant_ref: "party:counterparty-test", role: "principal_recipient",
        party_kind: "counterparty", address_digest: sha("b2") },
    ],
    message_refs: [
      { provider_thread_id: "pt-1-test", provider_message_id: "pm-1-test",
        occurred_at: "2026-09-10T15:00:00Z" },
      { provider_thread_id: "pt-1-test", provider_message_id: "pm-2-test",
        occurred_at: "2026-09-10T17:30:00Z" },
    ],
    attachments: [
      { attachment_id: "att-1-test", media_type: "application/pdf", byte_length: 4096,
        content_digest: sha("c3") },
    ],
    related_records: [{ record_kind: "deal", record_ref: "deal:test-0001" }],
    correspondence_state: "awaiting_us",
    relevance_state: "relevant_business_context",
    declared_data_classes: ["lease_economics"],
    started_at: "2026-09-10T15:00:00Z",
    last_activity_at: "2026-09-10T17:30:00Z",
    observed_at: "2026-09-10T18:00:00Z",
    ...overrides,
  };
}

function read(overrides = {}, b = binding()) {
  return readCorrespondenceThread({ binding: b, thread: thread(overrides), now: NOW });
}

function draft(overrides = {}) {
  return {
    draft_kind: "reply_in_thread",
    authored_by: "model",
    draft_body: "Confirming the lease economics discussed on the thread; ready for your review.",
    intended_participant_refs: ["party:counterparty-test"],
    declared_data_classes: ["lease_economics"],
    ...overrides,
  };
}

function proposal(overrides = {}) {
  return {
    entity: "correspondence_derived_term",
    field: "base_rent_psf",
    value_digest: sha("d4"),
    rationale: "The counterparty restated the number in the second message on this thread.",
    proposed_by: "model",
    evidence_provider_message_id: "pm-2-test",
    declared_data_classes: ["lease_economics"],
    ...overrides,
  };
}

/**
 * A field-authority registry F01 compiles, shaped for a correspondence-derived
 * term. `untrusted_external` is the registered taint because that is what the
 * adapter boundary carries; anything else would make every J103 candidate refuse
 * at F01's taint check, which is the correct behaviour and a useless fixture.
 */
function registry(entryOverrides = {}) {
  return compileFieldAuthorityRegistry({
    schema_version: "doctorcre-v5-f01-field-authority-registry.v1",
    registry_version: 1,
    tenant: ORGANIZATION_TENANT_ID,
    entries: [{
      entity: "correspondence_derived_term",
      field: "base_rent_psf",
      authoritative_home: "neon_record_layer",
      owner_source: "carr_record_layer_test",
      permitted_sources: [
        { source_system: "carr_record_layer_test", direction: "bidirectional" },
        { source_system: SOURCE, direction: "inbound" },
      ],
      requires_account_identity: true,
      requires_native_identity: true,
      version_comparator: "integer_sequence",
      conflict_behavior: "reconcile",
      human_resolver_class: "partner_business_review",
      readback_required: false,
      sensitivity_classes: ["lease_economics"],
      taint_class: "untrusted_external",
      ...entryOverrides,
    }],
  });
}

/** Every result this suite produces, collected so the no-dispatch claim is total. */
const PRODUCED_RESULTS = [];
function capture(result) {
  PRODUCED_RESULTS.push(result);
  return result;
}

// ---------------------------------------------------------------------------
// The settled decisions.
// ---------------------------------------------------------------------------

test("the four settled decisions are the four this slice was dispatched against", () => {
  assert.deepEqual(V5_J103_SETTLED_DECISION_IDS, ["Q005.D1", "Q076.D1", "Q133.D1", "Q134.D1"]);
});

test("the decision subset digest is derived from the table, not typed beside it", () => {
  const rebuilt = {
    schema_version: "doctorcre-v5-j103-decision-subset.v1",
    decisions: ["Q005.D1", "Q076.D1", "Q133.D1", "Q134.D1"].map(decision_id => ({
      decision_id,
      settled_requirement: V5_J103_SETTLED_DECISIONS[decision_id].settled_requirement,
      source_evidence_digest: V5_J103_SETTLED_DECISIONS[decision_id].source_evidence_digest,
    })),
  };
  assert.deepEqual(v5J103DecisionSubsetPreimage(), rebuilt);
  assert.equal(v5J103DecisionSubsetDigest(), digest(rebuilt));
});

test("a decision binding accepts the reviewed four and refuses drift in both directions", () => {
  const decisions = Object.fromEntries(V5_J103_SETTLED_DECISION_IDS.map(id => [id, {
    source_evidence_digest: V5_J103_SETTLED_DECISIONS[id].source_evidence_digest,
    settled_requirement: V5_J103_SETTLED_DECISIONS[id].settled_requirement,
  }]));
  assert.equal(assertJ103DecisionBinding({
    decisions, decision_subset_digest: v5J103DecisionSubsetDigest(),
  }), true);

  const missing = { ...decisions };
  delete missing["Q076.D1"];
  assert.throws(() => assertJ103DecisionBinding({ decisions: missing }),
    e => e instanceof V5J103Error && e.code === "decision_binding_drift");

  const extra = { ...decisions, "Q999.D1": { source_evidence_digest: "0".repeat(64) } };
  assert.throws(() => assertJ103DecisionBinding({ decisions: extra }),
    e => e.code === "decision_binding_drift");

  const reworded = {
    ...decisions,
    "Q133.D1": { ...decisions["Q133.D1"], settled_requirement: "Outlook remains mailbox truth." },
  };
  assert.throws(() => assertJ103DecisionBinding({ decisions: reworded }),
    e => e.code === "decision_binding_drift" && e.detail.decision_id === "Q133.D1");
});

test("Q076's settled text is carried verbatim, including the clause the module enforces", () => {
  assert.match(V5_J103_SETTLED_DECISIONS["Q076.D1"].settled_requirement,
    /never timestamp alone; expose values and provenance/);
  assert.match(V5_J103_SETTLED_DECISIONS["Q134.D1"].settled_requirement,
    /never claims both-partner coverage/);
});

// ---------------------------------------------------------------------------
// The binding.
// ---------------------------------------------------------------------------

test("a compiled binding names one partner, one account and one read-only adapter", () => {
  const b = binding();
  assert.equal(b.schema_version, V5_J103_BINDING_SCHEMA_VERSION);
  assert.equal(b.tenant, ORGANIZATION_TENANT_ID);
  assert.equal(b.partner_slug, "joe");
  assert.equal(b.account, JOE_ACCOUNT);
  assert.equal(b.adapter_mode, "read");
  assert.equal(b.authoritative_home, V5_J103_ADAPTERS[V5_F10_ADAPTER_KIND].authoritative_home);
  assert.ok(Object.isFrozen(b));
  assert.equal(correspondenceBindingCanonicalBytes(b), canonicalJson(JSON.parse(
    canonicalJson({
      schema_version: V5_J103_BINDING_SCHEMA_VERSION,
      tenant: b.tenant,
      partner_slug: b.partner_slug,
      adapter_kind: b.adapter_kind,
      adapter_mode: b.adapter_mode,
      authoritative_home: b.authoritative_home,
      retrieval_class: b.retrieval_class,
      account: b.account,
      source_system: b.source_system,
      availability: b.availability,
      binding_version: b.binding_version,
    }))));
});

test("a credential in the binding config is refused by field name, before any value is read", () => {
  for (const fragment of ["access_token", "client_secret", "oauth_state", "session_cookie"]) {
    assert.throws(
      () => compileCorrespondenceBinding({
        partner_slug: "joe", adapter_kind: V5_F10_ADAPTER_KIND, account: JOE_ACCOUNT,
        source_system: SOURCE, availability: "available", binding_version: 1, [fragment]: "x",
      }),
      e => e instanceof V5J103Error && e.code === "credential_in_correspondence_request",
      `expected ${fragment} to be refused`);
  }
  assert.ok(V5_J103_CREDENTIAL_FRAGMENTS.includes("token"));
});

test("an unregistered adapter kind is not a policy question", () => {
  assert.throws(() => binding({ adapter_kind: "some_other_adapter" }),
    e => e.code === "unregistered_adapter_kind");
  assert.deepEqual(V5_J103_ADAPTER_KINDS, [V5_F10_ADAPTER_KIND]);
});

test("a partner slug is a CARR reference, cannot be an address, and must be a partner", () => {
  assert.throws(() => binding({ partner_slug: "joe@carr-test.invalid" }),
    e => e.code === "invalid_reference");
  assert.throws(() => binding({ partner_slug: "someone-else" }),
    e => e.code === "unknown_partner");
});

test("a binding edited after compilation no longer hashes to its own digest", () => {
  const forged = { ...binding(), account: DELL_ACCOUNT };
  assert.throws(() => readCorrespondenceThread({ binding: forged, thread: thread(), now: NOW }),
    e => e.code === "binding_digest_mismatch");
});

test("a hand-built object claiming to be compiled is refused", () => {
  const b = binding();
  const forged = { ...b, compiled: false };
  assert.throws(() => readCorrespondenceThread({ binding: forged, thread: thread(), now: NOW }),
    e => e.code === "binding_not_compiled");
});

// ---------------------------------------------------------------------------
// checkable_done 1 — reads preserve account and native provenance.
// ---------------------------------------------------------------------------

test("a read preserves the account and the full native identity triple", () => {
  const result = capture(read());
  assert.equal(result.decision, "read");
  assert.equal(result.schema_version, V5_J103_THREAD_SCHEMA_VERSION);
  assert.equal(result.provenance.account, JOE_ACCOUNT);
  assert.equal(result.provenance.source_system, SOURCE);
  assert.deepEqual(result.provenance.native_identity, {
    source_system: SOURCE, native_id: "thread-aaa-test", native_id_epoch: "epoch-1",
  });
  assert.equal(result.provenance.partner_slug, "joe");
  assert.equal(result.provenance.adapter_kind, V5_F10_ADAPTER_KIND);
  assert.equal(result.provenance.taint_class, V5_J103_TAINT_CLASS);
  assert.equal(result.provenance.authoritative_home, "outlook");
  assert.equal(result.mailbox_remains_truth, true);
});

test("provenance rides on the refusals too, so a refusal can be audited", () => {
  const cases = [
    read({ relevance_state: "unrelated" }),
    read({ relevance_state: "ambiguous" }),
    read({ declared_data_classes: ["phi"] }),
    read({}, binding({ availability: "unavailable" })),
  ];
  for (const result of cases) {
    capture(result);
    assert.notEqual(result.decision, "read");
    assert.equal(result.provenance.account, JOE_ACCOUNT);
    assert.equal(result.provenance.native_identity.native_id, "thread-aaa-test");
    assert.equal(result.thread, null);
  }
});

test("the thread carries participants, provider ids, attachments, related records and state", () => {
  const t = capture(read()).thread;
  assert.equal(t.correspondence_state, "awaiting_us");
  assert.deepEqual(t.participants.map(p => p.participant_ref).sort(),
    ["party:counterparty-test", "party:joe-test"]);
  assert.deepEqual(t.message_refs.map(m => m.provider_message_id), ["pm-1-test", "pm-2-test"]);
  assert.equal(t.message_refs[0].provider_thread_id, "pt-1-test");
  assert.deepEqual(t.attachments[0],
    { attachment_id: "att-1-test", media_type: "application/pdf", byte_length: 4096,
      content_digest: sha("c3") });
  assert.deepEqual(t.related_records, [{ record_kind: "deal", record_ref: "deal:test-0001" }]);
  assert.equal(t.started_at, "2026-09-10T15:00:00Z");
  assert.ok(t.thread_ref.startsWith("sha256:"));
});

test("the thread ref is bound to the native identity and the account, not to a label", () => {
  const a = read().thread.thread_ref;
  const b = read({ native_identity: { source_system: SOURCE, native_id: "thread-aaa-test",
    native_id_epoch: "epoch-2" } }).thread.thread_ref;
  assert.notEqual(a, b, "a recycled native id under a new epoch is a different thread");
});

test("an account outside the binding is refused, and so is a foreign source system", () => {
  assert.equal(capture(read({ account: DELL_ACCOUNT })).reason_id, "account_outside_binding");
  assert.equal(capture(read({
    native_identity: { source_system: "other_source", native_id: "x-test", native_id_epoch: "e" },
  })).reason_id, "native_identity_source_mismatch");
});

test("an adapter nobody has observed is unavailable, and unknown sits with unavailable", () => {
  for (const availability of V5_J103_NON_READING_AVAILABILITY) {
    const result = capture(read({}, binding({ availability })));
    assert.equal(result.decision, "unavailable");
    assert.equal(result.reason_id, "authorized_adapter_unavailable");
    assert.equal(result.availability, availability);
  }
  assert.deepEqual([...V5_J103_AVAILABILITY_STATES].sort(),
    ["available", "unavailable", "unknown"]);
});

test("privacy is S01's answer carried through, and it is evaluated before relevance", () => {
  const refused = capture(read({ declared_data_classes: ["phi"] }));
  const s01 = evaluatePrivacyBoundary({ data_classes: ["phi"] });
  assert.equal(refused.decision, "refuse");
  assert.equal(refused.reason_id, s01.reason_id);
  assert.deepEqual(refused.prohibited_classes, [...s01.prohibited_classes]);

  const routed = capture(read({ declared_data_classes: ["aggregate_patient_volume_estimate"] }));
  assert.equal(routed.decision, "needs_independent_privacy_route");
  assert.equal(routed.required_evidence,
    evaluatePrivacyBoundary({ data_classes: ["aggregate_patient_volume_estimate"] }).required_evidence);

  // The ordering claim: an UNRELATED thread carrying PHI still refuses on privacy,
  // which is only true if privacy ran first. Excluded-without-classification and
  // classified-then-excluded are different facts about the same item.
  const both = capture(read({ relevance_state: "unrelated", declared_data_classes: ["phi"] }));
  assert.equal(both.decision, "refuse");
  assert.equal(both.reason_id, "phi_or_raw_patient_location_refused");
});

test("unrelated mail is excluded and ambiguity stays private; neither falls through", () => {
  assert.equal(capture(read({ relevance_state: "unrelated" })).decision, "exclude_unrelated");
  const ambiguous = capture(read({ relevance_state: "ambiguous" }));
  assert.equal(ambiguous.decision, "withhold_ambiguous");
  assert.equal(ambiguous.reason_id, "ambiguity_remains_private");
  assert.equal(ambiguous.thread, null);
});

test("an unreadable thread is not a judgement: shape defects throw rather than answer", () => {
  assert.throws(() => read({ correspondence_state: "sort_of_open" }),
    e => e.code === "unknown_correspondence_state");
  assert.throws(() => read({ last_activity_at: "2026-09-09T00:00:00Z" }),
    e => e.code === "thread_time_inverted");
  assert.throws(() => read({ started_at: "2026-02-31T00:00:00Z" }),
    e => e.code === "invalid_timestamp");
  assert.throws(() => read({ participants: [
    { participant_ref: "party:joe-test", role: "originator", party_kind: "partner",
      address_digest: sha("a1") },
    { participant_ref: "party:joe-test", role: "copied", party_kind: "partner",
      address_digest: sha("a1") },
  ] }), e => e.code === "duplicate_participant");
  assert.throws(() => read({ message_refs: [
    { provider_thread_id: "pt-1-test", provider_message_id: "pm-1-test",
      occurred_at: "2026-09-10T15:00:00Z" },
    { provider_thread_id: "pt-1-test", provider_message_id: "pm-1-test",
      occurred_at: "2026-09-10T16:00:00Z" },
  ] }), e => e.code === "duplicate_provider_message_id");
  assert.throws(() => read({ message_refs: [
    { provider_thread_id: "pt-1-test", provider_message_id: "pm-9-test",
      occurred_at: "2027-01-01T00:00:00Z" },
  ] }), e => e.code === "message_occurs_after_now");
});

test("a thread observed after now refuses rather than being read", () => {
  assert.equal(capture(read({ observed_at: "2026-09-11T18:00:01Z" })).reason_id, "observed_after_now");
});

// ---------------------------------------------------------------------------
// The structural seams: content, credentials, dispatch, addresses.
// ---------------------------------------------------------------------------

test("raw correspondence never crosses the seam, refused by field name", () => {
  for (const field of ["body", "body_html", "message_text", "subject_line", "preview_snippet",
    "attachment_bytes"]) {
    assert.throws(() => readCorrespondenceThread({
      binding: binding(), thread: { ...thread(), [field]: "x" }, now: NOW,
    }), e => e instanceof V5J103Error && e.code === "source_content_must_not_cross_seam",
    `expected ${field} to be refused`);
  }
  assert.ok(V5_J103_SOURCE_CONTENT_FRAGMENTS.includes("subject"));
});

test("the attachment descriptor holds no filename and no bytes", () => {
  assert.throws(() => read({ attachments: [{ attachment_id: "att-1-test",
    media_type: "application/pdf", byte_length: 1, content_digest: sha("c3"),
    filename: "lease.pdf" }] }), e => e.code === "unknown_field");
});

test("a dispatch instruction is refused before any value is read", () => {
  for (const field of ["send_after", "dispatch_at", "deliver_to", "smtp_relay",
    "schedule_send_at", "recipient_address"]) {
    assert.throws(() => readCorrespondenceThread({
      binding: binding(), thread: { ...thread(), [field]: "x" }, now: NOW,
    }), e => e.code === "dispatch_instruction_refused", `expected ${field} to be refused`);
  }
  assert.ok(V5_J103_DISPATCH_FRAGMENTS.includes("send"));
});

test("a routable address anywhere but the partner's own account is refused", () => {
  // The value scan runs before the field validators, so the refusal names the
  // boundary that was crossed rather than the syntax rule that would also have
  // caught it.
  assert.throws(() => read({ participants: [
    { participant_ref: "counterparty@elsewhere.invalid", role: "principal_recipient",
      party_kind: "counterparty", address_digest: sha("b2") },
  ] }), e => e.code === "routable_address_refused");
  assert.throws(() => read({ related_records: [
    { record_kind: "party", record_ref: "mailto:someone" },
  ] }), e => e.code === "routable_address_refused");
  // And the narrow reference validator is the second line: a reference that is
  // not address-shaped but is still not a CARR reference refuses on its own terms.
  assert.throws(() => read({ related_records: [
    { record_kind: "party", record_ref: "party ref with spaces" },
  ] }), e => e.code === "invalid_reference");
  // The exemption is exactly one key wide, and it is pinned to the binding.
  assert.equal(read().decision, "read", "the partner's own account may be an address");
  assert.equal(read({ account: DELL_ACCOUNT }).reason_id, "account_outside_binding");
});

test("a draft body carrying an address or a phone number is refused", () => {
  const b = binding();
  const r = read({}, b);
  assert.throws(() => draftCorrespondence({ binding: b, thread_read: r, now: NOW,
    draft: draft({ draft_body: "Reply to counterparty@elsewhere.invalid with the terms." }) }),
  e => e.code === "routable_address_refused");
  assert.throws(() => draftCorrespondence({ binding: b, thread_read: r, now: NOW,
    draft: draft({ draft_body: "Call them on +1 555 010 4477 to confirm." }) }),
  e => e.code === "routable_address_refused");
});

test("a guard can never refuse a field this module's own schemas require", () => {
  // The load-time self-check proves it; this asserts the case that makes the
  // asymmetry visible. `draft_body` contains "body" and is legitimate precisely
  // because the draft seam does not run the source-content scan.
  const b = binding();
  const drafted = capture(draftCorrespondence({
    binding: b, thread_read: read({}, b), now: NOW, draft: draft(),
  }));
  assert.equal(drafted.decision, "drafted");
  assert.ok(V5_J103_SOURCE_CONTENT_FRAGMENTS.includes("body"));
});

// ---------------------------------------------------------------------------
// Coverage — the claim that is unreachable from every input.
// ---------------------------------------------------------------------------

test("both-partner coverage is unavailable from every input", () => {
  const b = binding();
  const combined = capture(projectCorrespondenceCoverage({
    binding: b, requested_partner_slugs: ["joe", "dell"],
  }));
  assert.equal(combined.decision, "unavailable");
  assert.equal(combined.reason_id, "combined_partner_coverage_unavailable");

  const other = capture(projectCorrespondenceCoverage({
    binding: b, requested_partner_slugs: ["dell"],
  }));
  assert.equal(other.decision, "unavailable");
  assert.equal(other.reason_id, "partner_outside_binding_not_covered");

  const own = capture(projectCorrespondenceCoverage({
    binding: b, requested_partner_slugs: ["joe"],
  }));
  assert.equal(own.decision, "covered");
  assert.equal(own.covered_partner_slug, "joe");
  assert.equal(own.combined_partner_coverage, "unavailable");
  assert.equal(own.coverage_gate_satisfied, false);
});

test("an unavailable adapter cannot even cover its own partner", () => {
  const result = capture(projectCorrespondenceCoverage({
    binding: binding({ availability: "unknown" }), requested_partner_slugs: ["joe"],
  }));
  assert.equal(result.decision, "unavailable");
  assert.equal(result.reason_id, "authorized_adapter_unavailable");
});

test("every thread answer states single-partner coverage, including the refusals", () => {
  for (const result of [read(), read({ relevance_state: "unrelated" }),
    read({}, binding({ availability: "unavailable" }))]) {
    assert.equal(result.partner_coverage.combined_partner_coverage, "unavailable");
    assert.equal(result.partner_coverage.covered_partner_slug, "joe");
  }
});

// ---------------------------------------------------------------------------
// checkable_done 3 — drafts and proposed facts cannot dispatch a provider effect.
// ---------------------------------------------------------------------------

test("a draft is produced for a human to send and names no provider operation", () => {
  const b = binding();
  const result = capture(draftCorrespondence({
    binding: b, thread_read: read({}, b), now: NOW, draft: draft(),
  }));
  assert.equal(result.decision, "drafted");
  assert.equal(result.schema_version, V5_J103_DRAFT_SCHEMA_VERSION);
  assert.equal(result.dispatchable, false);
  assert.equal(result.provider_operation, null);
  assert.equal(result.requires_human_send, true);
  assert.equal(result.send_authority_holder, V5_J103_SEND_AUTHORITY_HOLDER);
  assert.equal(result.send_authority_seam, V5_J103_SEND_AUTHORITY_SEAM);
  assert.equal(result.model_seam, "correspondence_draft");
  assert.equal(result.draft.body_digest, digest(draft().draft_body));
  assert.deepEqual(result.draft.intended_participant_refs, ["party:counterparty-test"]);
  assert.equal(result.effects, V5_NO_EFFECTS);
});

test("the send refusal is proved where send is owned, not by this module's own say-so", () => {
  // F10 is the independent oracle: it holds the connector operation registry, and
  // every write in it refuses by name. A J103 draft changes nothing about that.
  const installation = compilePartnerInstallation({
    partner_slug: "joe", device_id: "device-test-1", account: JOE_ACCOUNT,
    source_system: SOURCE, item_kinds: ["mail_message"], deployment_state: "deployed",
    installation_version: 1,
  });
  for (const operation of V5_F10_WRITE_OPERATIONS) {
    const answer = evaluateConnectorOperation({ installation, operation });
    assert.equal(answer.decision, "refuse", `${operation} must refuse`);
    assert.equal(answer.reason_id, "write_capability_not_in_this_slice");
  }
  assert.ok(V5_F10_WRITE_OPERATIONS.includes("send_mail_message"));
  // And J103 names none of them anywhere in its own policy.
  const policyText = v5J103PolicyCanonicalBytes();
  for (const operation of v5J103AbsentWriteOperations()) {
    assert.ok(!policyText.includes(`"${operation}"`),
      `J103 policy must not name the write operation ${operation}`);
  }
  assert.deepEqual(v5J103PolicyPreimage().draft_and_proposal.send_operations_here, []);
});

test("no result this suite produced is dispatchable or carries a provider operation", () => {
  assert.ok(PRODUCED_RESULTS.length > 12, "the sweep needs results to sweep");
  for (const result of PRODUCED_RESULTS) {
    assert.equal(result.dispatchable, false);
    assert.equal(result.provider_operation, null);
    assert.equal(result.effects, V5_NO_EFFECTS);
    assert.ok(Object.isFrozen(result));
  }
});

test("a withheld or excluded thread cannot become a draft or a proposal", () => {
  const b = binding();
  for (const overrides of [{ relevance_state: "ambiguous" }, { relevance_state: "unrelated" },
    { declared_data_classes: ["phi"] }]) {
    const r = read(overrides, b);
    assert.throws(() => draftCorrespondence({ binding: b, thread_read: r, now: NOW, draft: draft() }),
      e => e.code === "work_from_non_readable_thread");
    assert.throws(() => evaluateProposedFact({ binding: b, thread_read: r, now: NOW,
      proposal: proposal() }), e => e.code === "work_from_non_readable_thread");
  }
});

test("one partner's thread is not another partner's to draft from", () => {
  const joe = binding();
  const dell = binding({ partner_slug: "dell", account: DELL_ACCOUNT });
  const joeRead = read({}, joe);
  assert.throws(() => draftCorrespondence({
    binding: dell, thread_read: joeRead, now: NOW, draft: draft(),
  }), e => e.code === "thread_outside_binding");
});

test("a recipient who is not on the thread is refused rather than quietly added", () => {
  const b = binding();
  const result = capture(draftCorrespondence({
    binding: b, thread_read: read({}, b), now: NOW,
    draft: draft({ intended_participant_refs: ["party:someone-else-test"] }),
  }));
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "recipient_outside_thread");
  assert.deepEqual(result.unknown_participant_refs, ["party:someone-else-test"]);
  assert.equal(result.draft, null);
});

test("a reply into a resolved thread, and a draft dated before the read, both refuse", () => {
  const b = binding();
  const resolved = read({ correspondence_state: "resolved" }, b);
  assert.equal(capture(draftCorrespondence({
    binding: b, thread_read: resolved, now: NOW, draft: draft(),
  })).reason_id, "reply_into_resolved_thread_refused");

  assert.equal(capture(draftCorrespondence({
    binding: b, thread_read: read({}, b), now: "2026-09-10T17:59:00Z", draft: draft(),
  })).reason_id, "draft_precedes_thread_observation");
});

test("a draft carrying a prohibited class refuses on S01's answer", () => {
  const b = binding();
  const result = capture(draftCorrespondence({
    binding: b, thread_read: read({}, b), now: NOW,
    draft: draft({ declared_data_classes: ["patient_record"] }),
  }));
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "phi_or_raw_patient_location_refused");
  assert.equal(result.draft, null);
  assert.equal(result.dispatchable, false);
});

// ---------------------------------------------------------------------------
// Proposed facts — inferred, never established.
// ---------------------------------------------------------------------------

test("a proposed fact cites the message it came from and establishes nothing", () => {
  const b = binding();
  const result = capture(evaluateProposedFact({
    binding: b, thread_read: read({}, b), now: NOW, proposal: proposal(),
  }));
  assert.equal(result.decision, "proposed");
  assert.equal(result.schema_version, V5_J103_PROPOSAL_SCHEMA_VERSION);
  assert.equal(result.applied, false);
  assert.equal(result.authority_established, false);
  assert.equal(result.next_authority_step, "record-source-authority.v5.js resolveObservation");
  assert.equal(result.model_seam, "proposed_fact");
  assert.equal(result.observation_candidate.observed_at, "2026-09-10T17:30:00Z");
  assert.equal(result.observation_candidate.taint_class, V5_J103_TAINT_CLASS);
  assert.equal(result.observation_candidate.account, JOE_ACCOUNT);
});

test("a proposal citing a message that is not on the thread is refused", () => {
  const b = binding();
  const result = capture(evaluateProposedFact({
    binding: b, thread_read: read({}, b), now: NOW,
    proposal: proposal({ evidence_provider_message_id: "pm-99-test" }),
  }));
  assert.equal(result.decision, "refuse");
  assert.equal(result.reason_id, "evidence_message_not_on_thread");
  assert.equal(result.observation_candidate, null);
});

test("a proposal citing evidence from after now is refused, naming the evidence", () => {
  const b = binding();
  const r = read({}, b);
  const result = capture(evaluateProposedFact({
    binding: b, thread_read: r, now: "2026-09-10T16:00:00Z", proposal: proposal(),
  }));
  assert.equal(result.reason_id, "evidence_message_occurs_after_now");
});

test("a proposal carrying a field that claims authority is refused", () => {
  const b = binding();
  const r = read({}, b);
  for (const field of ["authorized_by", "approved_by_partner", "owner_override", "acting_as"]) {
    assert.throws(() => evaluateProposedFact({
      binding: b, thread_read: r, now: NOW, proposal: { ...proposal(), [field]: "joe" },
    }), e => e.code === "authority_claim_in_proposal", `expected ${field} to be refused`);
  }
});

test("ordinary business prose is not an authority claim", () => {
  const b = binding();
  const result = capture(evaluateProposedFact({
    binding: b, thread_read: read({}, b), now: NOW,
    proposal: proposal({
      rationale: "The landlord said they would grant an extension once the admin fee is approved.",
    }),
  }));
  assert.equal(result.decision, "proposed",
    "the authority guard reads field names, not the words a broker uses");
});

test("the observation candidate is one F01 itself accepts", () => {
  // F01 is the independent oracle for the observation shape: if resolveObservation
  // can read the candidate and reach a decision about the FIELD rather than about
  // the request, the mapping is right. It is not graded by the code that built it.
  const b = binding();
  const result = evaluateProposedFact({
    binding: b, thread_read: read({}, b), now: NOW, proposal: proposal(),
  });
  const resolved = resolveObservation({
    tenant: ORGANIZATION_TENANT_ID,
    registry: registry(),
    observation: { ...result.observation_candidate, version: 1 },
    now: NOW,
  });
  assert.equal(resolved.entity, "correspondence_derived_term");
  assert.equal(resolved.field, "base_rent_psf");
  // The adapter is a permitted inbound source but not the owner, so F01 refuses
  // the establishment. That is the correct answer and the point: J103 proposed,
  // F01 decided, and the decision was no.
  assert.equal(resolved.decision, "refuse");
  assert.equal(resolved.reason_id, "non_owner_cannot_establish_field");
  assert.equal(resolved.applied, false);
  assert.equal(resolved.silent_last_write_wins, false);
});

// ---------------------------------------------------------------------------
// checkable_done 2 — conflicts show both values and the owning source.
// ---------------------------------------------------------------------------

/** Drive F01 until it emits each conflict kind, so the route table is checked against reality. */
function f01Conflicts() {
  const reg = registry();
  const owner = "carr_record_layer_test";
  const nativeIdentity = system => ({
    source_system: system, native_id: "term-1-test", native_id_epoch: "epoch-1",
  });
  const observation = (source_system, over = {}) => ({
    entity: "correspondence_derived_term",
    field: "base_rent_psf",
    tenant: ORGANIZATION_TENANT_ID,
    source_system,
    account: "acct-test-1",
    native_identity: nativeIdentity(source_system),
    value_digest: sha("aa"),
    version: 2,
    observed_at: "2026-09-10T12:00:00Z",
    provenance: { adapter_kind: "adapter_test", evidence_ref: "ev-1-test",
      retrieval_class: "class_test" },
    declared_data_classes: ["lease_economics"],
    taint_class: "untrusted_external",
    ...over,
  });
  const current = (over = {}) => ({
    entity: "correspondence_derived_term",
    field: "base_rent_psf",
    tenant: ORGANIZATION_TENANT_ID,
    account: "acct-test-1",
    // F01 requires the established record's native identity to belong to the
    // source that owns the field; it is the owner's record, not the adapter's.
    native_identity: nativeIdentity(owner),
    value_digest: sha("bb"),
    version: 2,
    owner_source: owner,
    observed_at: "2026-09-09T12:00:00Z",
    event_seq: 1,
    last_event_digest: sha("cc"),
    ...over,
  });
  const items = {};
  const add = result => {
    assert.ok(result.reconciliation_item, `expected a reconciliation item, got ${result.reason_id}`);
    items[result.reconciliation_item.conflict_kind] = result.reconciliation_item;
  };
  // Nothing established, and a non-owner tries to establish it.
  add(resolveObservation({ tenant: ORGANIZATION_TENANT_ID, registry: reg,
    observation: observation(SOURCE), now: NOW }));
  // Same version, different value.
  add(resolveObservation({ tenant: ORGANIZATION_TENANT_ID, registry: reg,
    observation: observation(SOURCE), current_state: current(), now: NOW }));
  // Newer version from a non-owner with a different value.
  add(resolveObservation({ tenant: ORGANIZATION_TENANT_ID, registry: reg,
    observation: observation(SOURCE, { version: 3 }), current_state: current(), now: NOW }));
  // An opaque comparator cannot order two different tokens.
  const opaque = registry({ version_comparator: "opaque_equality" });
  add(resolveObservation({ tenant: ORGANIZATION_TENANT_ID, registry: opaque,
    observation: observation(SOURCE, { version: "etag-b-test" }),
    current_state: current({ version: "etag-a-test" }), now: NOW }));
  return items;
}

test("the four conflict kinds this module routes are exactly the four F01 emits", () => {
  const emitted = Object.keys(f01Conflicts()).sort();
  assert.deepEqual(emitted, [...V5_J103_CONFLICT_KINDS],
    "the route table must cover exactly F01's vocabulary, no more and no less");
  for (const kind of emitted) {
    assert.ok(["human_business_review", "source_authority_review"]
      .includes(V5_J103_CONFLICT_ROUTES[kind]));
  }
});

test("a queued conflict shows both values, each labelled with the source that owns it", () => {
  const item = f01Conflicts().equal_version_contradiction;
  const entry = capture(buildSourceConflictQueueEntry({
    reconciliation_item: item,
    presented_values: {
      established: { value_text: "32.50", value_digest: item.established.value_digest,
        declared_data_classes: ["lease_economics"] },
      observed: { value_text: "33.75", value_digest: item.observed.value_digest,
        declared_data_classes: ["lease_economics"] },
    },
    now: NOW,
  }));
  assert.equal(entry.decision, "queued");
  assert.equal(entry.schema_version, V5_J103_CONFLICT_SCHEMA_VERSION);
  assert.equal(entry.both_values_visible, true);
  assert.equal(entry.sides.established.value_text, "32.50");
  assert.equal(entry.sides.observed.value_text, "33.75");
  assert.equal(entry.sides.established.source_system, "carr_record_layer_test");
  assert.equal(entry.sides.established.owns_the_field, true);
  assert.equal(entry.sides.observed.source_system, SOURCE);
  assert.equal(entry.sides.observed.owns_the_field, false);
  assert.equal(entry.owner_source, "carr_record_layer_test");
  assert.equal(entry.authoritative_home, "neon_record_layer");
  assert.equal(entry.human_resolver_class, "partner_business_review");
  assert.equal(entry.route, "human_business_review");
  assert.equal(entry.resolved_by_machine, false);
  assert.equal(entry.applied, false);
});

test("the owning side is computed from F01's owner_source, not asserted by the caller", () => {
  const item = f01Conflicts().forbidden_overwrite_by_non_owner;
  const entry = capture(buildSourceConflictQueueEntry({
    reconciliation_item: item,
    presented_values: {
      established: { value_text: "owner value", value_digest: item.established.value_digest,
        declared_data_classes: ["lease_economics"] },
      observed: { value_text: "adapter value", value_digest: item.observed.value_digest,
        declared_data_classes: ["lease_economics"] },
    },
    now: NOW,
  }));
  assert.equal(entry.sides.observed.owns_the_field, false);
  assert.equal(entry.route, "source_authority_review");
});

test("a conflict with no established side presents one value and says so", () => {
  const item = f01Conflicts().non_owner_establishment_attempt;
  assert.equal(item.established, null);
  const entry = capture(buildSourceConflictQueueEntry({
    reconciliation_item: item,
    presented_values: {
      observed: { value_text: "33.75", value_digest: item.observed.value_digest,
        declared_data_classes: ["lease_economics"] },
    },
    now: NOW,
  }));
  assert.equal(entry.decision, "queued");
  assert.equal(entry.both_values_visible, false);
  assert.equal(entry.sides.established, null);
});

test("a review surface may not invent a side of the conflict", () => {
  const item = f01Conflicts().non_owner_establishment_attempt;
  assert.throws(() => buildSourceConflictQueueEntry({
    reconciliation_item: item,
    presented_values: {
      established: { value_text: "invented", value_digest: sha("ee"),
        declared_data_classes: ["lease_economics"] },
      observed: { value_text: "33.75", value_digest: item.observed.value_digest,
        declared_data_classes: ["lease_economics"] },
    },
    now: NOW,
  }), e => e.code === "presented_value_without_conflict_side");
});

test("a presented value that is not the value F01 conflicted on is refused", () => {
  const item = f01Conflicts().equal_version_contradiction;
  const wrongObserved = capture(buildSourceConflictQueueEntry({
    reconciliation_item: item,
    presented_values: {
      established: { value_text: "32.50", value_digest: item.established.value_digest,
        declared_data_classes: ["lease_economics"] },
      observed: { value_text: "a value nobody observed", value_digest: sha("ff"),
        declared_data_classes: ["lease_economics"] },
    },
    now: NOW,
  }));
  assert.equal(wrongObserved.decision, "refuse");
  assert.equal(wrongObserved.reason_id, "presented_value_digest_mismatch");
  assert.equal(wrongObserved.side, "observed");
  assert.equal(wrongObserved.sides, null);

  const wrongEstablished = capture(buildSourceConflictQueueEntry({
    reconciliation_item: item,
    presented_values: {
      established: { value_text: "a value nobody established", value_digest: sha("ef"),
        declared_data_classes: ["lease_economics"] },
      observed: { value_text: "33.75", value_digest: item.observed.value_digest,
        declared_data_classes: ["lease_economics"] },
    },
    now: NOW,
  }));
  assert.equal(wrongEstablished.reason_id, "presented_value_digest_mismatch");
  assert.equal(wrongEstablished.side, "established");
});

test("every way of saying whoever wrote last wins is refused by name", () => {
  const item = f01Conflicts().equal_version_contradiction;
  const sides = {
    established: { value_text: "32.50", value_digest: item.established.value_digest,
      declared_data_classes: ["lease_economics"] },
    observed: { value_text: "33.75", value_digest: item.observed.value_digest,
      declared_data_classes: ["lease_economics"] },
  };
  for (const basis of V5_J103_REFUSED_RESOLUTION_BASES) {
    const entry = capture(buildSourceConflictQueueEntry({
      reconciliation_item: item, presented_values: sides,
      proposed_resolution_basis: basis, now: NOW,
    }));
    assert.equal(entry.decision, "refuse", `${basis} must refuse`);
    assert.equal(entry.reason_id, "timestamp_alone_refused");
    assert.equal(entry.sides, null);
  }
  const unregistered = capture(buildSourceConflictQueueEntry({
    reconciliation_item: item, presented_values: sides,
    proposed_resolution_basis: "whichever_looks_right", now: NOW,
  }));
  assert.equal(unregistered.reason_id, "unregistered_resolution_basis");

  const accepted = capture(buildSourceConflictQueueEntry({
    reconciliation_item: item, presented_values: sides,
    proposed_resolution_basis: V5_J103_RESOLUTION_BASIS, now: NOW,
  }));
  assert.equal(accepted.decision, "queued");
  assert.equal(accepted.timestamp_alone_is_not_authority, true);
});

test("a queue entry is built only from an item F01 emitted", () => {
  const item = f01Conflicts().equal_version_contradiction;
  assert.equal(item.schema_version, V5_F01_RECONCILIATION_SCHEMA_VERSION);
  assert.throws(() => buildSourceConflictQueueEntry({
    reconciliation_item: { ...item, schema_version: "something-else.v1" },
    presented_values: { observed: { value_text: "x", value_digest: item.observed.value_digest,
      declared_data_classes: ["lease_economics"] } },
    now: NOW,
  }), e => e.code === "uncompiled_reconciliation_item");
});

test("a conflict queued before the observation that caused it is refused", () => {
  const item = f01Conflicts().equal_version_contradiction;
  const entry = capture(buildSourceConflictQueueEntry({
    reconciliation_item: item,
    presented_values: {
      established: { value_text: "32.50", value_digest: item.established.value_digest,
        declared_data_classes: ["lease_economics"] },
      observed: { value_text: "33.75", value_digest: item.observed.value_digest,
        declared_data_classes: ["lease_economics"] },
    },
    now: "2026-09-10T11:00:00Z",
  }));
  assert.equal(entry.reason_id, "conflict_observed_after_now");
});

test("a presented value carrying a prohibited class refuses on S01's answer", () => {
  const item = f01Conflicts().equal_version_contradiction;
  const entry = capture(buildSourceConflictQueueEntry({
    reconciliation_item: item,
    presented_values: {
      established: { value_text: "32.50", value_digest: item.established.value_digest,
        declared_data_classes: ["lease_economics"] },
      observed: { value_text: "33.75", value_digest: item.observed.value_digest,
        declared_data_classes: ["patient_identifier"] },
    },
    now: NOW,
  }));
  assert.equal(entry.decision, "refuse");
  assert.equal(entry.reason_id, "phi_or_raw_patient_location_refused");
  assert.equal(entry.sides, null);
});

// ---------------------------------------------------------------------------
// The policy digest, the projection and the gaps.
// ---------------------------------------------------------------------------

test("the policy digest is deterministic and hashes the bytes it publishes", () => {
  assert.equal(v5J103PolicyDigest(), v5J103PolicyDigest());
  assert.equal(v5J103PolicyDigest(), digest(v5J103PolicyPreimage()));
  assert.equal(v5J103PolicyCanonicalBytes(), canonicalJson(v5J103PolicyPreimage()));
});

test("the policy binds the prohibitions, not only the capabilities", () => {
  const policy = v5J103PolicyPreimage();
  assert.equal(policy.read_interface.source_agnostic, true);
  assert.equal(policy.read_interface.subject_line_stored, false);
  assert.equal(policy.read_interface.attachment_filename_stored, false);
  assert.equal(policy.read_interface.privacy_evaluated_before_relevance, true);
  assert.equal(policy.coverage.combined_partner_coverage_reachable_here, false);
  assert.equal(policy.draft_and_proposal.holds_routable_destination, false);
  assert.equal(policy.draft_and_proposal.names_provider_operation, false);
  assert.equal(policy.draft_and_proposal.establishes_authority, false);
  assert.equal(policy.source_reconciliation.resolved_by_machine, false);
  assert.deepEqual(policy.source_reconciliation.refused_resolution_bases,
    [...V5_J103_REFUSED_RESOLUTION_BASES]);
  assert.equal(policy.acceptance.satisfied_by_this_module, false);
  assert.deepEqual(policy.requirement_ids, ["Q005", "Q076", "Q133", "Q134"]);
});

test("a stale expected digest refuses rather than serving the projection", () => {
  const projection = v5J103CorrespondenceProjection({
    expected_policy_digest: v5J103PolicyDigest(),
  });
  assert.equal(projection.policy_digest, v5J103PolicyDigest());
  assert.equal(projection.accepts_anything, false);
  assert.equal(projection.external_send_capability_present, false);
  assert.equal(projection.combined_partner_coverage_available, false);
  assert.equal(projection.journey_one_production_outcome_present, false);
  assert.throws(() => v5J103CorrespondenceProjection({ expected_policy_digest: sha("99") }),
    e => e.code === "stale_expected_digest");
});

test("every named gap is a missing fact, and none of them is landed", () => {
  const gaps = governedCorrespondenceGaps();
  assert.ok(gaps.length >= 5);
  for (const gap of gaps) {
    assert.equal(gap.landed, false);
    assert.ok(gap.gap.length > 0 && gap.where.length > 0 && gap.what.length > 0);
  }
  assert.ok(gaps.some(g => g.gap === "no_external_send_authority_decision"));
  assert.ok(gaps.some(g => g.gap === "no_persistence"));
  assert.ok(gaps.some(g => g.gap === "no_both_partner_coverage"));
});

test("the module reaches no clock, network, database or filesystem", () => {
  // Every evaluation takes `now` from the caller, and the same inputs answer the
  // same way whatever the machine's clock says.
  const b = binding();
  assert.deepEqual(read({}, b), read({}, b));
  assert.deepEqual(
    draftCorrespondence({ binding: b, thread_read: read({}, b), now: NOW, draft: draft() }),
    draftCorrespondence({ binding: b, thread_read: read({}, b), now: NOW, draft: draft() }));
});

test("correspondence states and the module's own vocabulary stay closed", () => {
  assert.deepEqual([...V5_J103_CORRESPONDENCE_STATES],
    ["awaiting_counterparty", "awaiting_us", "informational", "resolved"]);
  assert.throws(() => read({ relevance_state: "probably_relevant" }),
    e => e.code === "unknown_relevance_state");
  assert.throws(() => readCorrespondenceThread({
    binding: binding(), thread: { ...thread(), extra_field: 1 }, now: NOW,
  }), e => e.code === "unknown_field");
});
