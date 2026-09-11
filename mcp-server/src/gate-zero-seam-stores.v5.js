// DoctorCRE v5 slice V5-A02, the seam half: THE THREE STORES, AND NOTHING ELSE.
//
// This file fetches rows. It decides nothing. Every function here either returns
// the rows a store actually holds for one addressed query, or throws
// SeamStoreUnreachable naming what it could not reach. It contains no
// classification, no comparison, no threshold and no vocabulary a consumer could
// act on — those live as MODULE-PRIVATE functions inside
// gate-zero-seam-readers.v5.js, which is the only module that imports this one.
//
// WHY THE SPLIT IS THE POINT. The defect this slice exists to prevent is a
// function that turns a description of evidence into an outcome. A fetcher that
// also judges can be handed a fabricated connection and will judge it. So the
// I/O is here, exported and judgment-free; the judgment is private to the reader
// that fetched the rows, and is exported by nothing.
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
import { ORGANIZATION_TENANT_ID } from "./identity.js";

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

/**
 * One addressed field out of a query, or a refusal with a FIXED message.
 *
 * The three fetchers below are exported, so they can be called by anything with
 * anything — including nothing. Two ways a caller's object writes its own text
 * into an error are closed here:
 *
 *   * DESTRUCTURING IN THE SIGNATURE answered a missing argument with the
 *     engine's own TypeError, whose text names the parameter.
 *   * A REVOKED PROXY, a throwing getter or a throwing trap answers a plain
 *     property read with the ENGINE's message or the CALLER's. Node's own text
 *     for a revoked proxy carries a privileged substring, which is how this was
 *     found, and a throwing getter would carry whatever the caller wrote.
 *
 * So the read is caught and the shape is checked here, and what comes back on a
 * bad call is one sentence written in this file.
 */
function addressed(query, key) {
  let value;
  try {
    value = query === null || query === undefined ? undefined : Reflect.get(Object(query), key);
  } catch {
    value = undefined;
  }
  if (typeof value !== "string" || value.length === 0)
    throw new TypeError("this store takes an addressed query");
  return value;
}

function configured(name, storeRef, because) {
  const env = globalThis.process?.env;
  const value = env && typeof env[name] === "string" ? env[name].trim() : "";
  if (!value) throw new SeamStoreUnreachable(storeRef, because);
  return value;
}

/**
 * Several statements, ONE read-only transaction, one pool, closed before
 * returning. One transaction because the clauses compare facts to each other: a
 * receipt read at one instant and a card read at another could disagree, and a
 * reader that joined two instants would be reporting a state that never existed.
 */
async function readOnlyStatements(storeRef, statements) {
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
      const results = [];
      for (const { text, params } of statements) results.push((await client.query(text, params)).rows);
      await client.query("commit");
      return results;
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
 * A Work Request's outcome feedback, through the three doors the record layer
 * already opens — and NOT through ops.sourced_work_request_outcome_feedback,
 * which is granted to nobody.
 *
 * THAT IS A BOUNDARY, NOT A MISSING GRANT. The proposal table is reached only by
 * security-definer functions; a handler that selected from it directly would
 * fail with sqlstate 42501, and tools/test-handler-reads-are-granted.py refuses
 * the attempt at push time. It refused this reader's first draft, which is how
 * the boundary was found. The fix is to read what the record layer exposes, not
 * to widen a role.
 *
 *   ops.sourced_work_request_outcome_feedback_acceptance_receipt — THE
 *     AUTHORITY. One row per acceptance, carrying the hash a human signed. An
 *     outcome is accepted exactly when this table holds a row for it.
 *   ops.work_request_card — the readable detail: feedback_ref, the proposal's
 *     own hash and the outcome it concluded, for the latest accepted feedback
 *     and up to twenty of its history.
 *   ops.pending_sourced_work_request_outcome_feedback — the still-unsigned
 *     proposal, so "proposed and never accepted" stays distinguishable from
 *     "nothing was ever proposed". Without it both would look like absence, and
 *     those are different facts about a predecessor.
 *
 * `accepted_feedback_hash` IS SET ONLY FROM THE RECEIPT. The card's own
 * `feedback_hash` is the proposal's, and it is carried separately under that
 * name. They are equal in production, which is exactly why they must not be the
 * same field here: a reader that matched on the proposal's hash would look
 * correct for as long as nothing ever went wrong.
 *
 * AND A RECEIPT WITHOUT ITS CARD DETAIL IS A MISSING ROW, NOT AN ACCEPTED ONE.
 * An earlier draft synthesized the absent detail as nulls, so a receipt the card
 * did not carry came back looking accepted with a null outcome and could still be
 * admitted. It is now marked `detail_present: false` and the derivation refuses
 * on it. Nothing is invented to fill a row the store did not have.
 */
export async function fetchPredecessorOutcomeRows(query) {
  const storeRef = "record-layer:work-request-outcome-feedback";
  const workRequestRef = addressed(query, "workRequestRef");
  const [receipts, cards, pending] = await readOnlyStatements(storeRef, [
    { text: `select r.feedback_hash as accepted_feedback_hash, r.accepted_at
               from ops.sourced_work_request_outcome_feedback_acceptance_receipt r
               join ops.work_request w on w.id = r.work_request_id
              where w.ref = $1
              order by r.accepted_at desc`, params: [workRequestRef] },
    { text: `select outcome_feedback, outcome_feedback_history, accepted_feedback_count
               from ops.work_request_card($1::text, $2::text)`,
      params: [workRequestRef, ORGANIZATION_TENANT_ID] },
    { text: `select feedback_ref, feedback_hash, outcome, status
               from ops.pending_sourced_work_request_outcome_feedback($1::text, $2::text)`,
      params: [workRequestRef, ORGANIZATION_TENANT_ID] },
  ]);

  const card = cards[0] ?? {};
  const detail = new Map();
  for (const entry of [card.outcome_feedback, ...(Array.isArray(card.outcome_feedback_history)
    ? card.outcome_feedback_history : [])])
    if (entry && typeof entry === "object" && typeof entry.feedback_hash === "string")
      detail.set(entry.feedback_hash, entry);

  // NOTHING FREE-FORM TRAVELS. A row carries a status, the two hashes that are
  // compared, and whether the card actually held the detail for this receipt.
  // The feedback ref, the stored outcome and the acceptance timestamp are read
  // and deliberately dropped here: no store text reaches an answer, so no store
  // text can carry a word into one.
  const rows = receipts.map(receipt => {
    const entry = detail.get(receipt.accepted_feedback_hash);
    return {
      status: "accepted",
      detail_present: entry !== undefined,
      accepted_feedback_hash: receipt.accepted_feedback_hash,
      feedback_hash: entry?.feedback_hash ?? null,
    };
  });
  for (const proposal of pending)
    rows.push({
      status: proposal.status ?? "pending_human_acceptance",
      detail_present: true,
      accepted_feedback_hash: null,
      feedback_hash: proposal.feedback_hash ?? null,
    });
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
export async function fetchSchedulerLedgerRows(query) {
  const storeRef = "control-plane:ops.service+ops.run";
  const serviceKey = addressed(query, "serviceKey");
  const canaryRunKey = addressed(query, "canaryRunKey");
  const [rows] = await readOnlyStatements(storeRef, [{ text: `
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
     order by r.observed_at desc nulls last`, params: [serviceKey, canaryRunKey] }]);
  return { store_ref: storeRef, rows };
}

// ---------------------------------------------------------------------------
// Card 13's store — hosted CI, through the GitHub checks API.
// ---------------------------------------------------------------------------

/**
 * Every check run GitHub holds for one commit under one check name.
 *
 * The head sha and the check name ADDRESS the query and are validated by the
 * reader before they reach here. The repository is read from the environment,
 * not from the caller: a caller who could name the repository could name a
 * repository whose checks it controls.
 */
export async function fetchCheckConclusionRows(query) {
  const storeRef = "github:checks";
  const headSha = addressed(query, "headSha");
  const checkName = addressed(query, "checkName");
  const missing = "the checks source credentials are not configured in this process";
  const token = configured("GITHUB_TOKEN", storeRef, missing);
  const repository = configured("GITHUB_REPOSITORY", storeRef, missing);
  const url = `https://api.github.com/repos/${repository}/commits/${headSha}/check-runs`
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
