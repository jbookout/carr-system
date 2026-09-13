// V5-A02 STEP A — THE GATE ZERO PRODUCER, proved the only way a seam that takes
// no argument can be proved: by staging a whole candidate repository, editing
// the things a RUN differs by, and reading what the module then answers.
//
// THERE IS NO INJECTION POINT AND THAT IS THE SUBJECT. `v5A02GateZeroEmitOutcome`
// has arity zero. It cannot be handed evidence, a hash, an identity, a revision,
// a reader or a store; the standing rule of 2026-09-11 forbids a public function
// that turns a caller's description of evidence into a verdict, under any name,
// and a function with nowhere to put one cannot have the defect. Since the PR
// 1013 correction it cannot be handed an IDENTITY either, and that is the other
// half: the producer reads the authenticated call it is running inside, so a
// process that cannot authenticate cannot obtain a receipt at all.
//
// SO EVERY CASE BELOW IS A STAGED TREE under node_modules/.cache, holding:
//
//   * A REAL OBJECT STORE. `.git/HEAD` names the case's revision and
//     `.git/objects` holds the loose commit and tree objects for it, written
//     here: the trees are content-addressed exactly as git writes them, so the
//     path set and the blob ids the producer reads out of HEAD are the real
//     sealing of the staged bytes. Only the COMMIT object is filed under the
//     fixture's chosen revision rather than under its own hash — the producer
//     does not re-hash what it inflates, and a case has to be able to say which
//     revision it is standing on. The committer line is where the subject maker
//     comes from, and the reflog is not written at all any more;
//   * mcp-server/src — a copy, with the store module replaced by
//     ./gate-zero-producer-stores.v5.fixture.mjs, and with card 9's seat
//     declaration or the three `decision_id:` lines edited when a case asks;
//   * mcp-server/test — the sealed fixture bytes the fixture-set digest covers;
//   * ops/config/environments.json — the environment manifest its digest covers.
//
// Nothing in src reaches those trees, no argument selects one, and no environment
// variable points at one. What runs in each is the real producer, the real
// clauses, the real readers and the real ruling gate over known rows — invoked
// inside a REAL VERB DISPATCH.
//
// HOW THE AUTHENTICATED CALL IS OBTAINED, and this is the second correction
// round's subject. Nothing here fabricates an actor. The reviewer seat is minted
// by the staged identity.js's own `reviewActorForToken` from a recorded
// Authorization header and the recorded shape of the server's REVIEW_TOKENS map
// — the same door index.js now delegates to — and the correlation id is
// decorated on the way mcp.js decorates it. That actor then goes through the
// staged tools.js's `executeRegisteredTool`, the one dispatch every verb funnels
// through, and the producer runs INSIDE a real verb call: the stub database
// client the verb is handed calls the gate from within the handler, which is the
// only seam Step A has into a dispatched verb (binding Gate Zero to a verb of
// its own is Step B). An object that identity.js did not mint is refused by the
// derivation, so the fabricated `codex-reviewer` literal the first correction
// round's tests used obtains no identity and no receipt — control 11.
//
// THE CONTROLS, and each is named where it is asserted:
//   1. an authenticated call + every row present  -> passable, a digest, an instant
//   2. an UNAUTHENTICATED invocation              -> refused, and no receipt
//  11. a FABRICATED actor object                  -> no identity, and no receipt
//  12. the real verb dispatch                     -> is what establishes the
//                                                    identity the producer reads
//   3. the receipt's producer identity            -> equals the authenticated actor
//   4. each of the three clauses turned off       -> the gate refuses or fails
//   5. each digest against its own artifact       -> one byte moves it
//   6. the seat back to unstaffed                 -> the whole slice goes dark
//   7. an injected non-green conclusion           -> status "fail", denials observed
//   8. subject maker == the reviewing seat        -> denied
//   9. a caller-supplied anything                 -> unreachable, by parse and by call
//  10. the producer callable                      -> unreachable from every src
//                                                    namespace, by the repository's
//                                                    own runtime walk
//
//   node --test mcp-server/test/gate-zero-producer.v5.test.mjs

import test, { after } from "node:test";
import assert from "node:assert/strict";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync,
  writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { deflateSync } from "node:zlib";
import { join, relative, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";
import { types } from "node:util";

import { artifactManifestDigest, canonicalJson, digest } from "../src/artifact-trust.js";
import { V5BoundaryError, V5_NO_EFFECTS } from "../src/global-boundaries.v5.js";
import { WHOLE_WALK, pathToValue } from "./gate-zero-reachability-walk.testhelper.mjs";
import {
  CONSUMER_GATE_RECEIPT_FIELDS,
  CONSUMER_GATE_RECEIPT_SCHEMA,
  GATE_ZERO_STEP_REF,
} from "../src/benchmark-minimum.v5.js";
import { V5_A02_GATE_ZERO_REASON_IDS } from "../src/gate-zero-assurance.v5.js";
import * as producer from "../src/gate-zero-producer.v5.js";
import {
  CANARY_ABSENT, CANARY_UNBOUND, FORGED_HASH,
  REVISION_ALL_SUCCEED, REVISION_FAILED_ANCESTOR, REVISION_UNFINISHED,
} from "./gate-zero-producer-stores.v5.fixture.mjs";

/**
 * THE STAGING IS SHARED (2026-09-13). Every constant, the candidate-tree
 * builder, the loose-object writer, the one-match line editor, the recorded
 * authentication doors, the tree-module importer, the dispatched-verb seam and
 * the module parser live in gate-zero-candidate-tree.testhelper.mjs, because
 * Step B's write verb owes the SAME staging. They are IMPORTED rather than
 * retyped, for the reason the reachability walk gives in its own header: a
 * retyped harness is a second implementation that passes because it was written
 * from the same misunderstanding as the code it checks. Nothing below changed;
 * the trees these cases stand on are built by the same bytes they were built by
 * before, and a correction to the staging corrects both suites at once.
 */
import {
  CORRELATION_ID, ENVIRONMENT_MANIFEST, FIXTURE_FILE, GATE_FILE, IDENTITY_FILE,
  MAKER_GMAIL_EMAIL, MAKER_NOREPLY_EMAIL, PRODUCER_FILE, REGISTRATION_FILE, REPO,
  REVIEWING_SEAT_ACTOR, RULINGS_FILE, SEALED_FIXTURES, SRC, STORES_FILE,
  SUBJECT_MAKER_ACTOR, TEST_DIR, TOOLS_FILE,
  RECORDED_REVIEW_TOKEN, RECORDED_REVIEW_TOKENS,
  cleanupStagedTrees, editOnce, fabricatedReviewerActor, inDispatchedVerb,
  moduleImports, moduleOfTree, objectId, recordedPartnerActor, recordedReviewerActor,
  stageTree,
} from "./gate-zero-candidate-tree.testhelper.mjs";

after(cleanupStagedTrees);

/**
 * What the gate answers in a staged tree, inside a real dispatched verb call.
 *
 * `mint` builds the actor from the staged identity.js; `mint: null` runs the
 * same gate with no dispatch and no call established at all.
 */
async function emitFrom(options = {}, mint = recordedReviewerActor) {
  const { target } = stageTree(options);
  const gate = await moduleOfTree(target, GATE_FILE);
  if (mint === null) return gate.emitGateZeroOutcome();
  const identity = await moduleOfTree(target, IDENTITY_FILE);
  return inDispatchedVerb(target, mint(identity), () => gate.emitGateZeroOutcome());
}

/**
 * THE CANDIDATE DIGEST RECOMPUTED HERE, over both halves the manifest binds: the
 * blob ids HEAD's tree sealed for each candidate path, and the bytes on disk at
 * those same paths. Computed from the staged tree independently of the module
 * under test, so a producer that hashed something else is red.
 */
function candidateDigestOfTree(target, revision, treeId, policyDigest) {
  const files = [];
  const walk = (at) => {
    for (const name of readdirSync(at).sort()) {
      const full = join(at, name);
      if (statSync(full).isDirectory()) { walk(full); continue; }
      if (!name.endsWith(".js")) continue;
      files.push({ path: `mcp-server/src/${relative(target, full).split(sep).join("/")}`,
        bytes: readFileSync(full) });
    }
  };
  walk(target);
  files.sort((a, b) => (a.path < b.path ? -1 : 1));
  return artifactManifestDigest({
    artifact_digest: digest(canonicalJson(Object.fromEntries(
      files.map(file => [file.path, digest(file.bytes)])))),
    artifact_kind: "source_bundle",
    media_type: "application/vnd.carr.source-bundle+json",
    byte_length: files.reduce((total, file) => total + file.bytes.length, 0),
    source_ref: revision,
    source_digest: digest(canonicalJson(Object.fromEntries(
      files.map(file => [file.path, objectId("blob", file.bytes)])))),
    sbom_digest: null,
    provenance_digest: digest({
      head_revision: revision, head_tree_id: treeId, file_count: files.length }),
    policy_epoch: 1,
    policy_epoch_digest: policyDigest,
  });
}

// ===========================================================================
// CONTROL 1 — an authenticated call, over rows that are all there.
// ===========================================================================

test("PRODUCED: with every row present the gate is passable, with a real digest and instant", async () => {
  const emitted = await emitFrom({});
  assert.equal(emitted.passable, true, emitted.unavailable_because ?? emitted.reason_id);
  assert.equal(emitted.status, "outcome_produced");
  assert.equal(emitted.decision, "report");
  assert.equal(emitted.reason_id, null);
  assert.equal(emitted.producer_bound, true);
  assert.deepEqual(emitted.owed_seams, []);
  assert.equal(emitted.receipt_status, "pass");

  // A REAL DIGEST, by the stated recipe, over the receipt that is right there —
  // recomputed here rather than trusted, so a producer that stamped a constant
  // or hashed something else is red.
  assert.match(emitted.outcome_digest, /^sha256:[0-9a-f]{64}$/);
  assert.equal(emitted.outcome_digest, digest(emitted.receipt));
  assert.equal(emitted.outcome_digest_recipe.domain_tag, null);
  assert.equal(emitted.outcome_digest_recipe.schema_ref, CONSUMER_GATE_RECEIPT_SCHEMA);
  assert.equal(emitted.outcome_digest_recipe.self_digest_excluded, true);
  assert.equal(Object.hasOwn(emitted.receipt, "outcome_digest"), false,
    "the receipt carries its own digest, which the canonicalization rule forbids");

  // A REAL INSTANT, and it is the receipt's own. This value is the zero of the
  // v5 clock: everything downstream is timestamped strictly after it.
  assert.match(emitted.observed_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
  assert.equal(emitted.observed_at, emitted.receipt.observed_at);
  assert.ok(Date.parse(emitted.receipt.ttl_expires_at) > Date.parse(emitted.observed_at));

  // AND THE JOIN IS THE THREE CLAUSES, each held over rows.
  assert.deepEqual(Object.keys(emitted.join).sort(),
    ["gate_graph", "predecessor_join", "scheduler_canary"]);
  for (const [name, clause] of Object.entries(emitted.join))
    assert.equal(clause.state, "held", name);
  assert.equal(emitted.negative_admission.result, "all_required_denials_observed");
  assert.equal(emitted.negative_admission.case_count, 7);
  assert.deepEqual(emitted.negative_admission.missed_cases, []);
  assert.deepEqual(emitted.effects, V5_NO_EFFECTS);
  assert.equal(emitted.persisted, false);
  assert.equal(emitted.durable_outcome_record_required, true);
});

test("RECEIPT: all twenty-one fields of consumer-gate-receipt.v1, and nothing else", async () => {
  const { receipt } = await emitFrom({});
  assert.deepEqual(Object.keys(receipt).sort(), [...CONSUMER_GATE_RECEIPT_FIELDS].sort());
  assert.equal(Object.keys(receipt).length, 21);

  assert.equal(receipt.gate_id, "gate-zero-read-only-accepted");
  assert.equal(receipt.receipt_producer_step_ref, GATE_ZERO_STEP_REF);
  assert.equal(receipt.producer_role, "independent_control_plane_oracle");
  assert.equal(receipt.independent_oracle_ref, "oracle:gate-producer:gate-zero-read-only");
  assert.equal(receipt.oracle_version, "1.0.0");
  assert.equal(receipt.subject_environment, "candidate");
  assert.equal(receipt.evidence_scope, "candidate-and-test");
  assert.equal(receipt.status, "pass");
  assert.equal(receipt.negative_admission_result, "all_required_denials_observed");

  for (const field of ["subject_digest", "candidate_digest", "policy_digest",
    "environment_manifest_digest", "fixture_set_digest"])
    assert.match(receipt[field], /^sha256:[0-9a-f]{64}$/, field);
  // FIVE DISTINCT DIGESTS. A producer that computed one preimage and used it
  // five times would satisfy every pattern above and mean nothing.
  const digests = ["subject_digest", "candidate_digest", "policy_digest",
    "environment_manifest_digest", "fixture_set_digest"].map(field => receipt[field]);
  assert.equal(new Set(digests).size, 5, "two of the five digests are the same value");

  // r7's evidence_ref shape, and it is lowercase-only — a single capital is
  // refused by benchmark-minimum with a bare error naming no field.
  assert.match(receipt.evidence_ref, /^safe:[a-z0-9][a-z0-9:_./-]*$/);
  assert.ok(receipt.comparator.length >= 5 && receipt.comparator.length <= 300);

  // THE THREE IDENTITIES, each an authenticated-receipt-identity.v1, and the
  // independence r7's identity rule requires.
  for (const field of ["subject_maker_identity", "producer_identity", "evaluator_identity"]) {
    assert.deepEqual(Object.keys(receipt[field]).sort(),
      ["actor_id", "authority_class", "session_ref"], field);
    assert.match(receipt[field].session_ref, /^session:[a-z0-9][a-z0-9:._/-]{8,199}$/, field);
  }
  // THE PRODUCER IDENTITY IS THE AUTHENTICATED ACTOR, field for field. This is
  // the mutation control the first review round asked for: a receipt signed by
  // anything other than the call that produced it is a receipt nobody
  // authenticated, and before this correction the module reconstructed one out
  // of card 9's holder constant.
  assert.equal(receipt.producer_identity.actor_id, REVIEWING_SEAT_ACTOR);
  assert.equal(receipt.evaluator_identity.actor_id, REVIEWING_SEAT_ACTOR);
  // DERIVED BY identity.js, not typed anywhere: `review_agent` is what that
  // module resolves a review-token machine identity to.
  assert.equal(receipt.producer_identity.authority_class, "review_agent");
  // AND THE SESSION IS THE SERVER'S OWN CORRELATION ID, not a digest of what the
  // run happened to be looking at. A second call under a second correlation id
  // gets a second session; the old design gave both the same one.
  assert.equal(receipt.producer_identity.session_ref, `session:${CORRELATION_ID}`);
  const elsewhere = "7b1c9d40-2e55-4a61-9f03-8ac4be21d7e6";
  const second = await emitFrom({}, identity => recordedReviewerActor(identity, elsewhere));
  assert.equal(second.receipt.producer_identity.session_ref, `session:${elsewhere}`);

  // THE SUBJECT MAKER IS HEAD'S OWN COMMITTER — the committer line of the commit
  // object the staged HEAD names, not the reflog, which this staging no longer
  // writes at all. Resolved through identity.js's own registry, and its AUTHORITY
  // CLASS IS DERIVED THERE TOO: the first draft wrote the constant
  // `candidate_builder` into this field, which named a class nothing in this
  // system derives, admits or checks.
  assert.equal(receipt.subject_maker_identity.actor_id, SUBJECT_MAKER_ACTOR);
  assert.equal(receipt.subject_maker_identity.authority_class, "verified_partner");
  // THE SESSION IS DERIVED, NOT MANUFACTURED: it is the candidate-build seat
  // within the authenticated call the dispatch path established, so it moves
  // with the server's correlation id and not with the revision.
  assert.equal(receipt.subject_maker_identity.session_ref,
    `session:${CORRELATION_ID}:candidate-build`);
  assert.notEqual(receipt.subject_maker_identity.actor_id, receipt.producer_identity.actor_id);
  assert.notEqual(receipt.subject_maker_identity.session_ref, receipt.producer_identity.session_ref);
});

// ===========================================================================
// CONTROL 2 — an unauthenticated invocation cannot obtain a receipt.
// ===========================================================================

test("IDENTITY: with no authenticated call there is no receipt, only a derived refusal", async () => {
  // The same staged tree, the same rows, the same staffed seat — and no call.
  // This is the shape the first review round found emitting a receipt signed
  // `codex-reviewer` to whatever imported the module.
  const emitted = await emitFrom({}, null);
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_producer_identity_refused");
  assert.equal(emitted.outcome_digest, null);
  assert.equal(emitted.observed_at, null);
  assert.equal(emitted.producer_answer.receipt, null, "an unauthenticated call got a receipt");
  assert.equal(emitted.producer_answer.clauses, null,
    "an unauthenticated call reached the clauses");
  // The gate still NAMES card 9's seat holder — that is the declaration of who
  // may sign, and it is public. What must not exist is a signature: no identity,
  // no session ref, no receipt.
  assert.equal(JSON.stringify(emitted).includes("session:"), false,
    "a session ref was minted for an answer nobody authenticated");
  // And nowhere in the answer is there an `authenticated-receipt-identity.v1`:
  // asked of the SHAPE rather than of a field name, because the gate legitimately
  // names the receipt's field list in its own policy preimage.
  const identities = [];
  const walk = (value) => {
    if (Array.isArray(value)) { value.forEach(walk); return; }
    if (value === null || typeof value !== "object") return;
    const keys = Object.keys(value).sort().join(",");
    if (keys === "actor_id,authority_class,session_ref") identities.push(value);
    Object.values(value).forEach(walk);
  };
  walk(emitted);
  assert.deepEqual(identities, [],
    "an identity was derived for an answer nobody authenticated");

  // AND src ITSELF, imported bare, answers the same way. A CLI probe and a test
  // are the same case as far as this module is concerned.
  const bare = await producer.v5A02GateZeroEmitOutcome();
  assert.equal(bare.reason_id, "gate_zero_producer_identity_refused");
  assert.equal(bare.receipt, null);
});

test("IDENTITY: an authenticated actor of the wrong derived class is refused", async () => {
  // A verified partner is authenticated, registered and human — and is not the
  // class r7's registry admits for an independent control-plane oracle. The
  // class is DERIVED by identity.js from the live actor; this module only checks
  // membership, so widening it is an edit somebody reviews.
  const emitted = await emitFrom({}, recordedPartnerActor);
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_producer_identity_refused");
  assert.equal(emitted.producer_answer.receipt, null);
});

test("IDENTITY: an actor with no server-stamped correlation id has no session, and is refused", async () => {
  const emitted = await emitFrom({},
    identity => Object.assign(recordedReviewerActor(identity), { correlation_id: null }));
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_producer_identity_refused");
  assert.equal(emitted.producer_answer.receipt, null);
});

// ===========================================================================
// CONTROL 11 — a FABRICATED actor object obtains no receipt.
//
// THE MUTATION CONTROL FOR THE WHOLE IDENTITY DESIGN, and the one the second
// review round asked for by name. The first correction shipped
// `runInAuthenticatedCall(actor, fn)` as a public export that took an ordinary
// object, and the reviewer obtained `authority_class: "review_agent"` out of a
// literal. Both that name and its reader are gone; what replaces them derives
// an identity only for an actor identity.js itself minted from a credential.
// ===========================================================================

test("IDENTITY: a fabricated actor object obtains no identity and no receipt", async () => {
  const fabricated = fabricatedReviewerActor();
  const minted = recordedReviewerActor(await import("../src/identity.js"));
  // NON-VACUOUS: the fabricated object is field-for-field what the real door
  // mints, so what refuses it below is provenance and nothing else.
  assert.deepEqual(Object.keys(fabricated).sort(), Object.keys(minted).sort());
  for (const key of Object.keys(fabricated)) assert.equal(fabricated[key], minted[key], key);

  const emitted = await emitFrom({}, () => fabricated);
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_producer_identity_refused");
  assert.equal(emitted.outcome_digest, null);
  assert.equal(emitted.producer_answer.receipt, null);
  assert.equal(emitted.producer_answer.clauses, null);
  assert.equal(JSON.stringify(emitted).includes("session:"), false,
    "a session ref was minted for a fabricated actor");

  // AND THE SAME OBJECT THROUGH THE SAME DISPATCH DERIVES NOTHING AT ALL, asked
  // of identity.js directly from inside a real dispatched verb call: the real
  // door's actor answers a three-field identity there, the fabricated one null.
  const { target } = stageTree({});
  const identity = await moduleOfTree(target, IDENTITY_FILE);
  const read = () => identity.authenticatedIdentity.receiptIdentity();
  assert.deepEqual(await inDispatchedVerb(target, recordedReviewerActor(identity), read), {
    actor_id: REVIEWING_SEAT_ACTOR,
    session_ref: `session:${CORRELATION_ID}`,
    authority_class: "review_agent",
  });
  assert.equal(await inDispatchedVerb(target, fabricatedReviewerActor(), read), null);

  // AND EVERY SURFACE A PREVIOUS ROUND SHIPPED IS GONE BY NAME, so nothing can
  // quietly go back to one. The last two are amendment 8's: the door that took
  // the token map as an argument, and the exported context entry.
  for (const gone of ["runInAuthenticatedCall", "authenticatedCallIdentity",
    "reviewActorForToken", "dispatchAuthenticatedCall", "authenticatedCallReceiptIdentity"])
    assert.equal(Object.hasOwn(identity, gone), false, `${gone} is still exported`);
});

// ===========================================================================
// CONTROL 13 — THE COPY, AND THE WRITE-OVER. Amendment 8's subject, and the
// reviewer's own probe of the second correction round.
//
// The brand it replaced was an ENUMERABLE property, so it survived `{ ...actor }`
// — including the forger's. The reviewer took a branded partner actor, spread
// it, rewrote the copy as `codex-reviewer`, put it through the REAL
// `executeRegisteredTool`, and was signed for as a review agent. The brand is
// now membership of a module-private WeakSet, so the copy is simply a different
// object; and the fields the identity is built from are pinned at the moment the
// credential was verified, so writing over the ORIGINAL does not move them
// either. Both halves are asserted, and both are asserted non-vacuously.
// ===========================================================================

test("IDENTITY: a spread copy of a branded actor, rewritten as the reviewer, is refused", async () => {
  const rewrittenCopy = identity => ({
    ...recordedPartnerActor(identity),
    slug: REVIEWING_SEAT_ACTOR, display: `Reviewer (${REVIEWING_SEAT_ACTOR})`,
    human: false, review: true, via: "review-token",
  });

  const emitted = await emitFrom({}, rewrittenCopy);
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_producer_identity_refused");
  assert.equal(emitted.outcome_digest, null);
  assert.equal(emitted.producer_answer.receipt, null);
  assert.equal(JSON.stringify(emitted).includes("session:"), false,
    "a session ref was minted for a copied actor");

  // NON-VACUOUS, asked of identity.js from inside the SAME real dispatch: the
  // object the copy was made FROM derives an identity, and the copy derives
  // none. So what refuses above is the copying and nothing else.
  const { target } = stageTree({});
  const identity = await moduleOfTree(target, IDENTITY_FILE);
  const read = () => identity.authenticatedIdentity.receiptIdentity();
  const branded = recordedPartnerActor(identity);
  assert.deepEqual(await inDispatchedVerb(target, branded, read), {
    actor_id: "joe",
    session_ref: `session:${CORRELATION_ID}`,
    authority_class: "verified_partner",
  });
  assert.equal(await inDispatchedVerb(target, rewrittenCopy(identity), read), null);
});

test("IDENTITY: writing the reviewer's fields onto a branded actor does not move what it is", async () => {
  // THE OTHER HALF. Object identity alone would not stop this: the server now
  // decorates actors IN PLACE, so a forger inside the process could write onto
  // one too. The credential is pinned at the instant it was verified and is
  // never re-read off the actor, so the receipt still names the partner — and
  // the partner's class is not the one this oracle admits, so it refuses.
  const writtenOver = identity => Object.assign(recordedPartnerActor(identity), {
    slug: REVIEWING_SEAT_ACTOR, display: `Reviewer (${REVIEWING_SEAT_ACTOR})`,
    human: false, review: true, via: "review-token",
  });

  const emitted = await emitFrom({}, writtenOver);
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_producer_identity_refused");
  assert.equal(emitted.producer_answer.receipt, null);

  const { target } = stageTree({});
  const identity = await moduleOfTree(target, IDENTITY_FILE);
  assert.deepEqual(
    await inDispatchedVerb(target, writtenOver(identity),
      () => identity.authenticatedIdentity.receiptIdentity()),
    { actor_id: "joe", session_ref: `session:${CORRELATION_ID}`,
      authority_class: "verified_partner" },
    "a field written onto a branded actor rewrote who it was");
});

test("IDENTITY: a caller-chosen bearer plus a caller token map authenticates nobody", async () => {
  const { target } = stageTree({});
  const identity = await moduleOfTree(target, IDENTITY_FILE);
  const door = identity.authenticatedIdentity;
  const CALLERS_OWN = "a-bearer-the-caller-chose";
  const CALLERS_MAP = JSON.stringify({ [REVIEWING_SEAT_ACTOR]: CALLERS_OWN });

  // THE DOOR TAKES A BEARER AND NOTHING ELSE, so the call the reviewer made is
  // not one that can be written any more: a second argument is not read.
  assert.equal(door.reviewActorForToken.length, 1);
  assert.equal(door.reviewActorForToken(`Bearer ${CALLERS_OWN}`, CALLERS_MAP), null);
  // And before the server seals a map, no bearer authenticates at all.
  assert.equal(door.reviewActorForToken(`Bearer ${RECORDED_REVIEW_TOKEN}`), null);

  // THE SERVER'S BOOTSTRAP, once. Everything after it is refused, so a caller
  // arriving later has nowhere to put a map of its own.
  assert.equal(door.sealServerReviewTokens(RECORDED_REVIEW_TOKENS), true);
  assert.equal(door.sealServerReviewTokens(CALLERS_MAP), false);
  assert.equal(door.reviewActorForToken(`Bearer ${CALLERS_OWN}`), null);
  // NON-VACUOUS: the map the server did seal still authenticates its own bearer.
  assert.notEqual(door.reviewActorForToken(`Bearer ${RECORDED_REVIEW_TOKEN}`), null);

  // AND THE CALLER'S BEARER IS REFUSED THROUGH THE REAL DISPATCH TOO, which is
  // where the reviewer's probe ended: no actor, so no identity, so no receipt.
  const emitted = await emitFrom({}, staged => {
    // The server boots first, the way index.js boots it on the first request.
    assert.equal(staged.authenticatedIdentity.sealServerReviewTokens(RECORDED_REVIEW_TOKENS), true);
    // The caller then arrives with a map of its own and is refused it, and its
    // chosen bearer authenticates nothing against the map the server sealed.
    assert.equal(staged.authenticatedIdentity.sealServerReviewTokens(CALLERS_MAP), false);
    assert.equal(staged.authenticatedIdentity.reviewActorForToken(`Bearer ${CALLERS_OWN}`), null,
      "a caller-chosen bearer authenticated against a caller-chosen map");
    // So all it is left holding is an object it wrote itself, which is control
    // 11's case again and refuses for the same reason.
    return { slug: REVIEWING_SEAT_ACTOR, display: `Reviewer (${REVIEWING_SEAT_ACTOR})`,
      human: false, review: true, via: "review-token", client_id: null,
      correlation_id: CORRELATION_ID };
  });
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_producer_identity_refused");
  assert.equal(emitted.producer_answer.receipt, null);
});

test("DIGEST: each bound digest stands on its own artifact's bytes", async () => {
  const first = await emitFrom({});
  // The receipts differ only by their instants, so two runs over one candidate
  // agree on all five bound digests.
  const second = await emitFrom({});
  for (const field of ["subject_digest", "candidate_digest", "policy_digest",
    "environment_manifest_digest", "fixture_set_digest"])
    assert.equal(first.receipt[field], second.receipt[field], field);

  // (a) THE CANDIDATE DIGEST IS THE SEALED ARTIFACT MANIFEST FOR THE HEAD
  // REVISION — recomputed here by artifact-trust.js's own artifactManifestDigest
  // over the staged tree's actual bytes, which is the same JCS SHA-256 recipe
  // ops.scac_artifact_manifest_digest recomputes in the database. A producer
  // that hashed a DESCRIPTION of the candidate would satisfy every shape
  // assertion in this file and fail this one.
  const { target, treeId } = stageTree({});
  const gate = await moduleOfTree(target, GATE_FILE);
  const identity = await moduleOfTree(target, IDENTITY_FILE);
  const own = await inDispatchedVerb(target, recordedReviewerActor(identity),
    () => gate.emitGateZeroOutcome());
  assert.equal(own.receipt.candidate_digest,
    candidateDigestOfTree(target, REVISION_ALL_SUCCEED, treeId, own.receipt.policy_digest),
    "the candidate digest is not the sealed artifact manifest for this tree");

  // (b) ONE BYTE OF THE CANDIDATE MOVES IT. A comment appended to one module in
  // the staged src is one byte the candidate tree did not have before.
  const edited = await emitFrom({ candidateEdit: "one byte of the candidate tree" });
  assert.notEqual(edited.receipt.candidate_digest, first.receipt.candidate_digest);
  // And it moves NOTHING ELSE: the environment and the fixture set did not change.
  assert.equal(edited.receipt.environment_manifest_digest,
    first.receipt.environment_manifest_digest);
  assert.equal(edited.receipt.fixture_set_digest, first.receipt.fixture_set_digest);

  // (b2) AND ONE TRACKED FILE MUTATED AFTER THE SEAL MOVES IT TOO. HEAD's tree
  // still seals the old bytes, so only the observed half of the manifest moves —
  // which is the control that the manifest is not merely a relabelling of HEAD's
  // tree id, and the one the second review round asked for by name.
  const dirty = await emitFrom({ postSealEdit: "one byte the revision does not seal" });
  assert.notEqual(dirty.receipt.candidate_digest, first.receipt.candidate_digest);
  assert.equal(dirty.receipt.environment_manifest_digest,
    first.receipt.environment_manifest_digest);

  // (c) THE MANIFEST IS BOUND TO HEAD, so pointing the run at another revision
  // moves the candidate digest even when the working tree is byte-identical —
  // and the two halves are no longer independent, which is the correction. The
  // manifest's `source_digest` is the blob ids HEAD'S TREE seals, its
  // `artifact_digest` is the bytes at exactly those paths, and both are inside
  // the one digest: neither the revision nor the bytes can move without it.
  const elsewhere = await emitFrom({ revision: REVISION_FAILED_ANCESTOR });
  assert.notEqual(elsewhere.receipt.candidate_digest, first.receipt.candidate_digest);
  assert.equal(elsewhere.receipt.subject_digest, first.receipt.subject_digest,
    "the subject is the gate, not the candidate, and must not move with it");

  // (d) THE ENVIRONMENT DIGEST IS OVER ops/config/environments.json, so one byte
  // of THAT file moves it and moves nothing else.
  const environment = await emitFrom({ environmentEdit: 2 });
  assert.notEqual(environment.receipt.environment_manifest_digest,
    first.receipt.environment_manifest_digest);
  assert.equal(environment.receipt.fixture_set_digest, first.receipt.fixture_set_digest);

  // (e) THE FIXTURE-SET DIGEST IS OVER THE SEALED FIXTURE BYTES, so one byte of
  // one sealed fixture moves it and moves nothing else.
  const fixtures = await emitFrom({ fixtureEdit: "one byte of the sealed fixture set" });
  assert.notEqual(fixtures.receipt.fixture_set_digest, first.receipt.fixture_set_digest);
  assert.equal(fixtures.receipt.candidate_digest, first.receipt.candidate_digest);
  assert.equal(fixtures.receipt.environment_manifest_digest,
    first.receipt.environment_manifest_digest);

  // (f) AND NONE OF THE FIVE IS A HASH OF ITS OWN NAME. The refutation the
  // review asked for, stated as an assertion rather than as a comment.
  for (const [field, described] of [
    ["candidate_digest", { head_revision: REVISION_ALL_SUCCEED,
      declared_checks: ["local-db-ci --class migration",
        "main canary (gates, migration, types, freshness)", "ops/ci.sh --strict"] }],
    ["environment_manifest_digest", { tenant: "carr-internal",
      subject_environment: "candidate", evidence_scope: "candidate-and-test" }],
    ["fixture_set_digest", { service_key: "gate-zero-canary" }],
  ])
    assert.notEqual(first.receipt[field], digest(described),
      `${field} is a digest of a description of the artifact rather than of the artifact`);
});

// ===========================================================================
// CONTROL 3 — each clause turned off, one at a time.
// ===========================================================================

/**
 * ONE ROW BROKEN PER CASE, and each breaks the row of exactly one clause. The
 * rows behind the other two are untouched, so a clause that stopped being read
 * fails on one case rather than on none — which is the property a conjunction
 * over three booleans would not have.
 *
 * THE FAULT MOVED FROM THE BINDING TO THE STORE, and that is the correction:
 * the producer derives every address now, so there is no address a test can
 * write a bad value into. A broken world is a broken ROW.
 */
const CLAUSE_FALSIFIERS = Object.freeze([
  {
    name: "the predecessor clause, with the receipt and the card disagreeing",
    options: { predecessorWorld: "receipt-card-mismatch" },
    reason: "gate_zero_predecessor_clause_failed",
    clause: "predecessor_join",
  },
  {
    name: "the scheduler clause, with a canary bound to no receipt of its own",
    options: { ledgerCanary: CANARY_UNBOUND },
    reason: "gate_zero_scheduler_clause_failed",
    clause: "scheduler_canary",
  },
  {
    name: "the gate-graph clause, with a green gate over a failed ancestor",
    options: { revision: REVISION_FAILED_ANCESTOR },
    reason: "gate_zero_gate_graph_clause_failed",
    clause: "gate_graph",
  },
]);

test("CLAUSES: each of the three, turned off on its own, stops the gate passing", async () => {
  for (const falsifier of CLAUSE_FALSIFIERS) {
    const emitted = await emitFrom(falsifier.options);
    assert.equal(emitted.passable, false, falsifier.name);
    assert.equal(emitted.reason_id, falsifier.reason, falsifier.name);
    assert.equal(emitted.join[falsifier.clause].state, "failed", falsifier.name);
    // AND THE OTHER TWO STILL HELD, which is what makes it one clause and not
    // three failing together for an unrelated reason.
    for (const [name, clause] of Object.entries(emitted.join))
      if (name !== falsifier.clause)
        assert.equal(clause.state, "held", `${falsifier.name}: ${name} also failed`);
  }
  // THE CONTROL IS A CONTROL: the unmodified tree, staged the same way through
  // the same machinery, passes. Without this the three cases above would pass
  // just as well against a staging step that broke the file.
  assert.equal((await emitFrom({})).passable, true,
    "the staging itself breaks the run, so the falsifiers prove nothing");
});

test("TAMPER: an acceptance receipt the card does not carry is refused, and never echoed", async () => {
  // The receipt row signed one hash and the card carries another. Card 11 reads
  // the acceptance receipt BY THE STEP REF now — no hash is looked up and pasted
  // by anybody — and a store whose two sides disagree joins nothing.
  const emitted = await emitFrom({ predecessorWorld: "receipt-card-mismatch" });
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_predecessor_clause_failed");
  const clause = emitted.join.predecessor_join;
  assert.deepEqual([...clause.detail.failed].sort(), [
    "step:wr40-repository-outcome",
    "step:wr46-dissolution-outcome",
    "step:wr54-backup-recovery-outcome",
  ]);
  // AND NO HASH TRAVELS. The reader compares and never echoes, which is as true
  // of a derived hash as it was of a supplied one.
  assert.equal(JSON.stringify(emitted).includes(FORGED_HASH.slice("sha256:".length)), false,
    "an acceptance hash came back out of the answer");
});

// ===========================================================================
// CONTROL 4 — the seat, and the three rulings.
// ===========================================================================

test("SEAT: putting the holder back to null takes the whole slice dark", async () => {
  // The producer module is in this tree, unedited, with a fully pasted run
  // binding and every row present. The seam is shut anyway, because the only
  // thing that opens it is card 9's declaration.
  const emitted = await emitFrom({ staffedSeat: false });
  assert.equal(emitted.producer_bound, false);
  assert.equal(emitted.passable, false);
  assert.equal(emitted.oracle_seat_bound, false);
  assert.equal(emitted.reason_id, "gate_zero_producer_seam_unavailable");
  assert.equal(emitted.producer_answer, null, "an unstaffed seat still reached the producer");
  assert.equal(emitted.outcome_digest, null);
  assert.equal(emitted.observed_at, null);
  assert.deepEqual(emitted.owed_seams, ["seam:gate-zero-read-only-outcome-producer"]);
  assert.deepEqual([...emitted.undecided_governance_questions],
    ["which independent seat holds oracle:gate-producer:gate-zero-read-only"]);
});

test("READERS: withdrawing any one ruling leaves the producer with nothing to read", async () => {
  for (const [index, card] of ["card:11", "card:12", "card:13"].entries()) {
    const emitted = await emitFrom({ withdrawnCards: [index] });
    assert.equal(emitted.passable, false, card);
    // The producer is bound and was reached — it is the EVIDENCE that is gone,
    // and the reason says so rather than blaming the seam.
    assert.equal(emitted.producer_bound, true, card);
    assert.equal(emitted.reason_id, "gate_zero_evidence_unavailable", card);
    assert.equal(emitted.outcome_digest, null, card);
    // A withdrawn ruling is not a failed clause: nothing was read, so nothing
    // failed, and the negative admission is not even attempted over it.
    assert.equal(emitted.producer_answer.negative_admission, null, card);
  }
});

test("EVIDENCE: a canary row that does not exist is unanswered, not failed", async () => {
  const emitted = await emitFrom({ ledgerCanary: CANARY_ABSENT });
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_evidence_unavailable");
  assert.equal(emitted.join, null, "an outcome was emitted over a row that does not exist");
  assert.equal(emitted.producer_answer.clauses.scheduler_canary.state, "unknown");
});

test("EVIDENCE: a check with no conclusion yet is unanswered, not a failure", async () => {
  const emitted = await emitFrom({ revision: REVISION_UNFINISHED });
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_evidence_unavailable");
  assert.equal(emitted.producer_answer.clauses.gate_graph.state, "unknown");
  assert.deepEqual(emitted.producer_answer.clauses.gate_graph.detail.not_answered,
    ["local-db-ci-migration"]);
});

// ===========================================================================
// CONTROL 5 — Q036.D1's own falsifier.
// ===========================================================================

test("PROPAGATION: an injected failure produces a FAILING outcome, kept and digested", async () => {
  // The top gate reports success and its ancestor did not. A clause that
  // conjoined self-conclusions would read this graph as green, which is the
  // false-green result Q036.D1 names as a failure of the gate itself.
  const emitted = await emitFrom({ revision: REVISION_FAILED_ANCESTOR });
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_gate_graph_clause_failed");

  // AND IT IS AN OUTCOME, NOT AN ABSENCE. The retry policy this registration
  // carries says retryable, every run kept, failed runs retained — so a run that
  // read everything and found a failed gate has a receipt, a digest and an
  // instant, and its receipt says so in r7's own word.
  assert.equal(emitted.status, "outcome_produced");
  assert.equal(emitted.receipt_status, "fail");
  assert.equal(emitted.receipt.status, "fail");
  assert.match(emitted.outcome_digest, /^sha256:[0-9a-f]{64}$/);
  assert.equal(emitted.outcome_digest, digest(emitted.receipt));
  assert.match(emitted.observed_at, /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);

  // AND THE DENIALS WERE OBSERVED, which is the field's whole claim.
  assert.equal(emitted.receipt.negative_admission_result, "all_required_denials_observed");
  assert.deepEqual(emitted.negative_admission.missed_cases, []);
  assert.equal(emitted.negative_admission.observed_codes.length, 7);
  assert.ok(emitted.negative_admission.observed_codes
    .includes("gate_reporting_success_over_a_failed_ancestor"));

  // THE PROPAGATION ITSELF: the top gate reported success and is still refused.
  const clause = emitted.join.gate_graph;
  assert.deepEqual(clause.detail.conclusion_not_success, ["ops-ci-strict"]);
  assert.deepEqual(clause.detail.inherited_not_success, ["main-canary"]);
});

test("PROPAGATION: the required denials are re-derived, not asserted", async () => {
  const emitted = await emitFrom({});
  // Seven cases, and every one of them is a mutation of THIS run's readings put
  // back through this module's own live clauses. The mutation control for the
  // prover is the prover itself: it reports which cases it observed, and a case
  // that stopped firing lands in `missed_cases` and refuses the whole run.
  assert.deepEqual(emitted.negative_admission.observed_codes, [
    "gate_conclusion_not_success",
    "gate_reporting_success_over_a_failed_ancestor",
    "one_outcome_record_closing_two_predecessors",
    "predecessor_acceptance_receipt_hash_mismatch",
    "predecessor_not_accepted",
    "scheduler_canary_not_bound_to_its_receipt",
    "scheduler_observation_not_after_dispatch",
  ]);
});

// ===========================================================================
// CONTROL 8 — self-review.
// ===========================================================================

test("IDENTITY: a subject maker that is the reviewing seat is denied", async () => {
  // r7's rule denies same-actor self-review, and the guard has to exist whether
  // or not today's class set can reach it: `review_agent` is the only admitted
  // class and a committer never resolves to a machine identity, so the two seats
  // cannot collide in the shipped configuration. The case is reached the way
  // every other case here is reached — by editing one line in a throwaway tree —
  // so the guard is PROVED rather than assumed unreachable.
  const emitted = await emitFrom({ admitPartnerClass: true }, recordedPartnerActor);
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_producer_identity_refused");
  assert.equal(emitted.outcome_digest, null);
  assert.equal(emitted.producer_answer.clauses, null,
    "a self-review reached the clauses before it was denied");
  assert.equal(emitted.producer_answer.receipt, null);
});

test("PRODUCED: a head written by GitHub's merge path still names its maker", async () => {
  // THE THIRD REVIEW ROUND'S SECOND FINDING, as a control. Every head GitHub's
  // own merge path writes carries the author's `users.noreply.github.com`
  // address as the committer — it is what THIS PR's head carries — and a
  // registry holding only the Gmail addresses reported `subject_maker` unnamed
  // for the very branch under review.
  const emitted = await emitFrom({ maker: MAKER_NOREPLY_EMAIL });
  assert.equal(emitted.passable, true, emitted.unavailable_because ?? emitted.reason_id);

  // AND THE GMAIL ROW STILL NAMES THE SAME PARTNER. It stopped being the staged
  // default in PR 1014's second correction — the default is the address git
  // reports for this repository's own head, which is the noreply one above — so
  // without this line the registry's other row for the same partner would have
  // no case at all.
  const byGmail = await emitFrom({ maker: MAKER_GMAIL_EMAIL });
  assert.equal(byGmail.passable, true, byGmail.unavailable_because ?? byGmail.reason_id);
  assert.equal(byGmail.receipt.subject_maker_identity.actor_id, SUBJECT_MAKER_ACTOR);
  assert.equal(emitted.receipt.subject_maker_identity.actor_id, SUBJECT_MAKER_ACTOR);
  assert.equal(emitted.receipt.subject_maker_identity.authority_class, "verified_partner");
  assert.equal(emitted.receipt.subject_maker_identity.session_ref,
    `session:${CORRELATION_ID}:candidate-build`);

  // AND GITHUB'S SHARED WEB-FLOW COMMITTER STILL NAMES NOBODY. `noreply@github.com`
  // is carried by every web commit by every account, so registering it would put
  // a stranger's work under a partner's name. It is deliberately absent from the
  // table, and this is the line that keeps it absent.
  const shared = await emitFrom({ maker: "noreply@github.com" });
  assert.equal(shared.passable, false);
  assert.equal(shared.reason_id, "gate_zero_run_binding_unnamed");
  assert.deepEqual(shared.producer_answer.unnamed_bindings, ["subject_maker"]);
});

test("ABSENCE: a committer this system does not register IS the absent ruled row", async () => {
  // A real, well-formed address identity.js's actor registry does not map. The
  // repository is intact and every sealed file is on disk; what is genuinely
  // absent is the REGISTRATION of the principal a receipt would name. This is
  // the one case `gate_zero_run_binding_unnamed` is left with, and it says which.
  const emitted = await emitFrom({ maker: "someone.else@example.com" });
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_run_binding_unnamed");
  assert.equal(emitted.producer_answer.clauses, null);
  assert.deepEqual(emitted.producer_answer.unnamed_bindings, ["subject_maker"]);
});

// ===========================================================================
// THE THREE KINDS OF ABSENCE, AND THEY ARE THREE DIFFERENT ANSWERS.
//
// The second review round's third finding: `gate_zero_run_binding_unnamed`
// claimed "a ruled store row is absent" for unreadable git metadata and for a
// missing file alike. A refusal that misdescribes its own cause sends the next
// session to debug the wrong thing, so the three are now separated and each case
// below pins one of them.
// ===========================================================================

test("ABSENCE: a candidate whose repository names no revision is a METADATA absence", async () => {
  // A .git with nothing in it: no HEAD, so no revision, so no commit object, no
  // committer and no sealed tree. Nothing is missing from a ruled store here —
  // this run cannot see the candidate it is standing in, and it says so under
  // its own reason and names every part it could not resolve.
  const emitted = await emitFrom({ git: "empty" });
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_candidate_metadata_absent");
  assert.deepEqual(emitted.producer_answer.absent_candidate_metadata,
    ["head_revision", "head_revision_object", "head_maker_address",
      "head_candidate_manifest"]);
  assert.match(emitted.producer_answer.unavailable_because, /head_revision/);
  assert.equal(emitted.producer_answer.receipt, null);
  assert.equal(emitted.producer_answer.clauses, null);
});

test("ABSENCE: a sealed file a digest stands on that is not on disk is an ARTIFACT absence", async () => {
  // The environment manifest, deleted. The repository is intact, HEAD resolves,
  // the committer is registered — and one FILE this receipt's digests stand on
  // is not there. That is neither unreadable metadata nor an absent ruled row.
  const emitted = await emitFrom({ withoutEnvironmentManifest: true });
  assert.equal(emitted.passable, false);
  assert.equal(emitted.reason_id, "gate_zero_sealed_artifact_absent");
  assert.deepEqual(emitted.producer_answer.absent_sealed_artifacts, ["environment_manifest"]);
  assert.equal(emitted.producer_answer.receipt, null);
});

test("ABSENCE: HEAD's own object out of reach is a METADATA absence, not a missing row", async () => {
  // The one shape a real clone reaches: HEAD names a revision whose object this
  // producer cannot inflate (in a real repository, because it is packed). It
  // says exactly that, by name, rather than reporting an absent binding row.
  const { target } = stageTree({});
  const identity = await moduleOfTree(target, IDENTITY_FILE);
  const base = join(target, "..", "..");
  rmSync(join(base, ".git", "objects", REVISION_ALL_SUCCEED.slice(0, 2),
    REVISION_ALL_SUCCEED.slice(2)));
  const gate = await moduleOfTree(target, GATE_FILE);
  const emitted = await inDispatchedVerb(target, recordedReviewerActor(identity),
    () => gate.emitGateZeroOutcome());
  assert.equal(emitted.reason_id, "gate_zero_candidate_metadata_absent");
  assert.deepEqual(emitted.producer_answer.absent_candidate_metadata,
    ["head_revision_object", "head_maker_address", "head_candidate_manifest"]);
});

// ===========================================================================
// CONTROL 7 — no caller-supplied anything, by parse and by call.
// ===========================================================================

test("SURFACE: the producer's export list is exactly four constants and one callable", () => {
  assert.deepEqual(Object.keys(producer).sort(), [
    "V5_A02_GATE_ZERO_CLAUSE_STATES",
    "V5_A02_GATE_ZERO_PRODUCER_REASON_IDS",
    "V5_A02_GATE_ZERO_PRODUCER_SCHEMA_VERSION",
    "V5_A02_GATE_ZERO_RECEIPT_DIGEST_RECIPE",
    "v5A02GateZeroEmitOutcome",
  ]);
  // EXACTLY ONE export is callable, and its arity is zero. An exported builder
  // is the one shape that can be handed caller-supplied references — the PR 990
  // defect — and a function with no parameters has nowhere to put one.
  const callable = Object.entries(producer)
    .filter(([, value]) => typeof value === "function").map(([name]) => name);
  assert.deepEqual(callable, ["v5A02GateZeroEmitOutcome"]);
  assert.equal(producer.v5A02GateZeroEmitOutcome.length, 0);
  // No binding door, no setter, no registry, under any spelling — asked of what
  // could BE one. A constant named for the run binding's state is a fact a
  // reader can check; only a callable can be handed something.
  for (const [name, value] of Object.entries(producer))
    if (typeof value === "function")
      assert.equal(/bind|set|register|configure|inject/i.test(name), false,
        `${name} is a binding door on the producer surface`);
  // And nothing exported is writable or extensible, so a door cannot be added
  // to the namespace after the fact either.
  for (const [name, value] of Object.entries(producer))
    if (value !== null && typeof value === "object")
      assert.ok(Object.isFrozen(value), `${name} is not frozen`);
});

test("SURFACE: handing the producer an argument is a contract violation, thrown synchronously", () => {
  for (const argument of [
    { emitOutcome: () => ({ passable: true }) },
    { outcomeHash: `sha256:${"a".repeat(64)}` },
    { headSha: REVISION_ALL_SUCCEED },
    "allow", 1, true, null, undefined, [],
  ])
    assert.throws(() => producer.v5A02GateZeroEmitOutcome(argument),
      error => error instanceof V5BoundaryError
        && error.code === "gate_zero_producer_takes_no_argument",
      JSON.stringify(argument ?? null));
  // TWO arguments too, and the throw is synchronous rather than a rejection: a
  // caller reading a refusal never has to catch one, and a contract violation is
  // refused before anything is awaited.
  assert.throws(() => producer.v5A02GateZeroEmitOutcome(1, 2), V5BoundaryError);
});

test("SHAPE: the one callable wears amendment 2's closed shape", () => {
  const fn = producer.v5A02GateZeroEmitOutcome;
  assert.equal(Object.hasOwn(fn, "prototype"), false, "carries a prototype");
  assert.throws(() => Reflect.construct(fn, []), TypeError);
  assert.ok(Object.isFrozen(fn), "is not frozen");
  assert.equal(types.isProxy(fn), false, "is a Proxy");
  const descriptor = Object.getOwnPropertyDescriptor(fn, Symbol.hasInstance);
  assert.ok(descriptor !== undefined, "has no own Symbol.hasInstance");
  assert.equal(descriptor.writable, false);
  assert.equal(descriptor.configurable, false);
  assert.equal(descriptor.enumerable, false);
  // The operand is never read: every trap on it throws, and `instanceof` is
  // still false rather than the caller's own error.
  const hostile = new Proxy({}, {
    get() { throw new Error("the operand was read"); },
    getPrototypeOf() { throw new Error("the operand's chain was walked"); },
  });
  assert.equal(hostile instanceof fn, false);
});

/**
 * AMENDMENT 2'S SHAPE, ENUMERATED OVER EVERY MODULE THIS PR ADDED A CALLABLE TO.
 *
 * The second review round's standards finding: the new exports were plain
 * function declarations — a prototype, constructable, and `instanceof` answered
 * by walking the left operand's chain.
 *
 * THE ENUMERATION IS DYNAMIC, which the third round's third finding was about:
 * the list used to be written out by hand here, so the claim "any future export
 * arrives holding this shape or arrives red" was true of nothing. identity.js's
 * whole new surface is now ONE frozen namespace export, and the test walks
 * `Object.keys` of it — a seventh member added to that object is checked without
 * anyone remembering to add a name here. The file's OLDER doors are deliberately
 * out of scope: they predate the amendment and are not this PR's changed
 * surface. The testhelper is enumerated whole, because every callable in it is
 * new.
 */
function assertClosedShape(fn, where) {
  assert.equal(typeof fn, "function", `${where} is not callable`);
  assert.equal(Object.hasOwn(fn, "prototype"), false, `${where} carries a prototype`);
  assert.throws(() => Reflect.construct(fn, []), TypeError, `${where} is constructable`);
  assert.ok(Object.isFrozen(fn), `${where} is not frozen`);
  assert.equal(types.isProxy(fn), false, `${where} is a Proxy`);
  const descriptor = Object.getOwnPropertyDescriptor(fn, Symbol.hasInstance);
  assert.ok(descriptor !== undefined, `${where} has no own Symbol.hasInstance`);
  assert.ok(Object.hasOwn(descriptor, "value"), `${where}'s Symbol.hasInstance is an accessor`);
  assert.equal(descriptor.writable, false, where);
  assert.equal(descriptor.configurable, false, where);
  assert.equal(descriptor.enumerable, false, where);
  const hostile = new Proxy({}, {
    get() { throw new Error("the operand was read"); },
    getPrototypeOf() { throw new Error("the operand's chain was walked"); },
  });
  assert.equal(hostile instanceof fn, false, where);
}

test("SHAPE: every callable this PR added to identity.js and the walker wears amendment 2's shape", async () => {
  const identity = await import("../src/identity.js");
  const surface = identity.authenticatedIdentity;
  assert.ok(Object.isFrozen(surface), "identity.js's new surface is not frozen");
  // ENUMERATED, NOT LISTED. Every member of the namespace is checked, whatever
  // it is called and however many there are.
  const added = Object.keys(surface);
  assert.ok(added.length >= 6, `identity.js's new surface shrank to ${added.length}`);
  for (const name of added) assertClosedShape(surface[name], `identity.js#authenticatedIdentity.${name}`);
  // AND THE SURFACE IS ALL OF IT: amendment 8 leaves identity.js with no other
  // new top-level callable, so the enumeration above is not enumerating a subset.
  assert.deepEqual(added.filter(name => typeof surface[name] !== "function"), []);

  const walker = await import("./gate-zero-reachability-walk.testhelper.mjs");
  const callables = Object.entries(walker).filter(([, value]) => typeof value === "function");
  assert.deepEqual(callables.map(([name]) => name).sort(),
    ["pathToValue", "topLevelIdentityOnly"]);
  for (const [name, value] of callables)
    assertClosedShape(value, `gate-zero-reachability-walk.testhelper.mjs#${name}`);

  // AND THE WALKER STILL WALKS — a shape assertion over a callable that stopped
  // working would pass every line above.
  assert.equal(walker.pathToValue({ outer: { inner: walker } }, walker), ".outer.inner");
});


test("REACHABILITY: exactly one module in src may import the producer, and none re-exports it", () => {
  const imports = moduleImports(SRC);
  assert.ok(Object.keys(imports).length > 100, "every module in src must have been parsed");
  assert.ok(Object.hasOwn(imports, PRODUCER_FILE), "the parser did not see the producer at all");

  const importers = Object.entries(imports)
    .filter(([, specifiers]) => specifiers.some(one => one.endsWith(`/${PRODUCER_FILE}`)))
    .map(([name]) => name).sort();
  assert.deepEqual(importers, [GATE_FILE],
    "a module other than the gate can reach the producer");

  // The producer reaches no test file, by the same parser.
  for (const specifier of imports[PRODUCER_FILE])
    assert.equal(/\/test\/|\.testonly\.|\.testhelper\.|\.fixture\./.test(specifier), false,
      `the producer imports ${specifier}`);
});

/**
 * AND THE CALLABLE IS NOT REACHABLE FROM ANY PUBLIC NAMESPACE BUT ITS OWN — by
 * the repository's OWN runtime walk, not by a pair of source-text patterns.
 *
 * The first draft proved this with two regexes over the gate's source: one for
 * `export { v5A02GateZeroEmitOutcome ... }` and one for a star re-export of the
 * producer's path. Both are greps wearing a guard's clothes. `export {
 * v5A02GateZeroEmitOutcome as emit }` matches the first only by accident of
 * spelling; `export const api = { emit: v5A02GateZeroEmitOutcome }`, a
 * symbol-keyed property, a prototype, a getter, an accessor FUNCTION and an
 * inherited getter read under the exported child as receiver all match neither.
 *
 * The walk that does answer those was written and corrected over eight rounds
 * for the seam readers' own shared predicate. It is IMPORTED here rather than
 * retyped — gate-zero-reachability-walk.testhelper.mjs is the one copy, and the
 * readers suite's part-by-part mutation controls measure that same code.
 */
test("REACHABILITY: the producer callable is reachable from its own namespace and no other", async () => {
  const callable = producer.v5A02GateZeroEmitOutcome;
  // NON-VACUOUS FIRST: the walk finds it where it IS exported, so a walk that
  // silently answered null for everything could not pass this test.
  assert.equal(pathToValue(producer, callable, WHOLE_WALK), ".v5A02GateZeroEmitOutcome");

  const names = readdirSync(SRC).filter(name => name.endsWith(".js")).sort();
  assert.ok(names.length > 100, "every module in src must have been walked");
  const found = [];
  for (const name of names) {
    if (name === PRODUCER_FILE) continue;
    let namespace;
    try {
      namespace = await import(pathToFileURL(join(SRC, name)).href);
    } catch {
      // A module that cannot be loaded outside the Worker runtime exposes
      // nothing here either; the parser check above already covers its imports.
      continue;
    }
    const route = pathToValue(namespace, callable, WHOLE_WALK);
    if (route !== null) found.push(`${name}${route}`);
  }
  assert.deepEqual(found, [],
    "the producer's callable is reachable from a public namespace that is not its own");
});

test("SURFACE: no environment variable names a row, opens a seam or moves an answer", () => {
  // A FRESH PROCESS, because a module reads its environment while it evaluates:
  // the variables are set before the import, not after it.
  const script = `
    const { pathToFileURL } = require("node:url");
    import(pathToFileURL(process.argv[1]).href).then(async gate => {
      const { digest } = await import(pathToFileURL(process.argv[2]).href);
      process.stdout.write(JSON.stringify({ emitted: digest(await gate.emitGateZeroOutcome()) }));
    }).catch(error => { process.stderr.write(String(error)); process.exit(1); });
  `;
  const hostile = {
    ...process.env,
    CARR_GATE_ZERO_HEAD_REVISION: REVISION_ALL_SUCCEED,
    CARR_GATE_ZERO_SERVICE_KEY: "gate-zero-canary",
    CARR_GATE_ZERO_CANARY_RUN_KEY: "gate-zero-run-0001",
    CARR_GATE_ZERO_SUBJECT_MAKER: SUBJECT_MAKER_ACTOR,
    CARR_GATE_ZERO_ACCEPTANCE_HASH: `sha256:${"1".repeat(64)}`,
    CARR_GATE_ZERO_RUN_BINDING: "named",
    CARR_GATE_ZERO_ACTOR: REVIEWING_SEAT_ACTOR,
    GATE_ZERO_PASSABLE: "true",
  };
  const run = spawnSync(process.execPath,
    ["-e", script, join(SRC, GATE_FILE), join(SRC, "artifact-trust.js")],
    { encoding: "utf8", env: hostile });
  assert.equal(run.status, 0, `the child failed: ${run.stderr}`);
  const withEnvironment = JSON.parse(run.stdout).emitted;
  const clean = spawnSync(process.execPath,
    ["-e", script, join(SRC, GATE_FILE), join(SRC, "artifact-trust.js")],
    { encoding: "utf8", env: process.env });
  assert.equal(clean.status, 0, `the child failed: ${clean.stderr}`);
  assert.equal(withEnvironment, JSON.parse(clean.stdout).emitted,
    "an environment variable moved the emitted answer");
});

// ===========================================================================
// The closed vocabularies, and the one place they must agree.
// ===========================================================================

test("REASONS: every reason the producer can answer with is registered by the gate", () => {
  for (const id of producer.V5_A02_GATE_ZERO_PRODUCER_REASON_IDS)
    assert.ok(V5_A02_GATE_ZERO_REASON_IDS.includes(id),
      `${id} is a producer refusal the gate cannot express`);
  assert.equal(producer.V5_A02_GATE_ZERO_PRODUCER_REASON_IDS.length, 9);
  // And the source cites no id outside its own closed list: `reason()` raises on
  // an unregistered one, so a citation that is not here cannot be reached at all.
  const source = readFileSync(join(SRC, PRODUCER_FILE), "utf8");
  for (const cited of [...source.matchAll(/(?:refusal|reason)\("([a-z_]+)"/g)].map(m => m[1]))
    assert.ok(producer.V5_A02_GATE_ZERO_PRODUCER_REASON_IDS.includes(cited), cited);
});

test("VOCABULARY: the producer answers in held/failed/unknown and no conditional mood", () => {
  assert.deepEqual([...producer.V5_A02_GATE_ZERO_CLAUSE_STATES], ["failed", "held", "unknown"]);
  const source = readFileSync(join(SRC, PRODUCER_FILE), "utf8");
  // THE CONDITIONAL MOOD IS GONE FROM PRODUCTION, which is the point of
  // promoting the clauses at all: they are applied to readings taken from ruled
  // stores, so they answer, rather than describing what a caller's shape would
  // have meant if it had been evidence.
  for (const forbidden of ["would_", "_if_authoritative", "is_not_authority",
    "caller_supplied_shapes_not_authority"])
    assert.equal(source.includes(forbidden), false,
      `${forbidden} is a conditional-mood spelling in the production producer`);
});

test("VOCABULARY: no value the producer hands the gate carries a privileged word", async () => {
  // The standing rule's closed union, swept AS EXACT MATCH AND AS SUBSTRING over
  // every key and every string leaf — the same sweep the gate surface is held to,
  // applied here because these values travel into the gate's own answer.
  const PRIVILEGED = Object.freeze([
    "allow", "commit", "prompt", "suppress", "release", "read", "covered",
    "drafted", "proposed", "queued", "healthy", "passing", "ok", "pass",
    "satisfied", "complete", "admitted", "resumed", "attended", "verified",
    "present", "equivalent", "operational", "active", "green", "joins_exactly",
    "coverage_complete", "favorable",
  ]);
  // The four names main already answers with, which this module inherits from
  // the shape every V5 answer wears rather than inventing.
  // The names this module INHERITS rather than chooses: four field names every
  // V5 answer wears, and three identifiers r7 itself registers. Each carries the
  // union's "read" because Gate Zero is a READ-ONLY gate, and renaming any of
  // them would be renaming r7's own registry entry.
  const INHERITED = new Set([
    "request_read", "caller_evidence_admitted", "model_judgment_admitted",
    "gate_zero_step_ref", "step:gate-zero-read-only-outcome",
    "gate-zero-read-only-accepted", "oracle:gate-producer:gate-zero-read-only",
    "receipt:gate-zero-read-only-outcome",
    // AND THE ONE ADDED ON 2026-09-12, lifted out by its exact value the way
    // `status: "pass"` is rather than exempted by category. `verified_partner`
    // is identity.js's OWN derived authority class for an authenticated human
    // partner; the subject maker's class is now derived there rather than stated
    // as a constant, which is what the second review round required. Renaming it
    // here would be renaming identity.js's authorization vocabulary from inside
    // a receipt, and the word is a principal's class, never a verdict about
    // evidence — which is what the sweep exists to keep this module from
    // claiming.
    "verified_partner",
  ]);
  const found = [];
  const walk = (value, path) => {
    if (Array.isArray(value)) { value.forEach((one, at) => walk(one, `${path}[${at}]`)); return; }
    if (value !== null && typeof value === "object") {
      for (const [key, one] of Object.entries(value)) {
        if (!INHERITED.has(key)) check(key, `${path}.${key} (key)`);
        walk(one, `${path}.${key}`);
      }
      return;
    }
    if (typeof value === "string" && !INHERITED.has(value)) check(value, path);
  };
  const check = (text, where) => {
    const folded = text.toLowerCase();
    for (const word of PRIVILEGED)
      if (folded === word || folded.includes(word)) found.push(`${where}: ${word} in ${text}`);
  };
  // Both answers: the refusal src ships, and a produced outcome over rows.
  walk(await producer.v5A02GateZeroEmitOutcome(), "$");
  const produced = (await emitFrom({})).producer_answer;
  // `status: "pass"` is r7's own required value for a passing consumer-gate
  // receipt and is the one string this module may not rename. It is lifted out
  // by name rather than exempted by category.
  assert.equal(produced.receipt.status, "pass");
  walk({ ...produced, receipt: { ...produced.receipt, status: null } }, "$produced");
  assert.deepEqual(found, []);
  // NON-VACUOUS: the sweep catches what it exists to catch.
  check("ruled_store_readings", "$control");
  assert.equal(found.length, 1, "the sweep did not catch a word it must catch");
});
