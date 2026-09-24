// V5-F08 second half — the restore exercise, the recovery matrix cells, the
// outbound-queue reconciliation and the degraded-mode projection, proved
// criterion by criterion.
//
// Every negative is ONE named mutation of a clean request that passes, and every
// refusal asserts the reason id AND the check that decided it, so a request that
// fails for the wrong reason fails here.
//
//   node --test mcp-server/test/recovery-matrix.v5.test.mjs

import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";

import { V5BoundaryError, V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import {
  V5_RESTORE_EXERCISE_CHECKS,
  V5_RECOVERY_MATRIX_CELLS,
  V5_DEGRADED_MODES,
  evaluateRestoreExercise,
  evaluateRecoveryMatrix,
  evaluateOutboundQueueRelease,
  v5DegradedModeProjection,
  v5RecoveryMatrixPolicyDigest,
  v5RecoveryMatrixPolicyCanonicalBytes,
  v5RecoveryMatrixPolicyPreimage,
} from "../src/recovery-matrix.v5.js";
import {
  V5_RESTORE_AND_RECOVERY_MATRIX_SEAM,
  V5_OUTBOUND_RECONCILIATION_SEAM,
} from "../src/backup-quarantine.v5.js";

const D = ch => `sha256:${ch.repeat(64)}`;
const clone = v => JSON.parse(JSON.stringify(v));

function receipt() {
  return {
    receipt_kind: "restore-exercise-receipt.v1",
    target_kind: "disposable_branch",
    copy: {
      copy_id: "backup-nightly-run-101-attempt-1",
      custody_domain: "github-actions-artifact",
      primary_domain: "neon-primary",
      producer_id: "backup-nightly-workflow",
      produced_at: "2026-09-24T03:00:00Z",
      recorded_artifact_digest: D("a"),
    },
    oracle_id: "restore-rehearse",
    observed_artifact_digest: D("a"),
    artifact_watermark: { "public.deal": 40, "public.party": 120, "ops.run": 9 },
    restored_watermark: { "ops.run": 9, "public.party": 120, "public.deal": 40 },
    started_at: "2026-09-24T10:00:00Z",
    finished_at: "2026-09-24T10:20:00Z",
  };
}

function assertRefused(result, reason, check) {
  assert.equal(result.decision, "fail");
  assert.equal(result.reason_id, reason);
  assert.equal(result.checks[check], "fail");
  const i = V5_RESTORE_EXERCISE_CHECKS.indexOf(check);
  for (const later of V5_RESTORE_EXERCISE_CHECKS.slice(i + 1)) assert.equal(result.checks[later], "not_reached");
  for (const earlier of V5_RESTORE_EXERCISE_CHECKS.slice(0, i)) assert.equal(result.checks[earlier], "pass");
}

// --- item 4: restore from independently controlled copy, exact watermark/hash

test("item 4: an exact restore of an independently held copy passes every check", () => {
  const r = evaluateRestoreExercise(receipt());
  assert.equal(r.decision, "pass");
  assert.equal(r.reason_id, "restore_exercise_exact");
  for (const c of V5_RESTORE_EXERCISE_CHECKS) assert.equal(r.checks[c], "pass");
  assert.equal(r.tables_compared, 3);
  assert.deepEqual(r.watermark_mismatches, []);
  assert.equal(r.restore_seconds, 1200);
  assert.deepEqual(r.effects, V5_NO_EFFECTS);
});

test("item 4: a restore aimed at production is refused before anything else is read", () => {
  const req = receipt();
  req.target_kind = "production";
  assertRefused(evaluateRestoreExercise(req), "restore_target_is_production", "restore_target_not_production");
  req.target_kind = "unstated";
  assertRefused(evaluateRestoreExercise(req), "restore_target_unstated", "restore_target_not_production");
  req.target_kind = "staging";
  assert.equal(evaluateRestoreExercise(req).decision, "pass");
});

test("item 4: a copy held in the primary's own domain is not independently controlled", () => {
  const req = receipt();
  req.copy.custody_domain = "neon-primary";
  assertRefused(evaluateRestoreExercise(req), "copy_not_independently_controlled", "copy_independently_controlled");
});

test("item 4: the producer cannot be its own restore oracle", () => {
  const req = receipt();
  req.oracle_id = "backup-nightly-workflow";
  assertRefused(evaluateRestoreExercise(req), "oracle_is_the_producer", "oracle_independent_of_producer");
});

test("item 4: a restored artifact whose hash differs from the recorded one fails", () => {
  const req = receipt();
  req.observed_artifact_digest = D("b");
  assertRefused(evaluateRestoreExercise(req), "artifact_hash_mismatch", "artifact_hash_exact");
});

test("item 4: one row off, one table missing or one table extra is not exact", () => {
  const off = receipt();
  off.restored_watermark["public.party"] = 119;
  const r1 = evaluateRestoreExercise(off);
  assertRefused(r1, "watermark_mismatch", "watermark_exact");
  assert.deepEqual(r1.watermark_mismatches, [{ table: "public.party", artifact_rows: 120, restored_rows: 119 }]);

  const missing = receipt();
  delete missing.restored_watermark["ops.run"];
  assert.deepEqual(evaluateRestoreExercise(missing).watermark_mismatches,
    [{ table: "ops.run", artifact_rows: 9, restored_rows: null }]);

  const extra = receipt();
  extra.restored_watermark["public.stray"] = 0;
  assertRefused(evaluateRestoreExercise(extra), "watermark_mismatch", "watermark_exact");
});

test("item 4: a restore that finishes before it starts, or before the copy existed, is refused", () => {
  const req = receipt();
  req.finished_at = "2026-09-24T09:59:59Z";
  assertRefused(evaluateRestoreExercise(req), "restore_interval_invalid", "restore_interval_well_formed");
  const early = receipt();
  early.started_at = "2026-09-24T02:00:00Z";
  assertRefused(evaluateRestoreExercise(early), "restore_interval_invalid", "restore_interval_well_formed");
});

test("item 4: no caller field can skip a check or assert trust", () => {
  for (const key of ["trusted", "skip_checks", "already_verified"]) {
    const req = { ...receipt(), [key]: true };
    assert.throws(() => evaluateRestoreExercise(req), e => e instanceof V5BoundaryError && e.code === "unknown_field");
  }
  const req = receipt();
  req.observed_artifact_digest = "sha256:ABC";
  assert.throws(() => evaluateRestoreExercise(req), e => e.code === "invalid_digest");
});

// --- item 5: RPO/RTO cells pass independently --------------------------------

const OBSERVED = "2026-09-24T12:00:00Z";

function matrix() {
  return {
    observed_at: OBSERVED,
    cells: {
      record_layer_rpo: { source: "pitr_latest_restorable_readback", recovery_point_at: "2026-09-24T11:50:00Z" },
      independent_daily_restorable_copy: {
        newest_copy: {
          custody_domain: "github-actions-artifact",
          primary_domain: "neon-primary",
          producer_id: "backup-nightly-workflow",
          produced_at: "2026-09-24T03:00:00Z",
          recorded_artifact_digest: D("a"),
          independent_store_readback_digest: D("a"),
        },
        restore_exercise: receipt(),
      },
      core_rto: { restore_exercise: receipt() },
      adapter_rto: { recoveries: [{ adapter_id: "salesforce", outage_started_at: "2026-09-20T08:00:00Z", recovered_at: "2026-09-20T15:00:00Z" }] },
    },
  };
}

test("item 5: every cell passes on measured evidence inside the settled floor", () => {
  const m = evaluateRecoveryMatrix(matrix());
  assert.equal(m.decision, "pass");
  assert.deepEqual(m.not_passed, []);
  for (const c of V5_RECOVERY_MATRIX_CELLS) assert.equal(m.cells[c].state, "pass", c);
  assert.equal(m.cells.record_layer_rpo.measured_seconds, 600);
  assert.equal(m.cells.core_rto.measured_seconds, 1200);
});

test("item 5: the RPO cell passes at exactly 15 minutes and fails one second later", () => {
  const at15 = matrix(); at15.cells.record_layer_rpo.recovery_point_at = "2026-09-24T11:45:00Z";
  assert.equal(evaluateRecoveryMatrix(at15).cells.record_layer_rpo.state, "pass");
  const over = matrix(); over.cells.record_layer_rpo.recovery_point_at = "2026-09-24T11:44:59Z";
  assert.equal(evaluateRecoveryMatrix(over).cells.record_layer_rpo.state, "fail");
  const nightlyOnly = matrix();
  nightlyOnly.cells.record_layer_rpo = { source: "verified_copy_produced_at", recovery_point_at: "2026-09-24T03:00:00Z" };
  assert.equal(evaluateRecoveryMatrix(nightlyOnly).cells.record_layer_rpo.state, "fail");
});

test("item 5: a configured setting is not a measurement and the floor cannot be moved", () => {
  const configured = matrix();
  configured.cells.record_layer_rpo.source = "configured_retention_window";
  assert.throws(() => evaluateRecoveryMatrix(configured), e => e.code === "unknown_state");
  const widened = matrix();
  widened.cells.record_layer_rpo.rpo_max_seconds = 86400;
  assert.throws(() => evaluateRecoveryMatrix(widened), e => e.code === "unknown_field");
});

test("item 5: cells are independent — a missing or failing cell changes no other cell", () => {
  const base = evaluateRecoveryMatrix(matrix());
  const noAdapter = matrix(); delete noAdapter.cells.adapter_rto;
  const m1 = evaluateRecoveryMatrix(noAdapter);
  assert.equal(m1.cells.adapter_rto.state, "no_evidence");
  assert.equal(m1.decision, "fail");
  assert.deepEqual(m1.not_passed, ["adapter_rto"]);
  for (const c of ["record_layer_rpo", "independent_daily_restorable_copy", "core_rto"]) assert.deepEqual(m1.cells[c], base.cells[c]);

  const badRpo = matrix(); badRpo.cells.record_layer_rpo.recovery_point_at = "2026-09-24T06:00:00Z";
  const m2 = evaluateRecoveryMatrix(badRpo);
  assert.deepEqual(m2.not_passed, ["record_layer_rpo"]);
  for (const c of ["independent_daily_restorable_copy", "core_rto", "adapter_rto"]) assert.deepEqual(m2.cells[c], base.cells[c]);

  const empty = evaluateRecoveryMatrix({ observed_at: OBSERVED, cells: {} });
  assert.deepEqual(empty.not_passed, [...V5_RECOVERY_MATRIX_CELLS]);
  for (const c of V5_RECOVERY_MATRIX_CELLS) assert.equal(empty.cells[c].state, "no_evidence");
});

test("item 5: the daily copy cell needs a fresh independent copy, a matching readback and a same-lineage exact restore", () => {
  const cases = [
    [m => { m.cells.independent_daily_restorable_copy.newest_copy.produced_at = "2026-09-23T09:59:59Z"; }, "newest_copy_too_old"],
    [m => { m.cells.independent_daily_restorable_copy.newest_copy.independent_store_readback_digest = D("c"); }, "independent_store_readback_mismatch"],
    [m => { m.cells.independent_daily_restorable_copy.newest_copy.custody_domain = "neon-primary"; }, "copy_not_independently_controlled"],
    [m => { m.cells.independent_daily_restorable_copy.newest_copy.producer_id = "mac-nightly"; }, "restore_exercise_other_producer"],
    [m => { m.cells.independent_daily_restorable_copy.newest_copy.custody_domain = "r2-archive"; }, "restore_exercise_other_custody_domain"],
    [m => { m.cells.independent_daily_restorable_copy.restore_exercise.restored_watermark["ops.run"] = 8; }, "restore_exercise_not_exact"],
    [m => { m.observed_at = "2026-10-02T00:00:00Z"; m.cells.independent_daily_restorable_copy.newest_copy.produced_at = "2026-10-01T03:00:00Z"; }, "restore_exercise_too_old"],
  ];
  for (const [mutate, failure] of cases) {
    const m = matrix(); mutate(m);
    const cellResult = evaluateRecoveryMatrix(m).cells.independent_daily_restorable_copy;
    assert.equal(cellResult.state, "fail", failure);
    assert.ok(cellResult.failures.includes(failure), `${failure} in ${cellResult.failures}`);
  }
  const at26h = matrix();
  at26h.cells.independent_daily_restorable_copy.newest_copy.produced_at = "2026-09-23T10:00:00Z";
  at26h.cells.independent_daily_restorable_copy.restore_exercise.copy.produced_at = "2026-09-23T10:00:00Z";
  assert.equal(evaluateRecoveryMatrix(at26h).cells.independent_daily_restorable_copy.state, "pass");
});

test("item 5: core RTO passes at 4 hours, fails beyond, and a non-exact restore has no RTO", () => {
  const at4h = matrix(); at4h.cells.core_rto.restore_exercise.finished_at = "2026-09-24T14:00:00Z";
  at4h.observed_at = "2026-09-24T15:00:00Z";
  assert.equal(evaluateRecoveryMatrix(at4h).cells.core_rto.state, "pass");
  const over = matrix(); over.cells.core_rto.restore_exercise.finished_at = "2026-09-24T14:00:01Z";
  assert.equal(evaluateRecoveryMatrix(over).cells.core_rto.state, "fail");
  const inexact = matrix(); inexact.cells.core_rto.restore_exercise.observed_artifact_digest = D("f");
  const c = evaluateRecoveryMatrix(inexact).cells.core_rto;
  assert.equal(c.state, "fail");
  assert.equal(c.restore_exercise_reason, "artifact_hash_mismatch");
});

test("item 5: an adapter recovery within 24h passes; longer is indeterminate, never pass, until a calendar is bound", () => {
  const long = matrix();
  long.cells.adapter_rto.recoveries.push({ adapter_id: "drive", outage_started_at: "2026-09-19T17:00:00Z", recovered_at: "2026-09-22T09:00:00Z" });
  const m = evaluateRecoveryMatrix(long);
  assert.equal(m.cells.adapter_rto.state, "indeterminate");
  assert.deepEqual(m.not_passed, ["adapter_rto"]);
  assert.equal(m.decision, "fail");
  const backwards = matrix();
  backwards.cells.adapter_rto.recoveries[0].recovered_at = "2026-09-20T07:00:00Z";
  assert.equal(evaluateRecoveryMatrix(backwards).cells.adapter_rto.state, "fail");
});

// --- item 6: outbound queues quarantine until reconciliation -----------------

const K = { a: D("1"), b: D("2"), c: D("3"), d: D("4"), e: D("5") };

function outbound() {
  return {
    restore_id: "restore-2026-09-24",
    items: [
      { item_id: "mail-1", envelope_digest: K.a, state: "pending" },
      { item_id: "mail-2", envelope_digest: K.b, state: "in_flight" },
      { item_id: "sf-3", envelope_digest: K.c, state: "outcome_unknown" },
      { item_id: "sf-4", envelope_digest: K.d, state: "pending" },
      { item_id: "done-5", envelope_digest: K.e, state: "settled" },
    ],
    readbacks: [],
  };
}

test("item 6: after a restore every unsettled outbound item is quarantined and nothing is released", () => {
  const r = evaluateOutboundQueueRelease(outbound());
  assert.equal(r.decision, "hold");
  assert.deepEqual(r.quarantined_item_ids, ["mail-1", "mail-2", "sf-3", "sf-4"]);
  assert.equal(r.dispositions.find(d => d.item_id === "done-5").disposition, "already_settled");
  assert.equal(r.releases_anything, false);
  assert.deepEqual(r.effects, V5_NO_EFFECTS);
});

test("item 6: only an exact-key provider readback moves an item, and present is never resent", () => {
  const req = outbound();
  req.readbacks = [
    { item_id: "mail-1", idempotency_key: K.a, readback: "effect_present" },
    { item_id: "mail-2", idempotency_key: K.b, readback: "effect_absent" },
    { item_id: "sf-3", idempotency_key: K.c, readback: "indeterminate" },
    { item_id: "sf-4", idempotency_key: K.a, readback: "effect_absent" },
  ];
  const r = evaluateOutboundQueueRelease(req);
  const by = Object.fromEntries(r.dispositions.map(d => [d.item_id, d]));
  assert.equal(by["mail-1"].disposition, "settle_without_resend");
  assert.equal(by["mail-2"].disposition, "release_for_governed_send");
  assert.equal(by["sf-3"].disposition, "quarantined");
  assert.equal(by["sf-3"].reason, "readback_indeterminate");
  assert.equal(by["sf-4"].disposition, "quarantined");
  assert.equal(by["sf-4"].reason, "idempotency_key_mismatch");
  assert.equal(r.decision, "hold");
  assert.deepEqual(r.quarantined_item_ids, ["sf-3", "sf-4"]);
});

test("item 6: the queue is reconciled only when every item has an exact readback", () => {
  const req = outbound();
  req.readbacks = req.items.filter(i => i.state !== "settled")
    .map(i => ({ item_id: i.item_id, idempotency_key: i.envelope_digest, readback: "effect_present" }));
  const r = evaluateOutboundQueueRelease(req);
  assert.equal(r.decision, "reconciled");
  assert.deepEqual(r.quarantined_item_ids, []);
});

test("item 6: no age-out, no operator override, no duplicate readback", () => {
  for (const key of ["force_release", "release_after_seconds", "operator_override"]) {
    assert.throws(() => evaluateOutboundQueueRelease({ ...outbound(), [key]: true }), e => e.code === "unknown_field");
  }
  const dup = outbound();
  dup.readbacks = [
    { item_id: "mail-1", idempotency_key: K.a, readback: "effect_absent" },
    { item_id: "mail-1", idempotency_key: K.a, readback: "effect_present" },
  ];
  assert.throws(() => evaluateOutboundQueueRelease(dup), e => e.code === "duplicate_readback");
});

// --- degraded-mode projection ------------------------------------------------

test("degraded modes: every outage is a reduction that quarantines outbound; none claims full offline capability", () => {
  for (const [name, mode] of Object.entries(V5_DEGRADED_MODES)) assert.equal(mode.outbound, "quarantine", name);
  const p = v5DegradedModeProjection(["record_layer_unavailable", "model_provider_unavailable"]);
  assert.equal(p.mode, "degraded");
  assert.equal(p.outbound, "quarantine_until_reconciliation");
  assert.equal(p.claims_full_offline_capability, false);
  assert.equal(p.modes.record_layer_unavailable.writes, "refuse");
  assert.equal(v5DegradedModeProjection([]).mode, "normal");
  assert.throws(() => v5DegradedModeProjection(["everything_fine"]), e => e.code === "unknown_state");
});

// --- the policy --------------------------------------------------------------

test("policy: the digest is sha256 of the canonical bytes and closes exactly the two #974 seams", () => {
  const hex = createHash("sha256").update(v5RecoveryMatrixPolicyCanonicalBytes()).digest("hex");
  assert.equal(v5RecoveryMatrixPolicyDigest(), `sha256:${hex}`);
  const pre = v5RecoveryMatrixPolicyPreimage();
  assert.deepEqual(pre.closes_seams, [V5_OUTBOUND_RECONCILIATION_SEAM, V5_RESTORE_AND_RECOVERY_MATRIX_SEAM].sort());
  assert.equal(pre.rpo_max_seconds, 900);
  assert.equal(pre.core_rto_max_seconds, 14400);
  assert.equal(pre.no_evidence_is_pass, false);
  assert.equal(pre.claims_full_offline_capability, false);
  assert.equal(evaluateRestoreExercise(clone(receipt())).policy_digest, v5RecoveryMatrixPolicyDigest());
});
