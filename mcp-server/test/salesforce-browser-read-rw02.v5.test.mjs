// V5-RW02 browser-only Salesforce READ adapter: read-only guarantee, sign-in
// stops, reconciliation findings and the 5-consecutive-clean counter.
// Synthetic data only. No real browser, no real Salesforce, no network.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import * as mod from "../src/salesforce-browser-read-rw02.v5.js";
import {
  FakeReaderDriver, FakeCodeDriver, SpyRecorder, bindingSource, carr, opp, page,
  checkReadOnlyFacade, checkRecorderAllowlist, checkMissingJoeFlagged, checkCounterResets,
  checkCodeRetryBound, checkAbsenceNeedsCompleteRead, checkCodeStepNeedsSystemStart,
  checkNoFindingsAfterStop, checkHasNextStrict, checkKeysDistinctPerOpportunity, checkCredentialTextRefused,
  checkPageCapExact, checkFacadeForwardsNoArgs, checkCredentialKeyRefusedByName, checkRepeatRunArgsIdentical,
  checkEmptyReadInconclusive, checkGetterSwapHarmless,
} from "./salesforce-browser-read-rw02.fixtures.mjs";

const {
  V5_RW02_READER_METHODS, V5_RW02_READ_MODE_RECORD_VERBS, V5_RW02_AUTONOMY_THRESHOLD,
  V5_RW02_CODE_AUTOFILL_MAX_ATTEMPTS, V5RW02BrowserReadError,
  readOnlyReader, readModeRecorder, classifySignIn, attemptCodeAutofill,
  reconcileSalesforceDeals, evaluateAutonomyCounter, runSalesforceBrowserReadReconciliation,
  unavailableBindingSource,
} = mod;

// ---------------------------------------------------------------------------
// 1. Read mode performs ZERO Salesforce writes, and no write path is reachable.
// ---------------------------------------------------------------------------

test("the read facade exposes exactly the reader methods and never the driver", async () => {
  await checkReadOnlyFacade(mod);
  assert.deepEqual([...V5_RW02_READER_METHODS], ["nextPage", "observe"]);
});

test("a full read run never touches any driver write method", async () => {
  const driver = new FakeReaderDriver([
    page({ opportunities: [opp("A", { team: ["joe-sf"] })], has_next: true }),
    page({ opportunities: [opp("B", { team: [] })], has_next: false }),
  ]);
  const recorder = new SpyRecorder();
  const out = await runSalesforceBrowserReadReconciliation({ reader: driver,
    bindingSource: bindingSource(), carrDeals: async () => [carr("A"), carr("B")], recorder });
  assert.equal(out.decision, "reconciled");
  assert.equal(driver.writeCalls, 0, "no write-shaped driver method may be called");
  assert.deepEqual(driver.readCalls.sort(), ["nextPage", "observe", "observe"]);
  assert.equal(out.salesforce_writes, 0);
  assert.equal(out.effects.salesforce_writes, 0);
});

test("the recorder refuses every verb outside the read-mode allowlist", async () => {
  await checkRecorderAllowlist(mod);
  assert.deepEqual([...V5_RW02_READ_MODE_RECORD_VERBS],
    ["add-loop", "record-finding", "record-salesforce-page-stop"]);
});

test("a run records only through allowlisted verbs", async () => {
  const recorder = new SpyRecorder();
  await runSalesforceBrowserReadReconciliation({
    reader: new FakeReaderDriver([page({ opportunities: [opp("A"), opp("Z", { team: ["joe-sf"] })] })]),
    bindingSource: bindingSource(), carrDeals: async () => [carr("A"), carr("Q")], recorder });
  assert.ok(recorder.calls.length >= 3);
  for (const { verb } of recorder.calls) assert.ok(V5_RW02_READ_MODE_RECORD_VERBS.includes(verb), verb);
});

test("the runner takes no mode, writer, partner, org or capability argument", async () => {
  for (const extra of [{ mode: "write" }, { writer: {} }, { partner: "joe" }, { org_id: "00Dx" },
    { capability: {} }, { tenant: "carr-internal" }, { binding: {} }]) {
    await assert.rejects(runSalesforceBrowserReadReconciliation({ reader: new FakeReaderDriver([]),
      bindingSource: bindingSource(), carrDeals: async () => [], recorder: new SpyRecorder(), ...extra }),
    e => e instanceof V5RW02BrowserReadError && e.code === "unknown_option", JSON.stringify(extra));
  }
});

test("the module source names no Salesforce write method and no deal-mutating verb", () => {
  const src = readFileSync(fileURLToPath(new URL("../src/salesforce-browser-read-rw02.v5.js",
    import.meta.url)), "utf8");
  // A verb can only be called by its name as a string literal.
  for (const verb of ["update-deal", "patch-deal-field", "link-salesforce-reference", "new-deal",
    "record-salesforce-write-readback", "record-salesforce-duplicate-check", "call-verb"])
    assert.ok(!src.includes(`"${verb}"`) && !src.includes(`'${verb}'`) && !src.includes(`\`${verb}`), verb);
  for (const method of ["saveOpportunity", "addTeamMember", "clickLogIn", ".type(", "typeText", ".fill("])
    assert.ok(!src.includes(method), method);
});

// ---------------------------------------------------------------------------
// 2. Identity comes from the server-side binding, never from the caller.
// ---------------------------------------------------------------------------

test("no org binding means no run", async () => {
  const recorder = new SpyRecorder();
  const driver = new FakeReaderDriver([page()]);
  const out = await runSalesforceBrowserReadReconciliation({ reader: driver,
    bindingSource: unavailableBindingSource, carrDeals: async () => [], recorder });
  assert.equal(out.decision, "refused");
  assert.equal(out.reason_id, "org_binding_unavailable");
  assert.equal(driver.readCalls.length, 0, "the browser is not even observed");
  assert.equal(recorder.calls.length, 0);
});

test("the source must be Dell's org, with Dell's own OK", async () => {
  for (const [patch, reason] of [[{ source_partner: "joe" }, "source_not_dell"],
    [{ dell_consent: null }, "dell_consent_absent"],
    [{ dell_consent: { granted: false, decision_ref: "d-1" } }, "dell_consent_absent"]]) {
    const driver = new FakeReaderDriver([page()]);
    const out = await runSalesforceBrowserReadReconciliation({ reader: driver,
      bindingSource: bindingSource(patch), carrDeals: async () => [], recorder: new SpyRecorder() });
    assert.equal(out.reason_id, reason);
    assert.equal(driver.readCalls.length, 0);
  }
});

test("a page signed in to another org or seat stops through the kernel ladder", async () => {
  for (const [obs, reason] of [[{ org_id: "00DjoeOrg000001" }, "org_mismatch"],
    [{ signed_in_account_ref: "joe-seat" }, "signed_in_account_mismatch"],
    [{ ui_contract_digest: `sha256:${"9".repeat(64)}` }, "ui_drift"]]) {
    const recorder = new SpyRecorder();
    const out = await runSalesforceBrowserReadReconciliation({
      reader: new FakeReaderDriver([page({ observation: obs, opportunities: [opp("A")] })]),
      bindingSource: bindingSource(), carrDeals: async () => [carr("A")], recorder });
    assert.equal(out.decision, "stopped");
    assert.equal(out.reason_id, reason);
    assert.deepEqual(recorder.calls.map(c => c.verb), ["record-salesforce-page-stop"]);
    assert.equal(out.findings_recorded, 0, "a stopped run concludes nothing");
  }
});

// ---------------------------------------------------------------------------
// 3. Sign-in: only stop conditions, never credential entry.
// ---------------------------------------------------------------------------

test("sign-in stop conditions produce typed stops", () => {
  const cases = [
    [{ state: "password_prompt", credentials_autofilled: false }, "password_prompt_without_autofill"],
    [{ state: "password_prompt" }, "password_prompt_without_autofill"],
    [{ state: "code_prompt" }, "unprompted_code_prompt"],
    [{ state: "security_challenge" }, "security_challenge"],
    [{ state: "unknown" }, "sign_in_state_unobservable"],
    [{ state: "password_prompt", credentials_autofilled: true }, "partner_sign_in_required"],
  ];
  for (const [signIn, reason] of cases) {
    const a = classifySignIn(signIn, { systemStartedSignIn: false });
    assert.equal(a.decision, "stop", reason);
    assert.equal(a.reason_id, reason);
    assert.equal(a.resolution_owner, "partner_at_the_browser");
    assert.equal(a.credential_entry_performed, false);
  }
  assert.equal(classifySignIn({ state: "signed_in" }, { systemStartedSignIn: false }).decision, "continue");
});

test("a sign-in observation carrying a credential-shaped field is refused outright, by name", async () => {
  await checkCredentialKeyRefusedByName(mod);
  assert.throws(() => classifySignIn({ state: "code_prompt", colour: "x" }, { systemStartedSignIn: false }),
    e => e.code === "unknown_field");
});

test("a code prompt stops the run and is recorded as an MFA page stop", async () => {
  const recorder = new SpyRecorder();
  const out = await runSalesforceBrowserReadReconciliation({
    reader: new FakeReaderDriver([page({ sign_in: { state: "code_prompt" } })]),
    bindingSource: bindingSource(), carrDeals: async () => [], recorder });
  assert.equal(out.decision, "stopped");
  assert.equal(out.reason_id, "unprompted_code_prompt");
  assert.equal(out.page_stop.challenge, "mfa_challenge");
  assert.equal(recorder.calls[0].verb, "record-salesforce-page-stop");
  assert.equal(recorder.calls[0].args.page.observation.challenge, "mfa_challenge");
});

test("the code autofill step: click field, click the suggestion, check filled", async () => {
  const driver = new FakeCodeDriver({ suggestionOn: [1], fillAfterSuggestion: true });
  const a = await attemptCodeAutofill({ driver, ticket: mod.beginSystemSignIn({ nowMs: 5 }), nowMs: 6 });
  assert.equal(a.decision, "filled");
  assert.equal(a.attempts, 1);
  assert.deepEqual(driver.log, ["clickCodeField", "autofillSuggestionVisible", "clickAutofillSuggestion",
    "codeFieldFilled"]);
});

test("the code autofill step stops for the partner after exactly 3 attempts", async () => {
  await checkCodeRetryBound(mod);
  assert.equal(V5_RW02_CODE_AUTOFILL_MAX_ATTEMPTS, 3);
});

test("the code autofill step never clicks anything for a sign-in the system did not start", async () => {
  await checkCodeStepNeedsSystemStart(mod);
});

test("a sign-in ticket is single-use and goes stale", async () => {
  const ticket = mod.beginSystemSignIn({ nowMs: 1_000 });
  const stale = new FakeCodeDriver({ fillOn: [1] });
  const a = await attemptCodeAutofill({ driver: stale, ticket,
    nowMs: 1_000 + mod.V5_RW02_SIGN_IN_TICKET_TTL_MS + 1 });
  assert.equal(a.reason_id, "sign_in_ticket_stale");
  assert.deepEqual(stale.log, []);
  const reuse = new FakeCodeDriver({ fillOn: [1] });
  assert.equal((await attemptCodeAutofill({ driver: reuse, ticket, nowMs: 1_001 })).reason_id,
    "sign_in_ticket_spent");
  assert.deepEqual(reuse.log, []);
  const filled = mod.beginSystemSignIn({ nowMs: 0 });
  assert.equal((await attemptCodeAutofill({ driver: new FakeCodeDriver({ fillOn: [1] }), ticket: filled,
    nowMs: 0 })).decision, "filled");
  const after = new FakeCodeDriver({ fillOn: [1] });
  assert.equal((await attemptCodeAutofill({ driver: after, ticket: filled, nowMs: 1 })).reason_id,
    "sign_in_ticket_spent");
  assert.deepEqual(after.log, []);
  assert.ok(Object.isFrozen(ticket) && Object.keys(ticket).length === 0, "the ticket carries no readable state");
});

test("a run that stops on a later page records the stop and concludes nothing", async () => {
  await checkNoFindingsAfterStop(mod);
});

test("a non-boolean filled report stops rather than guessing", async () => {
  const driver = new FakeCodeDriver({ suggestionOn: [], filledValue: "123456" });
  const a = await attemptCodeAutofill({ driver, ticket: mod.beginSystemSignIn({ nowMs: 0 }), nowMs: 0 });
  assert.equal(a.decision, "stop");
  assert.equal(a.reason_id, "code_fill_state_unobservable");
  assert.ok(!JSON.stringify(a).includes("123456"), "the code value never enters the answer");
});

// ---------------------------------------------------------------------------
// 4. Reconciliation: presence and partner membership only.
// ---------------------------------------------------------------------------

test("a deal in Dell's Salesforce without Joe is flagged; Joe as owner or team member is present", async () => {
  await checkMissingJoeFlagged(mod);
  const r = reconcileSalesforceDeals({ joeUserRef: "joe-sf", complete: true,
    salesforce: [opp("A", { owner: "joe-sf", team: [] }), opp("B", { team: ["joe-sf"] })],
    carr: [carr("A"), carr("B")] });
  assert.deepEqual(r.missing_joe, []);
});

test("unknown-to-CARR and absent-from-Salesforce are both found, matching by opportunity id only", () => {
  const r = reconcileSalesforceDeals({ joeUserRef: "joe-sf", complete: true,
    salesforce: [opp("A", { team: ["joe-sf"] }), opp("N", { team: ["joe-sf"], name: "Deal Q" })],
    carr: [carr("A"), carr("Q"), { deal_id: "00000000-0000-4000-8000-00000000000f", name: "Unlinked",
      salesforce_id: null }] });
  assert.deepEqual(r.unknown_to_carr.map(f => f.opportunity_id), [opp("N").opportunity_id]);
  assert.deepEqual(r.absent_from_salesforce.map(f => [f.name, f.reason_id]).sort(),
    [["Deal Q", "salesforce_id_not_seen"], ["Unlinked", "no_salesforce_link"]]);
});

test("absence is never concluded from an incomplete read", async () => {
  await checkAbsenceNeedsCompleteRead(mod);
});

test("the same opportunity reported twice with different facts is an inconsistent read", () => {
  assert.throws(() => reconcileSalesforceDeals({ joeUserRef: "joe-sf", complete: true,
    salesforce: [opp("A"), opp("A", { team: ["joe-sf"] })], carr: [] }),
  e => e.code === "inconsistent_result");
});

test("reconciliation never compares or proposes field values", () => {
  const r = reconcileSalesforceDeals({ joeUserRef: "joe-sf", complete: true,
    salesforce: [opp("A", { team: ["joe-sf"] })], carr: [carr("A")] });
  assert.equal(r.field_sync, "not_performed_presence_and_membership_only");
  assert.throws(() => reconcileSalesforceDeals({ joeUserRef: "joe-sf", complete: true,
    salesforce: [{ ...opp("A"), stage: "Closed Won" }], carr: [] }), e => e.code === "unknown_field");
});

test("findings are recorded: missing Joe as a Dell loop, unknown as a partner loop, absent as a finding", async () => {
  const recorder = new SpyRecorder();
  const out = await runSalesforceBrowserReadReconciliation({
    reader: new FakeReaderDriver([page({ opportunities: [opp("A"), opp("N", { team: ["joe-sf"] })] })]),
    bindingSource: bindingSource(), carrDeals: async () => [carr("A"), carr("Q")], recorder });
  assert.equal(out.decision, "reconciled");
  assert.deepEqual(out.counts, { missing_joe: 1, unknown_to_carr: 1, absent_from_salesforce: 1 });
  const loops = recorder.calls.filter(c => c.verb === "add-loop").map(c => c.args);
  const dell = loops.find(l => l.owner === "Dell");
  assert.equal(dell.kind, "team_loop");
  assert.match(dell.title, /Add Joe/);
  const partner = loops.find(l => l.owner === "Joe");
  assert.equal(partner.kind, "open_loop");
  assert.equal(partner.blocker, "human_only");
  const finding = recorder.calls.find(c => c.verb === "record-finding").args;
  assert.equal(finding.found, false);
  assert.equal(finding.internal, true);
  assert.equal(finding.subject, carr("Q").deal_id);
  assert.equal(finding.kind, "salesforce_presence:salesforce_id_not_seen");
  assert.match(finding.source, /reason salesforce_id_not_seen$/);
  // Nothing the browser showed about sign-in or the seat leaks into a record.
  for (const c of recorder.calls) {
    assert.ok(!JSON.stringify(c.args).includes("dell-seat"), `${c.verb} carries the seat ref`);
    assert.ok(!Object.hasOwn(c.args, "tenant") && !Object.hasOwn(c.args, "actor"),
      `${c.verb} passes a caller identity`);
  }
});

test("a repeat run sends byte-identical arguments, so nothing is refused or filed twice", async () => {
  await checkRepeatRunArgsIdentical(mod);
});

test("two opportunities with the same name file two loops with two keys", async () => {
  await checkKeysDistinctPerOpportunity(mod);
});

test("a credential-shaped opportunity name is refused", async () => {
  await checkCredentialTextRefused(mod);
});

test("a non-boolean has_next is a malformed page, not the end of the list", async () => {
  await checkHasNextStrict(mod);
});

test("the page cap is exact", async () => {
  await checkPageCapExact(mod);
});

test("the read facade passes no arguments to the driver", async () => {
  await checkFacadeForwardsNoArgs(mod);
});

test("an empty complete read concludes no absence", async () => {
  await checkEmptyReadInconclusive(mod);
});

test("a getter that answers differently on a second read cannot smuggle a value into a record", async () => {
  await checkGetterSwapHarmless(mod);
});

test("the absence comparison is open deals only (CARR exposes no invoiced marker)", () => {
  const r = reconcileSalesforceDeals({ joeUserRef: "joe-sf", complete: true,
    salesforce: [opp("A", { team: ["joe-sf"] })], carr: [carr("A")] });
  assert.equal(r.absence_scope, "open_deals_only_no_invoiced_marker_exposed");
});

test("the run caps the number of pages it will read", async () => {
  const pages = Array.from({ length: mod.V5_RW02_READ_PAGE_CAP + 1 }, () => page({ has_next: true }));
  const recorder = new SpyRecorder();
  const out = await runSalesforceBrowserReadReconciliation({ reader: new FakeReaderDriver(pages),
    bindingSource: bindingSource(), carrDeals: async () => [], recorder });
  assert.equal(out.decision, "stopped");
  assert.equal(out.reason_id, "page_cap_reached");
  assert.equal(out.findings_recorded, 0);
});

// ---------------------------------------------------------------------------
// 5. The 5-consecutive-clean counter (decision 493de438). Data + pure function.
// ---------------------------------------------------------------------------

const run = (n, outcome = "clean", action_kind = "opportunity_phase_update") =>
  ({ run_ref: `run-${action_kind}-${n}`, action_kind, outcome, execution_mode: "attended" });

test("five consecutive clean attended runs meet the threshold, four do not", () => {
  assert.equal(V5_RW02_AUTONOMY_THRESHOLD, 5);
  const four = evaluateAutonomyCounter({ action_kind: "opportunity_phase_update",
    runs: [1, 2, 3, 4].map(n => run(n)) });
  assert.equal(four.consecutive_clean, 4);
  assert.equal(four.threshold_met, false);
  const five = evaluateAutonomyCounter({ action_kind: "opportunity_phase_update",
    runs: [1, 2, 3, 4, 5].map(n => run(n)) });
  assert.equal(five.consecutive_clean, 5);
  assert.equal(five.threshold_met, true);
});

test("any unclean run resets the count", async () => {
  await checkCounterResets(mod);
  for (const bad of ["refused", "corrected", "failed", "stopped"]) {
    const a = evaluateAutonomyCounter({ action_kind: "opportunity_phase_update",
      runs: [run(1), run(2), run(3), run(4), run(5, bad), run(6)] });
    assert.equal(a.consecutive_clean, 1, bad);
    assert.equal(a.threshold_met, false);
  }
});

test("the counter never promotes anything in this slice", () => {
  const a = evaluateAutonomyCounter({ action_kind: "opportunity_phase_update",
    runs: [1, 2, 3, 4, 5, 6, 7].map(n => run(n)) });
  assert.equal(a.threshold_met, true);
  assert.equal(a.autonomy_active, false);
  assert.equal(a.promotion, "not_performed_in_this_slice");
  assert.equal(a.outward_effect_granted, false);
});

test("runs of another kind neither count nor reset (no trust inheritance)", () => {
  const a = evaluateAutonomyCounter({ action_kind: "opportunity_phase_update",
    runs: [run(1), run(2), run(1, "failed", "opportunity_create"), run(3), run(2, "clean", "opportunity_create")] });
  assert.equal(a.consecutive_clean, 3);
});

test("an unattended run, a duplicate run and an unknown outcome are refused", () => {
  assert.throws(() => evaluateAutonomyCounter({ action_kind: "opportunity_phase_update",
    runs: [{ ...run(1), execution_mode: "unattended" }] }), e => e.code === "unattended_run_refused");
  assert.throws(() => evaluateAutonomyCounter({ action_kind: "opportunity_phase_update",
    runs: [run(1), run(1)] }), e => e.code === "run_counted_twice");
  assert.throws(() => evaluateAutonomyCounter({ action_kind: "opportunity_phase_update",
    runs: [run(1, "mostly_clean")] }), e => e.code === "unknown_outcome");
  assert.throws(() => evaluateAutonomyCounter({ action_kind: "etl_protected_send", runs: [] }),
    e => e.code === "unknown_action_kind");
});
