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
// Every fixture case is addressed by a query value, so one module covers the
// whole clause table and no case can leak into another.

// Re-declared, not imported: substituting the module file means the copied
// reader must get its error class and its closed reason set from HERE, and the
// test asserts the real module's export names are all present below.
export const SEAM_STORE_UNREACHABLE_REASONS = Object.freeze([
  "the checks source answer did not parse",
  "the checks source credentials are not configured in this process",
  "the checks source refused the request",
  "the checks source was not reachable",
  "the database client is not available in this process",
  "the query did not finish",
  "the connection target for this store is not configured in this process",
].sort());

export class SeamStoreUnreachable extends Error {
  constructor(storeRef, because, cause) {
    super(`${storeRef}: ${because}`, cause === undefined ? undefined : { cause });
    if (!SEAM_STORE_UNREACHABLE_REASONS.includes(because))
      throw new TypeError(`${because} is not a registered store-unreachable reason`);
    this.name = "SeamStoreUnreachable";
    this.store_ref = storeRef;
    this.because = because;
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

const PREDECESSOR_ROWS = Object.freeze({
  // Accepted, with a receipt whose hash is FIXTURE_ACCEPTED_HASH.
  "WR-000046": Object.freeze([Object.freeze({
    feedback_ref: "OUTCOME-61382a72d992-v1",
    feedback_hash: FIXTURE_ACCEPTED_HASH,
    feedback_version: 1,
    outcome: "criteria_met",
    created_at: "2026-09-06T16:00:00.000Z",
    status: "accepted",
    accepted_feedback_hash: FIXTURE_ACCEPTED_HASH,
    accepted_at: "2026-09-06T16:03:09.000Z",
  })]),
  // Proposed and never signed. The near miss that must not pass.
  "WR-000040": Object.freeze([Object.freeze({
    feedback_ref: "OUTCOME-51decafef96d-v2",
    feedback_hash: `sha256:${"5".repeat(64)}`,
    feedback_version: 2,
    outcome: "criteria_not_met",
    created_at: "2026-09-11T18:00:00.000Z",
    status: "pending_human_acceptance",
    accepted_feedback_hash: null,
    accepted_at: null,
  })]),
  // No outcome rows at all.
  "WR-000054": Object.freeze([]),
});

const SCHEDULER_ROWS = Object.freeze({
  // All three clauses hold.
  "canary-join": Object.freeze([Object.freeze({
    service_key: "carr-fleet-sync", service_registered_at: T0, service_retired_at: null,
    run_key: "canary-join", state: "succeeded", exit_code: 0, attempt: 1,
    started_at: T0, ended_at: T1, observed_at: T1,
    evidence_ref: "receipt:canary-join", source_kind: "scheduler", source_ref: "bin/run-scheduled.sh",
  })]),
  // The canary names no receipt.
  "canary-unbound": Object.freeze([Object.freeze({
    service_key: "carr-fleet-sync", service_registered_at: T0, service_retired_at: null,
    run_key: "canary-unbound", state: "succeeded", exit_code: 0, attempt: 1,
    started_at: T0, ended_at: T1, observed_at: T1,
    evidence_ref: null, source_kind: "scheduler", source_ref: "bin/run-scheduled.sh",
  })]),
  // Dispatch and readback share one instant — rows written in one transaction.
  "canary-same-instant": Object.freeze([Object.freeze({
    service_key: "carr-fleet-sync", service_registered_at: T0, service_retired_at: null,
    run_key: "canary-same-instant", state: "succeeded", exit_code: 0, attempt: 1,
    started_at: T0, ended_at: T0, observed_at: T0,
    evidence_ref: "receipt:same-instant", source_kind: "scheduler", source_ref: "bin/run-scheduled.sh",
  })]),
  // The readback observes a different receipt than the dispatch named.
  "canary-mismatch": Object.freeze([
    Object.freeze({
      service_key: "carr-fleet-sync", service_registered_at: T0, service_retired_at: null,
      run_key: "canary-mismatch", state: "running", exit_code: null, attempt: 1,
      started_at: T0, ended_at: null, observed_at: T0,
      evidence_ref: "receipt:dispatched", source_kind: "scheduler", source_ref: "bin/run-scheduled.sh",
    }),
    Object.freeze({
      service_key: "carr-fleet-sync", service_registered_at: T0, service_retired_at: null,
      run_key: "canary-mismatch", state: "succeeded", exit_code: 0, attempt: 1,
      started_at: T0, ended_at: T1, observed_at: T1,
      evidence_ref: "receipt:some-other-run", source_kind: "scheduler", source_ref: "bin/run-scheduled.sh",
    }),
  ]),
  // Dispatched and still in flight: no readback row.
  "canary-inflight": Object.freeze([Object.freeze({
    service_key: "carr-fleet-sync", service_registered_at: T0, service_retired_at: null,
    run_key: "canary-inflight", state: "running", exit_code: null, attempt: 1,
    started_at: T0, ended_at: null, observed_at: T0,
    evidence_ref: "receipt:inflight", source_kind: "scheduler", source_ref: "bin/run-scheduled.sh",
  })]),
  // The service is in the ledger and the canary never ran.
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
});

/** The one query value that makes a fixture store unreachable, on every store. */
export const FIXTURE_UNREACHABLE = "unreachable";

export async function fetchPredecessorOutcomeRows({ workRequestRef }) {
  const storeRef = "record-layer:ops.sourced_work_request_outcome_feedback";
  if (workRequestRef === FIXTURE_UNREACHABLE)
    throw new SeamStoreUnreachable(storeRef, "the query did not finish");
  return { store_ref: storeRef, rows: PREDECESSOR_ROWS[workRequestRef] ?? [] };
}

export async function fetchSchedulerLedgerRows({ serviceKey, canaryRunKey }) {
  const storeRef = "control-plane:ops.service+ops.run";
  if (canaryRunKey === FIXTURE_UNREACHABLE)
    throw new SeamStoreUnreachable(storeRef, "the query did not finish");
  if (serviceKey !== "carr-fleet-sync") return { store_ref: storeRef, rows: [] };
  return { store_ref: storeRef, rows: SCHEDULER_ROWS[canaryRunKey] ?? [] };
}

export async function fetchCheckConclusionRows({ commitSha, checkName }) {
  const storeRef = "github:checks";
  if (checkName === FIXTURE_UNREACHABLE)
    throw new SeamStoreUnreachable(storeRef, "the checks source was not reachable");
  void commitSha;
  return { store_ref: storeRef, rows: CHECK_ROWS[checkName] ?? [] };
}
