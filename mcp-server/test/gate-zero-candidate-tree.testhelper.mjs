// THE CANDIDATE-TREE STAGING, ONE COPY, SHARED (2026-09-13, PR 1014
// correction round).
//
// WHY IT MOVED HERE. The producer takes no argument, so the only honest way to
// ask what it answers under a different world is to stage a whole candidate
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
// correction to the staging corrects both proofs at once.
//
// IT DECIDES NOTHING. Every function here copies bytes, edits a line it has
// asserted matches exactly once, or imports a module out of the tree it built.
// No assertion about Gate Zero lives in this file.

import assert from "node:assert/strict";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync,
  writeFileSync } from "node:fs";
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
export const FIXTURE_FILE = "gate-zero-producer-stores.v5.fixture.mjs";

/** The sealed fixture set the receipt's fixture_set_digest covers, by path. */
export const SEALED_FIXTURES = Object.freeze([
  "gate-zero-producer-stores.v5.fixture.mjs",
  "gate-zero-seam-stores.v5.fixture.mjs",
  "gate-zero-seam-stores.v5.receipt-fixture.mjs",
]);
export const ENVIRONMENT_MANIFEST = ["ops", "config", "environments.json"];

/**
 * THE AUTHENTICATED CALLER, and it is a real registered machine identity rather
 * than a shape this file made up: identity.js accepts `codex-reviewer` only with
 * the `review` marker and `review-token` provenance, and derives `review_agent`
 * for it. A correlation id is what correlation.js stamps per request, and it is
 * what the session ref is built from.
 */
export const REVIEWING_SEAT_ACTOR = "codex-reviewer";
export const CORRELATION_ID = "3f2a6c18-9b4d-4e7a-8c11-5d0e2f7a6b93";
export function reviewerActor(correlationId = CORRELATION_ID) {
  return { slug: REVIEWING_SEAT_ACTOR, review: true, via: "review-token",
    human: false, correlation_id: correlationId };
}
/** A verified partner. Registered, authenticated, and NOT this oracle's class. */
export function partnerActor(correlationId = CORRELATION_ID) {
  return { slug: "joe", human: true, via: "oauth-google", correlation_id: correlationId };
}

/** The committer identity.js maps, and the actor it maps to. */
export const MAKER_EMAIL = "joe.bookout.carr.us@gmail.com";
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

export function editOnce(path, anchor, replacement, what) {
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
export function stageTree({
  revision = REVISION_ALL_SUCCEED, maker = MAKER_EMAIL, staffedSeat = true,
  withdrawnCards = [], ledgerCanary = null, predecessorWorld = null,
  substituteStore = true, environmentEdit = null, candidateEdit = null,
  fixtureEdit = null, admitPartnerClass = false, git = true,
} = {}) {
  const cache = fileURLToPath(new URL("../node_modules/.cache/", import.meta.url));
  mkdirSync(cache, { recursive: true });
  const base = mkdtempSync(join(cache, "gate-zero-producer-"));
  staged.push(base);
  const target = join(base, "mcp-server", "src");
  mkdirSync(target, { recursive: true });
  cpSync(SRC, target, { recursive: true });

  // THE CANDIDATE'S OWN PROVENANCE. A detached HEAD is the simplest true shape:
  // 40 hex in .git/HEAD is what the producer reads first, and the reflog's last
  // line is where the committer comes from.
  if (git !== false) mkdirSync(join(base, ".git", "logs"), { recursive: true });
  if (git === true) {
    writeFileSync(join(base, ".git", "HEAD"), `${revision}\n`);
    writeFileSync(join(base, ".git", "logs", "HEAD"),
      `${"0".repeat(40)} ${revision} A Committer <${maker}> 1757000000 +0000\tcommit: staged\n`);
  }

  // THE ONE SIBLING OF src/ THAT src/ IMPORTS. tools.js reaches
  // `../continuity-reference-manifest.mjs`, so a tree that holds only src cannot
  // be imported through the verb dispatch at all — which is the door Step B's
  // suite drives. It is copied rather than stubbed: a stub would be a second
  // implementation of a manifest the staged modules actually read.
  cpSync(join(REPO, "mcp-server", "continuity-reference-manifest.mjs"),
    join(base, "mcp-server", "continuity-reference-manifest.mjs"));

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
  return target;
}

export const moduleOfTree = (target, file) => import(pathToFileURL(join(target, file)).href);

/**
 * THE REACHABILITY GUARD, in the shape PR 1004's amendment 6 requires: a closed
 * set, parsed rather than grepped, and asserted to be exactly the modules that
 * may name this file. A producer that some other module could import and drive
 * is a producer with a second caller, and a second caller is an argument wearing
 * an import's clothes.
 */
export function moduleImports(directory) {
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
