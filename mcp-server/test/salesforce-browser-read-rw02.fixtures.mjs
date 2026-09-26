// Shared synthetic fixtures and property checks for the V5-RW02 browser read
// adapter. Each check takes a MODULE so the mutant suite runs the very same
// check against a planted-bug copy and requires it to fail there.
// Synthetic data only: no real org, seat, deal or person identifier.

import assert from "node:assert/strict";

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
  constructor(pages) { this.pages = pages; this.index = 0; this.readCalls = []; this.writeCalls = 0; }
  async observe() { this.readCalls.push("observe"); return structuredClone(this.pages[this.index]); }
  async nextPage() { this.readCalls.push("nextPage"); this.index++; }
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

export class SpyRecorder {
  constructor() { this.calls = []; }
  async record(verb, args) { this.calls.push({ verb, args: structuredClone(args) }); return { ok: true }; }
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
  const a = await mod.attemptCodeAutofill({ driver, signIn: { started_by_system: true } });
  assert.equal(a.decision, "stop");
  assert.equal(a.reason_id, "code_autofill_not_filled");
  assert.equal(a.attempts, 3);
  assert.equal(driver.log.filter(x => x === "clickCodeField").length, 3);
  assert.equal(a.resolution_owner, "partner_at_the_browser");
  // And a fill on the third attempt is still a fill.
  const late = new FakeCodeDriver({ fillOn: [3] });
  assert.equal((await mod.attemptCodeAutofill({ driver: late, signIn: { started_by_system: true } })).attempts, 3);
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
  for (const signIn of [{ started_by_system: false }, {}, { started_by_system: "yes" }]) {
    const driver = new FakeCodeDriver({ fillOn: [1] });
    const a = await mod.attemptCodeAutofill({ driver, signIn });
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
