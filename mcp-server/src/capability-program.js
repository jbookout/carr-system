// One fixed, ordered AI capability program over the canonical ops.work_request
// lifecycle.  Project definitions and mutable state live in Postgres.  This
// module owns only the server-enforced read/transition boundary.

export const COMPLETION_KINDS = Object.freeze(["built", "extended", "adopted", "declined"]);
const DEFAULT_PROGRAM = "carr-ai-engineering-suite-v1";

const textPresent = value => typeof value === "string" && value.trim().length > 0;
const refsPresent = value => Array.isArray(value) && value.length > 0 && value.every(textPresent);

export function completionEvidenceError(kind, evidence) {
  if (!COMPLETION_KINDS.includes(kind))
    return { error: "invalid_completion_kind", allowed: COMPLETION_KINDS };
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

export function nextProjectState(currentSequence, laterRows) {
  if (!Array.isArray(laterRows) || laterRows.length === 0)
    return { completeProgram: true, nextSequence: null };
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
    ref: row.ref,
    sequence: Number(row.program_ordinal),
    title: row.title,
    state: row.state,
    version: Number(row.version),
    disposition: row.disposition,
    existing_status: row.existing_status,
    desired_outcome: row.desired_outcome,
    acceptance_criteria: row.acceptance_criteria || [],
    context: row.project_context || {},
    executor_actor: row.executor_actor || null,
    blocker_code: row.blocker_code || null,
    blocker_detail: row.blocker_detail || null,
    completion_kind: row.completion_kind || null,
    completion_evidence: row.completion_evidence || null,
  };
}

function sessionBrief(row) {
  if (!row) return null;
  const p = programRow(row);
  const c = p.context;
  return [
    `Build the current CARR AI Engineering Suite project #${p.sequence}: ${p.title}.`,
    `Canonical Work Request: ${p.ref}; base_version: ${p.version}; disposition: ${p.disposition}; state: ${p.state}.`,
    `Outcome: ${p.desired_outcome}`,
    `Scope: ${c.scope || "Use the canonical Work Request scope."}`,
    `Non-goals: ${(c.non_goals || []).join("; ") || "None recorded."}`,
    `Prerequisites: ${(c.prerequisites || []).join("; ") || "None."}`,
    `First deliverable: ${c.first_deliverable || "Follow the acceptance criteria."}`,
    `Acceptance: ${(p.acceptance_criteria || []).join("; ")}`,
    `Rollback/exit: ${c.rollback_exit || "Stop and report; do not broaden scope."}`,
    `Data/risk: ${c.data_risk || "Unknown — stop before using sensitive data."}; effort: ${c.effort || "unknown"}.`,
    `Existing evidence: ${(c.evidence || []).join("; ") || "none recorded"}.`,
    `Completion meaning: ${c.completion_definition || "accepted evidence and independent verification"}.`,
    "Work in an isolated worktree. Write acceptance tests first. Use the cheapest qualified executor. Never merge, deploy, mutate Production, communicate externally, spend, or mark this project complete from the build session. Return artifact/test references for an independent verifier and owner-governed completion call.",
  ].join("\n");
}

async function readCurrent(c, programKey, lock = false) {
  const suffix = lock ? " for update of w" : "";
  const r = await c.query(
    `select w.* from ops.work_request w
      where w.program_key=$1 and w.state <> 'confirmed_closed'
      order by w.program_ordinal limit 1${suffix}`,
    [programKey]);
  return r.rows[0] || null;
}

async function requireCurrent(c, ToolError, programKey, sequence, baseVersion) {
  const row = await readCurrent(c, programKey, true);
  if (!row) throw new ToolError({ error: "capability_program_complete", program_key: programKey });
  if (Number(row.program_ordinal) !== Number(sequence))
    throw new ToolError({ error: "out_of_order_project", current_sequence: Number(row.program_ordinal), requested_sequence: Number(sequence) });
  if (Number(row.version) !== Number(baseVersion))
    throw new ToolError({ error: "version_conflict", current_version: Number(row.version), base_version: Number(baseVersion), resolution: "re-read capability-program and retry against the current project version; never overwrite blind" });
  return row;
}

const completionEvidenceSchema = {
  type: "object", additionalProperties: false,
  properties: {
    artifact_ref: { type: "string" },
    acceptance_test_refs: { type: "array", items: { type: "string" } },
    independent_verifier_ref: { type: "string" },
    decision_ref: { type: "string" },
    rollback_ref: { type: "string" },
    release_ref: { type: "string" },
    note: { type: "string" },
  },
};

export function capabilityProgramTools({ withEnvelope, writeEvent, ToolError }) {
  return {
    "capability-program": {
      write: false,
      description: "Read the one ordered CARR AI Engineering Suite from canonical Work Requests. Returns the current project, complete session brief, progress counts and optional project detail. The first not-confirmed-closed item is current; blocked/failed/needs-Joe remains current and can never be skipped by a later row. This read grants no build, merge, deploy, Production or external authority.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        program_key: { type: "string" },
        sequence: { type: "integer" },
        include_all: { type: "boolean" },
      } },
      handler: async (c, _actor, args) => {
        const key = args.program_key || DEFAULT_PROGRAM;
        const rows = await c.query(
          `select * from ops.work_request where program_key=$1 order by program_ordinal`, [key]);
        if (!rows.rows.length) throw new ToolError({ error: "capability_program_not_found", program_key: key });
        const current = rows.rows.find(row => row.state !== "confirmed_closed") || null;
        const requested = args.sequence === undefined ? current
          : rows.rows.find(row => Number(row.program_ordinal) === Number(args.sequence));
        if (args.sequence !== undefined && !requested)
          throw new ToolError({ error: "capability_project_not_found", program_key: key, sequence: args.sequence });
        return {
          program_key: key,
          total: rows.rows.length,
          completed: rows.rows.filter(row => row.state === "confirmed_closed").length,
          program_complete: !current,
          current: programRow(current),
          requested: programRow(requested),
          session_brief: sessionBrief(requested),
          projects: args.include_all ? rows.rows.map(programRow) : undefined,
        };
      },
    },

    "start-capability-project": {
      write: true, humanOnly: true,
      description: "Start only the current capability project after a read-only build session exists. Atomically applies the canonical ready→claimed→in_progress guards, binds the executor and session evidence, and never starts a later project. This verb does not run code, create a worktree, merge, deploy, spend, or communicate.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" }, program_key: { type: "string" },
        sequence: { type: "integer" }, base_version: { type: "integer" },
        executor_actor: { type: "string" }, session_ref: { type: "string" },
        human_quote: { type: "string" },
      }, required: ["idempotency_key", "sequence", "base_version", "executor_actor", "session_ref"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "start-capability-project", args, async () => {
        const key = args.program_key || DEFAULT_PROGRAM;
        const row = await requireCurrent(c, ToolError, key, args.sequence, args.base_version);
        if (row.state !== "ready")
          throw new ToolError({ error: "invalid_state_transition", from: row.state, to: "in_progress", required_from: "ready" });
        const updated = (await c.query(
          `update ops.work_request
              set state='in_progress', executor_actor=$2, claimed_at=now(), started_at=now(),
                  updated_at=now(), version=version+1,
                  project_context=project_context || jsonb_build_object('active_session_ref',$3::text)
            where id=$1 returning *`, [row.id, args.executor_actor, args.session_ref])).rows[0];
        await writeEvent(c, actor, "start-capability-project", "ops_work_request", row.id, {
          field: "state", old: { state: "ready" }, new: { state: "in_progress", executor_actor: args.executor_actor, session_ref: args.session_ref, transitions: ["ready_to_claimed", "claimed_to_in_progress"] },
          human_quote: args.human_quote || null, idempotency_key: args.idempotency_key,
        });
        return { ok: true, project: programRow(updated), session_brief: sessionBrief(updated) };
      }),
    },

    "prepare-capability-project": {
      write: true, humanOnly: true,
      description: "Submit candidate work for independent verification. Only the current in-progress project may enter verification, and the candidate must name an artifact plus actual acceptance-test evidence. This is not acceptance and does not advance the queue.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" }, program_key: { type: "string" },
        sequence: { type: "integer" }, base_version: { type: "integer" },
        artifact_ref: { type: "string" }, acceptance_test_refs: { type: "array", items: { type: "string" } },
        human_quote: { type: "string" },
      }, required: ["idempotency_key", "sequence", "base_version", "artifact_ref", "acceptance_test_refs"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "prepare-capability-project", args, async () => {
        const key = args.program_key || DEFAULT_PROGRAM;
        const row = await requireCurrent(c, ToolError, key, args.sequence, args.base_version);
        if (row.state !== "in_progress")
          throw new ToolError({ error: "invalid_state_transition", from: row.state, to: "verification", required_from: "in_progress" });
        if (!textPresent(args.artifact_ref) || !refsPresent(args.acceptance_test_refs))
          throw new ToolError({ error: "candidate_evidence_incomplete", required: ["artifact_ref", "acceptance_test_refs"] });
        const candidate = { artifact_ref: args.artifact_ref, acceptance_test_refs: args.acceptance_test_refs };
        const updated = (await c.query(
          `update ops.work_request set state='verification', updated_at=now(), version=version+1,
             project_context=project_context || jsonb_build_object('candidate_evidence',$2::jsonb)
            where id=$1 returning *`, [row.id, JSON.stringify(candidate)])).rows[0];
        await writeEvent(c, actor, "prepare-capability-project", "ops_work_request", row.id, {
          field: "state", old: { state: "in_progress" }, new: { state: "verification", candidate_evidence: candidate },
          human_quote: args.human_quote || null, idempotency_key: args.idempotency_key,
        });
        return { ok: true, project: programRow(updated), advanced: false };
      }),
    },

    "complete-capability-project": {
      write: true, humanOnly: true,
      description: "Accept independently verified completion of only the current project and atomically slide the next Work Request to the program head. Requires the exact current base_version and a conditional evidence bundle. Built/extended work needs an artifact, actual acceptance tests and independent verifier; adoption also needs owner decision and rollback; decline needs owner decision and independent review. A build session may never call this for itself. It never merges, deploys or communicates.",
      inputSchema: { type: "object", additionalProperties: false, properties: {
        idempotency_key: { type: "string" }, program_key: { type: "string" },
        sequence: { type: "integer" }, base_version: { type: "integer" },
        completion_kind: { type: "string", enum: COMPLETION_KINDS },
        completion_evidence: completionEvidenceSchema,
        human_quote: { type: "string" },
      }, required: ["idempotency_key", "sequence", "base_version", "completion_kind", "completion_evidence"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "complete-capability-project", args, async () => {
        const key = args.program_key || DEFAULT_PROGRAM;
        const row = await requireCurrent(c, ToolError, key, args.sequence, args.base_version);
        if (row.state !== "verification")
          throw new ToolError({ error: "invalid_state_transition", from: row.state, to: "confirmed_closed", required_from: "verification" });
        if (!permittedCompletion(row.disposition, args.completion_kind))
          throw new ToolError({ error: "completion_disposition_mismatch", disposition: row.disposition, completion_kind: args.completion_kind, allowed: [({ build: "built", extend: "extended", adopt: "adopted" })[row.disposition], "declined"].filter(Boolean) });
        const evidenceError = completionEvidenceError(args.completion_kind, args.completion_evidence);
        if (evidenceError) throw new ToolError(evidenceError);
        const updated = (await c.query(
          `update ops.work_request
              set state='confirmed_closed', completion_kind=$2, completion_evidence=$3::jsonb,
                  verification_accepted_at=now(), verification_evidence_ref=$4,
                  closed_at=now(), updated_at=now(), version=version+1
            where id=$1 returning *`,
          [row.id, args.completion_kind, JSON.stringify(args.completion_evidence), args.completion_evidence.independent_verifier_ref])).rows[0];
        await writeEvent(c, actor, "complete-capability-project", "ops_work_request", row.id, {
          field: "state", old: { state: "verification" }, new: { state: "confirmed_closed", completion_kind: args.completion_kind, evidence: args.completion_evidence },
          human_quote: args.human_quote || null, idempotency_key: args.idempotency_key,
        });
        const later = await c.query(
          `select * from ops.work_request where program_key=$1 and program_ordinal>$2 order by program_ordinal limit 1 for update`,
          [key, row.program_ordinal]);
        const next = nextProjectState(row.program_ordinal, later.rows.map(r => ({ sequence: Number(r.program_ordinal), state: r.state })));
        const nextRow = later.rows[0] || null;
        if (nextRow) await writeEvent(c, actor, "complete-capability-project", "ops_work_request", nextRow.id, {
          field: "program_head", old: { current: false }, new: { current: true, predecessor_ref: row.ref },
          idempotency_key: args.idempotency_key,
        });
        return {
          ok: true, completed_project: programRow(updated), program_complete: next.completeProgram,
          next_project: programRow(nextRow), next_session_brief: sessionBrief(nextRow),
        };
      }),
    },
  };
}
