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

const TEST_DIR = fileURLToPath(new URL("./", import.meta.url));
const SRC = fileURLToPath(new URL("../src/", import.meta.url));
const REPO = fileURLToPath(new URL("../../", import.meta.url));
const PRODUCER_FILE = "gate-zero-producer.v5.js";
const REGISTRATION_FILE = "gate-zero-producer-registration.v5.js";
const RULINGS_FILE = "gate-zero-seam-rulings.v5.js";
const STORES_FILE = "gate-zero-seam-stores.v5.js";
const GATE_FILE = "gate-zero-assurance.v5.js";
const IDENTITY_FILE = "identity.js";
const TOOLS_FILE = "tools.js";
/**
 * THE VERB EVERY CASE IS DISPATCHED THROUGH. Any registered read verb would do —
 * what is under test is `executeRegisteredTool`, not this verb — and `loop-board`
 * is chosen because its handler reaches its database client immediately and
 * finishes cleanly over no rows, so the stub client below is a one-line seam
 * into the middle of a real dispatched call.
 */
const DISPATCHED_VERB = "loop-board";
const FIXTURE_FILE = "gate-zero-producer-stores.v5.fixture.mjs";

/** The sealed fixture set the receipt's fixture_set_digest covers, by path. */
const SEALED_FIXTURES = Object.freeze([
  "gate-zero-producer-stores.v5.fixture.mjs",
  "gate-zero-seam-stores.v5.fixture.mjs",
  "gate-zero-seam-stores.v5.receipt-fixture.mjs",
]);
const ENVIRONMENT_MANIFEST = ["ops", "config", "environments.json"];

/**
 * THE RECORDED AUTHENTICATED CONTEXT. A bearer and the one-entry map shape the
 * Worker's REVIEW_TOKENS secret holds, which is everything index.js's review
 * door reads — recorded here so the actor can be MINTED by the real door rather
 * than written out as a literal. The token is a fixture string: it authenticates
 * against the map beside it and against nothing else.
 */
const REVIEWING_SEAT_ACTOR = "codex-reviewer";
const RECORDED_REVIEW_TOKEN = "gate-zero-recorded-review-bearer-2026-09-12";
const RECORDED_REVIEW_TOKENS = JSON.stringify({ [REVIEWING_SEAT_ACTOR]: RECORDED_REVIEW_TOKEN });
const CORRELATION_ID = "3f2a6c18-9b4d-4e7a-8c11-5d0e2f7a6b93";

/**
 * A LEGACY MAP-TAKING DOOR'S RECORDED SECRET, and the OAuth grant door's
 * recorded witness. Both are here for the same reason the review map is: the
 * fourth correction round closed the legacy doors WITHOUT an exception, so this
 * suite has to be able to show each of them branding for the server's own bytes
 * and refusing to brand for a caller's. `codex` is an ordinary DISPLAY actor and
 * the narrowest one that exercises the agent door.
 */
const RECORDED_AGENT_ACTOR = "codex";
const RECORDED_AGENT_TOKEN = "gate-zero-recorded-agent-bearer-2026-09-12";
const RECORDED_AGENT_TOKENS = JSON.stringify({ [RECORDED_AGENT_ACTOR]: RECORDED_AGENT_TOKEN });
const RECORDED_GRANT_WITNESS = "gate-zero-recorded-oauth-client-secret-2026-09-12";

/**
 * THE SERVER'S OWN SECRET SOURCE, AND THERE IS NO OTHER ONE ANY MORE.
 *
 * Amendment 8's fourth correction deleted `sealServerReviewTokens`: identity.js
 * reads its credentials ONCE, at module initialisation, out of the environment
 * the SERVER PROCESS was started with — `process.env`, which is what wrangler
 * populates this Worker's secrets into (nodejs_compat, 2026-07-01 compatibility
 * date). There is no function to install a map, so there is no first-caller race
 * to win, which is exactly how the reviewer's probe got in twice.
 *
 * SO THE SUITE BECOMES THE SERVER, the only way a suite now can: it writes the
 * recorded secrets into its own process environment, HERE, before any staged
 * tree is imported. Every `moduleOfTree(target, IDENTITY_FILE)` below is a fresh
 * module instance whose initialisation reads exactly these values, the way a
 * deployed Worker's does. Nothing in src is steered by an environment variable —
 * these are credentials, not addresses, and no staged tree, reader or store is
 * selected by one.
 *
 * The statically imported `../src/identity.js` instance was initialised before
 * this line ran and therefore holds NO credentials at all. That is deliberate
 * and it is load-bearing: every case that needs an authenticated actor takes it
 * from a staged module, so a case cannot accidentally pass because some other
 * import happened to have booted the door first.
 */
process.env.REVIEW_TOKENS = RECORDED_REVIEW_TOKENS;
process.env.AGENT_TOKENS = RECORDED_AGENT_TOKENS;
process.env.GOOGLE_CLIENT_SECRET = RECORDED_GRANT_WITNESS;

/**
 * The reviewer seat, authenticated by the STAGED identity.js.
 *
 * ONE STEP, AND IT IS A BEARER. There is no seal, no install and no map
 * argument: the staged module read REVIEW_TOKENS out of the process environment
 * written above when it initialised, so this is precisely the call index.js
 * makes on a /mcp request and precisely the call a caller cannot improve on.
 *
 * The correlation id is then written ON TO the authenticated object, the way
 * mcp.js now decorates it. It is NOT a spread: the brand is object identity, so
 * a copy of this actor authenticates as nobody — which is the whole design, and
 * the control three tests down proves it. identity.js derives `review_agent`
 * for the seat; nothing here says so.
 */
function recordedReviewerActor(identity, correlationId = CORRELATION_ID) {
  const actor = identity.authenticatedIdentity
    .reviewActorForToken(`Bearer ${RECORDED_REVIEW_TOKEN}`);
  assert.notEqual(actor, null, "the recorded review context authenticated no actor");
  return Object.assign(actor, { correlation_id: correlationId });
}

/**
 * A verified partner, from the same file's OAuth grant path. NOT this oracle's
 * class.
 *
 * `actorFromProps` is module-private under the fourth correction — props are an
 * ordinary object, so an exported builder was a brander taking caller bytes.
 * What the server calls is `connectionForGrant`, and the third argument is the
 * WITNESS: a credential byte string identity.js read from the server's own
 * environment. index.js and mcp.js pass `env.GOOGLE_CLIENT_SECRET`; this passes
 * the recorded value written into that environment above.
 */
function recordedPartnerActor(identity, correlationId = CORRELATION_ID) {
  const actor = identity.authenticatedIdentity.connectionForGrant(
    identity.propsForSlug("joe", { via: "oauth-google" }), null, RECORDED_GRANT_WITNESS);
  assert.notEqual(actor, null, "the recorded partner context authenticated no actor");
  return Object.assign(actor, { correlation_id: correlationId });
}

/**
 * THE MUTATION CONTROL FOR THE WHOLE IDENTITY DESIGN: an object with every field
 * the real reviewer seat carries, written out by hand. It is exactly what the
 * first correction round's tests passed to the exported setter, and exactly what
 * the review then used to obtain `authority_class: "review_agent"` from a
 * literal. identity.js did not mint it, so it derives nothing.
 */
function fabricatedReviewerActor() {
  return { slug: REVIEWING_SEAT_ACTOR, display: `Reviewer (${REVIEWING_SEAT_ACTOR})`,
    human: false, review: true, via: "review-token", client_id: null,
    correlation_id: CORRELATION_ID };
}

/** The committers identity.js's frozen registry maps, and the actor they map to. */
const MAKER_EMAIL = "joe.bookout.carr.us@gmail.com";
/** The address GitHub's own merge path writes as the committer of every merged
 *  head — the form this PR's own HEAD carries, and the one the registry could
 *  not name until amendment 8. */
const MAKER_NOREPLY_EMAIL = "64207374+jbookout@users.noreply.github.com";
const SUBJECT_MAKER_ACTOR = "joe";

const staged = [];
after(() => {
  for (const base of staged) rmSync(base, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// THE STAGING. It builds a whole candidate repository, because the producer now
// derives its run binding from one: the revision and the committer come out of
// .git, the candidate digest out of the source tree's bytes, the environment
// digest out of ops/config/environments.json and the fixture-set digest out of
// the sealed fixture bytes. Every edit is asserted to have matched exactly once
// before it is made — a staging whose anchor silently stopped matching would
// leave the case proving nothing, which is the failure mode the seam suites
// already learned once.
// ---------------------------------------------------------------------------

const STAFFED_SEAT_LINE = '  holder_ref: "seat:codex-reviewer:gpt-5.6-sol",\n';
const UNSTAFFED_SEAT_LINE = "  holder_ref: null,\n";
const RULED_DECISION_LINES = Object.freeze([
  '    decision_id: "16c7cdfb-b675-4b6a-bbff-4bbdab46baf8",\n',
  '    decision_id: "f7c486d6-5bee-4c4c-a76f-c0f162f66db8",\n',
  '    decision_id: "87e9e11e-64b2-49b3-a6aa-4901c24eaa91",\n',
]);
const NULL_DECISION_LINE = "    decision_id: null,\n";

/** The two lines the fixture STORE carries so a broken world is a store edit. */
const LEDGER_CANARY_LINE = "const LEDGER_CANARY = CANARY_JOINING;\n";
const PREDECESSOR_WORLD_LINE = 'const PREDECESSOR_WORLD = "clean";\n';
/** The producer's own admitted-class line, edited by exactly one control. */
const AUTHORITY_CLASSES_LINE =
  'const PRODUCER_AUTHORITY_CLASSES = Object.freeze(["review_agent"]);\n';

// ---------------------------------------------------------------------------
// THE STAGED OBJECT STORE. Git's own on-disk format, written here rather than
// shelled out for, because a case has to be able to CHOOSE which revision it is
// standing on and `git commit` hands out the hash it feels like.
//
// The trees are content-addressed exactly as git addresses them — id = sha1 of
// `tree <len>\0<entries>` — so the blob ids the producer reads out of HEAD's
// tree are the real sealing of the staged bytes, and a case that edits one
// staged file moves that file's sealed id the way a real commit would. Blob
// OBJECTS are never written because nothing ever inflates one: the producer
// reads ids from the tree and bytes from the working tree, which is the whole
// point of it holding both.
//
// The commit object is the one exception, filed under the case's chosen revision
// rather than under its own hash. The producer does not re-hash what it inflates
// — see its own note — and this is the seam that exception buys.
// ---------------------------------------------------------------------------

const objectId = (kind, body) => createHash("sha1")
  .update(Buffer.concat([Buffer.from(`${kind} ${body.length}\0`), body])).digest("hex");

function writeLooseObject(objects, id, kind, body) {
  mkdirSync(join(objects, id.slice(0, 2)), { recursive: true });
  writeFileSync(join(objects, id.slice(0, 2), id.slice(2)),
    deflateSync(Buffer.concat([Buffer.from(`${kind} ${body.length}\0`), body])));
}

/** Every tree object under one directory, written; answers the directory's id.
 *  `seen` collects each directory's id, because the candidate digest stands on
 *  the id of the CANDIDATE's tree rather than the repository root's. */
function writeTreeObjects(objects, directory, seen) {
  const entries = [];
  for (const name of readdirSync(directory).sort()) {
    if (name === ".git") continue;
    const full = join(directory, name);
    entries.push(statSync(full).isDirectory()
      ? { mode: "40000", name, id: writeTreeObjects(objects, full, seen) }
      : { mode: "100644", name, id: objectId("blob", readFileSync(full)) });
  }
  const body = Buffer.concat(entries.map(entry => Buffer.concat([
    Buffer.from(`${entry.mode} ${entry.name}\0`), Buffer.from(entry.id, "hex")])));
  const id = objectId("tree", body);
  writeLooseObject(objects, id, "tree", body);
  seen.set(directory, id);
  return id;
}

/**
 * Seal the staged tree at `revision`, with `maker` as its committer. Answers the
 * id of the CANDIDATE's own tree under that revision, which the digest
 * recomputation below needs.
 */
function sealCandidate(base, revision, maker) {
  const objects = join(base, ".git", "objects");
  const seen = new Map();
  const tree = writeTreeObjects(objects, base, seen);
  const body = Buffer.from(
    `tree ${tree}\n` +
    `author A Maker <${maker}> 1757000000 +0000\n` +
    `committer A Maker <${maker}> 1757000000 +0000\n` +
    "\nthe staged candidate\n");
  writeLooseObject(objects, revision, "commit", body);
  writeFileSync(join(base, ".git", "HEAD"), `${revision}\n`);
  return seen.get(join(base, "mcp-server", "src"));
}

function editOnce(path, anchor, replacement, what) {
  const source = readFileSync(path, "utf8");
  assert.equal(source.split(anchor).length - 1, 1,
    `the staging anchor no longer matches ${what}`);
  writeFileSync(path, source.replace(anchor, replacement));
}

/**
 * A candidate repository: .git, a copy of src, the sealed fixtures and the
 * environment manifest. `revision` and `maker` are what a RUN differs by; the
 * rest are the faults a case injects.
 */
function stageTree({
  revision = REVISION_ALL_SUCCEED, maker = MAKER_EMAIL, staffedSeat = true,
  withdrawnCards = [], ledgerCanary = null, predecessorWorld = null,
  substituteStore = true, environmentEdit = null, candidateEdit = null,
  fixtureEdit = null, admitPartnerClass = false, git = true,
  withoutEnvironmentManifest = false, postSealEdit = null,
} = {}) {
  const cache = fileURLToPath(new URL("../node_modules/.cache/", import.meta.url));
  mkdirSync(cache, { recursive: true });
  const base = mkdtempSync(join(cache, "gate-zero-producer-"));
  staged.push(base);
  const target = join(base, "mcp-server", "src");
  mkdirSync(target, { recursive: true });
  cpSync(SRC, target, { recursive: true });
  mkdirSync(join(base, ".git"), { recursive: true });
  // THE TWO FILES src IMPORTS FROM OUTSIDE ITSELF. The staged tools.js is a real
  // module graph and will not load without them; they are copied rather than
  // stubbed so the dispatch under test is the dispatch that ships.
  cpSync(join(REPO, "mcp-server", "continuity-reference-manifest.mjs"),
    join(base, "mcp-server", "continuity-reference-manifest.mjs"));
  cpSync(join(REPO, "mcp-server", "assets"), join(base, "mcp-server", "assets"),
    { recursive: true });


  // THE TWO SEALED ARTIFACTS, copied as bytes. A case that mutates one mutates
  // the bytes, which is the whole point of asserting the digest moves.
  const stagedTest = join(base, "mcp-server", "test");
  mkdirSync(stagedTest, { recursive: true });
  for (const name of SEALED_FIXTURES) cpSync(join(TEST_DIR, name), join(stagedTest, name));
  if (fixtureEdit !== null) {
    const path = join(stagedTest, SEALED_FIXTURES[1]);
    writeFileSync(path, `${readFileSync(path, "utf8")}\n// ${fixtureEdit}\n`);
  }
  mkdirSync(join(base, "ops", "config"), { recursive: true });
  cpSync(join(REPO, ...ENVIRONMENT_MANIFEST), join(base, ...ENVIRONMENT_MANIFEST));
  if (environmentEdit !== null) {
    const path = join(base, ...ENVIRONMENT_MANIFEST);
    const manifest = JSON.parse(readFileSync(path, "utf8"));
    manifest.version = environmentEdit;
    writeFileSync(path, JSON.stringify(manifest, null, 2));
  }

  if (!staffedSeat)
    editOnce(join(target, REGISTRATION_FILE), STAFFED_SEAT_LINE, UNSTAFFED_SEAT_LINE,
      "the seat declaration");

  if (withdrawnCards.length > 0) {
    const path = join(target, RULINGS_FILE);
    let source = readFileSync(path, "utf8");
    RULED_DECISION_LINES.forEach((anchor, index) => {
      assert.equal(source.split(anchor).length - 1, 1,
        `the staging anchor no longer matches card ${11 + index}'s ruling line`);
      if (withdrawnCards.includes(index)) source = source.replace(anchor, NULL_DECISION_LINE);
    });
    writeFileSync(path, source);
  }

  if (admitPartnerClass)
    editOnce(join(target, PRODUCER_FILE), AUTHORITY_CLASSES_LINE,
      'const PRODUCER_AUTHORITY_CLASSES = Object.freeze(["review_agent", "verified_partner"]);\n',
      "the producer's admitted authority classes");

  if (candidateEdit !== null) {
    const path = join(target, "global-boundaries.v5.js");
    writeFileSync(path, `${readFileSync(path, "utf8")}\n// ${candidateEdit}\n`);
  }

  if (substituteStore) {
    const store = join(target, STORES_FILE);
    cpSync(join(TEST_DIR, FIXTURE_FILE), store);
    // THE STORE IS WHERE A BROKEN WORLD IS CHOSEN NOW. The producer derives its
    // addresses, so a test can no longer steer by writing one — it edits the
    // row the store holds, which is the honest place for a fault to live.
    if (ledgerCanary !== null)
      editOnce(store, LEDGER_CANARY_LINE, `const LEDGER_CANARY = ${JSON.stringify(ledgerCanary)};\n`,
        "the fixture store's canary line");
    if (predecessorWorld !== null)
      editOnce(store, PREDECESSOR_WORLD_LINE,
        `const PREDECESSOR_WORLD = ${JSON.stringify(predecessorWorld)};\n`,
        "the fixture store's predecessor world line");
  }

  if (withoutEnvironmentManifest) rmSync(join(base, ...ENVIRONMENT_MANIFEST));

  // SEALED LAST, so every edit a case asked for is inside the revision HEAD
  // names — which is what makes "mutating one tracked file moves the candidate
  // digest" a statement about this revision rather than about a dirty checkout.
  const treeId = git === true ? sealCandidate(base, revision, maker) : null;

  // AFTER the seal: one tracked candidate file mutated in the working tree while
  // HEAD's tree still seals the old bytes. The sealed half of the manifest does
  // not move and the observed half does — which is the mutation control for
  // "the candidate digest is bound to HEAD's manifest AND to the bytes".
  if (postSealEdit !== null) {
    const path = join(target, "global-boundaries.v5.js");
    writeFileSync(path, `${readFileSync(path, "utf8")}\n// ${postSealEdit}\n`);
  }
  return { target, treeId };
}

const moduleOfTree = (target, file) => import(pathToFileURL(join(target, file)).href);

/**
 * RUN `fn` INSIDE A REAL DISPATCHED VERB CALL in a staged tree.
 *
 * `executeRegisteredTool` is the one dispatch every verb funnels through and the
 * only place an authenticated call is established. It is reached HERE through
 * the staged tools.js, so the async context the staged producer reads is the one
 * the staged identity.js entered for this actor — the same module instance, not
 * a value carried across a boundary.
 *
 * THE STUB CLIENT IS THE SEAM. Step A emits a value and is not bound to a verb
 * of its own yet (that is Step B), so the only way to be running inside a
 * dispatched call is to be called by one. The verb's database client is handed
 * in by the caller, so `fn` runs on its first query — in the middle of a real
 * handler, inside the real dispatch, under the real context — and the verb then
 * finishes over no rows.
 */
async function inDispatchedVerb(target, actor, fn) {
  const tools = await moduleOfTree(target, TOOLS_FILE);
  let answered;
  let ran = false;
  const client = { query: async () => {
    if (!ran) { ran = true; answered = await fn(); }
    return { rows: [] };
  } };
  await tools.executeRegisteredTool(client, actor, DISPATCHED_VERB, {});
  assert.equal(ran, true, "the dispatched verb never reached its client");
  return answered;
}

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
  // FROM A STAGED MODULE, because the statically imported identity.js was
  // initialised before this file wrote the recorded secrets into the process
  // environment and therefore holds no credentials at all. Every authenticated
  // actor in this suite comes from a module that booted the way the Worker does.
  const { target: mintedIn } = stageTree({});
  const minted = recordedReviewerActor(await moduleOfTree(mintedIn, IDENTITY_FILE));
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
    "reviewActorForToken", "dispatchAuthenticatedCall", "authenticatedCallReceiptIdentity",
    "actorFromProps", "sealServerReviewTokens"])
    assert.equal(Object.hasOwn(identity, gone), false, `${gone} is still exported`);
  // AND THE INSTALLER IS NOT ON THE NEW SURFACE EITHER, under any name: the
  // fourth round's mutation control is that the probe cannot name a function to
  // call, so it is asserted on both surfaces rather than on the module alone.
  for (const gone of ["sealServerReviewTokens", "actorFromProps",
    "dispatchAuthenticatedCall", "runInAuthenticatedCall"])
    assert.equal(Object.hasOwn(identity.authenticatedIdentity, gone), false,
      `${gone} is still on the authenticated-identity surface`);
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

  /** Did this object come out of the door BRANDED? Asked the way the dispatch
   *  asks: an unbranded object derives no identity and therefore no receipt. */
  const derivesIdentity = value => door.dispatchFor(value)(() => door.receiptIdentity()) !== null;

  // THE INSTALLER IS GONE, WITH NOTHING IN ITS PLACE. This is the fourth round's
  // mutation control stated as the review stated it: the probe cannot name a
  // function to call. One-shot state settled which caller was FIRST; it never
  // said which caller was the SERVER, and the probe simply arrived first.
  assert.equal(door.sealServerReviewTokens, undefined);
  assert.equal(Object.keys(door).some(name => /seal|install|bind|set/i.test(name)), false,
    "the new surface grew something that reads like an installer");

  // THE DOOR TAKES A BEARER AND NOTHING ELSE, so the call the reviewer made is
  // not one that can be written any more: a second argument is not read.
  assert.equal(door.reviewActorForToken.length, 1);
  assert.equal(door.reviewActorForToken(`Bearer ${CALLERS_OWN}`, CALLERS_MAP), null);
  // NON-VACUOUS: the map the SERVER'S OWN ENVIRONMENT holds still authenticates
  // its own bearer, and that actor is branded.
  const server = door.reviewActorForToken(`Bearer ${RECORDED_REVIEW_TOKEN}`);
  assert.notEqual(server, null);
  assert.equal(derivesIdentity(Object.assign(server, { correlation_id: CORRELATION_ID })), true);

  // AND THE LEGACY MAP-TAKING DOORS ARE CLOSED WITH NO EXCEPTION — the half the
  // third round carved out and the fourth was told to close. They still TAKE a
  // map, because the server has always called them that way, but branding no
  // longer follows from a match: it follows from the bytes matched against being
  // bytes identity.js read from the server's environment at initialisation.
  const callersAgentMap = JSON.stringify({ [RECORDED_AGENT_ACTOR]: CALLERS_OWN });
  const callersAgent = identity.agentActorForToken(`Bearer ${CALLERS_OWN}`, callersAgentMap);
  assert.notEqual(callersAgent, null, "the legacy door stopped returning its actor");
  assert.equal(derivesIdentity(Object.assign(callersAgent, { correlation_id: CORRELATION_ID })),
    false, "a caller's own token map branded an actor through the legacy agent door");
  // NON-VACUOUS, and it is the same door, the same bearer shape and the same
  // slug — only the PROVENANCE of the map differs.
  const serversAgent = identity.agentActorForToken(
    `Bearer ${RECORDED_AGENT_TOKEN}`, RECORDED_AGENT_TOKENS);
  assert.notEqual(serversAgent, null);
  assert.equal(derivesIdentity(Object.assign(serversAgent, { correlation_id: CORRELATION_ID })),
    true, "the server's own token map stopped branding through the legacy agent door");

  // AND THE GRANT DOOR IS THE SAME STORY WITH A WITNESS instead of a map: props
  // a caller wrote get the actor they always got, and no brand.
  const callersGrant = door.connectionForGrant(
    identity.propsForSlug("joe", { via: "oauth-google" }), null, "a-witness-the-caller-chose");
  assert.notEqual(callersGrant, null);
  assert.equal(derivesIdentity(Object.assign(callersGrant, { correlation_id: CORRELATION_ID })),
    false, "a caller-chosen witness branded a partner actor");

  // AND THE CALLER'S BEARER IS REFUSED THROUGH THE REAL DISPATCH TOO, which is
  // where the reviewer's probe ended: no branded actor, so no identity, so no
  // receipt.
  const emitted = await emitFrom({}, staged => {
    // Everything the probe could reach, tried in the staged module: there is no
    // installer, the caller's bearer authenticates nothing, and the caller's own
    // map brands nothing through the door that still accepts one.
    assert.equal(staged.authenticatedIdentity.sealServerReviewTokens, undefined);
    assert.equal(staged.authenticatedIdentity.reviewActorForToken(`Bearer ${CALLERS_OWN}`), null,
      "a caller-chosen bearer authenticated against the server's map");
    const smuggled = staged.agentActorForToken(`Bearer ${CALLERS_OWN}`,
      JSON.stringify({ [REVIEWING_SEAT_ACTOR]: CALLERS_OWN }));
    assert.equal(smuggled, null, "an unregistered slug came back through the agent door");
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

/**
 * THE HOSTILE VALUES. Everything a caller can write down, and nothing a server
 * holds: no recorded token, no recorded witness, no minted actor. Amendment 8's
 * claim is that NO export of identity.js turns any combination of these into a
 * branded actor or into an entered identity context, so the enumeration below
 * feeds every export every 1-, 2- and 3-tuple drawn from this set.
 */
function hostileValues(probe) {
  return [
    null,
    "Bearer a-bearer-the-caller-chose",
    JSON.stringify({ [REVIEWING_SEAT_ACTOR]: "a-bearer-the-caller-chose" }),
    JSON.stringify({ [RECORDED_AGENT_ACTOR]: "a-bearer-the-caller-chose" }),
    fabricatedReviewerActor(),
    { slug: "joe", human: true, via: "oauth-google", correlation_id: CORRELATION_ID },
    probe,
  ];
}

test("IDENTITY: no export of identity.js brands an actor or enters a context from caller bytes",
  async () => {
    const { target } = stageTree({});
    const identity = await moduleOfTree(target, IDENTITY_FILE);
    const door = identity.authenticatedIdentity;

    // THE SURFACE IS ENUMERATED OFF THE MODULE NAMESPACE, not off a list and not
    // off `authenticatedIdentity` alone — which was finding 4 of the third
    // re-review: the old loop walked only the members already inside the new
    // namespace, so a NEW top-level callable export bypassed it entirely while
    // the test next to it asserted the namespace was the whole new surface. A
    // callable added anywhere in identity.js now arrives in this loop by
    // existing, at any depth of one frozen namespace object.
    const surface = [];
    for (const [name, value] of Object.entries(identity)) {
      if (typeof value === "function") surface.push([name, value]);
      else if (value !== null && typeof value === "object")
        for (const [inner, member] of Object.entries(value))
          if (typeof member === "function") surface.push([`${name}.${inner}`, member]);
    }
    assert.ok(surface.length >= 20,
      `identity.js's callable surface enumerated as ${surface.length}, which is too few to be all of it`);
    for (const required of ["agentActorForToken", "continuityActorForTokenMaps",
      "hermesActorForToken", "hermesCosActorForToken", "propsForSlug",
      "authenticatedIdentity.reviewActorForToken", "authenticatedIdentity.connectionForGrant",
      "authenticatedIdentity.dispatchFor", "authenticatedIdentity.receiptIdentity",
      "authenticatedIdentity.buildContext", "authenticatedIdentity.committerIdentity"])
      assert.ok(surface.some(([name]) => name === required),
        `${required} was not reached by the enumeration`);

    /**
     * What a value derives through the real dispatcher. A branded actor answers
     * a three-field identity here; everything else answers null.
     *
     * THE CORRELATION ID IS WRITTEN ON FIRST, IN PLACE, exactly as mcp.js
     * decorates an authenticated actor — and without it this helper would answer
     * null for EVERY object and the whole enumeration would pass vacuously,
     * since a derived identity needs the server's per-call id. Written in place
     * rather than spread, because the brand is object identity.
     */
    const derives = value => {
      if (value !== null && typeof value === "object" && !Object.isFrozen(value)
          && value.correlation_id === undefined) {
        try { value.correlation_id = CORRELATION_ID; } catch { /* frozen enough */ }
      }
      try { return door.dispatchFor(value)(() => door.receiptIdentity()); }
      catch { return null; }
    };

    // A HOSTILE ARGUMENT THAT LOOKS BACK. If any export runs a caller's function
    // inside an entered context, this records what that context held.
    const seen = [];
    const probe = (...args) => { seen.push(door.receiptIdentity()); return args[0] ?? null; };
    const hostile = hostileValues(probe);

    const produced = [];
    for (const [name, callable] of surface)
      for (const a of hostile) for (const b of hostile) for (const c of hostile) {
        let answered;
        try { answered = callable(a, b, c); } catch { continue; }
        if (answered === null || answered === undefined) continue;
        produced.push([name, answered]);
        // A callable ANSWER is followed one step further: `dispatchFor` hands
        // back a closure, and a closure that entered an identity would be the
        // context entry amendment 8 says this file does not export.
        if (typeof answered === "function") {
          try { answered(probe); } catch { /* a refusal is an answer */ }
        }
      }

    assert.ok(produced.length > 0, "no export answered anything, so this proved nothing");
    for (const [name, value] of produced)
      assert.equal(derives(value), null,
        `${name} turned caller bytes into a branded actor`);
    for (const held of seen)
      assert.equal(held, null, "an export ran a caller's function inside an identity context");

    // NON-VACUOUS, against the same `derives`: the server's own credentials —
    // which appear nowhere in the hostile set — still brand, so what refuses
    // above is provenance and not the helper being broken.
    assert.deepEqual(derives(recordedReviewerActor(identity)), {
      actor_id: REVIEWING_SEAT_ACTOR,
      session_ref: `session:${CORRELATION_ID}`,
      authority_class: "review_agent",
    });
    assert.deepEqual(derives(recordedPartnerActor(identity)), {
      actor_id: "joe",
      session_ref: `session:${CORRELATION_ID}`,
      authority_class: "verified_partner",
    });
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
  assert.deepEqual(added.filter(name => typeof surface[name] !== "function"), []);

  // AND THE SURFACE IS ALL OF IT — asserted DYNAMICALLY against the file's own
  // exports, which is finding 4 of the third re-review. The old line only said
  // that every member of the namespace was callable; it said nothing about a new
  // top-level callable sitting NEXT to the namespace, which would have slipped
  // past the whole enumeration while the comment claimed otherwise. identity.js's
  // top-level callables are exactly the pre-amendment doors listed here, so any
  // callable added to this file arrives either inside the namespace above (where
  // it is shape-checked) or in this diff (where it is red).
  assert.deepEqual(
    Object.keys(identity).filter(name => typeof identity[name] === "function").sort(),
    ["agentActorForToken", "agentSlugForClient", "authorizationClassForActor",
     "continuityActorForTokenMaps", "hermesActorForToken", "hermesActorForTokenMaps",
     "hermesCosActorForToken", "isKnownActor", "isKnownPartner",
     "organizationTenantForActor", "permittedActionOwnerSlugs", "personalScopeForActor",
     "propsForSlug", "slugForEmail", "verifiedAgentSlugForClient"],
    "identity.js grew or lost a top-level callable export");

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

/**
 * THE REACHABILITY GUARD, in the shape PR 1004's amendment 6 requires: a closed
 * set, parsed rather than grepped, and asserted to be exactly the modules that
 * may name this file. A producer that some other module could import and drive
 * is a producer with a second caller, and a second caller is an argument wearing
 * an import's clothes.
 */
function moduleImports(directory) {
  const script = `
    const { readdirSync, readFileSync, statSync } = require("node:fs");
    const { join } = require("node:path");
    const vm = require("node:vm");
    const out = {};
    const walk = (dir, prefix) => {
      for (const name of readdirSync(dir).sort()) {
        const full = join(dir, name);
        if (statSync(full).isDirectory()) { walk(full, prefix + name + "/"); continue; }
        if (!name.endsWith(".js")) continue;
        const source = readFileSync(full, "utf8");
        out[prefix + name] =
          new vm.SourceTextModule(source, { identifier: name }).dependencySpecifiers;
      }
    };
    walk(process.argv[1], "");
    process.stdout.write(JSON.stringify(out));
  `;
  const run = spawnSync(process.execPath, ["--experimental-vm-modules", "-e", script, directory],
    { encoding: "utf8" });
  assert.equal(run.status, 0, `the module parser failed: ${run.stderr}`);
  return JSON.parse(run.stdout);
}

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
