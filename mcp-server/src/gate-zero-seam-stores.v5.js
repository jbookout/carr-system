// DoctorCRE v5 slice V5-A02, the seam half: THE THREE STORES, AND NOTHING ELSE.
//
// This file fetches rows. It decides nothing. Every function here either returns
// the rows a store actually holds for one addressed query, or throws
// SeamStoreUnreachable naming what it could not reach. It contains no
// classification, no comparison, no threshold and no vocabulary a consumer could
// act on — those live in gate-zero-seam-evidence.v5.js, which never touches a
// store, and the two halves are kept apart so that neither can quietly become
// the other.
//
// WHY THE SPLIT IS THE POINT. The defect this slice exists to prevent is a
// function that turns a description of evidence into an outcome. A fetcher that
// also judges can be handed a fabricated connection and will judge it; a judge
// that cannot fetch can only ever be given rows by the one caller that is
// allowed to fetch them. So: I/O here, judgment there, and the reader module is
// the only thing that holds both.
//
// NO CALLER EVER SUPPLIES A CONNECTION. There is no handle parameter, no client
// parameter, no `env` parameter and no injectable opener. The query arguments
// below ADDRESS a row — which work request, which service, which commit — and
// addressing is not asserting. A caller can say which row to look at; it can
// never say what is in it.
//
// WHERE THE CONNECTION COMES FROM, and why that is not the env override the
// ruling table forbids. The RULING is the switch: while a seam's decision id is
// null its reader never calls into this file at all. What the environment
// supplies is only WHERE the ruled store lives — the same read-only DSN and the
// same GitHub credentials every other ops-side reader in this repository uses.
// An absent DSN is not a fallback and not a degraded mode: it throws, the reader
// reports unreachable, and nothing optimistic is returned.
//
// THESE RUN OPS-SIDE, NOT IN THE WORKER. Gate Zero is a control-plane gate; its
// readers run in the ops/CI process, where `process.env` and a Postgres socket
// exist. `pg` is imported DYNAMICALLY inside the two database functions so the
// Worker bundle never pulls it in through this module's import graph.

/**
 * WHY `because` IS A CLOSED SET AND NOT THE UNDERLYING MESSAGE. A reader puts
 * this string straight into its answer, and an upstream message is text nobody
 * in this file controls: a driver error can carry a fragment of a DSN, a host
 * name, or a word the privileged-word sweep closes over. So the reason a store
 * was unreachable is one of the phrases below, and the real cause travels as
 * `error.cause` for a human reading a log — never into an answer.
 */
export const SEAM_STORE_UNREACHABLE_REASONS = Object.freeze([
  "the checks source answer did not parse",
  "the checks source credentials are not configured in this process",
  "the checks source refused the request",
  "the checks source was not reachable",
  "the database client is not available in this process",
  "the connection target for this store is not configured in this process",
  "the query did not finish",
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

function configured(name, storeRef, because) {
  const env = globalThis.process?.env;
  const value = env && typeof env[name] === "string" ? env[name].trim() : "";
  if (!value) throw new SeamStoreUnreachable(storeRef, because);
  return value;
}

/** One read-only query, one pool, closed before returning. No shared state. */
async function readOnlyQuery(storeRef, text, params) {
  const connectionString = configured("DATABASE_URL_READER", storeRef,
    "the connection target for this store is not configured in this process");
  let pg;
  try {
    ({ default: pg } = await import("pg"));
  } catch (cause) {
    throw new SeamStoreUnreachable(storeRef, "the database client is not available in this process", cause);
  }
  const pool = new pg.Pool({ connectionString, max: 1, statement_timeout: 15000 });
  try {
    const client = await pool.connect();
    try {
      await client.query("begin read only");
      const result = await client.query(text, params);
      await client.query("commit");
      return result.rows;
    } finally {
      client.release();
    }
  } catch (cause) {
    if (cause instanceof SeamStoreUnreachable) throw cause;
    throw new SeamStoreUnreachable(storeRef, "the query did not finish", cause);
  } finally {
    await pool.end().catch(() => {});
  }
}

// ---------------------------------------------------------------------------
// Card 11's store — the record layer's own outcome feedback.
// ---------------------------------------------------------------------------

/**
 * Every outcome-feedback row a Work Request carries, newest first, with the
 * acceptance receipt beside it.
 *
 * `status` is DERIVED IN SQL FROM THE RECEIPT TABLE, not read from a column: an
 * outcome is accepted exactly when
 * ops.sourced_work_request_outcome_feedback_acceptance_receipt holds a row for
 * it, which is the same test ops.accept_sourced_work_request_outcome_feedback
 * enforces when it writes one. `accepted_feedback_hash` is the receipt's own
 * hash column, which is what the caller's asked-about hash is matched against —
 * never the proposal's, because a proposal is what the machine wrote and the
 * receipt is what Joe signed.
 */
export async function fetchPredecessorOutcomeRows({ workRequestRef }) {
  const storeRef = "record-layer:ops.sourced_work_request_outcome_feedback";
  const rows = await readOnlyQuery(storeRef, `
    select f.feedback_ref,
           f.feedback_hash,
           f.feedback_version,
           f.outcome,
           f.created_at,
           case when r.id is null then 'pending_human_acceptance' else 'accepted' end as status,
           r.feedback_hash as accepted_feedback_hash,
           r.accepted_at
      from ops.work_request w
      join ops.sourced_work_request_outcome_feedback f on f.work_request_id = w.id
      left join ops.sourced_work_request_outcome_feedback_acceptance_receipt r on r.feedback_id = f.id
     where w.ref = $1
     order by f.feedback_version desc`, [workRequestRef]);
  return { store_ref: storeRef, rows };
}

// ---------------------------------------------------------------------------
// Card 12's store — the Control Plane ledger and bin/run-scheduled.sh's runs.
// ---------------------------------------------------------------------------

/**
 * The service row for a scheduled canary and every run row the scheduler wrote
 * for it, newest observation first.
 *
 * TIMESTAMPS ARE THE WHOLE POINT of this query, so all four the clauses need are
 * selected and none is computed here: `started_at` is dispatch, `ended_at` and
 * `observed_at` are readback, and `registered_at`/`retired_at` say whether the
 * ledger still carries the service at all. Ordering by `observed_at desc` is a
 * presentation choice; the reader compares instants and never trusts position.
 */
export async function fetchSchedulerLedgerRows({ serviceKey, canaryRunKey }) {
  const storeRef = "control-plane:ops.service+ops.run";
  const rows = await readOnlyQuery(storeRef, `
    select s.key            as service_key,
           s.registered_at  as service_registered_at,
           s.retired_at     as service_retired_at,
           r.run_key,
           r.state,
           r.exit_code,
           r.attempt,
           r.started_at,
           r.ended_at,
           r.observed_at,
           r.evidence_ref,
           r.source_kind,
           r.source_ref
      from ops.service s
      left join ops.run r on r.service_id = s.id and r.run_key = $2
     where s.key = $1
     order by r.observed_at desc nulls last`, [serviceKey, canaryRunKey]);
  return { store_ref: storeRef, rows };
}

// ---------------------------------------------------------------------------
// Card 13's store — hosted CI, through the GitHub checks API.
// ---------------------------------------------------------------------------

/**
 * Every check run GitHub holds for one commit under one check name.
 *
 * The commit sha and the check name ADDRESS the query and are validated by the
 * reader before they reach here. The repository is read from the environment,
 * not from the caller: a caller who could name the repository could name a
 * repository whose checks it controls.
 */
export async function fetchCheckConclusionRows({ commitSha, checkName }) {
  const storeRef = "github:checks";
  const missing = "the checks source credentials are not configured in this process";
  const token = configured("GITHUB_TOKEN", storeRef, missing);
  const repository = configured("GITHUB_REPOSITORY", storeRef, missing);
  const url = `https://api.github.com/repos/${repository}/commits/${commitSha}/check-runs`
    + `?check_name=${encodeURIComponent(checkName)}&per_page=100`;
  let response;
  try {
    response = await fetch(url, {
      headers: {
        accept: "application/vnd.github+json",
        authorization: `Bearer ${token}`,
        "user-agent": "carr-gate-zero-seam-reader",
        "x-github-api-version": "2022-11-28",
      },
    });
  } catch (cause) {
    throw new SeamStoreUnreachable(storeRef, "the checks source was not reachable", cause);
  }
  if (!response.ok)
    throw new SeamStoreUnreachable(storeRef, "the checks source refused the request",
      new Error(`http ${response.status}`));
  let body;
  try {
    body = await response.json();
  } catch (cause) {
    throw new SeamStoreUnreachable(storeRef, "the checks source answer did not parse", cause);
  }
  const runs = Array.isArray(body?.check_runs) ? body.check_runs : [];
  return {
    store_ref: storeRef,
    rows: runs.map(run => ({
      name: run?.name ?? null,
      head_sha: run?.head_sha ?? null,
      status: run?.status ?? null,
      conclusion: run?.conclusion ?? null,
      started_at: run?.started_at ?? null,
      completed_at: run?.completed_at ?? null,
      html_url: run?.html_url ?? null,
    })),
  };
}
