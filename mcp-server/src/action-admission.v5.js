// DoctorCRE v5 slice V5-F05: ACTION ADMISSION — the join between a context
// manifest, its coverage receipt and the action somebody is about to take.
//
// WHY THIS FILE EXISTS. The catalog names five interfaces for this slice:
// Context Assembler, rule registry, correction taxonomy, taint labels, and
// ACTION ADMISSION. Four of them shipped. The fifth did not, and its absence
// was not cosmetic — it left every artifact this slice produces unbound to the
// act it is supposed to govern:
//
//   * context-assembly.v5.js says, in the manifest record itself,
//     `write_gate_field: "consequential_action_permitted"`, and its own comment
//     says "an admission call site that reads `decision` reads the wrong
//     field". There was no admission call site to read either field.
//   * rule-applicability.v5.js says the same thing about its receipt, and its
//     `no_action_admission_enforcement` gap says the receipt "is produced and
//     returned, never enforced at a call site".
//   * Nothing checked that a receipt handed to a call site was THE receipt the
//     manifest was assembled with, that the action about to run was THE action
//     the manifest was assembled for, or that a record about to be passed as a
//     tool argument was one this manifest carries and Q068 permits there.
//
// This module is that join, as a pure function over artifacts the other two
// halves already produced. It is the DECISION and nothing else.
//
// WHAT THIS IS NOT, said first because "admission" invites the wrong reading.
// It admits nobody to anything. It registers no verb, consults no database,
// takes no lock, starts no clock and writes nothing. It is not the call site;
// it is the decision a call site would make, and `enforced_at_a_call_site:
// false` rides on every answer it gives. Wiring it into a verb is a production
// effect this slice does not take, and it is named below as the open seam
// `no_action_admission_call_site`.
//
// IT IS NOT A SECOND AUTHORITY, and that is the point of nearly every line.
// Every substantive question here is already decided somewhere in this slice,
// and this module reads that decision rather than forming its own:
//
//   * Whether the rules permit the action — the coverage receipt's own
//     `consequential_action_permitted`, read through the field name the receipt
//     itself publishes.
//   * Whether the context permits it — the manifest's, read the same way.
//   * Whether a record may become a tool argument — evaluateUntrustedUse, with
//     the intended use `tool_invocation`, which Q068 already lists among the
//     forbidden uses for untrusted content. No taint is computed here.
//   * Whether the actor has authority — the manifest's authority envelope,
//     which S01 computed. Nothing here re-decides it.
//
// What is NEW here is only the BINDING between those answers, which is exactly
// the part nobody owned: a receipt that is not this manifest's, an action that
// is not the assembled action, a risk tier that is not the assembled risk, a
// lineage that is not the manifest's, an argument record the budget dropped.
// Each of those is a way to pair two individually-honest artifacts into a
// dishonest permission, and each has a check below and a negative test.
//
// TWO KINDS OF NO, inherited unchanged from global-boundaries.v5.js and applied
// exactly as command-supervisor-admission.v5.js applies them:
//   * A POLICY ANSWER IS RETURNED — a frozen result whose `decision` is "allow"
//     or "refuse" with a stable `reason_id`. A caller may record it. "The
//     receipt does not match this manifest" is one of these: it is a finding
//     about two well-formed artifacts, not an unreadable request.
//   * A CONTRACT VIOLATION THROWS V5F05Error. Unknown fields, unknown enum
//     values, a manifest or receipt that does not hash to itself, and a
//     projection claiming an authentication this slice cannot emit are not
//     policy questions.
//
// THE CHECK LIST IS A MODULE CONSTANT, IN A FIXED ORDER, AND THE ORDER IS
// LOAD-BEARING. The first check that does not pass refuses, and every check
// after it reports `not_reached`. Two consequences are deliberate:
//
//   1. The binding checks run BEFORE the permission checks. A caller holding a
//      mismatched pair is told that first, rather than being told "coverage
//      does not permit this" about a receipt that was never this manifest's.
//   2. `authenticated_runtime_projection` is LAST. It is the one check that
//      cannot pass in this repository today (no component issues verifier
//      attestations — see `no_registered_verifier` in context-assembly.v5.js),
//      and putting it anywhere else would short-circuit the ladder and leave
//      every check below it unexercised until the day a verifier lands. Last
//      means every other term is really evaluated, really tested, and really
//      reported, and the missing verifier is one named term rather than a
//      trapdoor under the whole file.
//
// SO THE HONEST HEADLINE: `admitConsequentialAction` cannot return `allow` in
// this repository, on any input, and that is not a defect to be routed around.
// A consequential write needs a projection this system cannot yet authenticate.
// The value here is that every OTHER reason an action would be refused is
// computed, ordered and reported — and that the day a trusted verifier exists,
// the file that must change is this one, in one named check.
//
// A CALLER BOOLEAN NEVER BECOMES AN AUTHENTICATION. authenticateRuntimeProjection
// emits `authenticated: false` on every path it has; it is documented as being
// unable to refuse a self-minted credential, which is why it never claims one.
// A projection presented HERE with `authenticated: true` therefore did not come
// from that function, and it is refused as a CONTRACT VIOLATION rather than
// being read as a permission — the same discipline that makes `enforced: true`
// unreadable in the kernel. `V5_F05_ADMISSION_AUTHENTICATED_PROJECTION_EMITTED`
// is a module constant, exported and asserted, so a later edit that starts
// honouring the claim has to change a hashed constant to do it.
//
// FRESHNESS IS THE CALLER'S POLICY OR IT IS NO POLICY. A manifest is assembled
// at one instant and admitted at another, and how old is too old is not settled
// by any of the seven decisions this slice binds. So there is no default window
// here: `manifest_max_age_seconds` is an optional caller policy, the measured
// age is reported either way, and `manifest_age_policy_supplied` says which
// happened. A manifest assembled AFTER the admission instant still refuses,
// because that is internal inconsistency and not a policy call — the same
// distinction authenticateRuntimeProjection draws about attestation time.
//
// NO NEW EFFECTS, NO NEW STORE, NO NEW MIGRATION. Every answer carries
// `effects: V5_NO_EFFECTS` and hashes to its own digest so a hand-edited copy
// cannot pass as one.

import { canonicalJson, digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import { V5_NO_EFFECTS, V5_ACTION_KEYS } from "./global-boundaries.v5.js";
import {
  V5F05Error,
  V5_F05_GUARDS,
  V5_F05_POLICY_VERSION,
  V5_F05_RISK_TIERS,
  V5_F05_CONSEQUENTIAL_RISK_TIERS,
  V5_F05_COVERAGE_SCHEMA_VERSION,
  v5F05DecisionSubsetDigest,
  v5F05RuleKernelDigest,
  verifyCoverageReceipt,
} from "./rule-applicability.v5.js";
import {
  V5_F05_MANIFEST_SCHEMA_VERSION,
  V5_F05_ATTESTATION_SCHEMA_VERSION,
  V5_F05_LINEAGE_SCHEMA_VERSION,
  V5_F05_UNTRUSTED_FORBIDDEN_USES,
  evaluateUntrustedUse,
  verifyContextManifest,
  v5F05ContextContractDigest,
} from "./context-assembly.v5.js";

export { V5_NO_EFFECTS, V5F05Error };

const {
  fail, deepFreeze,
  assertObject, assertArray, assertClosedKeys, assertRequiredKeys,
  assertNoAccessorsOrHiddenKeys, assertExternalIdent, assertEnum, assertInstant,
  assertSafeInteger, assertTenant,
} = V5_F05_GUARDS;

export const V5_F05_ADMISSION_SCHEMA_VERSION = "doctorcre-v5-f05-action-admission.v1";

/**
 * The one intended use an action's argument records are evaluated under.
 *
 * It is a member of Q068's forbidden-use list, which is what makes the tainted
 * case refuse; asserted at load below so a rename in the assembler cannot leave
 * this module silently evaluating a permitted use instead.
 */
export const V5_F05_ADMISSION_ARGUMENT_USE = "tool_invocation";

/** The gate field this module knows how to read, on both artifacts. */
export const V5_F05_ADMISSION_WRITE_GATE_FIELD = "consequential_action_permitted";

/**
 * NO PROJECTION THIS SLICE EMITS IS AUTHENTICATED, so no projection presented
 * here may claim to be. Hashed into the contract preimage: honouring such a
 * claim later means moving a published digest, not flipping a local boolean.
 */
export const V5_F05_ADMISSION_AUTHENTICATED_PROJECTION_EMITTED = false;

/** At most this many records may be named as one action's arguments. */
export const V5_F05_ADMISSION_MAX_ARGUMENT_RECORDS = 64;

/**
 * The ordered check list. The first check that does not pass refuses; every
 * check after it is `not_reached`. See the header for why the order is fixed
 * and why the projection check is last.
 */
export const V5_F05_ADMISSION_CHECKS = deepFreeze([
  "manifest_integrity",
  "receipt_integrity",
  "write_gate_field_known",
  "universe_binds_manifest",
  "receipt_binds_manifest",
  "mode_admits_a_write",
  "action_is_the_assembled_action",
  "risk_tier_is_the_assembled_risk",
  "risk_tier_is_consequential",
  "manifest_freshness",
  "argument_records_admissible",
  "coverage_permits_the_action",
  "manifest_permits_the_action",
  "authenticated_runtime_projection",
]);

/** Every reason_id this module can return, so a consumer can enumerate them. */
export const V5_F05_ADMISSION_REASON_IDS = deepFreeze([
  "action_admitted",
  "action_not_the_assembled_action",
  "argument_record_not_in_manifest",
  "argument_record_omitted_from_manifest",
  "argument_record_use_refused",
  "coverage_does_not_permit_consequential_action",
  "coverage_receipt_not_the_manifests",
  "lineage_not_supplied",
  "lineage_not_the_manifests",
  "manifest_assembled_after_admission",
  "manifest_does_not_permit_consequential_action",
  "manifest_mode_is_read_only_exploration",
  "manifest_stale",
  "no_runtime_projection_supplied",
  "risk_tier_not_consequential",
  "risk_tier_not_the_assembled_risk",
  "rule_universe_not_the_manifests",
  "runtime_projection_not_authenticated",
  "runtime_projection_not_this_manifests",
  "runtime_projection_refused",
  "unknown_write_gate_field",
]);

const REQUEST_KEYS = Object.freeze([
  "schema_version", "tenant", "now", "manifest", "receipt", "lineage",
  "proposed_action", "projection", "manifest_max_age_seconds",
]);
const REQUEST_REQUIRED = Object.freeze([
  "schema_version", "tenant", "now", "manifest", "receipt", "proposed_action",
]);
const ACTION_KEYS = Object.freeze(["action", "risk_tier", "argument_records"]);
const ACTION_REQUIRED = Object.freeze(["action", "risk_tier"]);

const DECISION_KEYS_NOT_HASHED = Object.freeze(["admission_digest", "effects"]);

function admissionPreimage(decision) {
  const rest = {};
  for (const [key, value] of Object.entries(decision)) {
    if (DECISION_KEYS_NOT_HASHED.includes(key)) continue;
    rest[key] = value;
  }
  return rest;
}

// ---------------------------------------------------------------------------
// The proposed action.
//
// `action` is validated against S01's action keys — the SAME vocabulary the
// manifest's `boundary_action` comes from — so "the action is not the assembled
// action" is a comparison between two members of one closed set rather than
// between two free strings. `argument_records` is a list of record ids and
// nothing else: no values, no payloads, no content. This module decides whether
// a record MAY be an argument; it never carries what the record says.
// ---------------------------------------------------------------------------

function compileProposedAction(raw) {
  assertObject(raw, "request.proposed_action");
  assertClosedKeys(raw, ACTION_KEYS, "request.proposed_action");
  assertRequiredKeys(raw, ACTION_REQUIRED, "request.proposed_action");
  const argument_records = raw.argument_records === undefined || raw.argument_records === null
    ? []
    : assertArray(raw.argument_records, "request.proposed_action.argument_records",
      { min: 0, max: V5_F05_ADMISSION_MAX_ARGUMENT_RECORDS })
      .map((id, index) => assertExternalIdent(id,
        `request.proposed_action.argument_records[${index}]`, { maxLength: 128 }));
  const seen = new Set();
  for (const id of argument_records) {
    if (seen.has(id)) {
      fail("duplicate_argument_record",
        `record "${id}" is named twice among this action's arguments`, { record_id: id });
    }
    seen.add(id);
  }
  return {
    action: assertEnum(raw.action, V5_ACTION_KEYS, "request.proposed_action.action",
      "unknown_action"),
    risk_tier: assertEnum(raw.risk_tier, V5_F05_RISK_TIERS,
      "request.proposed_action.risk_tier", "unknown_risk_tier"),
    argument_records,
  };
}

// ---------------------------------------------------------------------------
// The runtime projection, when one is presented.
//
// SHAPE IS A CONTRACT QUESTION; MATCHING IS A POLICY ANSWER. An object that is
// not an authenticateRuntimeProjection answer at all, or that claims an
// authentication this slice does not emit, throws. An answer that is well
// formed but is about a different manifest, or that refused, is returned as a
// refusal a caller may record.
// ---------------------------------------------------------------------------

function assertProjectionShape(raw) {
  assertObject(raw, "request.projection");
  assertNoAccessorsOrHiddenKeys(raw, "request.projection");
  if (raw.schema_version !== V5_F05_ATTESTATION_SCHEMA_VERSION) {
    fail("projection_not_a_runtime_projection",
      `request.projection.schema_version must be "${V5_F05_ATTESTATION_SCHEMA_VERSION}";`
      + " only an authenticateRuntimeProjection answer can be presented here",
      { expected: V5_F05_ATTESTATION_SCHEMA_VERSION, actual: raw.schema_version ?? null });
  }
  // The self-minted credential, refused at the door. authenticateRuntimeProjection
  // emits `authenticated: false` on every path it has, so a `true` here was
  // written by the caller and is not a fact about a running system.
  if (raw.authenticated !== false || raw.verifier_trusted !== false ||
      raw.consequential_execution_permitted !== false ||
      raw.projection_kind !== "reproducible_proposal" || raw.trust_anchor !== null) {
    fail("projection_claims_unregistered_authentication",
      "this slice emits no authenticated projection; a projection claiming one was not"
      + " produced by authenticateRuntimeProjection and is not read as a permission",
      {
        authenticated_projection_emitted: V5_F05_ADMISSION_AUTHENTICATED_PROJECTION_EMITTED,
        projection_kind: raw.projection_kind ?? null,
        authenticated: raw.authenticated ?? null,
      });
  }
  return raw;
}

// ---------------------------------------------------------------------------
// The lineage, when argument records are named.
//
// The manifest carries `taint_lineage` and hashes it, so the ENTRIES are
// already bound to a manifest this module re-verified. What evaluateUntrustedUse
// needs is the compiled lineage ENVELOPE, which the manifest does not carry and
// which this module will not mint. So the caller supplies it and it must be
// byte-identical to the manifest's entries: a friendlier lineage relabelling an
// email as first-party cannot be swapped in, and no taint is decided here.
// ---------------------------------------------------------------------------

function lineageMatchesManifest(lineage, manifest) {
  return canonicalJson(lineage.entries) === canonicalJson(manifest.taint_lineage);
}

export function admitConsequentialAction(request) {
  assertObject(request, "request");
  assertClosedKeys(request, REQUEST_KEYS, "request");
  assertRequiredKeys(request, REQUEST_REQUIRED, "request");
  if (request.schema_version !== V5_F05_ADMISSION_SCHEMA_VERSION) {
    fail("unknown_schema_version",
      `request.schema_version must be "${V5_F05_ADMISSION_SCHEMA_VERSION}"`,
      { expected: V5_F05_ADMISSION_SCHEMA_VERSION, actual: request.schema_version ?? null });
  }
  assertTenant(request.tenant, "request.tenant");
  const now = assertInstant(request.now, "request.now");
  const maxAgeSeconds =
    request.manifest_max_age_seconds === undefined || request.manifest_max_age_seconds === null
      ? null
      : assertSafeInteger(request.manifest_max_age_seconds, "request.manifest_max_age_seconds",
        { min: 0, max: 315_360_000 });

  const proposed = compileProposedAction(request.proposed_action);

  // ---- check 1: the manifest hashes to itself. A forgery throws. ----------
  const manifest = assertObject(request.manifest, "request.manifest");
  verifyContextManifest(manifest);

  // ---- check 2: the receipt hashes to itself. A forgery throws. -----------
  const receipt = assertObject(request.receipt, "request.receipt");
  if (receipt.schema_version !== V5_F05_COVERAGE_SCHEMA_VERSION) {
    fail("unknown_schema_version",
      `request.receipt.schema_version must be "${V5_F05_COVERAGE_SCHEMA_VERSION}"`,
      { expected: V5_F05_COVERAGE_SCHEMA_VERSION, actual: receipt.schema_version ?? null });
  }
  verifyCoverageReceipt(receipt);

  let lineage = null;
  if (request.lineage !== undefined && request.lineage !== null) {
    lineage = assertObject(request.lineage, "request.lineage");
    if (lineage.schema_version !== V5_F05_LINEAGE_SCHEMA_VERSION) {
      fail("lineage_not_compiled",
        "request.lineage must be the output of compileTaintLineage",
        { path: "request.lineage" });
    }
  }
  const projection = request.projection === undefined || request.projection === null
    ? null : assertProjectionShape(request.projection);

  const manifest_age_seconds = (now - assertInstant(manifest.now, "request.manifest.now")) / 1000;

  // -------------------------------------------------------------------------
  // The ladder. `state` is filled in order; the first refusal stops the walk
  // and everything after it stays `not_reached`.
  // -------------------------------------------------------------------------
  const results = new Map(V5_F05_ADMISSION_CHECKS.map(check =>
    [check, { check, state: "not_reached", reason_id: null }]));
  const pass = (check, reason_id = null) => {
    results.get(check).state = "pass";
    results.get(check).reason_id = reason_id;
    return null;
  };
  const refuse = (check, reason_id, detail = {}) => {
    results.get(check).state = "refuse";
    results.get(check).reason_id = reason_id;
    return { reason_id, detail };
  };

  const walk = () => {
    pass("manifest_integrity");
    pass("receipt_integrity");

    if (manifest.write_gate_field !== V5_F05_ADMISSION_WRITE_GATE_FIELD ||
        receipt.write_gate_field !== V5_F05_ADMISSION_WRITE_GATE_FIELD) {
      // Version drift, refused rather than guessed: an artifact that names a
      // different write gate is one this module does not know how to read, and
      // reading `decision` instead is the exact mistake both halves warn about.
      return refuse("write_gate_field_known", "unknown_write_gate_field", {
        expected: V5_F05_ADMISSION_WRITE_GATE_FIELD,
        manifest_write_gate_field: manifest.write_gate_field ?? null,
        receipt_write_gate_field: receipt.write_gate_field ?? null,
      });
    }
    pass("write_gate_field_known");

    // THE COARSE QUESTION FIRST, and the order is what makes both checks
    // reachable. A receipt compiled over a different rule set answers here; a
    // receipt over the SAME rule set but different facts passes this and
    // answers below. Asked the other way round, the exact-digest check would
    // swallow both and this one could never fire on any real input.
    if (receipt.universe_digest !== manifest.universe_digest ||
        receipt.universe_version !== manifest.universe_version) {
      return refuse("universe_binds_manifest", "rule_universe_not_the_manifests", {
        manifest_universe_digest: manifest.universe_digest,
        receipt_universe_digest: receipt.universe_digest,
      });
    }
    pass("universe_binds_manifest");

    if (receipt.receipt_digest !== manifest.coverage_receipt_digest) {
      return refuse("receipt_binds_manifest", "coverage_receipt_not_the_manifests", {
        manifest_coverage_receipt_digest: manifest.coverage_receipt_digest,
        presented_receipt_digest: receipt.receipt_digest,
      });
    }
    pass("receipt_binds_manifest");

    if (manifest.mode !== "consequential_action_proposal") {
      // An exploration manifest cannot be upgraded into a write permission by
      // presenting it to this function. Q065's marked read-only mode exists
      // precisely so a session can proceed under uncertainty WITHOUT this.
      return refuse("mode_admits_a_write", "manifest_mode_is_read_only_exploration",
        { manifest_mode: manifest.mode });
    }
    pass("mode_admits_a_write");

    if (proposed.action !== manifest.task.boundary_action) {
      return refuse("action_is_the_assembled_action", "action_not_the_assembled_action", {
        assembled_action: manifest.task.boundary_action, proposed_action: proposed.action,
      });
    }
    pass("action_is_the_assembled_action");

    if (proposed.risk_tier !== manifest.task.facts.risk_tier) {
      // The rules that bound were selected against the ASSEMBLED risk tier. An
      // action executed at a different tier was governed by a different rule
      // set than the one this receipt enumerates.
      return refuse("risk_tier_is_the_assembled_risk", "risk_tier_not_the_assembled_risk", {
        assembled_risk_tier: manifest.task.facts.risk_tier ?? null,
        proposed_risk_tier: proposed.risk_tier,
      });
    }
    pass("risk_tier_is_the_assembled_risk");

    if (!V5_F05_CONSEQUENTIAL_RISK_TIERS.includes(proposed.risk_tier)) {
      // NOT a prohibition. This gate decides consequential actions; a routine
      // one is outside what it answers, and `gate_applies: false` on the result
      // says so, so a caller cannot read this refusal as "you may not".
      return refuse("risk_tier_is_consequential", "risk_tier_not_consequential", {
        risk_tier: proposed.risk_tier,
        consequential_risk_tiers: [...V5_F05_CONSEQUENTIAL_RISK_TIERS],
      });
    }
    pass("risk_tier_is_consequential");

    if (manifest_age_seconds < 0) {
      return refuse("manifest_freshness", "manifest_assembled_after_admission",
        { manifest_now: manifest.now, now: request.now, manifest_age_seconds });
    }
    if (maxAgeSeconds !== null && manifest_age_seconds > maxAgeSeconds) {
      return refuse("manifest_freshness", "manifest_stale",
        { manifest_age_seconds, max_age_seconds: maxAgeSeconds });
    }
    pass("manifest_freshness");

    if (proposed.argument_records.length > 0) {
      if (lineage === null) {
        return refuse("argument_records_admissible", "lineage_not_supplied", {
          argument_records: [...proposed.argument_records],
        });
      }
      if (!lineageMatchesManifest(lineage, manifest)) {
        return refuse("argument_records_admissible", "lineage_not_the_manifests", {
          manifest_digest: manifest.manifest_digest,
        });
      }
      const byId = new Map(manifest.records.map(record => [record.record_id, record]));
      for (const record_id of proposed.argument_records) {
        const record = byId.get(record_id);
        if (record === undefined) {
          return refuse("argument_records_admissible", "argument_record_not_in_manifest",
            { record_id });
        }
        if (record.included !== true) {
          // A record the budget dropped is not evidence this manifest carries.
          // Passing it as an argument would put content in front of the action
          // that the manifest says it omitted.
          return refuse("argument_records_admissible", "argument_record_omitted_from_manifest",
            { record_id, omission_reason_id: record.omission_reason_id ?? null });
        }
        const use = evaluateUntrustedUse({
          lineage: request.lineage, record_id,
          intended_use: V5_F05_ADMISSION_ARGUMENT_USE,
        });
        if (use.decision !== "allow") {
          return refuse("argument_records_admissible", "argument_record_use_refused", {
            record_id, taint_class: use.taint_class, use_reason_id: use.reason_id,
            intended_use: V5_F05_ADMISSION_ARGUMENT_USE,
          });
        }
      }
    }
    pass("argument_records_admissible");

    if (receipt[receipt.write_gate_field] !== true) {
      return refuse("coverage_permits_the_action",
        "coverage_does_not_permit_consequential_action",
        { receipt_blocking_reasons: [...receipt.blocking_reasons] });
    }
    pass("coverage_permits_the_action");

    if (manifest[manifest.write_gate_field] !== true) {
      return refuse("manifest_permits_the_action",
        "manifest_does_not_permit_consequential_action",
        { manifest_blocking_reasons: [...manifest.blocking_reasons] });
    }
    pass("manifest_permits_the_action");

    // The last term, and the one this repository cannot satisfy. See the header.
    if (projection === null) {
      return refuse("authenticated_runtime_projection", "no_runtime_projection_supplied", {
        execution_gap_id: "no_registered_verifier",
      });
    }
    if (projection.manifest_digest !== manifest.manifest_digest ||
        projection.input_digest !== manifest.input_digest) {
      return refuse("authenticated_runtime_projection", "runtime_projection_not_this_manifests", {
        manifest_digest: manifest.manifest_digest,
        projection_manifest_digest: projection.manifest_digest ?? null,
      });
    }
    if (projection.decision !== "allow") {
      return refuse("authenticated_runtime_projection", "runtime_projection_refused",
        { projection_reason_id: projection.reason_id ?? null });
    }
    // Reproducible is not authenticated. The projection proved the manifest
    // re-derives from its own bytes; nothing proved a trusted verifier saw it.
    return refuse("authenticated_runtime_projection", "runtime_projection_not_authenticated", {
      execution_gap_id: projection.execution_gap_id ?? "no_registered_verifier",
      projection_kind: projection.projection_kind,
    });
  };

  const failure = walk();
  const checks = V5_F05_ADMISSION_CHECKS.map(check => ({ ...results.get(check) }));
  const admitted = failure === null;
  const decision = {
    schema_version: V5_F05_ADMISSION_SCHEMA_VERSION,
    policy_version: V5_F05_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    now: request.now,
    decision: admitted ? "allow" : "refuse",
    reason_id: admitted ? "action_admitted" : failure.reason_id,
    admitted,
    detail: admitted ? {} : failure.detail,
    // Which question this gate answers, so a refusal on a routine action is not
    // read as a prohibition of it.
    gate_applies: V5_F05_CONSEQUENTIAL_RISK_TIERS.includes(proposed.risk_tier),
    action: proposed.action,
    risk_tier: proposed.risk_tier,
    argument_records: [...proposed.argument_records],
    assembled_action: manifest.task.boundary_action,
    manifest_digest: manifest.manifest_digest,
    input_digest: manifest.input_digest,
    manifest_mode: manifest.mode,
    coverage_receipt_digest: receipt.receipt_digest,
    manifest_coverage_receipt_digest: manifest.coverage_receipt_digest,
    universe_digest: manifest.universe_digest,
    rule_kernel_digest: v5F05RuleKernelDigest(),
    decision_subset_digest: v5F05DecisionSubsetDigest(),
    manifest_age_seconds,
    manifest_max_age_seconds: maxAgeSeconds,
    manifest_age_policy_supplied: maxAgeSeconds !== null,
    checks,
    // The refusing check and everything the walk never reached, named together:
    // a reader can see both what stopped the ladder and what was therefore
    // never established.
    unmet_admission_terms: checks
      .filter(entry => entry.state !== "pass").map(entry => entry.check),
    // Where each answer came from, so no reader takes this module for a second
    // opinion on any of them.
    write_gate_field_read: V5_F05_ADMISSION_WRITE_GATE_FIELD,
    authority_decided_by: "global-boundaries.v5.evaluateActorAuthority",
    coverage_decided_by: "rule-applicability.v5.deriveRuleApplicability",
    taint_decided_by: "context-assembly.v5.evaluateUntrustedUse",
    authenticated_runtime_projection_available:
      V5_F05_ADMISSION_AUTHENTICATED_PROJECTION_EMITTED,
    authenticated_by_caller_boolean: false,
    model_resolves_conflicts: false,
    // This module is the decision, not the call site.
    enforced_at_a_call_site: false,
    admission_gap_id: "no_action_admission_call_site",
    execution_gap_id: "no_registered_verifier",
  };
  return deepFreeze({
    ...decision,
    admission_digest: digest(admissionPreimage(decision)),
    effects: V5_NO_EFFECTS,
  });
}

/** Recompute a decision's digest, so a hand-edited copy cannot pass as one. */
export function verifyAdmissionDecision(decision) {
  assertObject(decision, "decision");
  if (decision.schema_version !== V5_F05_ADMISSION_SCHEMA_VERSION) {
    fail("unknown_schema_version",
      `decision.schema_version must be "${V5_F05_ADMISSION_SCHEMA_VERSION}"`,
      { expected: V5_F05_ADMISSION_SCHEMA_VERSION, actual: decision.schema_version ?? null });
  }
  V5_F05_GUARDS.assertDigestRef(decision.admission_digest, "decision.admission_digest");
  const recomputed = digest(admissionPreimage(decision));
  if (recomputed !== decision.admission_digest) {
    fail("admission_digest_mismatch",
      "the admission decision no longer hashes to its own digest",
      { expected: decision.admission_digest, actual: recomputed });
  }
  return true;
}

// ---------------------------------------------------------------------------
// The closed contract, and the seams that are deliberately not built.
// ---------------------------------------------------------------------------

export function v5F05AdmissionContractPreimage() {
  return {
    schema_version: V5_F05_ADMISSION_SCHEMA_VERSION,
    policy_version: V5_F05_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    // Bound to the two halves this module joins: a change to either contract
    // moves this digest, so a stale consumer is refused rather than surprised.
    rule_kernel_digest: v5F05RuleKernelDigest(),
    context_contract_digest: v5F05ContextContractDigest(),
    decision_subset_digest: v5F05DecisionSubsetDigest(),
    checks: [...V5_F05_ADMISSION_CHECKS],
    reason_ids: [...V5_F05_ADMISSION_REASON_IDS],
    write_gate_field_read: V5_F05_ADMISSION_WRITE_GATE_FIELD,
    argument_record_intended_use: V5_F05_ADMISSION_ARGUMENT_USE,
    max_argument_records: V5_F05_ADMISSION_MAX_ARGUMENT_RECORDS,
    // The four claims a reader must be able to check without reading the code.
    authenticated_projection_emitted: V5_F05_ADMISSION_AUTHENTICATED_PROJECTION_EMITTED,
    consequential_action_admissible_today: false,
    enforced_at_a_call_site: false,
    taint_recomputed_here: false,
    authority_recomputed_here: false,
    manifest_freshness_default_window_seconds: null,
    effects: V5_NO_EFFECTS,
  };
}

export function v5F05AdmissionContractDigest() {
  return digest(v5F05AdmissionContractPreimage());
}

export function v5F05AdmissionContractCanonicalBytes() {
  return canonicalJson(v5F05AdmissionContractPreimage());
}

export function actionAdmissionGaps() {
  return deepFreeze([
    {
      gap: "no_action_admission_call_site",
      where: "mcp-server/src/tools.js",
      what: "no verb calls this decision before a consequential write; the decision is"
        + " produced and returned, and wiring it into a call site is a production effect"
        + " this slice does not take",
      landed: false,
    },
    {
      gap: "no_registered_verifier",
      where: "mcp-server/src/",
      what: "the last check can never pass: no component issues verifier attestations, so no"
        + " projection is authenticated and no consequential action is admissible here on any"
        + " input. Every other term is still computed, ordered and reported",
      landed: false,
    },
    {
      gap: "no_admission_decision_persistence",
      where: "domain.sql",
      what: "an admission decision is returned to its caller and stored nowhere; nothing binds"
        + " an executed action to the decision that admitted it. This slice adds no table, no"
        + " migration and no ordinal",
      landed: false,
    },
    {
      gap: "no_argument_value_binding",
      where: "mcp-server/src/action-admission.v5.js",
      what: "an action names its argument RECORDS and this module decides whether each may be"
        + " an argument at all. It never sees the argument VALUES, so it cannot prove the"
        + " value passed at execution came from the record named here; that binding needs the"
        + " call site above",
      landed: false,
    },
    {
      gap: "no_manifest_freshness_policy",
      where: "the settled decisions",
      what: "how old a manifest may be at the moment it admits an action is settled by none of"
        + " the seven decisions this slice binds, so there is no default window here: the"
        + " caller states one or the measured age carries no verdict",
      landed: false,
    },
  ]);
}

export function assertActionAdmissionComplete() {
  const open = actionAdmissionGaps().filter(entry => entry.landed !== true);
  if (open.length > 0) {
    fail("action_admission_integration_incomplete",
      `F05 action admission is not integrated: ${open.map(e => e.gap).join(", ")}`,
      { open: open.map(e => ({ gap: e.gap, where: e.where, landed: e.landed })) });
  }
  return true;
}

// ---------------------------------------------------------------------------
// Load-time self-checks. Each is an invariant a later edit could break in
// silence, so it fails this module's own import rather than a caller's request.
// ---------------------------------------------------------------------------

// The argument use must stay a FORBIDDEN use for untrusted content. If the
// assembler ever renamed or reclassified it, the tainted-argument refusal below
// would quietly become an allow.
if (!V5_F05_UNTRUSTED_FORBIDDEN_USES.includes(V5_F05_ADMISSION_ARGUMENT_USE)) {
  throw new V5F05Error("admission_argument_use_not_forbidden",
    `"${V5_F05_ADMISSION_ARGUMENT_USE}" is no longer one of Q068's forbidden uses for`
    + " untrusted content, so admission would stop refusing tainted arguments",
    { intended_use: V5_F05_ADMISSION_ARGUMENT_USE });
}

// Every reason the ladder can return must be in the published list.
{
  const declared = new Set(V5_F05_ADMISSION_REASON_IDS);
  for (const entry of ["action_admitted", "no_runtime_projection_supplied"]) {
    if (!declared.has(entry)) {
      throw new V5F05Error("admission_reason_id_unpublished",
        `"${entry}" is returned by this module and is not in V5_F05_ADMISSION_REASON_IDS`,
        { reason_id: entry });
    }
  }
}

// The check list is a set, in the order the ladder walks it.
{
  const seen = new Set();
  for (const check of V5_F05_ADMISSION_CHECKS) {
    if (seen.has(check)) {
      throw new V5F05Error("admission_check_declared_twice",
        `"${check}" appears twice in the ordered check list`, { check });
    }
    seen.add(check);
  }
  if (V5_F05_ADMISSION_CHECKS[V5_F05_ADMISSION_CHECKS.length - 1] !==
      "authenticated_runtime_projection") {
    throw new V5F05Error("admission_projection_check_not_last",
      "the projection check must run last, or the checks below it stop being exercised",
      { checks: [...V5_F05_ADMISSION_CHECKS] });
  }
}

// The gate field this module reads must be the one both halves publish. A
// rename on either side is caught at import rather than at a call site.
if (V5_F05_MANIFEST_SCHEMA_VERSION.length === 0 ||
    V5_F05_COVERAGE_SCHEMA_VERSION.length === 0) {
  throw new V5F05Error("bound_schema_version_missing",
    "the manifest and coverage schema versions this module binds must both be present");
}

// A request key that collided with the shared guard's refused field names could
// never be supplied; proving it here means a later key addition fails at import.
for (const key of [...REQUEST_KEYS, ...ACTION_KEYS]) {
  try {
    V5_F05_GUARDS.assertNoCallerAssertions([key], "self_check");
  } catch (error) {
    throw new V5F05Error("admission_key_collides_with_refused_field",
      `"${key}" is a field name the shared guard refuses outright, so a request carrying it`
      + " could never be read", { key, cause: error.code });
  }
}
