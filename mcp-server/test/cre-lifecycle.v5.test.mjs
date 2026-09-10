// V5-J102 — the CRE lifecycle kernel, proved case by case.
//
// Everything here is synthetic and pure: no database, no network, no provider,
// no filesystem, no clock. Every evaluation takes `now` from the fixture.
//
// NO FIXTURE NAMES A REAL THING. Every client, property, document, digest and
// reason below is unmistakably test data. Nothing here is a claim about a real
// CARR client, a real deal or a real Salesforce record.
//
// THE SUITE IS ORGANISED BY DECISION, not by function, because the thing worth
// checking is that each settled requirement is actually enforced somewhere — and
// in particular that the FOUR OVERRULED RECOMMENDATIONS did not creep back in.
// The four cases that would catch that regression are marked in their titles.

import test from "node:test";
import assert from "node:assert/strict";

import { digest } from "../src/artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import { V5_F01_AUTHORITY_INJECTION_FRAGMENTS } from "../src/record-source-authority.v5.js";
import {
  V5_J102_AUTHORITY_INJECTION_FRAGMENTS,
  V5_J102_DEAL_AXES,
  V5_J102_EVIDENCE_KINDS,
  V5_J102_SETTLED_DECISIONS,
  V5_J102_SETTLED_DECISION_IDS,
  V5_J102_TRANSITION_IDS,
  V5_J102_USER_CORRECTIONS,
  V5J102Error,
  applyLifecycleModelProposal,
  assertJ102DecisionBinding,
  classifyLegacyLifecycleRow,
  compareMigrationShadow,
  evaluateConcurrentEdit,
  evaluateLifecycleTransition,
  evaluateSelectedPropertyConstraint,
  projectLegacyCompatibilityView,
  projectOwnershipAndFreshness,
  projectSalesforceReference,
  v5J102DecisionSubsetDigest,
  v5J102EvidenceContract,
  v5J102MigrationReadiness,
  v5J102PolicyDigest,
  v5J102Projection,
  v5J102TransitionContract,
} from "../src/cre-lifecycle.v5.js";

// --- synthetic fixtures ----------------------------------------------------

const NOW = "2026-09-09T12:00:00.000Z";
const T = {
  early: "2026-09-01T09:00:00Z",
  mid: "2026-09-05T09:00:00Z",
  late: "2026-09-08T09:00:00Z",
  future: "2026-09-10T09:00:00Z",
};
const D = n => `sha256:${String(n).padStart(2, "0").repeat(32)}`;

const PARTNER = Object.freeze({
  slug: "joe", human: true, authorization_class: "verified_partner",
  derived_by: "authenticated_handler_context",
});
const AGENT = Object.freeze({
  slug: "codex", human: false, authorization_class: "sponsored_agent",
  derived_by: "server_established_transaction_context",
});

const relationship = (over = {}) => ({
  subject_kind: "relationship", subject_id: "rel-synthetic-1",
  relationship_state: "prospect", active_engagement_count: 0, ...over,
});
const engagement = (over = {}) => ({
  subject_kind: "engagement", subject_id: "eng-synthetic-1",
  relationship_id: "rel-synthetic-1", engagement_state: "active",
  representation_basis: "signed_engagement_letter",
  effective_from: T.early, effective_to: null, ...over,
});
const assignment = (over = {}) => ({
  subject_kind: "assignment", subject_id: "asg-synthetic-1",
  engagement_id: "eng-synthetic-1", assignment_phase: "search",
  open_negotiation_count: 0, selected_property_id: null,
  active_lease_draft_target_id: null, pending_deal_id: null,
  multi_target_exception_ref: null, ...over,
});
const negotiation = (over = {}) => ({
  subject_kind: "property_negotiation", subject_id: "neg-synthetic-1",
  assignment_id: "asg-synthetic-1", property_id: "prop-synthetic-1",
  negotiation_state: "loi_drafted", ...over,
});
const deal = (over = {}) => ({
  subject_kind: "deal", subject_id: "deal-synthetic-1",
  assignment_id: "asg-synthetic-1", property_id: "prop-synthetic-1",
  instrument_kind: "lease", deal_state: "pending", execution_state: "unexecuted",
  diligence_state: "not_applicable", closing_state: "not_reached",
  commission_agreement_state: "absent", invoice_state: "not_invoiced",
  payment_state: "unpaid", completion_state: "open",
  cancellation_reason: null, closing_date: null, ...over,
});

const provenance = reader => ({
  loaded_by: "server_record_layer", reader, loaded_at: NOW,
  integrity: "recomputed_from_committed_row",
});

const documentEvidence = (evidence_kind, over = {}) => ({
  evidence_kind, source: "f01_document", reference: "doc-synthetic-1",
  document: {
    document_id: "doc-synthetic-1", document_class: "synthetic_agreement",
    version_no: 1, content_digest: D(1),
    preparation_state: "approved_for_delivery", delivery_state: "delivered",
    signature_state: "fully_executed", validity_state: "effective",
    version_state: "current", effective_from: null, effective_to: null, ...over,
  },
  provenance: provenance("ops.f01_read.document"),
});

const recordEvidence = (evidence_kind, over = {}) => ({
  evidence_kind, source: "first_party_record", reference: "rec-synthetic-1",
  record: {
    record_kind: v5J102EvidenceContract(evidence_kind).record_kind,
    record_id: "rec-synthetic-1", content_digest: D(2),
    recorded_by: "joe", recorded_at: T.mid, ...over,
  },
  provenance: provenance("ops.j102_first_party_record"),
});

const artifactEvidence = (evidence_kind, over = {}) => ({
  evidence_kind, source: "f01_corporate_artifact", reference: D(3),
  artifact: {
    artifact_digest: D(3), content_digest: D(4),
    source_system: "synthetic_counterparty", evidence_class: "synthetic_countersigned_loi",
    observed_at: T.mid, ...over,
  },
  provenance: provenance("ops.f01_stored_artifact"),
});

const approvalEvidence = (evidence_kind, over = {}) => ({
  evidence_kind, source: "typed_approval", reference: "appr-synthetic-1",
  approval: {
    approval_kind: v5J102EvidenceContract(evidence_kind).approval_kind,
    approval_ref: "appr-synthetic-1", approver_slug: "joe",
    approver_authorization_class: "verified_partner", approved_at: T.early,
    scope: "synthetic fixture scope", ...over,
  },
  provenance: provenance("ops.j102_typed_approval"),
});

const evaluate = over => evaluateLifecycleTransition({
  tenant: ORGANIZATION_TENANT_ID, actor: PARTNER, now: NOW, ...over,
});

// --- the settled binding ---------------------------------------------------

test("the thirteen settled decisions are bound exactly, and drift is refused in both directions", () => {
  assert.equal(V5_J102_SETTLED_DECISION_IDS.length, 13);
  const binding = {
    decisions: Object.fromEntries(V5_J102_SETTLED_DECISION_IDS.map(id => [id, {
      source_evidence_digest: V5_J102_SETTLED_DECISIONS[id].source_evidence_digest,
      settled_requirement: V5_J102_SETTLED_DECISIONS[id].settled_requirement,
    }])),
    decision_subset_digest: v5J102DecisionSubsetDigest(),
  };
  assert.equal(assertJ102DecisionBinding(binding), true);

  // A missing decision is drift.
  const missing = structuredClone(binding);
  delete missing.decisions["Q094.D1"];
  delete missing.decision_subset_digest;
  assert.throws(() => assertJ102DecisionBinding(missing),
    e => e instanceof V5J102Error && e.code === "decision_binding_drift");

  // So is an extra one.
  const extra = structuredClone(binding);
  extra.decisions["Q999.D1"] = { source_evidence_digest: "a".repeat(64) };
  delete extra.decision_subset_digest;
  assert.throws(() => assertJ102DecisionBinding(extra),
    e => e instanceof V5J102Error && e.code === "decision_binding_drift");

  // So is a changed evidence digest on a decision that is present.
  const tampered = structuredClone(binding);
  tampered.decisions["Q078.D1"].source_evidence_digest = "b".repeat(64);
  delete tampered.decision_subset_digest;
  assert.throws(() => assertJ102DecisionBinding(tampered),
    e => e instanceof V5J102Error && e.code === "decision_binding_drift");
});

test("every transition cites settled decisions, declares its five columns, and creates no Tour", () => {
  const cited = new Set();
  for (const id of V5_J102_TRANSITION_IDS) {
    const contract = v5J102TransitionContract(id);
    assert.ok(contract.required_evidence_alternatives.length >= 1, `${id} requires evidence`);
    assert.ok(contract.permitted_actor_classes.length >= 1, `${id} names permitted actors`);
    assert.ok(contract.coupled_facts.length >= 1, `${id} names its coupled facts`);
    assert.ok(typeof contract.reversibility === "string", `${id} declares reversibility`);
    assert.equal(contract.free_form_stage_update_permitted, false);
    // Q072 and Q080 both moved active Tour behaviour to Journey 3. No transition
    // here may create or activate one, and the contract says so rather than the
    // absence being inferred.
    assert.equal(contract.creates_or_activates_tour, false, `${id} must not touch a Tour`);
    contract.decision_refs.forEach(ref => cited.add(ref));
  }
  // Nine of the thirteen are transition rules; the other four (Q081 migration,
  // Q083 Salesforce, Q103 concurrency, and Q079's separation, which is proved by
  // the subject shapes) are enforced by the other evaluators, and their own tests
  // below carry them.
  for (const id of ["Q069.D1", "Q072.D1", "Q077.D1", "Q078.D1", "Q080.D1",
    "Q082.D1", "Q094.D1", "Q095.D1", "Q096.D1"]) {
    assert.ok(cited.has(id), `${id} is cited by no transition`);
  }
});

test("the four overruled recommendations are recorded in code with their corrections", () => {
  const byRequirement = Object.fromEntries(
    V5_J102_USER_CORRECTIONS.map(c => [c.requirement_id, c]));
  assert.deepEqual(Object.keys(byRequirement).sort(), ["Q069", "Q078", "Q094", "Q095"]);
  for (const correction of V5_J102_USER_CORRECTIONS) {
    assert.ok(correction.superseded_recommendation.length > 0);
    assert.ok(correction.user_correction.length > 0);
    assert.ok(correction.encoded_as.length > 0);
  }
  // The exact wording that overturned the executed-only rule.
  assert.match(byRequirement.Q078.user_correction, /accepted LOI would be a pending deal/);
  assert.match(byRequirement.Q094.user_correction, /doesn't close until the final closing day/);
  assert.match(byRequirement.Q095.user_correction, /we dont do multiple lease drafts/);
});

test("the authority-injection fragment list agrees with F01's, so the two cannot drift", () => {
  assert.deepEqual([...V5_J102_AUTHORITY_INJECTION_FRAGMENTS].sort(),
    [...V5_F01_AUTHORITY_INJECTION_FRAGMENTS].sort());
});

test("the policy digest is stable across calls and covers the transition table", () => {
  assert.equal(v5J102PolicyDigest(), v5J102PolicyDigest());
  const projection = v5J102Projection();
  assert.equal(projection.policy_digest, v5J102PolicyDigest());
  assert.equal(projection.transitions.length, V5_J102_TRANSITION_IDS.length);
  assert.equal(projection.evidence_kinds.length, V5_J102_EVIDENCE_KINDS.length);
  assert.equal(projection.journey_three_excluded.tour_activation, false);
  assert.equal(projection.legacy.may_retire_callers, false);
});

// --- Q077 / Q069: what creates a Client -----------------------------------

test("Q077: an active signed representation agreement creates the Client AND the Engagement together", () => {
  const answer = evaluate({
    transition_id: "establish-client-and-engagement",
    subject: relationship(),
    evidence: [documentEvidence("signed_engagement_letter")],
    declared: { new_subject_id: "eng-synthetic-1" },
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "active_representation_creates_client_and_engagement");
  assert.equal(answer.proposed_state.relationship.relationship_state, "client");
  assert.equal(answer.proposed_state.engagement.engagement_state, "active");
  assert.equal(answer.proposed_state.engagement.representation_basis, "signed_engagement_letter");
  assert.equal(answer.proposed_state.relationship.active_engagement_count, 1);
  // Q082's coupled facts: BOTH rows, or neither.
  assert.deepEqual(answer.coupled_facts_committed,
    ["relationship.relationship_state", "engagement.engagement_state",
      "engagement.representation_basis"]);
  assert.equal(answer.atomic_or_refuse, true);
  assert.deepEqual(answer.events.map(e => e.event_kind),
    ["client_status_established", "engagement_opened"]);
  assert.equal(answer.pre_signature_work_remains_prospect, true);
  assert.equal(answer.salesforce_opportunity_considered, false);
  // The kernel decides; the store applies. An allow is permission, not a record.
  assert.equal(answer.applied, false);
});

test("Q077: pre-signature work stays a Prospect — an unsigned or ineffective agreement refuses", () => {
  for (const [over, axis] of [
    [{ signature_state: "partially_signed" }, "signature_state"],
    [{ validity_state: "draft" }, "validity_state"],
    [{ version_state: "superseded" }, "version_state"],
  ]) {
    const answer = evaluate({
      transition_id: "establish-client-and-engagement",
      subject: relationship(),
      evidence: [documentEvidence("signed_engagement_letter", over)],
      declared: { new_subject_id: "eng-synthetic-1" },
    });
    assert.equal(answer.decision, "refuse");
    assert.equal(answer.reason_id, "document_state_not_met");
    assert.equal(answer.document_axis, axis);
    assert.equal(answer.proposed_state, null);
    assert.deepEqual(answer.events, []);
  }
});

test("Q077: a dated window is checked when the record layer carries one, and reported when it does not", () => {
  // Not yet open.
  const early = evaluate({
    transition_id: "establish-client-and-engagement",
    subject: relationship(),
    evidence: [documentEvidence("signed_engagement_letter", { effective_from: T.future })],
    declared: { new_subject_id: "eng-synthetic-1" },
  });
  assert.equal(early.reason_id, "representation_not_yet_effective");

  // Already closed. A boundary instant that has arrived counts as closed.
  const expired = evaluate({
    transition_id: "establish-client-and-engagement",
    subject: relationship(),
    evidence: [documentEvidence("signed_engagement_letter",
      { effective_from: T.early, effective_to: T.mid })],
    declared: { new_subject_id: "eng-synthetic-1" },
  });
  assert.equal(expired.reason_id, "representation_no_longer_active");

  // Absent, which is what F01 actually returns today. The answer SAYS the window
  // was not carried rather than implying it was verified.
  const unwindowed = evaluate({
    transition_id: "establish-client-and-engagement",
    subject: relationship(),
    evidence: [documentEvidence("signed_engagement_letter")],
    declared: { new_subject_id: "eng-synthetic-1" },
  });
  assert.equal(unwindowed.decision, "allow");
  assert.equal(unwindowed.effective_window_carried, false);
  assert.equal(unwindowed.activeness_established_by, "f01_document_validity_state");
  assert.equal(unwindowed.proposed_state.engagement.effective_from, null);
});

test("Q077: an approved equivalent rests on a partner approval, and a sponsored-agent approval refuses", () => {
  const answer = evaluate({
    transition_id: "establish-client-and-engagement",
    subject: relationship(),
    evidence: [approvalEvidence("approved_representation_equivalent")],
    declared: { new_subject_id: "eng-synthetic-1" },
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.proposed_state.engagement.representation_basis,
    "approved_representation_equivalent");

  assert.throws(() => evaluate({
    transition_id: "establish-client-and-engagement",
    subject: relationship(),
    evidence: [approvalEvidence("approved_representation_equivalent",
      { approver_authorization_class: "sponsored_agent" })],
    declared: { new_subject_id: "eng-synthetic-1" },
  }), e => e instanceof V5J102Error && e.code === "approval_authority_insufficient");
});

test("Q077: naming BOTH representation bases is ambiguous and refuses rather than picking one", () => {
  const answer = evaluate({
    transition_id: "establish-client-and-engagement",
    subject: relationship(),
    evidence: [documentEvidence("signed_engagement_letter"),
      approvalEvidence("approved_representation_equivalent")],
    declared: { new_subject_id: "eng-synthetic-1" },
  });
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "ambiguous_evidence_basis");
});

test("a relationship that is already a Client cannot be made one again", () => {
  const answer = evaluate({
    transition_id: "establish-client-and-engagement",
    subject: relationship({ relationship_state: "client", active_engagement_count: 1 }),
    evidence: [documentEvidence("signed_engagement_letter")],
    declared: { new_subject_id: "eng-synthetic-2" },
  });
  assert.equal(answer.reason_id, "prerequisite_not_met");
  assert.equal(answer.unmet_axis, "relationship_state");
  assert.equal(answer.observed, "client");
});

// --- Q079 / Q080: the Assignment ------------------------------------------

test("Q079/Q080: search initiation opens an Assignment without duplicating the client", () => {
  const answer = evaluate({
    transition_id: "open-assignment",
    subject: assignment({ assignment_phase: "research" }),
    related: { relationship: relationship({ relationship_state: "client", active_engagement_count: 1 }),
      engagement: engagement() },
    evidence: [recordEvidence("search_initiation")],
    declared: { mandate_scope: "search" },
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.proposed_state.assignment.assignment_phase, "search");
  assert.equal(answer.duplicates_client, false);
  assert.equal(answer.closes_sibling_assignments, false);
});

test("Q077: an Assignment cannot be opened for a Prospect or under an inactive Engagement", () => {
  const prospect = evaluate({
    transition_id: "open-assignment",
    subject: assignment(),
    related: { relationship: relationship(), engagement: engagement() },
    evidence: [recordEvidence("search_initiation")],
    declared: { mandate_scope: "search" },
  });
  assert.equal(prospect.reason_id, "client_status_required");

  const terminated = evaluate({
    transition_id: "open-assignment",
    subject: assignment(),
    related: { relationship: relationship({ relationship_state: "client", active_engagement_count: 1 }),
      engagement: engagement({ engagement_state: "terminated" }) },
    evidence: [recordEvidence("search_initiation")],
    declared: { mandate_scope: "search" },
  });
  assert.equal(terminated.reason_id, "engagement_not_active");
});

test("Q072: research versus search is declared by the mandate, never guessed", () => {
  const answer = evaluate({
    transition_id: "open-assignment",
    subject: assignment(),
    related: { relationship: relationship({ relationship_state: "client", active_engagement_count: 1 }),
      engagement: engagement() },
    evidence: [recordEvidence("search_initiation")],
  });
  assert.equal(answer.reason_id, "mandate_scope_required");
});

// --- Q078 / Q095: LOIs, and the recommendation that was overruled ----------

test("OVERRULED-RULE GUARD (Q078): an LOI submission moves the Assignment to negotiation and creates NO Deal", () => {
  const answer = evaluate({
    transition_id: "record-loi-submission",
    subject: negotiation(),
    related: { assignment: assignment() },
    evidence: [documentEvidence("submitted_loi", { document_class: "letter_of_intent" })],
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.proposed_state.property_negotiation.negotiation_state, "loi_submitted");
  assert.equal(answer.proposed_state.assignment.assignment_phase, "negotiation");
  assert.equal(answer.creates_deal, false);
  assert.equal(answer.concurrent_negotiations_permitted, true);
  assert.equal(answer.proposed_state.deal, undefined, "no deal is proposed by an LOI submission");
});

test("OVERRULED-RULE GUARD (Q078): an ACCEPTED LOI still creates no Deal — commitment does", () => {
  const answer = evaluate({
    transition_id: "record-loi-acceptance",
    subject: negotiation({ negotiation_state: "loi_submitted" }),
    evidence: [artifactEvidence("counterparty_loi_acceptance")],
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.proposed_state.property_negotiation.negotiation_state, "loi_accepted");
  assert.equal(answer.creates_deal, false);
  assert.equal(answer.requires_selection_and_commitment_for_deal, true);
  assert.equal(answer.concurrent_acceptances_permitted, true);
  assert.equal(answer.proposed_state.deal, undefined);
});

test("OVERRULED-RULE GUARD (Q078): selection AND commitment create the PENDING Deal, not an executed one", () => {
  const answer = evaluate({
    transition_id: "commit-winning-property",
    subject: assignment({ assignment_phase: "negotiation", open_negotiation_count: 3 }),
    related: { property_negotiation: negotiation({ negotiation_state: "loi_accepted" }) },
    evidence: [recordEvidence("winner_selection_commitment")],
    declared: { instrument_kind: "lease", new_deal_id: "deal-synthetic-1" },
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "selection_and_commitment_create_pending_deal");
  // THE DEAL IS PENDING AND UNEXECUTED. The superseded recommendation would have
  // required an executed lease before any Deal existed at all.
  assert.equal(answer.proposed_state.deal.deal_state, "pending");
  assert.equal(answer.proposed_state.deal.execution_state, "unexecuted");
  assert.equal(answer.proposed_state.deal.closing_state, "not_reached");
  assert.equal(answer.proposed_state.assignment.assignment_phase, "committed");
  assert.equal(answer.proposed_state.assignment.selected_property_id, "prop-synthetic-1");
  assert.equal(answer.proposed_state.assignment.active_lease_draft_target_id, "prop-synthetic-1");
  assert.equal(answer.proposed_state.property_negotiation.negotiation_state, "selected_winner");
  // Q095's "without erasing the alternatives".
  assert.equal(answer.alternative_negotiations_retained, true);
  assert.equal(answer.alternative_negotiations_modified, 0);
  assert.deepEqual(answer.events.map(e => e.event_kind),
    ["winning_property_selected", "assignment_committed", "pending_deal_created"]);
});

test("Q078: commitment requires an ACCEPTED negotiation, and only a verified partner may commit", () => {
  const notAccepted = evaluate({
    transition_id: "commit-winning-property",
    subject: assignment({ assignment_phase: "negotiation", open_negotiation_count: 1 }),
    related: { property_negotiation: negotiation({ negotiation_state: "loi_submitted" }) },
    evidence: [recordEvidence("winner_selection_commitment")],
    declared: { instrument_kind: "lease", new_deal_id: "deal-synthetic-1" },
  });
  assert.equal(notAccepted.reason_id, "winning_negotiation_not_accepted");

  const asAgent = evaluate({
    transition_id: "commit-winning-property",
    actor: AGENT,
    subject: assignment({ assignment_phase: "negotiation", open_negotiation_count: 1 }),
    related: { property_negotiation: negotiation({ negotiation_state: "loi_accepted" }) },
    evidence: [recordEvidence("winner_selection_commitment")],
    declared: { instrument_kind: "lease", new_deal_id: "deal-synthetic-1" },
  });
  assert.equal(asAgent.reason_id, "actor_class_not_permitted");
  assert.deepEqual(asAgent.permitted_actor_classes, ["verified_partner"]);
});

test("OVERRULED-RULE GUARD (Q095): concurrent LOIs are unconstrained; only the SELECTED target is capped", () => {
  const free = assignment({ assignment_phase: "negotiation", open_negotiation_count: 5 });
  const available = evaluateSelectedPropertyConstraint({
    tenant: ORGANIZATION_TENANT_ID, assignment: free,
    candidate_property_id: "prop-synthetic-1",
  });
  assert.equal(available.decision, "allow");
  assert.equal(available.concurrent_lois_permitted, true);
  assert.equal(available.concurrent_acceptances_permitted, true);

  const taken = assignment({
    assignment_phase: "committed", selected_property_id: "prop-synthetic-9",
    active_lease_draft_target_id: "prop-synthetic-9", open_negotiation_count: 5,
  });
  const blocked = evaluateSelectedPropertyConstraint({
    tenant: ORGANIZATION_TENANT_ID, assignment: taken,
    candidate_property_id: "prop-synthetic-1",
  });
  assert.equal(blocked.decision, "refuse");
  assert.equal(blocked.reason_id, "winning_property_already_selected");
  assert.deepEqual(blocked.conflicts, ["selected_property_id", "active_lease_draft_target_id"]);
  // A stored reference on the row is NOT the approval.
  assert.equal(blocked.stored_exception_ref_is_not_an_approval, true);

  const excepted = evaluateSelectedPropertyConstraint({
    tenant: ORGANIZATION_TENANT_ID, assignment: taken,
    candidate_property_id: "prop-synthetic-1",
    exception_approval: approvalEvidence("multi_target_exception_approval"),
  });
  assert.equal(excepted.decision, "allow");
  assert.equal(excepted.reason_id, "explicit_approved_exception");
  assert.equal(excepted.exception_used, true);
  assert.equal(excepted.exception_approved_by, "joe");
});

test("Q095: a stored exception reference cannot lift the constraint through the commit path", () => {
  // The assignment CARRIES an exception ref and a different selected property.
  // The commit path passes no approval, so the constraint holds: a string in a
  // column is not an approval a partner granted.
  const answer = evaluate({
    transition_id: "commit-winning-property",
    subject: assignment({
      assignment_phase: "negotiation", open_negotiation_count: 2,
      selected_property_id: "prop-synthetic-9",
      multi_target_exception_ref: "appr-synthetic-forged",
    }),
    related: { property_negotiation: negotiation({ negotiation_state: "loi_accepted" }) },
    evidence: [recordEvidence("winner_selection_commitment")],
    declared: { instrument_kind: "lease", new_deal_id: "deal-synthetic-1" },
  });
  assert.equal(answer.decision, "refuse");
  assert.equal(answer.reason_id, "winning_property_already_selected");
});

test("Q095: an assignment already holding a pending Deal cannot commit a second one", () => {
  const answer = evaluate({
    transition_id: "commit-winning-property",
    subject: assignment({ assignment_phase: "negotiation", open_negotiation_count: 2,
      pending_deal_id: "deal-synthetic-0" }),
    related: { property_negotiation: negotiation({ negotiation_state: "loi_accepted" }) },
    evidence: [recordEvidence("winner_selection_commitment")],
    declared: { instrument_kind: "lease", new_deal_id: "deal-synthetic-1" },
  });
  assert.equal(answer.reason_id, "assignment_already_holds_pending_deal");
});

test("Q095: once committed, the Assignment refuses a fresh LOI rather than silently reopening", () => {
  const answer = evaluate({
    transition_id: "record-loi-submission",
    subject: negotiation({ subject_id: "neg-synthetic-2", property_id: "prop-synthetic-2" }),
    related: { assignment: assignment({ assignment_phase: "committed",
      selected_property_id: "prop-synthetic-1", pending_deal_id: "deal-synthetic-1" }) },
    evidence: [documentEvidence("submitted_loi")],
  });
  assert.equal(answer.reason_id, "assignment_already_committed");
});

// --- Q078 / Q094: execution, and the second overruled recommendation -------

test("Q078: lease signing marks the executed lease and says nothing about closing", () => {
  const answer = evaluate({
    transition_id: "record-lease-execution",
    subject: deal(),
    evidence: [documentEvidence("executed_lease", { document_class: "lease" })],
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.proposed_state.deal.execution_state, "executed");
  assert.equal(answer.proposed_state.deal.deal_state, "pending");
  assert.equal(answer.proposed_state.deal.closing_state, "not_reached");
  assert.equal(answer.execution_implies_closing, false);
});

test("OVERRULED-RULE GUARD (Q094): a signed purchase contract is EXECUTED and the Deal stays PENDING", () => {
  const answer = evaluate({
    transition_id: "record-purchase-contract-execution",
    subject: deal({ instrument_kind: "purchase" }),
    evidence: [documentEvidence("signed_purchase_contract",
      { document_class: "purchase_contract", validity_state: "draft" })],
  });
  assert.equal(answer.decision, "allow");
  // The pair of facts the superseded recommendation could not express.
  assert.equal(answer.legally_executed, true);
  assert.equal(answer.proposed_state.deal.execution_state, "executed");
  assert.equal(answer.proposed_state.deal.deal_state, "pending");
  assert.equal(answer.proposed_state.deal.diligence_state, "in_progress");
  assert.equal(answer.proposed_state.deal.closing_state, "not_reached");
  assert.equal(answer.signing_closes_deal, false);
});

test("Q094: the purchase transition will not run on a lease deal, and vice versa", () => {
  const purchaseOnLease = evaluate({
    transition_id: "record-purchase-contract-execution",
    subject: deal({ instrument_kind: "lease" }),
    evidence: [documentEvidence("signed_purchase_contract")],
  });
  assert.equal(purchaseOnLease.reason_id, "instrument_kind_not_permitted");

  const leaseOnPurchase = evaluate({
    transition_id: "record-lease-execution",
    subject: deal({ instrument_kind: "purchase" }),
    evidence: [documentEvidence("executed_lease")],
  });
  assert.equal(leaseOnPurchase.reason_id, "instrument_kind_not_permitted");
});

test("OVERRULED-RULE GUARD (Q094): signing is not closing — a deal closes only on the actual date", () => {
  const executedPurchase = deal({
    instrument_kind: "purchase", execution_state: "executed", diligence_state: "satisfied",
  });
  const answer = evaluate({
    transition_id: "record-deal-closing",
    subject: executedPurchase,
    evidence: [recordEvidence("final_closing_settlement", { closing_date: T.late })],
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.proposed_state.deal.deal_state, "closed");
  assert.equal(answer.proposed_state.deal.closing_state, "closed");
  assert.equal(answer.proposed_state.deal.closing_date, T.late);
  assert.equal(answer.closed_on_execution_evidence, false);
  // Q082's coupled facts: the business state, the closing axis and the date land
  // together or not at all.
  assert.deepEqual(answer.coupled_facts_committed,
    ["deal.deal_state", "deal.closing_state", "deal.closing_date"]);
});

test("Q094: a closing refuses while diligence is unresolved, and refuses a date that has not arrived", () => {
  const inDiligence = evaluate({
    transition_id: "record-deal-closing",
    subject: deal({ instrument_kind: "purchase", execution_state: "executed",
      diligence_state: "in_progress" }),
    evidence: [recordEvidence("final_closing_settlement", { closing_date: T.late })],
  });
  assert.equal(inDiligence.reason_id, "diligence_not_resolved");

  const scheduled = evaluate({
    transition_id: "record-deal-closing",
    subject: deal({ instrument_kind: "purchase", execution_state: "executed",
      diligence_state: "satisfied" }),
    evidence: [recordEvidence("final_closing_settlement", { closing_date: T.future })],
  });
  assert.equal(scheduled.reason_id, "closing_date_in_the_future");
});

test("Q094: an unexecuted deal cannot close, and a settlement record with no date is unreadable", () => {
  const unexecuted = evaluate({
    transition_id: "record-deal-closing",
    subject: deal({ execution_state: "unexecuted" }),
    evidence: [recordEvidence("final_closing_settlement", { closing_date: T.late })],
  });
  assert.equal(unexecuted.reason_id, "prerequisite_not_met");
  assert.equal(unexecuted.unmet_axis, "execution_state");

  // A closing_settlement with no date is not a closing this module can judge; it
  // is a record it cannot read, so it throws rather than refusing.
  assert.throws(() => evaluate({
    transition_id: "record-deal-closing",
    subject: deal({ execution_state: "executed" }),
    evidence: [recordEvidence("final_closing_settlement")],
  }), e => e instanceof V5J102Error && e.code === "missing_field");
});

test("Q080: diligence is its own axis and a failure does not cancel the deal", () => {
  const answer = evaluate({
    transition_id: "record-diligence-outcome",
    subject: deal({ instrument_kind: "purchase", execution_state: "executed",
      diligence_state: "in_progress" }),
    evidence: [recordEvidence("diligence_outcome")],
    declared: { diligence_result: "failed" },
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.proposed_state.deal.diligence_state, "failed");
  assert.equal(answer.proposed_state.deal.deal_state, "pending");
  assert.equal(answer.cancels_deal, false);
});

// --- Q080: the orthogonal axes --------------------------------------------

test("Q080: each money axis moves alone, and every other axis is echoed unchanged", () => {
  const base = deal({ execution_state: "executed", deal_state: "closed",
    closing_state: "closed", closing_date: T.late });
  const cases = [
    ["record-commission-agreement", documentEvidence("commission_agreement"), {},
      "commission_agreement_state", "agreed"],
    ["record-invoice-issued", recordEvidence("invoice_issued"), {}, "invoice_state", "invoiced"],
    ["record-payment", recordEvidence("payment_received"), { payment_level: "paid" },
      "payment_state", "paid"],
    ["record-completion", recordEvidence("completion_recorded"), {},
      "completion_state", "complete"],
  ];
  for (const [transition_id, evidence, declared, axis, value] of cases) {
    const answer = evaluate({ transition_id, subject: base, evidence: [evidence], declared });
    assert.equal(answer.decision, "allow", `${transition_id} allows`);
    assert.equal(answer.proposed_state.deal[axis], value);
    assert.equal(answer.axis, axis);
    for (const other of V5_J102_DEAL_AXES) {
      if (other === axis) continue;
      assert.equal(answer.proposed_state.deal[other], base[other],
        `${transition_id} must not move ${other}`);
    }
    assert.equal(answer.unchanged_axes.length, V5_J102_DEAL_AXES.length - 1);
  }
});

test("Q072/Q080: completion is NOT derived from payment — an unpaid deal may still complete", () => {
  const unpaid = deal({ execution_state: "executed", deal_state: "closed",
    closing_state: "closed", closing_date: T.late, payment_state: "unpaid",
    invoice_state: "not_invoiced" });
  const answer = evaluate({
    transition_id: "record-completion", subject: unpaid,
    evidence: [recordEvidence("completion_recorded")],
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.proposed_state.deal.completion_state, "complete");
  assert.equal(answer.proposed_state.deal.payment_state, "unpaid");
});

test("Q080: a payment level that would not move the state refuses rather than writing a no-op", () => {
  const answer = evaluate({
    transition_id: "record-payment",
    subject: deal({ payment_state: "partially_paid" }),
    evidence: [recordEvidence("payment_received")],
    declared: { payment_level: "partially_paid" },
  });
  assert.equal(answer.reason_id, "payment_level_would_not_change_state");
});

// --- Q096: a failed Deal ---------------------------------------------------

test("Q096: a failed pending Deal is cancelled with its reason, and the Client is untouched", () => {
  const client = relationship({ relationship_state: "client", active_engagement_count: 1 });
  const answer = evaluate({
    transition_id: "cancel-pending-deal",
    subject: deal({ assignment_id: "asg-synthetic-1" }),
    related: {
      assignment: assignment({ assignment_phase: "committed", open_negotiation_count: 2,
        selected_property_id: "prop-synthetic-1",
        active_lease_draft_target_id: "prop-synthetic-1",
        pending_deal_id: "deal-synthetic-1" }),
      relationship: client,
    },
    evidence: [recordEvidence("deal_failure_record",
      { reason: "synthetic fixture: landlord withdrew before lease execution" })],
    declared: { return_phase: "negotiation" },
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.proposed_state.deal.deal_state, "cancelled");
  assert.equal(answer.proposed_state.deal.cancellation_reason,
    "synthetic fixture: landlord withdrew before lease execution");
  // The assignment goes back to work, and the commitment is released.
  assert.equal(answer.proposed_state.assignment.assignment_phase, "negotiation");
  assert.equal(answer.proposed_state.assignment.selected_property_id, null);
  assert.equal(answer.proposed_state.assignment.active_lease_draft_target_id, null);
  assert.equal(answer.proposed_state.assignment.pending_deal_id, null);
  // Losing one property is not losing the client.
  assert.equal(answer.client_relationship_preserved, true);
  assert.equal(answer.proposed_state.relationship.relationship_state, "client");
  assert.equal(answer.history_preserved, true);
  assert.equal(answer.deal_row_deleted, false);
  assert.equal(answer.negotiation_history_deleted, false);
});

test("Q096: returning to negotiation requires an open negotiation to return to", () => {
  const answer = evaluate({
    transition_id: "cancel-pending-deal",
    subject: deal(),
    related: { assignment: assignment({ assignment_phase: "committed",
      open_negotiation_count: 0, pending_deal_id: "deal-synthetic-1" }) },
    evidence: [recordEvidence("deal_failure_record", { reason: "synthetic fixture reason" })],
    declared: { return_phase: "negotiation" },
  });
  assert.equal(answer.reason_id, "no_open_negotiation_to_return_to");
});

test("Q096: a failure record with no reason is unreadable, and a closed Deal cannot be cancelled", () => {
  assert.throws(() => evaluate({
    transition_id: "cancel-pending-deal",
    subject: deal(),
    related: { assignment: assignment() },
    evidence: [recordEvidence("deal_failure_record")],
    declared: { return_phase: "search" },
  }), e => e instanceof V5J102Error && e.code === "missing_field");

  const closed = evaluate({
    transition_id: "cancel-pending-deal",
    subject: deal({ deal_state: "closed", closing_state: "closed", closing_date: T.late }),
    related: { assignment: assignment() },
    evidence: [recordEvidence("deal_failure_record", { reason: "synthetic fixture reason" })],
    declared: { return_phase: "search" },
  });
  assert.equal(closed.reason_id, "prerequisite_not_met");
  assert.equal(closed.unmet_axis, "deal_state");
});

// --- Q082: evidence, actors and the refusal matrix -------------------------

test("Q082/Q072: model output is never evidence, and the refusal names the source", () => {
  assert.throws(() => evaluate({
    transition_id: "record-lease-execution",
    subject: deal(),
    evidence: [{ ...documentEvidence("executed_lease"), source: "assistant_text" }],
  }), e => e instanceof V5J102Error && e.code === "refused_evidence_source");

  assert.throws(() => evaluate({
    transition_id: "record-lease-execution",
    subject: deal(),
    evidence: [{ ...documentEvidence("executed_lease"), source: "model_output" }],
  }), e => e instanceof V5J102Error && e.code === "refused_evidence_source");
});

test("Q082: evidence that was not server-loaded is refused, however well-formed", () => {
  const forged = documentEvidence("executed_lease");
  forged.provenance = { ...forged.provenance, loaded_by: "caller" };
  assert.throws(() => evaluate({
    transition_id: "record-lease-execution", subject: deal(), evidence: [forged],
  }), e => e instanceof V5J102Error && e.code === "evidence_not_server_loaded");

  const untrusted = documentEvidence("executed_lease");
  untrusted.provenance = { ...untrusted.provenance, integrity: "trusted_readback" };
  assert.throws(() => evaluate({
    transition_id: "record-lease-execution", subject: deal(), evidence: [untrusted],
  }), e => e instanceof V5J102Error && e.code === "evidence_integrity_not_recomputed");
});

test("Q082: an actor that does not name a server derivation is refused", () => {
  assert.throws(() => evaluate({
    transition_id: "record-lease-execution", subject: deal(),
    evidence: [documentEvidence("executed_lease")],
    actor: { ...PARTNER, derived_by: "caller_supplied" },
  }), e => e instanceof V5J102Error && e.code === "actor_not_server_derived");
});

test("Q082: a caller cannot assert the outcome or smuggle authority into the request", () => {
  for (const [field, code] of [
    ["signed", "caller_asserted_fact_refused"],
    ["approved_equivalent", "caller_asserted_fact_refused"],
    ["executed_at", "caller_asserted_fact_refused"],
    ["acting_as", "caller_authority_field_refused"],
    ["override_prerequisites", "caller_authority_field_refused"],
  ]) {
    assert.throws(() => evaluate({
      transition_id: "record-lease-execution", subject: deal(),
      evidence: [documentEvidence("executed_lease")], [field]: true,
    }), e => e instanceof V5J102Error && e.code === code, `${field} must refuse as ${code}`);
  }
});

test("Q082: evidence for the wrong transition, duplicated, or extra is refused by name", () => {
  const wrong = evaluate({
    transition_id: "record-lease-execution", subject: deal(),
    evidence: [recordEvidence("invoice_issued")],
  });
  assert.equal(wrong.reason_id, "required_evidence_absent");
  assert.deepEqual(wrong.supplied_evidence_kinds, ["invoice_issued"]);

  assert.throws(() => evaluate({
    transition_id: "record-lease-execution", subject: deal(),
    evidence: [documentEvidence("executed_lease"), documentEvidence("executed_lease")],
  }), e => e instanceof V5J102Error && e.code === "duplicate_evidence_kind");

  const extra = evaluate({
    transition_id: "record-lease-execution", subject: deal(),
    evidence: [documentEvidence("executed_lease"), recordEvidence("invoice_issued")],
  });
  assert.equal(extra.reason_id, "unexpected_evidence_supplied");
  assert.deepEqual(extra.unexpected_evidence_kinds, ["invoice_issued"]);
});

test("Q082: a first-party record of the wrong kind cannot stand in for the right one", () => {
  const swapped = recordEvidence("deal_failure_record", { reason: "synthetic fixture reason" });
  swapped.record.record_kind = "invoice";
  assert.throws(() => evaluate({
    transition_id: "cancel-pending-deal", subject: deal(),
    related: { assignment: assignment() }, evidence: [swapped],
    declared: { return_phase: "search" },
  }), e => e instanceof V5J102Error && e.code === "evidence_record_kind_mismatch");
});

test("Q082: evidence recorded after the server instant is refused", () => {
  const answer = evaluate({
    transition_id: "record-invoice-issued", subject: deal(),
    evidence: [recordEvidence("invoice_issued", { recorded_at: T.future })],
  });
  assert.equal(answer.reason_id, "evidence_observed_after_server_time");
});

test("Q082: there is no free-form stage update — an unregistered transition cannot be named", () => {
  assert.throws(() => evaluate({
    transition_id: "set-deal-stage", subject: deal(),
    evidence: [documentEvidence("executed_lease")],
  }), e => e instanceof V5J102Error && e.code === "unknown_transition");
  for (const answerKey of ["free_form_stage_update", "model_output_used_as_evidence",
    "creates_or_activates_tour"]) {
    const answer = evaluate({
      transition_id: "record-lease-execution", subject: deal(),
      evidence: [documentEvidence("executed_lease")],
    });
    assert.equal(answer[answerKey], false, `${answerKey} must be false on every answer`);
  }
});

// --- Q072: the model seam --------------------------------------------------

test("Q072: a model proposes within its seam and advances nothing", () => {
  const ok = applyLifecycleModelProposal({
    seam: "inbound_lifecycle_document_kind", label: "lease", confidence: 0.9,
  });
  assert.equal(ok.decision, "allow");
  assert.equal(ok.advances_state, false);
  assert.equal(ok.is_evidence, false);
  assert.equal(ok.requires_deterministic_validation, true);

  const outside = applyLifecycleModelProposal({
    seam: "counterparty_response_hint", label: "definitely_signed",
  });
  assert.equal(outside.decision, "refuse");
  assert.equal(outside.reason_id, "model_label_outside_seam");

  const widening = applyLifecycleModelProposal({
    seam: "suggested_transition", label: "record-deal-closing", apply_now: true,
  });
  assert.equal(widening.decision, "refuse");
  assert.equal(widening.reason_id, "model_widening_refused");
  assert.equal(widening.offending_field, "apply_now");
});

// --- Q083: Salesforce ------------------------------------------------------

test("Q083: a Salesforce opportunity stays external, keeps its own labels, and sets no state", () => {
  const answer = projectSalesforceReference({
    tenant: ORGANIZATION_TENANT_ID,
    opportunity_id: "006SYNTHETIC001",
    opportunity_name: "Synthetic Medical Group - New Location",
    opportunity_phase: "Pending Deal",
    observed_at: T.mid,
    linked_subject_kind: "assignment",
    linked_subject_id: "asg-synthetic-1",
  });
  assert.equal(answer.decision, "allow");
  assert.equal(answer.reason_id, "external_reference_progressively_linked");
  // Salesforce's own words, preserved verbatim.
  assert.equal(answer.opportunity_name, "Synthetic Medical Group - New Location");
  assert.equal(answer.opportunity_phase, "Pending Deal");
  // And none of them is a DoctorCRE fact.
  assert.equal(answer.is_external_corporate_reference, true);
  assert.equal(answer.creates_doctorcre_client, false);
  assert.equal(answer.creates_doctorcre_deal, false);
  assert.equal(answer.sets_lifecycle_state, false);
  assert.equal(answer.phase_label_is_doctorcre_state, false);
});

test("Q083: an attempt to map a Salesforce label onto lifecycle state refuses by name", () => {
  for (const key of ["deal_state", "maps_to_assignment_phase", "derived_state"]) {
    assert.throws(() => projectSalesforceReference({
      tenant: ORGANIZATION_TENANT_ID, opportunity_id: "006SYNTHETIC001",
      opportunity_name: "Synthetic", opportunity_phase: "Pending Deal",
      observed_at: T.mid, [key]: "pending",
    }), e => e instanceof V5J102Error && e.code === "salesforce_label_mapping_refused",
    `${key} must refuse the mapping`);
  }
});

// --- Q103: concurrency -----------------------------------------------------

test("Q103: only demonstrably non-overlapping ROUTINE edits auto-merge", () => {
  const edit = (field, field_class, by) => ({
    field, field_class, value_digest: D(7), edited_by: by, edited_at: T.mid,
  });
  const merged = evaluateConcurrentEdit({
    tenant: ORGANIZATION_TENANT_ID, actor: PARTNER,
    base_version_digest: D(10), current_version_digest: D(11),
    incoming: [edit("internal_note", "routine", "joe")],
    concurrent: [edit("next_touch_hint", "routine", "dell")],
  });
  assert.equal(merged.decision, "allow");
  assert.equal(merged.merged, true);
  assert.deepEqual(merged.auto_merged_fields, ["internal_note"]);
  assert.equal(merged.last_writer_wins, false);
  assert.equal(merged.silent_overwrite, false);
});

test("Q103: overlapping edits and material-class edits reconcile visibly with both versions kept", () => {
  const edit = (field, field_class, by) => ({
    field, field_class, value_digest: D(7), edited_by: by, edited_at: T.mid,
  });
  const overlapping = evaluateConcurrentEdit({
    tenant: ORGANIZATION_TENANT_ID, actor: PARTNER,
    base_version_digest: D(10), current_version_digest: D(11),
    incoming: [edit("internal_note", "routine", "joe")],
    concurrent: [edit("internal_note", "routine", "dell")],
  });
  assert.equal(overlapping.decision, "reconcile");
  assert.equal(overlapping.reason_id, "overlapping_edits_require_reconciliation");
  assert.deepEqual(overlapping.overlapping_fields, ["internal_note"]);
  assert.equal(overlapping.reconciliation_item.visible, true);
  assert.equal(overlapping.reconciliation_item.resolved_by_machine, false);
  assert.equal(overlapping.reconciliation_item.incoming_edits.length, 1);
  assert.equal(overlapping.reconciliation_item.concurrent_edits.length, 1);
  assert.deepEqual(overlapping.preserved_versions, ["base", "current", "incoming"]);

  for (const cls of ["lifecycle", "financial", "recipient", "document"]) {
    const material = evaluateConcurrentEdit({
      tenant: ORGANIZATION_TENANT_ID, actor: PARTNER,
      base_version_digest: D(10), current_version_digest: D(11),
      incoming: [edit(`${cls}_field`, cls, "joe")],
      concurrent: [edit("internal_note", "routine", "dell")],
    });
    assert.equal(material.decision, "reconcile", `${cls} must reconcile`);
    assert.equal(material.reason_id, "material_class_edits_require_reconciliation");
    assert.equal(material.merged, false);
  }
});

test("Q103: an uncharacterized concurrent change reconciles rather than merging on an absence", () => {
  const answer = evaluateConcurrentEdit({
    tenant: ORGANIZATION_TENANT_ID, actor: PARTNER,
    base_version_digest: D(10), current_version_digest: D(11),
    incoming: [{ field: "internal_note", field_class: "routine", value_digest: D(7),
      edited_by: "joe", edited_at: T.mid }],
  });
  assert.equal(answer.decision, "reconcile");
  assert.equal(answer.reason_id, "concurrent_change_not_characterized");
  assert.equal(answer.merged, false);
});

test("Q103: ownership and freshness are projected from trusted state, and unknown stays unknown", () => {
  const known = projectOwnershipAndFreshness({
    tenant: ORGANIZATION_TENANT_ID, subject_kind: "deal", subject_id: "deal-synthetic-1",
    owner_slug: "joe", last_material_change_at: T.late, last_material_change_by: "joe",
    state_digest: D(12), active_automation: [], now: NOW,
  });
  assert.equal(known.owner_known, true);
  assert.equal(known.freshness_known, true);
  assert.equal(known.active_automation_known, true);
  assert.deepEqual(known.active_automation, []);
  assert.ok(known.freshness_age_seconds > 0);
  assert.deepEqual(known.inferred_fields, []);

  // An UNSUPPLIED automation list is unknown, and is never rendered as "none".
  const unknown = projectOwnershipAndFreshness({
    tenant: ORGANIZATION_TENANT_ID, subject_kind: "deal", subject_id: "deal-synthetic-1",
    state_digest: D(12), now: NOW,
  });
  assert.equal(unknown.owner_known, false);
  assert.equal(unknown.freshness_known, false);
  assert.equal(unknown.freshness_age_seconds, null);
  assert.equal(unknown.active_automation_known, false);
  assert.equal(unknown.active_automation, null);

  assert.throws(() => projectOwnershipAndFreshness({
    tenant: ORGANIZATION_TENANT_ID, subject_kind: "deal", subject_id: "deal-synthetic-1",
    state_digest: D(12), last_material_change_at: T.future, now: NOW,
  }), e => e instanceof V5J102Error && e.code === "last_material_change_after_now");
});

// --- Q081: migration -------------------------------------------------------

test("Q081: legacy rows are classified from evidence, and ambiguity goes to reconciliation", () => {
  const executed = documentEvidence("executed_lease");
  const closing = recordEvidence("final_closing_settlement", { closing_date: T.late });

  const assignmentRow = classifyLegacyLifecycleRow({
    tenant: ORGANIZATION_TENANT_ID, legacy_row_id: "legacy-1", legacy_phase: "site_selection",
  });
  assert.equal(assignmentRow.classification, "assignment");
  assert.equal(assignmentRow.projected_assignment_phase, "search");
  assert.equal(assignmentRow.label_used_as_state, false);

  // Q094 on the migration path: executed with no closing evidence lands PENDING.
  const pendingDeal = classifyLegacyLifecycleRow({
    tenant: ORGANIZATION_TENANT_ID, legacy_row_id: "legacy-2", legacy_phase: "due_diligence",
    executed_instrument_evidence: executed,
  });
  assert.equal(pendingDeal.classification, "deal");
  assert.equal(pendingDeal.projected_deal_state, "pending");
  assert.equal(pendingDeal.closed_on_execution_evidence, false);

  const closedDeal = classifyLegacyLifecycleRow({
    tenant: ORGANIZATION_TENANT_ID, legacy_row_id: "legacy-3", legacy_phase: "closing",
    executed_instrument_evidence: executed, closing_evidence: closing,
  });
  assert.equal(closedDeal.projected_deal_state, "closed");

  // A post-execution label with no executed evidence is exactly the ambiguous row.
  const ambiguous = classifyLegacyLifecycleRow({
    tenant: ORGANIZATION_TENANT_ID, legacy_row_id: "legacy-4", legacy_phase: "closing",
  });
  assert.equal(ambiguous.decision, "reconcile");
  assert.equal(ambiguous.classification, "requires_reconciliation");
  assert.deepEqual(ambiguous.missing_evidence, ["executed_instrument_evidence"]);

  // Q082's coupled-fact defect, found in the legacy data.
  const contradictory = classifyLegacyLifecycleRow({
    tenant: ORGANIZATION_TENANT_ID, legacy_row_id: "legacy-5", legacy_phase: "closing",
    legacy_closed_flag: true, legacy_outcome: "open",
  });
  assert.equal(contradictory.reason_id, "closed_flag_and_outcome_disagree");
  assert.equal(contradictory.requires_reconciliation, true);
});

test("Q081: the compatibility view is a projection, and the shadow reports without repairing", () => {
  const view = projectLegacyCompatibilityView({
    tenant: ORGANIZATION_TENANT_ID,
    assignment: assignment({ assignment_phase: "committed" }),
    deal: deal({ execution_state: "executed", instrument_kind: "purchase",
      diligence_state: "in_progress" }),
  });
  assert.equal(view.legacy_phase, "due_diligence");
  assert.equal(view.legacy_phase_source, "deal");
  assert.equal(view.legacy_closed, false);
  assert.equal(view.legacy_outcome, "open");
  // The four properties that keep it a view rather than a second home.
  assert.equal(view.authoritative, false);
  assert.equal(view.writable, false);
  assert.equal(view.derived_from_current_records, true);
  assert.equal(view.retires_any_caller, false);

  const clean = compareMigrationShadow({
    tenant: ORGANIZATION_TENANT_ID,
    legacy_row: { legacy_row_id: "legacy-6", phase: "due_diligence", closed: false, outcome: "open" },
    projected_view: view,
  });
  assert.equal(clean.decision, "allow");
  assert.deepEqual(clean.differences, []);

  const drifted = compareMigrationShadow({
    tenant: ORGANIZATION_TENANT_ID,
    legacy_row: { legacy_row_id: "legacy-7", phase: "closing", closed: true, outcome: "won" },
    projected_view: view,
  });
  assert.equal(drifted.decision, "reconcile");
  assert.equal(drifted.differences.length, 3);
  assert.equal(drifted.requires_human_reconciliation, true);
  // A shadow that repaired the row it is comparing against would prove itself.
  assert.equal(drifted.legacy_row_modified, false);
  assert.equal(drifted.projection_modified, false);
});

test("Q081: migration is never claimed complete, and the missing facts are named", () => {
  const bare = v5J102MigrationReadiness({ tenant: ORGANIZATION_TENANT_ID });
  assert.equal(bare.decision, "refuse");
  assert.equal(bare.reason_id, "caller_census_absent");
  assert.equal(bare.may_retire_callers, false);
  assert.equal(bare.migration_complete, false);
  assert.equal(bare.big_bang_rename, false);
  assert.deepEqual(bare.missing_facts.map(f => f.fact),
    ["exact_caller_census", "shadow_comparison_clean_run"]);

  // A census a CALLER hands in is a claim, not a verification. It changes the
  // reason and nothing else.
  const claimed = v5J102MigrationReadiness({
    tenant: ORGANIZATION_TENANT_ID, shadow_runs: 500,
    caller_census: { census_ref: "census-synthetic-1", enumerated_callers: 12,
      migrated_callers: 12, attested_by: "joe", attested_at: T.late },
  });
  assert.equal(claimed.decision, "refuse");
  assert.equal(claimed.reason_id, "caller_census_supplied_but_unverified");
  assert.equal(claimed.may_retire_callers, false);
  assert.equal(claimed.caller_census_verified, false);
  assert.equal(claimed.migration_complete, false);
});

// --- the whole journey, end to end -----------------------------------------

test("Journey 1 end to end: Prospect through Client, Assignment, multiple LOIs, pending Deal and close", () => {
  // 1. The ETL lands. Client and Engagement together.
  const established = evaluate({
    transition_id: "establish-client-and-engagement",
    subject: relationship(),
    evidence: [documentEvidence("signed_engagement_letter")],
    declared: { new_subject_id: "eng-synthetic-1" },
  });
  assert.equal(established.decision, "allow");
  const client = established.proposed_state.relationship;
  const eng = established.proposed_state.engagement;

  // 2. A search opens. Q079: the client is not duplicated.
  const opened = evaluate({
    transition_id: "open-assignment",
    subject: assignment({ assignment_phase: "research" }),
    related: { relationship: client, engagement: eng },
    evidence: [recordEvidence("search_initiation")],
    declared: { mandate_scope: "search" },
  });
  let asg = opened.proposed_state.assignment;

  // 3. THREE LOIs go out, which is normal practice and not an exception.
  const negotiations = [];
  for (const n of [1, 2, 3]) {
    const submitted = evaluate({
      transition_id: "record-loi-submission",
      subject: negotiation({ subject_id: `neg-synthetic-${n}`, property_id: `prop-synthetic-${n}` }),
      related: { assignment: asg },
      evidence: [documentEvidence("submitted_loi")],
    });
    assert.equal(submitted.decision, "allow");
    assert.equal(submitted.creates_deal, false, "no LOI submission creates a Deal");
    asg = submitted.proposed_state.assignment;
    negotiations.push(submitted.proposed_state.property_negotiation);
  }
  assert.equal(asg.open_negotiation_count, 3);

  // 4. TWO of them are accepted. Still no Deal.
  const accepted = [0, 1].map(i => {
    const answer = evaluate({
      transition_id: "record-loi-acceptance",
      subject: negotiations[i],
      evidence: [artifactEvidence("counterparty_loi_acceptance")],
    });
    assert.equal(answer.creates_deal, false, "no acceptance creates a Deal");
    return answer.proposed_state.property_negotiation;
  });

  // 5. One winner is chosen and committed to. NOW the pending Deal exists.
  const committed = evaluate({
    transition_id: "commit-winning-property",
    subject: asg,
    related: { property_negotiation: accepted[0] },
    evidence: [recordEvidence("winner_selection_commitment")],
    declared: { instrument_kind: "lease", new_deal_id: "deal-synthetic-1" },
  });
  assert.equal(committed.decision, "allow");
  assert.equal(committed.alternative_negotiations_modified, 0,
    "the losing negotiations are retained untouched");
  const pending = committed.proposed_state.deal;
  assert.equal(pending.deal_state, "pending");

  // 6. The lease is signed. Executed, still pending.
  const executed = evaluate({
    transition_id: "record-lease-execution", subject: pending,
    evidence: [documentEvidence("executed_lease")],
  });
  assert.equal(executed.proposed_state.deal.execution_state, "executed");
  assert.equal(executed.proposed_state.deal.deal_state, "pending");

  // 7. The closing actually happens, on a date somebody recorded.
  const closed = evaluate({
    transition_id: "record-deal-closing", subject: executed.proposed_state.deal,
    evidence: [recordEvidence("final_closing_settlement", { closing_date: T.late })],
  });
  assert.equal(closed.proposed_state.deal.deal_state, "closed");
  assert.equal(closed.proposed_state.deal.closing_date, T.late);
  // The money and completion axes are untouched by the close.
  assert.equal(closed.proposed_state.deal.invoice_state, "not_invoiced");
  assert.equal(closed.proposed_state.deal.payment_state, "unpaid");
  assert.equal(closed.proposed_state.deal.completion_state, "open");
});

test("the failed-deal branch returns the Assignment to work without touching the Client", () => {
  const client = relationship({ relationship_state: "client", active_engagement_count: 1 });
  const committedAssignment = assignment({
    assignment_phase: "committed", open_negotiation_count: 2,
    selected_property_id: "prop-synthetic-1",
    active_lease_draft_target_id: "prop-synthetic-1",
    pending_deal_id: "deal-synthetic-1",
  });
  const cancelled = evaluate({
    transition_id: "cancel-pending-deal",
    subject: deal(),
    related: { assignment: committedAssignment, relationship: client },
    evidence: [recordEvidence("deal_failure_record",
      { reason: "synthetic fixture: terms could not be agreed" })],
    declared: { return_phase: "negotiation" },
  });
  const reopened = cancelled.proposed_state.assignment;

  // And the assignment can immediately take a new LOI again.
  const resubmitted = evaluate({
    transition_id: "record-loi-submission",
    subject: negotiation({ subject_id: "neg-synthetic-9", property_id: "prop-synthetic-9" }),
    related: { assignment: reopened },
    evidence: [documentEvidence("submitted_loi")],
  });
  assert.equal(resubmitted.decision, "allow");
  assert.equal(cancelled.proposed_state.relationship.relationship_state, "client");
  assert.equal(digest(cancelled.proposed_state.relationship), digest(client),
    "the client row is written back byte-identical");
});
