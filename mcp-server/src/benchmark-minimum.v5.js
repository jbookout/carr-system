// DoctorCRE v5 slice V5-A00: the CLOSED benchmark contract and the deterministic
// Foundation/Assurance minimum receipt join (requirement Q012, decision Q012.D1,
// gate obligation Q012.D2).
//
// TWO THINGS LIVE HERE and they are deliberately different in kind:
//
//   1. benchmark-manifest.v1 — a CLOSED schema of exactly thirty fields whose
//      SLO, cost and clock constants are identity, not configuration. This file
//      can compute the exact payload digest a verified partner would have to
//      accept, and can authenticate an acceptance envelope that already exists.
//      It CANNOT accept. Acceptance is a human act performed elsewhere by the
//      verified_partner_benchmark_authority; the four envelope fields
//      (benchmark_manifest_digest, accepted_by_identity, accepted_at, status)
//      are recorded by the gateway from that act and are never caller-created
//      authority. `evaluateBenchmarkAdmissibility` proposes; it never accepts.
//
//   2. step:foundation-assurance-minimum-receipt — the join over eight consumed
//      gates. It produces a PROPOSED consumer-gate-receipt.v1, never an issued
//      one. `issued`, `persisted` and `clock_started` are false on every result
//      and there is no code path that sets them true, because durable issuance
//      and the Journey 1 clock origin belong to adapters this file does not
//      contain and cannot reach.
//
// WHAT THIS FILE IS NOT. It is not a gateway, an issuer, a persistence layer, a
// clock, an acceptance path or an activation path. It reads no filesystem, no
// network, no database, no environment and no clock: every instant comes from
// the authenticated `as_of`. `V5_NO_EFFECTS` rides on every result to say so.
//
// AUTHENTICATION IS AN INSTALLED CODE DEPENDENCY, NEVER A JSON CLAIM. The join
// is reachable only through createFoundationAssuranceMinimumGate, whose
// `authenticateEvidence` is trusted server code that authenticates the RAW
// artifacts, the LIVE identities, their currentness, and the exact digest of the
// supplied envelope. Ordinary request JSON cannot install it, and no field named
// `verified_human`, `partner_confirmed` or similar exists anywhere below: a
// closed schema refuses one. `authority_class` is likewise not self-asserted —
// the verifier must derive it from identity.js's authorizationClassForActor over
// the LIVE actor, exactly as global-boundaries.v5.js does, and must never read
// it back out of a stored record. A projection that copies a stored class string
// turns the partner test into the caller boolean this file forbids.
//
// REUSED, NOT REDECIDED. Canonical hashing is artifact-trust.js's `digest`; the
// partner predicate is identity.js's `isKnownPartner`; the deadline contract is
// journey-one-clock.v5.js's JOURNEY_ONE_DEADLINE_CONTRACT, reconciled against
// the manifest's own closed deadline_contract at module load (see
// BENCHMARK_DEADLINE_CONTRACT) so the two can never drift into two authorities.
// The small parsing helpers below are re-stated rather than imported because the
// equivalents in the sibling v5 modules are private to those files; they are
// deliberately identical in behaviour, not a second policy.
//
// FOUR PLACES WHERE r7 IS SILENT, stated rather than papered over:
//
//   * step:gate-zero-read-only-outcome HAS NO ENTRY in r7's
//     receipt_producer_step_registry. Eight producers depend on it and the item
//     catalog lists it as an evidence input, but it declares no producer role,
//     oracle, output schema, evidence scope or produced gate id. This file
//     therefore refuses to invent a Gate Zero receipt schema. It requires the
//     authenticated projection to carry the Gate Zero outcome as an
//     authenticated fact — an outcome digest and an observed instant — and it
//     enforces the one ordering the catalog does settle: every member, and the
//     benchmark acceptance in particular, must be observed STRICTLY after it.
//     r7 is silent on the boundary instant itself, so the exclusive reading is
//     stated here rather than left to differ between the two paths: a member
//     observed AT the Gate Zero instant is refused exactly as an acceptance
//     recorded at that instant is.
//   * consumer-gate-receipt.v1 declares twenty-one required fields, no
//     `schema_version` among them, and additional_properties false — so an
//     r7-exact receipt carries no schema_version and one that does is refused
//     here. The M01 clock kernel REQUIRES that field on the same receipt. The
//     divergence is real; `journeyOneClockMinimumReceiptView` below is the
//     explicit adapter, and reconciling the two shapes is integration work.
//     That field set is only ONE of THREE M01 divergences; all three are named
//     under THE M01 SEAM below rather than left as a single sentence.
//
// THE M01 SEAM, in full. journey-one-clock.v5.js consumes what this module
// proposes, and its domains are narrower than r7's in three places. A00 stays on
// the r7-exact side of all three — r7's accepted values are preserved and no new
// global cap is invented here — and the seam is where the mismatch is made
// visible, never where evidence is quietly repaired:
//
//   1. FIELD SET. M01's MINIMUM is the twenty-one r7 fields plus schema_version.
//      `journeyOneClockMinimumReceiptView` adds that field and nothing else.
//   2. REFERENCE DOMAIN. M01 caps every `safe:` and `session:` reference at 300
//      characters. r7 declares minLength 1 and NO maximum, so this file declares
//      none either: an r7-legal evidence_ref longer than 300 characters is
//      admissible evidence HERE and is refused by M01. The view therefore
//      REJECTS such a receipt with `m01_incompatible_evidence_ref` (or
//      `m01_incompatible_session_ref`) rather than truncating it — a truncated
//      reference is a different reference, and shortening evidence to fit a
//      consumer is the one repair this seam must never perform.
//   3. TIMESTAMP DOMAIN. `ISO_INSTANT` here accepts 1..9 fractional-second
//      digits, as r7's date-time does; M01's stamp accepts 1..3. The proposed
//      receipt is safe in practice only because `iso()` emits exactly three, so
//      the view rejects a wider instant with `m01_incompatible_timestamp` rather
//      than rounding it.
//
// A FOURTH, SEPARATE MISMATCH, which the view does not and must not touch: the
// accepted manifest's `deadline_contract` carries the pause budget in DAYS and
// M01's projection field `benchmark.deadline_contract` is compared against the
// HOURS variant. The module-load reconciliation below keeps the two from
// drifting; it does NOT convert between them, and nothing in this file converts
// a manifest deadline_contract into an M01 projection deadline_contract. Doing
// that automatically would make a unit assumption on the caller's behalf, which
// is precisely what carrying two unit conventions exists to prevent. Building
// the M01 projection is integration work, named below.
//   * `outlier_rule` is free text (5..300 chars). No engine can execute a
//     sentence, so this file BINDS it (a measurement set must quote the accepted
//     rule byte-for-byte) and BOUNDS it (exclusions may never take a cell below
//     samples_per_cell) without pretending to apply it.
//   * cost_variance_thresholds are enforced as exact constants. r7 defines no
//     variance measure or denominator, so no cost-variance evaluator is written
//     here; inventing one would be a second cost authority.
//
// WHAT REMAINS INTEGRATION WORK, named rather than implied:
//   * Durable issuance of the proposed receipt, and the compare-and-swap that
//     makes the FIRST such issuance the clock origin. Returning a proposed
//     receipt does not claim it was persisted, which is why every result carries
//     durable_receipt_issuance_required.
//   * The live admission adapter that authenticates raw evidence into the
//     projection this file consumes.
//   * Recording a verified partner's exact-hash benchmark acceptance into the
//     four-field envelope. This file verifies such an envelope; it cannot make one.
//   * Bounding the PROPOSED receipt's own TTL against the DOWNSTREAM ISSUER's
//     policy. `binding.minimum_receipt_ttl_ms` is validated here only as a
//     positive integer, because r7 supplies no constraint on it at all — not an
//     absolute maximum, and not any relation to `maximum_member_receipt_ttl_ms`,
//     which is a policy over CONSUMED evidence and not over what this join
//     proposes. Inventing either would be a second TTL authority. The real bound
//     is the issuer's: M01 refuses a minimum receipt whose window exceeds its own
//     `binding.maximum_receipt_ttl_ms`, so the issuance adapter must propose a
//     TTL its own policy admits. This file states that obligation and declines
//     to guess its value.
//   * Reconciling the three M01 divergences named under THE M01 SEAM above, and
//     building M01's `benchmark.deadline_contract` projection field in the HOURS
//     convention from the manifest's DAYS one. No code here performs that
//     conversion.

import { digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID, isKnownPartner } from "./identity.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import { JOURNEY_ONE_DEADLINE_CONTRACT } from "./journey-one-clock.v5.js";

// --- schema identity --------------------------------------------------------

/** r7 schema names. These are contract identity and are matched exactly. */
export const BENCHMARK_MANIFEST_SCHEMA = "benchmark-manifest.v1";
export const CONSUMER_GATE_RECEIPT_SCHEMA = "consumer-gate-receipt.v1";
export const AUTHENTICATED_RECEIPT_IDENTITY_SCHEMA = "authenticated-receipt-identity.v1";
/** r7 canonicalization_contract, receipt_payload_digest_rule. */
export const BENCHMARK_PAYLOAD_DOMAIN_TAG = "doctorcre:benchmark-payload:v1";

// Module-local adapter schemas. NOT r7 schemas, and deliberately namespaced so
// they can never be mistaken for one: r7 declares no projection shape, no
// measurement-evidence shape and no admissibility shape at all.
export const FOUNDATION_ASSURANCE_MINIMUM_SCHEMA = "doctorcre-v5-foundation-assurance-minimum.v1";
export const FOUNDATION_ASSURANCE_MINIMUM_PROJECTION = "doctorcre-v5-foundation-assurance-minimum-projection.v1";
export const BENCHMARK_ADMISSIBILITY_SCHEMA = "doctorcre-v5-benchmark-admissibility.v1";
export const BENCHMARK_MEASUREMENT_SET_SCHEMA = "doctorcre-v5-benchmark-measurement-set.v1";
export const BENCHMARK_CELL_DOMAIN_TAG = "doctorcre:benchmark-cell:v1";

// --- the closed benchmark constants -----------------------------------------

/** r7 benchmark_manifest_schema.field_schemas.slo_thresholds — three consts. */
export const BENCHMARK_SLO_THRESHOLDS = Object.freeze({
  warm_core_navigation_p95_ms: 2000,
  cold_lcp_p95_ms: 4000,
  command_acknowledgement_p95_ms: 300,
});
/** r7 cost_variance_thresholds — two consts, in basis points. */
export const BENCHMARK_COST_VARIANCE_THRESHOLDS = Object.freeze({
  warn_basis_points: 15000,
  mandatory_replan_basis_points: 20000,
});
/** Workload weights are basis points and must total exactly this. */
export const WORKLOAD_WEIGHT_TOTAL_BASIS_POINTS = 10000;
/** The single permitted p95 aggregation method string. */
export const P95_AGGREGATION_METHOD = "nearest-rank-per-required-cell-all-cells-must-pass";
/** cache_states is a two-value closed enum with minItems 2: both are required. */
export const BENCHMARK_CACHE_STATES = Object.freeze(["cold", "warm"]);
/** r7 minimums for the sampling floor. */
export const MINIMUM_SAMPLES_PER_CELL = 20;
export const MINIMUM_WARMUP_RUNS = 1;

/**
 * The manifest's own closed deadline_contract, verbatim from r7. It carries the
 * pause budget in DAYS; the M01 kernel carries the same budget in HOURS because
 * a day is the one unit that file exists to stop anyone assuming is 24 hours.
 * The reconciliation below is what keeps them one contract rather than two: a
 * future edit to either side fails this module's own import.
 */
export const BENCHMARK_DEADLINE_CONTRACT = Object.freeze({
  timezone: "America/Chicago",
  calendar_days: 30,
  clock_origin_gate_id: "foundation-assurance-minimum-accepted",
  clock_origin_rule: "observed_at of the first current passing foundation-assurance-minimum receipt that makes Journey 1 admissible",
  maximum_external_blocker_pause_days: 5,
  reset_policy: "never_reset_or_rebase_elapsed_history",
  amendment_policy: "verified_partner_exact_hash_amendment_preserves_original_origin_and_elapsed_history",
  clock_terminus_gate_id: "journey-one-kernel-production-accepted",
  kernel_obligation_decision_ids: Object.freeze(["Q002.D1", "Q014.D1", "Q123.D1"]),
  miss_consequence: "mark_deadline_missed_require_replan_preserve_origin_and_elapsed_continue_safe_construction_without_claiming_deadline_success",
});

// --- the eight consumed gates and their exact producer contracts ------------

export const MINIMUM_GATE_ID = "foundation-assurance-minimum-accepted";
export const MINIMUM_STEP_REF = "step:foundation-assurance-minimum-receipt";
export const MINIMUM_PRODUCER_ROLE = "independent_foundation_assurance_minimum_oracle";
export const MINIMUM_ORACLE_REF = "oracle:gate-producer:foundation-assurance-minimum";
export const MINIMUM_ORACLE_VERSION = "1.0.0";
export const MINIMUM_COMBINER = "all_current_independent_pass";
export const MINIMUM_EVIDENCE_SCOPE = "candidate-and-test";
export const MINIMUM_SUBJECT_ENVIRONMENT = "candidate";
export const MINIMUM_TARGET_DAG = "foundation";
export const MINIMUM_CAUSAL_PHASE = "pre_activation";
export const MINIMUM_OBLIGATION_DECISION_IDS = Object.freeze(["Q012.D2"]);
/** The dependency step r7 references but never registers. See the header. */
export const GATE_ZERO_STEP_REF = "step:gate-zero-read-only-outcome";
export const BENCHMARK_GATE_ID = "benchmark-contract-accepted";
export const BENCHMARK_STEP_REF = "step:benchmark-contract-human-exact-hash-acceptance-receipt";
export const BENCHMARK_PRODUCER_ROLE = "verified_partner_benchmark_authority";
export const BENCHMARK_ORACLE_REF = "oracle:gate-producer:benchmark-human-exact-hash-acceptance";
export const BENCHMARK_COMBINER = "exact_verified_partner_hash_acceptance";

/**
 * The join's closed member set, copied field-for-field from r7's
 * receipt_producer_step_registry and consumer_gate_registry and C-sorted by
 * step_ref so two readers enumerate it identically. Nothing here is derived,
 * defaulted or inferred; a member absent from this list is not admissible
 * evidence for this gate, and a member present in it is mandatory.
 */
export const MINIMUM_REQUIRED_MEMBERS = Object.freeze([
  {
    step_ref: "step:assurance-fabric-preactivation-contract-receipt",
    gate_id: "assurance-fabric-child-accepted",
    producer_role: "independent_assurance_contract_reviewer",
    oracle_ref: "oracle:gate-producer:assurance-preactivation",
    oracle_version: "1.0.0", evidence_scope: "candidate-and-test", subject_environment: "candidate",
    output_schema_ref: CONSUMER_GATE_RECEIPT_SCHEMA, combiner: "all_current_independent_pass",
  },
  {
    step_ref: BENCHMARK_STEP_REF,
    gate_id: BENCHMARK_GATE_ID,
    producer_role: BENCHMARK_PRODUCER_ROLE,
    oracle_ref: BENCHMARK_ORACLE_REF,
    oracle_version: "1.0.0", evidence_scope: "human-policy-review", subject_environment: "human-review",
    output_schema_ref: BENCHMARK_MANIFEST_SCHEMA, combiner: BENCHMARK_COMBINER,
  },
  {
    step_ref: "step:foundation-control-plane-preactivation-contract-receipt",
    gate_id: "foundation-control-plane-child-accepted",
    producer_role: "independent_foundation_contract_reviewer",
    oracle_ref: "oracle:gate-producer:foundation-preactivation",
    oracle_version: "1.0.0", evidence_scope: "candidate-and-test", subject_environment: "candidate",
    output_schema_ref: CONSUMER_GATE_RECEIPT_SCHEMA, combiner: "all_current_independent_pass",
  },
  {
    step_ref: "step:global-execution-contract-independent-receipt",
    gate_id: "global-execution-contract-accepted",
    producer_role: "independent_effect_contract_oracle",
    oracle_ref: "oracle:gate-producer:global-execution",
    oracle_version: "1.0.0", evidence_scope: "candidate-and-test", subject_environment: "candidate",
    output_schema_ref: CONSUMER_GATE_RECEIPT_SCHEMA, combiner: "all_current_independent_pass",
  },
  {
    step_ref: "step:global-no-phi-boundary-independent-receipt",
    gate_id: "global-phi-boundary-accepted",
    producer_role: "independent_privacy_oracle",
    oracle_ref: "oracle:gate-producer:global-phi",
    oracle_version: "1.0.0", evidence_scope: "candidate-and-test", subject_environment: "candidate",
    output_schema_ref: CONSUMER_GATE_RECEIPT_SCHEMA, combiner: "all_current_independent_pass",
  },
  {
    step_ref: "step:global-prompt-injection-boundary-independent-receipt",
    gate_id: "global-prompt-injection-boundary-accepted",
    producer_role: "independent_adversarial_content_oracle",
    oracle_ref: "oracle:gate-producer:global-prompt-injection",
    oracle_version: "1.0.0", evidence_scope: "candidate-and-test", subject_environment: "candidate",
    output_schema_ref: CONSUMER_GATE_RECEIPT_SCHEMA, combiner: "all_current_independent_pass",
  },
  {
    step_ref: "step:global-secrets-boundary-independent-receipt",
    gate_id: "global-secrets-boundary-accepted",
    producer_role: "independent_secret_boundary_oracle",
    oracle_ref: "oracle:gate-producer:global-secrets",
    oracle_version: "1.0.0", evidence_scope: "candidate-and-test", subject_environment: "candidate",
    output_schema_ref: CONSUMER_GATE_RECEIPT_SCHEMA, combiner: "all_current_independent_pass",
  },
  {
    step_ref: "step:global-source-authority-independent-receipt",
    gate_id: "global-source-authority-accepted",
    producer_role: "independent_source_authority_oracle",
    oracle_ref: "oracle:gate-producer:global-source-authority",
    oracle_version: "1.0.0", evidence_scope: "candidate-and-test", subject_environment: "candidate",
    output_schema_ref: CONSUMER_GATE_RECEIPT_SCHEMA, combiner: "all_current_independent_pass",
  },
].map(Object.freeze));

const MEMBER_BY_STEP = new Map(MINIMUM_REQUIRED_MEMBERS.map(m => [m.step_ref, m]));

/** r7 producer_role_registry, verbatim. A role outside it is not a role. */
export const PRODUCER_ROLE_REGISTRY = Object.freeze([
  "independent_adversarial_content_oracle", "independent_assurance_contract_reviewer",
  "independent_assurance_terminal_oracle", "independent_authority_and_caller_oracle",
  "independent_caller_inventory_oracle", "independent_cost_conservation_oracle",
  "independent_effect_contract_oracle", "independent_foundation_assurance_minimum_oracle",
  "independent_foundation_contract_reviewer", "independent_foundation_terminal_oracle",
  "independent_heat_map_privacy_oracle", "independent_human_operability_contract_oracle",
  "independent_human_operability_outcome_oracle", "independent_journey_one_contract_oracle",
  "independent_journey_three_contract_oracle", "independent_journey_three_outcome_oracle",
  "independent_journey_two_contract_oracle", "independent_journey_two_outcome_oracle",
  "independent_partner_connector_oracle", "independent_portfolio_semantic_reviewer",
  "independent_privacy_oracle", "independent_representative_workflow_contract_oracle",
  "independent_representative_workflow_outcome_oracle", "independent_restore_oracle",
  "independent_rollout_join_oracle", "independent_secret_boundary_oracle",
  "independent_source_authority_oracle", "independent_successor_control_oracle",
  "independent_successor_control_outcome_oracle", "independent_tour_map_contract_oracle",
  "verified_partner_design_authority", "verified_partner_policy_authority",
  "independent_attended_effect_contract_oracle", "verified_partner_attended_effect_authority",
  "verified_partner_benchmark_authority", "independent_journey_one_kernel_outcome_oracle",
  "independent_journey_one_outcome_oracle",
]);
const PRODUCER_ROLES = new Set(PRODUCER_ROLE_REGISTRY);
/** r7 subject_environment_registry and evidence_scope_registry, verbatim. */
export const SUBJECT_ENVIRONMENT_REGISTRY = Object.freeze([
  "candidate", "test", "staging", "production", "human-review",
]);
export const EVIDENCE_SCOPE_REGISTRY = Object.freeze([
  "accepted-candidate", "candidate-and-staging", "candidate-and-test", "human-pilot",
  "human-policy-review", "partner-devices", "production", "production-and-partner-devices",
  "production-control-plane", "production-ledger", "production-portfolio", "production-registry",
  "recovery-exercise",
]);
const SUBJECT_ENVIRONMENTS = new Set(SUBJECT_ENVIRONMENT_REGISTRY);
const EVIDENCE_SCOPES = new Set(EVIDENCE_SCOPE_REGISTRY);
const RECEIPT_STATUSES = new Set(["pass", "fail", "unknown", "stale", "quarantined"]);
const NEGATIVE_ADMISSION_RESULT = "all_required_denials_observed";

// --- closed field lists (r7 required_fields, additional_properties false) ----

export const BENCHMARK_MANIFEST_FIELDS = Object.freeze([
  "subject_digest", "candidate_digest", "policy_digest", "benchmark_manifest_digest",
  "capacity_profiles", "workload_mix", "request_size_distribution", "concurrency_levels",
  "arrival_patterns", "routes", "browsers", "runtime_versions", "device_profiles",
  "hardware_profiles", "network_profiles", "cache_states", "samples_per_cell", "warmup_runs",
  "p95_aggregation_method", "outlier_rule", "acknowledgement_endpoints", "evaluator_identities",
  "comparator_versions", "slo_thresholds", "cost_expectation_matrix_digest",
  "cost_variance_thresholds", "deadline_contract", "accepted_by_identity", "accepted_at", "status",
]);
/**
 * The four fields the gateway records from a verified human acceptance of the
 * exact payload digest. They are excluded from the payload by the
 * canonicalization contract, so no artifact hashes its own digest.
 */
export const BENCHMARK_ACCEPTANCE_ENVELOPE_FIELDS = Object.freeze([
  "accepted_at", "accepted_by_identity", "benchmark_manifest_digest", "status",
]);
export const BENCHMARK_PAYLOAD_FIELDS = Object.freeze(
  BENCHMARK_MANIFEST_FIELDS.filter(f => !BENCHMARK_ACCEPTANCE_ENVELOPE_FIELDS.includes(f)));

export const CONSUMER_GATE_RECEIPT_FIELDS = Object.freeze([
  "gate_id", "receipt_producer_step_ref", "subject_digest", "candidate_digest", "policy_digest",
  "environment_manifest_digest", "subject_environment", "evidence_scope", "subject_maker_identity",
  "producer_identity", "evaluator_identity", "producer_role", "independent_oracle_ref",
  "oracle_version", "evidence_ref", "fixture_set_digest", "observed_at", "ttl_expires_at",
  "status", "comparator", "negative_admission_result",
]);
const IDENTITY_FIELDS = Object.freeze(["actor_id", "session_ref", "authority_class"]);
const WORKLOAD_FIELDS = Object.freeze(["workload_id", "weight_basis_points", "operation_mix_digest"]);
const REQUEST_SIZE_FIELDS = Object.freeze(["percentile", "bytes"]);
const BROWSER_FIELDS = Object.freeze(["name", "version", "build"]);
const SLO_FIELDS = Object.freeze(Object.keys(BENCHMARK_SLO_THRESHOLDS));
const COST_FIELDS = Object.freeze(Object.keys(BENCHMARK_COST_VARIANCE_THRESHOLDS));
const DEADLINE_FIELDS = Object.freeze(Object.keys(BENCHMARK_DEADLINE_CONTRACT));

const BINDING_FIELDS = Object.freeze([
  "subject_digest", "candidate_digest", "policy_digest", "minimum_environment_manifest_digest",
  "benchmark_manifest_digest", "maximum_member_receipt_ttl_ms", "minimum_receipt_ttl_ms",
]);
const GATE_ZERO_FIELDS = Object.freeze(["step_ref", "outcome_digest", "observed_at"]);
const MEMBER_FIELDS = Object.freeze(["step_ref", "receipt"]);
const MINIMUM_CONTEXT_FIELDS = Object.freeze([
  "subject_maker_identity", "producer_identity", "evaluator_identity",
  "evidence_ref", "fixture_set_digest", "comparator",
]);
const PROJECTION_FIELDS = Object.freeze([
  "schema_version", "tenant", "as_of", "binding", "gate_zero", "members", "minimum_receipt_context",
]);

// --- patterns ---------------------------------------------------------------

const SHA256_REF = /^sha256:[0-9a-f]{64}$/;
const SESSION_REF = /^session:[a-z0-9][a-z0-9:._/-]{8,199}$/;
const STEP_REF = /^step:[a-z0-9][a-z0-9:._/-]*$/;
const GATE_ID = /^[a-z0-9][a-z0-9.-]*$/;
const ORACLE_REF = /^oracle:[a-z0-9][a-z0-9:._/-]*$/;
const ORACLE_VERSION = /^[0-9]+\.[0-9]+\.[0-9]+$/;
const EVIDENCE_REF = /^safe:[a-z0-9][a-z0-9:_./-]*$/;
const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u;
const ISO_INSTANT = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$/;

/**
 * journey-one-clock.v5.js's NARROWER domains, restated here for one purpose: so
 * `journeyOneClockMinimumReceiptView` can refuse a receipt M01 could not read,
 * at the seam, with a code that names the seam. They are NOT applied to r7
 * evidence anywhere else in this file — see THE M01 SEAM in the header for why
 * an r7-legal value that M01 refuses is a reconciliation problem and not a
 * reason to cap what r7 leaves uncapped.
 */
const M01_REFERENCE = /^[a-zA-Z0-9:._/-]{3,300}$/;
const M01_INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$/;

export class BenchmarkMinimumError extends Error {
  constructor(code, detail) {
    super(code);
    this.name = "BenchmarkMinimumError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}
function fail(code, detail) { throw new BenchmarkMinimumError(code, detail); }

// --- primitives -------------------------------------------------------------

/**
 * JSON-safe under the r7 canonicalization contract: lone surrogates and
 * non-finite numbers are refused, and so is every shape that would let two
 * different values canonicalize identically — an exotic prototype (copy() is
 * JSON.stringify and honours an inherited toJSON), a symbol key, an accessor, a
 * non-enumerable property, a sparse array, or a literal "__proto__" key.
 */
function assertJsonSafe(value, path = "input") {
  if (value === null || typeof value === "boolean") return;
  if (typeof value === "string") {
    if (LONE_SURROGATE.test(value)) fail("invalid_unicode", path);
    return;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) fail("non_finite_number", path);
    return;
  }
  if (typeof value !== "object") fail("not_json", path);
  const proto = Object.getPrototypeOf(value);
  if (Array.isArray(value) ? proto !== Array.prototype : ![Object.prototype, null].includes(proto)) {
    fail("invalid_object", path);
  }
  if (Object.getOwnPropertySymbols(value).length) fail("hidden_key", path);
  for (const key of Object.getOwnPropertyNames(value)) {
    if (Array.isArray(value) && key === "length") continue;
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (descriptor.get || descriptor.set || !descriptor.enumerable || key === "__proto__") fail("hidden_key", path);
    assertJsonSafe(key, path);
    assertJsonSafe(descriptor.value, `${path}.${key}`);
  }
  if (Array.isArray(value) && Object.keys(value).length !== value.length) fail("sparse_array", path);
}

/** An open schema is an unenforced one: exact key set, no more and no less. */
function closed(value, fields, path) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail("invalid_object", path);
  const keys = Object.keys(value);
  if (keys.length !== fields.length || fields.some(f => !Object.hasOwn(value, f))) {
    fail("closed_shape", {
      path,
      missing: fields.filter(f => !Object.hasOwn(value, f)),
      extra: keys.filter(k => !fields.includes(k)),
    });
  }
  return value;
}

function assertDigest(value, path) {
  if (typeof value !== "string" || !SHA256_REF.test(value)) fail("invalid_digest", path);
  return value;
}
function assertPattern(value, pattern, code, path) {
  if (typeof value !== "string" || !pattern.test(value)) fail(code, path);
  return value;
}
function assertInteger(value, minimum, path, maximum = Number.MAX_SAFE_INTEGER) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail("invalid_integer", path);
  return value;
}
function assertText(value, min, max, path) {
  if (typeof value !== "string" || value.length < min || value.length > max) fail("invalid_text", path);
  return value;
}
/** r7 declares minLength 1 and no maximum for these; no cap is invented here. */
function assertNonEmptyString(value, path) {
  if (typeof value !== "string" || value.length === 0) fail("invalid_text", path);
  return value;
}
function assertConst(value, expected, path) {
  if (digest(value) !== digest(expected)) fail("benchmark_constant_mismatch", { path, expected });
  return value;
}
/** minItems / uniqueItems / non-empty-string item list, as r7 declares them. */
function assertStringList(value, minItems, path, { unique = true } = {}) {
  if (!Array.isArray(value) || value.length < minItems) fail("invalid_list", path);
  const seen = new Set();
  value.forEach((item, index) => {
    if (typeof item !== "string" || item.length === 0) fail("invalid_list_item", `${path}[${index}]`);
    if (unique && seen.has(item)) fail("duplicate_list_item", `${path}[${index}]`);
    seen.add(item);
  });
  return value;
}

function freeze(value) {
  if (value && typeof value === "object") { Object.values(value).forEach(freeze); Object.freeze(value); }
  return value;
}
function copy(value) { return JSON.parse(JSON.stringify(value)); }
const iso = ms => new Date(ms).toISOString();

/**
 * A date-time is parsed, never inferred. The calendar is checked against the
 * LITERAL fields before parsing, because Date.parse normalizes an impossible
 * date rather than refusing it: "2026-02-31T00:00:00Z" silently becomes 3 March,
 * and evidence bound to an instant nobody wrote is not bound at all.
 */
function stamp(value, path) {
  const match = typeof value === "string" ? ISO_INSTANT.exec(value) : null;
  if (!match) fail("invalid_timestamp", path);
  const [year, month, day, hour, minute, second] = match.slice(1, 7).map(Number);
  const probe = new Date(0);
  probe.setUTCFullYear(year, month - 1, day);
  probe.setUTCHours(hour, minute, second, 0);
  const offset = match[8];
  if (probe.getUTCFullYear() !== year || probe.getUTCMonth() !== month - 1 || probe.getUTCDate() !== day ||
      hour > 23 || minute > 59 || second > 59 ||
      (offset !== "Z" && (Number(offset.slice(1, 3)) > 23 || Number(offset.slice(4)) > 59))) {
    fail("invalid_timestamp", path);
  }
  const ms = Date.parse(value);
  if (!Number.isSafeInteger(ms)) fail("invalid_timestamp", path);
  return ms;
}

/**
 * authenticated-receipt-identity.v1. `partner` additionally requires the exact
 * verified-partner class — see the header on why that string must be derived by
 * the installed verifier from the LIVE actor and never read back out of a
 * stored record.
 */
function assertIdentity(value, path, { partner = false } = {}) {
  closed(value, IDENTITY_FIELDS, path);
  if (typeof value.actor_id !== "string" || value.actor_id.length === 0) fail("invalid_identity", path);
  assertPattern(value.session_ref, SESSION_REF, "invalid_identity", `${path}.session_ref`);
  if (typeof value.authority_class !== "string" || value.authority_class.length === 0) fail("invalid_identity", path);
  if (partner && (!isKnownPartner(value.actor_id) || value.authority_class !== "verified_partner")) {
    fail("verified_partner_required", path);
  }
  return value;
}
/** Two seats collide when they share EITHER the actor or the session. */
function sameSeat(a, b) { return a.actor_id === b.actor_id || a.session_ref === b.session_ref; }

// --- module-load reconciliation with the M01 deadline contract --------------

// One deadline contract, two unit conventions. If a later edit moves either
// side, this module refuses to load rather than letting a second clock policy
// exist quietly beside the first.
for (const field of DEADLINE_FIELDS) {
  if (field === "maximum_external_blocker_pause_days") continue;
  if (digest(BENCHMARK_DEADLINE_CONTRACT[field]) !== digest(JOURNEY_ONE_DEADLINE_CONTRACT[field])) {
    throw new BenchmarkMinimumError("deadline_contract_drift", { field });
  }
}
if (BENCHMARK_DEADLINE_CONTRACT.maximum_external_blocker_pause_days * 24 !==
    JOURNEY_ONE_DEADLINE_CONTRACT.maximum_external_blocker_pause_hours) {
  throw new BenchmarkMinimumError("deadline_contract_drift", { field: "maximum_external_blocker_pause" });
}

// The benchmark member's registry entry against this module's own constants.
// This is a LOAD-TIME INVARIANT over two frozen tables, not a check on evidence:
// benchmark-manifest.v1 carries no producer_role, oracle_ref or combiner for a
// validator to compare a receipt against, so the same comparison inside the join
// could never fire and would read like an evidence check that isn't one.
{
  const entry = MEMBER_BY_STEP.get(BENCHMARK_STEP_REF);
  for (const [field, expected] of [
    ["gate_id", BENCHMARK_GATE_ID],
    ["producer_role", BENCHMARK_PRODUCER_ROLE],
    ["oracle_ref", BENCHMARK_ORACLE_REF],
    ["combiner", BENCHMARK_COMBINER],
    ["output_schema_ref", BENCHMARK_MANIFEST_SCHEMA],
  ]) {
    if (!entry || entry[field] !== expected) {
      throw new BenchmarkMinimumError("benchmark_member_registry_drift", { field, expected });
    }
  }
}

// ---------------------------------------------------------------------------
// benchmark-manifest.v1 — the closed payload.
// ---------------------------------------------------------------------------

/**
 * Validate the twenty-six payload fields of benchmark-manifest.v1: every closed
 * field populated, every nested shape closed, the workload weights totalling
 * exactly 10000 basis points, and the fixed SLO, cost and clock constants equal
 * to this schema's. Throws on the first violation with a stable code.
 *
 * FOUR UNIQUENESS RULES ARE DERIVED, NOT COPIED, and are marked as such:
 * workload_mix, browsers, evaluator_identities and request_size_distribution
 * carry no uniqueItems in r7. Each rule over-constrains an r7 silence, so each
 * one is stated with the reason a reviewer would need in order to disagree with
 * it, rather than enforced quietly:
 *
 *   workload_mix, browsers — the pass_rule requires every matrix cell to be
 *     exercised and both of these index a cell dimension. A repeated workload_id
 *     or browser triple makes "which weight applies to this cell" unanswerable
 *     rather than merely redundant.
 *   evaluator_identities — a repeated evaluator seat is one evaluator counted
 *     twice, which inflates apparent independent review.
 *   request_size_distribution — this is the one derived rule that is NOT about
 *     the cell matrix, and it is the file's most over-constraining decision, so
 *     it is named as such. The list is a distribution WITHIN a cell: a mapping
 *     from percentile to bytes. Two entries for the same percentile make "how
 *     many bytes at p50" unanswerable, so a repeat is a malformed distribution
 *     rather than a redundant one. The consequence is stated plainly: a manifest
 *     a verified partner accepted with a repeated percentile IS denied here even
 *     though r7 would admit it, and the remedy is to change this rule, not to
 *     work around it.
 */
export function validateBenchmarkPayload(payload) {
  assertJsonSafe(payload, "payload");
  closed(payload, BENCHMARK_PAYLOAD_FIELDS, "payload");

  for (const field of ["subject_digest", "candidate_digest", "policy_digest", "cost_expectation_matrix_digest"]) {
    assertDigest(payload[field], `payload.${field}`);
  }

  assertStringList(payload.capacity_profiles, 1, "payload.capacity_profiles");
  assertStringList(payload.arrival_patterns, 1, "payload.arrival_patterns");
  assertStringList(payload.routes, 1, "payload.routes");
  assertStringList(payload.runtime_versions, 1, "payload.runtime_versions");
  assertStringList(payload.device_profiles, 1, "payload.device_profiles");
  assertStringList(payload.hardware_profiles, 1, "payload.hardware_profiles");
  assertStringList(payload.network_profiles, 1, "payload.network_profiles");
  assertStringList(payload.acknowledgement_endpoints, 1, "payload.acknowledgement_endpoints");
  assertStringList(payload.comparator_versions, 1, "payload.comparator_versions");

  if (!Array.isArray(payload.workload_mix) || payload.workload_mix.length < 1) fail("invalid_list", "payload.workload_mix");
  const workloadIds = new Set();
  let weightTotal = 0;
  payload.workload_mix.forEach((workload, index) => {
    const path = `payload.workload_mix[${index}]`;
    closed(workload, WORKLOAD_FIELDS, path);
    assertNonEmptyString(workload.workload_id, `${path}.workload_id`);
    assertInteger(workload.weight_basis_points, 1, `${path}.weight_basis_points`, WORKLOAD_WEIGHT_TOTAL_BASIS_POINTS);
    assertDigest(workload.operation_mix_digest, `${path}.operation_mix_digest`);
    if (workloadIds.has(workload.workload_id)) fail("benchmark_duplicate_workload_id", path);
    workloadIds.add(workload.workload_id);
    weightTotal += workload.weight_basis_points;
  });
  if (weightTotal !== WORKLOAD_WEIGHT_TOTAL_BASIS_POINTS) {
    fail("benchmark_weight_total_mismatch", { expected: WORKLOAD_WEIGHT_TOTAL_BASIS_POINTS, actual: weightTotal });
  }

  if (!Array.isArray(payload.request_size_distribution) || payload.request_size_distribution.length < 1) {
    fail("invalid_list", "payload.request_size_distribution");
  }
  const percentiles = new Set();
  payload.request_size_distribution.forEach((point, index) => {
    const path = `payload.request_size_distribution[${index}]`;
    closed(point, REQUEST_SIZE_FIELDS, path);
    assertInteger(point.percentile, 1, `${path}.percentile`, 100);
    assertInteger(point.bytes, 0, `${path}.bytes`);
    if (percentiles.has(point.percentile)) fail("benchmark_duplicate_request_size_percentile", path);
    percentiles.add(point.percentile);
  });

  if (!Array.isArray(payload.concurrency_levels) || payload.concurrency_levels.length < 1) {
    fail("invalid_list", "payload.concurrency_levels");
  }
  const concurrency = new Set();
  payload.concurrency_levels.forEach((level, index) => {
    assertInteger(level, 1, `payload.concurrency_levels[${index}]`);
    if (concurrency.has(level)) fail("duplicate_list_item", `payload.concurrency_levels[${index}]`);
    concurrency.add(level);
  });

  if (!Array.isArray(payload.browsers) || payload.browsers.length < 1) fail("invalid_list", "payload.browsers");
  const browsers = new Set();
  payload.browsers.forEach((browser, index) => {
    const path = `payload.browsers[${index}]`;
    closed(browser, BROWSER_FIELDS, path);
    for (const field of BROWSER_FIELDS) assertNonEmptyString(browser[field], `${path}.${field}`);
    const key = digest([browser.name, browser.version, browser.build]);
    if (browsers.has(key)) fail("benchmark_duplicate_browser", path);
    browsers.add(key);
  });

  assertStringList(payload.cache_states, 2, "payload.cache_states");
  for (const state of payload.cache_states) {
    if (!BENCHMARK_CACHE_STATES.includes(state)) fail("benchmark_unknown_cache_state", state);
  }

  assertInteger(payload.samples_per_cell, MINIMUM_SAMPLES_PER_CELL, "payload.samples_per_cell");
  assertInteger(payload.warmup_runs, MINIMUM_WARMUP_RUNS, "payload.warmup_runs");
  if (payload.p95_aggregation_method !== P95_AGGREGATION_METHOD) {
    fail("benchmark_constant_mismatch", { path: "payload.p95_aggregation_method", expected: P95_AGGREGATION_METHOD });
  }
  assertText(payload.outlier_rule, 5, 300, "payload.outlier_rule");

  if (!Array.isArray(payload.evaluator_identities) || payload.evaluator_identities.length < 1) {
    fail("invalid_list", "payload.evaluator_identities");
  }
  const evaluators = new Set();
  payload.evaluator_identities.forEach((identity, index) => {
    const path = `payload.evaluator_identities[${index}]`;
    assertIdentity(identity, path);
    const key = digest([identity.actor_id, identity.session_ref]);
    if (evaluators.has(key)) fail("benchmark_duplicate_evaluator_identity", path);
    evaluators.add(key);
  });

  closed(payload.slo_thresholds, SLO_FIELDS, "payload.slo_thresholds");
  assertConst(payload.slo_thresholds, BENCHMARK_SLO_THRESHOLDS, "payload.slo_thresholds");
  closed(payload.cost_variance_thresholds, COST_FIELDS, "payload.cost_variance_thresholds");
  assertConst(payload.cost_variance_thresholds, BENCHMARK_COST_VARIANCE_THRESHOLDS, "payload.cost_variance_thresholds");
  closed(payload.deadline_contract, DEADLINE_FIELDS, "payload.deadline_contract");
  assertConst(payload.deadline_contract, BENCHMARK_DEADLINE_CONTRACT, "payload.deadline_contract");

  return payload;
}

/** The payload half of a manifest: every required field but the four envelope ones. */
export function benchmarkPayloadOf(manifest) {
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) fail("invalid_object", "manifest");
  const payload = {};
  for (const field of BENCHMARK_PAYLOAD_FIELDS) {
    if (!Object.hasOwn(manifest, field)) fail("closed_shape", { path: "manifest", missing: [field] });
    payload[field] = manifest[field];
  }
  return payload;
}

/**
 * SHA-256 over the JCS serialization of [domain_tag, payload], per r7's
 * receipt_payload_digest_rule. This is the ONE digest a verified partner
 * accepts, and no artifact contributes its own whole-byte digest to it.
 */
export function benchmarkPayloadDigest(payload) {
  validateBenchmarkPayload(payload);
  return digest([BENCHMARK_PAYLOAD_DOMAIN_TAG, payload]);
}

/**
 * Validate a complete accepted manifest: the closed thirty fields, the payload
 * above, and the four-field acceptance envelope — including that
 * benchmark_manifest_digest is the exact payload digest, that the acceptor is a
 * verified partner, and that status is the single permitted value.
 */
export function validateBenchmarkManifest(manifest) {
  assertJsonSafe(manifest, "manifest");
  closed(manifest, BENCHMARK_MANIFEST_FIELDS, "manifest");
  const payload = benchmarkPayloadOf(manifest);
  validateBenchmarkPayload(payload);
  const expected = digest([BENCHMARK_PAYLOAD_DOMAIN_TAG, payload]);
  assertDigest(manifest.benchmark_manifest_digest, "manifest.benchmark_manifest_digest");
  if (manifest.benchmark_manifest_digest !== expected) {
    fail("benchmark_manifest_digest_mismatch", { expected, actual: manifest.benchmark_manifest_digest });
  }
  assertIdentity(manifest.accepted_by_identity, "manifest.accepted_by_identity", { partner: true });
  const acceptedAt = stamp(manifest.accepted_at, "manifest.accepted_at");
  if (manifest.status !== "accepted") fail("benchmark_status_not_accepted", manifest.status);
  return freeze({ payload_digest: expected, accepted_at_ms: acceptedAt });
}

// ---------------------------------------------------------------------------
// The workload matrix, its required cells and the nearest-rank p95 rule.
//
// r7's pass_rule requires that "every matrix cell is exercised" and fixes the
// p95 method, but it never enumerates what a cell IS. The definition below is
// module-local and stated in full so a reviewer can disagree with it precisely:
//
//   A CELL is one (metric, subject, cache_state) triple crossed with the nine
//   environment dimensions the manifest declares as lists — capacity profile,
//   workload id, concurrency level, arrival pattern, browser triple, runtime
//   version, device profile, hardware profile and network profile.
//
//   request_size_distribution is a distribution WITHIN a cell (percentile to
//   bytes), not an axis of executions. comparator_versions and
//   evaluator_identities describe the tooling and the people, not the subject.
//
//   The METRIC decides both the subject axis and the applicable fixed SLO, so a
//   threshold is never chosen by the caller:
//     core_navigation_ms        routes                    warm      2000 ms
//     lcp_ms                    routes                    cold      4000 ms
//     command_acknowledgement_ms acknowledgement_endpoints every      300 ms
//
// Workload weights govern representative coverage and cost reporting only. They
// are read to enumerate the workload axis and are never applied to a threshold;
// `weights_applied_to_thresholds: false` is on the result because that is the
// property the rule actually settles.
// ---------------------------------------------------------------------------

export const BENCHMARK_METRICS = Object.freeze({
  core_navigation_ms: Object.freeze({
    slo_key: "warm_core_navigation_p95_ms", subject_field: "routes", cache_states: Object.freeze(["warm"]),
  }),
  lcp_ms: Object.freeze({
    slo_key: "cold_lcp_p95_ms", subject_field: "routes", cache_states: Object.freeze(["cold"]),
  }),
  command_acknowledgement_ms: Object.freeze({
    slo_key: "command_acknowledgement_p95_ms", subject_field: "acknowledgement_endpoints", cache_states: null,
  }),
});
export const BENCHMARK_METRIC_KEYS = Object.freeze(Object.keys(BENCHMARK_METRICS).sort());
/**
 * A bound, not a policy: the cross product of eleven declared dimensions is
 * combinatorial, and a manifest whose required matrix exceeds this refuses
 * loudly instead of hanging. Raising it is a deliberate edit.
 */
export const MAX_REQUIRED_MATRIX_CELLS = 200000;

const CELL_FIELDS = Object.freeze([
  "arrival_pattern", "browser", "cache_state", "capacity_profile", "concurrency_level",
  "device_profile", "hardware_profile", "metric", "network_profile", "runtime_version",
  "subject", "workload_id",
]);

function cellDigest(cell) { return digest([BENCHMARK_CELL_DOMAIN_TAG, cell]); }

/**
 * Every cell the accepted manifest requires, in a deterministic order. Pure and
 * total: two callers holding the same payload enumerate the same list.
 */
export function benchmarkRequiredCells(payload) {
  validateBenchmarkPayload(payload);
  const environmentSize = payload.capacity_profiles.length * payload.workload_mix.length *
    payload.concurrency_levels.length * payload.arrival_patterns.length * payload.browsers.length *
    payload.runtime_versions.length * payload.device_profiles.length *
    payload.hardware_profiles.length * payload.network_profiles.length;
  let total = 0;
  for (const metric of BENCHMARK_METRIC_KEYS) {
    const spec = BENCHMARK_METRICS[metric];
    const states = spec.cache_states ?? payload.cache_states;
    for (const state of states) {
      if (!payload.cache_states.includes(state)) fail("benchmark_required_cache_state_absent", { metric, cache_state: state });
    }
    total += payload[spec.subject_field].length * states.length * environmentSize;
  }
  if (total > MAX_REQUIRED_MATRIX_CELLS) {
    fail("benchmark_matrix_cell_cap_exceeded", { required: total, cap: MAX_REQUIRED_MATRIX_CELLS });
  }
  const cells = [];
  for (const metric of BENCHMARK_METRIC_KEYS) {
    const spec = BENCHMARK_METRICS[metric];
    const states = spec.cache_states ?? payload.cache_states;
    for (const subject of payload[spec.subject_field]) {
      for (const cache_state of states) {
        for (const capacity_profile of payload.capacity_profiles) {
          for (const workload of payload.workload_mix) {
            for (const concurrency_level of payload.concurrency_levels) {
              for (const arrival_pattern of payload.arrival_patterns) {
                for (const browser of payload.browsers) {
                  for (const runtime_version of payload.runtime_versions) {
                    for (const device_profile of payload.device_profiles) {
                      for (const hardware_profile of payload.hardware_profiles) {
                        for (const network_profile of payload.network_profiles) {
                          cells.push({
                            arrival_pattern,
                            browser: { name: browser.name, version: browser.version, build: browser.build },
                            cache_state, capacity_profile, concurrency_level, device_profile,
                            hardware_profile, metric, network_profile, runtime_version,
                            subject, workload_id: workload.workload_id,
                          });
                        }
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
  return freeze(cells);
}

/**
 * Nearest-rank p95: sort the cell's valid post-warmup samples ascending and take
 * the one-based ceiling(0.95 * n) observation. Exported because the rule is the
 * kind of thing a reviewer should be able to check on three numbers.
 */
export function nearestRankP95(samples) {
  if (!Array.isArray(samples) || samples.length === 0) fail("invalid_list", "samples");
  const sorted = [...samples].sort((a, b) => a - b);
  return sorted[Math.ceil(0.95 * sorted.length) - 1];
}

/**
 * Evaluate a measurement set against the accepted payload's required matrix.
 *
 * Coverage is exact in both directions: a missing cell denies and so does an
 * unrequired or duplicated one. Every cell must carry exactly warmup_runs warmup
 * observations (proving warmup happened AND was excluded) and, after any
 * declared outlier exclusions, at least samples_per_cell valid ones — so an
 * exclusion can never be the thing that shrinks a cell below its floor. The set
 * must quote the accepted outlier_rule and p95 method byte-for-byte, and must
 * name the exact payload digest it was measured against.
 */
export function evaluateBenchmarkWorkloadCoverage({ payload, measurements }) {
  assertJsonSafe(measurements, "measurements");
  closed(measurements, ["schema_version", "benchmark_payload_digest", "outlier_rule",
    "p95_aggregation_method", "cells"], "measurements");
  if (measurements.schema_version !== BENCHMARK_MEASUREMENT_SET_SCHEMA) fail("wrong_measurement_schema");
  const payloadDigest = benchmarkPayloadDigest(payload);
  if (measurements.benchmark_payload_digest !== payloadDigest) {
    fail("measurement_payload_binding_mismatch", { expected: payloadDigest });
  }
  if (measurements.outlier_rule !== payload.outlier_rule) fail("measurement_outlier_rule_mismatch");
  if (measurements.p95_aggregation_method !== payload.p95_aggregation_method) fail("measurement_p95_method_mismatch");
  if (!Array.isArray(measurements.cells)) fail("invalid_list", "measurements.cells");

  const required = new Map(benchmarkRequiredCells(payload).map(cell => [cellDigest(cell), cell]));
  const measured = new Map();
  const worst = {};
  const failing = [];

  measurements.cells.forEach((entry, index) => {
    const path = `measurements.cells[${index}]`;
    closed(entry, ["cell", "warmup_samples", "samples", "excluded_sample_indexes"], path);
    closed(entry.cell, CELL_FIELDS, `${path}.cell`);
    closed(entry.cell.browser, BROWSER_FIELDS, `${path}.cell.browser`);
    const key = cellDigest(entry.cell);
    if (!required.has(key)) fail("benchmark_unrequired_cell", { path, cell: entry.cell });
    if (measured.has(key)) fail("benchmark_duplicate_cell", { path, cell: entry.cell });
    measured.set(key, true);

    for (const field of ["warmup_samples", "samples"]) {
      if (!Array.isArray(entry[field])) fail("invalid_list", `${path}.${field}`);
      entry[field].forEach((sample, i) => {
        if (!Number.isFinite(sample) || sample < 0) fail("invalid_sample", `${path}.${field}[${i}]`);
      });
    }
    if (entry.warmup_samples.length !== payload.warmup_runs) {
      fail("benchmark_warmup_run_count_mismatch", { path, expected: payload.warmup_runs });
    }
    if (!Array.isArray(entry.excluded_sample_indexes)) fail("invalid_list", `${path}.excluded_sample_indexes`);
    const excluded = new Set();
    entry.excluded_sample_indexes.forEach((i, at) => {
      assertInteger(i, 0, `${path}.excluded_sample_indexes[${at}]`, entry.samples.length - 1);
      if (excluded.has(i)) fail("duplicate_list_item", `${path}.excluded_sample_indexes[${at}]`);
      excluded.add(i);
    });
    const valid = entry.samples.filter((_, i) => !excluded.has(i));
    if (valid.length < payload.samples_per_cell) {
      fail("benchmark_sample_floor_not_met", { path, required: payload.samples_per_cell, valid: valid.length });
    }
    const spec = BENCHMARK_METRICS[entry.cell.metric];
    const threshold = payload.slo_thresholds[spec.slo_key];
    const p95 = nearestRankP95(valid);
    if (!(worst[spec.slo_key]?.p95_ms >= p95)) worst[spec.slo_key] = { p95_ms: p95, cell: entry.cell };
    if (p95 > threshold) failing.push({ cell: entry.cell, slo_key: spec.slo_key, p95_ms: p95, threshold_ms: threshold });
  });

  const missing = [...required.keys()].filter(key => !measured.has(key));
  if (missing.length > 0) {
    fail("benchmark_matrix_coverage_incomplete", {
      required: required.size, measured: measured.size, missing_count: missing.length,
      first_missing_cells: missing.slice(0, 5).map(key => required.get(key)),
    });
  }
  if (failing.length > 0) fail("benchmark_slo_not_met", { failing_cell_count: failing.length, failing_cells: failing.slice(0, 5) });

  return freeze({
    schema_version: BENCHMARK_MEASUREMENT_SET_SCHEMA,
    benchmark_payload_digest: payloadDigest,
    required_cell_count: required.size,
    measured_cell_count: measured.size,
    coverage_complete: true,
    all_required_cells_meet_slo: true,
    p95_aggregation_method: P95_AGGREGATION_METHOD,
    worst_p95_by_slo_key: worst,
    weights_applied_to_thresholds: false,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * The deterministic pre-acceptance answer, and the whole of what source
 * construction may say about a benchmark.
 *
 * It reports the ONE payload digest a verified partner would have to accept and,
 * when measurement evidence is supplied, that the matrix is completely covered
 * and every required cell meets its fixed SLO. It accepts nothing: `accepted` is
 * false, no acceptance envelope is produced, and the gate stays closed until the
 * verified_partner_benchmark_authority acts out of band, after Gate Zero, and
 * the gateway records that act into the four envelope fields.
 */
export function evaluateBenchmarkAdmissibility({ payload, measurements = null }) {
  const payloadDigest = benchmarkPayloadDigest(payload);
  const coverage = measurements === null ? null : evaluateBenchmarkWorkloadCoverage({ payload, measurements });
  return freeze({
    schema_version: BENCHMARK_ADMISSIBILITY_SCHEMA,
    manifest_schema_ref: BENCHMARK_MANIFEST_SCHEMA,
    gate_id: BENCHMARK_GATE_ID,
    producer_step_ref: BENCHMARK_STEP_REF,
    combiner: BENCHMARK_COMBINER,
    schema_admissible: true,
    workload_coverage: coverage,
    workload_coverage_evaluated: coverage !== null,
    proposed_benchmark_manifest_digest: payloadDigest,
    acceptance_required_from_producer_role: BENCHMARK_PRODUCER_ROLE,
    acceptance_required_after_step_ref: GATE_ZERO_STEP_REF,
    accepted: false,
    acceptance_recorded_here: false,
    durable_acceptance_required: true,
    authority_granted: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The Foundation/Assurance minimum join.
// ---------------------------------------------------------------------------

function validateConsumerGateMember(receipt, member, context) {
  const { binding, now, gateZeroMs, path } = context;
  closed(receipt, CONSUMER_GATE_RECEIPT_FIELDS, path);
  assertPattern(receipt.gate_id, GATE_ID, "invalid_gate_id", `${path}.gate_id`);
  assertPattern(receipt.receipt_producer_step_ref, STEP_REF, "invalid_step_ref", `${path}.receipt_producer_step_ref`);
  if (receipt.receipt_producer_step_ref !== member.step_ref) fail("member_step_mismatch", path);
  if (receipt.gate_id !== member.gate_id) fail("member_gate_mismatch", path);

  for (const [field, expected] of [
    ["subject_digest", binding.subject_digest],
    ["candidate_digest", binding.candidate_digest],
    ["policy_digest", binding.policy_digest],
    ["environment_manifest_digest", binding.minimum_environment_manifest_digest],
  ]) {
    assertDigest(receipt[field], `${path}.${field}`);
    if (receipt[field] !== expected) fail("member_binding_mismatch", { path, field });
  }

  if (!SUBJECT_ENVIRONMENTS.has(receipt.subject_environment) || !EVIDENCE_SCOPES.has(receipt.evidence_scope)) {
    fail("member_scope_unregistered", path);
  }
  // A valid enum from another environment is substitution, not evidence.
  if (receipt.subject_environment !== member.subject_environment ||
      receipt.evidence_scope !== member.evidence_scope) fail("member_scope_mismatch", path);

  if (!PRODUCER_ROLES.has(receipt.producer_role)) fail("member_role_unregistered", path);
  if (receipt.producer_role !== member.producer_role) fail("member_role_mismatch", path);
  assertPattern(receipt.independent_oracle_ref, ORACLE_REF, "invalid_oracle_ref", `${path}.independent_oracle_ref`);
  assertPattern(receipt.oracle_version, ORACLE_VERSION, "invalid_oracle_version", `${path}.oracle_version`);
  if (receipt.independent_oracle_ref !== member.oracle_ref || receipt.oracle_version !== member.oracle_version) {
    fail("member_oracle_mismatch", path);
  }

  assertPattern(receipt.evidence_ref, EVIDENCE_REF, "invalid_evidence_ref", `${path}.evidence_ref`);
  assertDigest(receipt.fixture_set_digest, `${path}.fixture_set_digest`);
  assertText(receipt.comparator, 5, 300, `${path}.comparator`);

  for (const field of ["subject_maker_identity", "producer_identity", "evaluator_identity"]) {
    assertIdentity(receipt[field], `${path}.${field}`);
  }
  // Independence is against the SUBJECT MAKER, and BOTH other seats are checked:
  // a producer that is the maker attests to its own work exactly as much as an
  // evaluator that is.
  for (const field of ["producer_identity", "evaluator_identity"]) {
    if (sameSeat(receipt.subject_maker_identity, receipt[field])) fail("member_self_attestation", { path, field });
  }

  if (!RECEIPT_STATUSES.has(receipt.status)) fail("member_status_unregistered", path);
  if (receipt.status !== "pass") fail("member_not_passing", { path, status: receipt.status });
  if (receipt.negative_admission_result !== NEGATIVE_ADMISSION_RESULT) fail("member_negative_admission_missing", path);

  const observed = stamp(receipt.observed_at, `${path}.observed_at`);
  const expires = stamp(receipt.ttl_expires_at, `${path}.ttl_expires_at`);
  if (observed > now) fail("member_receipt_observed_after_reference", path);
  // STRICTLY after Gate Zero, on the same reading validateBenchmarkMember uses
  // for the acceptance instant. Evidence observed AT the Gate Zero instant did
  // not follow it, and one boundary convention across both paths is what keeps
  // the header's sentence and this code the same claim.
  if (observed <= gateZeroMs) fail("member_observed_before_gate_zero", path);
  if (expires <= observed) fail("member_receipt_window_invalid", path);
  if (expires - observed > binding.maximum_member_receipt_ttl_ms) fail("member_receipt_ttl_policy_exceeded", path);
  if (expires <= now) fail("member_receipt_not_current", path);

  return freeze({
    observed_at_ms: observed,
    producer_identity: receipt.producer_identity,
    evaluator_identity: receipt.evaluator_identity,
    subject_maker_identity: receipt.subject_maker_identity,
  });
}

/**
 * The benchmark member takes no registry entry: benchmark-manifest.v1 carries no
 * producer_role, oracle_ref or combiner, so there is nothing on the receipt to
 * compare one against. That the registry entry itself matches this module's
 * benchmark constants is asserted once at module load, where it is a real
 * invariant over two frozen tables rather than a comparison that cannot fail.
 */
function validateBenchmarkMember(manifest, context) {
  const { binding, now, gateZeroMs, path } = context;
  const { payload_digest, accepted_at_ms } = validateBenchmarkManifest(manifest);

  for (const [field, expected] of [
    ["subject_digest", binding.subject_digest],
    ["candidate_digest", binding.candidate_digest],
    ["policy_digest", binding.policy_digest],
  ]) {
    if (manifest[field] !== expected) fail("benchmark_binding_mismatch", { path, field });
  }
  // Exact-hash acceptance: the accepted manifest must be THE bound manifest, not
  // a differently-but-validly accepted one.
  if (payload_digest !== binding.benchmark_manifest_digest) {
    fail("benchmark_acceptance_mismatched", { expected: binding.benchmark_manifest_digest, actual: payload_digest });
  }
  if (accepted_at_ms > now) fail("benchmark_accepted_after_reference", path);
  // Gate Zero precedes verified-partner benchmark acceptance. Acceptance at or
  // before the Gate Zero outcome is the excluded case, not a boundary nicety.
  if (accepted_at_ms <= gateZeroMs) fail("benchmark_accepted_before_gate_zero", path);

  return freeze({
    observed_at_ms: accepted_at_ms,
    benchmark_manifest_digest: payload_digest,
    accepted_by_identity: manifest.accepted_by_identity,
  });
}

/**
 * The pure join core. Every denial throws with one stable code; there is no
 * partial pass, because a gate that half-passes is a gate.
 */
function joinCore(snapshot, negativeAdmission) {
  assertJsonSafe(snapshot, "snapshot");
  closed(snapshot, PROJECTION_FIELDS, "snapshot");
  if (snapshot.schema_version !== FOUNDATION_ASSURANCE_MINIMUM_PROJECTION) fail("wrong_projection");
  if (snapshot.tenant !== ORGANIZATION_TENANT_ID) fail("wrong_tenant");
  const now = stamp(snapshot.as_of, "snapshot.as_of");

  const binding = snapshot.binding;
  closed(binding, BINDING_FIELDS, "snapshot.binding");
  // TWO TTL POLICIES, ONE DIRECTION EACH, and no invented relation between them.
  // `maximum_member_receipt_ttl_ms` bounds the CONSUMED evidence below and is
  // enforced per member. `minimum_receipt_ttl_ms` sizes the window this join
  // PROPOSES, and r7 constrains it in no way at all — not absolutely, and not
  // against the member policy, which governs a different set of receipts. So it
  // is validated as a positive integer and nothing more. The bound that really
  // applies is the DOWNSTREAM ISSUER's: M01 refuses a minimum receipt whose
  // window exceeds its own binding.maximum_receipt_ttl_ms, so an issuance adapter
  // must supply a value its own policy admits. Asserting a global minimum-vs-
  // member relation here would be this file inventing a TTL authority r7 did not
  // give it, so the obligation is named in the header instead of guessed at.
  for (const field of BINDING_FIELDS) {
    if (field.endsWith("_ttl_ms")) assertInteger(binding[field], 1, `snapshot.binding.${field}`);
    else assertDigest(binding[field], `snapshot.binding.${field}`);
  }

  // GATE ZERO. r7 registers no producer contract for this step (see the header),
  // so the only honest checks are that the authenticated projection carries its
  // outcome as a digest-bound fact and that it precedes everything downstream.
  const gateZero = snapshot.gate_zero;
  closed(gateZero, GATE_ZERO_FIELDS, "snapshot.gate_zero");
  if (gateZero.step_ref !== GATE_ZERO_STEP_REF) fail("wrong_gate_zero_step");
  assertDigest(gateZero.outcome_digest, "snapshot.gate_zero.outcome_digest");
  const gateZeroMs = stamp(gateZero.observed_at, "snapshot.gate_zero.observed_at");
  if (gateZeroMs > now) fail("gate_zero_observed_after_reference");

  const context = snapshot.minimum_receipt_context;
  closed(context, MINIMUM_CONTEXT_FIELDS, "snapshot.minimum_receipt_context");
  for (const field of ["subject_maker_identity", "producer_identity", "evaluator_identity"]) {
    assertIdentity(context[field], `snapshot.minimum_receipt_context.${field}`);
  }
  assertPattern(context.evidence_ref, EVIDENCE_REF, "invalid_evidence_ref", "snapshot.minimum_receipt_context.evidence_ref");
  assertDigest(context.fixture_set_digest, "snapshot.minimum_receipt_context.fixture_set_digest");
  assertText(context.comparator, 5, 300, "snapshot.minimum_receipt_context.comparator");

  // MEMBERSHIP. Exactly the eight declared members, each exactly once. An
  // unrequired step is not extra decoration, it is a claim this gate consumes
  // evidence it does not consume.
  if (!Array.isArray(snapshot.members)) fail("invalid_list", "snapshot.members");
  const seenSteps = new Set();
  const seenReceipts = new Set();
  const results = new Map();
  snapshot.members.forEach((entry, index) => {
    const path = `snapshot.members[${index}]`;
    closed(entry, MEMBER_FIELDS, path);
    assertPattern(entry.step_ref, STEP_REF, "invalid_step_ref", `${path}.step_ref`);
    const member = MEMBER_BY_STEP.get(entry.step_ref);
    if (!member) fail("unknown_member_step", { path, step_ref: entry.step_ref });
    if (seenSteps.has(entry.step_ref)) fail("duplicate_member_step", { path, step_ref: entry.step_ref });
    seenSteps.add(entry.step_ref);
    const receiptDigest = digest(entry.receipt);
    if (seenReceipts.has(receiptDigest)) fail("duplicate_member_receipt", path);
    seenReceipts.add(receiptDigest);
    const memberContext = { binding, now, gateZeroMs, path: `${path}.receipt` };
    results.set(entry.step_ref, member.output_schema_ref === BENCHMARK_MANIFEST_SCHEMA
      ? validateBenchmarkMember(entry.receipt, memberContext)
      : validateConsumerGateMember(entry.receipt, member, memberContext));
  });
  const missing = MINIMUM_REQUIRED_MEMBERS.filter(m => !seenSteps.has(m.step_ref)).map(m => m.step_ref);
  if (missing.length > 0) fail("missing_required_member", { missing });

  // INDEPENDENCE. The join's own producer may not have produced or evaluated a
  // member it consumes, the two independent oracles behind two consumed gates
  // may not be one seat wearing two roles, and the human benchmark authority is
  // distinct from the builder, from every reviewer, and from this oracle.
  const benchmark = results.get(BENCHMARK_STEP_REF);
  const reviewers = [];
  for (const [step, result] of results) {
    if (step === BENCHMARK_STEP_REF) continue;
    reviewers.push(result.producer_identity, result.evaluator_identity);
  }
  for (const field of ["producer_identity", "evaluator_identity"]) {
    if (sameSeat(context.subject_maker_identity, context[field])) fail("minimum_self_attestation", { field });
    for (const reviewer of reviewers) {
      if (sameSeat(context[field], reviewer)) fail("minimum_producer_not_independent", { field });
    }
  }
  // Distinctness is over SEATS, on this file's one definition of a seat: two
  // producers collide when they share EITHER the actor or the session. Comparing
  // actor ids alone would admit the exact case the paragraph above forbids —
  // two ids on one session is one seat wearing two roles, and every other
  // independence test in this file already reads it that way.
  const memberProducers = [];
  for (const [step, result] of results) {
    if (step === BENCHMARK_STEP_REF) continue;
    for (const seen of memberProducers) {
      if (sameSeat(seen, result.producer_identity)) fail("member_producers_not_distinct", { step });
    }
    memberProducers.push(result.producer_identity);
  }
  for (const seat of [context.subject_maker_identity, context.producer_identity, context.evaluator_identity, ...reviewers]) {
    if (sameSeat(benchmark.accepted_by_identity, seat)) fail("benchmark_authority_not_independent");
  }

  // The PROPOSED receipt. Exactly the twenty-one r7 fields, and nothing else.
  const proposed = {
    gate_id: MINIMUM_GATE_ID,
    receipt_producer_step_ref: MINIMUM_STEP_REF,
    subject_digest: binding.subject_digest,
    candidate_digest: binding.candidate_digest,
    policy_digest: binding.policy_digest,
    environment_manifest_digest: binding.minimum_environment_manifest_digest,
    subject_environment: MINIMUM_SUBJECT_ENVIRONMENT,
    evidence_scope: MINIMUM_EVIDENCE_SCOPE,
    subject_maker_identity: context.subject_maker_identity,
    producer_identity: context.producer_identity,
    evaluator_identity: context.evaluator_identity,
    producer_role: MINIMUM_PRODUCER_ROLE,
    independent_oracle_ref: MINIMUM_ORACLE_REF,
    oracle_version: MINIMUM_ORACLE_VERSION,
    evidence_ref: context.evidence_ref,
    fixture_set_digest: context.fixture_set_digest,
    observed_at: iso(now),
    // The proposed window, exactly as the projection supplied it. Whether this
    // TTL is acceptable is the ISSUER's question, not this join's: see the
    // binding validation above.
    ttl_expires_at: iso(now + binding.minimum_receipt_ttl_ms),
    status: "pass",
    comparator: context.comparator,
    // Asserted only because this module proved it: see
    // foundationAssuranceMinimumNegativeAdmission, which re-derives every
    // required denial from the live validator before this field is written.
    negative_admission_result: negativeAdmission.result,
  };

  return freeze({
    schema_version: FOUNDATION_ASSURANCE_MINIMUM_SCHEMA,
    gate_id: MINIMUM_GATE_ID,
    producer_step_ref: MINIMUM_STEP_REF,
    producer_role: MINIMUM_PRODUCER_ROLE,
    combiner: MINIMUM_COMBINER,
    obligation_decision_ids: [...MINIMUM_OBLIGATION_DECISION_IDS],
    admissible: true,
    satisfied_gate_ids: MINIMUM_REQUIRED_MEMBERS.map(m => m.gate_id).sort(),
    gate_zero_outcome_digest: gateZero.outcome_digest,
    gate_zero_observed_at: iso(gateZeroMs),
    benchmark_manifest_digest: benchmark.benchmark_manifest_digest,
    benchmark_accepted_at: iso(benchmark.observed_at_ms),
    benchmark_accepted_here: false,
    proposed_receipt: proposed,
    // r7 declares no payload-digest rule for consumer-gate-receipt.v1, so this
    // is a detached local reference for the issuance adapter, not a contract
    // digest. It is deliberately not fed back into the receipt.
    proposed_receipt_reference_digest: digest(proposed),
    receipt_state: "proposed_not_issued",
    issued: false,
    persisted: false,
    durable_receipt_issuance_required: true,
    clock_started: false,
    journey_one_clock_origin_gate_id: JOURNEY_ONE_DEADLINE_CONTRACT.clock_origin_gate_id,
    journey_one_clock_origin_available: false,
    product_activation_authorized: false,
    authority_granted: false,
    negative_admission: negativeAdmission,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// Negative admission, proved rather than asserted.
//
// consumer-gate-receipt.v1 requires negative_admission_result, whose one legal
// value claims that every required denial was observed. A module that simply
// wrote the string would be self-attesting the exact property the field exists
// to prevent, so this file earns it: it builds a synthetic admissible fixture,
// mutates it in each required way, and requires the live validator to refuse
// with the exact expected code. If any case passes, or refuses for the wrong
// reason, the whole join refuses.
//
// The fixture is synthetic evidence about nothing. It binds no real subject,
// names no real session, and produces no effect; the partner slug appears only
// because the verified-partner predicate is identity.js's and cannot be faked.
// ---------------------------------------------------------------------------

const SELF_CHECK_GATE_ZERO_AT = "2026-01-01T00:00:00.000Z";
const SELF_CHECK_BENCHMARK_AT = "2026-01-02T00:00:00.000Z";
const SELF_CHECK_OBSERVED_AT = "2026-01-03T00:00:00.000Z";
const SELF_CHECK_AS_OF = "2026-01-04T00:00:00.000Z";
const DAY_MS = 86400000;

function selfCheckDigest(seed) {
  return `sha256:${digest(["doctorcre:a00-self-check:v1", seed]).slice(7)}`;
}
function selfCheckIdentity(role) {
  return { actor_id: `self-check-${role}`, session_ref: `session:a00-self-check-${role}`, authority_class: "synthetic_self_check" };
}
function selfCheckPayload() {
  return {
    subject_digest: selfCheckDigest("subject"),
    candidate_digest: selfCheckDigest("candidate"),
    policy_digest: selfCheckDigest("policy"),
    capacity_profiles: ["baseline"],
    workload_mix: [{ workload_id: "core", weight_basis_points: WORKLOAD_WEIGHT_TOTAL_BASIS_POINTS, operation_mix_digest: selfCheckDigest("ops") }],
    request_size_distribution: [{ percentile: 50, bytes: 1024 }, { percentile: 95, bytes: 8192 }],
    concurrency_levels: [1],
    arrival_patterns: ["steady"],
    routes: ["/self-check"],
    browsers: [{ name: "self-check", version: "1", build: "1" }],
    runtime_versions: ["self-check-runtime-1"],
    device_profiles: ["self-check-device"],
    hardware_profiles: ["self-check-hardware"],
    network_profiles: ["self-check-network"],
    cache_states: ["cold", "warm"],
    samples_per_cell: MINIMUM_SAMPLES_PER_CELL,
    warmup_runs: MINIMUM_WARMUP_RUNS,
    p95_aggregation_method: P95_AGGREGATION_METHOD,
    outlier_rule: "self-check fixture rule: discard no sample",
    acknowledgement_endpoints: ["/self-check/ack"],
    evaluator_identities: [selfCheckIdentity("evaluator")],
    comparator_versions: ["self-check-comparator-1"],
    slo_thresholds: copy(BENCHMARK_SLO_THRESHOLDS),
    cost_expectation_matrix_digest: selfCheckDigest("cost-matrix"),
    cost_variance_thresholds: copy(BENCHMARK_COST_VARIANCE_THRESHOLDS),
    deadline_contract: copy(BENCHMARK_DEADLINE_CONTRACT),
  };
}
function selfCheckManifest(payload = selfCheckPayload()) {
  // A DEEP copy, so a denial case that mutates a nested constant on the manifest
  // cannot reach back into the payload the binding was derived from.
  return {
    ...copy(payload),
    benchmark_manifest_digest: digest([BENCHMARK_PAYLOAD_DOMAIN_TAG, payload]),
    accepted_by_identity: { actor_id: "joe", session_ref: "session:a00-self-check-authority", authority_class: "verified_partner" },
    accepted_at: SELF_CHECK_BENCHMARK_AT,
    status: "accepted",
  };
}
function selfCheckSnapshot() {
  const payload = selfCheckPayload();
  const manifest = selfCheckManifest(payload);
  const members = MINIMUM_REQUIRED_MEMBERS.map(member => {
    if (member.output_schema_ref === BENCHMARK_MANIFEST_SCHEMA) return { step_ref: member.step_ref, receipt: manifest };
    return {
      step_ref: member.step_ref,
      receipt: {
        gate_id: member.gate_id,
        receipt_producer_step_ref: member.step_ref,
        subject_digest: payload.subject_digest,
        candidate_digest: payload.candidate_digest,
        policy_digest: payload.policy_digest,
        environment_manifest_digest: selfCheckDigest("environment"),
        subject_environment: member.subject_environment,
        evidence_scope: member.evidence_scope,
        subject_maker_identity: selfCheckIdentity("builder"),
        producer_identity: selfCheckIdentity(`producer-${member.producer_role}`),
        evaluator_identity: selfCheckIdentity(`evaluator-${member.producer_role}`),
        producer_role: member.producer_role,
        independent_oracle_ref: member.oracle_ref,
        oracle_version: member.oracle_version,
        evidence_ref: "safe:a00-self-check:member-evidence",
        fixture_set_digest: selfCheckDigest("fixtures"),
        observed_at: SELF_CHECK_OBSERVED_AT,
        ttl_expires_at: iso(Date.parse(SELF_CHECK_OBSERVED_AT) + 7 * DAY_MS),
        status: "pass",
        comparator: "self-check exact comparator",
        negative_admission_result: NEGATIVE_ADMISSION_RESULT,
      },
    };
  });
  return {
    schema_version: FOUNDATION_ASSURANCE_MINIMUM_PROJECTION,
    tenant: ORGANIZATION_TENANT_ID,
    as_of: SELF_CHECK_AS_OF,
    binding: {
      subject_digest: payload.subject_digest,
      candidate_digest: payload.candidate_digest,
      policy_digest: payload.policy_digest,
      minimum_environment_manifest_digest: selfCheckDigest("environment"),
      benchmark_manifest_digest: manifest.benchmark_manifest_digest,
      maximum_member_receipt_ttl_ms: 30 * DAY_MS,
      minimum_receipt_ttl_ms: 7 * DAY_MS,
    },
    gate_zero: {
      step_ref: GATE_ZERO_STEP_REF,
      outcome_digest: selfCheckDigest("gate-zero"),
      observed_at: SELF_CHECK_GATE_ZERO_AT,
    },
    members,
    minimum_receipt_context: {
      subject_maker_identity: selfCheckIdentity("builder"),
      producer_identity: selfCheckIdentity("minimum-oracle"),
      evaluator_identity: selfCheckIdentity("minimum-evaluator"),
      evidence_ref: "safe:a00-self-check:minimum-evidence",
      fixture_set_digest: selfCheckDigest("fixtures"),
      comparator: "self-check exact join comparator",
    },
  };
}

function memberOf(snapshot, stepRef) {
  return snapshot.members.find(entry => entry.step_ref === stepRef);
}
const PHI_STEP = "step:global-no-phi-boundary-independent-receipt";
const SECRETS_STEP = "step:global-secrets-boundary-independent-receipt";

/**
 * The closed set of denials this gate must observe. Each case names the exact
 * refusal code, so a mutation that starts failing EARLIER for an unrelated
 * reason is itself a failure rather than a silent pass.
 */
const REQUIRED_DENIALS = Object.freeze([
  ["missing_required_member", s => { s.members = s.members.filter(m => m.step_ref !== PHI_STEP); }],
  ["missing_required_member", s => { s.members = s.members.filter(m => m.step_ref !== BENCHMARK_STEP_REF); }],
  ["unknown_member_step", s => { s.members.push({ step_ref: "step:portfolio-constitution-semantic-review-receipt", receipt: copy(memberOf(s, PHI_STEP).receipt) }); }],
  ["duplicate_member_step", s => { s.members.push(copy(memberOf(s, PHI_STEP))); }],
  // A REPLAYED receipt: one member's evidence re-presented under another step.
  // r7's denial_rule names this case verbatim, so the set that earns
  // negative_admission_result has to exercise it rather than assert it.
  ["duplicate_member_receipt", s => { memberOf(s, SECRETS_STEP).receipt = copy(memberOf(s, PHI_STEP).receipt); }],
  ["member_receipt_not_current", s => { memberOf(s, PHI_STEP).receipt.ttl_expires_at = "2026-01-03T12:00:00.000Z"; }],
  // An OVERLONG window — long enough that the currentness check below would have
  // passed it. The policy check is what refuses it, and it is the other denial
  // category r7's denial_rule names.
  ["member_receipt_ttl_policy_exceeded", s => { memberOf(s, PHI_STEP).receipt.ttl_expires_at = iso(Date.parse(SELF_CHECK_OBSERVED_AT) + 400 * DAY_MS); }],
  ["member_receipt_window_invalid", s => { memberOf(s, PHI_STEP).receipt.ttl_expires_at = SELF_CHECK_OBSERVED_AT; }],
  ["member_receipt_observed_after_reference", s => { memberOf(s, PHI_STEP).receipt.observed_at = "2026-01-05T00:00:00.000Z"; }],
  ["member_observed_before_gate_zero", s => { memberOf(s, PHI_STEP).receipt.observed_at = "2025-12-31T00:00:00.000Z"; }],
  // The BOUNDARY instant itself: "strictly after Gate Zero" is exclusive for a
  // member exactly as it is for the acceptance, so the equal case is a denial
  // and not an admission.
  ["member_observed_before_gate_zero", s => { memberOf(s, PHI_STEP).receipt.observed_at = SELF_CHECK_GATE_ZERO_AT; }],
  ["member_binding_mismatch", s => { memberOf(s, PHI_STEP).receipt.candidate_digest = selfCheckDigest("other-candidate"); }],
  ["member_scope_mismatch", s => { memberOf(s, PHI_STEP).receipt.evidence_scope = "production"; }],
  ["member_scope_mismatch", s => { memberOf(s, PHI_STEP).receipt.subject_environment = "production"; }],
  ["member_role_mismatch", s => { memberOf(s, PHI_STEP).receipt.producer_role = "independent_secret_boundary_oracle"; }],
  ["member_oracle_mismatch", s => { memberOf(s, PHI_STEP).receipt.oracle_version = "2.0.0"; }],
  ["member_not_passing", s => { memberOf(s, PHI_STEP).receipt.status = "fail"; }],
  ["member_self_attestation", s => { memberOf(s, PHI_STEP).receipt.producer_identity = copy(memberOf(s, PHI_STEP).receipt.subject_maker_identity); }],
  ["member_producers_not_distinct", s => { memberOf(s, SECRETS_STEP).receipt.producer_identity = copy(memberOf(s, PHI_STEP).receipt.producer_identity); }],
  // The SAME SEAT under two actor ids. Distinctness is over seats, so a shared
  // session is a collision even when the ids differ; a set that only ever
  // exercised the identical-identity case would leave the weaker comparison
  // looking proved.
  ["member_producers_not_distinct", s => { memberOf(s, SECRETS_STEP).receipt.producer_identity.session_ref = memberOf(s, PHI_STEP).receipt.producer_identity.session_ref; }],
  ["closed_shape", s => { delete memberOf(s, PHI_STEP).receipt.comparator; }],
  ["closed_shape", s => { memberOf(s, PHI_STEP).receipt.obligation_decision_ids = ["Q012.D2"]; }],
  ["closed_shape", s => { delete memberOf(s, BENCHMARK_STEP_REF).receipt.outlier_rule; }],
  ["closed_shape", s => { memberOf(s, BENCHMARK_STEP_REF).receipt.extra_workload_note = "x"; }],
  ["benchmark_manifest_digest_mismatch", s => { memberOf(s, BENCHMARK_STEP_REF).receipt.routes = ["/mutated"]; }],
  ["benchmark_acceptance_mismatched", s => {
    const payload = { ...selfCheckPayload(), routes: ["/replacement"] };
    memberOf(s, BENCHMARK_STEP_REF).receipt = selfCheckManifest(payload);
  }],
  ["benchmark_accepted_before_gate_zero", s => { memberOf(s, BENCHMARK_STEP_REF).receipt.accepted_at = SELF_CHECK_GATE_ZERO_AT; }],
  ["benchmark_status_not_accepted", s => { memberOf(s, BENCHMARK_STEP_REF).receipt.status = "pass"; }],
  ["verified_partner_required", s => { memberOf(s, BENCHMARK_STEP_REF).receipt.accepted_by_identity.authority_class = "sponsored_agent"; }],
  ["benchmark_authority_not_independent", s => { s.minimum_receipt_context.producer_identity = copy(memberOf(s, BENCHMARK_STEP_REF).receipt.accepted_by_identity); }],
  ["minimum_producer_not_independent", s => { s.minimum_receipt_context.producer_identity = copy(memberOf(s, PHI_STEP).receipt.producer_identity); }],
  ["minimum_self_attestation", s => { s.minimum_receipt_context.evaluator_identity = copy(s.minimum_receipt_context.subject_maker_identity); }],
  ["benchmark_weight_total_mismatch", s => { memberOf(s, BENCHMARK_STEP_REF).receipt.workload_mix[0].weight_basis_points = 9999; rebindBenchmark(s); }],
  ["benchmark_constant_mismatch", s => { memberOf(s, BENCHMARK_STEP_REF).receipt.slo_thresholds.cold_lcp_p95_ms = 8000; rebindBenchmark(s); }],
  ["benchmark_constant_mismatch", s => { memberOf(s, BENCHMARK_STEP_REF).receipt.deadline_contract.calendar_days = 45; rebindBenchmark(s); }],
  ["gate_zero_observed_after_reference", s => { s.gate_zero.observed_at = "2026-01-05T00:00:00.000Z"; }],
  ["wrong_gate_zero_step", s => { s.gate_zero.step_ref = "step:foundation-assurance-minimum-receipt"; }],
  ["wrong_tenant", s => { s.tenant = "other-tenant"; }],
]);

/**
 * Re-seal a deliberately mutated manifest so the case under test is the one
 * intended: without this, every payload mutation would stop at the digest
 * mismatch and the weight and constant rules would never be reached.
 */
function rebindBenchmark(snapshot) {
  const entry = memberOf(snapshot, BENCHMARK_STEP_REF);
  const payload = {};
  for (const field of BENCHMARK_PAYLOAD_FIELDS) payload[field] = entry.receipt[field];
  entry.receipt.benchmark_manifest_digest = digest([BENCHMARK_PAYLOAD_DOMAIN_TAG, payload]);
  snapshot.binding.benchmark_manifest_digest = entry.receipt.benchmark_manifest_digest;
}

let cachedNegativeAdmission = null;

/**
 * Run the closed denial set against the live validator. Pure, deterministic, and
 * memoized after the first success. Throws `negative_admission_incomplete` when
 * any required denial is not observed exactly.
 */
export function foundationAssuranceMinimumNegativeAdmission() {
  if (cachedNegativeAdmission) return cachedNegativeAdmission;
  const asserted = { result: NEGATIVE_ADMISSION_RESULT, case_count: REQUIRED_DENIALS.length, observed_codes: [] };
  // The admissible control first: a denial set proves nothing if the unmutated
  // fixture does not actually pass.
  joinCore(selfCheckSnapshot(), asserted);
  const observed = [];
  REQUIRED_DENIALS.forEach(([code, mutate], index) => {
    const snapshot = selfCheckSnapshot();
    mutate(snapshot);
    let refused = null;
    try { joinCore(snapshot, asserted); }
    catch (error) { refused = error instanceof BenchmarkMinimumError ? error.code : `unexpected:${error?.message}`; }
    if (refused !== code) fail("negative_admission_incomplete", { case_index: index, expected: code, observed: refused });
    observed.push(code);
  });
  cachedNegativeAdmission = freeze({
    result: NEGATIVE_ADMISSION_RESULT,
    case_count: REQUIRED_DENIALS.length,
    observed_codes: [...new Set(observed)].sort(),
  });
  return cachedNegativeAdmission;
}

/**
 * Install the trusted authenticating dependency and get the join.
 *
 * `authenticateEvidence` is server code, never a tool argument. It receives the
 * raw envelope and must return { envelope_digest, snapshot }: the digest binds
 * the authentication to the exact bytes supplied, and the snapshot is the
 * projection it authenticated — raw artifacts, live identities, their
 * currentness, and the exact accepted benchmark envelope. A projection is
 * evidence that the verifier read the record; it is never evidence that a raw
 * producer admitted anything.
 */
export function createFoundationAssuranceMinimumGate({ authenticateEvidence } = {}) {
  if (typeof authenticateEvidence !== "function") fail("authenticated_verifier_required");
  return Object.freeze({
    evaluate(envelope) {
      assertJsonSafe(envelope, "envelope");
      // A PLAIN JSON OBJECT, before anything is hashed. artifact-trust.js hashes
      // a top-level string as its own raw bytes, so the string `{"a":1}` and the
      // object {a:1} would canonicalize to one envelope_digest — the exact
      // "two values canonicalize identically" collision assertJsonSafe exists to
      // prevent, which it cannot catch because a top-level string is JSON-safe.
      // The binding below is only as strong as the injectivity of this digest.
      if (!envelope || typeof envelope !== "object" || Array.isArray(envelope)) fail("invalid_object", "envelope");
      const input = freeze(copy(envelope));
      const verified = authenticateEvidence(input);
      assertJsonSafe(verified, "verification");
      closed(verified, ["envelope_digest", "snapshot"], "verification");
      if (verified.envelope_digest !== digest(input)) fail("verification_binding_mismatch");
      return joinCore(copy(verified.snapshot), foundationAssuranceMinimumNegativeAdmission());
    },
  });
}

/**
 * The M01 seam, and the only shape in this file that is not r7-exact. It adapts
 * the FIELD SET — r7's consumer-gate-receipt.v1 is closed and declares no
 * schema_version; journey-one-clock.v5.js requires one on the same receipt — and
 * it CHECKS, without adapting, the two VALUE DOMAINS where M01 is narrower than
 * r7. All three divergences are enumerated under THE M01 SEAM in the header.
 *
 * A domain mismatch REFUSES here with a code that names the seam. It is never
 * repaired: truncating a reference to 300 characters produces a different
 * reference, and rounding an instant to milliseconds produces a different
 * instant, so either "fix" would hand M01 evidence that no producer issued. A
 * receipt this function refuses is still valid r7 evidence — the refusal says
 * M01 cannot read it, not that it is malformed.
 *
 * It converts no deadline contract. The manifest's deadline_contract is in DAYS
 * and M01's projection field is compared against the HOURS variant; building
 * that projection is integration work and is not done implicitly here.
 */
export function journeyOneClockMinimumReceiptView(receipt) {
  // Before closed(), because a non-enumerable or accessor property would slip
  // past the closed key count and then be dropped silently by copy().
  assertJsonSafe(receipt, "receipt");
  closed(receipt, CONSUMER_GATE_RECEIPT_FIELDS, "receipt");

  const withinM01 = (value, pattern) => typeof value === "string" && pattern.test(value);

  if (!withinM01(receipt.evidence_ref, M01_REFERENCE)) {
    fail("m01_incompatible_evidence_ref", { path: "receipt.evidence_ref", m01_maximum_length: 300 });
  }
  // A session_ref admitted here is already within M01's length domain by
  // construction (SESSION_REF caps it at 208 characters), so this is a
  // belt-and-braces assertion at the seam rather than a live divergence. It is
  // written out so a later widening of either pattern surfaces here.
  for (const field of ["subject_maker_identity", "producer_identity", "evaluator_identity"]) {
    const seat = receipt[field];
    if (!seat || typeof seat !== "object" || !withinM01(seat.session_ref, M01_REFERENCE)) {
      fail("m01_incompatible_session_ref", { path: `receipt.${field}.session_ref`, m01_maximum_length: 300 });
    }
  }
  for (const field of ["observed_at", "ttl_expires_at"]) {
    if (!withinM01(receipt[field], M01_INSTANT)) {
      fail("m01_incompatible_timestamp", { path: `receipt.${field}`, m01_maximum_fractional_digits: 3 });
    }
  }

  return freeze({ schema_version: CONSUMER_GATE_RECEIPT_SCHEMA, ...copy(receipt) });
}
