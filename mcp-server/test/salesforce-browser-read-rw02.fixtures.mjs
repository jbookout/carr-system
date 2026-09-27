// Shared synthetic fixtures and property checks for the V5-RW02 browser read
// adapter. Each check takes a MODULE so the mutant suite runs the very same
// check against a planted-bug copy and requires it to fail there.
// Synthetic data only: no real org, seat, deal or person identifier.

import assert from "node:assert/strict";
import { createHash } from "node:crypto";

const UI = `sha256:${"1".repeat(64)}`;
export const ORG = Object.freeze({ origin: "https://synthetic-dell.invalid",
  org_id: "00DsyntheticD01", account_ref: "dell-seat" });

/** A synthetic 18-char opportunity id derived from a one-letter tag. */
export function oppId(tag) { return `006SYNTH${tag.padEnd(7, "0")}AAA`; }

export function opp(tag, { owner = "dell-sf", team = [], name } = {}) {
  return { opportunity_id: oppId(tag), name: name ?? `Deal ${tag}`, owner_ref: owner,
    team_member_refs: team };
}

export function carr(tag) {
  const hex = tag.charCodeAt(0).toString(16).padStart(12, "0");
  return { deal_id: `00000000-0000-4000-8000-${hex}`, name: `Deal ${tag}`, salesforce_id: oppId(tag) };
}

export function page({ opportunities = [], has_next = false, sign_in = { state: "signed_in" },
  observation = {} } = {}) {
  return { sign_in, has_next, opportunities,
    page: { origin: ORG.origin, org_id: ORG.org_id, signed_in_account_ref: ORG.account_ref,
      ui_contract_digest: UI, challenge: "none", result_consistency: "consistent", ...observation } };
}

export function bindingSource(patch = {}) {
  return { read: async () => ({ source_partner: "dell", org: { ...ORG }, ui_contract_digest: UI,
    joe_user_ref: "joe-sf", dell_consent: { granted: true, decision_ref: "synthetic-dell-ok" },
    ...patch }) };
}

/**
 * A fake browser driver. It deliberately carries write-shaped methods too, so a
 * test can prove the runner never reaches them.
 */
export class FakeReaderDriver {
  constructor(pages) {
    this.pages = pages; this.index = 0; this.readCalls = []; this.writeCalls = 0; this.argCounts = [];
  }
  async observe(...args) {
    this.readCalls.push("observe"); this.argCounts.push(args.length);
    const p = this.pages[this.index];
    return typeof p === "function" ? p() : structuredClone(p);
  }
  async nextPage(...args) { this.readCalls.push("nextPage"); this.argCounts.push(args.length); this.index++; }
  async saveOpportunity() { this.writeCalls++; }
  async addTeamMember() { this.writeCalls++; }
  async clickLogIn() { this.writeCalls++; }
  async typeText() { this.writeCalls++; }
}

/**
 * The code-step fake. It reports only filled / not filled; it has no value to
 * give. `suggestionOn` lists the attempts on which the From-Messages chip shows;
 * `fillOn` the attempts the field fills with no chip; `fillAfterSuggestion` fills
 * once the chip is clicked. A runaway loop is caught by the click ceiling.
 */
export class FakeCodeDriver {
  constructor({ suggestionOn = [], fillOn = [], fillAfterSuggestion = false, filledValue } = {}) {
    Object.assign(this, { suggestionOn, fillOn, fillAfterSuggestion, filledValue });
    this.attempt = 0; this.log = []; this.filled = false;
  }
  async clickCodeField() {
    this.log.push("clickCodeField"); this.attempt++;
    if (this.attempt > 10) throw new Error("runaway: code field clicked more than 10 times");
    if (this.fillOn.includes(this.attempt)) this.filled = true;
  }
  async autofillSuggestionVisible() {
    this.log.push("autofillSuggestionVisible"); return this.suggestionOn.includes(this.attempt);
  }
  async clickAutofillSuggestion() {
    this.log.push("clickAutofillSuggestion"); if (this.fillAfterSuggestion) this.filled = true;
  }
  async codeFieldFilled() {
    this.log.push("codeFieldFilled"); return this.filledValue !== undefined ? this.filledValue : this.filled;
  }
}

/**
 * Mirrors withEnvelope (tools.js): the request hash covers the verb and every
 * argument except the key; a known key arriving with a different hash is
 * refused as key_reuse, and a matching one replays without a second write.
 */
export class SpyRecorder {
  constructor() { this.calls = []; this.hashes = new Map(); this.writes = 0; }
  async record(verb, args) {
    this.calls.push({ verb, args: structuredClone(args) });
    const hash = createHash("sha256").update(JSON.stringify({ verb,
      args: { ...args, idempotency_key: undefined } })).digest("hex");
    const prior = this.hashes.get(args.idempotency_key);
    if (prior !== undefined) {
      if (prior !== hash) { const e = new Error("key_reuse"); e.error = "key_reuse"; throw e; }
      return { replayed: true };
    }
    this.hashes.set(args.idempotency_key, hash); this.writes++;
    return { ok: true };
  }
}

// ---------------------------------------------------------------------------
// Property checks, shared with the mutant suite.
// ---------------------------------------------------------------------------

export async function checkReadOnlyFacade(mod) {
  const driver = new FakeReaderDriver([]);
  const facade = mod.readOnlyReader(driver);
  assert.deepEqual(Object.keys(facade).sort(), ["nextPage", "observe"]);
  assert.notEqual(facade, driver);
  assert.ok(Object.isFrozen(facade));
  for (const name of ["saveOpportunity", "addTeamMember", "clickLogIn", "typeText"])
    assert.equal(facade[name], undefined, name);
  assert.equal(Object.getPrototypeOf(facade), null, "no prototype path back to the driver");
}

export async function checkRecorderAllowlist(mod) {
  const inner = new SpyRecorder();
  const recorder = mod.readModeRecorder(inner);
  for (const verb of ["update-deal", "patch-deal-field", "link-salesforce-reference",
    "record-salesforce-write-readback", "new-deal", "call-verb"]) {
    await assert.rejects(recorder.record(verb, {}),
      e => e instanceof mod.V5RW02BrowserReadError && e.code === "verb_not_permitted_in_read_mode", verb);
  }
  assert.equal(inner.calls.length, 0);
}

export async function checkMissingJoeFlagged(mod) {
  const r = mod.reconcileSalesforceDeals({ joeUserRef: "joe-sf", complete: true,
    salesforce: [opp("A", { owner: "dell-sf", team: ["someone-else"] })], carr: [carr("A")] });
  assert.equal(r.missing_joe.length, 1);
  assert.equal(r.missing_joe[0].opportunity_id, oppId("A"));
  assert.equal(r.missing_joe[0].action, "get_joe_added_to_deal");
}

export async function checkCounterResets(mod) {
  const run = (n, outcome = "clean") => ({ run_ref: `r-${n}`, action_kind: "opportunity_phase_update",
    outcome, execution_mode: "attended" });
  const a = mod.evaluateAutonomyCounter({ action_kind: "opportunity_phase_update",
    runs: [run(1), run(2), run(3), run(4), run(5, "failed"), run(6), run(7), run(8), run(9)] });
  assert.equal(a.consecutive_clean, 4);
  assert.equal(a.threshold_met, false);
}

export async function checkCodeRetryBound(mod) {
  const driver = new FakeCodeDriver({ suggestionOn: [2], fillAfterSuggestion: false });
  const ticket = mod.beginSystemSignIn({ nowMs: 1_000 });
  const a = await mod.attemptCodeAutofill({ driver, ticket, nowMs: 1_000 });
  assert.equal(a.decision, "stop");
  assert.equal(a.reason_id, "code_autofill_not_filled");
  assert.equal(a.attempts, 3);
  assert.equal(driver.log.filter(x => x === "clickCodeField").length, 3);
  assert.equal(a.resolution_owner, "partner_at_the_browser");
  // The bound is PER SIGN-IN: calling again with the same ticket clicks nothing.
  const again = await mod.attemptCodeAutofill({ driver, ticket, nowMs: 1_500 });
  assert.equal(again.decision, "stop");
  assert.equal(driver.log.filter(x => x === "clickCodeField").length, 3, "3 clicks per sign-in, not per call");
  // And a fill on the third attempt is still a fill.
  const late = new FakeCodeDriver({ fillOn: [3] });
  assert.equal((await mod.attemptCodeAutofill({ driver: late, ticket: mod.beginSystemSignIn({ nowMs: 0 }),
    nowMs: 0 })).attempts, 3);
}

export async function checkAbsenceNeedsCompleteRead(mod) {
  const partial = mod.reconcileSalesforceDeals({ joeUserRef: "joe-sf", complete: false,
    salesforce: [opp("A", { team: ["joe-sf"] })], carr: [carr("A"), carr("Q")] });
  assert.deepEqual(partial.absent_from_salesforce, []);
  assert.equal(partial.absence_concluded, false);
  const full = mod.reconcileSalesforceDeals({ joeUserRef: "joe-sf", complete: true,
    salesforce: [opp("A", { team: ["joe-sf"] })], carr: [carr("A"), carr("Q")] });
  assert.equal(full.absent_from_salesforce.length, 1);
}

export async function checkCodeStepNeedsSystemStart(mod) {
  const real = mod.beginSystemSignIn({ nowMs: 0 });
  const forged = [undefined, null, { started_by_system: true }, Object.freeze(Object.create(null)),
    { ...real }, Object.create(real)];
  for (const ticket of forged) {
    const driver = new FakeCodeDriver({ fillOn: [1] });
    const a = await mod.attemptCodeAutofill({ driver, ticket, nowMs: 0 });
    assert.equal(a.decision, "stop");
    assert.equal(a.reason_id, "unprompted_code_prompt");
    assert.deepEqual(driver.log, [], "nothing is clicked for a sign-in the system did not start");
  }
}

export async function checkNoFindingsAfterStop(mod) {
  // Page 1 reads a missing-Joe deal; page 2 hits a code prompt. Nothing may be concluded.
  const recorder = new SpyRecorder();
  const out = await mod.runSalesforceBrowserReadReconciliation({
    reader: new FakeReaderDriver([page({ opportunities: [opp("A")], has_next: true }),
      page({ sign_in: { state: "code_prompt" } })]),
    bindingSource: bindingSource(), carrDeals: async () => [carr("A"), carr("Q")], recorder });
  assert.equal(out.decision, "stopped");
  assert.deepEqual(recorder.calls.map(c => c.verb), ["record-salesforce-page-stop"]);
  assert.equal(out.findings_recorded, 0);
}

// ---------------------------------------------------------------------------
// Checks added for the independent review of PR #1320.
// ---------------------------------------------------------------------------

const run = (mod, pages, { recorder = new SpyRecorder(), carrDeals = [], clock } = {}) =>
  mod.runSalesforceBrowserReadReconciliation({ reader: new FakeReaderDriver(pages),
    bindingSource: bindingSource(), carrDeals: async () => carrDeals, recorder,
    ...(clock ? { clock } : {}) });

/** Reviewer M1: a non-boolean has_next is a malformed page, never the end of the list. */
export async function checkHasNextStrict(mod) {
  for (const bad of [undefined, "no", 0, null]) {
    const recorder = new SpyRecorder();
    const pages = [{ ...page({ opportunities: [opp("A")] }), has_next: bad }];
    if (bad === undefined) delete pages[0].has_next;
    await assert.rejects(run(mod, pages, { recorder, carrDeals: [carr("A"), carr("Q")] }),
      e => e.code === "invalid_shape", String(bad));
    assert.equal(recorder.calls.length, 0, "nothing is concluded from a malformed page");
  }
}

/** Reviewer M4: two opportunities with the same name are two findings with two keys. */
export async function checkKeysDistinctPerOpportunity(mod) {
  const recorder = new SpyRecorder();
  await run(mod, [page({ opportunities: [opp("A", { name: "Same Name" }), opp("B", { name: "Same Name" })] })],
    { recorder, carrDeals: [carr("A"), carr("B")] });
  const keys = recorder.calls.filter(c => c.verb === "add-loop").map(c => c.args.idempotency_key);
  assert.equal(keys.length, 2);
  assert.equal(new Set(keys).size, 2);
  assert.equal(recorder.writes, 2);
}

/** Reviewer M5: a credential-shaped name is refused, not recorded. */
export async function checkCredentialTextRefused(mod) {
  for (const name of ["password=hunter2", "Bearer abcdefghijklmnop", "sk-abcdefghijklmnopqrstu"]) {
    assert.throws(() => mod.reconcileSalesforceDeals({ joeUserRef: "joe-sf", complete: true,
      salesforce: [opp("A", { name })], carr: [] }), e => e.code === "credential_shaped_value", name);
  }
}

/** Reviewer M8: the cap is exact. CAP pages ending the list reconcile; more stops after CAP reads. */
export async function checkPageCapExact(mod) {
  const cap = mod.V5_RW02_READ_PAGE_CAP;
  const exact = Array.from({ length: cap }, (_, i) => page({ has_next: i < cap - 1,
    opportunities: i === 0 ? [opp("A", { team: ["joe-sf"] })] : [] }));
  const ok = await run(mod, exact, { carrDeals: [carr("A")] });
  assert.equal(ok.decision, "reconciled");
  assert.equal(ok.pages_read, cap);
  const driver = new FakeReaderDriver(Array.from({ length: cap + 2 }, () => page({ has_next: true })));
  const over = await mod.runSalesforceBrowserReadReconciliation({ reader: driver,
    bindingSource: bindingSource(), carrDeals: async () => [], recorder: new SpyRecorder() });
  assert.equal(over.reason_id, "page_cap_reached");
  assert.equal(driver.readCalls.filter(x => x === "observe").length, cap);
}

/** Reviewer M9: the facade hands the driver no arguments. */
export async function checkFacadeForwardsNoArgs(mod) {
  const driver = new FakeReaderDriver([page()]);
  const facade = mod.readOnlyReader(driver);
  await facade.observe({ command: "saveOpportunity" }, "x");
  await facade.nextPage("click:Save");
  assert.deepEqual(driver.argCounts, [0, 0]);
}

/** Reviewer M10: credential-named keys are refused BY NAME, not as a generic unknown field. */
export async function checkCredentialKeyRefusedByName(mod) {
  for (const key of ["password", "code", "otp", "username", "value", "sms_code", "passcode"]) {
    assert.throws(() => mod.classifySignIn({ state: "code_prompt", [key]: "x" }, { systemStartedSignIn: false }),
      e => e.code === "credential_field_refused", key);
  }
}

/**
 * Reviewer M11 and finding 1: a repeat run (another time, renamed opportunities)
 * sends BYTE-IDENTICAL arguments for the same findings, so withEnvelope
 * replays rather than refusing key_reuse, and nothing is filed twice.
 */
export async function checkRepeatRunArgsIdentical(mod) {
  const recorder = new SpyRecorder();
  const carrDeals = [carr("A"), carr("Q"),
    { deal_id: "00000000-0000-4000-8000-00000000000f", name: "Unlinked", salesforce_id: null }];
  const first = await run(mod, [page({ opportunities: [opp("A"), opp("N", { name: "Old name" })] })],
    { recorder, carrDeals, clock: () => "2026-09-26T12:00:00.000Z" });
  const firstArgs = recorder.calls.map(c => JSON.stringify(c));
  const second = await run(mod, [page({ opportunities: [opp("A", { name: "Renamed A" }),
    opp("N", { name: "New name" })] })], { recorder, carrDeals, clock: () => "2026-09-27T08:30:00.000Z" });
  assert.notEqual(first.run_ref, second.run_ref);
  const secondArgs = recorder.calls.slice(firstArgs.length).map(c => JSON.stringify(c));
  assert.equal(firstArgs.length, 5);
  assert.deepEqual(secondArgs, firstArgs, "repeat-run arguments must be byte-identical");
  assert.equal(recorder.writes, 5, "the repeat run filed nothing new");
}

/** Finding 5: an empty complete read concludes no absence. */
export async function checkEmptyReadInconclusive(mod) {
  const recorder = new SpyRecorder();
  const out = await run(mod, [page({ opportunities: [], has_next: false })],
    { recorder, carrDeals: [carr("A"), carr("Q")] });
  assert.equal(out.decision, "reconciled");
  assert.equal(out.counts.absent_from_salesforce, 0);
  assert.equal(out.reconciliation.absence_concluded, false);
  assert.equal(recorder.calls.length, 0);
}

/** Finding 3: a getter that answers differently on a second read cannot smuggle a value. */
export async function checkGetterSwapHarmless(mod) {
  const evil = "006‮evil\npassword=hunter2";
  const swapping = () => {
    let reads = 0; let teamReads = 0;
    const o = { name: "Deal S", owner_ref: "dell-sf" };
    Object.defineProperty(o, "opportunity_id", { enumerable: true,
      get: () => (reads++ === 0 ? oppId("S") : evil) });
    Object.defineProperty(o, "team_member_refs", { enumerable: true,
      get: () => (teamReads++ === 0 ? [] : ["bad ref\npassword=hunter2"]) });
    return o;
  };
  const r = mod.reconcileSalesforceDeals({ joeUserRef: "joe-sf", complete: true,
    salesforce: [swapping()], carr: [] });
  assert.deepEqual(r.missing_joe.map(f => f.opportunity_id), [oppId("S")]);
  const recorder = new SpyRecorder();
  await run(mod, [() => ({ ...page(), opportunities: [swapping()] })], { recorder });
  const text = JSON.stringify(recorder.calls);
  assert.ok(!text.includes("hunter2") && !text.includes("evil"), "the swapped value never reaches a record");
  assert.ok(text.includes(oppId("S")));
}
