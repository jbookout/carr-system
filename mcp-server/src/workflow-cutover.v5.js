// DoctorCRE V5-R02 (Workflow cutover, caller migration and retirement
// readiness). Store: migrations/0590_v5_r02_workflow_cutover_and_caller_
// inventory.sql.
//
// Q116 (recovered 2026-09-24): "avoid uncontrolled dual writes, and migrate
// one workflow at a time. The steps are: read legacy state, build the new
// projection, compare outcomes in shadow, establish one write authority, cut
// over, monitor, preserve a bounded recovery path, then retire the old
// workflow and its instructions." `open-workflow-cutover-plan` starts a plan
// at stage=read_legacy; `advance-workflow-cutover-stage` moves it forward
// exactly one step at a time through recovery_ready, requiring fresh
// accepted `ops.workflow_acceptance` evidence (the same store
// `accept-workflow` writes to) at shadow_compare/single_write_authority/
// cutover; `retire-workflow-cutover-plan` is the ONLY door to stage=retired,
// Joe-only, and requires an existing `ops.legacy_schedule_disable_receipt`
// row -- i.e. an already-performed `disable-legacy-schedule` call. It never
// disables a native scheduler itself.
//
// Q153 (recovered): stale-plan supersession -- `open-workflow-cutover-plan`
// automatically supersedes any prior ACTIVE plan for the same workflow
// identity, in the same transaction, and records why. Q153 also added an
// explicit per-slice completion-marking requirement: `mark-slice-completion`
// / `read-slice-completion` are that door, generic across every V5 slice.
//
// Q157 (recovered): a workflow or caller must never read back "done" from
// hidden or absent evidence. `ops.record_workflow_caller` refuses
// status=done without a non-null evidence_ref (enforced in the migration).
// `workflow-cutover-board` goes one step further: it independently re-checks
// the V5-F09/A01 workflow-truth census
// (lib/control_plane_workflow_truth_reader.py) at read time, and while that
// reader answers available:false (its documented state today, pending a
// separate durable signed census store), the board reports
// census.state='unknown' rather than promoting any cached or inferred
// figure to "done".
//
// NOTHING HERE RETIRES A REAL PRODUCTION WORKFLOW. `retire-workflow-cutover-
// plan` requires concrete already-existing evidence rows; this slice adds no
// script and no call that manufactures that evidence for any live workflow.

const CUTOVER_STAGES = Object.freeze([
  "read_legacy", "build_projection", "shadow_compare", "single_write_authority",
  "cutover", "monitor", "recovery_ready", "retired",
]);
const CALLER_KINDS = Object.freeze(["script", "verb", "worker_route", "job_definition", "external"]);
const CALLER_STATUSES = Object.freeze(["remaining", "done", "blocked", "superseded", "retired"]);
const SLICE_STATUSES = Object.freeze(["in_progress", "complete", "blocked"]);

// The verb handlers run in TWO runtimes: the Node MCP server (a real
// filesystem, node:child_process, a repo checkout) AND the deployed
// Cloudflare Worker (workerd -- no filesystem, no child_process, no
// import.meta.url-relative repo path; those APIs either do not exist or
// throw at MODULE LOAD, not at call time, which is what made the previous
// version of this file crash the Worker's boot on every deploy: top-level
// `fileURLToPath(import.meta.url)` ran before any verb was ever invoked).
//
// The V5-F09/A01 workflow-truth census reader
// (lib/control_plane_workflow_truth_reader.py) is a Python module reachable
// only from the Node process, and only ever answers `available: false`
// today regardless of runtime (a separate, not-yet-landed durable
// signed-census-store PR is what would ever flip it to true). So this
// module never spawns a subprocess at all: a Worker-safe constant read gets
// the exact same fail-closed answer the Node-side subprocess call would
// have returned, without any Node-only API existing anywhere in this file's
// module scope or call graph. When the census store lands, this becomes a
// real read again (over HTTP/fetch, which workerd supports) -- Q157 still
// requires it to fail closed until then, in EITHER runtime.
function readWorkflowTruthCensus() {
  return { available: false, reason: "census_route_not_in_worker" };
}

export function workflowCutoverTools({ withEnvelope, ToolError }) {
  return {
    "open-workflow-cutover-plan": {
      write: true,
      description: "Q116 step 1: open a workflow-migration plan at stage=read_legacy. Q153 stale-plan supersession: any existing ACTIVE plan for the same (workflow_key, workflow_version) is superseded first, in the same transaction, and the reason is recorded -- never left to coexist with the new plan. recovery_plan (Q116's bounded recovery path) is required up front, not deferred to the retire step. Idempotent on idempotency_key.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          workflow_key: { type: "string", minLength: 1 },
          workflow_version: { type: "integer", minimum: 1 },
          recovery_plan: { type: "string", minLength: 1 },
        },
        required: ["idempotency_key", "workflow_key", "workflow_version", "recovery_plan"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "open-workflow-cutover-plan", args, async () => {
        const row = (await c.query(
          "select * from ops.open_workflow_cutover_plan($1,$2,$3,$4,$5)",
          [args.workflow_key, args.workflow_version, args.recovery_plan, args.idempotency_key, actor.slug || null],
        )).rows[0];
        if (!row) throw new ToolError({ error: "workflow_cutover_plan_open_refused" });
        return { ok: true, plan_id: row.id, workflow_key: row.workflow_key, workflow_version: row.workflow_version,
          stage: row.stage, status: row.status, superseded_by: row.superseded_by };
      }),
    },

    "advance-workflow-cutover-stage": {
      write: true,
      description: "Q116: advance an active cutover plan exactly one step forward through read_legacy -> build_projection -> shadow_compare -> single_write_authority -> cutover -> monitor -> recovery_ready. Refuses to skip a stage, refuses a plan that is not active, and refuses shadow_compare/single_write_authority/cutover without an evidence_ref pointing at an already-accepted ops.workflow_acceptance row (the same store accept-workflow writes) for the exact workflow identity -- 'shadow parity and single-writer checks pass' is verified against that row, not asserted. Cannot reach stage=retired; see retire-workflow-cutover-plan.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          plan_id: { type: "string" },
          to_stage: { type: "string", enum: [...CUTOVER_STAGES] },
          evidence_ref: { type: ["string", "null"], description: "For shadow_compare/single_write_authority/cutover: the ops.workflow_acceptance.id of an accepted shadow (shadow_compare) or canary (single_write_authority, cutover) acceptance for this workflow." },
          reason: { type: "string", minLength: 1 },
        },
        required: ["idempotency_key", "plan_id", "to_stage", "reason"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "advance-workflow-cutover-stage", args, async () => {
        if (args.to_stage === "retired")
          throw new ToolError({ error: "retired_stage_requires_retire_door" });
        let row;
        try {
          row = (await c.query(
            "select * from ops.advance_workflow_cutover_stage($1,$2,$3,$4,$5,$6)",
            [args.plan_id, args.to_stage, args.evidence_ref ?? null, args.reason, args.idempotency_key, actor.slug || null],
          )).rows[0];
        } catch (err) {
          throw new ToolError({ error: "workflow_cutover_stage_advance_refused", detail: String(err.message || err) });
        }
        if (!row) throw new ToolError({ error: "workflow_cutover_plan_not_found" });
        return { ok: true, plan_id: row.id, stage: row.stage, status: row.status };
      }),
    },

    "retire-workflow-cutover-plan": {
      write: true, authorityOnly: true,
      description: "Q116 last step, Joe-only: the ONLY door to stage=retired. Requires the plan already at stage=recovery_ready and an existing ops.legacy_schedule_disable_receipt row (from a prior, already-performed disable-legacy-schedule call) for the same workflow_key. Never performs a native disable itself. Also independently re-reads the V5-F09/A01 workflow-truth census; while that route answers unavailable (its documented state today), this verb still relies only on the disable receipt and accepted-evidence chain already enforced in the store -- the census read is surfaced in the response as an additional, non-authoritative signal, never substituted for the receipt.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          plan_id: { type: "string" },
          disable_receipt_id: { type: "string" },
          reason: { type: "string", minLength: 1 },
        },
        required: ["idempotency_key", "plan_id", "disable_receipt_id", "reason"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "retire-workflow-cutover-plan", args, async () => {
        const census = readWorkflowTruthCensus();
        let row;
        try {
          row = (await c.query(
            "select * from ops.retire_workflow_cutover_plan($1,$2,$3,$4,$5)",
            [args.plan_id, args.disable_receipt_id, args.reason, args.idempotency_key, actor.slug || null],
          )).rows[0];
        } catch (err) {
          throw new ToolError({ error: "workflow_cutover_retire_refused", detail: String(err.message || err) });
        }
        if (!row) throw new ToolError({ error: "workflow_cutover_plan_not_found" });
        return { ok: true, plan_id: row.id, stage: row.stage, status: row.status, census };
      }),
    },

    "record-workflow-caller": {
      write: true,
      description: "Caller inventory: upsert one caller's migration status for one workflow identity (done/remaining/blocked/superseded/retired). status=done is refused server-side without a non-null evidence_ref, and status=blocked is refused without blocked_reason -- Q157, a caller cannot read back done from silence.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          workflow_key: { type: "string", minLength: 1 },
          workflow_version: { type: "integer", minimum: 1 },
          caller_locator: { type: "string", minLength: 1 },
          caller_kind: { type: "string", enum: [...CALLER_KINDS] },
          status: { type: "string", enum: [...CALLER_STATUSES] },
          blocked_reason: { type: ["string", "null"] },
          evidence_ref: { type: ["string", "null"] },
        },
        required: ["idempotency_key", "workflow_key", "workflow_version", "caller_locator", "caller_kind", "status"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "record-workflow-caller", args, async () => {
        let row;
        try {
          row = (await c.query(
            "select * from ops.record_workflow_caller($1,$2,$3,$4,$5,$6,$7,$8)",
            [args.workflow_key, args.workflow_version, args.caller_locator, args.caller_kind, args.status,
             args.blocked_reason ?? null, args.evidence_ref ?? null, actor.slug || null],
          )).rows[0];
        } catch (err) {
          throw new ToolError({ error: "workflow_caller_record_refused", detail: String(err.message || err) });
        }
        if (!row) throw new ToolError({ error: "workflow_caller_refused" });
        return { ok: true, id: row.id, workflow_key: row.workflow_key, workflow_version: row.workflow_version,
          caller_locator: row.caller_locator, status: row.status };
      }),
    },

    "workflow-cutover-board": {
      write: false,
      description: "Read the active cutover plan, its stage history, and caller counts (done/remaining/blocked/superseded/retired) for one workflow identity, plus an independently fail-closed read of the V5-F09/A01 workflow-truth census. Per Q157, when that census route answers unavailable (its documented state today), this board reports census.state='unknown' -- it never promotes a cached or inferred figure to 'done'.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          workflow_key: { type: "string", minLength: 1 },
          workflow_version: { type: "integer", minimum: 1 },
        },
        required: ["workflow_key", "workflow_version"],
      },
      handler: async (c, _actor, args) => {
        const row = (await c.query(
          "select ops.workflow_cutover_board($1,$2) as board",
          [args.workflow_key, args.workflow_version],
        )).rows[0];
        const board = row?.board;
        if (!board) throw new ToolError({ error: "workflow_cutover_board_unavailable" });
        const census = readWorkflowTruthCensus();
        return {
          ok: true, ...board,
          census: census.available === true
            ? { state: "read", ...census }
            : { state: "unknown", reason: census.reason },
        };
      },
    },

    "mark-slice-completion": {
      write: true,
      description: "Q153: append one explicit completion mark for one slice_id, so a future session reads what's done and what's left without re-deriving it. status='complete' is refused server-side unless every element of criteria_receipt (one per checkable_done criterion: {criterion, evidence, pass}) carries a non-empty criterion, non-empty evidence, and pass=true -- an agent cannot mark a slice complete by asserting it. Idempotent on idempotency_key.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          slice_id: { type: "string", minLength: 1 },
          status: { type: "string", enum: [...SLICE_STATUSES] },
          criteria_receipt: {
            type: "array", minItems: 1,
            items: {
              type: "object", additionalProperties: false,
              properties: {
                criterion: { type: "string", minLength: 1 },
                evidence: { type: "string", minLength: 1 },
                pass: { type: "boolean" },
              },
              required: ["criterion", "evidence", "pass"],
            },
          },
          reason: { type: ["string", "null"] },
        },
        required: ["idempotency_key", "slice_id", "status", "criteria_receipt"],
      },
      handler: async (c, actor, args) => withEnvelope(c, actor, "mark-slice-completion", args, async () => {
        let row;
        try {
          row = (await c.query(
            "select * from ops.mark_slice_completion($1,$2,$3,$4,$5,$6)",
            [args.slice_id, args.status, JSON.stringify(args.criteria_receipt), args.reason ?? null,
             args.idempotency_key, actor.slug || null],
          )).rows[0];
        } catch (err) {
          throw new ToolError({ error: "slice_completion_mark_refused", detail: String(err.message || err) });
        }
        if (!row) throw new ToolError({ error: "slice_completion_mark_refused" });
        return { ok: true, id: row.id, slice_id: row.slice_id, status: row.status, created_at: row.created_at };
      }),
    },

    "read-slice-completion": {
      write: false,
      description: "Q153: read the current (most recent) explicit completion mark for one slice_id, or complete:false/no-mark-yet when the slice has never been marked.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: { slice_id: { type: "string", minLength: 1 } },
        required: ["slice_id"],
      },
      handler: async (c, _actor, args) => {
        const row = (await c.query(
          "select * from ops.read_slice_completion($1)",
          [args.slice_id],
        )).rows[0];
        if (!row || !row.slice_id)
          return { ok: true, slice_id: args.slice_id, marked: false, status: null, criteria_receipt: null };
        return { ok: true, slice_id: row.slice_id, marked: true, status: row.status,
          criteria_receipt: row.criteria_receipt, reason: row.reason, created_at: row.created_at };
      },
    },
  };
}
