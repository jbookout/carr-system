// Planted bugs for the V5-RW02 browser read adapter. Each mutant is loaded from
// a scratch copy; the SAME property check the main suite runs must pass on the
// real module and fail on the mutant. Mutant set chosen with Jev
// (verification_selection, 2026-09-26): all eight scored >= 0.72.

import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import * as real from "../src/salesforce-browser-read-rw02.v5.js";
import {
  checkReadOnlyFacade, checkRecorderAllowlist, checkMissingJoeFlagged, checkCounterResets,
  checkCodeRetryBound, checkAbsenceNeedsCompleteRead, checkCodeStepNeedsSystemStart,
  checkNoFindingsAfterStop,
} from "./salesforce-browser-read-rw02.fixtures.mjs";

const SRC = fileURLToPath(new URL("../src/", import.meta.url));
const FILE = "salesforce-browser-read-rw02.v5.js";
const WORK = mkdtempSync(join(tmpdir(), "rw02-browser-read-mutants-"));
test.after(() => rmSync(WORK, { recursive: true, force: true }));
let serial = 0;

function relink(source) {
  return source.replace(/from\s+"\.\/([^"]+)"/g, (_, file) =>
    `from "${pathToFileURL(join(SRC, file)).href}"`);
}
async function mutant(anchor, replacement) {
  const source = readFileSync(join(SRC, FILE), "utf8");
  assert.equal(source.split(anchor).length - 1, 1, `unique mutant anchor: ${anchor}`);
  const path = join(WORK, `${++serial}.mjs`);
  writeFileSync(path, relink(source.replace(anchor, replacement)));
  return import(pathToFileURL(path).href);
}

async function killed(check, anchor, replacement) {
  await check(real); // the check holds on the real module
  const mod = await mutant(anchor, replacement);
  await assert.rejects(Promise.resolve().then(() => check(mod)), "the check must fail on the mutant");
}

test("MUTANT R1 write reachable: the read facade hands back the driver itself", () => killed(
  checkReadOnlyFacade,
  "  return Object.freeze(facade);\n}\n\n/** A recorder that can reach",
  "  return driver;\n}\n\n/** A recorder that can reach"));

test("MUTANT R2 write reachable: the read-mode recorder forwards any verb", () => killed(
  checkRecorderAllowlist,
  "if (!V5_RW02_READ_MODE_RECORD_VERBS.includes(verb)) fail(",
  "if (false) fail("));

test("MUTANT R3 counter not resetting: an unclean run is skipped instead of resetting", () => killed(
  checkCounterResets,
  "else { consecutive_clean = 0; last_reset_run_ref = r.run_ref; }",
  "else { last_reset_run_ref = r.run_ref; }"));

test("MUTANT R4 missing Joe not flagged: only an empty team counts as missing Joe", () => killed(
  checkMissingJoeFlagged,
  ".filter(o => o.owner_ref !== joeUserRef && !o.team_member_refs.includes(joeUserRef))",
  ".filter(o => o.owner_ref !== joeUserRef && o.team_member_refs.length === 0)"));

test("MUTANT R5 unbounded code retry: the 3-attempt bound is removed", () => killed(
  checkCodeRetryBound,
  "attempt <= V5_RW02_CODE_AUTOFILL_MAX_ATTEMPTS; attempt++",
  "; attempt++"));

test("MUTANT R6 absence concluded from a partial read", () => killed(
  checkAbsenceNeedsCompleteRead,
  "const absent_from_salesforce = !complete ? [] : deals",
  "const absent_from_salesforce = deals"));

test("MUTANT R7 code step clicks for a sign-in the system did not start", () => killed(
  checkCodeStepNeedsSystemStart,
  "if (!plain(signIn) || signIn.started_by_system !== true) return signInStop(",
  "if (!plain(signIn)) return signInStop("));

test("MUTANT R8 a stopped run still records findings from its partial read", () => killed(
  checkNoFindingsAfterStop,
  "page: kernel });\n      return freeze(",
  "page: kernel });\n      break; return freeze("));
