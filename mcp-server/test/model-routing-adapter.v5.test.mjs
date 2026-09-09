// V5-F04 — the replaceable adapter boundary, proved case by case.
//
// The positive half proves the boundary is USEFUL: it produces a validated
// invocation proposal and validates a bound response. The negative half proves
// it is a boundary at all — an unauthenticated route, a backend mismatch, a
// substituted occupant, a drifted receipt context, a replayed invocation and a
// response carrying an instruction each have to refuse by name.
//
// EVERY ADAPTER BELOW IS A TEST FIXTURE. None of them speaks to a provider, a
// Mac Studio or a Hermes deployment; no test makes a network call, and the
// module contains no code that could. `dispatchInvocation` is asserted to refuse
// precisely because the dispatch it names does not exist yet.

import test from "node:test";
import assert from "node:assert/strict";

import { digest } from "../src/artifact-trust.js";
import { V5_CANONICAL_AUTHORITY } from "../src/global-boundaries.v5.js";
import {
  V5_PUBLIC_PRODUCT_IDENTITY,
  V5_PROMPT_PAYLOAD_SCHEMA_VERSION,
  V5_PROVENANCE_SCOPE,
  V5RoutingError,
  defineRole,
  compileRoutingPolicy,
  buildJobEnvelope,
  bindEnvelopeToRoute,
  buildPromptPayload,
  proposeRoutePlan,
  createModelRoutingGate,
} from "../src/model-routing.v5.js";
import {
  V5_ADAPTER_SCHEMA_VERSION,
  V5_ADAPTER_TRANSPORT_CLASSES,
  V5_INVOCATION_PROPOSAL_SCHEMA_VERSION,
  V5_MODEL_RESPONSE_SCHEMA_VERSION,
  V5_MODEL_UNCERTAINTY_CLASSES,
  V5_DISPATCH_SEAM,
  V5AdapterError,
  registerAdapter,
  buildInvocationProposal,
  dispatchInvocation,
  validateBoundResponse,
  createInvocationLedger,
  assertAdapterContractEquivalence,
  v5ModelRoutingAdapterProjection,
} from "../src/model-routing-adapter.v5.js";

const NOW = "2026-09-09T12:00:00.000Z";
const PRODUCED_AT = "2026-09-09T12:00:03.000Z";
const OBSERVED_AT = "2026-09-09T12:00:05.000Z";
const CONTEXT_DIGEST = digest({ fixture: "f04-adapter-context", n: 1 });

function refuses(fn, code) {
  try {
    fn();
  } catch (error) {
    assert.ok(error instanceof V5AdapterError || error instanceof V5RoutingError,
      `expected a V5 error, got ${error?.name}: ${error?.message}`);
    assert.equal(error.code, code, `expected code "${code}", got "${error.code}" (${error.message})`);
    return error;
  }
  return assert.fail(`expected a refusal with code "${code}"`);
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

// --- synthetic routing fixtures --------------------------------------------

function route(route_key, backend_key, model_key, effort, cost) {
  return {
    route_key, task_class: "task:deal-review", backend_key,
    model_key, model_version: "2026-01", effort, residual_risk_class: "risk:standard",
    cost_components: {
      base_cost_units: cost, expected_retry_cost_units: 1,
      expected_review_cost_units: 1, expected_fallback_cost_units: 0,
    },
  };
}

function policyDocument() {
  return {
    schema_version: "model-routing-policy.v1",
    policy_id: "policy:f04-adapter-fixture",
    policy_version: 1,
    ranking: {
      key_order: [
        "quality_rank", "residual_risk_rank", "privacy_rank",
        "latency_ms_p95", "expected_total_cost_units", "local_preference_rank",
      ],
    },
    risk_ranks: { "risk:low": 0, "risk:standard": 1 },
    privacy_ranks: { "egress:none": 0, "egress:internal": 1, "egress:external": 2 },
    // One declared scale; the occupant grades and the strength reference below
    // both name grades from it. Synthetic names, like everything else here.
    quality_scale: {
      scale_id: "scale:f04-adapter-fixture-grades",
      scale_version: 1,
      grades: ["grade:fixture-review", "grade:fixture-architecture"],
    },
    occupant_grades: {
      "model:fixture-strong-a@2026-01/high": "grade:fixture-architecture",
      "model:fixture-mid-b@2026-01/standard": "grade:fixture-review",
    },
    strength_refs: { "strength:review-grade": "grade:fixture-review" },
    backends: [
      {
        backend_key: "backend:mac-studio-local", kind: "local", local_node: "mac-studio",
        egress_class: "egress:none", permitted_data_classes: ["market_comp"],
        user_facing: false, grants_authority: false,
      },
      {
        backend_key: "backend:hermes-platform", kind: "hosted_open_model_platform",
        egress_class: "egress:internal", permitted_data_classes: ["market_comp"],
        user_facing: false, grants_authority: false,
      },
      {
        backend_key: "backend:cloud-gateway", kind: "cloud",
        egress_class: "egress:external", permitted_data_classes: ["market_comp"],
        user_facing: false, grants_authority: false,
      },
    ],
    task_classes: [{
      task_class: "task:deal-review",
      permitted_backend_keys: [
        "backend:cloud-gateway", "backend:hermes-platform", "backend:mac-studio-local",
      ],
      permitted_data_classes: ["market_comp"],
      required_quality_floor_refs: ["floor:reviewer-evidence-cited"],
      local_preference_enabled: true,
      local_capability: "local_model_inference",
      privacy_restriction: "none",
    }],
    routes: [
      route("route:local-a", "backend:mac-studio-local", "model:fixture-strong-a", "high", 4),
      route("route:cloud-a", "backend:cloud-gateway", "model:fixture-strong-a", "high", 3),
      route("route:hermes-b", "backend:hermes-platform", "model:fixture-mid-b", "standard", 2),
    ],
  };
}

function compiled(mutate) {
  const document = policyDocument();
  if (mutate) mutate(document);
  return compileRoutingPolicy(document);
}

function reviewerRole() {
  return defineRole({
    role_key: "reviewer",
    title: "Reviewer",
    mission: "Independently review delivered work against its accepted contract and its evidence.",
    skills: ["reads a delivered change against the accepted slice contract"],
    rules: ["records a finding rather than repairing the work silently"],
    authority: { authority_class: "developer", capability_refs: ["capability:deal.read"] },
    evidence_requirements: ["an exact source digest for every finding"],
    quality_floor_refs: ["floor:reviewer-evidence-cited"],
    minimum_strength_ref: "strength:review-grade",
    task_classes: ["task:deal-review"],
  });
}

function reviewJob() {
  return {
    job_id: "job:fixture-adapter-1",
    task_class: "task:deal-review",
    data_classes: ["market_comp"],
    required_tool_ids: ["tool:comp-lookup"],
    risk_class: "risk:standard",
    context_digest: CONTEXT_DIGEST,
    capability_refs: ["capability:deal.read"],
    receipt_binding_ref: "receipt:fixture-adapter-1",
  };
}

function qualification(id, backend_key, model_key, effort, latency) {
  return {
    qualification_id: id,
    task_class: "task:deal-review",
    backend_key, model_key, model_version: "2026-01", effort,
    qualified_tool_ids: ["tool:comp-lookup"],
    permitted_data_classes: ["market_comp"],
    qualified_max_risk_class: "risk:standard",
    met_quality_floor_refs: ["floor:reviewer-evidence-cited"],
    measured_latency_ms_p95: latency,
    measured_at: "2026-09-01T00:00:00.000Z",
    expires_at: "2026-10-01T00:00:00.000Z",
    measurement_digest: digest({ fixture: "f04-adapter-measurement", id }),
    verifier_id: "verifier:fixture-evaluation-kernel",
  };
}

const QUALIFICATIONS = [
  qualification("qual:local", "backend:mac-studio-local", "model:fixture-strong-a", "high", 1200),
  qualification("qual:cloud", "backend:cloud-gateway", "model:fixture-strong-a", "high", 2400),
  qualification("qual:hermes", "backend:hermes-platform", "model:fixture-mid-b", "standard", 1500),
];

const TRUSTED_VERIFIER_IDS = Object.freeze(["verifier:fixture-evaluation-kernel"]);

function routeOn({ policy = compiled(), nodes = { "mac-studio": "available" }, job = reviewJob() } = {}) {
  const probe = createModelRoutingGate({
    authenticateQualifications: () => ({}), trusted_verifier_ids: [...TRUSTED_VERIFIER_IDS],
  });
  const gate = createModelRoutingGate({
    trusted_verifier_ids: [...TRUSTED_VERIFIER_IDS],
    authenticateQualifications: request => ({
      request_binding_digest: probe.requestBindingDigest(request),
      qualifications: clone(QUALIFICATIONS),
    }),
  });
  return gate.evaluate({ policy, role: reviewerRole(), job, now: NOW, local_node_states: nodes });
}

const ENVELOPE = (job = reviewJob()) => buildJobEnvelope({ role: reviewerRole(), job });

function boundOn(options = {}) {
  return bindEnvelopeToRoute({
    envelope: ENVELOPE(options.job ?? reviewJob()),
    routing_result: routeOn(options),
  });
}

function otherJob(suffix) {
  return {
    ...reviewJob(),
    job_id: `job:fixture-adapter-${suffix}`,
    receipt_binding_ref: `receipt:fixture-adapter-${suffix}`,
  };
}

function payloadFor(boundEnvelope, policy = compiled()) {
  return buildPromptPayload({
    policy, bound_envelope: boundEnvelope,
    payload: { parts: [{ part_id: "part:comps", data_class: "market_comp", text: "three comparable leases" }] },
  });
}

// --- synthetic adapters ----------------------------------------------------

function adapterFor(backend_key, transport_class, adapter_id, adapter_version = "1.0.0") {
  return registerAdapter({
    adapter_id, adapter_version, backend_key, transport_class,
    capability_grants: [], user_facing: false, grants_authority: false,
    product_identity: V5_PUBLIC_PRODUCT_IDENTITY,
  });
}

const localAdapter = () =>
  adapterFor("backend:mac-studio-local", "local_node_worker", "adapter:fixture-local-worker");
const hermesAdapter = () =>
  adapterFor("backend:hermes-platform", "hosted_open_model_platform", "adapter:fixture-hermes-platform");
const cloudAdapter = () =>
  adapterFor("backend:cloud-gateway", "cloud_model_gateway", "adapter:fixture-cloud-gateway");

function proposalOn({ adapter, options, invocation_id = "invocation:fixture-1" } = {}) {
  const boundEnvelope = boundOn(options);
  return buildInvocationProposal({
    adapter, bound_envelope: boundEnvelope, prompt_payload: payloadFor(boundEnvelope),
    invocation_id, now: NOW,
  });
}

function responseFor(proposal, overrides = {}) {
  return {
    invocation_id: proposal.invocation_id,
    adapter_id: proposal.adapter_id,
    backend_key: proposal.route.backend_key,
    model_key: proposal.route.model_key,
    model_version: proposal.route.model_version,
    effort: proposal.route.effort,
    produced_at: PRODUCED_AT,
    envelope_binding_digest: proposal.envelope_binding_digest,
    context_digest: proposal.context_digest,
    receipt_binding_ref: proposal.receipt_binding_ref,
    proposal_digest: proposal.proposal_digest,
    content: "three comparable leases, each with its source cited",
    finish_reason: "complete",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Registration.
// ---------------------------------------------------------------------------

test("an adapter descriptor seals a contract and claims nothing about a live backend", () => {
  const adapter = localAdapter();
  assert.equal(adapter.schema_version, V5_ADAPTER_SCHEMA_VERSION);
  assert.equal(adapter.backend_key, "backend:mac-studio-local");
  assert.equal(adapter.live_backend_verified, false);
  assert.equal(adapter.dispatch_implemented, false);
  assert.equal(adapter.dispatch_seam, V5_DISPATCH_SEAM);
  assert.equal(adapter.canonical_authority, V5_CANONICAL_AUTHORITY);
  assert.deepEqual([...adapter.capability_grants], []);
  assert.equal(adapter.grants_authority, false);
  assert.equal(adapter.user_facing, false);
  assert.equal(adapter.product_identity, V5_PUBLIC_PRODUCT_IDENTITY);
  assert.throws(() => { adapter.grants_authority = true; }, TypeError);
});

test("a replacement adapter for one backend keeps the contract and changes only its identity", () => {
  const first = adapterFor("backend:hermes-platform", "hosted_open_model_platform", "adapter:fixture-hermes-a");
  const second = adapterFor("backend:hermes-platform", "hosted_open_model_platform", "adapter:fixture-hermes-b", "2.0.0");
  assert.equal(first.contract_digest, second.contract_digest);
  assert.notEqual(first.adapter_digest, second.adapter_digest);
});

test("an adapter cannot mint permissions, face the user, or rename the product", () => {
  const base = {
    adapter_id: "adapter:fixture-x", adapter_version: "1.0.0",
    backend_key: "backend:cloud-gateway", transport_class: "cloud_model_gateway",
    capability_grants: [], user_facing: false, grants_authority: false,
    product_identity: V5_PUBLIC_PRODUCT_IDENTITY,
  };
  refuses(() => registerAdapter({ ...base, capability_grants: ["capability:deal.write"] }),
    "adapter_capability_grant_refused");
  refuses(() => registerAdapter({ ...base, grants_authority: true }), "adapter_authority_mint_refused");
  refuses(() => registerAdapter({ ...base, user_facing: true }), "adapter_user_facing_refused");
  refuses(() => registerAdapter({ ...base, product_identity: "Hermes" }), "product_identity_change_refused");
  refuses(() => registerAdapter({ ...base, transport_class: "carrier_pigeon" }), "unknown_transport_class");
  refuses(() => registerAdapter({ ...base, endpoint: "https://example.invalid" }), "unknown_field");
});

test("an adapter cannot record a verification nothing here can perform", () => {
  const base = {
    adapter_id: "adapter:fixture-x", adapter_version: "1.0.0",
    backend_key: "backend:mac-studio-local", transport_class: "local_node_worker",
    capability_grants: [], user_facing: false, grants_authority: false,
    product_identity: V5_PUBLIC_PRODUCT_IDENTITY,
  };
  for (const field of ["verified", "mac_studio_verified", "api_verified", "provider_verified", "benchmarked"]) {
    refuses(() => registerAdapter({ ...base, [field]: true }), "unverifiable_backend_claim_refused");
  }
});

// ---------------------------------------------------------------------------
// The invocation proposal.
// ---------------------------------------------------------------------------

test("a proposal carries the envelope bindings and states that nothing was sent", () => {
  const boundEnvelope = boundOn();
  const proposal = proposalOn({ adapter: localAdapter() });
  assert.equal(proposal.schema_version, V5_INVOCATION_PROPOSAL_SCHEMA_VERSION);
  assert.equal(proposal.envelope_binding_digest, boundEnvelope.binding_digest);
  assert.equal(proposal.job_id, "job:fixture-adapter-1");
  assert.equal(proposal.context_digest, CONTEXT_DIGEST);
  assert.equal(proposal.receipt_binding_ref, "receipt:fixture-adapter-1");
  assert.equal(proposal.route.backend_key, "backend:mac-studio-local");
  assert.equal(proposal.dispatched, false);
  assert.equal(proposal.dispatch_implemented, false);
  assert.equal(proposal.dispatch, V5_DISPATCH_SEAM);
  assert.equal(proposal.grants_authority, false);
  assert.equal(proposal.live_backend_verified, false);
  assert.equal(proposal.effects.network_calls, 0);
  assert.equal(proposal.effects.provider_actions, 0);
  assert.throws(() => { proposal.dispatched = true; }, TypeError);
});

test("the adapter boundary refuses an unauthenticated or unrouted envelope", () => {
  // A bare envelope: no route chosen and nothing authenticated.
  refuses(() => buildInvocationProposal({
    adapter: localAdapter(), bound_envelope: ENVELOPE(),
    prompt_payload: payloadFor(boundOn()), invocation_id: "invocation:fixture-1", now: NOW,
  }), "unauthenticated_route_refused");

  // And a schema-valid routing PROPOSAL cannot become a bound envelope at all,
  // so it can never reach this boundary.
  refuses(() => bindEnvelopeToRoute({
    envelope: ENVELOPE(),
    routing_result: proposeRoutePlan({
      policy: compiled(), role: reviewerRole(), job: reviewJob(), now: NOW,
      local_node_states: { "mac-studio": "available" }, qualifications: clone(QUALIFICATIONS),
    }),
  }), "unauthenticated_route_refused");
});

test("an adapter may not carry an invocation routed to another backend", () => {
  refuses(() => proposalOn({ adapter: cloudAdapter() }), "adapter_backend_mismatch");
});

test("a bound envelope this process did not produce is refused, however well it hashes", () => {
  const boundEnvelope = boundOn();
  const payload = payloadFor(boundEnvelope);
  const propose = (bound_envelope, adapter = localAdapter()) => buildInvocationProposal({
    adapter, bound_envelope, prompt_payload: payload,
    invocation_id: "invocation:fixture-1", now: NOW,
  });
  assert.ok(propose(boundEnvelope));

  // A round trip through JSON keeps every byte and loses the one thing that
  // mattered: this is not the object the router bound.
  refuses(() => propose(clone(boundEnvelope)), "bound_envelope_not_locally_produced");
  refuses(() => propose({ ...boundEnvelope }), "bound_envelope_not_locally_produced");

  // The occupant swap. `route` sits OUTSIDE the binding preimage by design, so
  // this copy re-hashes to the identical binding_digest — and it is paired with
  // the matching adapter, so `adapter_backend_mismatch` cannot be what refuses
  // it. Only provenance can.
  const swapped = {
    ...boundEnvelope,
    route: { ...boundEnvelope.route, backend_key: "backend:cloud-gateway", route_key: "route:cloud-a" },
  };
  assert.equal(swapped.binding_digest, boundEnvelope.binding_digest);
  assert.equal(swapped.qualification_authenticated, true);
  refuses(() => propose(swapped, cloudAdapter()), "bound_envelope_not_locally_produced");

  // And the honest unauthenticated case still names its own status first.
  refuses(() => propose(ENVELOPE()), "unauthenticated_route_refused");
  refuses(() => propose({ ...ENVELOPE(), qualification_authenticated: true }),
    "bound_envelope_not_locally_produced");
});

test("a routing decision the gate did not produce never becomes a bound envelope", () => {
  const decision = routeOn();
  const forged = clone(decision);
  assert.equal(forged.qualification_authenticated, true);
  assert.equal(forged.selected_route.route_key, decision.selected_route.route_key);
  refuses(() => bindEnvelopeToRoute({ envelope: ENVELOPE(), routing_result: forged }),
    "routing_decision_not_locally_produced");
  refuses(() => bindEnvelopeToRoute({
    envelope: ENVELOPE(),
    routing_result: { ...decision, selected_route: { ...decision.selected_route, model_key: "model:fixture-mid-b" } },
  }), "routing_decision_not_locally_produced");
});

test("a prompt payload this process did not produce is refused by the adapter", () => {
  const boundEnvelope = boundOn();
  const payload = payloadFor(boundEnvelope);
  const propose = prompt_payload => buildInvocationProposal({
    adapter: localAdapter(), bound_envelope: boundEnvelope, prompt_payload,
    invocation_id: "invocation:fixture-1", now: NOW,
  });
  assert.ok(propose(payload));
  refuses(() => propose(clone(payload)), "prompt_payload_not_locally_produced");

  // A hand-built payload whose digest is correct over its own parts. The parts
  // were never checked against the job, the task class or the backend's data
  // policy — which is exactly what buildPromptPayload does and a digest cannot.
  const parts = [{
    part_id: "part:unadmitted", data_class: "practice_business_profile",
    text: "a class this job never declared",
  }];
  const preimage = {
    schema_version: V5_PROMPT_PAYLOAD_SCHEMA_VERSION,
    envelope_binding_digest: boundEnvelope.binding_digest,
    parts,
  };
  const forged = { ...preimage, payload_digest: digest(preimage), policy_digest: boundEnvelope.policy_digest };
  assert.equal(reviewJob().data_classes.includes("practice_business_profile"), false);
  refuses(() => propose(forged), "prompt_payload_not_locally_produced");
  // And buildPromptPayload itself would have refused those parts outright.
  refuses(() => buildPromptPayload({
    policy: compiled(), bound_envelope: boundEnvelope, payload: { parts },
  }), "prompt_data_class_outside_job");
});

test("a prompt admitted under another policy does not travel to this bound route", () => {
  const hermesOnly = compiled(p => { p.routes = p.routes.filter(r => r.route_key === "route:hermes-b"); });
  const onHermes = boundOn({ policy: hermesOnly, nodes: { "mac-studio": "unavailable" } });
  const hermesPayload = payloadFor(onHermes, hermesOnly);
  assert.notEqual(hermesPayload.policy_digest, boundOn().policy_digest);
  refuses(() => buildInvocationProposal({
    adapter: localAdapter(), bound_envelope: boundOn(), prompt_payload: hermesPayload,
    invocation_id: "invocation:fixture-1", now: NOW,
  }), "prompt_payload_policy_mismatch");
});

test("a prompt payload built for a different envelope refuses", () => {
  const boundEnvelope = boundOn();
  const otherEnvelope = boundOn({ job: otherJob("2") });
  refuses(() => buildInvocationProposal({
    adapter: localAdapter(), bound_envelope: boundEnvelope,
    prompt_payload: payloadFor(otherEnvelope),
    invocation_id: "invocation:fixture-1", now: NOW,
  }), "prompt_payload_envelope_mismatch");
});

test("dispatch is unimplemented and fails closed", () => {
  refuses(() => dispatchInvocation(), "dispatch_not_implemented");
  refuses(() => dispatchInvocation(proposalOn({ adapter: localAdapter() })), "dispatch_not_implemented");
  assert.equal(v5ModelRoutingAdapterProjection().dispatch_implemented, false);
  assert.equal(v5ModelRoutingAdapterProjection().provider_calls_possible_here, false);
  assert.equal(v5ModelRoutingAdapterProjection().callback_shell_or_network_mechanism_added, false);
});

// ---------------------------------------------------------------------------
// The bound response.
// ---------------------------------------------------------------------------

test("a well-bound response validates into advisory content and nothing more", () => {
  const adapter = localAdapter();
  const proposal = proposalOn({ adapter });
  const validated = validateBoundResponse({
    adapter, proposal, now: OBSERVED_AT,
    response: responseFor(proposal, {
      typed_proposals: [
        { proposal_id: "p:kind", uncertainty_class: "classification", label: "market_comp_summary" },
        { proposal_id: "p:draft", uncertainty_class: "drafting", label: "review_note", note: "two sentences" },
      ],
    }),
  });
  assert.equal(validated.schema_version, V5_MODEL_RESPONSE_SCHEMA_VERSION);
  assert.equal(validated.invocation_id, proposal.invocation_id);
  assert.equal(validated.envelope_binding_digest, proposal.envelope_binding_digest);
  assert.equal(validated.job_id, proposal.job_id);
  assert.equal(validated.receipt_binding_ref, proposal.receipt_binding_ref);
  assert.equal(validated.model_content_is_advisory, true);
  assert.equal(validated.authority_granted, false);
  assert.equal(validated.executed, false);
  assert.deepEqual(validated.typed_proposals.map(entry => entry.proposal_id), ["p:draft", "p:kind"]);
  assert.ok(V5_MODEL_UNCERTAINTY_CLASSES.includes(validated.typed_proposals[0].uncertainty_class));
  assert.throws(() => { validated.executed = true; }, TypeError);
});

test("a substituted occupant is refused at the response boundary", () => {
  const adapter = localAdapter();
  const proposal = proposalOn({ adapter });
  for (const [field, value] of [
    ["model_key", "model:fixture-mid-b"],
    ["model_version", "2025-06"],
    ["effort", "standard"],
    ["backend_key", "backend:cloud-gateway"],
  ]) {
    const error = refuses(() => validateBoundResponse({
      adapter, proposal, now: OBSERVED_AT, response: responseFor(proposal, { [field]: value }),
    }), "response_route_substitution_refused");
    assert.deepEqual(error.detail.fields, [field]);
  }
});

test("a response that drifts off its invocation, proposal or receipt context refuses", () => {
  const adapter = localAdapter();
  const proposal = proposalOn({ adapter });
  refuses(() => validateBoundResponse({
    adapter, proposal, now: OBSERVED_AT,
    response: responseFor(proposal, { invocation_id: "invocation:fixture-2" }),
  }), "response_invocation_id_mismatch");
  refuses(() => validateBoundResponse({
    adapter, proposal, now: OBSERVED_AT,
    response: responseFor(proposal, { proposal_digest: digest({ other: "proposal" }) }),
  }), "response_proposal_digest_mismatch");
  refuses(() => validateBoundResponse({
    adapter, proposal, now: OBSERVED_AT,
    response: responseFor(proposal, { envelope_binding_digest: digest({ other: "envelope" }) }),
  }), "receipt_context_drift");
  refuses(() => validateBoundResponse({
    adapter, proposal, now: OBSERVED_AT,
    response: responseFor(proposal, { context_digest: digest({ other: "context" }) }),
  }), "receipt_context_drift");
  refuses(() => validateBoundResponse({
    adapter, proposal, now: OBSERVED_AT,
    response: responseFor(proposal, { receipt_binding_ref: "receipt:someone-elses" }),
  }), "receipt_context_drift");
  refuses(() => validateBoundResponse({
    adapter: hermesAdapter(), proposal, now: OBSERVED_AT, response: responseFor(proposal),
  }), "response_adapter_mismatch");
});

test("a response cannot predate its invocation or postdate the observing instant", () => {
  const adapter = localAdapter();
  const proposal = proposalOn({ adapter });
  refuses(() => validateBoundResponse({
    adapter, proposal, now: OBSERVED_AT,
    response: responseFor(proposal, { produced_at: "2026-09-09T11:59:00.000Z" }),
  }), "response_precedes_proposal");
  refuses(() => validateBoundResponse({
    adapter, proposal, now: OBSERVED_AT,
    response: responseFor(proposal, { produced_at: "2026-09-09T13:00:00.000Z" }),
  }), "response_from_the_future");
  refuses(() => validateBoundResponse({
    adapter, proposal, now: OBSERVED_AT,
    response: responseFor(proposal, { produced_at: "2026-02-31T00:00:00.000Z" }),
  }), "invalid_timestamp");
});

test("model content is content: an instruction-shaped field refuses by name", () => {
  const adapter = localAdapter();
  const proposal = proposalOn({ adapter });
  for (const field of [
    "tool_calls", "shell_command", "grant_capability", "authority_override",
    "exec_plan", "api_key", "deploy_target",
  ]) {
    refuses(() => validateBoundResponse({
      adapter, proposal, now: OBSERVED_AT, response: responseFor(proposal, { [field]: "anything" }),
    }), "model_content_is_not_a_command");
  }
  refuses(() => validateBoundResponse({
    adapter, proposal, now: OBSERVED_AT,
    response: responseFor(proposal, {
      typed_proposals: [{ proposal_id: "p:a", uncertainty_class: "ranking", label: "x", escalate_to: "joe" }],
    }),
  }), "model_content_is_not_a_command");
  refuses(() => validateBoundResponse({
    adapter, proposal, now: OBSERVED_AT,
    response: responseFor(proposal, {
      typed_proposals: [{ proposal_id: "p:a", uncertainty_class: "decision", label: "x" }],
    }),
  }), "unknown_uncertainty_class");
  refuses(() => validateBoundResponse({
    adapter, proposal, now: OBSERVED_AT, response: responseFor(proposal, { confidence: 0.99 }),
  }), "unknown_field");
  refuses(() => validateBoundResponse({
    adapter, proposal, now: OBSERVED_AT, response: responseFor(proposal, { finish_reason: "vibes" }),
  }), "unknown_finish_reason");
  refuses(() => validateBoundResponse({
    adapter, proposal, now: OBSERVED_AT, response: responseFor(proposal, { content: { text: "hi" } }),
  }), "invalid_shape");
});

// ---------------------------------------------------------------------------
// Replay and single-use binding.
// ---------------------------------------------------------------------------

test("the ledger refuses a replayed invocation and a second response", () => {
  const adapter = localAdapter();
  const ledger = createInvocationLedger();
  const proposal = proposalOn({ adapter });
  const opened = ledger.open(proposal);
  assert.equal(opened.state, "open");
  assert.equal(opened.durable, false);
  refuses(() => ledger.open(proposal), "invocation_replay_refused");

  const second = proposalOn({ adapter, invocation_id: "invocation:fixture-2" });
  refuses(() => ledger.bindResponse({
    adapter, proposal: second, now: OBSERVED_AT, response: responseFor(second),
  }), "invocation_not_open");

  const validated = ledger.bindResponse({
    adapter, proposal, now: OBSERVED_AT, response: responseFor(proposal),
  });
  assert.equal(validated.invocation_id, proposal.invocation_id);
  assert.equal(ledger.state().open_invocation_count, 0);
  assert.equal(ledger.state().closed_invocation_count, 1);
  refuses(() => ledger.bindResponse({
    adapter, proposal, now: OBSERVED_AT, response: responseFor(proposal),
  }), "response_already_bound");
  refuses(() => ledger.open(proposal), "invocation_replay_refused");
});

test("an edited or over-claiming proposal is refused wherever it is read", () => {
  const edited = clone(proposalOn({ adapter: localAdapter() }));
  edited.route.model_key = "model:fixture-mid-b";
  refuses(() => createInvocationLedger().open(edited), "proposal_digest_mismatch");

  // These three ride outside the digest, so they are checked at every read.
  const dispatched = clone(proposalOn({ adapter: localAdapter() }));
  dispatched.dispatched = true;
  refuses(() => createInvocationLedger().open(dispatched), "dispatch_claim_refused");

  const granting = clone(proposalOn({ adapter: localAdapter() }));
  granting.grants_authority = true;
  refuses(() => createInvocationLedger().open(granting), "adapter_authority_mint_refused");

  const renamed = clone(proposalOn({ adapter: localAdapter() }));
  renamed.product_identity = "Hermes";
  refuses(() => createInvocationLedger().open(renamed), "product_identity_change_refused");
});

// ---------------------------------------------------------------------------
// Q130 — one envelope, three adapters.
// ---------------------------------------------------------------------------

test("replacing the backend adapter preserves every binding it must preserve", () => {
  const onLocal = boundOn();
  const onCloud = boundOn({ nodes: { "mac-studio": "unavailable" } });
  const hermesOnly = compiled(p => { p.routes = p.routes.filter(r => r.route_key === "route:hermes-b"); });
  const onHermes = boundOn({ policy: hermesOnly, nodes: { "mac-studio": "unavailable" } });

  assert.equal(onLocal.route.backend_key, "backend:mac-studio-local");
  assert.equal(onCloud.route.backend_key, "backend:cloud-gateway");
  assert.equal(onHermes.route.backend_key, "backend:hermes-platform");

  const proposals = [
    [localAdapter(), onLocal],
    [cloudAdapter(), onCloud],
    [hermesAdapter(), onHermes],
  ].map(([adapter, boundEnvelope], index) => buildInvocationProposal({
    adapter, bound_envelope: boundEnvelope,
    prompt_payload: payloadFor(boundEnvelope, index === 2 ? hermesOnly : compiled()),
    invocation_id: `invocation:fixture-${index + 1}`, now: NOW,
  }));

  const report = assertAdapterContractEquivalence(proposals);
  assert.equal(report.equivalent, true);
  assert.equal(report.compared_adapter_ids.length, 3);
  assert.equal(report.product_identity, V5_PUBLIC_PRODUCT_IDENTITY);
  assert.equal(report.capability_grants_total, 0);
  assert.equal(report.any_backend_granted_authority, false);
  // The bindings are identical byte for byte; only the occupant differs.
  assert.equal(new Set(proposals.map(p => p.envelope_binding_digest)).size, 1);
  assert.equal(new Set(proposals.map(p => p.prompt_payload_digest)).size, 1);
  assert.equal(new Set(proposals.map(p => p.context_digest)).size, 1);
  assert.equal(new Set(proposals.map(p => p.receipt_binding_ref)).size, 1);
  assert.equal(new Set(proposals.map(p => p.route.backend_key)).size, 3);
  assert.deepEqual([...report.route_keys], ["route:cloud-a", "route:hermes-b", "route:local-a"]);
});

test("a replacement that changes a preserved binding is a divergence, not a warning", () => {
  const adapter = localAdapter();
  const first = proposalOn({ adapter, invocation_id: "invocation:fixture-1" });
  const otherEnvelope = boundOn({ job: otherJob("9") });
  const second = buildInvocationProposal({
    adapter: adapterFor("backend:mac-studio-local", "local_node_worker", "adapter:fixture-local-b"),
    bound_envelope: otherEnvelope, prompt_payload: payloadFor(otherEnvelope),
    invocation_id: "invocation:fixture-2", now: NOW,
  });
  const error = refuses(() => assertAdapterContractEquivalence([first, second]),
    "adapter_contract_divergence");
  assert.equal(error.detail.field, "envelope_binding_digest");
  refuses(() => assertAdapterContractEquivalence([first, first]), "invalid_shape");
  refuses(() => assertAdapterContractEquivalence([first]), "invalid_shape");
});

test("the adapter projection names its gaps rather than filling them", () => {
  const projection = v5ModelRoutingAdapterProjection();
  assert.deepEqual([...projection.transport_classes], [...V5_ADAPTER_TRANSPORT_CLASSES]);
  assert.equal(projection.adapter_can_mint_permissions, false);
  assert.equal(projection.adapter_can_change_product_identity, false);
  assert.equal(projection.live_backend_verified, false);
  // The provenance checks above are process-local, and the projection says so
  // rather than reading as durable authenticity.
  assert.equal(projection.provenance_scope, V5_PROVENANCE_SCOPE);
  assert.equal(projection.provenance_survives_serialization, false);
  assert.ok(projection.unimplemented_dependencies.some(entry => entry.includes("V5-F06/V5-F07")));
  assert.ok(projection.unimplemented_dependencies.some(entry => entry.includes("process-local")));
  assert.ok(projection.unimplemented_dependencies.some(entry => entry.includes("migration")));
  assert.ok(projection.unimplemented_dependencies.some(entry =>
    entry.includes("signed capability token")));
  assert.throws(() => { projection.dispatch_implemented = true; }, TypeError);
});
