// DoctorCRE v5 slice V5-A02 — THE GATE ZERO PRODUCER, Step A.
//
// WHAT THIS FILE IS, in one sentence: the seam the three ruled evidence readers
// were built for. They answer truthfully about whatever row they are pointed at;
// pointing them is this module's job, and nothing else in this repository has
// ever been allowed to do it.
//
// WHAT IT DOES, in the order it does it:
//
//   1. AIMS the three bound readers at NAMED rows, from bindings that are
//      module-private constants or derived from the staffed seat.
//   2. APPLIES the three deterministic clauses V5-A02's checkable_done names —
//      in production form, over READINGS, with the conditional mood gone.
//   3. EARNS `negative_admission_result` rather than writing it, by mutating a
//      reading in each required way and requiring its own clause to refuse.
//   4. ASSEMBLES one `consumer-gate-receipt.v1` — all twenty-one fields — and
//      takes its digest.
//
// WHAT IT IS NOT, and every one of these is a door somebody could have built:
//
//   * IT TAKES NO ARGUMENT. `v5A02GateZeroEmitOutcome` has arity zero. There is
//     no evidence parameter, no hash parameter, no identity parameter, no head
//     revision parameter and no reader parameter. The PR 985 defect was a public
//     function that turned a CALLER'S DESCRIPTION of evidence into a verdict;
//     the standing rule of 2026-09-11 closes it under any name, and the cheapest
//     way to stay closed is to have nowhere to put one.
//   * IT READS NO ENVIRONMENT VARIABLE. Not for a binding, not for an identity,
//     not for a switch. The ONE thing the environment supplies is where a ruled
//     store lives, and that is `gate-zero-seam-stores.v5.js`'s business, reached
//     only through a reader whose ruling already said it may look.
//   * IT EXPORTS NO SETTER AND NO REGISTRY. Every binding below is either a
//     frozen module-private constant or DERIVED at call time from the candidate
//     tree, the ruled stores and the authenticated call. There is no line a
//     human pastes, so there is no human step inside this slice at all.
//   * IT WRITES NOTHING. No database, no file, no verb. Step A emits a VALUE.
//     Recording that value as a row is Step B, it is the heavy path, and it is
//     waiting on Joe's ruling about which authority may write it.
//
// ---------------------------------------------------------------------------
// THE RUN BINDING, AND WHY NOBODY PASTES IT.
//
// The addresses a Gate Zero run stands on are facts about THAT RUN: the
// candidate's head revision, the scheduler service and the canary the wrapper
// dispatched, the actor that built the candidate, and each Work-Request
// predecessor's acceptance receipt. None of them can be a caller argument, and
// none of them can be invented here — an invented hash is the exact "digest
// derived from a synthetic test fixture" that benchmark-acceptance-store.v5.js
// lists as explicitly refused.
//
// THE FIRST DRAFT SHIPPED THEM AS PASTED LINES, on the theory that a human
// editing a constant is how a ruling changes. The review was right to refuse it:
// the seam study says in its own words that there is NO human step in the middle
// of this slice, and a file whose comment says "somebody edits this and commits
// it" names no owner and never acquires one.
//
// SO EVERY ONE OF THEM IS DERIVED, and the table beside SCHEDULER_SERVICE_KEY
// below says from what. `gate_zero_run_binding_unnamed` now fires on exactly one
// condition — a row the derivation asked for is genuinely absent — and its
// reason text names which one. In a repository with no Gate Zero run yet that is
// still the answer, and it is now an answer ABOUT MISSING ROWS rather than about
// missing typing.
//
// ---------------------------------------------------------------------------
// THE DIGEST RECIPE, AND THE ONE GAP THE SEAM STUDY NAMED.
//
// r7's canonicalization contract is JCS over UTF-8, SHA-256, no trailing
// newline, and its receipt-payload rule names domain tags for
// `benchmark-manifest.v1`, `attended-effect-capability.v1`,
// `attended-effect-consumption-receipt.v2` and
// `attended-effect-outcome-receipt.v1` — AND NOT FOR `consumer-gate-receipt.v1`.
// Two readings are available and they produce different digests: a plain
// `digest(receipt)`, or a tagged `digest([schema_version, receipt])`.
//
// THIS MODULE FOLLOWS THE REPOSITORY'S OWN PRECEDENT — plain `digest(receipt)`,
// JCS SHA-256 over the receipt object with no domain tag, which is what
// `benchmark-minimum.v5.js:1597` already does for a consumer-gate receipt it
// proposes. The choice is STATED rather than buried, as
// `V5_A02_GATE_ZERO_RECEIPT_DIGEST_RECIPE`, so a reviewer can overturn it in one
// line. If a reviewer prefers the tagged reading it becomes an r7 amendment, and
// an r7 amendment reseals all sixty-two chunks — which is precisely why the
// choice is on the surface instead of inside a function.
//
// And the rule's closing clause is obeyed either way: no artifact includes its
// own whole-byte digest as input to that digest. The receipt carries
// twenty-one fields and the digest is taken over all twenty-one; the digest is
// not one of them.
//
// ---------------------------------------------------------------------------
// WHO SIGNS, AND IT IS NOT A CONSTANT.
//
// r7's identity rule: all identities derive from authenticated execution
// context, `producer_role` binds to the registry entry, and the subject maker's
// actor and session must differ from the evaluator's. Caller-supplied identity,
// unauthorized role, or same-actor self-review denies.
//
// THE FIRST DRAFT RECONSTRUCTED THEM instead. It read card 9's holder ref, took
// the actor slug out of it, and built each session ref out of that slug plus a
// digest of the evidence — so any process that imported this module got a
// receipt signed `codex-reviewer` without authenticating anything, and two runs
// over the same rows shared a session. That is the defect the review named
// first, and it is closed here rather than patched.
//
// THE PRODUCER AND THE EVALUATOR ARE THE AUTHENTICATED CALLER.
// `authenticatedCallIdentity()` answers with the identity tools.js's one verb
// dispatch bound to the async context for this call — the actor the server
// established, the authority class identity.js derives for it, and the session
// ref built from correlation.js's per-request correlation id. Outside an
// authenticated call it answers null and this producer refuses. A test, a CLI
// probe and a bare import all take that path.
//
// THE SUBJECT MAKER IS THE CANDIDATE'S OWN COMMITTER, read from the repository
// the running module was built from and resolved to an actor THROUGH
// identity.js. An address identity.js does not map is not an actor a receipt may
// name, and the derivation refuses rather than inventing one. Its authority
// class is stated as `candidate_builder` rather than derived, because identity
// .js derives classes for authenticated actors and a committer is not one — the
// field says what this identity is, and it grants nothing.
//
// CARD 9 IS STILL WHAT BINDS THE SEAM. An unstaffed seat still turns the whole
// slice dark. What the seat no longer does is sign: naming who may sign and
// being the signer are different acts.
//
// ---------------------------------------------------------------------------
// TWO KINDS OF NO, inherited unchanged from global-boundaries.v5.js:
//   * A POLICY ANSWER IS RETURNED, with a stable `reason_id` out of the closed
//     registry below.
//   * A CONTRACT VIOLATION THROWS V5BoundaryError. Handing this module an
//     argument is not a policy question.

import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { artifactManifestDigest, canonicalJson, digest } from "./artifact-trust.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import {
  ORGANIZATION_TENANT_ID, authenticatedCallIdentity, slugForEmail,
} from "./identity.js";
import {
  CONSUMER_GATE_RECEIPT_FIELDS,
  CONSUMER_GATE_RECEIPT_SCHEMA,
  GATE_ZERO_STEP_REF,
} from "./benchmark-minimum.v5.js";
import {
  V5_A02_GATE_ZERO_GATE_ID,
  V5_A02_GATE_ZERO_ORACLE_REF,
  V5_A02_GATE_ZERO_ORACLE_VERSION,
  V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS,
  V5_A02_GATE_ZERO_PRODUCER_REGISTRATION,
  V5_A02_GATE_ZERO_PRODUCER_ROLE,
  V5_A02_SCHEDULER_STEP_REF,
} from "./gate-zero-producer-registration.v5.js";
import {
  readGateConclusionEvidence,
  readPredecessorOutcomeEvidence,
  readSchedulerCanaryEvidence,
} from "./gate-zero-seam-readers.v5.js";

export const V5_A02_GATE_ZERO_PRODUCER_SCHEMA_VERSION =
  "doctorcre-v5-a02-gate-zero-producer.v1";

/**
 * THE STATED DIGEST RECIPE. See the header: r7 names no domain tag for
 * `consumer-gate-receipt.v1`, so this follows the repository's own precedent
 * and says so out loud, on the surface, where one line overturns it.
 */
export const V5_A02_GATE_ZERO_RECEIPT_DIGEST_RECIPE = deepFreeze({
  schema_ref: CONSUMER_GATE_RECEIPT_SCHEMA,
  canonicalization: "jcs_utf8_sha256_no_trailing_newline",
  domain_tag: null,
  recipe: "sha256 over the JCS canonicalization of the twenty-one-field receipt object",
  gap_in_r7: "r7's receipt_payload_digest_rule names domain tags for four schemas and not for this one",
  chosen_because: "benchmark-minimum.v5.js takes a plain digest over the consumer-gate receipt it proposes",
  alternative_if_overturned: "a tagged digest over [schema_ref, receipt], which would be an r7 amendment",
  self_digest_excluded: true,
});

function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (isPlainObject(value)) { Object.values(value).forEach(deepFreeze); return Object.freeze(value); }
  return value;
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function fail(code, message, detail) {
  throw new V5BoundaryError(code, message, detail);
}

/**
 * AMENDMENT 2'S CLOSED SHAPE FOR AN EXPORTED CALLABLE, the same helper the
 * gate and the readers use, for the same two reasons: a bound function is not
 * constructable and quotes no line of this module back at whoever probed it,
 * and an own `Symbol.hasInstance` answers `instanceof` false WITHOUT walking the
 * left operand's prototype chain. Frozen, so no property can be written over it
 * afterwards — clause (d), the one the shape enumeration found missing.
 */
function closedCallable(callable) {
  const closed = callable.bind(null);
  Object.defineProperty(closed, Symbol.hasInstance, {
    value: () => false, writable: false, enumerable: false, configurable: false,
  });
  return Object.freeze(closed);
}

// ---------------------------------------------------------------------------
// THE CLOSED VOCABULARIES.
// ---------------------------------------------------------------------------

/**
 * How a clause came out. THREE VALUES AND NO BOOLEAN, the same three the
 * readers use and for the same reason: a clause with no row behind it has not
 * failed, it has not been answered, and a boolean cannot say so.
 */
const HELD = "held";
const FAILED = "failed";
// "unknown", and not the obvious word for a clause nothing answered: every
// string this module can hand the gate is swept by the standing rule's closed
// union AS A SUBSTRING, and the obvious word carries "read". This is the
// readers' own third value, which is the right one anyway.
const UNKNOWN = "unknown";

export const V5_A02_GATE_ZERO_CLAUSE_STATES = deepFreeze([FAILED, HELD, UNKNOWN].sort());

/**
 * Every refusal this producer can answer with. Closed, so a refusal is
 * checkable, and every member is also registered in the GATE's own closed reason
 * registry — the gate refuses to build an answer around an id it does not know,
 * so the two lists cannot drift apart in silence.
 */
export const V5_A02_GATE_ZERO_PRODUCER_REASON_IDS = deepFreeze([
  "gate_zero_evidence_unavailable",
  "gate_zero_gate_graph_clause_failed",
  "gate_zero_negative_admission_unproved",
  "gate_zero_predecessor_clause_failed",
  "gate_zero_producer_identity_refused",
  "gate_zero_run_binding_unnamed",
  "gate_zero_scheduler_clause_failed",
].sort());

function reason(id) {
  if (!V5_A02_GATE_ZERO_PRODUCER_REASON_IDS.includes(id))
    fail("unknown_producer_reason_id", `${id} is not a registered producer reason`, { reason_id: id });
  return id;
}

/** The one finding each reader gives when it has actually read what was asked. */
const PREDECESSOR_HELD_FINDING = "predecessor_outcome_accepted_with_matching_hash";
const SCHEDULER_HELD_FINDING = "scheduler_canary_and_observation_join";
const CONCLUSION_READ_FINDING = "gate_conclusion_observed";

/**
 * THE FINDINGS THAT MEAN "NO ROW", not "a row that did not hold" — and the
 * distinction is load-bearing rather than pedantic. A canary the ledger has no
 * row for has not failed its clause: nothing was read, so nothing failed, and a
 * run that recorded a failing outcome over an absent row would be inventing an
 * observation. These answer `unknown`, which refuses without emitting anything.
 */
const ABSENT_ROW_FINDINGS = Object.freeze([
  "predecessor_outcome_absent",
  "scheduler_dispatch_row_absent",
  "scheduler_observation_absent",
  "scheduler_service_row_absent",
]);

/** The one conclusion word that is green. Every other one propagates non-green. */
const GREEN_CONCLUSION = "success";

// ---------------------------------------------------------------------------
// THE BINDINGS. Module-private constants, every one of them.
// ---------------------------------------------------------------------------

/**
 * The three predecessors card 11 answers for. The fourth,
 * `step:scheduler-active-receipt`, is answered by card 12 instead: no Work
 * Request carries it, and `PREDECESSOR_WORK_REQUEST_REFS` in the reader maps it
 * to null on purpose. Derived from the frozen four rather than restated, so a
 * change to the plan's predecessor set moves this list with it.
 */
const OUTCOME_BACKED_PREDECESSORS = deepFreeze(
  V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS.filter(step => step !== V5_A02_SCHEDULER_STEP_REF));

/**
 * THE GATE GRAPH A GATE ZERO RUN STANDS ON, and it is this module's own
 * constant rather than anything a caller could assemble. Each node names one
 * check this repository DECLARES — card 13's reader refuses any other name — and
 * the edges are what make non-green propagation a real clause rather than a
 * conjunction: `main-canary` reports over the two gates beneath it, so a green
 * canary above a failed `ops/ci.sh --strict` is refused as inherited non-green
 * rather than read as a pass.
 */
const V5_A02_GATE_ZERO_GATE_GRAPH = deepFreeze([
  { gate_id: "ops-ci-strict", check_name: "ops/ci.sh --strict", depends_on: [] },
  { gate_id: "local-db-ci-migration", check_name: "local-db-ci --class migration", depends_on: [] },
  {
    gate_id: "main-canary",
    check_name: "main canary (gates, migration, types, freshness)",
    depends_on: ["local-db-ci-migration", "ops-ci-strict"],
  },
]);

/**
 * THE TTL THIS PRODUCER STAMPS, as a module constant. A Gate Zero outcome is the
 * zero of the v5 clock and everything downstream must be observed strictly after
 * it, so the window is the one this module chose and not one a caller asked for.
 */
const RECEIPT_TTL_MS = 7 * 24 * 60 * 60 * 1000;

/**
 * The comparator sentence, 5-300 characters, stating what this producer compared
 * rather than what it concluded.
 */
const COMPARATOR =
  "each of the three ruled evidence seams is compared against the clause it answers, and the three clauses are conjoined with inherited-refusal propagation across the declared gate graph";

// ---------------------------------------------------------------------------
// THE RUN BINDING, DERIVED — there is no line anybody pastes.
//
// Four addresses a Gate Zero run stands on, and each one is now READ from
// something that already exists rather than looked up by a human and typed here:
//
//   the four predecessor step refs  frozen literals already
//                                   (gate-zero-producer-registration.v5.js)
//   each predecessor's acceptance   read from the ruled record-layer store BY
//     receipt                       that step ref; card 11's reader takes the
//                                   step and no hash, and reads the receipt
//   the scheduler service           a module-private constant naming this
//                                   wrapper's own service, the way the reader's
//                                   own bin/run-scheduled.sh constant is
//   the canary run                  the LATEST scheduler-minted row for that
//                                   service, which the ledger answers itself
//   the head revision               the revision the running module was built
//                                   from, read from the repository this file
//                                   lives in — the same 40-hex value
//                                   bin/deploy-worker.sh stamps as GIT_SHA and
//                                   /release reports back
//   the subject maker               the committer of that revision, resolved to
//                                   an actor THROUGH identity.js, never typed
//
// So `gate_zero_run_binding_unnamed` now fires on ONE condition only: a row this
// derivation asked for is genuinely absent. Its reason text names which.
// ---------------------------------------------------------------------------

/**
 * THE SERVICE THIS PRODUCER'S CANARY RUNS UNDER. A module-private constant, and
 * the only one of the four addresses that is: the scheduler cannot be asked
 * which of its services is Gate Zero's, so this file names it, the same way
 * card 12's reader names `bin/run-scheduled.sh`. ops/config/services.json
 * carries the row; ops/launchd/com.carr.gate-zero-canary.plist runs it.
 */
const SCHEDULER_SERVICE_KEY = "gate-zero-canary";

/** Where the running module's own repository begins, walking up from this file. */
function repositoryRoot() {
  let at = dirname(fileURLToPath(import.meta.url));
  for (let step = 0; step < 32; step += 1) {
    try {
      statSync(join(at, ".git"));
      return at;
    } catch { /* keep walking */ }
    const up = dirname(at);
    if (up === at) return null;
    at = up;
  }
  return null;
}

function readText(path) {
  try {
    return readFileSync(path, "utf8");
  } catch {
    return null;
  }
}

/**
 * The git directory for the candidate tree, and the common directory its refs
 * and reflog live in. A worktree's `.git` is a FILE naming its own directory,
 * which is the shape this repository's sessions actually run in.
 */
function gitDirectories(root) {
  if (root === null) return null;
  const dotGit = join(root, ".git");
  let gitDir = dotGit;
  const asFile = readText(dotGit);
  if (asFile !== null) {
    const named = /^gitdir:\s*(.+?)\s*$/m.exec(asFile);
    if (named === null) return null;
    gitDir = named[1].startsWith(sep) ? named[1] : join(root, named[1]);
  }
  const common = readText(join(gitDir, "commondir"));
  return {
    gitDir,
    commonDir: common === null ? gitDir
      : (common.trim().startsWith(sep) ? common.trim() : join(gitDir, common.trim())),
  };
}

const HEAD_REVISION = /^[0-9a-f]{40}$/;
const SERVICE_KEY = /^[a-z0-9][a-z0-9._-]{0,63}$/;
const ACTOR_ID = /^[a-z0-9][a-z0-9-]{0,63}$/;

/**
 * THE REVISION THE RUNNING MODULE WAS BUILT FROM. Loose ref, packed ref or a
 * detached HEAD — all three are read, none is guessed, and anything else answers
 * null, which refuses.
 */
function headRevisionOf(git) {
  if (git === null) return null;
  const head = readText(join(git.gitDir, "HEAD"));
  if (head === null) return null;
  const direct = head.trim();
  if (HEAD_REVISION.test(direct)) return direct;
  const symbolic = /^ref:\s*(refs\/\S+)\s*$/m.exec(head);
  if (symbolic === null) return null;
  const ref = symbolic[1];
  for (const base of [git.gitDir, git.commonDir]) {
    const loose = readText(join(base, ...ref.split("/")));
    if (loose !== null && HEAD_REVISION.test(loose.trim())) return loose.trim();
  }
  for (const base of [git.gitDir, git.commonDir]) {
    const packed = readText(join(base, "packed-refs"));
    if (packed === null) continue;
    for (const line of packed.split("\n")) {
      const entry = /^([0-9a-f]{40})\s+(\S+)$/.exec(line.trim());
      if (entry !== null && entry[2] === ref) return entry[1];
    }
  }
  return null;
}

/**
 * WHO BUILT THE CANDIDATE, as an actor this system registers.
 *
 * The reflog is the one place a repository records the identity behind the
 * revision it is standing on without an object walk, and its email is resolved
 * THROUGH identity.js: an address `slugForEmail` does not map is not an actor a
 * receipt may name, and this answers null rather than inventing one.
 */
function subjectMakerActorOf(git) {
  if (git === null) return null;
  for (const base of [git.gitDir, git.commonDir]) {
    const log = readText(join(base, "logs", "HEAD"));
    if (log === null) continue;
    const lines = log.split("\n").filter(one => one.trim().length > 0);
    if (lines.length === 0) continue;
    const email = /<([^<>]+)>/.exec(lines[lines.length - 1]);
    if (email === null) continue;
    const slug = slugForEmail(email[1]);
    if (typeof slug === "string" && ACTOR_ID.test(slug)) return slug;
  }
  return null;
}

/**
 * THE TWO SEALED ARTIFACTS THIS RECEIPT'S DIGESTS STAND ON, as repository paths.
 *
 * NEITHER IS A DESCRIPTION. The environment manifest is the declaration
 * ops/environment-matrix-selftest.py checks the repository against; the fixture
 * set is the sealed gate-zero fixture bytes, which `evidence_scope:
 * candidate-and-test` puts inside this receipt's scope by name. They are READ as
 * bytes and hashed. Nothing here imports or executes either one.
 */
const ENVIRONMENT_MANIFEST_PATH = ["ops", "config", "environments.json"];
const SEALED_FIXTURE_SET_PATHS = Object.freeze([
  ["mcp-server", "test", "gate-zero-producer-stores.v5.fixture.mjs"],
  ["mcp-server", "test", "gate-zero-seam-stores.v5.fixture.mjs"],
  ["mcp-server", "test", "gate-zero-seam-stores.v5.receipt-fixture.mjs"],
]);

/** The candidate tree: every module the running producer was built alongside. */
function candidateTreeFiles(root) {
  const base = dirname(fileURLToPath(import.meta.url));
  const found = [];
  const walk = (at) => {
    let names;
    try { names = readdirSync(at).sort(); } catch { return; }
    for (const name of names) {
      const full = join(at, name);
      let stat;
      try { stat = statSync(full); } catch { continue; }
      if (stat.isDirectory()) { walk(full); continue; }
      if (!name.endsWith(".js")) continue;
      const bytes = readFileSync(full);
      found.push({ path: relative(root ?? base, full).split(sep).join("/"), bytes });
    }
  };
  walk(base);
  return found;
}

// ---------------------------------------------------------------------------
// THE IDENTITIES, DERIVED FROM THE AUTHENTICATED CALL.
//
// r7's identity rule: the gateway derives all identities from authenticated
// execution context, binds `producer_role` to the registry entry, and requires
// the subject maker's actor and session to differ from the evaluator's.
//
// THE FIRST REVIEW ROUND FOUND THIS FILE RECONSTRUCTING THEM. It read card 9's
// holder ref, took the actor slug out of it, and manufactured a session ref from
// that slug and a digest of what it had just read — so any process that imported
// this module received a receipt signed `codex-reviewer` without authenticating
// anything, and two runs over the same evidence shared a "session".
//
// WHAT IT DOES NOW. `authenticatedCallIdentity()` answers with the identity
// tools.js's verb dispatch bound to the async context for THIS call — actor,
// session and authority class, each derived in identity.js from the actor the
// server established, with the session ref being correlation.js's per-request
// correlation id. Outside an authenticated call it answers null and this
// producer refuses. A test, a CLI probe and an unauthenticated import all take
// that path, which is the point: nothing that cannot authenticate can obtain a
// receipt.
//
// CARD 9 IS STILL LOAD-BEARING and is unchanged by this: the seat declaration is
// what BINDS the producer seam at all, so an unstaffed seat still turns the whole
// slice dark. What the seat no longer does is supply an identity. Naming who may
// sign and being the signer are different acts, which is the distinction the
// first round collapsed.
// ---------------------------------------------------------------------------

/**
 * The authority classes r7's registry admits for an independent control-plane
 * oracle. DERIVED classes only — identity.js computes the class, this module
 * checks membership, and a class outside the set refuses rather than signs.
 */
const PRODUCER_AUTHORITY_CLASSES = Object.freeze(["review_agent"]);

const SESSION_REF = /^session:[a-z0-9][a-z0-9:._/-]{8,199}$/;

/** Two seats collide when they share EITHER the actor or the session. */
function sameSeat(a, b) {
  return a.actor_id === b.actor_id || a.session_ref === b.session_ref;
}

// ---------------------------------------------------------------------------
// THE THREE CLAUSES, IN PRODUCTION FORM.
//
// These are V5-A02's checkable_done clauses, promoted out of the conditional.
// The test-side clause helper answers in the conditional about a CALLER'S SHAPE,
// and it has to, because a caller's shape is not evidence. These answer `held`,
// `failed` or `unknown` about a ROW read by a ruled reader from a ruled store —
// which is the whole difference, and it is why the conditional mood does not
// appear on this side of the line in any spelling.
//
// Every one of them is PURE over the readings handed to it: no store, no clock,
// no environment. The reading is fetched by `aim()` below and by nothing else.
// ---------------------------------------------------------------------------

function clause(state, reasonId, detail) {
  return deepFreeze({ state, reason_id: reasonId === null ? null : reason(reasonId), detail });
}

/**
 * CLAUSE 1 — the four bound predecessors join exactly.
 *
 * Three of them are Work-Request outcomes read through card 11 and must each
 * answer with the one finding that means "accepted, complete, and the acceptance
 * receipt's own hash is the hash this run was bound to". The fourth is the
 * scheduler receipt, which card 12 answers and clause 2 judges.
 *
 * EXACTLY means exactly: each predecessor present once, each read from the ruled
 * store, and NO TWO OF THEM ANSWERED BY THE SAME WORK REQUEST — one outcome
 * record cannot close two predecessors, and the reader hands back the Work
 * Request ref out of its own frozen table, so the check is on a value no caller
 * chose.
 */
function predecessorClause(readings) {
  const seenWorkRequests = new Map();
  const per = {};
  for (const step of OUTCOME_BACKED_PREDECESSORS) {
    const answered = readings[step];
    if (!isPlainObject(answered) || !Object.hasOwn(answered, "card_ref")) {
      per[step] = UNKNOWN;
      continue;
    }
    if (ABSENT_ROW_FINDINGS.includes(answered.finding) || answered.finding === null) {
      per[step] = UNKNOWN;
      continue;
    }
    if (answered.finding !== PREDECESSOR_HELD_FINDING || answered.hash_match !== HELD) {
      per[step] = FAILED;
      continue;
    }
    const workRequest = answered.work_request_ref;
    if (typeof workRequest !== "string" || seenWorkRequests.has(workRequest)) {
      per[step] = FAILED;
      continue;
    }
    seenWorkRequests.set(workRequest, step);
    per[step] = HELD;
  }
  const unknown = OUTCOME_BACKED_PREDECESSORS.filter(step => per[step] === UNKNOWN);
  const failed = OUTCOME_BACKED_PREDECESSORS.filter(step => per[step] === FAILED);
  if (unknown.length > 0)
    return clause(UNKNOWN, "gate_zero_evidence_unavailable", { per_predecessor: per, not_answered: unknown });
  if (failed.length > 0)
    return clause(FAILED, "gate_zero_predecessor_clause_failed", { per_predecessor: per, failed });
  return clause(HELD, null, { per_predecessor: per, failed: [] });
}

/**
 * CLAUSE 2 — the scheduler canary and its readback join exactly, and the canary
 * is bound to the accepted scheduler-active receipt.
 *
 * Card 12's reader reports the three sub-clauses separately, so this reads all
 * three rather than only the finding: a canary bound to no receipt, an
 * observation at or before its own dispatch, and an observation of another
 * canary each fail on their own name.
 */
function schedulerClause(answered) {
  if (!isPlainObject(answered) || !Object.hasOwn(answered, "card_ref"))
    return clause(UNKNOWN, "gate_zero_evidence_unavailable", { sub_clauses: null });
  const sub = deepFreeze({
    receipt_binding: answered.receipt_binding ?? UNKNOWN,
    observation_after_dispatch: answered.observation_after_dispatch ?? UNKNOWN,
    canary_match: answered.canary_match ?? UNKNOWN,
  });
  if (ABSENT_ROW_FINDINGS.includes(answered.finding) || answered.finding === null)
    return clause(UNKNOWN, "gate_zero_evidence_unavailable", { sub_clauses: sub });
  const allHeld = sub.receipt_binding === HELD
    && sub.observation_after_dispatch === HELD && sub.canary_match === HELD;
  if (answered.finding !== SCHEDULER_HELD_FINDING || !allHeld)
    return clause(FAILED, "gate_zero_scheduler_clause_failed", { sub_clauses: sub });
  return clause(HELD, null, { sub_clauses: sub });
}

/**
 * CLAUSE 3 — a failed injected gate cannot claim green.
 *
 * A gate is green when its OWN conclusion is `success` AND every gate it
 * descends from is green. The second half is what makes an injected failure
 * impossible to claim past, and it is transitive rather than one hop deep. An
 * empty graph is not green: a Gate Zero that read no gate read nothing.
 */
function gateGraphClause(readings) {
  const conclusions = new Map();
  for (const node of V5_A02_GATE_ZERO_GATE_GRAPH) {
    const answered = readings[node.gate_id];
    if (!isPlainObject(answered) || !Object.hasOwn(answered, "card_ref")) continue;
    if (answered.finding !== CONCLUSION_READ_FINDING) continue;
    if (typeof answered.conclusion !== "string") continue;
    conclusions.set(node.gate_id, answered.conclusion);
  }
  const unknown = V5_A02_GATE_ZERO_GATE_GRAPH
    .filter(node => !conclusions.has(node.gate_id)).map(node => node.gate_id);
  if (unknown.length > 0)
    return clause(UNKNOWN, "gate_zero_evidence_unavailable",
      { not_answered: unknown, conclusion_not_success: [], inherited_not_success: [] });

  const byId = new Map(V5_A02_GATE_ZERO_GATE_GRAPH.map(node => [node.gate_id, node]));
  const memo = new Map();
  const isGreen = (gateId) => {
    if (memo.has(gateId)) return memo.get(gateId);
    const node = byId.get(gateId);
    // A gate this graph does not contain is a gate nothing answered for, and
    // unanswered is never
    // green. Pessimistic while the walk is open, so a cycle cannot answer green.
    if (node === undefined) { memo.set(gateId, false); return false; }
    memo.set(gateId, false);
    const green = conclusions.get(gateId) === GREEN_CONCLUSION && node.depends_on.every(isGreen);
    memo.set(gateId, green);
    return green;
  };

  const notSucceeding = [...byId.keys()].filter(id => conclusions.get(id) !== GREEN_CONCLUSION).sort();
  const inherited = [...byId.keys()]
    .filter(id => conclusions.get(id) === GREEN_CONCLUSION && !isGreen(id)).sort();
  const allGreen = byId.size > 0 && [...byId.keys()].every(isGreen);
  const detail = {
    not_answered: [], conclusion_not_success: notSucceeding, inherited_not_success: inherited,
  };
  if (!allGreen) return clause(FAILED, "gate_zero_gate_graph_clause_failed", detail);
  return clause(HELD, null, detail);
}

// ---------------------------------------------------------------------------
// NEGATIVE ADMISSION, EARNED RATHER THAN WRITTEN.
//
// `consumer-gate-receipt.v1` requires `negative_admission_result`, whose one
// legal value claims that every required denial was OBSERVED. Q036.D1 is
// explicit that a Gate Zero run must watch its injected failures actually being
// denied, not merely watch its passes pass — "any missed finding, false-green
// result, unbounded loop, or unproven recovery fails".
//
// So this module does what benchmark-minimum.v5.js does for the same field: it
// takes the readings it actually holds, mutates each in a required way, and
// requires ITS OWN live clause to refuse with the exact expected reason. A
// module that simply wrote the string would be self-attesting the property the
// field exists to prevent. If any case is not denied, production refuses.
// ---------------------------------------------------------------------------

const NEGATIVE_ADMISSION_RESULT = "all_required_denials_observed";

/** One mutation, and the reason the live clause must answer with. */
const REQUIRED_DENIALS = deepFreeze([
  "predecessor_not_accepted",
  "predecessor_acceptance_receipt_hash_mismatch",
  "one_outcome_record_closing_two_predecessors",
  "scheduler_canary_not_bound_to_its_receipt",
  "scheduler_observation_not_after_dispatch",
  "gate_conclusion_not_success",
  "gate_reporting_success_over_a_failed_ancestor",
]);

function withoutKey(reading, key, value) {
  return { ...reading, [key]: value };
}

/**
 * Every required denial, re-derived from the live clauses over the readings this
 * run actually took. Returns the proof, or the first case that was NOT denied.
 */
function proveNegativeAdmission(predecessorReadings, schedulerReading, conclusionReadings) {
  const observed = [];
  const record = (name, answer, expected) => {
    if (answer.state === HELD || answer.reason_id !== expected) return name;
    observed.push(name);
    return null;
  };

  const firstStep = OUTCOME_BACKED_PREDECESSORS[0];
  const secondStep = OUTCOME_BACKED_PREDECESSORS[1];

  const notAccepted = {
    ...predecessorReadings,
    [firstStep]: withoutKey(predecessorReadings[firstStep], "finding", "predecessor_outcome_not_accepted"),
  };
  const hashMismatch = {
    ...predecessorReadings,
    [firstStep]: withoutKey(predecessorReadings[firstStep], "hash_match", FAILED),
  };
  const doubleDuty = {
    ...predecessorReadings,
    [secondStep]: withoutKey(predecessorReadings[secondStep], "work_request_ref",
      predecessorReadings[firstStep]?.work_request_ref),
  };
  const unbound = withoutKey(schedulerReading, "receipt_binding", FAILED);
  const notAfter = withoutKey(
    withoutKey(schedulerReading, "observation_after_dispatch", FAILED),
    "finding", "scheduler_observation_not_after_dispatch");
  const firstGate = V5_A02_GATE_ZERO_GATE_GRAPH[0].gate_id;
  const topGate = V5_A02_GATE_ZERO_GATE_GRAPH[V5_A02_GATE_ZERO_GATE_GRAPH.length - 1].gate_id;
  const notGreen = {
    ...conclusionReadings,
    [topGate]: withoutKey(conclusionReadings[topGate], "conclusion", "failure"),
  };
  const greenOverFailure = {
    ...conclusionReadings,
    [firstGate]: withoutKey(conclusionReadings[firstGate], "conclusion", "failure"),
  };

  const missed = [
    record("predecessor_not_accepted",
      predecessorClause(notAccepted), "gate_zero_predecessor_clause_failed"),
    record("predecessor_acceptance_receipt_hash_mismatch",
      predecessorClause(hashMismatch), "gate_zero_predecessor_clause_failed"),
    record("one_outcome_record_closing_two_predecessors",
      predecessorClause(doubleDuty), "gate_zero_predecessor_clause_failed"),
    record("scheduler_canary_not_bound_to_its_receipt",
      schedulerClause(unbound), "gate_zero_scheduler_clause_failed"),
    record("scheduler_observation_not_after_dispatch",
      schedulerClause(notAfter), "gate_zero_scheduler_clause_failed"),
    record("gate_conclusion_not_success",
      gateGraphClause(notGreen), "gate_zero_gate_graph_clause_failed"),
    // THE ONE A CONJUNCTION WOULD MISS. The top gate still reports success; its
    // ancestor does not. A clause that only conjoined self-conclusions would
    // call this green.
    record("gate_reporting_success_over_a_failed_ancestor",
      gateGraphClause(greenOverFailure), "gate_zero_gate_graph_clause_failed"),
  ].filter(one => one !== null);

  return deepFreeze({
    result: missed.length === 0 ? NEGATIVE_ADMISSION_RESULT : null,
    case_count: REQUIRED_DENIALS.length,
    observed_codes: observed.sort(),
    missed_cases: missed.sort(),
  });
}

// ---------------------------------------------------------------------------
// AIMING THE THREE READERS.
// ---------------------------------------------------------------------------

/**
 * Every reading this run stands on, taken from the bound readers, at the
 * addresses the module-private binding names and at no others.
 *
 * The readers take an ADDRESS and no authority. What is handed to each one here
 * is built entirely out of this module's own constants: a step ref out of the
 * frozen predecessor list, an acceptance-receipt hash off the pasted binding, a
 * service and run key off the pasted binding, the pasted head revision, and a
 * check name out of this module's own gate graph — which card 13 refuses unless
 * a workflow in this repository declares it.
 */
async function aim(binding) {
  const predecessors = {};
  for (const step of OUTCOME_BACKED_PREDECESSORS) {
    // THE STEP REF AND NOTHING ELSE. Card 11 reads the acceptance receipt the
    // ruled store holds for this step's Work Request; no hash goes in, so no
    // hash had to be looked up by a human first.
    predecessors[step] = await readPredecessorOutcomeEvidence({ stepRef: step });
  }
  // THE SERVICE AND NOTHING ELSE. Card 12 reads the latest run its own wrapper
  // minted a receipt for, which is the canary a Gate Zero run stands on.
  const scheduler = await readSchedulerCanaryEvidence({ serviceKey: binding.scheduler_service_key });
  const conclusions = {};
  for (const node of V5_A02_GATE_ZERO_GATE_GRAPH) {
    conclusions[node.gate_id] = await readGateConclusionEvidence({
      headSha: binding.head_revision,
      checkName: node.check_name,
    });
  }
  return { predecessors, scheduler, conclusions };
}

/**
 * The digest of WHAT WAS READ, and it carries no store text: every reader answer
 * already reduces the store's own words to digests, counts and constants out of
 * the reader's frozen tables, so what is hashed here is a reading rather than a
 * transcript. It becomes the evidence ref and the session refs, so an identity
 * and an address both move when the evidence does.
 */
function evidenceDigestOf(read) {
  return digest(canonicalJson({
    predecessors: Object.fromEntries(OUTCOME_BACKED_PREDECESSORS
      .map(step => [step, digest(read.predecessors[step] ?? null)])),
    scheduler: digest(read.scheduler ?? null),
    conclusions: Object.fromEntries(V5_A02_GATE_ZERO_GATE_GRAPH
      .map(node => [node.gate_id, digest(read.conclusions[node.gate_id] ?? null)])),
  }));
}

// ---------------------------------------------------------------------------
// THE FIVE DIGESTS THE RECEIPT STANDS ON — AND THREE OF THEM ARE OVER BYTES.
//
// THE FIRST REVIEW ROUND FOUND THREE OF THESE HASHING A DESCRIPTION. The
// candidate digest hashed a revision string beside a list of check names; the
// environment digest hashed registration metadata; the fixture digest hashed the
// addresses the readers were aimed at. Every one of them had the right shape and
// bound nothing: change the artifact, and the digest did not move.
//
// WHAT EACH IS NOW:
//
//   subject_digest      the GATE's identity — the one that is legitimately a
//                       constant, because the subject of a Gate Zero receipt is
//                       Gate Zero and does not move with the candidate.
//   candidate_digest    the SEALED ARTIFACT MANIFEST for the head revision, by
//                       artifact-trust.js's own artifactManifestDigest: JCS
//                       SHA-256 over `scac-artifact-manifest.v1`'s eleven
//                       fields, which is byte-for-byte the recipe
//                       ops.scac_artifact_manifest_digest recomputes in the
//                       database and the recipe the release manifest binds. Its
//                       artifact and source digests are taken over the CANDIDATE
//                       TREE'S ACTUAL BYTES, so one byte changed in one module
//                       moves this digest.
//   policy_digest       this module's own sealed policy — the clause states, the
//                       reason registry, the gate graph, the required denials,
//                       the TTL and the stated digest recipe.
//   environment_        JCS SHA-256 over ops/config/environments.json, READ AS
//     manifest_digest   BYTES and parsed. That file is the environment matrix
//                       ops/environment-matrix-selftest.py holds the repository
//                       to; one byte changed in it moves this digest.
//   fixture_set_digest  SHA-256 over the SEALED FIXTURE SET'S BYTES, file by
//                       file, under their repository-relative paths. The
//                       receipt's own `evidence_scope` is `candidate-and-test`,
//                       so the test material is inside its scope by r7's own
//                       registration rather than by this module's choice.
//
// AN ARTIFACT THIS DERIVATION CANNOT READ IS AN ABSENT ROW, not a zero and not a
// default: every one of these answers null upward and the producer refuses
// naming which artifact it could not reach.
// ---------------------------------------------------------------------------

function subjectDigestOf() {
  return digest({
    subject: "gate-zero",
    gate_id: V5_A02_GATE_ZERO_GATE_ID,
    step_ref: GATE_ZERO_STEP_REF,
    predecessor_step_refs: [...V5_A02_GATE_ZERO_PREDECESSOR_STEP_REFS],
    oracle_ref: V5_A02_GATE_ZERO_ORACLE_REF,
    oracle_version: V5_A02_GATE_ZERO_ORACLE_VERSION,
  });
}

/**
 * The sealed source-bundle manifest for the candidate, assembled from the tree's
 * own bytes. Its digest is the candidate digest; nothing about it is a
 * description of the candidate, and every field is computed here.
 */
function candidateManifestOf(root, headRevision, policyDigest) {
  const files = candidateTreeFiles(root);
  if (files.length === 0) return null;
  const bundle = digest(canonicalJson(Object.fromEntries(
    files.map(file => [file.path, digest(file.bytes)]))));
  return {
    artifact_digest: bundle,
    artifact_kind: "source_bundle",
    media_type: "application/vnd.carr.source-bundle+json",
    byte_length: files.reduce((total, file) => total + file.bytes.length, 0),
    source_ref: headRevision,
    source_digest: bundle,
    sbom_digest: null,
    provenance_digest: digest({ head_revision: headRevision, file_count: files.length }),
    policy_epoch: 1,
    policy_epoch_digest: policyDigest,
  };
}

function policyDigestOf() {
  return digest({
    schema_version: V5_A02_GATE_ZERO_PRODUCER_SCHEMA_VERSION,
    clause_states: [...V5_A02_GATE_ZERO_CLAUSE_STATES],
    reason_ids: [...V5_A02_GATE_ZERO_PRODUCER_REASON_IDS],
    gate_graph: V5_A02_GATE_ZERO_GATE_GRAPH.map(node =>
      ({ gate_id: node.gate_id, depends_on: [...node.depends_on] })),
    required_denials: [...REQUIRED_DENIALS],
    receipt_ttl_ms: RECEIPT_TTL_MS,
    digest_recipe: V5_A02_GATE_ZERO_RECEIPT_DIGEST_RECIPE,
  });
}

/** JCS SHA-256 over the environment manifest's own parsed bytes, or null. */
function environmentManifestDigestOf(root) {
  if (root === null) return null;
  const bytes = readText(join(root, ...ENVIRONMENT_MANIFEST_PATH));
  if (bytes === null) return null;
  try {
    return digest(canonicalJson(JSON.parse(bytes)));
  } catch {
    return null;
  }
}

/** SHA-256 over the sealed fixture set's bytes, under their own paths, or null. */
function fixtureSetDigestOf(root) {
  if (root === null) return null;
  const sealed = {};
  for (const parts of SEALED_FIXTURE_SET_PATHS) {
    let bytes;
    try { bytes = readFileSync(join(root, ...parts)); } catch { return null; }
    sealed[parts.join("/")] = digest(bytes);
  }
  return digest(canonicalJson(sealed));
}

// ---------------------------------------------------------------------------
// THE ANSWER SHAPES.
// ---------------------------------------------------------------------------

function refusal(reasonId, because, extra) {
  return deepFreeze({
    schema_version: V5_A02_GATE_ZERO_PRODUCER_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    gate_zero_step_ref: GATE_ZERO_STEP_REF,
    run_binding_status: "derived",
    status: "unavailable",
    decision: "refuse",
    reason_id: reason(reasonId),
    unavailable_because: because,
    outcome_digest: null,
    observed_at: null,
    receipt: null,
    request_read: false,
    caller_evidence_admitted: false,
    model_judgment_admitted: false,
    decided_by: "ruled_store_rows",
    effects: V5_NO_EFFECTS,
    ...extra,
  });
}

// ---------------------------------------------------------------------------
// THE PRODUCER.
// ---------------------------------------------------------------------------

/**
 * ONE MODULE-PRIVATE PRODUCER. It takes nothing, reads nothing a caller
 * controls, and either refuses with a derived reason or returns one
 * `consumer-gate-receipt.v1` with its digest and the instant it was observed.
 */
async function produce() {
  // (1) WHO IS CALLING, and it is the first question because an unauthenticated
  // invocation must not reach a store, let alone a receipt.
  const caller = authenticatedCallIdentity();
  if (caller === null
      || typeof caller.actor_id !== "string" || !ACTOR_ID.test(caller.actor_id)
      || typeof caller.session_ref !== "string" || !SESSION_REF.test(caller.session_ref))
    return refusal("gate_zero_producer_identity_refused",
      "this module was not called inside an authenticated call, so there is no execution context to derive a producer identity from",
      { clauses: null, negative_admission: null });
  if (!PRODUCER_AUTHORITY_CLASSES.includes(caller.authority_class))
    return refusal("gate_zero_producer_identity_refused",
      "the authenticated call's derived authority class is not one r7's registry admits for this independent oracle",
      { clauses: null, negative_admission: null });

  // (2) WHAT THIS RUN STANDS ON, derived. Each absence names itself.
  const root = repositoryRoot();
  const git = gitDirectories(root);
  const headRevision = headRevisionOf(git);
  const subjectMakerActorId = subjectMakerActorOf(git);
  const policyDigest = policyDigestOf();
  const candidateManifest = headRevision === null
    ? null : candidateManifestOf(root, headRevision, policyDigest);
  const environmentDigest = environmentManifestDigestOf(root);
  const fixtureDigest = fixtureSetDigestOf(root);

  const unnamed = [
    ["head_revision", headRevision !== null && HEAD_REVISION.test(headRevision)],
    ["subject_maker", subjectMakerActorId !== null],
    ["scheduler_service_key", SERVICE_KEY.test(SCHEDULER_SERVICE_KEY)],
    ["candidate_artifact_manifest", candidateManifest !== null],
    ["environment_manifest", environmentDigest !== null],
    ["sealed_fixture_set", fixtureDigest !== null],
  ].filter(([, held]) => !held).map(([name]) => name);
  if (unnamed.length > 0)
    return refusal("gate_zero_run_binding_unnamed",
      `a row this run's binding derives from is absent: ${unnamed.join(", ")}`,
      { clauses: null, negative_admission: null, unnamed_bindings: unnamed });

  const binding = deepFreeze({
    head_revision: headRevision,
    scheduler_service_key: SCHEDULER_SERVICE_KEY,
    subject_maker_actor_id: subjectMakerActorId,
  });

  // (3) THE THREE IDENTITIES. Producer and evaluator are the authenticated
  // call; the subject maker is the candidate's own committer, resolved through
  // identity.js. r7's rule is checked on BOTH, because a producer that is the
  // maker attests to its own work exactly as much as an evaluator that is.
  const producerIdentity = deepFreeze({ ...caller });
  const evaluatorIdentity = producerIdentity;
  const subjectMakerIdentity = deepFreeze({
    actor_id: subjectMakerActorId,
    session_ref: `session:candidate-build:${binding.head_revision}`,
    authority_class: "candidate_builder",
  });
  for (const other of [producerIdentity, evaluatorIdentity])
    if (sameSeat(subjectMakerIdentity, other))
      return refusal("gate_zero_producer_identity_refused",
        "the seat that built the candidate and the seat that reviews it are the same seat",
        { clauses: null, negative_admission: null });

  const read = await aim(binding);
  const evidenceDigest = evidenceDigestOf(read);

  const clauses = deepFreeze({
    predecessor_join: predecessorClause(read.predecessors),
    scheduler_canary: schedulerClause(read.scheduler),
    gate_graph: gateGraphClause(read.conclusions),
  });
  const ordered = [clauses.predecessor_join, clauses.scheduler_canary, clauses.gate_graph];

  // NOT ANSWERED IS NOT THE SAME AS NOT HELD, and the order here is the whole
  // difference. A clause with no row behind it has not failed — nothing was
  // read, so there is no outcome to emit and no failure to record either. That
  // refuses, before the negative admission is even attempted: a falsifier run
  // over readings that do not exist proves nothing about anything.
  const unknown = ordered.find(one => one.state === UNKNOWN) ?? null;
  if (unknown !== null)
    return refusal("gate_zero_evidence_unavailable",
      "a ruled evidence seam returned no row for the address this run derived",
      { clauses, negative_admission: null });

  // The negative admission is proved over the readings this run actually took,
  // and it is proved BEFORE any conclusion is drawn from them — a run whose own
  // falsifiers do not fire has not established that its passes mean anything.
  const negativeAdmission = proveNegativeAdmission(
    read.predecessors, read.scheduler, read.conclusions);
  if (negativeAdmission.result !== NEGATIVE_ADMISSION_RESULT)
    return refusal("gate_zero_negative_admission_unproved",
      "at least one required denial was not observed, so this run cannot claim its injected failures were denied",
      { clauses, negative_admission: negativeAdmission });

  // A CLAUSE THAT DID NOT HOLD IS AN OUTCOME, NOT A REFUSAL, and this is what
  // the retry policy already said in its own words: retryable, every run kept,
  // failed runs retained. A Gate Zero run that read all three seams and found a
  // failed gate has established something — it must be recorded, with the same
  // twenty-one fields and the same digest as a passing one, and `status: "fail"`
  // on it. Refusing instead would throw away the one observation Q036.D1 exists
  // to demand: that an injected failure propagates rather than disappearing.
  const failed = ordered.find(one => one.state === FAILED) ?? null;

  const observedAt = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  const expiresAt = new Date(Date.parse(observedAt) + RECEIPT_TTL_MS)
    .toISOString().replace(/\.\d{3}Z$/, "Z");
  const entry = V5_A02_GATE_ZERO_PRODUCER_REGISTRATION.registry_entry;

  const receipt = deepFreeze({
    gate_id: V5_A02_GATE_ZERO_GATE_ID,
    receipt_producer_step_ref: GATE_ZERO_STEP_REF,
    subject_digest: subjectDigestOf(),
    candidate_digest: artifactManifestDigest(candidateManifest),
    policy_digest: policyDigest,
    environment_manifest_digest: environmentDigest,
    subject_environment: entry.subject_environment,
    evidence_scope: entry.evidence_scope,
    subject_maker_identity: subjectMakerIdentity,
    producer_identity: producerIdentity,
    evaluator_identity: evaluatorIdentity,
    producer_role: entry.producer_role,
    independent_oracle_ref: entry.oracle_ref,
    oracle_version: entry.oracle_version,
    evidence_ref: `safe:gate-zero/evidence/${evidenceDigest.slice("sha256:".length)}`,
    fixture_set_digest: fixtureDigest,
    observed_at: observedAt,
    ttl_expires_at: expiresAt,
    // THE VERDICT, AND IT IS THE RECEIPT'S RATHER THAN THE GATE'S. r7's closed
    // set is pass/fail/unknown/stale/quarantined, and a clause that did not hold
    // makes this "fail" — a real, kept, retryable outcome that no consumer gate
    // will accept as a member, rather than an absence.
    status: failed === null ? "pass" : "fail",
    comparator: COMPARATOR,
    negative_admission_result: negativeAdmission.result,
  });

  // The twenty-one fields, counted rather than trusted: a field added or dropped
  // by a later edit refuses here instead of reaching a consumer.
  const fields = Object.keys(receipt).sort();
  const required = [...CONSUMER_GATE_RECEIPT_FIELDS].sort();
  if (fields.length !== required.length || fields.some((name, at) => name !== required[at]))
    fail("consumer_gate_receipt_fields_drifted",
      "the assembled receipt is not consumer-gate-receipt.v1's closed field set",
      { assembled: fields.length, required: required.length });

  return deepFreeze({
    schema_version: V5_A02_GATE_ZERO_PRODUCER_SCHEMA_VERSION,
    tenant: ORGANIZATION_TENANT_ID,
    gate_zero_step_ref: GATE_ZERO_STEP_REF,
    run_binding_status: "derived",
    status: "outcome_produced",
    decision: "report",
    reason_id: failed === null ? null : failed.reason_id,
    unavailable_because: failed === null ? null
      : "a clause this run stands on was not held by the rows the ruled evidence seams returned",
    // THE DIGEST, by the stated recipe: JCS SHA-256 over the twenty-one-field
    // receipt, no domain tag, and the digest is not one of the fields it covers.
    outcome_digest: digest(receipt),
    outcome_digest_recipe: V5_A02_GATE_ZERO_RECEIPT_DIGEST_RECIPE,
    observed_at: observedAt,
    receipt,
    receipt_schema_ref: CONSUMER_GATE_RECEIPT_SCHEMA,
    clauses,
    negative_admission: negativeAdmission,
    request_read: false,
    caller_evidence_admitted: false,
    model_judgment_admitted: false,
    decided_by: "ruled_store_rows",
    // STEP A EMITS A VALUE. Recording it as a row is Step B, it is the heavy
    // path, and the authority that may write it is Joe's to rule.
    persisted: false,
    durable_outcome_record_required: true,
    effects: V5_NO_EFFECTS,
  });
}

/**
 * THE ONE CALLABLE EXPORT, AND ITS ARITY IS THE BOUNDARY.
 *
 * Zero parameters. There is no evidence argument, no binding argument and no
 * reader argument, so there is nothing for a caller to smuggle authority
 * through. Handing it one is a CONTRACT VIOLATION and throws synchronously,
 * before any store is opened — a policy answer is for policy questions, and
 * "you passed me an argument" is not one.
 *
 * The gate binds this as its producer seam's `emitOutcome`. Nothing else in this
 * repository may call it, and nothing that calls it can change what it answers.
 */
export const v5A02GateZeroEmitOutcome = closedCallable((...received) => {
  if (received.length > 0)
    fail("gate_zero_producer_takes_no_argument",
      "the Gate Zero producer is bound by this module and reads no caller input",
      { arguments_received: received.length });
  return produce();
});
