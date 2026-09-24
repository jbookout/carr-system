// DoctorCRE v5 slice V5-F08, second half — the RESTORE EXERCISE verdict, the
// RECOVERY MATRIX cells, OUTBOUND-QUEUE RECONCILIATION after a restore, and the
// DEGRADED-MODE projection (requirement Q034).
//
// backup-quarantine.v5.js (#974) closed catalog `checkable_done` items 1-3 and
// named items 4-6 as seams it did not decide. This file decides exactly those:
//
//   4. "restore from independently controlled copy succeeds with exact
//       watermark/hash"            -> evaluateRestoreExercise
//   5. "RPO/RTO cells pass independently"
//                                  -> evaluateRecoveryMatrix, four cells, each
//                                     computed from its OWN evidence block only
//   6. "outbound queues quarantine until reconciliation"
//                                  -> evaluateOutboundQueueRelease
//   interface "degraded-mode projection"
//                                  -> v5DegradedModeProjection
//
// WHAT THIS FILE IS NOT. It restores nothing, reads no backup, opens no
// connection, sends nothing and releases nothing. Every fact it decides on is a
// TYPED OBSERVATION THE CALLER SUPPLIES — in production, the receipt line
// bin/restore-rehearse.sh prints (tools/restore-watermark.py computes the exact
// watermark and hash it carries). A `pass` here says the registered checks did
// not fire on the facts as reported; every result carries V5_NO_EFFECTS.
//
// THE FLOOR IS A CONSTANT, NOT A PARAMETER. The numbers are the catalog's
// concrete output for Q034.D1 — RPO <= 15 minutes, an independent daily
// restorable copy, core RTO <= 4 hours, adapter RTO <= 1 business day. No
// request field can widen them: `rpo_max_seconds`, `trusted`, `skip_checks` are
// unknown fields, so a request that tries to move the floor cannot be read.
//
// MEASURED, NEVER CONFIGURED. Every cell is decided from observed instants
// (produced_at, observed_at, started_at, finished_at). There is no field for a
// configured retention window or a vendor's advertised RPO; "point-in-time
// restore is enabled" is not a recovery point; a point-in-time branch that
// provably holds a known write is (bin/pitr-restore-proof.sh).
//
// CELLS ARE INDEPENDENT. Each cell reads only its own evidence block. A cell
// with no evidence is `no_evidence`, which is not a pass, and it changes no
// other cell. The matrix passes only when every cell passes, and it still
// reports every cell, so one red cell never hides behind another.
//
// THE ADAPTER CELL AND THE BUSINESS DAY. One business day is never SHORTER
// than 24 wall-clock hours, so an adapter recovery measured at <= 24h passes
// under any calendar. Longer recoveries are judged against the sealed US
// federal business calendar (ops/config/business-calendar.us-federal.json):
// recovered by the same wall-clock time on the next business day. Without the
// calendar, or outside its covered years, the answer is `indeterminate`, which
// is not a pass.
//
// TWO KINDS OF NO, inherited unchanged from global-boundaries.v5.js: a policy
// answer is RETURNED (decision + stable reason_id); a contract violation THROWS
// V5BoundaryError (unknown field, unknown enum, malformed digest or instant).

import { canonicalJson, digest } from "./artifact-trust.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import { ORGANIZATION_TENANT_ID } from "./identity.js";
import {
  V5_READBACK_STATES,
  V5_READBACK_RESOLUTIONS,
} from "./workflow-effect-envelope.v5.js";
import {
  V5_RESTORE_AND_RECOVERY_MATRIX_SEAM,
  V5_OUTBOUND_RECONCILIATION_SEAM,
  v5BackupQuarantinePolicyDigest,
} from "./backup-quarantine.v5.js";

export const V5_RECOVERY_MATRIX_SCHEMA_VERSION = "doctorcre-v5-recovery-matrix.v1";
export const V5_RECOVERY_MATRIX_POLICY_VERSION = 1;

/** The receipt kind bin/restore-rehearse.sh emits and evaluateRestoreExercise reads. */
export const V5_RESTORE_EXERCISE_RECEIPT_KIND = "restore-exercise-receipt.v1";

// --- the settled floor (Q034.D1, catalog concrete_output) --------------------
export const V5_RPO_MAX_SECONDS = 15 * 60;
export const V5_CORE_RTO_MAX_SECONDS = 4 * 60 * 60;
/** One business day is never shorter than this; see the header. */
export const V5_ADAPTER_RTO_CALENDAR_FREE_BOUND_SECONDS = 24 * 60 * 60;
/** Daily cadence plus the nightly job's own runtime window. */
export const V5_DAILY_COPY_MAX_AGE_SECONDS = 26 * 60 * 60;
/** Weekly restore-exercise cadence. */
export const V5_RESTORE_EXERCISE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60;


/** Where a restore may land. Production is registered so it can be REFUSED by name. */
export const V5_RESTORE_TARGET_KINDS = Object.freeze([
  "disposable_branch", "disposable_local_cluster", "staging", "production", "unstated",
]);
export const V5_ADMISSIBLE_RESTORE_TARGETS = Object.freeze([
  "disposable_branch", "disposable_local_cluster", "staging",
]);

/** The checks of item 4, IN ORDER. The first that does not pass decides. */
export const V5_RESTORE_EXERCISE_CHECKS = Object.freeze([
  "restore_target_not_production",
  "copy_independently_controlled",
  "oracle_independent_of_producer",
  "artifact_hash_exact",
  "watermark_exact",
  "restore_interval_well_formed",
]);

export const V5_CHECK_STATES = Object.freeze(["pass", "fail", "not_reached"]);

export const V5_RECOVERY_MATRIX_CELLS = Object.freeze([
  "record_layer_rpo",
  "independent_daily_restorable_copy",
  "core_rto",
  "adapter_rto",
]);

export const V5_CELL_STATES = Object.freeze(["pass", "fail", "no_evidence", "indeterminate"]);

/**
 * How a recovery point was OBSERVED. Configuration is deliberately absent.
 *
 * `pitr_branch_proof` (bin/pitr-restore-proof.sh): the provider's history
 * retention is read back from its API, a disposable branch is created from
 * production AT a requested past instant T, and a probe row that production
 * shows last written at least V5_PITR_PROBE_MARGIN_SECONDS before T is read on
 * that branch. The provider exposes no "latest restorable point" field, so the
 * point is PROVEN rather than read: what is proven restorable is the probe's
 * write instant, and exposure is measured from THAT, never from T.
 */
export const V5_RECOVERY_POINT_SOURCES = Object.freeze([
  "pitr_branch_proof",
  "verified_copy_produced_at",
]);

/** A probe written this close to T might not have committed by T; it proves nothing. */
export const V5_PITR_PROBE_MARGIN_SECONDS = 60;

/** Where the point-in-time proof ran. Production is registered so it can be refused by name. */
export const V5_PITR_PROOF_TARGET_KINDS = Object.freeze(["disposable_branch", "production", "unstated"]);

/** Outbound queue item states at the moment of restore. */
export const V5_OUTBOUND_ITEM_STATES = Object.freeze([
  "pending", "in_flight", "outcome_unknown", "settled",
]);

export const V5_OUTBOUND_DISPOSITIONS = Object.freeze([
  "quarantined", "settle_without_resend", "release_for_governed_send", "already_settled",
]);

export const V5_RECOVERY_MATRIX_REASON_IDS = Object.freeze([
  "artifact_hash_mismatch",
  "copy_not_independently_controlled",
  "oracle_is_the_producer",
  "restore_exercise_exact",
  "restore_interval_invalid",
  "restore_target_is_production",
  "restore_target_unstated",
  "watermark_mismatch",
  "every_cell_passed",
  "one_or_more_cells_not_passed",
  "outbound_items_quarantined",
  "outbound_items_all_reconciled",
]);

const SHA256_REF = /^sha256:[0-9a-f]{64}$/;
const STABLE_ID = /^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,255}$/;
const INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/;
// schema.table as both producers write it: the unquoted catalog names joined by
// a dot. Postgres allows any character in a quoted identifier, so only control
// characters are refused; both sides of the comparison spell it the same way.
const TABLE_NAME = /^[^\x00-\x1f.][^\x00-\x1f]{0,127}\.[^\x00-\x1f]{1,128}$/;

// --- contract helpers (same discipline as backup-quarantine.v5.js) -----------

function fail(code, message, detail) {
  throw new V5BoundaryError(code, message, detail);
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

function closed(object, allowed, path, required = allowed) {
  if (!isPlainObject(object)) fail("invalid_shape", `${path} must be a plain object`, { path });
  for (const key of Object.keys(object)) {
    if (!allowed.includes(key)) fail("unknown_field", `unknown field "${key}" at ${path}`, { path: `${path}.${key}`, key });
  }
  for (const key of required) {
    if (!(key in object)) fail("missing_field", `${path}.${key} is required`, { path: `${path}.${key}` });
  }
  return object;
}

function stableId(value, path) {
  if (typeof value !== "string" || !STABLE_ID.test(value)) fail("invalid_identifier", `${path} must be a stable identifier`, { path });
  return value;
}

function digestRef(value, path) {
  if (typeof value !== "string" || !SHA256_REF.test(value)) {
    fail("invalid_digest", `${path} must be a "sha256:" reference to a 64-character lower-case digest`, { path });
  }
  return value;
}

function enumValue(value, allowed, path) {
  if (typeof value !== "string" || !allowed.includes(value)) {
    fail("unknown_state", `${path} must be one of the registered values`, { path, registered: [...allowed] });
  }
  return value;
}

/** A UTC instant, parsed to epoch milliseconds. A label never outvotes the clock, so the clock must be readable. */
function instant(value, path) {
  if (typeof value !== "string" || !INSTANT.test(value)) fail("invalid_instant", `${path} must be a UTC ISO-8601 instant ending in Z`, { path });
  const ms = Date.parse(value);
  if (!Number.isFinite(ms)) fail("invalid_instant", `${path} is not a real instant`, { path });
  return ms;
}

/** A watermark: schema-qualified table name -> non-negative integer row count. Order-free. */
function watermark(value, path) {
  if (!isPlainObject(value)) fail("invalid_shape", `${path} must be a plain object`, { path });
  const out = {};
  for (const [table, rows] of Object.entries(value)) {
    if (!TABLE_NAME.test(table)) fail("invalid_identifier", `${path} key "${table}" must be schema.table`, { path });
    if (!Number.isSafeInteger(rows) || rows < 0) fail("invalid_shape", `${path}.${table} must be a non-negative integer`, { path });
    out[table] = rows;
  }
  if (Object.keys(out).length === 0) fail("missing_field", `${path} must name at least one table`, { path });
  return out;
}

function checkStates(checks, firstFailIndex) {
  return Object.fromEntries(checks.map((name, i) =>
    [name, firstFailIndex < 0 || i < firstFailIndex ? "pass" : i === firstFailIndex ? "fail" : "not_reached"]));
}

// ---------------------------------------------------------------------------
// Item 4 — the restore exercise.
//
// A restore is exact when the bytes restored are the bytes the producer
// recorded (hash), and the restored database holds exactly the rows the
// artifact itself carries, table for table (watermark). The comparison is
// against the ARTIFACT, never against live production: production moves after
// the dump, so a production comparison can only ever be approximate, and an
// approximate check is what this item exists to replace.
// ---------------------------------------------------------------------------

const RECEIPT_KEYS = Object.freeze([
  "artifact_watermark", "copy", "finished_at", "oracle_id", "observed_artifact_digest",
  "receipt_kind", "restored_watermark", "started_at", "target_kind",
]);
const COPY_KEYS = Object.freeze([
  "copy_id", "custody_domain", "primary_domain", "produced_at", "producer_id", "recorded_artifact_digest",
]);

export function normalizeRestoreExerciseReceipt(receipt) {
  closed(receipt, RECEIPT_KEYS, "receipt");
  if (receipt.receipt_kind !== V5_RESTORE_EXERCISE_RECEIPT_KIND) {
    fail("unknown_state", `receipt.receipt_kind must be "${V5_RESTORE_EXERCISE_RECEIPT_KIND}"`, { path: "receipt.receipt_kind" });
  }
  const copy = closed(receipt.copy, COPY_KEYS, "receipt.copy");
  return deepFreeze({
    receipt_kind: V5_RESTORE_EXERCISE_RECEIPT_KIND,
    target_kind: enumValue(receipt.target_kind, V5_RESTORE_TARGET_KINDS, "receipt.target_kind"),
    copy: {
      copy_id: stableId(copy.copy_id, "receipt.copy.copy_id"),
      custody_domain: stableId(copy.custody_domain, "receipt.copy.custody_domain"),
      primary_domain: stableId(copy.primary_domain, "receipt.copy.primary_domain"),
      producer_id: stableId(copy.producer_id, "receipt.copy.producer_id"),
      produced_at: (instant(copy.produced_at, "receipt.copy.produced_at"), copy.produced_at),
      recorded_artifact_digest: digestRef(copy.recorded_artifact_digest, "receipt.copy.recorded_artifact_digest"),
    },
    oracle_id: stableId(receipt.oracle_id, "receipt.oracle_id"),
    observed_artifact_digest: digestRef(receipt.observed_artifact_digest, "receipt.observed_artifact_digest"),
    artifact_watermark: watermark(receipt.artifact_watermark, "receipt.artifact_watermark"),
    restored_watermark: watermark(receipt.restored_watermark, "receipt.restored_watermark"),
    started_at: (instant(receipt.started_at, "receipt.started_at"), receipt.started_at),
    finished_at: (instant(receipt.finished_at, "receipt.finished_at"), receipt.finished_at),
  });
}

function watermarkDiff(artifact, restored) {
  const tables = [...new Set([...Object.keys(artifact), ...Object.keys(restored)])].sort();
  return tables
    .filter(t => artifact[t] !== restored[t])
    .map(t => ({ table: t, artifact_rows: artifact[t] ?? null, restored_rows: restored[t] ?? null }));
}

export function evaluateRestoreExercise(receiptInput) {
  const r = normalizeRestoreExerciseReceipt(receiptInput);
  const mismatches = watermarkDiff(r.artifact_watermark, r.restored_watermark);
  const startedMs = Date.parse(r.started_at);
  const finishedMs = Date.parse(r.finished_at);
  const outcomes = [
    () => r.target_kind === "production" ? "restore_target_is_production"
      : !V5_ADMISSIBLE_RESTORE_TARGETS.includes(r.target_kind) ? "restore_target_unstated" : null,
    () => r.copy.custody_domain === r.copy.primary_domain ? "copy_not_independently_controlled" : null,
    () => r.oracle_id === r.copy.producer_id ? "oracle_is_the_producer" : null,
    () => r.observed_artifact_digest !== r.copy.recorded_artifact_digest ? "artifact_hash_mismatch" : null,
    () => mismatches.length ? "watermark_mismatch" : null,
    () => !(finishedMs >= startedMs && startedMs >= Date.parse(r.copy.produced_at)) ? "restore_interval_invalid" : null,
  ];
  let failIndex = -1;
  let reason = "restore_exercise_exact";
  for (let i = 0; i < outcomes.length; i++) {
    const found = outcomes[i]();
    if (found) { failIndex = i; reason = found; break; }
  }
  return deepFreeze({
    schema_version: V5_RECOVERY_MATRIX_SCHEMA_VERSION,
    policy_digest: v5RecoveryMatrixPolicyDigest(),
    decision: failIndex < 0 ? "pass" : "fail",
    reason_id: reason,
    checks: checkStates(V5_RESTORE_EXERCISE_CHECKS, failIndex),
    receipt_digest: digest(r),
    copy_id: r.copy.copy_id,
    custody_domain: r.copy.custody_domain,
    producer_id: r.copy.producer_id,
    artifact_digest: r.observed_artifact_digest,
    tables_compared: Object.keys(r.artifact_watermark).length,
    watermark_mismatches: mismatches,
    restore_seconds: Math.round((finishedMs - startedMs) / 1000),
    finished_at: r.finished_at,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// Item 5 — the recovery matrix. Four cells, each read from its own block.
// ---------------------------------------------------------------------------

const MATRIX_KEYS = Object.freeze(["business_calendar", "cells", "observed_at"]);
const CELL_BLOCK_KEYS = Object.freeze([...V5_RECOVERY_MATRIX_CELLS]);
const RPO_COPY_KEYS = Object.freeze(["recovery_point_at", "source"]);
const RPO_PITR_KEYS = Object.freeze([
  "history_retention_seconds", "probe_committed_at", "probe_present_on_branch", "proof_target_kind",
  "requested_restorable_point", "retention_read_at", "source",
]);
const DAILY_KEYS = Object.freeze(["newest_copy", "restore_exercise"]);
const NEWEST_COPY_KEYS = Object.freeze([
  "custody_domain", "independent_store_readback_digest", "primary_domain", "produced_at",
  "producer_id", "recorded_artifact_digest",
]);
const CORE_RTO_KEYS = Object.freeze(["restore_exercise"]);
const ADAPTER_KEYS = Object.freeze(["recoveries"]);
const ADAPTER_RECOVERY_KEYS = Object.freeze(["adapter_id", "outage_started_at", "recovered_at"]);

function cell(state, detail) {
  return { state, ...detail };
}

function rpoCell(block, observedMs) {
  const at = "cells.record_layer_rpo";
  if (!isPlainObject(block)) fail("invalid_shape", `${at} must be a plain object`, { path: at });
  enumValue(block.source, V5_RECOVERY_POINT_SOURCES, `${at}.source`);
  if (block.source === "verified_copy_produced_at") {
    closed(block, RPO_COPY_KEYS, at);
    const pointMs = instant(block.recovery_point_at, `${at}.recovery_point_at`);
    if (pointMs > observedMs) return cell("fail", { failures: ["recovery_point_after_observation"], source: block.source });
    const seconds = Math.floor((observedMs - pointMs) / 1000);
    return cell(seconds <= V5_RPO_MAX_SECONDS ? "pass" : "fail", {
      failures: seconds <= V5_RPO_MAX_SECONDS ? [] : ["exposure_exceeds_rpo"],
      measured_seconds: seconds, bound_seconds: V5_RPO_MAX_SECONDS, source: block.source,
    });
  }
  closed(block, RPO_PITR_KEYS, at);
  enumValue(block.proof_target_kind, V5_PITR_PROOF_TARGET_KINDS, `${at}.proof_target_kind`);
  if (typeof block.probe_present_on_branch !== "boolean") {
    fail("invalid_shape", `${at}.probe_present_on_branch must be a boolean`, { path: `${at}.probe_present_on_branch` });
  }
  const retention = block.history_retention_seconds;
  if (!Number.isSafeInteger(retention) || retention < 0) {
    fail("invalid_shape", `${at}.history_retention_seconds must be a non-negative integer`, { path: `${at}.history_retention_seconds` });
  }
  const requestedMs = instant(block.requested_restorable_point, `${at}.requested_restorable_point`);
  const probeMs = instant(block.probe_committed_at, `${at}.probe_committed_at`);
  const readMs = instant(block.retention_read_at, `${at}.retention_read_at`);
  // The proven point is the probe's write instant, never the requested T.
  const exposure = Math.floor((observedMs - probeMs) / 1000);
  const failures = [];
  if (block.proof_target_kind !== "disposable_branch") failures.push("proof_target_not_disposable");
  if (probeMs > requestedMs - V5_PITR_PROBE_MARGIN_SECONDS * 1000) failures.push("probe_not_before_restorable_point");
  if (requestedMs > observedMs) failures.push("restorable_point_after_observation");
  if (!block.probe_present_on_branch) failures.push("probe_absent_on_point_in_time_branch");
  if (exposure > V5_RPO_MAX_SECONDS) failures.push("exposure_exceeds_rpo");
  if (retention < exposure) failures.push("retention_does_not_cover_exposure");
  if (readMs > observedMs || observedMs - readMs > V5_RPO_MAX_SECONDS * 1000) failures.push("retention_readback_stale");
  return cell(failures.length ? "fail" : "pass", {
    failures,
    source: block.source,
    measured_seconds: exposure,
    bound_seconds: V5_RPO_MAX_SECONDS,
    proven_restorable_point: block.probe_committed_at,
    history_retention_seconds: retention,
  });
}

function dailyCopyCell(block, observedMs) {
  closed(block, DAILY_KEYS, "cells.independent_daily_restorable_copy");
  const c = closed(block.newest_copy, NEWEST_COPY_KEYS, "cells.independent_daily_restorable_copy.newest_copy");
  const at = "cells.independent_daily_restorable_copy.newest_copy";
  const producedMs = instant(c.produced_at, `${at}.produced_at`);
  stableId(c.custody_domain, `${at}.custody_domain`);
  stableId(c.primary_domain, `${at}.primary_domain`);
  stableId(c.producer_id, `${at}.producer_id`);
  digestRef(c.recorded_artifact_digest, `${at}.recorded_artifact_digest`);
  digestRef(c.independent_store_readback_digest, `${at}.independent_store_readback_digest`);
  const exercise = evaluateRestoreExercise(block.restore_exercise);
  const ageSeconds = Math.floor((observedMs - producedMs) / 1000);
  const exerciseAge = Math.floor((observedMs - Date.parse(exercise.finished_at)) / 1000);
  const failures = [];
  if (c.custody_domain === c.primary_domain) failures.push("copy_not_independently_controlled");
  if (producedMs > observedMs) failures.push("copy_produced_after_observation");
  else if (ageSeconds > V5_DAILY_COPY_MAX_AGE_SECONDS) failures.push("newest_copy_too_old");
  if (c.independent_store_readback_digest !== c.recorded_artifact_digest) failures.push("independent_store_readback_mismatch");
  if (exercise.decision !== "pass") failures.push("restore_exercise_not_exact");
  if (exercise.custody_domain !== c.custody_domain) failures.push("restore_exercise_other_custody_domain");
  if (exercise.producer_id !== c.producer_id) failures.push("restore_exercise_other_producer");
  if (exerciseAge < 0) failures.push("restore_exercise_after_observation");
  else if (exerciseAge > V5_RESTORE_EXERCISE_MAX_AGE_SECONDS) failures.push("restore_exercise_too_old");
  return cell(failures.length ? "fail" : "pass", {
    failures,
    newest_copy_age_seconds: ageSeconds,
    bound_seconds: V5_DAILY_COPY_MAX_AGE_SECONDS,
    restore_exercise_age_seconds: exerciseAge,
    restore_exercise_bound_seconds: V5_RESTORE_EXERCISE_MAX_AGE_SECONDS,
    restore_exercise_receipt_digest: exercise.receipt_digest,
  });
}

function coreRtoCell(block) {
  closed(block, CORE_RTO_KEYS, "cells.core_rto");
  const exercise = evaluateRestoreExercise(block.restore_exercise);
  if (exercise.decision !== "pass") {
    // A restore that did not come back exact has no recovery time: it did not recover.
    return cell("fail", { reason: "restore_exercise_not_exact", restore_exercise_reason: exercise.reason_id });
  }
  return cell(exercise.restore_seconds <= V5_CORE_RTO_MAX_SECONDS ? "pass" : "fail", {
    measured_seconds: exercise.restore_seconds,
    bound_seconds: V5_CORE_RTO_MAX_SECONDS,
    restore_exercise_receipt_digest: exercise.receipt_digest,
  });
}

// --- the business calendar (ops/config/business-calendar.us-federal.json) ----
//
// The calendar is CONFIG DATA, supplied by the caller and SEALED: its canonical
// digest must equal V5_BUSINESS_CALENDAR_DIGEST, so a caller cannot add a
// holiday to stretch a deadline without the change being a visible re-pin here.
//
// THE RULE (owner default, reversible): an adapter outage is recovered within
// one business day when it is recovered no later than the SAME wall-clock time,
// in the calendar's timezone, on the first business day after the day the
// outage started. That deadline is never less than 24 hours away, so a
// recovery inside 24 wall-clock hours passes under any calendar; an outage
// spanning weekends or holidays is judged on business days.

const CALENDAR_KEYS = Object.freeze([
  "business_weekdays", "calendar_id", "calendar_kind", "covers_from", "covers_through",
  "holiday_rule", "holidays", "timezone",
]);
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export const V5_BUSINESS_CALENDAR_KIND = "business-calendar.v1";
/** canonical digest of ops/config/business-calendar.us-federal.json. Re-pin deliberately. */
export const V5_BUSINESS_CALENDAR_DIGEST =
  "sha256:74a5a5b2d7b1e71e794d7a0c1ee3fe3062a82533346edb4fe1945817a83b52e0";

export function normalizeBusinessCalendar(calendar) {
  closed(calendar, CALENDAR_KEYS, "business_calendar");
  if (calendar.calendar_kind !== V5_BUSINESS_CALENDAR_KIND) {
    fail("unknown_state", `business_calendar.calendar_kind must be "${V5_BUSINESS_CALENDAR_KIND}"`, { path: "business_calendar.calendar_kind" });
  }
  const actual = digest(calendar);
  if (actual !== V5_BUSINESS_CALENDAR_DIGEST) {
    fail("business_calendar_digest_moved", "the business calendar is not the pinned calendar; re-pin it deliberately rather than passing a different one",
      { expected: V5_BUSINESS_CALENDAR_DIGEST, actual });
  }
  return calendar;
}

function zonedParts(ms, timeZone) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).formatToParts(new Date(ms));
  const get = type => Number(parts.find(p => p.type === type).value);
  return { y: get("year"), m: get("month"), d: get("day"), hh: get("hour"), mm: get("minute"), ss: get("second") };
}

function zoneOffsetMs(ms, timeZone) {
  const p = zonedParts(ms, timeZone);
  return Date.UTC(p.y, p.m - 1, p.d, p.hh, p.mm, p.ss) - (ms - (ms % 1000));
}

/** The UTC instant of a wall-clock time in a timezone (two-pass, DST-safe outside the gap hour). */
function zonedToUtc(y, m, d, hh, mm, ss, timeZone) {
  const asUtc = Date.UTC(y, m - 1, d, hh, mm, ss);
  const first = asUtc - zoneOffsetMs(asUtc, timeZone);
  return asUtc - zoneOffsetMs(first, timeZone);
}

function isoDate(y, m, d) {
  return `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

/**
 * The next-business-day deadline for an outage starting at `startMs`, or null
 * when any date consulted falls outside the calendar's covered range.
 */
export function nextBusinessDayDeadline(startMs, calendar) {
  const tz = calendar.timezone;
  const p = zonedParts(startMs, tz);
  let day = new Date(Date.UTC(p.y, p.m - 1, p.d));
  for (let i = 0; i < 31; i++) {
    day = new Date(day.getTime() + 86400000);
    const date = isoDate(day.getUTCFullYear(), day.getUTCMonth() + 1, day.getUTCDate());
    if (date < calendar.covers_from || date > calendar.covers_through) return null;
    const isoWeekday = ((day.getUTCDay() + 6) % 7) + 1;
    if (calendar.business_weekdays.includes(isoWeekday) && !(date in calendar.holidays)) {
      return zonedToUtc(day.getUTCFullYear(), day.getUTCMonth() + 1, day.getUTCDate(), p.hh, p.mm, p.ss, tz);
    }
  }
  return null;
}

function adapterRtoCell(block, calendar) {
  closed(block, ADAPTER_KEYS, "cells.adapter_rto");
  if (!Array.isArray(block.recoveries) || block.recoveries.length === 0) {
    fail("invalid_shape", "cells.adapter_rto.recoveries must be a non-empty array; omit the cell to report no evidence", { path: "cells.adapter_rto.recoveries" });
  }
  const perAdapter = block.recoveries.map((rec, i) => {
    const at = `cells.adapter_rto.recoveries[${i}]`;
    closed(rec, ADAPTER_RECOVERY_KEYS, at);
    stableId(rec.adapter_id, `${at}.adapter_id`);
    const start = instant(rec.outage_started_at, `${at}.outage_started_at`);
    const end = instant(rec.recovered_at, `${at}.recovered_at`);
    if (end < start) return { adapter_id: rec.adapter_id, state: "fail", reason: "recovered_before_outage" };
    const seconds = Math.floor((end - start) / 1000);
    if (seconds <= V5_ADAPTER_RTO_CALENDAR_FREE_BOUND_SECONDS) {
      return { adapter_id: rec.adapter_id, measured_seconds: seconds, state: "pass" };
    }
    if (!calendar) {
      return { adapter_id: rec.adapter_id, measured_seconds: seconds, state: "indeterminate", reason: "business_calendar_not_supplied" };
    }
    const deadline = nextBusinessDayDeadline(start, calendar);
    if (deadline === null) {
      return { adapter_id: rec.adapter_id, measured_seconds: seconds, state: "indeterminate", reason: "business_calendar_does_not_cover_dates" };
    }
    return {
      adapter_id: rec.adapter_id,
      measured_seconds: seconds,
      business_day_deadline: new Date(deadline).toISOString(),
      state: end <= deadline ? "pass" : "fail",
    };
  });
  const state = perAdapter.some(a => a.state === "fail") ? "fail"
    : perAdapter.some(a => a.state === "indeterminate") ? "indeterminate" : "pass";
  return cell(state, {
    adapters: perAdapter,
    calendar_free_bound_seconds: V5_ADAPTER_RTO_CALENDAR_FREE_BOUND_SECONDS,
    business_calendar_id: calendar ? calendar.calendar_id : null,
  });
}

const CELL_EVALUATORS = Object.freeze({
  record_layer_rpo: (block, observedMs) => rpoCell(block, observedMs),
  independent_daily_restorable_copy: (block, observedMs) => dailyCopyCell(block, observedMs),
  core_rto: block => coreRtoCell(block),
  adapter_rto: (block, _observedMs, calendar) => adapterRtoCell(block, calendar),
});

export function evaluateRecoveryMatrix(request) {
  closed(request, MATRIX_KEYS, "request", ["cells", "observed_at"]);
  const calendar = request.business_calendar === undefined ? null : normalizeBusinessCalendar(request.business_calendar);
  const observedMs = instant(request.observed_at, "request.observed_at");
  closed(request.cells, CELL_BLOCK_KEYS, "request.cells", []);
  const cells = {};
  for (const name of V5_RECOVERY_MATRIX_CELLS) {
    const block = request.cells[name];
    cells[name] = block === undefined || block === null
      ? cell("no_evidence", {})
      : CELL_EVALUATORS[name](block, observedMs, calendar);
  }
  const allPass = V5_RECOVERY_MATRIX_CELLS.every(name => cells[name].state === "pass");
  return deepFreeze({
    schema_version: V5_RECOVERY_MATRIX_SCHEMA_VERSION,
    policy_digest: v5RecoveryMatrixPolicyDigest(),
    observed_at: request.observed_at,
    decision: allPass ? "pass" : "fail",
    reason_id: allPass ? "every_cell_passed" : "one_or_more_cells_not_passed",
    cells,
    not_passed: V5_RECOVERY_MATRIX_CELLS.filter(name => cells[name].state !== "pass"),
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// Item 6 — outbound queues quarantine until reconciliation.
//
// After a restore, the database may believe an effect is still owed that the
// provider already performed (the restore rewound past the send), or believe it
// was sent when it was not. Neither belief is trusted. Every item not already
// settled is QUARANTINED, and it leaves only on a provider readback matched by
// its exact idempotency key (the F06 envelope digest):
//   effect_present -> settle_without_resend   (never sent twice)
//   effect_absent  -> release_for_governed_send (a fresh governed send, not a replay)
//   indeterminate / unstated / no readback / key mismatch -> stays quarantined
// There is no age-out and no operator override field. The readback vocabulary
// and its resolution map are F06's, imported rather than copied.
// ---------------------------------------------------------------------------

const OUTBOUND_KEYS = Object.freeze(["items", "readbacks", "restore_id"]);
const ITEM_KEYS = Object.freeze(["envelope_digest", "item_id", "state"]);
const READBACK_KEYS = Object.freeze(["idempotency_key", "item_id", "readback"]);

const READBACK_DISPOSITION = Object.freeze({
  confirmed_success: "settle_without_resend",
  confirmed_failure: "release_for_governed_send",
  unknown: "quarantined",
});

export function evaluateOutboundQueueRelease(request) {
  closed(request, OUTBOUND_KEYS, "request");
  stableId(request.restore_id, "request.restore_id");
  if (!Array.isArray(request.items)) fail("invalid_shape", "request.items must be an array", { path: "request.items" });
  if (!Array.isArray(request.readbacks)) fail("invalid_shape", "request.readbacks must be an array", { path: "request.readbacks" });
  const seen = new Set();
  const items = request.items.map((item, i) => {
    const at = `request.items[${i}]`;
    closed(item, ITEM_KEYS, at);
    stableId(item.item_id, `${at}.item_id`);
    if (seen.has(item.item_id)) fail("duplicate_item", `${at}.item_id repeats "${item.item_id}"`, { path: at });
    seen.add(item.item_id);
    digestRef(item.envelope_digest, `${at}.envelope_digest`);
    enumValue(item.state, V5_OUTBOUND_ITEM_STATES, `${at}.state`);
    return item;
  });
  const readbacks = new Map();
  request.readbacks.forEach((rb, i) => {
    const at = `request.readbacks[${i}]`;
    closed(rb, READBACK_KEYS, at);
    stableId(rb.item_id, `${at}.item_id`);
    digestRef(rb.idempotency_key, `${at}.idempotency_key`);
    enumValue(rb.readback, V5_READBACK_STATES, `${at}.readback`);
    if (readbacks.has(rb.item_id)) fail("duplicate_readback", `${at}.item_id has two readbacks`, { path: at });
    readbacks.set(rb.item_id, rb);
  });
  const dispositions = items.map(item => {
    if (item.state === "settled") return { item_id: item.item_id, disposition: "already_settled" };
    const rb = readbacks.get(item.item_id);
    if (!rb) return { item_id: item.item_id, disposition: "quarantined", reason: "no_readback" };
    if (rb.idempotency_key !== item.envelope_digest) {
      return { item_id: item.item_id, disposition: "quarantined", reason: "idempotency_key_mismatch" };
    }
    const disposition = READBACK_DISPOSITION[V5_READBACK_RESOLUTIONS[rb.readback]];
    return disposition === "quarantined"
      ? { item_id: item.item_id, disposition, reason: `readback_${rb.readback}` }
      : { item_id: item.item_id, disposition, readback: rb.readback };
  });
  const quarantined = dispositions.filter(d => d.disposition === "quarantined").map(d => d.item_id);
  return deepFreeze({
    schema_version: V5_RECOVERY_MATRIX_SCHEMA_VERSION,
    policy_digest: v5RecoveryMatrixPolicyDigest(),
    restore_id: request.restore_id,
    decision: quarantined.length ? "hold" : "reconciled",
    reason_id: quarantined.length ? "outbound_items_quarantined" : "outbound_items_all_reconciled",
    dispositions,
    quarantined_item_ids: quarantined,
    // A disposition is a verdict, not a send. Nothing here dispatches anything.
    releases_anything: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The degraded-mode projection. What each dependency outage does to the system,
// stated as a constant so it is hashed. It never claims full offline
// capability: every mode below is a REDUCTION, and outbound effects always
// quarantine behind reconciliation.
// ---------------------------------------------------------------------------

export const V5_DEGRADED_MODES = deepFreeze({
  record_layer_unavailable: {
    reads: "refuse_or_labeled_stale", writes: "refuse", outbound: "quarantine",
    recovery: "pitr_within_window_else_independent_copy_restore_to_disposable_target_then_promote",
  },
  edge_unavailable: {
    reads: "refuse", writes: "refuse", outbound: "quarantine",
    recovery: "redeploy_prior_release_after_edge_returns",
  },
  identity_provider_unavailable: {
    reads: "existing_sessions_only", writes: "existing_sessions_only", outbound: "quarantine",
    recovery: "no_new_sessions_until_identity_returns",
  },
  model_provider_unavailable: {
    reads: "records_without_model_summary", writes: "deterministic_verbs_only", outbound: "quarantine",
    recovery: "resume_model_features_after_provider_returns",
  },
  adapter_unavailable: {
    reads: "last_synced_labeled_stale", writes: "record_layer_only", outbound: "quarantine",
    recovery: "reconcile_adapter_within_one_business_day",
  },
  operator_device_lost: {
    reads: "other_enrolled_devices", writes: "other_enrolled_devices", outbound: "quarantine",
    recovery: "revoke_device_then_reenroll",
  },
});

export function v5DegradedModeProjection(unavailable) {
  if (!Array.isArray(unavailable)) fail("invalid_shape", "unavailable must be an array of registered dependency outages", { path: "unavailable" });
  const names = [...new Set(unavailable.map((n, i) => enumValue(n, Object.keys(V5_DEGRADED_MODES), `unavailable[${i}]`)))].sort();
  return deepFreeze({
    schema_version: V5_RECOVERY_MATRIX_SCHEMA_VERSION,
    policy_digest: v5RecoveryMatrixPolicyDigest(),
    unavailable: names,
    mode: names.length ? "degraded" : "normal",
    modes: Object.fromEntries(names.map(n => [n, V5_DEGRADED_MODES[n]])),
    outbound: names.length ? "quarantine_until_reconciliation" : "normal",
    claims_full_offline_capability: false,
    effects: V5_NO_EFFECTS,
  });
}

// ---------------------------------------------------------------------------
// The closed policy, digest-sealed. Every list sorted explicitly.
// ---------------------------------------------------------------------------

export function v5RecoveryMatrixPolicyPreimage() {
  return {
    schema_version: V5_RECOVERY_MATRIX_SCHEMA_VERSION,
    policy_version: V5_RECOVERY_MATRIX_POLICY_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    slice: "V5-F08",
    requirement_ids: ["Q034"],
    decision_ids: ["Q034.D1"],
    closes_seams: [V5_OUTBOUND_RECONCILIATION_SEAM, V5_RESTORE_AND_RECOVERY_MATRIX_SEAM].sort(),
    business_calendar_digest: V5_BUSINESS_CALENDAR_DIGEST,
    business_day_rule: "recovered_by_same_wall_clock_time_on_next_business_day",
    pitr_probe_margin_seconds: V5_PITR_PROBE_MARGIN_SECONDS,
    pitr_proof_target_kinds: [...V5_PITR_PROOF_TARGET_KINDS].sort(),
    rpo_measured_from: "proven_probe_write_instant_not_requested_point",
    retention_must_cover: "measured_exposure",
    // Bound by reference so a change to the admission half moves this digest too.
    backup_quarantine_policy_digest: v5BackupQuarantinePolicyDigest(),
    restore_exercise_receipt_kind: V5_RESTORE_EXERCISE_RECEIPT_KIND,
    rpo_max_seconds: V5_RPO_MAX_SECONDS,
    core_rto_max_seconds: V5_CORE_RTO_MAX_SECONDS,
    adapter_rto_calendar_free_bound_seconds: V5_ADAPTER_RTO_CALENDAR_FREE_BOUND_SECONDS,
    daily_copy_max_age_seconds: V5_DAILY_COPY_MAX_AGE_SECONDS,
    restore_exercise_max_age_seconds: V5_RESTORE_EXERCISE_MAX_AGE_SECONDS,
    restore_target_kinds: [...V5_RESTORE_TARGET_KINDS].sort(),
    admissible_restore_targets: [...V5_ADMISSIBLE_RESTORE_TARGETS].sort(),
    restore_exercise_checks_in_order: [...V5_RESTORE_EXERCISE_CHECKS],
    check_states: [...V5_CHECK_STATES].sort(),
    matrix_cells: [...V5_RECOVERY_MATRIX_CELLS].sort(),
    cell_states: [...V5_CELL_STATES].sort(),
    recovery_point_sources: [...V5_RECOVERY_POINT_SOURCES].sort(),
    outbound_item_states: [...V5_OUTBOUND_ITEM_STATES].sort(),
    outbound_dispositions: [...V5_OUTBOUND_DISPOSITIONS].sort(),
    readback_disposition: { ...READBACK_DISPOSITION },
    readback_authority: "workflow-effect-envelope.v5.js:V5_READBACK_RESOLUTIONS",
    degraded_modes: V5_DEGRADED_MODES,
    reason_ids: [...V5_RECOVERY_MATRIX_REASON_IDS].sort(),
    watermark_compared_against: "artifact_not_live_production",
    cells_independent: true,
    no_evidence_is_pass: false,
    indeterminate_is_pass: false,
    caller_may_move_floor: false,
    configured_values_accepted_as_measurement: false,
    outbound_age_out: false,
    outbound_operator_override: false,
    claims_full_offline_capability: false,
  };
}

export function v5RecoveryMatrixPolicyDigest() {
  return digest(v5RecoveryMatrixPolicyPreimage());
}

export function v5RecoveryMatrixPolicyCanonicalBytes() {
  return canonicalJson(v5RecoveryMatrixPolicyPreimage());
}
