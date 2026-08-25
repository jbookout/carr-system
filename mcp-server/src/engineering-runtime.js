// Durable Engineering Passport runtime seam.
//
// This module is deliberately a projection/admission adapter over the existing
// Work Request, accepted sourced plan, capability session and ops.job ledgers.
// It does not create a second queue, authority source, transcript store, or
// model identity.  Codex is the only executable adapter in this first slice;
// Claude is refused explicitly until a fresh-native-session launcher exists.

import { sha256 } from "./sha256.js";

const DIGEST = /^sha256:[0-9a-f]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ID = /^[A-Za-z][A-Za-z0-9._:-]{2,127}$/;
const OUTCOMES = new Set(["claimed_complete", "failed", "blocked", "reopened"]);

const canonicalize = value => {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object")
    return Object.keys(value).sort().reduce((out, key) => {
      if (value[key] !== undefined) out[key] = canonicalize(value[key]);
      return out;
    }, {});
  return value;
};

export function canonicalDigest(value) {
  return `sha256:${sha256(JSON.stringify(canonicalize(value)))}`; /* legacy inline implementation retained below only as a review aid; Worker code uses shared helper.
  const K = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a8,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xab1c5ed5,0xc24b8b70,0xd192e819,0xd6990624,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
  const bytes = new TextEncoder().encode(JSON.stringify(canonicalize(value)));
  const bitLength = bytes.length * 8;
  const padded = new Uint8Array(((bytes.length + 9 + 63) >> 6) << 6); padded.set(bytes); padded[bytes.length] = 0x80;
  const view = new DataView(padded.buffer); view.setUint32(padded.length - 4, bitLength >>> 0, false); view.setUint32(padded.length - 8, Math.floor(bitLength / 0x100000000), false);
  let h0=0x6a09e667,h1=0xbb67ae85,h2=0x3c6ef372,h3=0xa54ff53a,h4=0x510e527f,h5=0x9b05688c,h6=0x1f83d9ab,h7=0x5be0cd19;
  const rotr=(x,n)=>(x>>>n)|(x<<(32-n));
  for (let offset=0; offset<padded.length; offset+=64) {
    const w = new Uint32Array(64); for (let i=0;i<16;i++) w[i]=view.getUint32(offset+i*4,false);
    for (let i=16;i<64;i++) { const s0=rotr(w[i-15],7)^rotr(w[i-15],18)^(w[i-15]>>>3); const s1=rotr(w[i-2],17)^rotr(w[i-2],19)^(w[i-2]>>>10); w[i]=(w[i-16]+s0+w[i-7]+s1)>>>0; }
    let a=h0,b=h1,c=h2,d=h3,e=h4,f=h5,g=h6,hh=h7;
    for (let i=0;i<64;i++) { const S1=rotr(e,6)^rotr(e,11)^rotr(e,25); const ch=(e&f)^(~e&g); const t1=(hh+S1+ch+K[i]+w[i])>>>0; const S0=rotr(a,2)^rotr(a,13)^rotr(a,22); const maj=(a&b)^(a&c)^(b&c); const t2=(S0+maj)>>>0; hh=g;g=f;f=e;e=(d+t1)>>>0;d=c;c=b;b=a;a=(t1+t2)>>>0; }
    h0=(h0+a)>>>0;h1=(h1+b)>>>0;h2=(h2+c)>>>0;h3=(h3+d)>>>0;h4=(h4+e)>>>0;h5=(h5+f)>>>0;h6=(h6+g)>>>0;h7=(h7+hh)>>>0;
  }
  */
}

function error(ToolError, payload) { throw new ToolError(payload); }
function text(value, field, ToolError) {
  if (typeof value !== "string" || !value.trim()) error(ToolError, { error: "engineering_field_required", field });
  return value.trim();
}
function id(value, field, ToolError) {
  const result = text(value, field, ToolError);
  if (!ID.test(result)) error(ToolError, { error: "engineering_identifier_invalid", field });
  return result;
}
function digest(value, field, ToolError) {
  if (typeof value !== "string" || !DIGEST.test(value)) error(ToolError, { error: "engineering_digest_invalid", field });
  return value;
}
function uuid(value, field, ToolError) {
  const result = text(value, field, ToolError);
  if (!UUID.test(result)) error(ToolError, { error: "engineering_uuid_invalid", field });
  return result;
}
function exactAuthorityFree(args, ToolError) {
  const forbidden = ["tenant", "organization_tenant_id", "sponsor", "partner", "actor", "identity",
    "authority", "capability", "runtime", "model", "provider", "surface", "adapter", "native_session_ref"];
  const found = forbidden.filter(key => Object.hasOwn(args || {}, key));
  if (found.length) error(ToolError, { error: "caller_authority_selector_forbidden", fields: found });
}

function requirePlan(plan, ToolError) {
  if (!plan || typeof plan !== "object" || Array.isArray(plan)) error(ToolError, { error: "engineering_slice_plan_invalid" });
  for (const key of ["schema_version", "work_request", "accepted_plan_revision", "plan_digest", "slices"])
    if (!(key in plan)) error(ToolError, { error: "engineering_slice_plan_missing_field", field: key });
  if (plan.schema_version !== "engineering-slice-plan.v1") error(ToolError, { error: "engineering_slice_plan_schema_invalid" });
  digest(plan.plan_digest, "plan_digest", ToolError);
  if (!Array.isArray(plan.slices) || plan.slices.length < 1) error(ToolError, { error: "engineering_slice_plan_empty" });
  const refs = new Set();
  for (const slice of plan.slices) {
    id(slice.slice_ref, "slice_ref", ToolError);
    if (refs.has(slice.slice_ref)) error(ToolError, { error: "engineering_slice_duplicate", slice_ref: slice.slice_ref });
    refs.add(slice.slice_ref);
    if (!Array.isArray(slice.dependency_refs) || slice.dependency_refs.some(ref => !refs.has(ref) && !plan.slices.some(candidate => candidate.slice_ref === ref)))
      error(ToolError, { error: "engineering_slice_dependency_unknown", slice_ref: slice.slice_ref });
  }
  if (canonicalDigest(Object.fromEntries(Object.entries(plan).filter(([key]) => key !== "plan_digest"))) !== plan.plan_digest)
    error(ToolError, { error: "engineering_slice_plan_digest_mismatch" });
  return plan;
}

function sourceParts(source, ToolError) {
  if (!source || typeof source !== "object" || !source.work_request || !source.accepted_plan)
    error(ToolError, { error: "engineering_admission_source_missing" });
  const work = source.work_request;
  const plan = source.accepted_plan;
  id(work.id, "work_request.id", ToolError);
  if (!Number.isInteger(Number(work.version)) || Number(work.version) < 1)
    error(ToolError, { error: "engineering_work_request_version_invalid" });
  digest(work.canonical_record_digest, "work_request.canonical_record_digest", ToolError);
  id(plan.plan_ref, "accepted_plan.plan_ref", ToolError);
  digest(plan.digest, "accepted_plan.digest", ToolError);
  return { work, plan };
}

function sourcePlan(facts, source, ToolError) {
  const row = (facts.slice_plans || []).find(item =>
    item.accepted_plan_id === source.plan.record_id && item.accepted_plan_hash === source.plan.digest);
  if (!row) error(ToolError, { error: "engineering_slice_plan_not_registered", accepted_plan: source.plan.plan_ref });
  return requirePlan(row.plan, ToolError);
}

function sliceFor(plan, sliceRef, ToolError) {
  const row = plan.slices.find(item => item.slice_ref === sliceRef);
  if (!row) error(ToolError, { error: "engineering_slice_not_found", slice_ref: sliceRef });
  return row;
}

function nowIso() { return new Date().toISOString().replace(/\.\d{3}Z$/, "Z"); }

export function buildCodexEnvelope({ source, plan, slice, jobId, sessionId, actor }) {
  const issue = nowIso();
  const expiry = new Date(Date.now() + 30 * 60 * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
  const resources = slice.declared_resource_refs || [];
  const envelope = {
    schema_version: "execution-envelope.v1",
    envelope_id: `env:${globalThis.crypto.randomUUID()}`,
    work_request_id: source.work.id,
    plan_revision: { id: `plan:${source.plan.plan_ref}`, revision: Number(source.plan.revision), digest: source.plan.digest },
    agent_session: { id: `session:${sessionId}`, lease_expires_at: expiry },
    issued_at: issue, expires_at: expiry,
    state_binding: {
      state_version: Number(source.work.version),
      canonical_record_digest: source.work.canonical_record_digest,
      accepted_resource_revisions: resources.map(resource_ref => ({ resource_ref, revision_ref: `revision:${source.work.version}`, digest: canonicalDigest({ resource_ref, version: source.work.version }) })),
      compare_and_swap_required: true,
    },
    phase_binding: { phase_id: `phase:${slice.slice_ref}`, session_affinity: "fresh_native_session_required", switch_conditions: ["verified_checkpoint", "phase_boundary"], native_session_transfer: "semantic_state_only" },
    evaluation_context: {
      experiment_arm: "audited_state_routed_executors", auditor_mode: "diverse_read_only_auditor",
      evaluation_kernel_ref: "kernel:engineering-passport-v1", workflow_rubric_digest: plan.plan_digest,
      case_set_digest: canonicalDigest({ work_request: source.work.id, slice: slice.slice_ref }),
    },
    request: {
      job_ref: `job:${jobId}`, input_digest: canonicalDigest({ work_request: source.work.id, plan: plan.plan_digest, slice: slice.slice_ref }),
      data_class: "metadata_only", allowed_actions: [],
      declared_expectations: {
        plan_step_refs: slice.declared_plan_step_refs || [], component_refs: slice.declared_component_refs || [],
        resource_refs: resources, component_dependencies: [],
      },
    },
    server_binding: {
      identity: {
        organization_tenant_id: "tenant:carr-internal", sponsoring_human_id: actor.sponsoring_human_slug ? `human:${actor.sponsoring_human_slug}` : "human:joe",
        agent_principal_id: "agent:codex", runtime_principal: "runtime:codex", personal_brain_scope: "brain:shared",
        personal_brain_version: "brain:shared-v1", personal_rule_count: 0, derived_by: "server_identity_resolution", client_mutable: false,
      },
      authority: { environment: "rehearsal", risk_class: slice.risk_class || "R1", capability_profile: "capability:engineering-read-only", capability_grant_ref: "grant:engineering-codex-v1", read_only: true, derived_by: "server_capability_resolution", client_mutable: false },
      adapter: { surface: "codex_desktop", adapter_id: "adapter:codex-desktop", adapter_version: "v1", harness_id: "harness:codex", harness_version: "v1", provider_id: "provider:openai", model_id: "model:codex", native_session_ref: `native:codex:${sessionId}`, configuration_fingerprint: canonicalDigest({ adapter: "codex", model: "codex" }) },
    },
    handoff: { mode: "original", replaces_agent_session_id: null, capability_inherited: false, checkpoint_ref: null, native_session_transfer: "semantic_state_only" },
  };
  return envelope;
}

export function validateReceiptBinding(receipt, envelope, slice, actor, ToolError) {
  if (!receipt || typeof receipt !== "object") error(ToolError, { error: "engineering_receipt_invalid" });
  if (receipt.schema_version !== "engineering-slice-receipt.v1") error(ToolError, { error: "engineering_receipt_schema_invalid" });
  for (const field of ["attribution", "planned_resource_refs", "actual_resource_refs", "planned_component_refs", "actual_component_refs", "checks", "artifact_refs", "evidence_refs", "deviations", "source_evidence"])
    if (!(field in receipt)) error(ToolError, { error: "engineering_receipt_missing_field", field });
  if (!Array.isArray(receipt.checks) || receipt.checks.length < 1 || !Array.isArray(receipt.evidence_refs) || !Array.isArray(receipt.deviations))
    error(ToolError, { error: "engineering_receipt_typed_fields_invalid" });
  if (receipt.envelope_digest !== envelope.envelope_digest) error(ToolError, { error: "engineering_receipt_envelope_mismatch" });
  if (receipt.slice_ref !== slice.slice_ref || receipt.plan_digest !== slice.plan_digest) error(ToolError, { error: "engineering_receipt_slice_binding_mismatch" });
  if (!OUTCOMES.has(receipt.outcome) || receipt.independent_verification_required !== true) error(ToolError, { error: "engineering_receipt_outcome_invalid" });
  if (receipt.reset_reconstruction?.fresh_session !== true || receipt.reset_reconstruction?.inherited_transcript_used !== false) error(ToolError, { error: "engineering_receipt_fresh_session_required" });
  if (receipt.executor_claim?.claimed_by !== actor.slug) error(ToolError, { error: "engineering_receipt_executor_mismatch" });
  return receipt;
}

function closureProjection(facts, ToolError) {
  const source = sourceParts(facts.source, ToolError);
  const plan = sourcePlan(facts, source, ToolError);
  const receipts = facts.receipts || [];
  const reviews = facts.reviewer_facts || [];
  const verified = new Set(reviews.filter(review => review.state === "passed").map(review => review.slice_ref));
  const states = plan.slices.map(slice => {
    const receipt = receipts.find(row => row.slice_ref === slice.slice_ref);
    const review = reviews.find(row => row.slice_ref === slice.slice_ref);
    const dependenciesVerified = (slice.dependency_refs || []).every(ref => verified.has(ref));
    const state = !receipt ? (dependenciesVerified ? "eligible" : "blocked")
      : review?.state === "passed" && receipt.outcome === "claimed_complete" ? "verified_complete"
        : receipt.outcome === "failed" || receipt.outcome === "reopened" ? "reopened" : "claimed";
    return { slice_ref: slice.slice_ref, ordinal: slice.ordinal, dependency_refs: slice.dependency_refs || [], state };
  });
  const complete = states.every(row => row.state === "verified_complete") && receipts.length === plan.slices.length;
  return {
    schema_version: "engineering-passport.v1", work_request: source.work_request,
    accepted_plan_revision: { id: `plan:${source.plan.plan_ref}`, revision: Number(source.plan.revision), digest: source.plan.digest },
    plan_digest: plan.plan_digest, slices: states, receipts, reviewer_facts: reviews,
    closure_state: complete ? "complete" : "blocked",
    closure: { work: complete ? "complete" : "unresolved", proof: complete ? "complete" : "unresolved", explanation: "unresolved", release: "unresolved", learning: { state: "unresolved", route: null } },
    stale_conflict: { state: "none", reason: null },
  };
}

export async function runCodexSlice({ dispatchEnvelope, desk, envelope, task }) {
  if (envelope?.server_binding?.adapter?.surface !== "codex_desktop")
    throw new Error("engineering adapter unsupported: only codex_desktop is enabled in v1");
  if (typeof dispatchEnvelope !== "function") throw new Error("engineering Codex adapter requires dispatchEnvelope");
  return dispatchEnvelope(desk, envelope, task, { fresh: true });
}

// These functions are intentionally usable by both an MCP adapter (writer
// transaction) and the supervised worker (jobs transaction).  They return
// only server-derived bindings; callers never select identity, authority,
// provider, model, or native session continuity.
export async function admitEngineeringSlice(c, actor, args, ToolError) {
  exactAuthorityFree(args, ToolError);
  uuid(args.idempotency_key, "idempotency_key", ToolError);
  const workRequest = text(args.work_request, "work_request", ToolError);
  const sliceRef = id(args.slice_ref, "slice_ref", ToolError);
  const sourceResult = await c.query("select ops.engineering_passport_facts($1::text) as facts", [workRequest]);
  if (!sourceResult.rows.length || !sourceResult.rows[0].facts?.source) error(ToolError, { error: "engineering_work_request_not_found_or_not_ready" });
  const facts = sourceResult.rows[0].facts;
  const source = sourceParts(facts.source, ToolError);
  const plan = sourcePlan(facts, source, ToolError);
  const slice = sliceFor(plan, sliceRef, ToolError);
  const existing = (facts.envelopes || []).find(row => row.slice_ref === sliceRef && row.accepted_plan_id === source.plan.record_id);
  if (existing) return { ok: true, replayed: true, envelope: existing.envelope, envelope_id: existing.id, job_id: existing.job_id };

  let sessionResult = await c.query(
    `select id, executor_actor_id, state from ops.capability_agent_session
      where work_request_id=$1 and state not in ('completed','cancelled')
      order by created_at desc limit 1`, [source.work.id.replace(/^wr:/, "")]);
  let session = sessionResult.rows[0];
  const executor = (await c.query("select id, slug from actor where slug='codex' and active=true")).rows[0];
  if (!executor) error(ToolError, { error: "engineering_codex_actor_not_provisioned" });
  if (session && session.executor_actor_id !== executor.id)
    error(ToolError, { error: "engineering_session_executor_conflict" });
  if (!session) {
    const created = await c.query(
      `insert into ops.capability_agent_session
         (work_request_id, executor_actor_id, created_by_actor_id, source_commit_sha, worktree_ref, scope_ref)
       values ($1,$2,$3,$4,$5,$6) returning id, executor_actor_id, state`,
      [source.work.id.replace(/^wr:/, ""), executor.id, actor.id, "0".repeat(40), "engineering:server-admission", `slice:${sliceRef}`]);
    session = created.rows[0];
  }
  const job = (await c.query(
    "select * from ops.engineering_enqueue_slice_job($1::text,$2::text,$3::text,$4::text)",
    [source.work.ref, sliceRef, plan.plan_digest, args.idempotency_key])).rows[0];
  if (!job) error(ToolError, { error: "engineering_job_admission_failed" });
  const envelope = buildCodexEnvelope({ source, plan, slice, jobId: job.id, sessionId: session.id, actor });
  const envelopeDigest = canonicalDigest(envelope);
  const inserted = await c.query(
    `insert into ops.engineering_execution_envelope
      (job_id,work_request_id,accepted_plan_id,slice_plan_id,slice_ref,agent_session_id,
       state_version,canonical_record_digest,envelope_digest,envelope,issued_at,expires_at)
     values ($1,$2,$3,(select id from ops.engineering_slice_plan where accepted_plan_id=$3),$4,$5,
       $6,$7,$8,$9::jsonb,$10::timestamptz,$11::timestamptz)
     returning *`,
    [job.id, source.work.id.replace(/^wr:/, ""), source.plan.record_id, sliceRef, session.id,
      Number(source.work.version), source.work.canonical_record_digest, envelopeDigest,
      JSON.stringify(envelope), envelope.issued_at, envelope.expires_at]);
  if (!inserted.rows.length) error(ToolError, { error: "engineering_envelope_admission_failed" });
  const row = inserted.rows[0];
  await writeEvent(c, actor, "admit-engineering-slice", "ops_work_request", source.work.id.replace(/^wr:/, ""), {
    new: { engineering_job_id: job.id, envelope_id: row.id, slice_ref: sliceRef, plan_digest: plan.plan_digest }, idempotency_key: args.idempotency_key,
  });
  return { ok: true, replayed: false, job_id: job.id, envelope_id: row.id, envelope_digest: envelopeDigest, agent_session_id: session.id, slice_ref: sliceRef };
}

export async function claimEngineeringSlice(c, worker, limit = 1) {
  const claimed = await c.query("select * from ops.claim_job($1::text,$2::integer,1800)", [worker, limit]);
  const rows = [];
  for (const job of claimed.rows) {
    const bound = await c.query("select * from ops.engineering_execution_envelope where job_id=$1", [job.job_id]);
    if (bound.rows.length && bound.rows[0].envelope) rows.push({ ...job, envelope: bound.rows[0].envelope, envelope_id: bound.rows[0].id, envelope_digest: bound.rows[0].envelope_digest });
  }
  return rows;
}

export async function submitEngineeringReceipt(c, claimed, receipt, actor, ToolError) {
  if (!claimed?.job_id || !claimed.lease_token || !claimed.envelope_id) error(ToolError, { error: "engineering_claim_required" });
  const envelopeRow = (await c.query("select * from ops.engineering_execution_envelope where id=$1", [claimed.envelope_id])).rows[0];
  if (!envelopeRow) error(ToolError, { error: "engineering_envelope_not_found" });
  const workRef = (await c.query("select w.ref from ops.work_request w where w.id=$1", [envelopeRow.work_request_id])).rows[0]?.ref;
  const facts = workRef ? (await c.query("select ops.engineering_passport_facts($1::text) as facts", [workRef])).rows[0]?.facts : null;
  const source = facts ? sourceParts(facts.source, ToolError) : null;
  const plan = source ? sourcePlan(facts, source, ToolError) : null;
  const slice = plan ? sliceFor(plan, envelopeRow.slice_ref, ToolError) : { slice_ref: envelopeRow.slice_ref, plan_digest: null };
  if (plan && receipt.plan_digest !== plan.plan_digest) error(ToolError, { error: "engineering_receipt_plan_mismatch" });
  validateReceiptBinding(receipt, { ...envelopeRow.envelope, envelope_digest: envelopeRow.envelope_digest }, { ...slice, plan_digest: receipt.plan_digest }, actor, ToolError);
  const receiptDigest = canonicalDigest(receipt);
  const inserted = await c.query(
    "select * from ops.engineering_record_slice_receipt($1::uuid,$2::uuid,$3::jsonb,$4::text,$5::uuid)",
    [envelopeRow.id, claimed.lease_token, JSON.stringify(receipt), receiptDigest, actor.id]);
  if (!inserted.rows.length) error(ToolError, { error: "engineering_attempt_or_lease_mismatch" });
  if (receipt.outcome === "claimed_complete") await c.query("select ops.complete_job($1::uuid,$2::uuid,$3::jsonb,$4::text)", [claimed.job_id, claimed.lease_token, JSON.stringify({ engineering_receipt_id: inserted.rows[0].id, receipt_digest: receiptDigest }), `engineering:${inserted.rows[0].id}`]);
  else await c.query("select ops.fail_job($1::uuid,$2::uuid,$3::text,$4::text)", [claimed.job_id, claimed.lease_token, `engineering_${receipt.outcome}`, "typed engineering receipt reported non-complete outcome"]);
  return { ok: true, receipt_id: inserted.rows[0].id, receipt_digest: receiptDigest };
}

export async function recordEngineeringReview(c, actor, args, ToolError) {
  exactAuthorityFree(args, ToolError);
  uuid(args.idempotency_key, "idempotency_key", ToolError);
  const receiptId = text(args.receipt_id, "receipt_id", ToolError);
  const fact = args.fact;
  if (!fact || typeof fact !== "object" || !["passed", "failed", "blocked"].includes(fact.state)) error(ToolError, { error: "engineering_reviewer_fact_invalid" });
  for (const field of ["attempt_id", "slice_ref", "reviewer_ref", "session_ref", "evidence_refs", "reviewed_deviation_refs", "resolved_deviation_refs"])
    if (!(field in fact)) error(ToolError, { error: "engineering_reviewer_fact_missing_field", field });
  if (!Array.isArray(fact.evidence_refs) || !Array.isArray(fact.reviewed_deviation_refs) || !Array.isArray(fact.resolved_deviation_refs))
    error(ToolError, { error: "engineering_reviewer_fact_typed_fields_invalid" });
  const receipt = (await c.query("select * from ops.engineering_slice_receipt where id=$1", [receiptId])).rows[0];
  if (!receipt) error(ToolError, { error: "engineering_receipt_not_found" });
  if (receipt.executor_actor_id === actor.id || fact.attempt_id !== receipt.attempt_id || fact.slice_ref !== receipt.slice_ref || fact.is_independent !== true || ![actor.slug, `actor:${actor.slug}`, `reviewer:${actor.slug}`].includes(fact.reviewer_ref))
    error(ToolError, { error: "engineering_independent_review_required" });
  if (!Array.isArray(fact.evidence_refs) || fact.state === "passed" && fact.evidence_refs.length === 0)
    error(ToolError, { error: "engineering_review_evidence_required" });
  const row = (await c.query(
    `insert into ops.engineering_reviewer_fact
      (receipt_id,work_request_id,slice_ref,reviewer_actor_id,reviewer_session_ref,state,fact,idempotency_key)
     values ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::uuid) returning *`,
    [receipt.id, receipt.work_request_id, receipt.slice_ref, actor.id, text(fact.session_ref, "fact.session_ref", ToolError), fact.state, JSON.stringify(fact), args.idempotency_key])).rows[0];
  await writeEvent(c, actor, "review-engineering-slice", "ops_work_request", receipt.work_request_id, { new: { reviewer_fact_id: row.id, receipt_id: receipt.id, state: row.state }, idempotency_key: args.idempotency_key });
  return { ok: true, reviewer_fact_id: row.id, state: row.state };
}

export function engineeringRuntimeTools({ withEnvelope, writeEvent, ToolError }) {
  return {
    "register-engineering-slice-plan": {
      write: true,
      description: "Register one typed Engineering Slice Plan as an immutable projection of the exact accepted sourced plan. It does not accept, assign, dispatch, or grant authority.",
      inputSchema: { type: "object", additionalProperties: false, properties: { idempotency_key: { type: "string" }, work_request: { type: "string" }, plan: { type: "object" }, plan_digest: { type: "string" } }, required: ["idempotency_key", "work_request", "plan", "plan_digest"] },
      handler: async (c, actor, args) => { exactAuthorityFree(args, ToolError); return withEnvelope(c, actor, "register-engineering-slice-plan", args, async () => {
        uuid(args.idempotency_key, "idempotency_key", ToolError); const plan = requirePlan(args.plan, ToolError); const work = text(args.work_request, "work_request", ToolError); const planDigest = digest(args.plan_digest, "plan_digest", ToolError);
        if (plan.plan_digest !== planDigest) error(ToolError, { error: "engineering_slice_plan_digest_mismatch" });
        const r = await c.query("select * from ops.engineering_register_slice_plan($1::text,$2::jsonb,$3::text,$4::uuid)", [work, JSON.stringify(plan), planDigest, args.idempotency_key]);
        if (!r.rows.length) error(ToolError, { error: "engineering_slice_plan_not_registered" });
        const row = r.rows[0]; await writeEvent(c, actor, "register-engineering-slice-plan", "ops_work_request", row.work_request_id, { new: { engineering_slice_plan_id: row.id, plan_digest: row.plan_digest }, idempotency_key: args.idempotency_key });
        return { ok: true, engineering_slice_plan_id: row.id, work_request_id: row.work_request_id, accepted_plan_id: row.accepted_plan_id, plan_digest: row.plan_digest };
      }); },
    },
    "admit-engineering-slice": {
      write: true,
      description: "Admit one eligible DAG slice from the exact accepted plan. The server creates the canonical ops.job, capability session, and immutable execution envelope; caller identity, authority, adapter, and native session continuity are never accepted as input.",
      inputSchema: { type: "object", additionalProperties: false, properties: { idempotency_key: { type: "string" }, work_request: { type: "string" }, slice_ref: { type: "string" } }, required: ["idempotency_key", "work_request", "slice_ref"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "admit-engineering-slice", args, () => admitEngineeringSlice(c, actor, args, ToolError)),
    },
    "review-engineering-slice": {
      write: true,
      description: "Record one independent typed reviewer fact against a persisted Engineering Slice Receipt. The reviewer must be a different actor from the executor and must provide evidence for a pass.",
      inputSchema: { type: "object", additionalProperties: false, properties: { idempotency_key: { type: "string" }, receipt_id: { type: "string" }, fact: { type: "object" } }, required: ["idempotency_key", "receipt_id", "fact"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "review-engineering-slice", args, () => recordEngineeringReview(c, actor, args, ToolError)),
    },
    "engineering-passport": {
      description: "Read the canonical typed Engineering Passport projection. Closure is derived from persisted envelopes, receipts, and independent reviewer facts; it is not a task store or authority source.",
      inputSchema: { type: "object", additionalProperties: false, properties: { work_request: { type: "string" } }, required: ["work_request"] },
      handler: async (c, _actor, args) => { const work = text(args.work_request, "work_request", ToolError); const r = await c.query("select ops.engineering_passport_facts($1::text) as facts", [work]); if (!r.rows.length || !r.rows[0].facts?.source) error(ToolError, { error: "engineering_work_request_not_found" }); return closureProjection(r.rows[0].facts, ToolError); },
    },
  };
}
