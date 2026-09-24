import test from "node:test";
import assert from "node:assert/strict";
import { ToolError, executeRegisteredTool, TOOLS } from "../src/tools.js";

const AGENT = { id: "10000000-0000-0000-0000-000000000021", slug: "codex", human: false, via: "test" };
const AUTHORITY_AGENT = { id: "10000000-0000-0000-0000-000000000022", slug: "joe", human: true, via: "test" };

async function rejected(fn) {
  try { await fn(); assert.fail("expected refusal"); }
  catch (e) {
    if (e instanceof ToolError) return e.payload;
    throw e;
  }
}

// A minimal fake DB standing in for migrations/0593's five write doors and
// two read doors, plus the two upstream stores retire-workflow-cutover-plan
// composes: ops.workflow_acceptance (accept-workflow's store) and
// ops.legacy_schedule_disable_receipt (disable-legacy-schedule's store).
// Mirrors resource-observation.test.mjs's fake shape (mcp-server/test/
// resource-observation.test.mjs): match on the leading SQL text, keep state
// in plain JS structures, refuse the same way the real SECURITY DEFINER
// functions refuse.
class WorkflowCutoverFake {
  constructor() {
    this.calls = []; this.toolCalls = new Map();
    this.plans = new Map(); this.transitions = []; this.callers = new Map();
    this.slices = [];
    this.acceptances = new Map(); // id -> {workflow_key, workflow_version, mode, status}
    this.disableReceipts = new Map(); // id -> {workflow_key}
    this._planSeq = 0; this._callerSeq = 0; this._sliceSeq = 0;
  }

  seedAcceptance(id, row) { this.acceptances.set(id, row); }
  seedDisableReceipt(id, row) { this.disableReceipts.set(id, row); }

  async query(text, params = []) {
    const sql = text.replace(/\s+/g, " ").trim();
    this.calls.push({ sql, params });

    if (sql.startsWith("select request_hash, response")) {
      const row = this.toolCalls.get(params[0]);
      return { rows: row ? [row] : [] };
    }
    if (sql.startsWith("insert into tool_call")) {
      this.toolCalls.set(params[0], { request_hash: params[3], response: JSON.parse(params[4]) });
      return { rows: [] };
    }
    if (sql.startsWith("insert into event")) return { rows: [] };

    if (sql.includes("ops.open_workflow_cutover_plan")) {
      const [workflowKey, workflowVersion, recoveryPlan, idempotencyKey] = params;
      const existing = [...this.plans.values()].find(p => p.idempotency_key === idempotencyKey);
      if (existing) return { rows: [existing] };
      if (!workflowKey) throw new Error("workflow_key_required");
      if (!recoveryPlan) throw new Error("recovery_plan_required");
      const prior = [...this.plans.values()]
        .find(p => p.workflow_key === workflowKey && p.workflow_version === workflowVersion && p.status === "active");
      if (prior) { prior.status = "superseded"; prior.supersede_reason = "superseded by a new open"; }
      this._planSeq += 1;
      const id = `50000000-0000-0000-0000-${String(this._planSeq).padStart(12, "0")}`;
      const row = { id, workflow_key: workflowKey, workflow_version: workflowVersion, stage: "read_legacy",
        status: "active", recovery_plan: recoveryPlan, superseded_by: null, idempotency_key: idempotencyKey };
      this.plans.set(id, row);
      if (prior) prior.superseded_by = id;
      return { rows: [row] };
    }

    if (sql.includes("ops.advance_workflow_cutover_stage")) {
      const [planId, toStage, evidenceRef, reason, idempotencyKey] = params;
      const replayed = this.transitions.find(t => t.idempotency_key === idempotencyKey);
      if (replayed) return { rows: [this.plans.get(replayed.plan_id)] };
      if (toStage === "retired") throw new Error("retired_stage_requires_retire_door");
      if (!reason) throw new Error("reason_required");
      const plan = this.plans.get(planId);
      if (!plan) return { rows: [] };
      if (plan.status !== "active") throw new Error("workflow_cutover_plan_not_active");
      const stages = ["read_legacy", "build_projection", "shadow_compare", "single_write_authority",
        "cutover", "monitor", "recovery_ready"];
      const fromIdx = stages.indexOf(plan.stage);
      const toIdx = stages.indexOf(toStage);
      if (toIdx < 0) throw new Error("workflow_cutover_stage_invalid");
      if (toIdx !== fromIdx + 1) throw new Error("workflow_cutover_stage_not_sequential");
      if (["shadow_compare", "single_write_authority", "cutover"].includes(toStage)) {
        if (!evidenceRef) throw new Error(`evidence_ref_required_for_stage: ${toStage}`);
        const acc = this.acceptances.get(evidenceRef);
        const wantMode = toStage === "shadow_compare" ? "shadow" : "canary";
        if (!acc || acc.workflow_key !== plan.workflow_key || acc.workflow_version !== plan.workflow_version ||
            acc.mode !== wantMode || acc.status !== "accepted")
          throw new Error(`${wantMode}_acceptance_evidence_not_found_or_not_accepted`);
      }
      plan.stage = toStage;
      this.transitions.push({ plan_id: planId, from_stage: stages[fromIdx], to_stage: toStage,
        evidence_ref: evidenceRef, reason, idempotency_key: idempotencyKey });
      return { rows: [plan] };
    }

    if (sql.includes("ops.retire_workflow_cutover_plan")) {
      const [planId, disableReceiptId, reason, idempotencyKey, , censusAvailable] = params;
      const replayed = this.transitions.find(t => t.idempotency_key === idempotencyKey);
      if (replayed) return { rows: [this.plans.get(replayed.plan_id)] };
      if (!reason) throw new Error("reason_required");
      if (!disableReceiptId) throw new Error("disable_receipt_id_required");
      const plan = this.plans.get(planId);
      if (!plan) return { rows: [] };
      if (plan.status !== "active") throw new Error("workflow_cutover_plan_not_active");
      if (plan.stage !== "recovery_ready") throw new Error("workflow_cutover_plan_not_recovery_ready");
      const receipt = this.disableReceipts.get(disableReceiptId);
      if (!receipt) throw new Error("legacy_schedule_disable_receipt_not_found");
      if (receipt.workflow_key !== plan.workflow_key) throw new Error("legacy_schedule_disable_receipt_workflow_mismatch");
      // P1 fix (PR #1245 review, item 6 / Q157): mirrors the real
      // ops.retire_workflow_cutover_plan's p_census_available check, ordered
      // last so the more specific errors above still win when those are
      // what's actually wrong.
      if (censusAvailable !== true) throw new Error("workflow_cutover_retire_refused_census_unknown");
      plan.stage = "retired";
      this.transitions.push({ plan_id: planId, from_stage: "recovery_ready", to_stage: "retired",
        evidence_ref: disableReceiptId, reason, idempotency_key: idempotencyKey });
      return { rows: [plan] };
    }

    if (sql.includes("ops.record_workflow_caller")) {
      const [workflowKey, workflowVersion, callerLocator, callerKind, status, blockedReason, evidenceRef] = params;
      if (!workflowKey) throw new Error("workflow_key_required");
      if (!callerLocator) throw new Error("caller_locator_required");
      if (status === "done" && !evidenceRef) throw new Error("caller_done_requires_evidence_ref");
      if (status === "blocked" && !blockedReason) throw new Error("caller_blocked_requires_reason");
      const key = `${workflowKey}::${workflowVersion}::${callerLocator}`;
      this._callerSeq += 1;
      const id = this.callers.get(key)?.id ?? `51000000-0000-0000-0000-${String(this._callerSeq).padStart(12, "0")}`;
      const row = { id, workflow_key: workflowKey, workflow_version: workflowVersion, caller_locator: callerLocator,
        caller_kind: callerKind, status, blocked_reason: blockedReason, evidence_ref: evidenceRef };
      this.callers.set(key, row);
      return { rows: [row] };
    }

    if (sql.includes("ops.workflow_cutover_board")) {
      const [workflowKey, workflowVersion] = params;
      const plan = [...this.plans.values()]
        .find(p => p.workflow_key === workflowKey && p.workflow_version === workflowVersion && p.status === "active");
      const callers = [...this.callers.values()]
        .filter(c => c.workflow_key === workflowKey && c.workflow_version === workflowVersion);
      const counts = {};
      for (const c of callers) counts[c.status] = (counts[c.status] ?? 0) + 1;
      const board = {
        schema: "doctorcre-v5-r02-workflow-cutover-board.v1",
        workflow_key: workflowKey, workflow_version: workflowVersion,
        plan: plan ? { id: plan.id, stage: plan.stage, status: plan.status, recovery_plan: plan.recovery_plan,
          superseded_by: plan.superseded_by } : null,
        stage_history: this.transitions.filter(t => plan && t.plan_id === plan.id),
        caller_counts: counts,
        callers: callers.map(c => ({ caller_locator: c.caller_locator, caller_kind: c.caller_kind,
          status: c.status, blocked_reason: c.blocked_reason, evidence_ref: c.evidence_ref })),
      };
      return { rows: [{ board }] };
    }

    if (sql.includes("ops.mark_slice_completion")) {
      const [sliceId, status, criteriaReceiptJson, reason, idempotencyKey] = params;
      const existing = this.slices.find(s => s.idempotency_key === idempotencyKey);
      if (existing) return { rows: [existing] };
      if (!sliceId) throw new Error("slice_id_required");
      const criteria = JSON.parse(criteriaReceiptJson);
      if (!Array.isArray(criteria) || criteria.length === 0)
        throw new Error("criteria_receipt_required_nonempty_array");
      const allProven = criteria.every(el => el.pass === true && el.criterion && el.evidence);
      if (status === "complete" && !allProven)
        throw new Error("slice_completion_complete_requires_every_criterion_proven");
      this._sliceSeq += 1;
      const row = { id: `52000000-0000-0000-0000-${String(this._sliceSeq).padStart(12, "0")}`,
        slice_id: sliceId, status, criteria_receipt: criteria, reason, idempotency_key: idempotencyKey,
        created_at: "2026-09-24T12:00:00Z" };
      this.slices.push(row);
      return { rows: [row] };
    }

    if (sql.includes("ops.read_slice_completion")) {
      const [sliceId] = params;
      const rows = this.slices.filter(s => s.slice_id === sliceId);
      const latest = rows.at(-1);
      return { rows: latest ? [latest] : [{}] };
    }

    throw new Error(`WorkflowCutoverFake: unhandled query: ${sql}`);
  }
}

test("open-workflow-cutover-plan starts at read_legacy and requires a stated recovery plan", async () => {
  const client = new WorkflowCutoverFake();
  const result = await executeRegisteredTool(client, AGENT, "open-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000001",
    workflow_key: "lead-board-nightly", workflow_version: 1,
    recovery_plan: "revert to the legacy launchd job by re-enabling com.carr.lead-board-nightly",
  });
  assert.equal(result.ok, true);
  assert.equal(result.stage, "read_legacy");
  assert.equal(result.status, "active");
});

test("open-workflow-cutover-plan refuses a missing recovery plan (Q116: a bounded recovery path is required up front)", async () => {
  const client = new WorkflowCutoverFake();
  await assert.rejects(
    () => executeRegisteredTool(client, AGENT, "open-workflow-cutover-plan", {
      idempotency_key: "60000000-0000-0000-0000-000000000002",
      workflow_key: "lead-board-nightly", workflow_version: 1, recovery_plan: "",
    }),
    (e) => e instanceof ToolError && (e.payload.error === "missing_required" || e.payload.error === "value_too_short"),
  );
});

test("Q153 stale-plan supersession: opening a second plan for the same workflow supersedes the first", async () => {
  const client = new WorkflowCutoverFake();
  const first = await executeRegisteredTool(client, AGENT, "open-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000003",
    workflow_key: "renewal-feed", workflow_version: 1, recovery_plan: "re-enable legacy schedule",
  });
  const second = await executeRegisteredTool(client, AGENT, "open-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000004",
    workflow_key: "renewal-feed", workflow_version: 1, recovery_plan: "re-enable legacy schedule v2",
  });
  assert.notEqual(second.plan_id, first.plan_id);
  const firstRow = client.plans.get(first.plan_id);
  assert.equal(firstRow.status, "superseded");
  assert.equal(firstRow.superseded_by, second.plan_id);
  const secondRow = client.plans.get(second.plan_id);
  assert.equal(secondRow.status, "active");
});

test("Q116: advance-workflow-cutover-stage refuses to skip a stage", async () => {
  const client = new WorkflowCutoverFake();
  const opened = await executeRegisteredTool(client, AGENT, "open-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000005",
    workflow_key: "calendar-prebrief", workflow_version: 1, recovery_plan: "revert",
  });
  const payload = await rejected(() => executeRegisteredTool(client, AGENT, "advance-workflow-cutover-stage", {
    idempotency_key: "60000000-0000-0000-0000-000000000006",
    plan_id: opened.plan_id, to_stage: "cutover", reason: "trying to skip ahead",
  }));
  assert.match(payload.detail, /workflow_cutover_stage_not_sequential/);
});

test("Q116: advance to shadow_compare requires an accepted shadow acceptance for the exact workflow identity", async () => {
  const client = new WorkflowCutoverFake();
  const opened = await executeRegisteredTool(client, AGENT, "open-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000007",
    workflow_key: "calendar-prebrief", workflow_version: 1, recovery_plan: "revert",
  });
  await executeRegisteredTool(client, AGENT, "advance-workflow-cutover-stage", {
    idempotency_key: "60000000-0000-0000-0000-000000000008",
    plan_id: opened.plan_id, to_stage: "build_projection", reason: "legacy read complete",
  });
  const payload = await rejected(() => executeRegisteredTool(client, AGENT, "advance-workflow-cutover-stage", {
    idempotency_key: "60000000-0000-0000-0000-000000000009",
    plan_id: opened.plan_id, to_stage: "shadow_compare", evidence_ref: "missing-acceptance", reason: "trying anyway",
  }));
  assert.match(payload.detail, /shadow_acceptance_evidence_not_found_or_not_accepted/);
  client.seedAcceptance("70000000-0000-0000-0000-000000000001",
    { workflow_key: "calendar-prebrief", workflow_version: 1, mode: "shadow", status: "accepted" });
  const advanced = await executeRegisteredTool(client, AGENT, "advance-workflow-cutover-stage", {
    idempotency_key: "60000000-0000-0000-0000-000000000010",
    plan_id: opened.plan_id, to_stage: "shadow_compare",
    evidence_ref: "70000000-0000-0000-0000-000000000001", reason: "shadow parity checks pass",
  });
  assert.equal(advanced.stage, "shadow_compare");
});

test("advance-workflow-cutover-stage can never reach stage=retired -- that door is retire-workflow-cutover-plan alone", async () => {
  const client = new WorkflowCutoverFake();
  const opened = await executeRegisteredTool(client, AGENT, "open-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000011",
    workflow_key: "x", workflow_version: 1, recovery_plan: "revert",
  });
  await assert.rejects(
    () => executeRegisteredTool(client, AGENT, "advance-workflow-cutover-stage", {
      idempotency_key: "60000000-0000-0000-0000-000000000012",
      plan_id: opened.plan_id, to_stage: "retired", reason: "trying to shortcut retirement",
    }),
    (e) => e instanceof ToolError && e.payload.error === "retired_stage_requires_retire_door",
  );
});

test("retire-workflow-cutover-plan is the only workflow-cutover verb declared authority-only", () => {
  for (const name of ["open-workflow-cutover-plan", "advance-workflow-cutover-stage",
    "record-workflow-caller", "workflow-cutover-board", "mark-slice-completion", "read-slice-completion"])
    assert.equal(TOOLS[name].authorityOnly, undefined, `${name} should not be authority-only`);
  assert.equal(TOOLS["retire-workflow-cutover-plan"].authorityOnly, true);
  assert.equal(TOOLS["retire-workflow-cutover-plan"].write, true);
});

test("Q116: retire-workflow-cutover-plan refuses before recovery_ready, and refuses without a real disable-legacy-schedule receipt", async () => {
  const client = new WorkflowCutoverFake();
  const opened = await executeRegisteredTool(client, AGENT, "open-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000013",
    workflow_key: "y", workflow_version: 1, recovery_plan: "revert",
  });
  const early = await rejected(() => executeRegisteredTool(client, AUTHORITY_AGENT, "retire-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000014",
    plan_id: opened.plan_id, disable_receipt_id: "80000000-0000-0000-0000-000000000001", reason: "too early",
  }));
  assert.match(early.detail, /workflow_cutover_plan_not_recovery_ready/);
  client.plans.get(opened.plan_id).stage = "recovery_ready";
  const noReceipt = await rejected(() => executeRegisteredTool(client, AUTHORITY_AGENT, "retire-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000015",
    plan_id: opened.plan_id, disable_receipt_id: "80000000-0000-0000-0000-000000000099", reason: "no real receipt",
  }));
  assert.match(noReceipt.detail, /legacy_schedule_disable_receipt_not_found/);
  client.seedDisableReceipt("80000000-0000-0000-0000-000000000001", { workflow_key: "y" });
  // P1 fix (PR #1245 review, item 6 / Q157): retire-workflow-cutover-plan now
  // refuses outright when the independent workflow-truth census answers
  // unavailable, rather than proceeding on the receipt/evidence chain alone
  // and merely decorating the response with the raw reading. The census
  // route answers unavailable today (the durable census store has not
  // landed), so THIS build refuses every retire -- that is the intended,
  // correct fail-closed behavior, not a bug to work around in the fixture.
  // The check is ordered LAST in the store (after stage/receipt checks,
  // which the two sub-cases above already proved still fire first), so a
  // fully valid plan+receipt still gets refused specifically for the census.
  const refused = await rejected(() => executeRegisteredTool(client, AUTHORITY_AGENT, "retire-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000016",
    plan_id: opened.plan_id, disable_receipt_id: "80000000-0000-0000-0000-000000000001",
    reason: "legacy schedule confirmed disabled",
  }));
  assert.equal(refused.error, "workflow_cutover_retire_refused");
  assert.match(refused.detail, /workflow_cutover_retire_refused_census_unknown/);
  // The plan must still be exactly where it was -- refused, not partially
  // applied.
  assert.equal(client.plans.get(opened.plan_id).stage, "recovery_ready");
});

test("Q157: record-workflow-caller refuses status=done without evidence, and refuses status=blocked without a reason", async () => {
  const client = new WorkflowCutoverFake();
  const noEvidence = await rejected(() => executeRegisteredTool(client, AGENT, "record-workflow-caller", {
    idempotency_key: "60000000-0000-0000-0000-000000000017",
    workflow_key: "z", workflow_version: 1, caller_locator: "tools/legacy-caller.py",
    caller_kind: "script", status: "done",
  }));
  assert.match(noEvidence.detail, /caller_done_requires_evidence_ref/);
  const noReason = await rejected(() => executeRegisteredTool(client, AGENT, "record-workflow-caller", {
    idempotency_key: "60000000-0000-0000-0000-000000000018",
    workflow_key: "z", workflow_version: 1, caller_locator: "tools/legacy-caller.py",
    caller_kind: "script", status: "blocked",
  }));
  assert.match(noReason.detail, /caller_blocked_requires_reason/);
  const recorded = await executeRegisteredTool(client, AGENT, "record-workflow-caller", {
    idempotency_key: "60000000-0000-0000-0000-000000000019",
    workflow_key: "z", workflow_version: 1, caller_locator: "tools/legacy-caller.py",
    caller_kind: "script", status: "done", evidence_ref: "migrated in PR #9999",
  });
  assert.equal(recorded.status, "done");
});

test("workflow-cutover-board reports caller counts and never claims the census route is anything but unknown while it is fail-closed (Q157)", async () => {
  const client = new WorkflowCutoverFake();
  await executeRegisteredTool(client, AGENT, "open-workflow-cutover-plan", {
    idempotency_key: "60000000-0000-0000-0000-000000000020",
    workflow_key: "board-fixture", workflow_version: 1, recovery_plan: "revert",
  });
  await executeRegisteredTool(client, AGENT, "record-workflow-caller", {
    idempotency_key: "60000000-0000-0000-0000-000000000021",
    workflow_key: "board-fixture", workflow_version: 1, caller_locator: "a", caller_kind: "script",
    status: "done", evidence_ref: "receipt-a",
  });
  await executeRegisteredTool(client, AGENT, "record-workflow-caller", {
    idempotency_key: "60000000-0000-0000-0000-000000000022",
    workflow_key: "board-fixture", workflow_version: 1, caller_locator: "b", caller_kind: "script",
    status: "remaining",
  });
  const board = await executeRegisteredTool(client, AGENT, "workflow-cutover-board", {
    workflow_key: "board-fixture", workflow_version: 1,
  });
  assert.equal(board.plan.stage, "read_legacy");
  assert.deepEqual(board.caller_counts, { done: 1, remaining: 1 });
  // The V5-F09/A01 census reader answers unavailable today (documented,
  // fail-closed state); the board must never promote that into "done".
  assert.equal(board.census.state, "unknown");
});

test("Q153: mark-slice-completion refuses status=complete unless every criterion is proven", async () => {
  const client = new WorkflowCutoverFake();
  const refused = await rejected(() => executeRegisteredTool(client, AGENT, "mark-slice-completion", {
    idempotency_key: "60000000-0000-0000-0000-000000000023",
    slice_id: "V5-R02", status: "complete",
    criteria_receipt: [
      { criterion: "shadow parity and single-writer checks pass", evidence: "test suite", pass: true },
      { criterion: "retirement requires fresh exact PASS and a one-use capability", evidence: "not yet exercised against a real workflow", pass: false },
    ],
  }));
  assert.match(refused.detail, /slice_completion_complete_requires_every_criterion_proven/);
  const inProgress = await executeRegisteredTool(client, AGENT, "mark-slice-completion", {
    idempotency_key: "60000000-0000-0000-0000-000000000024",
    slice_id: "V5-R02", status: "in_progress",
    criteria_receipt: [{ criterion: "readiness machinery built", evidence: "PR", pass: true }],
  });
  assert.equal(inProgress.status, "in_progress");
  const read = await executeRegisteredTool(client, AGENT, "read-slice-completion", { slice_id: "V5-R02" });
  assert.equal(read.marked, true);
  assert.equal(read.status, "in_progress");
});

test("read-slice-completion reports marked:false for a slice that was never marked", async () => {
  const client = new WorkflowCutoverFake();
  const read = await executeRegisteredTool(client, AGENT, "read-slice-completion", { slice_id: "V5-R99" });
  assert.equal(read.marked, false);
  assert.equal(read.status, null);
});
