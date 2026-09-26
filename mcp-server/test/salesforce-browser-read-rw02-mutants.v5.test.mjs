// Planted bugs for the V5-RW02 browser read adapter. Each mutant is loaded from
// a scratch copy; the SAME property check the main suite runs must pass on the
// real module and fail on the mutant.
//
// R1-R8: the builder's set, chosen with Jev (verification_selection,
// 2026-09-26; all eight scored >= 0.72). M1-M11 and F1-F5: the mutants and
// findings from the independent Opus review of PR #1320 at b6d79860, each of
// which survived the first suite.

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
  checkNoFindingsAfterStop, checkHasNextStrict, checkKeysDistinctPerOpportunity, checkCredentialTextRefused,
  checkPageCapExact, checkFacadeForwardsNoArgs, checkCredentialKeyRefusedByName, checkRepeatRunArgsIdentical,
  checkEmptyReadInconclusive, checkGetterSwapHarmless,
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
/** `pairs` is [[anchor, replacement], ...]; every anchor must occur exactly once. */
async function mutant(pairs) {
  let source = readFileSync(join(SRC, FILE), "utf8");
  for (const [anchor, replacement] of pairs) {
    assert.equal(source.split(anchor).length - 1, 1, `unique mutant anchor: ${anchor}`);
    source = source.replace(anchor, replacement);
  }
  const path = join(WORK, `${++serial}.mjs`);
  writeFileSync(path, relink(source));
  return import(pathToFileURL(path).href);
}

async function killed(check, ...pairs) {
  await check(real); // the check holds on the real module
  const mod = await mutant(pairs);
  await assert.rejects(Promise.resolve().then(() => check(mod)), "the check must fail on the mutant");
}

// --- Builder's set --------------------------------------------------------

test("MUTANT R1 write reachable: the read facade hands back the driver itself", () => killed(
  checkReadOnlyFacade,
  ["  return Object.freeze(facade);\n}\n\n/** A recorder that can reach",
    "  return driver;\n}\n\n/** A recorder that can reach"]));

test("MUTANT R2 write reachable: the read-mode recorder forwards any verb", () => killed(
  checkRecorderAllowlist,
  ["if (!V5_RW02_READ_MODE_RECORD_VERBS.includes(verb)) fail(", "if (false) fail("]));

test("MUTANT R3 counter not resetting: an unclean run is skipped instead of resetting", () => killed(
  checkCounterResets,
  ["else { consecutive_clean = 0; last_reset_run_ref = r.run_ref; }", "else { last_reset_run_ref = r.run_ref; }"]));

test("MUTANT R4 missing Joe not flagged: only an empty team counts as missing Joe", () => killed(
  checkMissingJoeFlagged,
  [".filter(o => o.owner_ref !== joeUserRef && !o.team_member_refs.includes(joeUserRef))",
    ".filter(o => o.owner_ref !== joeUserRef && o.team_member_refs.length === 0)"]));

test("MUTANT R5 unbounded code retry: the 3-attempt bound is removed", () => killed(
  checkCodeRetryBound,
  ["while (state.attempts_used < V5_RW02_CODE_AUTOFILL_MAX_ATTEMPTS) {", "while (true) {"]));

test("MUTANT R6 absence concluded from a partial read", () => killed(
  checkAbsenceNeedsCompleteRead,
  ["const absent_from_salesforce = !concluded ? [] : deals", "const absent_from_salesforce = deals"]));

test("MUTANT R7 code step accepts a caller-built {started_by_system: true} as a sign-in", () => killed(
  checkCodeStepNeedsSystemStart,
  ["SIGN_IN_TICKETS.get(ticket) : undefined;",
    "(SIGN_IN_TICKETS.get(ticket) ?? (ticket.started_by_system === true ? { minted_at_ms: 0, attempts_used: 0, spent: false } : undefined)) : undefined;"]));

test("MUTANT R8 a stopped run still records findings from its partial read", () => killed(
  checkNoFindingsAfterStop,
  ["page: kernel });\n      return freeze(", "page: kernel });\n      break; return freeze("]));

// --- Reviewer's surviving mutants -----------------------------------------

test("MUTANT M1 a non-boolean has_next is treated as the end of the list", () => killed(
  checkHasNextStrict,
  ["if (hasNext !== true && hasNext !== false) fail(\"invalid_shape\", \"observe().has_next must be a boolean\");\n    if (hasNext === false) break;",
    "if (hasNext !== true) break;"]));

test("MUTANT M4 the missing-Joe loop is keyed on the opportunity name", () => killed(
  checkKeysDistinctPerOpportunity,
  ["idempotency_key: key(\"missing-joe\", f.opportunity_id),", "idempotency_key: key(\"missing-joe\", f.name),"]));

test("MUTANT M5 the credential-pattern text check is dropped", () => killed(
  checkCredentialTextRefused,
  ["  for (const pattern of Object.values(V5_RW02_CREDENTIAL_PATTERNS))", "  for (const pattern of [])"]));

test("MUTANT M8a page cap off by one (one page too many)", () => killed(
  checkPageCapExact,
  ["if (index >= V5_RW02_READ_PAGE_CAP) return", "if (index > V5_RW02_READ_PAGE_CAP) return"]));

test("MUTANT M8b page cap off by one (one page too few)", () => killed(
  checkPageCapExact,
  ["if (index >= V5_RW02_READ_PAGE_CAP) return", "if (index >= V5_RW02_READ_PAGE_CAP - 1) return"]));

test("MUTANT M9 the reader facade forwards arguments to the driver", () => killed(
  checkFacadeForwardsNoArgs,
  ["facade[name] = () => method.call(driver);", "facade[name] = (...args) => method.apply(driver, args);"]));

test("MUTANT M10 the credential-key refusal is dropped (falls through to unknown_field)", () => killed(
  checkCredentialKeyRefusedByName,
  ["    if (CREDENTIAL_KEYS.test(key)) fail(\"credential_field_refused\",", "    if (false) fail(\"credential_field_refused\","]));

test("MUTANT M11 the unknown-to-CARR key includes run_ref", () => killed(
  checkRepeatRunArgsIdentical,
  ["idempotency_key: key(\"unknown-to-carr\", f.opportunity_id),",
    "idempotency_key: key(\"unknown-to-carr\", f.opportunity_id, run_ref),"]));

// --- Reviewer's findings as mutants ---------------------------------------

test("MUTANT F1 run-specific text in source_note (repeat run hits key_reuse)", () => killed(
  checkRepeatRunArgsIdentical,
  ["const source_note = \"V5-RW02 attended browser read of Dell's Salesforce (decisions c04ac197, c0014a37)\";",
    "const source_note = `V5-RW02 attended browser read, run ${run_ref}`;"]));

test("MUTANT F2 the code bound is per call: the ticket is not spent and the count is call-local", () => killed(
  checkCodeRetryBound,
  ["  state.spent = true;\n", "\n"],
  ["while (state.attempts_used < V5_RW02_CODE_AUTOFILL_MAX_ATTEMPTS) {\n    const attempt = ++state.attempts_used;",
    "for (let attempt = 1; attempt <= V5_RW02_CODE_AUTOFILL_MAX_ATTEMPTS; attempt++) {"]));

test("MUTANT F3 TOCTOU: no snapshot copy and a second read of each field", () => killed(
  checkGetterSwapHarmless,
  ["try { sfCopy = structuredClone(salesforce); carrCopy = structuredClone(carr); }",
    "try { sfCopy = salesforce; carrCopy = carr; }"],
  ["  const team = [...team_member_refs];\n  return {\n    opportunity_id,",
    "  const team = [...raw.team_member_refs];\n  return {\n    opportunity_id: raw.opportunity_id,"]));

test("MUTANT F4 the absent finding is keyed per run", () => killed(
  checkRepeatRunArgsIdentical,
  ["idempotency_key: key(\"absent\", f.deal_id, f.reason_id)", "idempotency_key: key(\"absent\", f.deal_id, run_ref)"]));

test("MUTANT F5 an empty complete read concludes every open deal absent", () => killed(
  checkEmptyReadInconclusive,
  ["const concluded = complete && opportunities.length > 0;", "const concluded = complete;"]));
