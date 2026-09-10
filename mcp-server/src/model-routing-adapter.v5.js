// DoctorCRE v5 slice V5-F04: the replaceable model-adapter boundary
// (decisions Q130.D1, Q047.D1, Q106.D1, Q031.D1).
//
// AN ADAPTER IS A DESCRIPTOR, NOT A CALLBACK. Registering one hands this module
// a closed, hashed record of which backend it speaks for and under what
// transport class — nothing executable. There is no function slot, no command
// string, no URL, no shell and no socket anywhere in this file, so replacing an
// adapter cannot introduce a new way to reach the outside world. That is what
// makes "Hermes is a replaceable backend platform" checkable rather than
// aspirational: the thing being replaced is a validated description.
//
// THE THREE THINGS AN ADAPTER MAY DO:
//   1. Produce a VALIDATED INVOCATION PROPOSAL from an authenticated bound
//      envelope and a validated prompt payload.
//   2. VALIDATE A BOUND RESPONSE that claims to answer one such proposal.
//   3. Report its own contract, so two adapters can be compared field by field.
//
// AND THE ONE IT MAY NOT. `dispatchInvocation` always throws. Actual dispatch is
// V5-F06/V5-F07 work and no provider call exists here; the function is present
// precisely so the gap is a named, tested, fail-closed refusal instead of an
// absence someone later fills with an ad-hoc call.
//
// WHAT THE BOUNDARY ENFORCES, and why each one is a real bypass:
//   * An UNAUTHENTICATED route is refused. A schema-valid routing PROPOSAL is
//     not a qualification, and the proposal path exists so that distinction is
//     visible; letting one through here would erase it.
//   * AN ENVELOPE OR PROMPT THIS PROCESS DID NOT PRODUCE is refused. Membership
//     is asked of the routing module by object identity — `isRouteBoundEnvelope`
//     and `isValidatedPromptPayload` — because every preimage in that module is
//     public and `digest` is exported: `{ ...boundEnvelope, route: cheaper }`
//     re-hashes perfectly and is still not the object the router built. The
//     digest checks stay beside the identity checks; neither replaces the other,
//     and the identity check is process-local only (see the routing module's
//     header and its `unimplemented_dependencies`).
//   * AN INVOCATION PROPOSAL OR A VALIDATED RESPONSE THIS MODULE DID NOT PRODUCE
//     is refused, for exactly the same reason and by the same mechanism. This
//     file's own preimages are public too, so a hand-built proposal carrying a
//     correct `proposal_digest` over its own public fields can still describe an
//     envelope and a prompt that never existed; the two registers below are what
//     tell it apart from one `buildInvocationProposal` built. WHAT "VALIDATED"
//     MEANS ON A RESPONSE, exactly: this module checked the response CONTRACT
//     against an AUTHENTIC proposal in this process. It is not a claim that any
//     backend produced it — none is reachable from here — and the model's text
//     remains untrusted content that no check in this file can vouch for.
//   * A BACKEND MISMATCH is refused: an adapter for one backend may not carry an
//     invocation routed to another.
//   * NO ADAPTER MINTS PERMISSIONS. `capability_grants` must be empty and
//     `grants_authority` must be literally false; the public product identity is
//     a constant an adapter cannot set.
//   * A RESPONSE THAT SUBSTITUTES ITS OWN MODEL, VERSION, EFFORT OR BACKEND is
//     refused. This is the silent downgrade caught at the other end of the wire:
//     routing can refuse a weaker route, but only the response boundary can
//     catch a backend that answered with one anyway.
//   * MODEL CONTENT IS NOT A COMMAND. A response carries advisory text and typed
//     proposals; a field whose name reaches into execution, capability or
//     authority is refused by name before the closed-key check.
//   * REPLAY AND DRIFT. The ledger refuses a second invocation of one id and a
//     second response for one invocation, and every response must echo the
//     envelope binding digest, the context digest and the receipt binding it was
//     issued against.
//
// FIXTURES ARE FIXTURES. Nothing here can record that a backend was verified
// live: a descriptor field claiming a verified API, a verified Mac Studio or a
// production check is refused by name. Every adapter this module can produce
// reports `live_backend_verified: false`.
//
// The module is pure: no filesystem, network, database, environment or clock.
// The ledger below is an explicitly process-local guard, not a record store.

import { digest } from "./artifact-trust.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import { V5_NO_EFFECTS, V5_CANONICAL_AUTHORITY } from "./global-boundaries.v5.js";
import {
  V5_PUBLIC_PRODUCT_IDENTITY,
  V5_JOB_ENVELOPE_SCHEMA_VERSION,
  V5_PROMPT_PAYLOAD_SCHEMA_VERSION,
  V5_PROVENANCE_SCOPE,
  isRouteBoundEnvelope,
  isValidatedPromptPayload,
} from "./model-routing.v5.js";

export const V5_ADAPTER_SCHEMA_VERSION = "model-routing-adapter.v1";
export const V5_INVOCATION_PROPOSAL_SCHEMA_VERSION = "model-invocation-proposal.v1";
export const V5_MODEL_RESPONSE_SCHEMA_VERSION = "model-response.v1";

/**
 * Transport classes, closed. Every one of them is unimplemented for dispatch;
 * the class says which future adapter would own the call, not that a call
 * exists.
 */
export const V5_ADAPTER_TRANSPORT_CLASSES = Object.freeze([
  "local_node_worker", "hosted_open_model_platform", "cloud_model_gateway",
]);

/** The V5-F06/V5-F07 seam, named once so every result can point at it. */
export const V5_DISPATCH_SEAM = "unimplemented_pending_v5_f06_f07_execution_adapters";

const REF = /^[a-z0-9][a-z0-9_.:/-]{0,127}$/;
const SHA256_REF = /^sha256:[0-9a-f]{64}$/;
const ISO_INSTANT =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|([+-])(\d{2}):(\d{2}))$/;

// ---------------------------------------------------------------------------
// Local provenance registers.
//
// The same mechanism the routing module uses for envelopes and prompt payloads,
// applied to the two products this file creates. Membership is by OBJECT
// IDENTITY of the frozen product, recorded at the moment it is built, so nothing
// a caller can construct, spread, parse or re-hash is a member.
//
// The scope of the claim is identical and no larger: "this object was produced
// by this module in this process". It is NOT durable, NOT transferable, and NOT
// a statement about a backend, a wire or a stored row — see
// `v5ModelRoutingAdapterProjection().unimplemented_dependencies`.
// ---------------------------------------------------------------------------

const AUTHENTIC_INVOCATION_PROPOSALS = new WeakSet();
const VALIDATED_MODEL_RESPONSES = new WeakSet();

/** True only for a proposal `buildInvocationProposal` produced in this process. */
export function isInvocationProposal(value) {
  return typeof value === "object" && value !== null && AUTHENTIC_INVOCATION_PROPOSALS.has(value);
}

/** True only for a response `validateBoundResponse` produced in this process. */
export function isValidatedModelResponse(value) {
  return typeof value === "object" && value !== null && VALIDATED_MODEL_RESPONSES.has(value);
}

export class V5AdapterError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5AdapterError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) {
  throw new V5AdapterError(code, message, detail);
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (isPlainObject(value)) { Object.values(value).forEach(deepFreeze); return Object.freeze(value); }
  return value;
}

function assertObject(value, path) {
  if (!isPlainObject(value)) fail("invalid_shape", `${path} must be a plain object`, { path });
  return value;
}

function assertClosedKeys(object, allowed, path) {
  for (const key of Object.keys(object)) {
    if (!allowed.includes(key)) {
      fail("unknown_field", `unknown field "${key}" at ${path}`, { path: `${path}.${key}`, key });
    }
  }
}

function assertRequiredKeys(object, required, path) {
  for (const key of required) {
    if (!(key in object)) fail("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
  }
}

function assertRef(value, path) {
  if (typeof value !== "string" || !REF.test(value)) {
    fail("invalid_shape", `${path} must be a lower-case reference token`, { path, value });
  }
  return value;
}

function assertSha256Ref(value, path) {
  if (typeof value !== "string" || !SHA256_REF.test(value)) {
    fail("invalid_digest", `${path} must be a "sha256:" reference`, { path });
  }
  return value;
}

function daysInMonth(year, month) {
  if (month === 2) return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0 ? 29 : 28;
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

/** Same calendar-literal parse as the routing module; see its note on Date.parse. */
function assertInstant(value, path) {
  const match = typeof value === "string" ? ISO_INSTANT.exec(value) : null;
  if (!match) {
    fail("invalid_timestamp", `${path} must be an ISO-8601 instant with an explicit offset`, { path, value });
  }
  const [, year, month, day, hour, minute, second, , offsetHour, offsetMinute] = match;
  if (Number(month) < 1 || Number(month) > 12 || Number(day) < 1 ||
      Number(day) > daysInMonth(Number(year), Number(month)) ||
      Number(hour) > 23 || Number(minute) > 59 || Number(second) > 59 ||
      (offsetHour !== undefined && (Number(offsetHour) > 23 || Number(offsetMinute) > 59))) {
    fail("invalid_timestamp", `${path} names an instant that does not exist on the calendar`, { path, value });
  }
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) fail("invalid_timestamp", `${path} is not a readable instant`, { path, value });
  return parsed;
}

// ---------------------------------------------------------------------------
// Adapter registration.
// ---------------------------------------------------------------------------

const ADAPTER_KEYS = Object.freeze([
  "adapter_id", "adapter_version", "backend_key", "transport_class",
  "capability_grants", "user_facing", "grants_authority", "product_identity",
]);

// A descriptor asserting that a real backend was checked is refused BY NAME.
// Nothing in this repository can verify a provider API or a Mac Studio, so a
// field that records one would be a claim no code could ever substantiate.
const UNVERIFIABLE_CLAIM_FIELDS = Object.freeze([
  "verified", "live_verified", "live_backend_verified", "production_verified",
  "api_verified", "provider_verified", "mac_studio_verified", "hardware_verified",
  "benchmarked", "certified",
]);

function refuseUnverifiableClaims(object, path) {
  const found = Object.keys(object).filter(key => UNVERIFIABLE_CLAIM_FIELDS.includes(key)).sort();
  if (found.length > 0) {
    fail("unverifiable_backend_claim_refused",
      "nothing in this slice can verify a live backend, so no descriptor may record that one was verified",
      { path, fields: found });
  }
}

/**
 * Validate and seal one adapter descriptor.
 *
 * Two adapters that describe the same contract for the same backend produce the
 * same `contract_digest` while keeping distinct `adapter_digest`s — that pair is
 * what a Q130 replacement is checked against.
 */
export function registerAdapter(descriptor) {
  assertObject(descriptor, "adapter");
  refuseUnverifiableClaims(descriptor, "adapter");
  assertClosedKeys(descriptor, ADAPTER_KEYS, "adapter");
  assertRequiredKeys(descriptor, ADAPTER_KEYS, "adapter");
  assertRef(descriptor.adapter_id, "adapter.adapter_id");
  assertRef(descriptor.adapter_version, "adapter.adapter_version");
  assertRef(descriptor.backend_key, "adapter.backend_key");
  if (!V5_ADAPTER_TRANSPORT_CLASSES.includes(descriptor.transport_class)) {
    fail("unknown_transport_class", `"${descriptor.transport_class}" is not a registered transport class`,
      { registered: [...V5_ADAPTER_TRANSPORT_CLASSES] });
  }
  if (!Array.isArray(descriptor.capability_grants) || descriptor.capability_grants.length > 0) {
    fail("adapter_capability_grant_refused",
      "an adapter mints no permissions; capability_grants must be an empty list",
      { path: "adapter.capability_grants" });
  }
  if (descriptor.grants_authority !== false) {
    fail("adapter_authority_mint_refused",
      "an adapter never grants authority; the canonical record layer holds it",
      { path: "adapter.grants_authority" });
  }
  if (descriptor.user_facing !== false) {
    fail("adapter_user_facing_refused",
      "no backend adapter is user-facing; the product surface is DoctorCRE",
      { path: "adapter.user_facing" });
  }
  if (descriptor.product_identity !== V5_PUBLIC_PRODUCT_IDENTITY) {
    fail("product_identity_change_refused",
      "an adapter cannot change the public product identity",
      { expected: V5_PUBLIC_PRODUCT_IDENTITY, actual: descriptor.product_identity });
  }
  // The contract is what a replacement must preserve; the identity is what
  // changes when one adapter is swapped for another.
  const contract = {
    schema_version: V5_ADAPTER_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    backend_key: descriptor.backend_key,
    transport_class: descriptor.transport_class,
    capability_grants: [],
    user_facing: false,
    grants_authority: false,
    product_identity: V5_PUBLIC_PRODUCT_IDENTITY,
    dispatch_implemented: false,
  };
  const identity = { ...contract, adapter_id: descriptor.adapter_id, adapter_version: descriptor.adapter_version };
  return deepFreeze({
    ...identity,
    contract_digest: digest(contract),
    adapter_digest: digest(identity),
    live_backend_verified: false,
    canonical_authority: V5_CANONICAL_AUTHORITY,
    dispatch_seam: V5_DISPATCH_SEAM,
    effects: V5_NO_EFFECTS,
  });
}

function assertSealedAdapter(adapter, path = "adapter") {
  assertObject(adapter, path);
  if (adapter.schema_version !== V5_ADAPTER_SCHEMA_VERSION) {
    fail("adapter_schema_version_invalid", `${path}.schema_version must be ${V5_ADAPTER_SCHEMA_VERSION}`, { path });
  }
  const resealed = registerAdapter({
    adapter_id: adapter.adapter_id, adapter_version: adapter.adapter_version,
    backend_key: adapter.backend_key, transport_class: adapter.transport_class,
    capability_grants: adapter.capability_grants, user_facing: adapter.user_facing,
    grants_authority: adapter.grants_authority, product_identity: adapter.product_identity,
  });
  if (resealed.adapter_digest !== adapter.adapter_digest) {
    fail("adapter_digest_mismatch", `${path} does not hash to its own adapter_digest`,
      { expected: resealed.adapter_digest, actual: adapter.adapter_digest });
  }
  return resealed;
}

// ---------------------------------------------------------------------------
// The invocation proposal.
// ---------------------------------------------------------------------------

function assertBoundEnvelope(envelope, path = "bound_envelope") {
  assertObject(envelope, path);
  if (envelope.schema_version !== V5_JOB_ENVELOPE_SCHEMA_VERSION) {
    fail("envelope_schema_version_invalid", `${path}.schema_version must be ${V5_JOB_ENVELOPE_SCHEMA_VERSION}`,
      { path });
  }
  if (envelope.qualification_authenticated !== true) {
    fail("unauthenticated_route_refused",
      "the adapter boundary accepts only an envelope bound to an authenticated qualified route",
      { path });
  }
  // The flag above is a claim; this is the check. Only the object
  // `bindEnvelopeToRoute` produced is accepted, so a spread copy carrying an
  // edited route — which re-hashes to the identical binding_digest, because the
  // route is deliberately outside that preimage — is refused here.
  if (!isRouteBoundEnvelope(envelope)) {
    fail("bound_envelope_not_locally_produced",
      `${path} was not produced by bindEnvelopeToRoute in this process; a matching shape and a matching digest are not provenance`,
      { path, provenance_scope: V5_PROVENANCE_SCOPE });
  }
  assertObject(envelope.route, `${path}.route`);
  assertSha256Ref(envelope.binding_digest, `${path}.binding_digest`);
  assertSha256Ref(envelope.context_digest, `${path}.context_digest`);
  assertRef(envelope.receipt_binding_ref, `${path}.receipt_binding_ref`);
  if (envelope.public_product_identity !== V5_PUBLIC_PRODUCT_IDENTITY) {
    fail("product_identity_change_refused", `${path} does not carry the public product identity`,
      { expected: V5_PUBLIC_PRODUCT_IDENTITY, actual: envelope.public_product_identity });
  }
  // Recomputed rather than trusted: the binding digest is the one value every
  // equivalence claim below rests on.
  const recomputed = digest({
    schema_version: envelope.schema_version,
    tenant: envelope.tenant,
    public_product_identity: envelope.public_product_identity,
    role: envelope.role,
    job: envelope.job,
    job_digest: envelope.job_digest,
    context_digest: envelope.context_digest,
    capability_refs: envelope.capability_refs,
    receipt_binding_ref: envelope.receipt_binding_ref,
  });
  if (recomputed !== envelope.binding_digest) {
    fail("envelope_binding_digest_mismatch", `${path} does not hash to its own binding_digest`,
      { expected: recomputed, actual: envelope.binding_digest });
  }
  return envelope;
}

function assertPromptPayload(payload, envelope, path = "prompt_payload") {
  assertObject(payload, path);
  if (payload.schema_version !== V5_PROMPT_PAYLOAD_SCHEMA_VERSION) {
    fail("prompt_payload_schema_version_invalid",
      `${path}.schema_version must be ${V5_PROMPT_PAYLOAD_SCHEMA_VERSION}`, { path });
  }
  // Same reasoning as the envelope: the payload preimage is public, so anyone
  // can hash arbitrary parts into a well-formed payload. Only the object
  // `buildPromptPayload` produced has actually been through that function's
  // data-class admission and its secret- and authority-field refusals.
  if (!isValidatedPromptPayload(payload)) {
    fail("prompt_payload_not_locally_produced",
      `${path} was not produced by buildPromptPayload in this process, so nothing here has checked its parts against the job, the task class or the backend's data policy`,
      { path, provenance_scope: V5_PROVENANCE_SCOPE });
  }
  assertSha256Ref(payload.payload_digest, `${path}.payload_digest`);
  // One policy across the decision, the envelope and the prompt.
  if (payload.policy_digest !== envelope.policy_digest) {
    fail("prompt_payload_policy_mismatch",
      `${path} was admitted under a different policy than the one the bound route was decided under`,
      { path, expected: envelope.policy_digest, actual: payload.policy_digest });
  }
  if (payload.envelope_binding_digest !== envelope.binding_digest) {
    fail("prompt_payload_envelope_mismatch",
      `${path} was built for a different envelope`,
      { expected: envelope.binding_digest, actual: payload.envelope_binding_digest });
  }
  const recomputed = digest({
    schema_version: payload.schema_version,
    envelope_binding_digest: payload.envelope_binding_digest,
    parts: payload.parts,
  });
  if (recomputed !== payload.payload_digest) {
    fail("prompt_payload_digest_mismatch", `${path} does not hash to its own payload_digest`,
      { expected: recomputed, actual: payload.payload_digest });
  }
  return payload;
}

const PROPOSAL_ARG_KEYS = Object.freeze([
  "adapter", "bound_envelope", "prompt_payload", "invocation_id", "now",
]);

/**
 * Produce the validated invocation proposal for one adapter.
 *
 * NOTHING IS SENT. The proposal describes what WOULD be asked of the occupant if
 * a dispatch adapter existed, and says on its own face that none does.
 */
export function buildInvocationProposal(args) {
  assertObject(args, "args");
  assertClosedKeys(args, PROPOSAL_ARG_KEYS, "args");
  assertRequiredKeys(args, PROPOSAL_ARG_KEYS, "args");
  const adapter = assertSealedAdapter(args.adapter);
  const envelope = assertBoundEnvelope(args.bound_envelope);
  const payload = assertPromptPayload(args.prompt_payload, envelope);
  assertRef(args.invocation_id, "args.invocation_id");
  assertInstant(args.now, "args.now");
  if (adapter.backend_key !== envelope.route.backend_key) {
    fail("adapter_backend_mismatch",
      "this adapter speaks for a different backend than the bound route names",
      { adapter_backend_key: adapter.backend_key, route_backend_key: envelope.route.backend_key });
  }

  const preimage = {
    schema_version: V5_INVOCATION_PROPOSAL_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    invocation_id: args.invocation_id,
    proposed_at: args.now,
    adapter_id: adapter.adapter_id,
    adapter_version: adapter.adapter_version,
    adapter_contract_digest: adapter.contract_digest,
    // The bindings a Q130 replacement must preserve: job, context, capability
    // and receipt. They come from the envelope and this file cannot compute a
    // different value for any of them. The capability set itself rides inside
    // envelope_binding_digest and is deliberately not carried to the backend —
    // the occupant is told what to work on, never what it may do.
    envelope_binding_digest: envelope.binding_digest,
    job_id: envelope.job.job_id,
    role_digest: envelope.role.role_digest,
    context_digest: envelope.context_digest,
    receipt_binding_ref: envelope.receipt_binding_ref,
    // The occupant, which is the part a replacement is allowed to change.
    route: {
      route_key: envelope.route.route_key,
      backend_key: envelope.route.backend_key,
      model_key: envelope.route.model_key,
      model_version: envelope.route.model_version,
      effort: envelope.route.effort,
    },
    prompt_payload_digest: payload.payload_digest,
  };
  const proposal = deepFreeze({
    ...preimage,
    proposal_digest: digest(preimage),
    dispatch: V5_DISPATCH_SEAM,
    dispatched: false,
    dispatch_implemented: false,
    live_backend_verified: false,
    grants_authority: false,
    product_identity: V5_PUBLIC_PRODUCT_IDENTITY,
    effects: V5_NO_EFFECTS,
  });
  AUTHENTIC_INVOCATION_PROPOSALS.add(proposal);
  return proposal;
}

function assertProposal(proposal, path = "proposal") {
  assertObject(proposal, path);
  if (proposal.schema_version !== V5_INVOCATION_PROPOSAL_SCHEMA_VERSION) {
    fail("proposal_schema_version_invalid",
      `${path}.schema_version must be ${V5_INVOCATION_PROPOSAL_SCHEMA_VERSION}`, { path });
  }
  const recomputed = digest({
    schema_version: proposal.schema_version,
    tenant: proposal.tenant,
    invocation_id: proposal.invocation_id,
    proposed_at: proposal.proposed_at,
    adapter_id: proposal.adapter_id,
    adapter_version: proposal.adapter_version,
    adapter_contract_digest: proposal.adapter_contract_digest,
    envelope_binding_digest: proposal.envelope_binding_digest,
    job_id: proposal.job_id,
    role_digest: proposal.role_digest,
    context_digest: proposal.context_digest,
    receipt_binding_ref: proposal.receipt_binding_ref,
    route: proposal.route,
    prompt_payload_digest: proposal.prompt_payload_digest,
  });
  if (recomputed !== proposal.proposal_digest) {
    fail("proposal_digest_mismatch", `${path} does not hash to its own proposal_digest`,
      { expected: recomputed, actual: proposal.proposal_digest });
  }
  // These three ride OUTSIDE the digest, so they are checked rather than sealed:
  // a proposal that renames the product, claims a grant, or claims it was
  // dispatched is refused wherever it is read, not only where it was built.
  if (proposal.product_identity !== V5_PUBLIC_PRODUCT_IDENTITY) {
    fail("product_identity_change_refused", `${path} does not carry the public product identity`,
      { expected: V5_PUBLIC_PRODUCT_IDENTITY, actual: proposal.product_identity });
  }
  if (proposal.grants_authority !== false) {
    fail("adapter_authority_mint_refused", `${path} claims to grant authority`, { path });
  }
  if (proposal.dispatched !== false || proposal.dispatch_implemented !== false) {
    fail("dispatch_claim_refused",
      `${path} claims a dispatch this slice cannot perform`, { path, seam: V5_DISPATCH_SEAM });
  }
  // PROVENANCE LAST, so the checks above still name what is actually wrong with
  // an edited or over-claiming object. Everything above is tamper-EVIDENCE over
  // a public preimage: a hand-built proposal naming an envelope and a prompt
  // that never existed can satisfy every one of them, because `digest` is
  // exported and every field of the preimage above is public. Only this refuses
  // it. The check is process-local and claims nothing more; a proposal that
  // crossed a serialization boundary is refused here rather than re-admitted on
  // its digest.
  if (!isInvocationProposal(proposal)) {
    fail("proposal_not_locally_produced",
      `${path} was not produced by buildInvocationProposal in this process, so no bound envelope and no validated prompt payload were ever checked behind it; a matching shape and a correct digest are not provenance`,
      { path, provenance_scope: V5_PROVENANCE_SCOPE });
  }
  return proposal;
}

/**
 * The fail-closed dispatch seam.
 *
 * This slice contains no provider client, no network code and no execution path,
 * and it deliberately does not gain one through a caller-supplied callback. The
 * function exists so that "dispatch is not implemented" is an explicit refusal
 * with a name, tested like any other refusal, rather than a hole.
 */
export function dispatchInvocation(proposal) {
  if (proposal !== undefined) assertProposal(proposal);
  fail("dispatch_not_implemented",
    "model dispatch is V5-F06/V5-F07 work; this boundary proposes and validates only",
    { seam: V5_DISPATCH_SEAM });
}

// ---------------------------------------------------------------------------
// The bound response.
// ---------------------------------------------------------------------------

const RESPONSE_KEYS = Object.freeze([
  "invocation_id", "adapter_id", "backend_key", "model_key", "model_version", "effort",
  "produced_at", "envelope_binding_digest", "context_digest", "receipt_binding_ref",
  "proposal_digest", "content", "typed_proposals", "finish_reason",
]);
const RESPONSE_REQUIRED = Object.freeze([
  "invocation_id", "adapter_id", "backend_key", "model_key", "model_version", "effort",
  "produced_at", "envelope_binding_digest", "context_digest", "receipt_binding_ref",
  "proposal_digest", "content", "finish_reason",
]);
const TYPED_PROPOSAL_KEYS = Object.freeze(["proposal_id", "uncertainty_class", "label", "note"]);

/**
 * The typed-uncertainty vocabulary, mirroring the one the V5-F03 execution
 * contract already accepts for model judgment steps. Restated rather than
 * imported because that set is private to engineering-runtime.js; it is
 * deliberately the same vocabulary, not a second one.
 */
export const V5_MODEL_UNCERTAINTY_CLASSES = Object.freeze([
  "classification", "extraction", "summarization", "ranking", "drafting", "disambiguation",
]);
export const V5_RESPONSE_FINISH_REASONS = Object.freeze([
  "complete", "truncated", "refused_by_model", "error",
]);

// Field-name fragments that mean the response reached past advice. Checked
// BEFORE the closed-key check so the refusal names what was attempted.
const COMMAND_FRAGMENTS = Object.freeze([
  "exec", "shell", "command", "tool_call", "function_call", "sudo",
  "grant", "capability", "permission", "privilege", "authority", "authorization",
  "override", "escalate", "credential", "secret", "api_key", "apikey",
  "delete", "deploy", "migrate", "activate",
]);

function refuseCommandShapedFields(object, path) {
  for (const key of Object.keys(object)) {
    const normalized = key.toLowerCase();
    const hit = COMMAND_FRAGMENTS.find(fragment => normalized.includes(fragment));
    if (hit) {
      fail("model_content_is_not_a_command",
        "a model response is advisory content; it never carries an instruction, a capability or an authority",
        { path: `${path}.${key}`, field: key, fragment: hit });
    }
  }
}

const BIND_ARG_KEYS = Object.freeze(["adapter", "proposal", "response", "now"]);

/**
 * Validate one response against the exact proposal it claims to answer.
 *
 * The route comparison is the important one and it is exact on all four fields:
 * a backend that answered with a cheaper model, an older version or a lower
 * effort is refused here, which is the only place in this slice where such a
 * substitution could still be caught.
 */
export function validateBoundResponse(args) {
  assertObject(args, "args");
  assertClosedKeys(args, BIND_ARG_KEYS, "args");
  assertRequiredKeys(args, BIND_ARG_KEYS, "args");
  const adapter = assertSealedAdapter(args.adapter);
  const proposal = assertProposal(args.proposal);
  const response = assertObject(args.response, "response");
  const nowMs = assertInstant(args.now, "args.now");

  refuseCommandShapedFields(response, "response");
  assertClosedKeys(response, RESPONSE_KEYS, "response");
  assertRequiredKeys(response, RESPONSE_REQUIRED, "response");

  if (response.invocation_id !== proposal.invocation_id) {
    fail("response_invocation_id_mismatch", "this response answers a different invocation",
      { expected: proposal.invocation_id, actual: response.invocation_id });
  }
  if (response.proposal_digest !== proposal.proposal_digest) {
    fail("response_proposal_digest_mismatch", "this response is bound to different proposal bytes",
      { expected: proposal.proposal_digest, actual: response.proposal_digest });
  }
  if (response.adapter_id !== adapter.adapter_id || proposal.adapter_id !== adapter.adapter_id) {
    fail("response_adapter_mismatch", "the response, the proposal and the adapter must be the same adapter",
      { adapter_id: adapter.adapter_id, proposal_adapter_id: proposal.adapter_id,
        response_adapter_id: response.adapter_id });
  }
  // Context and receipt drift: a response that quietly re-parents itself onto a
  // different context or receipt would make the receipt record a job that was
  // never the one performed.
  if (response.envelope_binding_digest !== proposal.envelope_binding_digest) {
    fail("receipt_context_drift", "the response does not carry the envelope binding it was issued against",
      { expected: proposal.envelope_binding_digest, actual: response.envelope_binding_digest });
  }
  if (response.context_digest !== proposal.context_digest) {
    fail("receipt_context_drift", "the response names a different context digest",
      { expected: proposal.context_digest, actual: response.context_digest });
  }
  if (response.receipt_binding_ref !== proposal.receipt_binding_ref) {
    fail("receipt_context_drift", "the response names a different receipt binding",
      { expected: proposal.receipt_binding_ref, actual: response.receipt_binding_ref });
  }
  const substituted = ["backend_key", "model_key", "model_version", "effort"]
    .filter(key => response[key] !== proposal.route[key]);
  if (substituted.length > 0) {
    fail("response_route_substitution_refused",
      "the responding occupant is not the routed one; a substituted model is never accepted silently",
      { fields: substituted, routed: { ...proposal.route }, responded: {
        backend_key: response.backend_key, model_key: response.model_key,
        model_version: response.model_version, effort: response.effort,
      } });
  }
  const producedAt = assertInstant(response.produced_at, "response.produced_at");
  const proposedAt = assertInstant(proposal.proposed_at, "proposal.proposed_at");
  if (producedAt < proposedAt) {
    fail("response_precedes_proposal", "a response cannot be older than the invocation it answers",
      { proposed_at: proposal.proposed_at, produced_at: response.produced_at });
  }
  if (producedAt > nowMs) {
    fail("response_from_the_future", "a response cannot be produced after the observing instant",
      { produced_at: response.produced_at, now: args.now });
  }
  if (typeof response.content !== "string") {
    fail("invalid_shape", "response.content must be a string", { path: "response.content" });
  }
  if (!V5_RESPONSE_FINISH_REASONS.includes(response.finish_reason)) {
    fail("unknown_finish_reason", `"${response.finish_reason}" is not a registered finish reason`,
      { registered: [...V5_RESPONSE_FINISH_REASONS] });
  }
  const typedProposals = [];
  if (response.typed_proposals !== undefined && response.typed_proposals !== null) {
    if (!Array.isArray(response.typed_proposals)) {
      fail("invalid_shape", "response.typed_proposals must be an array", { path: "response.typed_proposals" });
    }
    const seen = new Set();
    response.typed_proposals.forEach((entry, index) => {
      const path = `response.typed_proposals[${index}]`;
      assertObject(entry, path);
      refuseCommandShapedFields(entry, path);
      assertClosedKeys(entry, TYPED_PROPOSAL_KEYS, path);
      assertRequiredKeys(entry, ["proposal_id", "uncertainty_class", "label"], path);
      assertRef(entry.proposal_id, `${path}.proposal_id`);
      if (seen.has(entry.proposal_id)) fail("duplicate_entry", `${path}.proposal_id is repeated`, { path });
      seen.add(entry.proposal_id);
      if (!V5_MODEL_UNCERTAINTY_CLASSES.includes(entry.uncertainty_class)) {
        fail("unknown_uncertainty_class",
          `"${entry.uncertainty_class}" is not a registered typed-uncertainty class`,
          { path, registered: [...V5_MODEL_UNCERTAINTY_CLASSES] });
      }
      if (typeof entry.label !== "string" || entry.label.length === 0) {
        fail("invalid_shape", `${path}.label must be a non-empty string`, { path });
      }
      if (entry.note !== undefined && typeof entry.note !== "string") {
        fail("invalid_shape", `${path}.note must be a string when present`, { path });
      }
      typedProposals.push({
        proposal_id: entry.proposal_id, uncertainty_class: entry.uncertainty_class,
        label: entry.label, note: entry.note ?? null,
      });
    });
  }

  const validated = deepFreeze({
    schema_version: V5_MODEL_RESPONSE_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    invocation_id: proposal.invocation_id,
    adapter_id: adapter.adapter_id,
    proposal_digest: proposal.proposal_digest,
    envelope_binding_digest: proposal.envelope_binding_digest,
    job_id: proposal.job_id,
    role_digest: proposal.role_digest,
    context_digest: proposal.context_digest,
    receipt_binding_ref: proposal.receipt_binding_ref,
    route: { ...proposal.route },
    produced_at: response.produced_at,
    finish_reason: response.finish_reason,
    content: response.content,
    typed_proposals: typedProposals.sort((a, b) => (a.proposal_id < b.proposal_id ? -1 : 1)),
    // The properties a caller is entitled to rely on after this returns. The
    // contract was validated against an authentic proposal; the content itself
    // is untrusted model output and nothing here says otherwise.
    model_content_is_advisory: true,
    authority_granted: false,
    executed: false,
    dispatch_implemented: false,
    live_backend_verified: false,
    effects: V5_NO_EFFECTS,
  });
  VALIDATED_MODEL_RESPONSES.add(validated);
  return validated;
}

/**
 * Read one validated response as evidence.
 *
 * THE READ A RECEIPT LAYER OWES. `validateBoundResponse` returns a frozen
 * `model-response.v1`, and that object carries no digest of its own — its
 * bindings are the proposal's, copied across after the contract check. So a
 * spread copy or a JSON round trip carrying the genuine `proposal_digest` and
 * `envelope_binding_digest` is indistinguishable by hashing, and object identity
 * is the only thing separating it from a response this module actually
 * validated.
 *
 * WHAT PASSING THIS MEANS, EXACTLY: this module validated the response CONTRACT
 * — invocation, proposal bytes, adapter, envelope binding, context, receipt,
 * occupant and ordering — against an AUTHENTIC proposal, in this process. It is
 * not a claim that a backend produced the content, not a claim that the content
 * is true, and not a claim that survives leaving this process.
 */
export function assertValidatedResponse(response, path = "validated_response") {
  assertObject(response, path);
  if (response.schema_version !== V5_MODEL_RESPONSE_SCHEMA_VERSION) {
    fail("response_schema_version_invalid",
      `${path}.schema_version must be ${V5_MODEL_RESPONSE_SCHEMA_VERSION}`, { path });
  }
  // Checked at every read for the same reason the proposal's three are: they
  // are claims about what happened, and nothing seals them.
  if (response.executed !== false || response.dispatch_implemented !== false) {
    fail("dispatch_claim_refused",
      `${path} claims an execution this slice cannot perform`, { path, seam: V5_DISPATCH_SEAM });
  }
  if (response.authority_granted !== false) {
    fail("adapter_authority_mint_refused", `${path} claims to grant authority`, { path });
  }
  if (!isValidatedModelResponse(response)) {
    fail("validated_response_not_locally_produced",
      `${path} was not produced by validateBoundResponse in this process, so no proposal was ever checked behind it; the bindings it carries are copied values, not a seal`,
      { path, provenance_scope: V5_PROVENANCE_SCOPE });
  }
  return response;
}

// ---------------------------------------------------------------------------
// The invocation ledger.
//
// PROCESS-LOCAL AND SAID SO. This is a Set with rules, not a record store: it
// refuses a repeated invocation id and a second response for one invocation
// inside one process. Durable replay defence belongs to the record layer, and
// this slice adds no table, no migration and no persistence — see
// `v5ModelRoutingAdapterProjection`.
// ---------------------------------------------------------------------------

export function createInvocationLedger() {
  const open = new Map();
  const closed = new Set();
  return Object.freeze({
    open(proposal) {
      const checked = assertProposal(proposal);
      if (closed.has(checked.invocation_id) || open.has(checked.invocation_id)) {
        fail("invocation_replay_refused", "this invocation id has already been used",
          { invocation_id: checked.invocation_id });
      }
      open.set(checked.invocation_id, checked.proposal_digest);
      return deepFreeze({
        invocation_id: checked.invocation_id,
        proposal_digest: checked.proposal_digest,
        state: "open",
        durable: false,
        effects: V5_NO_EFFECTS,
      });
    },
    bindResponse(args) {
      assertObject(args, "args");
      assertClosedKeys(args, BIND_ARG_KEYS, "args");
      const proposal = assertProposal(args.proposal);
      if (closed.has(proposal.invocation_id)) {
        fail("response_already_bound", "this invocation already has a bound response",
          { invocation_id: proposal.invocation_id });
      }
      if (!open.has(proposal.invocation_id)) {
        fail("invocation_not_open", "no open invocation carries this id",
          { invocation_id: proposal.invocation_id });
      }
      if (open.get(proposal.invocation_id) !== proposal.proposal_digest) {
        fail("invocation_proposal_drift",
          "the open invocation was recorded against different proposal bytes",
          { invocation_id: proposal.invocation_id });
      }
      const validated = validateBoundResponse(args);
      open.delete(proposal.invocation_id);
      closed.add(proposal.invocation_id);
      return validated;
    },
    state() {
      return deepFreeze({
        open_invocation_count: open.size,
        closed_invocation_count: closed.size,
        durable: false,
        effects: V5_NO_EFFECTS,
      });
    },
  });
}

// ---------------------------------------------------------------------------
// Q130 — adapter replacement equivalence.
// ---------------------------------------------------------------------------

/**
 * Prove that a set of proposals built for ONE job through DIFFERENT adapters
 * preserves every binding a replacement must preserve.
 *
 * The occupant is expected to differ — that is the point of a replaceable
 * backend — so `route` is reported rather than compared. Everything else is
 * compared exactly, and a divergence throws instead of being summarised as a
 * warning.
 */
export function assertAdapterContractEquivalence(proposals) {
  if (!Array.isArray(proposals) || proposals.length < 2) {
    fail("invalid_shape", "pass at least two proposals to compare", { path: "proposals" });
  }
  const checked = proposals.map((proposal, index) => assertProposal(proposal, `proposals[${index}]`));
  const preserved = [
    "envelope_binding_digest", "job_id", "role_digest", "context_digest",
    "receipt_binding_ref", "prompt_payload_digest", "product_identity",
  ];
  const first = checked[0];
  checked.slice(1).forEach((proposal, index) => {
    for (const key of preserved) {
      if (proposal[key] !== first[key]) {
        fail("adapter_contract_divergence",
          `adapter replacement changed "${key}", which a replacement must preserve`,
          { field: key, expected: first[key], actual: proposal[key], proposal_index: index + 1 });
      }
    }
  });
  const adapterIds = checked.map(p => p.adapter_id);
  if (new Set(adapterIds).size !== adapterIds.length) {
    fail("invalid_shape", "compare distinct adapters; the same adapter twice proves nothing",
      { adapter_ids: adapterIds });
  }
  return deepFreeze({
    schema_version: V5_ADAPTER_SCHEMA_VERSION,
    equivalent: true,
    compared_adapter_ids: [...adapterIds].sort(),
    envelope_binding_digest: first.envelope_binding_digest,
    job_id: first.job_id,
    role_digest: first.role_digest,
    context_digest: first.context_digest,
    receipt_binding_ref: first.receipt_binding_ref,
    prompt_payload_digest: first.prompt_payload_digest,
    product_identity: V5_PUBLIC_PRODUCT_IDENTITY,
    // Reported, not compared: the replaceable part.
    route_keys: checked.map(p => p.route.route_key).sort(),
    capability_grants_total: 0,
    any_backend_granted_authority: false,
    effects: V5_NO_EFFECTS,
  });
}

/** What the adapter boundary implements, and the gaps it declines to fake. */
export function v5ModelRoutingAdapterProjection() {
  return deepFreeze({
    schema_version: V5_ADAPTER_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    proposal_schema_version: V5_INVOCATION_PROPOSAL_SCHEMA_VERSION,
    response_schema_version: V5_MODEL_RESPONSE_SCHEMA_VERSION,
    transport_classes: [...V5_ADAPTER_TRANSPORT_CLASSES],
    uncertainty_classes: [...V5_MODEL_UNCERTAINTY_CLASSES],
    product_identity: V5_PUBLIC_PRODUCT_IDENTITY,
    canonical_authority: V5_CANONICAL_AUTHORITY,
    dispatch_implemented: false,
    dispatch_seam: V5_DISPATCH_SEAM,
    provider_calls_possible_here: false,
    callback_shell_or_network_mechanism_added: false,
    adapter_can_mint_permissions: false,
    adapter_can_change_product_identity: false,
    live_backend_verified: false,
    // Scoped honestly: within this process the envelope, the prompt, the
    // proposal and the validated response must each be the object the module
    // that owns them actually produced. Nothing here can establish that about
    // anything that arrived over a wire.
    provenance_scope: V5_PROVENANCE_SCOPE,
    provenance_survives_serialization: false,
    proposal_provenance_registered: true,
    validated_response_provenance_registered: true,
    // What a validated response is, and the three things it is not. Stated as
    // fields because a downstream receipt layer reads this projection, and the
    // difference between these lines is the whole weight of the record.
    validated_response_is_contract_validated_against_an_authentic_proposal: true,
    validated_response_is_an_authentic_backend_answer: false,
    model_payload_is_trusted_content: false,
    unimplemented_dependencies: [
      "actual dispatch to any backend: V5-F06/V5-F07 own it and dispatchInvocation always refuses",
      "durable invocation and response record store: the ledger here is process-local, and this slice adds no table or migration",
      "live backend health and provider version evidence: none is observed or claimed anywhere in this slice",
      "authenticity for an envelope, prompt, invocation proposal or validated response that crossed a process boundary: every check here is an object-identity check against a process-local register — the routing module's for envelopes and prompts, this module's for proposals and responses — and a deserialized one is refused rather than re-admitted on its digest; a signed capability token from the record layer is what this would need and none exists in this slice",
      "an authenticated backend answer: a validated response means this module checked the response contract against an authentic proposal, never that a backend produced the content or that the content is true; the model payload stays untrusted and no signature over it exists here",
    ],
    effects: V5_NO_EFFECTS,
  });
}
