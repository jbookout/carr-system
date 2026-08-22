// One fixed, ordered AI capability program over the canonical ops.work_request
// lifecycle. Project definitions and mutable state live in Postgres. This module
// owns the server-enforced transition and independent-verification boundary.

import { implementationShapeError } from "./work-shape.js";

export const COMPLETION_KINDS = Object.freeze(["built", "extended", "adopted", "declined"]);
export const DEFAULT_PROGRAM = "carr-ai-engineering-suite-v1";

const textPresent = value => typeof value === "string" && value.trim().length > 0;
const refsPresent = value => Array.isArray(value) && value.length > 0 && value.every(textPresent);
const fullSha = value => typeof value === "string" && /^[0-9a-f]{40}$/.test(value);
const uuid = value => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);

// Completion is deliberately evidence-free now. Evidence is submitted once,
// bound to a persisted candidate, and never accepted again as caller text.
export function candidateEvidenceError(kind, evidence) {
  if (!COMPLETION_KINDS.includes(kind)) return { error: "invalid_completion_kind", allowed: COMPLETION_KINDS };
  const e = evidence && typeof evidence === "object" && !Array.isArray(evidence) ? evidence : {};
  const missing = [];
  if (kind === "declined") {
    if (!uuid(e.decision_ref)) missing.push("decision_ref");
  } else {
    if (!textPresent(e.artifact_ref)) missing.push("artifact_ref");
    if (!fullSha(e.candidate_commit_sha)) missing.push("candidate_commit_sha (40 lowercase hex)");
    if (!refsPresent(e.acceptance_test_refs)) missing.push("acceptance_test_refs");
    if (kind === "adopted") {
      if (!textPresent(e.rollback_ref)) missing.push("rollback_ref");
      if (!uuid(e.decision_ref)) missing.push("decision_ref");
    }
  }
  return missing.length ? { error: "candidate_evidence_incomplete", completion_kind: kind, missing } : null;
}

// Legacy public-shape validation stays exported so a malformed request fails at
// the contract edge. The completion handler below never trusts these strings:
// it requires the persisted session and independently stored attestation.
export function completionEvidenceError(kind, evidence) {
  if (!COMPLETION_KINDS.includes(kind)) return { error: "invalid_completion_kind", allowed: COMPLETION_KINDS };
  const e = evidence && typeof evidence === "object" && !Array.isArray(evidence) ? evidence : {};
  const missing = [];
  if (kind === "declined") {
    if (!textPresent(e.decision_ref)) missing.push("decision_ref");
    if (!textPresent(e.independent_verifier_ref)) missing.push("independent_verifier_ref");
  } else {
    if (!textPresent(e.artifact_ref)) missing.push("artifact_ref");
    if (!refsPresent(e.acceptance_test_refs)) missing.push("acceptance_test_refs");
    if (!textPresent(e.independent_verifier_ref)) missing.push("independent_verifier_ref");
    if (kind === "adopted") {
      if (!textPresent(e.rollback_ref)) missing.push("rollback_ref");
      if (!textPresent(e.decision_ref)) missing.push("decision_ref");
    }
  }
  return missing.length ? { error: "completion_evidence_incomplete", completion_kind: kind, missing } : null;
}

export function fixedProgramKey(value) {
  return value === undefined || value === null || value === "" || value === DEFAULT_PROGRAM;
}

export function nextProjectState(currentSequence, laterRows) {
  if (!Array.isArray(laterRows) || laterRows.length === 0) return { completeProgram: true, nextSequence: null };
  const next = [...laterRows].sort((a, b) => Number(a.sequence) - Number(b.sequence))[0];
  if (Number(next.sequence) !== Number(currentSequence) + 1) throw new Error("queue_gap");
  if (next.state !== "ready") throw new Error("successor_not_ready");
  return { completeProgram: false, nextSequence: Number(next.sequence) };
}

function permittedCompletion(disposition, kind) {
  if (kind === "declined") return true;
  return ({ build: "built", extend: "extended", adopt: "adopted" })[disposition] === kind;
}

function programRow(row) {
  if (!row) return null;
  return {
    ref: row.ref, sequence: Number(row.program_ordinal), title: row.title, state: row.state,
    version: Number(row.version), disposition: row.disposition, existing_status: row.existing_status,
    desired_outcome: row.desired_outcome, acceptance_criteria: row.acceptance_criteria || [],
    context: row.project_context || {}, executor_actor: row.executor_actor || null,
    blocker_code: row.blocker_code || null, blocker_detail: row.blocker_detail || null,
    completion_kind: row.completion_kind || null, completion_evidence: row.completion_evidence || null,
    shape_disposition: row.shape_disposition || null,
    shape_fixed_surface_ref: row.shape_fixed_surface_ref || null,
    shape_rationale: row.shape_rationale || null,
  };
}

function sessionBrief(row) {
  if (!row) return null;
  const p = programRow(row); const c = p.context;
  return [
    `Build the current CARR AI Engineering Suite project #${p.sequence}: ${p.title}.`,
    `Canonical Work Request: ${p.ref}; base_version: ${p.version}; disposition: ${p.disposition}; state: ${p.state}.`,
    `Outcome: ${p.desired_outcome}`, `Scope: ${c.scope || "Use the canonical Work Request scope."}`,
    `Non-goals: ${(c.non_goals || []).join("; ") || "None recorded."}`,
    `Prerequisites: ${(c.prerequisites || []).join("; ") || "None."}`,
    `First deliverable: ${c.first_deliverable || "Follow the acceptance criteria."}`,
    `Acceptance: ${(p.acceptance_criteria || []).join("; ")}`,
    `Rollback/exit: ${c.rollback_exit || "Stop and report; do not broaden scope."}`,
    `Data/risk: ${c.data_risk || "Unknown — stop before using sensitive data."}; effort: ${c.effort || "unknown"}.`,
    `Existing evidence: ${(c.evidence || []).join("; ") || "none recorded"}.`,
    `Completion meaning: ${c.completion_definition || "accepted evidence and independent verification"}.`,
    p.shape_disposition === "required"
      ? `Implementation shape: required; read the current decision with read-work-shape ${p.ref}.`
      : p.shape_disposition === "not_required"
        ? `Implementation shape: fixed at ${p.shape_fixed_surface_ref}; rationale: ${p.shape_rationale}`
        : "Implementation shape: UNDECIDED. This request cannot be claimed until a qualified seat records required or not_required with set-work-shape-disposition.",
    "Work in an isolated worktree. Write acceptance tests first. Use the cheapest qualified executor. Never merge, deploy, mutate Production, communicate externally, spend, or mark this project complete from the build session. Return a candidate for an independent actor to attest; only a separate human-governed completion can slide the queue.",
  ].join("\n");
}

function requireFixedProgram(args, ToolError) {
  if (!fixedProgramKey(args.program_key)) throw new ToolError({ error: "fixed_capability_program", program_key: args.program_key, allowed: DEFAULT_PROGRAM });
  return DEFAULT_PROGRAM;
}

async function readCurrent(c, lock = false) {
  const suffix = lock ? " for update of w" : "";
  const r = await c.query(`select w.* from ops.work_request w where w.program_key=$1 and w.state <> 'confirmed_closed' order by w.program_ordinal limit 1${suffix}`, [DEFAULT_PROGRAM]);
  return r.rows[0] || null;
}

async function readCurrentSession(c, workRequest) {
  if (!workRequest) return null;
  const r = await c.query(
    `select id, state from ops.capability_agent_session
      where work_request_id=$1 and state not in ('completed','cancelled')`,
    [workRequest.id],
  );
  const session = r.rows[0];
  return session ? { id: session.id, state: session.state } : null;
}

async function requireCurrent(c, ToolError, sequence, baseVersion) {
  const row = await readCurrent(c, true);
  if (!row) throw new ToolError({ error: "capability_program_complete", program_key: DEFAULT_PROGRAM });
  if (Number(row.program_ordinal) !== Number(sequence)) throw new ToolError({ error: "out_of_order_project", current_sequence: Number(row.program_ordinal), requested_sequence: Number(sequence) });
  if (Number(row.version) !== Number(baseVersion)) throw new ToolError({ error: "version_conflict", current_version: Number(row.version), base_version: Number(baseVersion), resolution: "re-read capability-program and retry against the current project version; never overwrite blind" });
  return row;
}

async function loadSession(c, ToolError, sessionId, workRequestId) {
  if (!uuid(sessionId)) throw new ToolError({ error: "capability_agent_session_not_found", session_id: sessionId });
  const r = await c.query(`select s.*, a.slug as executor_slug from ops.capability_agent_session s join actor a on a.id=s.executor_actor_id where s.id=$1 for update of s`, [sessionId]);
  const session = r.rows[0];
  if (!session || session.work_request_id !== workRequestId) throw new ToolError({ error: "capability_agent_session_not_for_current_project", session_id: sessionId });
  return session;
}

async function requireDecision(c, ToolError, decisionRef) {
  if (!uuid(decisionRef)) throw new ToolError({ error: "decision_not_found", decision_ref: decisionRef });
  const r = await c.query(`select 1 from event where subject_type='decision' and subject_id=$1 and verb='log-decision' limit 1`, [decisionRef]);
  if (!r.rows.length) throw new ToolError({ error: "decision_not_found", decision_ref: decisionRef });
}

const candidateEvidenceSchema = {
  type: "object", additionalProperties: false,
  properties: {
    artifact_ref: { type: "string" }, candidate_commit_sha: { type: "string" },
    acceptance_test_refs: { type: "array", items: { type: "string" } }, decision_ref: { type: "string" },
    rollback_ref: { type: "string" }, note: { type: "string" },
  },
};

const completionEvidenceSchema = {
  type: "object", additionalProperties: false,
  properties: {
    // These legacy fields keep invalid old callers legible. The handler rejects
    // them as authoritative evidence and reads the frozen candidate instead.
    artifact_ref: { type: "string" }, acceptance_test_refs: { type: "array", items: { type: "string" } },
    independent_verifier_ref: { type: "string" }, decision_ref: { type: "string" }, rollback_ref: { type: "string" },
    candidate_fingerprint: { type: "string" },
  },
};

export function capabilityProgramTools({ withEnvelope, writeEvent, ToolError }) {
  const projectFields = { idempotency_key: { type: "string" }, program_key: { type: "string", const: DEFAULT_PROGRAM }, sequence: { type: "integer" }, base_version: { type: "integer" }, human_quote: { type: "string" } };
  return {
    "capability-program": {
      write: false,
      description: "Read the one fixed CARR AI Engineering Suite. The first not-confirmed-closed item is current; blocked, failed, needs-Joe and verification rows remain current and can never be skipped. This read grants no build, merge, deploy, Production or external authority.",
      inputSchema: { type: "object", additionalProperties: false, properties: { program_key: { type: "string", const: DEFAULT_PROGRAM }, sequence: { type: "integer" }, include_all: { type: "boolean" } } },
      handler: async (c, _actor, args) => {
        requireFixedProgram(args, ToolError);
        const rows = await c.query(`select * from ops.work_request where program_key=$1 order by program_ordinal`, [DEFAULT_PROGRAM]);
        if (!rows.rows.length) throw new ToolError({ error: "capability_program_not_found", program_key: DEFAULT_PROGRAM });
        const current = rows.rows.find(row => row.state !== "confirmed_closed") || null;
        const requested = args.sequence === undefined ? current : rows.rows.find(row => Number(row.program_ordinal) === Number(args.sequence));
        if (args.sequence !== undefined && !requested) throw new ToolError({ error: "capability_project_not_found", program_key: DEFAULT_PROGRAM, sequence: args.sequence });
        const capability_session = await readCurrentSession(c, current);
        return { program_key: DEFAULT_PROGRAM, total: rows.rows.length, completed: rows.rows.filter(row => row.state === "confirmed_closed").length, program_complete: !current, current: programRow(current), requested: programRow(requested), capability_session, session_brief: sessionBrief(requested), projects: args.include_all ? rows.rows.map(programRow) : undefined };
      },
    },
    "start-capability-project": {
      write: true, humanOnly: true,
      description: "Create the server-persisted build session for only the current ready project and perform the actual ready→claimed transition. The named executor must be an active CARR actor; arbitrary session strings are not accepted. This does not begin work, merge, deploy, spend or communicate.",
      inputSchema: { type: "object", additionalProperties: false, properties: { ...projectFields, executor_actor: { type: "string" }, source_commit_sha: { type: "string" }, worktree_ref: { type: "string" }, scope_ref: { type: "string" } }, required: ["idempotency_key","sequence","base_version","executor_actor","source_commit_sha","worktree_ref"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "start-capability-project", args, async () => {
        requireFixedProgram(args, ToolError);
        const row = await requireCurrent(c, ToolError, args.sequence, args.base_version);
        if (row.state !== "ready") throw new ToolError({ error: "invalid_state_transition", from: row.state, to: "claimed", required_from: "ready" });
        const shape = row.shape_disposition === "required"
          ? (await c.query(`select work_request_version from ops.work_shape_revision where work_request_id=$1 order by version desc limit 1`, [row.id])).rows[0]
          : null;
        const shapeError = implementationShapeError(row, shape);
        if (shapeError) throw new ToolError(shapeError);
        if (!fullSha(args.source_commit_sha) || !textPresent(args.worktree_ref)) throw new ToolError({ error: "capability_agent_session_identity_invalid", required: ["source_commit_sha (40 lowercase hex)","worktree_ref"] });
        const executor = (await c.query(`select id, slug from actor where slug=$1 and active=true`, [args.executor_actor])).rows[0];
        if (!executor) throw new ToolError({ error: "capability_executor_not_active", executor_actor: args.executor_actor });
        const session = (await c.query(`insert into ops.capability_agent_session (work_request_id, executor_actor_id, created_by_actor_id, source_commit_sha, worktree_ref, scope_ref) values ($1,$2,$3,$4,$5,$6) returning *`, [row.id, executor.id, actor.id, args.source_commit_sha, args.worktree_ref, args.scope_ref || null])).rows[0];
        const updated = (await c.query(`update ops.work_request set state='claimed', executor_actor=$2, claimed_at=now(), updated_at=now(), version=version+1 where id=$1 returning *`, [row.id, executor.slug])).rows[0];
        await writeEvent(c, actor, "start-capability-project", "ops_work_request", row.id, { field: "state", old: { state: "ready" }, new: { state: "claimed", executor_actor: executor.slug, capability_agent_session_id: session.id }, human_quote: args.human_quote || null, idempotency_key: args.idempotency_key });
        return { ok: true, project: programRow(updated), capability_session: { id: session.id, state: session.state, executor_actor: executor.slug, source_commit_sha: session.source_commit_sha, worktree_ref: session.worktree_ref }, session_brief: sessionBrief(updated) };
      }),
    },
    "begin-capability-project": {
      write: true, humanOnly: true,
      description: "Perform the separate claimed→in_progress transition for the persisted current capability session. It cannot create or substitute a session.",
      inputSchema: { type: "object", additionalProperties: false, properties: { ...projectFields, capability_agent_session_id: { type: "string" } }, required: ["idempotency_key","sequence","base_version","capability_agent_session_id"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "begin-capability-project", args, async () => {
        requireFixedProgram(args, ToolError);
        const row = await requireCurrent(c, ToolError, args.sequence, args.base_version);
        if (row.state !== "claimed") throw new ToolError({ error: "invalid_state_transition", from: row.state, to: "in_progress", required_from: "claimed" });
        const session = await loadSession(c, ToolError, args.capability_agent_session_id, row.id);
        if (session.state !== "claimed") throw new ToolError({ error: "invalid_capability_agent_session_state", from: session.state, to: "in_progress" });
        await c.query(`update ops.capability_agent_session set state='in_progress', started_at=now(), version=version+1 where id=$1`, [session.id]);
        const updated = (await c.query(`update ops.work_request set state='in_progress', started_at=now(), updated_at=now(), version=version+1 where id=$1 returning *`, [row.id])).rows[0];
        await writeEvent(c, actor, "begin-capability-project", "ops_work_request", row.id, { field: "state", old: { state: "claimed", capability_agent_session_id: session.id }, new: { state: "in_progress", capability_agent_session_id: session.id }, human_quote: args.human_quote || null, idempotency_key: args.idempotency_key });
        return { ok: true, project: programRow(updated), capability_session: { id: session.id, state: "in_progress" } };
      }),
    },
    "prepare-capability-project": {
      write: true, humanOnly: true,
      description: "Freeze exactly one candidate for independent verification. The candidate is bound to the persisted session and may never be changed. This does not accept completion or advance the queue.",
      inputSchema: { type: "object", additionalProperties: false, properties: { ...projectFields, capability_agent_session_id: { type: "string" }, completion_kind: { type: "string", enum: COMPLETION_KINDS }, candidate_evidence: candidateEvidenceSchema }, required: ["idempotency_key","sequence","base_version","capability_agent_session_id","completion_kind","candidate_evidence"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "prepare-capability-project", args, async () => {
        requireFixedProgram(args, ToolError);
        const row = await requireCurrent(c, ToolError, args.sequence, args.base_version);
        if (row.state !== "in_progress") throw new ToolError({ error: "invalid_state_transition", from: row.state, to: "verification", required_from: "in_progress" });
        if (!permittedCompletion(row.disposition, args.completion_kind)) throw new ToolError({ error: "completion_disposition_mismatch", disposition: row.disposition, completion_kind: args.completion_kind });
        const evidenceError = candidateEvidenceError(args.completion_kind, args.candidate_evidence);
        if (evidenceError) throw new ToolError(evidenceError);
        if (args.completion_kind === "declined" || args.completion_kind === "adopted") await requireDecision(c, ToolError, args.candidate_evidence.decision_ref);
        const session = await loadSession(c, ToolError, args.capability_agent_session_id, row.id);
        if (session.state !== "in_progress" || session.candidate_evidence) throw new ToolError({ error: "candidate_already_prepared_or_session_not_running", session_state: session.state });
        const prepared = (await c.query(`update ops.capability_agent_session set state='verification', candidate_kind=$2, candidate_evidence=$3::jsonb, prepared_at=now(), version=version+1 where id=$1 returning *`, [session.id, args.completion_kind, JSON.stringify(args.candidate_evidence)])).rows[0];
        const updated = (await c.query(`update ops.work_request set state='verification', updated_at=now(), version=version+1 where id=$1 returning *`, [row.id])).rows[0];
        await writeEvent(c, actor, "prepare-capability-project", "ops_work_request", row.id, { field: "state", old: { state: "in_progress", capability_agent_session_id: session.id }, new: { state: "verification", capability_agent_session_id: session.id, candidate_fingerprint: prepared.candidate_fingerprint }, human_quote: args.human_quote || null, idempotency_key: args.idempotency_key });
        return { ok: true, project: programRow(updated), capability_session: { id: prepared.id, state: prepared.state, candidate_fingerprint: prepared.candidate_fingerprint }, advanced: false };
      }),
    },
    "attest-capability-project": {
      write: true, humanOnly: true,
      description: "Persist an independent pass or fail against the frozen current candidate. The server rejects an executor attesting its own work and records both the evidence and source now, never at completion time.",
      inputSchema: { type: "object", additionalProperties: false, properties: { ...projectFields, capability_agent_session_id: { type: "string" }, outcome: { type: "string", enum: ["pass","fail"] }, verification_evidence_ref: { type: "string" }, source_ref: { type: "string" }, note: { type: "string" } }, required: ["idempotency_key","sequence","base_version","capability_agent_session_id","outcome","verification_evidence_ref","source_ref"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "attest-capability-project", args, async () => {
        requireFixedProgram(args, ToolError);
        const row = await requireCurrent(c, ToolError, args.sequence, args.base_version);
        if (row.state !== "verification") throw new ToolError({ error: "invalid_state_transition", from: row.state, to: "attested", required_from: "verification" });
        if (!textPresent(args.verification_evidence_ref) || !textPresent(args.source_ref)) throw new ToolError({ error: "attestation_evidence_incomplete", required: ["verification_evidence_ref","source_ref"] });
        const session = await loadSession(c, ToolError, args.capability_agent_session_id, row.id);
        if (session.state !== "verification" || !session.candidate_fingerprint) throw new ToolError({ error: "capability_agent_session_not_ready_for_attestation", session_state: session.state });
        if (actor.id === session.executor_actor_id) throw new ToolError({ error: "capability_self_attestation_forbidden", capability_agent_session_id: session.id });
        const attestation = (await c.query(`insert into ops.capability_verification (build_session_id, work_request_id, verifier_actor_id, outcome, verification_evidence_ref, source_ref, candidate_fingerprint, note) values ($1,$2,$3,$4,$5,$6,$7,$8) returning *`, [session.id, row.id, actor.id, args.outcome, args.verification_evidence_ref, args.source_ref, session.candidate_fingerprint, args.note || null])).rows[0];
        await writeEvent(c, actor, "attest-capability-project", "ops_work_request", row.id, { field: "capability_attestation", new: { capability_agent_session_id: session.id, attestation_id: attestation.id, outcome: attestation.outcome, candidate_fingerprint: session.candidate_fingerprint }, human_quote: args.human_quote || null, idempotency_key: args.idempotency_key });
        return { ok: true, attestation: { id: attestation.id, outcome: attestation.outcome, candidate_fingerprint: attestation.candidate_fingerprint }, advanced: false };
      }),
    },
    "complete-capability-project": {
      write: true, humanOnly: true,
      description: "Close only the current project from its frozen candidate plus a stored independent PASS attestation, then expose exactly the next ordered Work Request. It accepts no caller-supplied artifact, test or verifier references, and an executor cannot self-complete.",
      inputSchema: { type: "object", additionalProperties: false, properties: { ...projectFields, capability_agent_session_id: { type: "string" }, completion_kind: { type: "string", enum: COMPLETION_KINDS }, completion_evidence: completionEvidenceSchema }, required: ["idempotency_key","sequence","base_version","capability_agent_session_id","completion_kind","completion_evidence"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "complete-capability-project", args, async () => {
        requireFixedProgram(args, ToolError);
        const row = await requireCurrent(c, ToolError, args.sequence, args.base_version);
        if (row.state !== "verification") throw new ToolError({ error: "invalid_state_transition", from: row.state, to: "confirmed_closed", required_from: "verification" });
        if (!args.capability_agent_session_id) throw new ToolError({ error: "unbound_completion_evidence", hint: "completion needs the persisted capability_agent_session_id; arbitrary references never close this queue" });
        const supplied = args.completion_evidence || {};
        if (Object.keys(supplied).some(k => k !== "candidate_fingerprint"))
          throw new ToolError({ error: "caller_supplied_completion_evidence_forbidden", hint: "artifact, test, decision and verifier references were frozen during prepare; pass only the persisted candidate fingerprint" });
        const session = await loadSession(c, ToolError, args.capability_agent_session_id, row.id);
        if (session.state !== "verification" || session.candidate_kind !== args.completion_kind || !permittedCompletion(row.disposition, args.completion_kind)) throw new ToolError({ error: "prepared_candidate_does_not_match_completion", candidate_kind: session.candidate_kind, completion_kind: args.completion_kind });
        if (!textPresent(supplied.candidate_fingerprint) || supplied.candidate_fingerprint !== session.candidate_fingerprint)
          throw new ToolError({ error: "completion_candidate_fingerprint_mismatch", hint: "re-read the persisted capability session; completion cannot substitute a candidate" });
        if (actor.id === session.executor_actor_id) throw new ToolError({ error: "capability_self_completion_forbidden", capability_agent_session_id: session.id });
        // NO `for update` HERE, and that omission is the whole reason this queue could
        // never close. ops.capability_verification is immutable by design: migration
        // 0127 puts a before-update-or-delete trigger on it, and carr_writer is granted
        // only `insert, select`. PostgreSQL requires UPDATE privilege to take a row
        // lock, so `select ... for update` against this table raised permission denied
        // on EVERY completion attempt and surfaced to the caller as a bare
        // `internal error`. That is why the AI Engineering Suite read 0 of 51 complete
        // while six of its projects were demonstrably finished — the close verb had
        // never once run to the end.
        //
        // The lock was never needed. requireCurrent() already takes `for update of w`
        // on the queue-head work_request and loadSession() takes `for update of s` on
        // the session, so two concurrent completions of the same project serialize on
        // rows this role may actually lock. Granting UPDATE here instead would widen
        // the write surface of a deliberately append-only evidence table to buy a lock
        // that adds nothing (rule 5409731b).
        //
        // Proven by ops/capability-completion-gate.py, which fires as carr_writer
        // rather than as the owner. Migration 0127's own proof block ran as the
        // migration role, which is exactly why it never saw this.
        const pass = (await c.query(`select * from ops.capability_verification where build_session_id=$1 and work_request_id=$2 and outcome='pass' and candidate_fingerprint=$3 and verifier_actor_id <> $4 order by attested_at desc limit 1`, [session.id, row.id, session.candidate_fingerprint, session.executor_actor_id])).rows[0];
        if (!pass) throw new ToolError({ error: "independent_capability_pass_required", capability_agent_session_id: session.id });
        const persistedEvidence = { candidate: session.candidate_evidence, attestation: { id: pass.id, verifier_actor_id: pass.verifier_actor_id, verification_evidence_ref: pass.verification_evidence_ref, source_ref: pass.source_ref, attested_at: pass.attested_at, candidate_fingerprint: pass.candidate_fingerprint } };
        const updated = (await c.query(`update ops.work_request set state='confirmed_closed', completion_kind=$2, completion_evidence=$3::jsonb, verification_accepted_at=now(), verification_evidence_ref=$4, closed_at=now(), updated_at=now(), version=version+1 where id=$1 returning *`, [row.id, args.completion_kind, JSON.stringify(persistedEvidence), pass.id])).rows[0];
        await c.query(`update ops.capability_agent_session set state='completed', completed_at=now(), version=version+1 where id=$1`, [session.id]);
        await writeEvent(c, actor, "complete-capability-project", "ops_work_request", row.id, { field: "state", old: { state: "verification", capability_agent_session_id: session.id }, new: { state: "confirmed_closed", completion_kind: args.completion_kind, attestation_id: pass.id, candidate_fingerprint: session.candidate_fingerprint }, human_quote: args.human_quote || null, idempotency_key: args.idempotency_key });
        const later = await c.query(`select * from ops.work_request where program_key=$1 and program_ordinal>$2 order by program_ordinal limit 1 for update`, [DEFAULT_PROGRAM, row.program_ordinal]);
        const next = nextProjectState(row.program_ordinal, later.rows.map(r => ({ sequence: Number(r.program_ordinal), state: r.state })));
        const nextRow = later.rows[0] || null;
        if (nextRow) await writeEvent(c, actor, "complete-capability-project", "ops_work_request", nextRow.id, { field: "program_head", old: { current: false }, new: { current: true, predecessor_ref: row.ref }, idempotency_key: args.idempotency_key });
        return { ok: true, completed_project: programRow(updated), program_complete: next.completeProgram, next_project: programRow(nextRow), next_session_brief: sessionBrief(nextRow) };
      }),
    },
  };
}
