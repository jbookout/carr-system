// THE CANDIDATE-TREE STAGING, ONE COPY, SHARED (2026-09-13, PR 1014 correction
// rounds; merged with PR 1013's third correction).
//
// WHY IT IS HERE. The producer takes no argument, so the only honest way to ask
// what it answers under a different world is to stage a whole candidate
// repository and read what the module then says. Step B's write verb owes the
// SAME staging for the opposite reason: its test must drive the registered verb
// through the real dispatch path over a receipt the real producer really
// emitted, never a hand-built object, and the only place such a receipt exists
// is inside one of these trees.
//
// IT IS LIFTED, NOT RETYPED — the same discipline, and for the same reason, as
// gate-zero-reachability-walk.testhelper.mjs beside it: a retyped harness is a
// second implementation that passes because it was written from the same
// misunderstanding as the code it checks. Both suites import THIS file, so a
// correction to the staging corrects both proofs at once. The bytes below are
// Step A's own staging, moved rather than rewritten.
//
// WHAT THE STAGED TREE HOLDS:
//
//   * A REAL OBJECT STORE. `.git/HEAD` names the case's revision and
//     `.git/objects` holds the loose commit and tree objects for it, written
//     here: the trees are content-addressed exactly as git writes them, so the
//     path set and the blob ids the producer reads out of HEAD are the real
//     sealing of the staged bytes. Only the COMMIT object is filed under the
//     fixture's chosen revision rather than under its own hash. The committer
//     line is where the subject maker comes from, and no reflog is written.
//   * mcp-server/src — a copy, with the store module replaced by the fixture,
//     and with card 9's seat declaration or the three `decision_id:` lines
//     edited when a case asks;
//   * mcp-server/test — the sealed fixture bytes the fixture-set digest covers;
//   * ops/config/environments.json — the environment manifest its digest covers.
//
// IT DECIDES NOTHING. Every function here copies bytes, edits a line it has
// asserted matches exactly once, or imports a module out of the tree it built.
// No assertion about Gate Zero lives in this file.

import assert from "node:assert/strict";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync,
  writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { deflateSync } from "node:zlib";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { spawnSync } from "node:child_process";

import {
  REVISION_ALL_SUCCEED,
} from "./gate-zero-producer-stores.v5.fixture.mjs";

export const TEST_DIR = fileURLToPath(new URL("./", import.meta.url));
export const SRC = fileURLToPath(new URL("../src/", import.meta.url));
export const REPO = fileURLToPath(new URL("../../", import.meta.url));
export const PRODUCER_FILE = "gate-zero-producer.v5.js";
export const REGISTRATION_FILE = "gate-zero-producer-registration.v5.js";
export const RULINGS_FILE = "gate-zero-seam-rulings.v5.js";
export const STORES_FILE = "gate-zero-seam-stores.v5.js";
export const GATE_FILE = "gate-zero-assurance.v5.js";
export const IDENTITY_FILE = "identity.js";
export const TOOLS_FILE = "tools.js";
/**
 * THE VERB EVERY CASE IS DISPATCHED THROUGH. Any registered read verb would do —
 * what is under test is `executeRegisteredTool`, not this verb — and `loop-board`
 * is chosen because its handler reaches its database client immediately and
 * finishes cleanly over no rows, so the stub client below is a one-line seam
 * into the middle of a real dispatched call.
 */
export const DISPATCHED_VERB = "loop-board";
export const FIXTURE_FILE = "gate-zero-producer-stores.v5.fixture.mjs";

/** The sealed fixture set the receipt's fixture_set_digest covers, by path. */
export const SEALED_FIXTURES = Object.freeze([
  "gate-zero-producer-stores.v5.fixture.mjs",
  "gate-zero-seam-stores.v5.fixture.mjs",
  "gate-zero-seam-stores.v5.receipt-fixture.mjs",
]);
export const ENVIRONMENT_MANIFEST = ["ops", "config", "environments.json"];

/**
 * THE RECORDED AUTHENTICATED CONTEXT. A bearer and the one-entry map shape the
 * Worker's REVIEW_TOKENS secret holds, which is everything index.js's review
 * door reads — recorded here so the actor can be MINTED by the real door rather
 * than written out as a literal. The token is a fixture string: it authenticates
 * against the map beside it and against nothing else.
 */
export const REVIEWING_SEAT_ACTOR = "codex-reviewer";
export const RECORDED_REVIEW_TOKEN = "gate-zero-recorded-review-bearer-2026-09-12";
export const RECORDED_REVIEW_TOKENS = JSON.stringify({ [REVIEWING_SEAT_ACTOR]: RECORDED_REVIEW_TOKEN });
export const CORRELATION_ID = "3f2a6c18-9b4d-4e7a-8c11-5d0e2f7a6b93";

/**
 * The reviewer seat, authenticated by the STAGED identity.js.
 *
 * TWO STEPS, BOTH THE SERVER'S, because amendment 8 moved the token map off the
 * caller's side of the door. First the server SEALS its map into the module —
 * one-shot, exactly as index.js does on the first /mcp request. Then the door
 * takes a BEARER and nothing else: there is no parameter left for a caller to
 * pair a bearer of its choosing with a map of its choosing.
 *
 * The correlation id is then written ON TO the authenticated object, the way
 * mcp.js now decorates it. It is NOT a spread: the brand is object identity, so
 * a copy of this actor authenticates as nobody — which is the whole design, and
 * the control three tests down proves it. identity.js derives `review_agent`
 * for the seat; nothing here says so.
 */
export
function recordedReviewerActor(identity, correlationId = CORRELATION_ID) {
  identity.authenticatedIdentity.sealServerReviewTokens(RECORDED_REVIEW_TOKENS);
  const actor = identity.authenticatedIdentity
    .reviewActorForToken(`Bearer ${RECORDED_REVIEW_TOKEN}`);
  assert.notEqual(actor, null, "the recorded review context authenticated no actor");
  return Object.assign(actor, { correlation_id: correlationId });
}

/** A verified partner, from the same file's OAuth-props path. NOT this oracle's class. */
export
function recordedPartnerActor(identity, correlationId = CORRELATION_ID) {
  const actor = identity.actorFromProps(identity.propsForSlug("joe", { via: "oauth-google" }));
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
export
function fabricatedReviewerActor() {
  return { slug: REVIEWING_SEAT_ACTOR, display: `Reviewer (${REVIEWING_SEAT_ACTOR})`,
    human: false, review: true, via: "review-token", client_id: null,
    correlation_id: CORRELATION_ID };
}

/** The committers identity.js's frozen registry maps, and the actor they map to. */
/**
 * THE COMMITTERS identity.js's frozen registry maps, and the actor they map to.
 *
 * `MAKER_EMAIL` IS DERIVED, NOT TYPED (PR 1014, Sol's re-review). It used to be
 * the Gmail literal below, written into every staged tree, so every proof in
 * both suites ran over a committer the mapping already knew — and the fact that
 * the REAL head's committer is a GitHub noreply address never showed up in a
 * single green run. The default staged maker is now the address git reports for
 * this repository's own head, and the two literal rows keep their own controls
 * in the producer suite.
 *
 * `--no-merges`, DELIBERATELY. On a `pull_request` run the checked-out head is
 * `refs/pull/N/merge`, a merge commit GitHub itself creates and commits as
 * `noreply@github.com`; that is the FORGE's identity, not a partner's, and
 * nothing in this system maps it. The last non-merge commit is the one a partner
 * actually made, which is what a candidate's maker means.
 *
 * IF THIS ADDRESS DOES NOT MAP, BOTH SUITES GO RED, and that is the point: the
 * registry is identity.js's, the address is the repository's, and a test may not
 * paper over a gap between them.
 */
function repositoryCommitterEmail() {
  const run = spawnSync("git", ["-C", REPO, "log", "-1", "--no-merges", "--format=%ce"],
    { encoding: "utf8" });
  assert.equal(run.status, 0,
    `git could not name this repository's committer: ${run.stderr}`);
  const email = run.stdout.trim();
  assert.match(email, /^[^\s<>@]+@[^\s<>@]+$/,
    "git did not answer with one committer email for this repository's head");
  return email;
}
export const MAKER_GMAIL_EMAIL = "joe.bookout.carr.us@gmail.com";
/** The address GitHub's own merge path writes as the committer of every merged
 *  head — the form this PR's own HEAD carries, and the one the registry could
 *  not name until amendment 8. */
export const MAKER_NOREPLY_EMAIL = "64207374+jbookout@users.noreply.github.com";
export const MAKER_EMAIL = repositoryCommitterEmail();
export const SUBJECT_MAKER_ACTOR = "joe";

/**
 * Every tree this process staged, and the one way they are removed. A suite
 * registers `after(cleanupStagedTrees)` once; the helper keeps no hook of its
 * own, because a hook registered at import time belongs to whichever suite
 * imported it first and that is not a property either suite should depend on.
 */
const staged = [];
export function cleanupStagedTrees() {
  for (const base of staged) rmSync(base, { recursive: true, force: true });
  staged.length = 0;
}

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

export const objectId = (kind, body) => createHash("sha1")
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

export
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
export
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

export const moduleOfTree = (target, file) => import(pathToFileURL(join(target, file)).href);

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
export
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

/**
 * THE REACHABILITY GUARD, in the shape PR 1004's amendment 6 requires: a closed
 * set, parsed rather than grepped, and asserted to be exactly the modules that
 * may name this file. A producer that some other module could import and drive
 * is a producer with a second caller, and a second caller is an argument wearing
 * an import's clothes.
 */
export
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
