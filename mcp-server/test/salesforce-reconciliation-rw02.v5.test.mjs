// V5-RW02 — attended Salesforce reconciliation and per-action evaluation,
// proved clause by clause against the reviewed catalog's `checkable_done` and
// the eight mapped decisions.
//
// SYNTHETIC DATA ONLY. Every origin is under the reserved `.invalid` TLD, every
// opportunity id is a made-up 006-prefixed id, and every case, deal and party
// ref is a placeholder. No real client, deal, org or person appears here.
//
// The F06 answers are REAL: envelopes, capabilities and presentations are built
// through workflow-effect-envelope.v5.js and the actor authority through
// global-boundaries.v5.js, so a drift in either breaks this suite rather than
// the wiring rotting silently.
//
//   node --test mcp-server/test/salesforce-reconciliation-rw02.v5.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { evaluateActorAuthority, V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID } from "../src/identity.js";
import {
  normalizeEffectAttemptObservation,
  normalizeEffectCapability,
  normalizeEffectEnvelope,
  normalizeEffectPresentation,
} from "../src/workflow-effect-envelope.v5.js";
import * as rw02 from "../src/salesforce-reconciliation-rw02.v5.js";

const {
  V5RW02Error,
  V5_RW02_ACTION_KIND_KEYS,
  V5_RW02_DECISION_IDS,
  V5_RW02_EXCLUDED_ACTIONS,
  V5_RW02_PAGE_CHECKS,
  V5_RW02_PUBLIC_SURFACE,
  V5_RW02_RUNTIME_EVIDENCE_INPUTS,
  V5_RW02_SEAMS,
  V5_RW02_SETTLED_DECISIONS,
  V5_RW02_EVALUATION_WINDOW_SEAM,
  buildActionPreview,
  evaluateActionAdmission,
  evaluateActionTrustWindow,
  evaluateDuplicateSearch,
  evaluatePageObservation,
  evaluateResume,
  evaluateWriteReadback,
  rw02StepKey,
  rw02WorkflowId,
  v5Rw02PolicyDigest,
  v5Rw02Projection,
} = rw02;

const SRC = fileURLToPath(new URL("../src/salesforce-reconciliation-rw02.v5.js", import.meta.url));
const TOOLS = fileURLToPath(new URL("../src/tools.js", import.meta.url));

const T = ORGANIZATION_TENANT_ID;
const ORIGIN = "https://synthetic-org.invalid";
const ORG = "00Dsynthetic0001";
const SEAT = "sf-seat-synthetic-partner";
const UI = "sha256:" + "1".repeat(64);
const OPP = "006SYNTH0000001";
const OPP2 = "006SYNTH0000002";
const OPP3 = "006SYNTH0000003";
const NOW = "2026-09-24T12:00:00Z";
const ISSUED = "2026-09-24T11:58:00Z";
const EXPIRES = "2026-09-24T12:08:00Z";
const CONFIRMED = "2026-09-24T11:59:00Z";

const CASE = Object.freeze({ workflow_ref: "rw02-case-synthetic-1", deal_ref: "deal-synthetic-1",
  engagement_ref: "engagement-synthetic-1" });

const isErr = code => e => e instanceof V5RW02Error && e.code === code;

// ------------------------------------------------------------------ fixtures

const page = (obs = {}, bind = {}, mode = "attended") => ({
  execution_mode: mode,
  binding: {
    expected_origin: ORIGIN, expected_org_id: ORG, expected_account_ref: SEAT,
    expected_ui_contract_digest: UI, ...bind,
  },
  observation: {
    origin: ORIGIN, org_id: ORG, signed_in_account_ref: SEAT, ui_contract_digest: UI,
    challenge: "none", result_consistency: "consistent", ...obs,
  },
});

const stepKey = (action_kind, intent_ordinal = 1, kase = CASE) =>
  rw02StepKey({ tenant: T, case: kase, action_kind, intent_ordinal });

const CREATE_FIELDS = Object.freeze([
  { field: "Name", value: "Synthetic Clinic Lease", semantics: "ordinary", provenance: "partner_entered" },
  { field: "StageName", value: "Research", semantics: "phase", provenance: "partner_entered" },
  { field: "Out_of_Market_Deal__c", value: false, semantics: "out_of_market_flag", provenance: "partner_entered" },
  { field: "Total_Commission__c", value: 1000, semantics: "commission_placeholder", provenance: "partner_entered" },
  { field: "CloseDate", value: "2027-01-01", semantics: "close_date_placeholder", provenance: "partner_entered" },
]);

const preview = (action_kind = "opportunity_create", over = {}) => buildActionPreview({
  tenant: T, action_kind, case: CASE, step_key: stepKey(action_kind),
  fields: CREATE_FIELDS.map(f => ({ ...f })), ...over,
});

const docPreview = () => preview("etl_document_prepare", {
  target_opportunity_id: OPP,
  fields: [{ field: "Template_Ref__c", value: "etl-template-synthetic", semantics: "ordinary",
    provenance: "doctorcre_record" }],
});

const dupSearch = (candidates = [], completeness = "complete", step = stepKey("opportunity_create")) => ({
  tenant: T, case: CASE, step_key: step, page: page(), search: { completeness, candidates },
});

const PRINCIPALS = Object.freeze({
  actor_slug: "joe", account_ref: SEAT, deal_owner_slug: "joe", signer_slug: "joe",
});
const ACTION = "business.update_deal";
const CAPNAME = "deal.write";

const envelopeFor = (p, over = {}) => normalizeEffectEnvelope({
  tenant: T, envelope_id: "env-synthetic-1", workflow_id: rw02WorkflowId(p.preview.case),
  step_id: p.preview.step_key, action: ACTION, principals: { ...PRINCIPALS },
  payload_digest: p.preview_digest, caps: [], autonomy_tier_label: "tier-attended", ...over,
});
const capabilityFor = (env, over = {}) => normalizeEffectCapability({
  tenant: T, capability_id: "cap-synthetic-1", envelope_digest: env.envelope_digest, capability: CAPNAME,
  nonce: "nonce-synthetic-1", principals: { ...PRINCIPALS }, autonomy_tier_label: "tier-attended",
  issued_at: ISSUED, expires_at: EXPIRES, ...over,
});
const presentationFor = (env, over = {}) => normalizeEffectPresentation({
  presentation_id: "pres-synthetic-1", envelope_digest: env.envelope_digest, capability_id: "cap-synthetic-1",
  requested_action: ACTION, payload_digest: env.payload_digest, presented_principals: { ...PRINCIPALS },
  presented_autonomy_tier_label: "tier-attended", nonce_state: "unconsumed", capability_state: "active",
  cap_observations: [], ...over,
});
const authority = () => evaluateActorAuthority({
  actor: Object.freeze({ slug: "joe", display: "Joe", human: true, via: "oauth-google", client_id: null,
    sponsoring_human_slug: null, human_slug: null, sponsor_required: false }),
  action: ACTION, tenant: T, now: NOW,
  controls: { deal_owner_slug: "joe", account_slug: "joe", policy_scope: [ACTION], capabilities: [CAPNAME] },
});

const confirmationFor = (p, over = {}) => ({
  confirmation_ref: "confirm-synthetic-1", confirmed_at: CONFIRMED, preview_digest: p.preview_digest,
  action_kind: p.preview.action_kind, step_key: p.preview.step_key, ...over,
});

const admission = (p = preview(), over = {}, f06over = {}) => {
  const env = f06over.envelope ?? envelopeFor(p);
  return evaluateActionAdmission({
    tenant: T, execution_mode: "attended", page: page(), preview: p.preview,
    ...(p.preview.action_kind === "opportunity_create" ? { duplicate_search: dupSearch() } : {}),
    confirmation: confirmationFor(p),
    f06: {
      envelope: env, capability: f06over.capability ?? capabilityFor(env),
      presentation: f06over.presentation ?? presentationFor(env), authority: authority(), now: NOW,
    },
    ...over,
  });
};

const attemptFor = (env, over = {}) => normalizeEffectAttemptObservation({
  attempt_id: "att-synthetic-1", envelope_digest: env.envelope_digest, capability_id: "cap-synthetic-1",
  consumption: { state: "committed", committed_seq: 1 }, provider_call: { state: "started", started_seq: 2 },
  outcome: { state: "succeeded" }, ...over,
});

const readbackOf = (p, overrides = {}, opp = OPP) => ({
  opportunity_id: opp, completeness: "complete",
  fields: [...p.preview.fields.map(f => ({ field: f.field, value: f.field in overrides ? overrides[f.field] : f.value })),
    { field: "LastModifiedDate", value: "2026-09-24T12:00:01Z" }],
});

const readback = (p = preview(), over = {}, attemptOver = {}) => {
  const env = envelopeFor(p);
  return evaluateWriteReadback({
    tenant: T, page: page(), preview: p.preview, evidence_class: "fixture", observed_at: NOW,
    f06: { envelope: env, capability: capabilityFor(env), attempt: attemptFor(env, attemptOver) },
    provider_readback: readbackOf(p), ...over,
  });
};

function assertGrantsNothing(answer) {
  assert.equal(answer.outward_effect_granted, false);
  assert.equal(answer.autonomy_active, false);
  assert.equal(answer.attended_activation_receipt_present, false);
  assert.deepEqual(answer.effects, V5_NO_EFFECTS);
}

// ---------------------------------------------------------------------------
// Contract identity.
// ---------------------------------------------------------------------------

test("the eight mapped decisions are exactly the catalog's, each with verbatim text and a digest", () => {
  assert.deepEqual([...V5_RW02_DECISION_IDS],
    ["Q059.D5", "Q070.D1", "Q084.D1", "Q097.D1", "Q098.D1", "Q099.D1", "Q100.D1", "Q123.D4"]);
  for (const id of V5_RW02_DECISION_IDS) {
    assert.match(V5_RW02_SETTLED_DECISIONS[id].source_evidence_digest, /^[0-9a-f]{64}$/, id);
    assert.ok(V5_RW02_SETTLED_DECISIONS[id].settled_requirement.length > 40, id);
  }
  assert.match(V5_RW02_SETTLED_DECISIONS["Q084.D1"].settled_requirement, /immediate stops on authentication/);
  assert.match(V5_RW02_SETTLED_DECISIONS["Q099.D1"].settled_requirement, /never use global trust/);
});

test("exports equal the declared public surface exactly", () => {
  assert.deepEqual(Object.keys(rw02).sort(), [...V5_RW02_PUBLIC_SURFACE].sort());
});

test("the projection grants nothing, lists every runtime input as missing and names every seam", () => {
  const p = v5Rw02Projection();
  assertGrantsNothing(p);
  assert.deepEqual(p.runtime_inputs_missing, [...V5_RW02_RUNTIME_EVIDENCE_INPUTS]);
  assert.ok(p.runtime_inputs_missing.includes("step:journey-three-production-outcome"));
  assert.deepEqual(p.seams_owed, [...V5_RW02_SEAMS]);
  assert.equal(p.adapter_admission.admitted, false);
  assert.equal(p.adapter_admission.credential_enters_model_or_context, false);
  assert.deepEqual(p.source_build_dependencies, ["V5-F01", "V5-F06", "V5-J301"]);
  assert.equal(p.policy_digest, v5Rw02PolicyDigest());
});

test("the module is pure: no filesystem, network, environment, clock or dynamic import", () => {
  const src = readFileSync(SRC, "utf8");
  for (const banned of [/\bfetch\s*\(/, /from\s+["']node:/, /process\.env/, /Date\.now\s*\(/,
    /new Date\s*\(\s*\)/, /\bimport\s*\(/, /require\s*\(/]) {
    assert.doesNotMatch(src, banned, String(banned));
  }
});

// ---------------------------------------------------------------------------
// checkable_done 1 — auth challenge / UI drift / unexpected recipient / policy
// conflict stop safely (Q084.D1, Q100.D1).
// ---------------------------------------------------------------------------

test("CD1 clean page: an attended, bound, unchallenged page continues and grants nothing", () => {
  const a = evaluatePageObservation(page());
  assert.equal(a.decision, "continue");
  assertGrantsNothing(a);
});

const STOP_CASES = [
  ["unattended execution", page({}, {}, "unattended"), "unattended_execution_excluded", "execution_mode"],
  ["login challenge", page({ challenge: "login_required" }), "authentication_challenge", "authentication_challenge"],
  ["MFA challenge", page({ challenge: "mfa_challenge" }), "authentication_challenge", "authentication_challenge"],
  ["CAPTCHA", page({ challenge: "captcha" }), "authentication_challenge", "authentication_challenge"],
  ["expired session", page({ challenge: "session_expired" }), "authentication_challenge", "authentication_challenge"],
  ["unstated challenge", page({ challenge: "unstated" }), "challenge_state_unobservable", "authentication_challenge"],
  ["look-alike origin", page({ origin: "https://synthetic-org.invalid.attacker.invalid" }), "origin_mismatch", "origin"],
  ["origin with a path", page({ origin: ORIGIN + "/x" }), "origin_mismatch", "origin"],
  ["other org", page({ org_id: "00Dsynthetic0002" }), "org_mismatch", "org"],
  ["other signed-in account", page({ signed_in_account_ref: "sf-seat-other" }), "signed_in_account_mismatch", "signed_in_account"],
  ["other record", page({ record_id: OPP2 }, { expected_record_id: OPP }), "record_mismatch", "record"],
  ["UI drift", page({ ui_contract_digest: "sha256:" + "2".repeat(64) }), "ui_drift", "ui_contract"],
  ["unexpected recipient", page({ recipients: ["party-a", "party-z"] }, { expected_recipients: ["party-a"] }),
    "unexpected_recipient", "recipients"],
  ["policy conflict", page({ policy_conflicts: ["rule-synthetic-1"] }), "policy_conflict", "policy_conflict"],
  ["inconsistent result", page({ result_consistency: "inconsistent" }), "inconsistent_result", "result_consistency"],
  ["unstated consistency", page({ result_consistency: "unstated" }), "result_consistency_unobservable", "result_consistency"],
];

for (const [name, req, reason, check] of STOP_CASES) {
  test(`CD1 stop: ${name} stops with no bypass and no automatic retry`, () => {
    const a = evaluatePageObservation(req);
    assert.equal(a.decision, "stop");
    assert.equal(a.reason_id, reason);
    assert.equal(a.blocking_check, check);
    assert.equal(a.bypass_permitted, false);
    assert.equal(a.automatic_retry_permitted, false);
    assert.equal(a.resolution_owner, "partner_at_the_browser");
    assertGrantsNothing(a);
  });
}

test("CD1 ordering: a challenge is decided before any binding is compared", () => {
  const a = evaluatePageObservation(page({ challenge: "mfa_challenge", org_id: "00Dsynthetic0002" }));
  assert.equal(a.reason_id, "authentication_challenge");
  assert.deepEqual(a.checks_required, [...V5_RW02_PAGE_CHECKS]);
});

test("CD1 a stop on the page stops the duplicate search and the admission too", () => {
  const d = evaluateDuplicateSearch({ ...dupSearch(), page: page({ challenge: "captcha" }) });
  assert.equal(d.decision, "stop");
  assert.equal(d.reason_id, "authentication_challenge");
  const a = admission(preview(), { page: page({ ui_contract_digest: "sha256:" + "3".repeat(64) }) });
  assert.equal(a.decision, "stop");
  assert.equal(a.reason_id, "ui_drift");
});

test("CD1 unattended is refused at the admission door by name", () => {
  const a = admission(preview(), { execution_mode: "unattended" });
  assert.equal(a.decision, "stop");
  assert.equal(a.reason_id, "unattended_execution_excluded");
});

test("data boundary: credential-shaped values throw and are never echoed", () => {
  const secret = "password=hunter2-synthetic";
  let caught;
  try {
    evaluatePageObservation(page({ signed_in_account_ref: secret }));
  } catch (e) { caught = e; }
  assert.ok(caught instanceof V5RW02Error);
  assert.ok(["credential_shaped_value", "invalid_identifier"].includes(caught.code));
  assert.ok(!caught.message.includes("hunter2"));
  assert.throws(() => preview("opportunity_create", {
    fields: [{ field: "Name", value: "eyJhbGciOiJI.eyJzdWIiOiIx.c2lnbmF0dXJl", semantics: "ordinary",
      provenance: "partner_entered" }],
  }), isErr("credential_shaped_value"));
  assert.throws(() => preview("opportunity_create", {
    fields: [{ field: "Name", value: "Bearer abcdef123456", semantics: "ordinary", provenance: "partner_entered" }],
  }), isErr("credential_shaped_value"));
  assert.throws(() => evaluatePageObservation({ ...page(), session_cookie: "x" }), isErr("unknown_field"));
});

// ---------------------------------------------------------------------------
// checkable_done 2a — duplicate detection (Q098.D1, never auto-merge on a name).
// ---------------------------------------------------------------------------

const cand = (opportunity_id, over = {}) => ({ opportunity_id, step_marker: "absent", name_match: "none", ...over });

test("CD2 duplicates: an empty complete search admits a create, pending preview and confirmation", () => {
  const d = evaluateDuplicateSearch(dupSearch([cand(OPP2)]));
  assert.equal(d.decision, "create_admissible");
  assertGrantsNothing(d);
});

test("CD2 duplicates: this step's marker on the provider means already effected — read back, never create", () => {
  const d = evaluateDuplicateSearch(dupSearch([cand(OPP, { step_marker: "present" }), cand(OPP2)]));
  assert.equal(d.decision, "already_effected");
  assert.equal(d.opportunity_id, OPP);
  assert.equal(d.required_next_step, "readback");
  assert.equal(d.create_permitted, false);
});

test("CD2 duplicates: an opportunity already linked to this deal is linked, not duplicated", () => {
  const d = evaluateDuplicateSearch(dupSearch([cand(OPP, { linked_deal_ref: CASE.deal_ref })]));
  assert.equal(d.decision, "link_existing");
  assert.equal(d.create_permitted, false);
});

test("CD2 duplicates: a name match is a human question, never an automatic join", () => {
  for (const name_match of ["exact", "similar"]) {
    const d = evaluateDuplicateSearch(dupSearch([cand(OPP, { name_match }), cand(OPP2)]));
    assert.equal(d.decision, "human_disambiguation_required");
    assert.deepEqual(d.candidate_opportunity_ids, [OPP]);
    assert.equal(d.create_permitted, false);
  }
});

test("CD2 duplicates: incomplete searches, unknown markers and double markers stop", () => {
  for (const [req, detail] of [
    [dupSearch([], "truncated"), "duplicate_search_incomplete"],
    [dupSearch([], "unstated"), "duplicate_search_incomplete"],
    [dupSearch([cand(OPP, { step_marker: "unstated" })]), "step_marker_unobservable"],
    [dupSearch([cand(OPP, { step_marker: "present" }), cand(OPP2, { step_marker: "present" })]),
      "step_marker_on_multiple_opportunities"],
    [dupSearch([cand(OPP, { linked_deal_ref: CASE.deal_ref }), cand(OPP2, { linked_deal_ref: CASE.deal_ref })]),
      "deal_linked_to_multiple_opportunities"],
  ]) {
    const d = evaluateDuplicateSearch(req);
    assert.equal(d.decision, "stop", detail);
    assert.equal(d.reason_id, "inconsistent_result");
    assert.equal(d.detail_reason, detail);
  }
});

// ---------------------------------------------------------------------------
// Preview rules (Q070.D1, Q097.D1, placeholder / lane / lifecycle doctrine).
// ---------------------------------------------------------------------------

test("preview: sealed, placeholders labelled as not figures, and bound for confirmation", () => {
  const p = preview();
  assert.equal(p.decision, "preview_ready");
  assert.deepEqual(p.placeholder_fields, ["CloseDate", "Total_Commission__c"]);
  assert.equal(p.placeholders_are_figures, false);
  assert.equal(p.payload_digest_for_envelope, p.preview_digest);
  assert.equal(p.f06_action, "business.update_deal");
  assert.deepEqual(p.confirmation_binds,
    { preview_digest: p.preview_digest, action_kind: "opportunity_create", step_key: stepKey("opportunity_create") });
  // Field order does not change identity; a field value does.
  const reordered = preview("opportunity_create", { fields: [...CREATE_FIELDS].reverse().map(f => ({ ...f })) });
  assert.equal(reordered.preview_digest, p.preview_digest);
  const edited = preview("opportunity_create", {
    fields: CREATE_FIELDS.map(f => (f.field === "StageName" ? { ...f, value: "Negotiation" } : { ...f })),
  });
  assert.notEqual(edited.preview_digest, p.preview_digest);
});

test("preview: an inferred out-of-market lane is refused", () => {
  const p = preview("opportunity_create", {
    fields: CREATE_FIELDS.map(f => (f.semantics === "out_of_market_flag" ? { ...f, provenance: "inferred" } : { ...f })),
  });
  assert.equal(p.decision, "refuse");
  assert.equal(p.reason_id, "lane_inferred_not_authoritative");
});

test("preview: Salesforce state never crosses into DoctorCRE lifecycle; the link carries only the external id", () => {
  const ok = preview("opportunity_link_record", {
    target_opportunity_id: OPP,
    fields: [{ field: "salesforce_id", value: OPP, semantics: "external_id_link", provenance: "salesforce_observed" }],
  });
  assert.equal(ok.decision, "preview_ready");
  assert.equal(ok.record_layer_verb, "update-deal");
  for (const bad of [
    { field: "phase", value: "Research", semantics: "phase", provenance: "salesforce_observed" },
    { field: "won_value", value: 1000, semantics: "commission_placeholder", provenance: "salesforce_observed" },
    { field: "payment_state", value: "paid", semantics: "ordinary", provenance: "salesforce_observed" },
  ]) {
    const p = preview("opportunity_link_record", {
      target_opportunity_id: OPP,
      fields: [{ field: "salesforce_id", value: OPP, semantics: "external_id_link", provenance: "salesforce_observed" }, bad],
    });
    assert.equal(p.decision, "refuse", bad.field);
    assert.equal(p.reason_id, "salesforce_state_is_not_doctorcre_lifecycle", bad.field);
  }
  const wrongId = preview("opportunity_link_record", {
    target_opportunity_id: OPP,
    fields: [{ field: "salesforce_id", value: OPP2, semantics: "external_id_link", provenance: "salesforce_observed" }],
  });
  assert.equal(wrongId.reason_id, "record_layer_link_carries_only_external_id");
});

test("preview: the link write names a deployed record-layer verb that accepts salesforce_id", () => {
  const tools = readFileSync(TOOLS, "utf8");
  const at = tools.indexOf('"update-deal": {');
  assert.ok(at > 0, "update-deal is registered");
  const block = tools.slice(at, at + 1500);
  assert.match(block, /salesforce_id/);
  assert.match(block, /required: \["idempotency_key","deal","base_version","fields"\]/);
});

test("preview: document preparation and phase moves require a target; a create may not name one", () => {
  assert.equal(preview("etl_document_prepare", { fields: [{ field: "Template_Ref__c", value: "t",
    semantics: "ordinary", provenance: "doctorcre_record" }] }).reason_id, "target_opportunity_required");
  assert.equal(preview("opportunity_create", { target_opportunity_id: OPP }).reason_id,
    "create_names_an_existing_target");
  assert.equal(preview("opportunity_phase_update", { target_opportunity_id: OPP }).reason_id,
    "phase_update_carries_exactly_one_phase_field");
});

test("protected sends are RW01's and are refused by name", () => {
  for (const kind of Object.keys(V5_RW02_EXCLUDED_ACTIONS)) {
    assert.throws(() => rw02StepKey({ tenant: T, case: CASE, action_kind: kind, intent_ordinal: 1 }),
      isErr("action_excluded_from_rw02"));
  }
});

// ---------------------------------------------------------------------------
// Per-action admission (Q059.D5, Q070.D1, Q100.D1 and every predicate's
// "distinct current exact-action capability ... atomically consumed" clause).
// ---------------------------------------------------------------------------

test("admission clean case: every RW02 check and the real F06 presentation pass, and nothing is granted", () => {
  const a = admission();
  assert.equal(a.decision, "admissible_pending_attended_runtime");
  assert.equal(a.blocking_check, null);
  assert.equal(a.consumption_must_commit_before_provider_call, true);
  assert.deepEqual(a.runtime_inputs_missing, [...V5_RW02_RUNTIME_EVIDENCE_INPUTS]);
  assert.equal(a.adapter_admission.admitted, false);
  assertGrantsNothing(a);
});

test("admission: a confirmation bound to another preview, action or step refuses", () => {
  const p = preview();
  assert.equal(admission(p, { confirmation: confirmationFor(p, { preview_digest: "sha256:" + "9".repeat(64) }) }).reason_id,
    "confirmation_for_other_preview");
  assert.equal(admission(p, { confirmation: confirmationFor(p, { action_kind: "opportunity_phase_update" }) }).reason_id,
    "confirmation_for_other_action");
  assert.equal(admission(p, { confirmation: confirmationFor(p, { step_key: stepKey("opportunity_create", 2) }) }).reason_id,
    "confirmation_for_other_step");
  assert.equal(admission(p, { confirmation: confirmationFor(p, { confirmed_at: "2026-09-24T12:00:01Z" }) }).reason_id,
    "confirmation_after_presentation");
});

test("admission: there is no batch or session confirmation to name", () => {
  const p = preview();
  assert.throws(() => admission(p, { confirmation: { ...confirmationFor(p), scope: "session" } }), isErr("unknown_field"));
  assert.throws(() => admission(p, { confirmation: { ...confirmationFor(p), covers_all_actions: true } }),
    isErr("unknown_field"));
});

test("admission: an edited preview cannot be admitted", () => {
  const p = preview();
  const tampered = { ...p.preview, fields: p.preview.fields.map(f => ({ ...f, value: f.field === "Name" ? "Other" : f.value })) };
  assert.throws(() => admission(p, { preview: tampered }), isErr("preview_seal_broken"));
});

test("admission: a create needs a clean duplicate search for the same step", () => {
  assert.equal(admission(preview(), { duplicate_search: null }).reason_id, "duplicate_search_required_before_create");
  assert.equal(admission(preview(), { duplicate_search: dupSearch([cand(OPP, { name_match: "similar" })]) }).reason_id,
    "create_not_admissible_after_duplicate_search");
  assert.equal(admission(preview(), { duplicate_search: dupSearch([], "complete", stepKey("opportunity_create", 2)) }).reason_id,
    "duplicate_search_for_other_step");
});

test("admission: document preparation requires the opportunity to read back present first", () => {
  const p = docPreview();
  assert.equal(admission(p).reason_id, "target_readback_required");
  assert.equal(admission(p, { target_readback: { opportunity_id: OPP, state: "absent" } }).reason_id,
    "target_opportunity_absent");
  assert.equal(admission(p, { target_readback: { opportunity_id: OPP, state: "indeterminate" } }).reason_id,
    "target_readback_indeterminate");
  assert.equal(admission(p, { target_readback: { opportunity_id: OPP3, state: "present" } }).reason_id,
    "record_mismatch");
  const ok = admission(p, { target_readback: { opportunity_id: OPP, state: "present" } });
  assert.equal(ok.decision, "admissible_pending_attended_runtime");
});

test("admission: a capability for one action cannot be presented for another (exact-action)", () => {
  const create = preview();
  const doc = docPreview();
  // An envelope + capability sealed over the CREATE preview, presented for the DOC preview.
  const env = envelopeFor(create);
  const a = evaluateActionAdmission({
    tenant: T, execution_mode: "attended", page: page(), preview: doc.preview,
    target_readback: { opportunity_id: OPP, state: "present" }, confirmation: confirmationFor(doc),
    f06: { envelope: env, capability: capabilityFor(env), presentation: presentationFor(env), authority: authority(), now: NOW },
  });
  assert.equal(a.decision, "refuse");
  assert.equal(a.reason_id, "envelope_payload_is_not_this_preview");
});

test("admission: replayed, expired or wrong-account capabilities refuse through the real F06 ladder", () => {
  const p = preview();
  const env = envelopeFor(p);
  const cases = [
    [{ presentation: presentationFor(env, { nonce_state: "consumed" }) }, "capability_replayed"],
    [{ capability: capabilityFor(env, { issued_at: "2026-09-24T11:40:00Z", expires_at: "2026-09-24T11:50:00Z" }) },
      "capability_expired"],
    [{ presentation: presentationFor(env, { presented_principals: { ...PRINCIPALS, account_ref: "sf-seat-other" } }) },
      "account_mismatch"],
  ];
  for (const [f06over, f06reason] of cases) {
    const a = admission(p, {}, { envelope: env, ...f06over });
    assert.equal(a.decision, "refuse", f06reason);
    assert.equal(a.reason_id, "capability_presentation_refused");
    assert.equal(a.f06_reason_id, f06reason);
  }
});

test("admission: an envelope sealed for another action, step or case refuses", () => {
  const p = preview();
  assert.equal(admission(p, {}, { envelope: envelopeFor(p, { step_id: "step-other" }) }).reason_id,
    "envelope_step_is_not_this_step");
  assert.equal(admission(p, {}, { envelope: envelopeFor(p, { workflow_id: "wf-other" }) }).reason_id,
    "envelope_workflow_is_not_this_case");
  assert.equal(admission(p, {}, { envelope: envelopeFor(p, { action: "business.send_client_document" }) }).reason_id,
    "envelope_action_not_rw02");
});

// ---------------------------------------------------------------------------
// checkable_done 2b — readback (Q084.D1 exact readback; F06 quarantine).
// ---------------------------------------------------------------------------

test("CD2 readback: an exact field-by-field match confirms and seals one evidence record", () => {
  const r = readback();
  assert.equal(r.decision, "confirmed");
  assert.equal(r.evidence.outcome, "exact_match");
  assert.equal(r.evidence.action_kind, "opportunity_create");
  assert.match(r.evidence.evidence_digest, /^sha256:/);
  assertGrantsNothing(r);
});

test("CD2 readback: any differing field is an inconsistent result that stops, naming fields not values", () => {
  const p = preview();
  const r = readback(p, { provider_readback: readbackOf(p, { StageName: "Closing" }) });
  assert.equal(r.decision, "stop");
  assert.equal(r.reason_id, "inconsistent_result");
  assert.deepEqual(r.mismatched_fields, ["StageName"]);
  assert.ok(!JSON.stringify(r).includes("Closing"));
  assert.equal(r.evidence.outcome, "mismatch");
  const missing = readbackOf(p);
  missing.fields = missing.fields.filter(f => f.field !== "CloseDate");
  assert.deepEqual(readback(p, { provider_readback: missing }).mismatched_fields, ["CloseDate"]);
});

test("CD2 readback: a timeout is unknown and requires a readback before anything else", () => {
  const r = readback(preview(), { provider_readback: null }, { outcome: { state: "timed_out" } });
  assert.equal(r.decision, "readback_required");
  assert.equal(r.reason_id, "outcome_unknown_readback_first");
  assert.equal(r.retry_permitted, false);
  // A matching field readback that is NOT joined to this effect's idempotency
  // key does not lift the quarantine: the fields could be someone else's write.
  const unjoined = readback(preview(), {}, { outcome: { state: "timed_out" } });
  assert.equal(unjoined.decision, "readback_required");
  assert.equal(unjoined.reason_id, "outcome_unknown_readback_first");
  assert.equal(unjoined.evidence, undefined);
});

test("CD2 readback: a timeout resolved present by the provider confirms as unknown_resolved_by_readback", () => {
  const p = preview();
  const env = envelopeFor(p);
  const r = readback(p, {}, { outcome: { state: "timed_out" },
    readback: { state: "effect_present", join_key: env.envelope_digest } });
  assert.equal(r.decision, "confirmed");
  assert.equal(r.evidence.outcome, "unknown_resolved_by_readback");
});

test("CD2 readback: an absent effect is a confirmed failure; the consumed capability is never reusable", () => {
  const p = preview();
  const env = envelopeFor(p);
  const r = readback(p, { provider_readback: null }, { outcome: { state: "failed" },
    readback: { state: "effect_absent", join_key: env.envelope_digest } });
  assert.equal(r.decision, "confirmed_failure");
  assert.equal(r.consumed_capability_reusable, false);
  assert.deepEqual(r.retry_requires, ["fresh_preview_confirmation", "fresh_capability"]);
});

test("CD2 readback: a provider call before consumption committed stops", () => {
  const r = readback(preview(), {}, {
    consumption: { state: "committed", committed_seq: 3 }, provider_call: { state: "started", started_seq: 2 },
  });
  assert.equal(r.decision, "stop");
  assert.equal(r.detail_reason, "consumption_order_refused");
});

test("CD2 readback: a readback of another opportunity stops as a record mismatch", () => {
  const p = docPreview();
  const r = readback(p, { provider_readback: readbackOf(p, {}, OPP2) });
  assert.equal(r.reason_id, "record_mismatch");
});

// ---------------------------------------------------------------------------
// checkable_done 2c — idempotent resume, anchored on the provider.
// ---------------------------------------------------------------------------

const resume = (effect, journal_hint, opportunity_id) => evaluateResume({
  tenant: T, action_kind: "opportunity_create", step_key: stepKey("opportunity_create"), journal_hint,
  provider: opportunity_id ? { effect, opportunity_id } : { effect },
});

test("CD2 resume: the provider decides; the journal is only ever a hint", () => {
  const behind = resume("effect_present", "not_started", OPP);
  assert.equal(behind.decision, "skip_already_effected");
  assert.equal(behind.reason_id, "journal_behind_provider");
  assert.equal(behind.repeat_write_permitted, false);
  assert.equal(behind.journal_is_authority, false);
  assert.equal(resume("effect_present", "effected", OPP).reason_id, "provider_confirms_journal");

  const lying = resume("effect_absent", "effected");
  assert.equal(lying.decision, "stop");
  assert.equal(lying.detail_reason, "journal_records_effect_provider_lacks");

  const fresh = resume("effect_absent", "not_started");
  assert.equal(fresh.decision, "proceed_to_preview");
  assert.equal(fresh.consumed_capability_reusable, false);
  assert.ok(fresh.requires.includes("fresh_capability"));

  for (const effect of ["indeterminate", "unstated"]) {
    for (const hint of ["not_started", "effected", "unknown", "absent"]) {
      const r = resume(effect, hint);
      assert.equal(r.decision, "readback_required", `${effect}/${hint}`);
      assert.equal(r.retry_permitted, false);
    }
  }
});

test("CD2 resume: the same intent always folds to the same step key; distinct intents do not", () => {
  assert.equal(stepKey("opportunity_create"), stepKey("opportunity_create"));
  assert.notEqual(stepKey("opportunity_phase_update", 1), stepKey("opportunity_phase_update", 2));
  assert.notEqual(stepKey("opportunity_create"), stepKey("etl_document_prepare"));
  assert.notEqual(stepKey("opportunity_create"),
    stepKey("opportunity_create", 1, { ...CASE, deal_ref: "deal-synthetic-2" }));
});

// ---------------------------------------------------------------------------
// checkable_done 3 — each action records distinct evidence and cannot inherit
// trust (Q099.D1).
// ---------------------------------------------------------------------------

function evidenceFor(action_kind, { cls = "fixture", ordinal = 1, observed_at = NOW, mismatch = false } = {}) {
  const fields = action_kind === "opportunity_create"
    ? CREATE_FIELDS.map(f => ({ ...f }))
    : action_kind === "opportunity_phase_update"
      ? [{ field: "StageName", value: `Phase-${ordinal}`, semantics: "phase", provenance: "partner_entered" }]
      : [{ field: "Template_Ref__c", value: `tpl-${ordinal}`, semantics: "ordinary", provenance: "doctorcre_record" }];
  const p = buildActionPreview({
    tenant: T, action_kind, case: CASE, step_key: stepKey(action_kind, ordinal), fields,
    ...(action_kind === "opportunity_create" ? {} : { target_opportunity_id: OPP }),
  });
  const env = envelopeFor(p);
  const r = evaluateWriteReadback({
    tenant: T, page: page(), preview: p.preview, evidence_class: cls, observed_at,
    f06: { envelope: env, capability: capabilityFor(env), attempt: attemptFor(env) },
    provider_readback: readbackOf(p, mismatch ? { [fields[0].field]: "drifted" } : {}),
  });
  return r.evidence;
}

test("CD3 evidence records are distinct per action kind and per step", () => {
  const a = evidenceFor("opportunity_create");
  const b = evidenceFor("opportunity_phase_update");
  const c = evidenceFor("opportunity_phase_update", { ordinal: 2 });
  assert.notEqual(a.evidence_digest, b.evidence_digest);
  assert.notEqual(b.evidence_digest, c.evidence_digest);
  assert.equal(a.action_kind, "opportunity_create");
  assert.equal(b.action_kind, "opportunity_phase_update");
});

test("CD3 a window refuses evidence from another action — trust is never inherited", () => {
  const w = evaluateActionTrustWindow({ tenant: T, action_kind: "opportunity_phase_update",
    evidence: [evidenceFor("opportunity_phase_update"), evidenceFor("opportunity_create")] });
  assert.equal(w.decision, "refuse");
  assert.equal(w.reason_id, "evidence_foreign_to_action");
  assert.equal(w.offending_action_kind, "opportunity_create");
  assert.deepEqual(w.inherits_from, []);
});

test("CD3 global trust is refused by name", () => {
  const w = evaluateActionTrustWindow({ tenant: T, action_kind: "opportunity_create", evidence: [], trust_scope: "global" });
  assert.equal(w.reason_id, "global_trust_excluded");
});

test("CD3 a sample counted twice, or an edited record, is refused", () => {
  const e = evidenceFor("opportunity_create");
  assert.equal(evaluateActionTrustWindow({ tenant: T, action_kind: "opportunity_create", evidence: [e, e] }).reason_id,
    "evidence_counted_twice");
  const reobserved = evidenceFor("opportunity_create", { observed_at: "2026-09-24T12:30:00Z" });
  assert.equal(evaluateActionTrustWindow({ tenant: T, action_kind: "opportunity_create",
    evidence: [e, reobserved] }).reason_id, "evidence_counted_twice");
  assert.throws(() => evaluateActionTrustWindow({ tenant: T, action_kind: "opportunity_create",
    evidence: [{ ...e, outcome: "exact_match", evidence_class: "supervised_production_sample" }] }),
  isErr("evidence_seal_broken"));
});

test("CD3 the window restarts after a failure, counts per class, and never activates autonomy", () => {
  const evidence = [
    evidenceFor("opportunity_phase_update", { ordinal: 1, observed_at: "2026-09-20T12:00:00Z" }),
    evidenceFor("opportunity_phase_update", { ordinal: 2, observed_at: "2026-09-21T12:00:00Z", mismatch: true }),
    evidenceFor("opportunity_phase_update", { ordinal: 3, observed_at: "2026-09-22T12:00:00Z",
      cls: "supervised_production_sample" }),
    evidenceFor("opportunity_phase_update", { ordinal: 4, observed_at: "2026-09-23T12:00:00Z", cls: "recovery_exercise" }),
  ];
  const w = evaluateActionTrustWindow({ tenant: T, action_kind: "opportunity_phase_update", evidence });
  assert.equal(w.decision, "window_read");
  assert.equal(w.evidence_total, 4);
  assert.equal(w.failures_total, 1);
  assert.equal(w.window_since_last_failure.records, 2);
  assert.deepEqual(w.window_since_last_failure.counts_by_class,
    { fixture: 0, supervised_production_sample: 1, recovery_exercise: 1 });
  assert.equal(w.window_since_last_failure.recovery_exercised, true);
  assert.equal(w.activation_review_eligibility, "unavailable");
  assert.equal(w.activation_review_eligibility_seam, V5_RW02_EVALUATION_WINDOW_SEAM);
  assert.equal(w.activation_gate, "system.autonomy_tier_activation");
  assertGrantsNothing(w);
});

test("every action kind is independently evaluable (no shared window)", () => {
  for (const kind of V5_RW02_ACTION_KIND_KEYS) {
    const w = evaluateActionTrustWindow({ tenant: T, action_kind: kind, evidence: [] });
    assert.equal(w.action_kind, kind);
    assert.equal(w.window_since_last_failure.records, 0);
  }
});
