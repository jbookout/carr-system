// The three seam stores, served from fixture rows instead of from Postgres and
// GitHub. It lives in test/ and NOT in src/, so a production module cannot
// import it — the parser-backed import scan in
// gate-zero-seam-readers.v5.test.mjs proves that rather than asserting it.
//
// HOW IT IS USED, and why it is a substitution rather than an injection. The
// reader module takes no store handle: there is no argument, no setter and no
// env var that could point it somewhere else, which is the whole point of the
// slice. So the ruled path is proved the only honest way left — the test copies
// mcp-server/src into a scratch tree, pastes a fixture decision id onto the
// exact `decision_id:` line Joe will paste his onto, REPLACES the store module
// file with this one, and imports the copied reader. What runs is the real
// reader, the real ruling gate and the real derivation over known rows.
//
// THE EXPORT NAMES AND SHAPES ARE THE CONTRACT, and the test asserts this file's
// export names are identical to the real store module's before it substitutes —
// so a store function added or renamed in src turns this red instead of silently
// leaving a path unproved.
//
// THE ROWS ARE SHAPED AS PRODUCTION WRITES THEM, and that is not decoration.
// An earlier fixture invented `source_kind: "scheduler"` for card 12, a value
// db/schema.sql's `run_source_kind_check` does not permit and no writer in this
// repository emits. The reader was written against the invention, so the passing
// test proved a clause that could never have matched a real ops.run row. The
// card 12 rows below carry `wrapper` / `bin/run-scheduled.sh`, which is what
// bin/run-scheduled.sh:275 actually writes, and there is one negative row for
// each field the receipt-binding clause reads.
//
// Every fixture case is addressed by a query value, so one module covers the
// whole clause table and no case can leak into another.

// Re-declared, not imported: substituting the module file means the copied
// reader must get its error class and its closed reason set from HERE, and the
// test asserts the real module's export names are all present below.
// MODULE-PRIVATE, exactly as in the real store module, and for the same reason:
// the substituted reader reads `.because` straight into an answer, so the set it
// can come back with has to be a set nothing outside this file can enumerate or
// extend. Neither name is exported, so the export-parity check in the test still
// sees this file's surface as the real module's.
const REGISTERED_REASONS = Object.freeze([
  "the checks source answer did not parse",
  "the checks source credentials are not configured in this process",
  "the checks source refused the request",
  "the checks source was not reachable",
  "the database client is not available in this process",
  "the connection target for this store is not configured in this process",
  "the query did not finish",
  "the reason this store was unreachable is not a registered one",
]);

const REGISTERED_STORE_TOKENS = Object.freeze([
  "record-layer:work-request-outcome-feedback",
  "control-plane:ops.service+ops.run",
  "github:checks",
  "a-store-this-file-does-not-serve",
]);

function own(target, key, value, enumerable) {
  Object.defineProperty(target, key, { value, writable: false, enumerable, configurable: false });
}

function causeKind(cause) {
  try {
    if (cause === undefined || cause === null) return "none";
    if (cause instanceof SeamStoreUnreachable) return "a-seam-store-that-was-unreachable";
    if (cause instanceof Error) return "an-error";
    return "not-an-error";
  } catch {
    return "undetermined";
  }
}

export class SeamStoreUnreachable extends Error {
  constructor(storeRef, because, cause) {
    if (new.target !== SeamStoreUnreachable)
      throw new TypeError("this error type is final and cannot be extended");
    const store = REGISTERED_STORE_TOKENS.includes(storeRef)
      ? storeRef : "a-store-this-file-does-not-serve";
    const reason = REGISTERED_REASONS.includes(because)
      ? because : "the reason this store was unreachable is not a registered one";
    const message = `${store}: ${reason}`;
    super(message);
    own(this, "name", "SeamStoreUnreachable", false);
    own(this, "message", message, false);
    own(this, "stack", `SeamStoreUnreachable: ${message}`, false);
    own(this, "store_ref", store, true);
    own(this, "because", reason, true);
    own(this, "cause_kind", causeKind(cause), true);
    Object.freeze(this);
  }
}

/** The acceptance-receipt hash the fixture's accepted WR-000046 row carries. */
export const FIXTURE_ACCEPTED_HASH = `sha256:${"4".repeat(64)}`;
/** A well-formed hash that no fixture row carries. A forgery, in other words. */
export const FIXTURE_FORGED_HASH = `sha256:${"f".repeat(64)}`;

export const FIXTURE_COMMIT_SHA = "a".repeat(40);
export const FIXTURE_OTHER_COMMIT_SHA = "b".repeat(40);

const T0 = "2026-09-11T17:00:00.000Z";
const T1 = "2026-09-11T17:00:30.000Z";
const T2 = "2026-09-11T17:00:31.000Z";

/**
 * Rows in the shape fetchPredecessorOutcomeRows builds them: a status, the two
 * hashes that are compared, and whether the work_request_card actually carried
 * the detail for this receipt. No feedback ref, no stored outcome and no
 * timestamp — the real store drops those before a reader sees them.
 */
const PREDECESSOR_ROWS = Object.freeze({
  // Accepted, complete, with a receipt whose hash is FIXTURE_ACCEPTED_HASH. In
  // production the proposal hash equals the receipt hash, and it does here.
  "WR-000046": Object.freeze([Object.freeze({
    status: "accepted",
    detail_present: true,
    accepted_feedback_hash: FIXTURE_ACCEPTED_HASH,
    feedback_hash: FIXTURE_ACCEPTED_HASH,
  })]),
  // Proposed and never signed. The near miss that must not pass.
  "WR-000040": Object.freeze([Object.freeze({
    status: "pending_human_acceptance",
    detail_present: true,
    accepted_feedback_hash: null,
    feedback_hash: `sha256:${"5".repeat(64)}`,
  })]),
  // No outcome rows at all.
  "WR-000054": Object.freeze([]),
});

/**
 * ops.run rows exactly as bin/run-scheduled.sh writes them.
 *
 *   source_kind  `wrapper` — one of collector|registry|wrapper|operator, the
 *                closed set db/schema.sql permits. There is no "scheduler".
 *   source_ref   `bin/run-scheduled.sh` — the wrapper's own path.
 *   one row      per run, carrying started_at, ended_at and an observed_at
 *                stamped when the row lands. Dispatch and observation are the
 *                same row in production, and the derivation handles that.
 *
 * The negative cases below change ONE field each, so a clause that stopped
 * reading a field fails on exactly one case rather than on none.
 */
const RUN = Object.freeze({
  service_key: "carr-fleet-sync", service_registered_at: T0, service_retired_at: null,
  state: "succeeded", exit_code: 0, attempt: 1,
  started_at: T0, ended_at: T1, observed_at: T2,
  source_kind: "wrapper", source_ref: "bin/run-scheduled.sh",
});

const SCHEDULER_ROWS = Object.freeze({
  // All three clauses hold: the wrapper's own row, carrying a receipt.
  "canary-join": Object.freeze([Object.freeze({ ...RUN,
    run_key: "canary-join", evidence_ref: "ops.run:carr-fleet-sync.canary-join" })]),

  // NEGATIVE, evidence_ref — and this is not a hypothetical. It is the row
  // bin/run-scheduled.sh writes TODAY, because it passes no --evidence-ref. A
  // ruling landing this afternoon would get exactly this answer from a live
  // scheduled job, and the answer is a refusal that names the owed change.
  "canary-today": Object.freeze([Object.freeze({ ...RUN,
    run_key: "canary-today", evidence_ref: null })]),

  // NEGATIVE, source_kind — an `operator` row is a hand-run, not a dispatch.
  "canary-hand-run": Object.freeze([Object.freeze({ ...RUN,
    run_key: "canary-hand-run", source_kind: "operator",
    evidence_ref: "ops.run:carr-fleet-sync.canary-hand-run" })]),

  // NEGATIVE, source_kind — a `collector` row is a probe writing about a job,
  // not the wrapper that dispatched it.
  "canary-probe": Object.freeze([Object.freeze({ ...RUN,
    run_key: "canary-probe", source_kind: "collector",
    source_ref: "bin/probe-keepalive.py",
    evidence_ref: "ops.run:carr-fleet-sync.canary-probe" })]),

  // NEGATIVE, source_ref — the right kind, written by a different wrapper.
  "canary-foreign-wrapper": Object.freeze([Object.freeze({ ...RUN,
    run_key: "canary-foreign-wrapper", source_ref: "bin/deploy-worker.sh",
    evidence_ref: "ops.run:carr-fleet-sync.canary-foreign-wrapper" })]),

  // Dispatch and observation share one instant — rows written in one transaction.
  "canary-same-instant": Object.freeze([Object.freeze({ ...RUN,
    run_key: "canary-same-instant", ended_at: T0, observed_at: T0,
    evidence_ref: "ops.run:carr-fleet-sync.canary-same-instant" })]),

  // The observation is of a different receipt than the dispatch named.
  "canary-mismatch": Object.freeze([
    Object.freeze({ ...RUN,
      run_key: "canary-mismatch", state: "running", exit_code: null,
      ended_at: null, observed_at: T0,
      evidence_ref: "ops.run:carr-fleet-sync.canary-mismatch.1" }),
    Object.freeze({ ...RUN,
      run_key: "canary-mismatch", started_at: T0, ended_at: T1, observed_at: T1,
      evidence_ref: "ops.run:carr-fleet-sync.some-other-run" }),
  ]),

  // Dispatched and still in flight: nothing has ended, so nothing is observed.
  "canary-inflight": Object.freeze([Object.freeze({ ...RUN,
    run_key: "canary-inflight", state: "running", exit_code: null,
    ended_at: null, observed_at: T0,
    evidence_ref: "ops.run:carr-fleet-sync.canary-inflight" })]),

  // The left join found the service and no run: the canary never ran.
  "canary-never-ran": Object.freeze([Object.freeze({
    service_key: "carr-fleet-sync", service_registered_at: T0, service_retired_at: null,
    run_key: null, state: null, exit_code: null, attempt: null,
    started_at: null, ended_at: null, observed_at: null,
    evidence_ref: null, source_kind: null, source_ref: null,
  })]),
});

const CHECK_ROWS = Object.freeze({
  "db-acceptance": Object.freeze([Object.freeze({
    name: "db-acceptance", head_sha: FIXTURE_COMMIT_SHA, status: "completed",
    conclusion: "success", started_at: T0, completed_at: T1,
    html_url: "https://github.test/run/1",
  })]),
  // A re-run: two completed runs, and the later one is what the merge gate acts on.
  "rerun-check": Object.freeze([
    Object.freeze({
      name: "rerun-check", head_sha: FIXTURE_COMMIT_SHA, status: "completed",
      conclusion: "success", started_at: T0, completed_at: T0,
      html_url: "https://github.test/run/2",
    }),
    Object.freeze({
      name: "rerun-check", head_sha: FIXTURE_COMMIT_SHA, status: "completed",
      conclusion: "failure", started_at: T0, completed_at: T1,
      html_url: "https://github.test/run/3",
    }),
  ]),
  // Still queued: no conclusion exists, so none is reported.
  "queued-check": Object.freeze([Object.freeze({
    name: "queued-check", head_sha: FIXTURE_COMMIT_SHA, status: "queued",
    conclusion: null, started_at: null, completed_at: null,
    html_url: "https://github.test/run/4",
  })]),
  // A completed run that belongs to a DIFFERENT commit than the one asked about.
  "wrong-commit": Object.freeze([Object.freeze({
    name: "wrong-commit", head_sha: FIXTURE_OTHER_COMMIT_SHA, status: "completed",
    conclusion: "success", started_at: T0, completed_at: T1,
    html_url: "https://github.test/run/5",
  })]),
  // A word GitHub's API does not document. Not passed through: reported as
  // unrecognized, so the only conclusion strings a consumer ever sees are the
  // constants in the reader module.
  "invented-conclusion": Object.freeze([Object.freeze({
    name: "invented-conclusion", head_sha: FIXTURE_COMMIT_SHA, status: "completed",
    conclusion: "everything is fine", started_at: T0, completed_at: T1,
    html_url: "https://github.test/run/6",
  })]),
});

/** The one query value that makes a fixture store unreachable, on every store. */
export const FIXTURE_UNREACHABLE = "unreachable";

/**
 * The one canary key for which this store answers about a DIFFERENT store than
 * the one it was asked for. Not a thing a real store does — it is how the
 * reader's post-fetch identity check is made reachable, so that check is proved
 * rather than merely written.
 */
export const FIXTURE_WRONG_STORE = "canary-from-another-store";

export async function fetchPredecessorOutcomeRows(query) {
  const workRequestRef = query?.workRequestRef;
  const storeRef = "record-layer:work-request-outcome-feedback";
  if (workRequestRef === FIXTURE_UNREACHABLE)
    throw new SeamStoreUnreachable(storeRef, "the query did not finish");
  return { store_ref: storeRef, rows: PREDECESSOR_ROWS[workRequestRef] ?? [] };
}

export async function fetchSchedulerLedgerRows(query) {
  const serviceKey = query?.serviceKey;
  const canaryRunKey = query?.canaryRunKey;
  const storeRef = "control-plane:ops.service+ops.run";
  if (canaryRunKey === FIXTURE_UNREACHABLE)
    throw new SeamStoreUnreachable(storeRef, "the query did not finish");
  if (canaryRunKey === FIXTURE_WRONG_STORE)
    return { store_ref: "github:checks", rows: SCHEDULER_ROWS["canary-join"] };
  if (serviceKey !== "carr-fleet-sync") return { store_ref: storeRef, rows: [] };
  return { store_ref: storeRef, rows: SCHEDULER_ROWS[canaryRunKey] ?? [] };
}

export async function fetchCheckConclusionRows(query) {
  const checkName = query?.checkName;
  const storeRef = "github:checks";
  if (checkName === FIXTURE_UNREACHABLE)
    throw new SeamStoreUnreachable(storeRef, "the checks source was not reachable");
  return { store_ref: storeRef, rows: CHECK_ROWS[checkName] ?? [] };
}
