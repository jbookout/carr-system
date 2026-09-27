// DoctorCRE v5 V5-RW02 — the browser-only Salesforce READ adapter and the
// day-to-day reconciliation pass, READ-ONLY. No verb, no route, no schema.
//
// FIVE RULINGS OF 2026-09-26 SHAPE THIS FILE, read fresh from the record layer:
//
//   c04ac197  Dell's Salesforce is the primary source. A deal there without Joe
//             on it becomes an action to get Joe added, never a silent skip.
//   c0014a37  No continuous field sync. Day to day, reconciliation checks only
//             deal PRESENCE and PARTNER MEMBERSHIP. Field values are a
//             pre-invoice step, and that step is not in this file.
//   493de438  A kind of update turns autonomous only after 5 CONSECUTIVE clean
//             attended runs of that kind; any unclean run resets the count.
//             Here that is data plus a pure function. It promotes nothing.
//   9b2e2011  Browser only. The adapter drives a browser session a partner has
//             already signed in to. No API, connector or MCP route.
//   9ea10e5b  Sign-in: the system never types, reads, copies, logs or stores a
//   4e1efae4  password or a code. An unprompted code prompt, an autofill that
//             does not fill, or a security challenge stops the run for the
//             partner. Dell's accounts need Dell's own OK.
//
// READ MODE IS STRUCTURAL, NOT A FLAG. The runner receives the browser driver
// only through readOnlyReader(), a frozen null-prototype object holding exactly
// two functions (observe, nextPage). Whatever else the driver object carries is
// not reachable from here. The runner writes to the record layer only through
// readModeRecorder(), which admits exactly three existing verbs
// (record-salesforce-page-stop, record-finding, add-loop) and refuses every
// other name. There is no writer interface, no write mode and no option that
// names one: an unknown runner option is refused.
//
// IDENTITY IS NOT AN ARGUMENT. The org, origin, signed-in seat, Joe's Salesforce
// user ref, the source partner and Dell's consent come from a server-side
// binding source, never from the caller. The acting principal is derived by the
// record verbs themselves (the RW02 store checks the database principal against
// the handler actor). Until a real binding source exists, the production
// default is unavailableBindingSource and every run refuses, naming the seam.
//
// SIGN-IN IS STOPS ONLY. This slice never presses Log in, so it never starts a
// sign-in, so every code prompt it sees is unprompted. attemptCodeAutofill() is
// the bounded code step for a LATER slice's system-started sign-in (Joe's
// refinement: click the field, click the From-Messages suggestion if shown,
// check filled; at most 3 attempts PER SIGN-IN, then stop). It runs only on a
// module-private, single-use, time-limited ticket minted by beginSystemSignIn;
// a caller-built object is not a ticket. The driver reports filled/not-filled,
// never a value.
//
// REPEAT RUNS ARE REPLAYS. Every finding is keyed on org + opportunity (or
// deal + reason) and carries no run-specific or renamable text, so a repeat
// run sends byte-identical arguments and the verb envelope replays instead of
// refusing key_reuse or filing a duplicate.

import { digest } from "./artifact-trust.js";
import { V5_NO_EFFECTS } from "./global-boundaries.v5.js";
import {
  V5_RW02_ACTION_KIND_KEYS,
  V5_RW02_CREDENTIAL_PATTERNS,
  V5_RW02_ORG_BINDING_SEAM,
  evaluatePageObservation,
} from "./salesforce-reconciliation-rw02.v5.js";

export const V5_RW02_BROWSER_READ_SCHEMA_VERSION = "doctorcre-v5-rw02-browser-read.v1";

/** The ONLY driver methods read mode can reach. */
export const V5_RW02_READER_METHODS = Object.freeze(["nextPage", "observe"]);

/** The ONLY record-layer verbs read mode can call. All three exist already. */
export const V5_RW02_READ_MODE_RECORD_VERBS = Object.freeze([
  "add-loop", "record-finding", "record-salesforce-page-stop",
]);

/** Decision 493de438. */
export const V5_RW02_AUTONOMY_THRESHOLD = 5;
export const V5_RW02_RUN_OUTCOMES = Object.freeze(["clean", "corrected", "failed", "refused", "stopped"]);

/** Joe's code-step refinement: at most three attempts per sign-in. */
export const V5_RW02_CODE_AUTOFILL_MAX_ATTEMPTS = 3;

/** A read that has not ended by this page is not trusted to be a whole list. */
export const V5_RW02_READ_PAGE_CAP = 50;

export const V5_RW02_SIGN_IN_STATES = Object.freeze([
  "signed_in", "password_prompt", "code_prompt", "security_challenge", "unknown",
]);

export const V5_RW02_SIGN_IN_STOP_REASONS = Object.freeze([
  "code_autofill_not_filled",
  "code_fill_state_unobservable",
  "partner_sign_in_required",
  "password_prompt_without_autofill",
  "security_challenge",
  "sign_in_state_unobservable",
  "sign_in_ticket_spent",
  "sign_in_ticket_stale",
  "unprompted_code_prompt",
]);

/** Sign-in state -> the kernel challenge the page stop is recorded under. */
const KERNEL_CHALLENGE = Object.freeze({
  password_prompt: "login_required",
  code_prompt: "mfa_challenge",
  security_challenge: "captcha",
  unknown: "unstated",
});

const OPPORTUNITY_ID = /^006[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?$/;
const STABLE_ID = /^[A-Za-z0-9][A-Za-z0-9._:+-]{0,255}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const UNSAFE_TEXT = /[\u0000-\u001F\u007F-\u009F​-‏‪-‮⁠-⁤⁦-⁩﻿]/u;
const CREDENTIAL_KEYS = /^(?:password|passwd|pwd|passcode|code|otp|sms_code|mfa_code|token|secret|username|user_name|login|value|credential)s?$/i;

export class V5RW02BrowserReadError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.name = "V5RW02BrowserReadError";
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, message, detail) { throw new V5RW02BrowserReadError(code, message, detail); }

function plain(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}
function object(value, path) {
  if (!plain(value)) fail("invalid_shape", `${path} must be a plain object`, { path });
  return value;
}
function closed(value, allowed, path) {
  const raw = object(value, path);
  for (const key of Object.keys(raw)) {
    if (allowed.includes(key)) continue;
    if (CREDENTIAL_KEYS.test(key)) fail("credential_field_refused",
      `${path}.${key} would carry a credential; this adapter never holds one`, { path: `${path}.${key}` });
    fail("unknown_field", `unknown field ${path}.${key}`, { path, allowed });
  }
  return raw;
}
function freeze(value) {
  if (Array.isArray(value)) { value.forEach(freeze); return Object.freeze(value); }
  if (plain(value)) { Object.values(value).forEach(freeze); return Object.freeze(value); }
  return value;
}
function stableId(value, path) {
  if (typeof value !== "string" || !STABLE_ID.test(value)) fail("invalid_id", `${path} must be a stable id`, { path });
  return value;
}
function safeText(value, path, max = 512) {
  if (typeof value !== "string" || !value.trim() || value.length > max || UNSAFE_TEXT.test(value))
    fail("invalid_text", `${path} must be 1-${max} characters of plain text`, { path });
  for (const pattern of Object.values(V5_RW02_CREDENTIAL_PATTERNS))
    if (pattern.test(value)) fail("credential_shaped_value", `${path} carries a credential shape`, { path });
  return value;
}

const EFFECTS = freeze({ ...V5_NO_EFFECTS, salesforce_writes: 0 });

// ---------------------------------------------------------------------------
// 1. The adapter contract: a read-only facade over a browser driver.
// ---------------------------------------------------------------------------

/**
 * The typed contract a browser driver fulfils for READ:
 *
 *   observe()  -> { sign_in: { state, credentials_autofilled? },
 *                   page: <kernel page observation: origin, org_id,
 *                          signed_in_account_ref, ui_contract_digest,
 *                          challenge, result_consistency, ...>,
 *                   opportunities: [{ opportunity_id, name, owner_ref,
 *                                     team_member_refs[] }],
 *                   has_next: boolean }
 *   nextPage() -> moves the SAME list view to its next page.
 *
 * Returns a frozen, prototype-less object holding exactly those two functions.
 * Each calls the driver's own method with the driver as `this`; nothing else on
 * the driver can be named through it.
 */
export function readOnlyReader(driver) {
  if (driver === null || typeof driver !== "object") fail("reader_unavailable", "a browser driver is required");
  const facade = Object.create(null);
  for (const name of V5_RW02_READER_METHODS) {
    const method = driver[name];
    if (typeof method !== "function") fail("reader_incomplete", `the driver has no ${name}()`, { method: name });
    // No arguments pass through: the runner cannot hand the driver a command.
    facade[name] = () => method.call(driver);
  }
  return Object.freeze(facade);
}

/** A recorder that can reach exactly the three read-mode verbs. */
export function readModeRecorder(recorder) {
  if (!recorder || typeof recorder.record !== "function") fail("recorder_unavailable", "a verb recorder is required");
  const facade = Object.create(null);
  facade.record = async (verb, args) => {
    if (!V5_RW02_READ_MODE_RECORD_VERBS.includes(verb)) fail("verb_not_permitted_in_read_mode",
      `read mode may not call ${String(verb)}`, { verb: String(verb), allowed: [...V5_RW02_READ_MODE_RECORD_VERBS] });
    return recorder.record(verb, args);
  };
  return Object.freeze(facade);
}

/**
 * The production recorder: each call goes through the SAME registered verb a
 * session would use, so the verb derives the actor and tenant itself.
 * `callVerb(name, args)` is e.g. executeRegisteredTool bound to (client, actor).
 */
export function createVerbRecorder({ callVerb } = {}) {
  if (typeof callVerb !== "function") fail("recorder_unavailable", "callVerb is required");
  return readModeRecorder({ record: (verb, args) => callVerb(verb, args) });
}

/** The production binding source until the org/seat binding seam is filled. */
export const unavailableBindingSource = Object.freeze({ read: async () => null });

/** Open CARR deals, read through the narrow reconciliation view (0091). */
export async function readCarrDealsForReconciliation(client) {
  const r = await client.query(
    `select id, name, salesforce_id from v_deal_reconciliation_read
      where outcome is null and closed_on is null order by id`);
  return r.rows.map(row => ({ deal_id: row.id, name: row.name, salesforce_id: row.salesforce_id ?? null }));
}

// ---------------------------------------------------------------------------
// 2. Sign-in: typed stops only. No credential entry of any kind.
// ---------------------------------------------------------------------------

function signInStop(reason_id, detail = {}) {
  return freeze({ answer_kind: "rw02-sign-in.v1", decision: "stop", reason_id, ...detail,
    resolution_owner: "partner_at_the_browser", bypass_permitted: false,
    automatic_retry_permitted: false, credential_entry_performed: false, effects: EFFECTS });
}

/**
 * Classify a sign-in observation. `systemStartedSignIn` is the RUNNER's own
 * state, never a driver or caller claim; this slice never starts a sign-in.
 */
export function classifySignIn(signIn, { systemStartedSignIn = false } = {}) {
  const raw = closed(signIn, ["state", "credentials_autofilled"], "sign_in");
  if (!V5_RW02_SIGN_IN_STATES.includes(raw.state)) return signInStop("sign_in_state_unobservable");
  if (raw.state === "signed_in") return freeze({ answer_kind: "rw02-sign-in.v1", decision: "continue",
    reason_id: "signed_in", credential_entry_performed: false, effects: EFFECTS });
  if (raw.state === "security_challenge") return signInStop("security_challenge");
  if (raw.state === "unknown") return signInStop("sign_in_state_unobservable");
  if (raw.state === "code_prompt") {
    if (systemStartedSignIn !== true) return signInStop("unprompted_code_prompt");
    // A system-started sign-in may try the bounded code step (attemptCodeAutofill).
    return freeze({ answer_kind: "rw02-sign-in.v1", decision: "code_autofill_permitted",
      reason_id: "system_started_sign_in", max_attempts: V5_RW02_CODE_AUTOFILL_MAX_ATTEMPTS,
      credential_entry_performed: false, effects: EFFECTS });
  }
  // password_prompt
  if (raw.credentials_autofilled !== true) return signInStop("password_prompt_without_autofill");
  // Ruling 9ea10e5b permits pressing Log in on an autofilled sign-in; this slice
  // does not implement that press, so the partner signs in.
  return signInStop("partner_sign_in_required", { permitted_later_by: "9ea10e5b" });
}

/** How long after a system-started sign-in the code step may still run. */
export const V5_RW02_SIGN_IN_TICKET_TTL_MS = 120_000;

// Module-private ledger of sign-in tickets. A ticket is an opaque frozen object;
// its state (mint time, attempts used, spent) lives here, where no caller can
// reach it, so a copied or hand-built object is not a ticket.
const SIGN_IN_TICKETS = new WeakMap();

/**
 * Mint the ticket for ONE sign-in the system itself starts. Minting a ticket IS
 * starting a sign-in: the later slice that presses Log in (ruling 9ea10e5b) is
 * the only caller this is for. The read run in this slice never calls it.
 */
export function beginSystemSignIn({ nowMs } = {}) {
  if (!Number.isSafeInteger(nowMs) || nowMs < 0) fail("invalid_clock", "nowMs must be an integer millisecond time");
  const ticket = Object.freeze(Object.create(null));
  SIGN_IN_TICKETS.set(ticket, { minted_at_ms: nowMs, attempts_used: 0, spent: false });
  return ticket;
}

/**
 * The bounded code step (decision 4e1efae4 plus Joe's refinement). The driver
 * reports only booleans; there is no method that returns a code.
 *
 *   clickCodeField(); if autofillSuggestionVisible() then clickAutofillSuggestion();
 *   codeFieldFilled() === true -> filled.
 *
 * At most 3 attempts PER SIGN-IN: the count lives in the ticket's private state
 * and the ticket is spent by its first use, so a second call clicks nothing.
 * A forged, reused or stale ticket is an unprompted code prompt: a stop.
 */
export async function attemptCodeAutofill({ driver, ticket, nowMs } = {}) {
  const state = ticket !== null && typeof ticket === "object" ? SIGN_IN_TICKETS.get(ticket) : undefined;
  if (!state) return signInStop("unprompted_code_prompt");
  if (state.spent) return signInStop("sign_in_ticket_spent", { attempts: state.attempts_used });
  state.spent = true;
  if (!Number.isSafeInteger(nowMs) || nowMs < state.minted_at_ms ||
      nowMs - state.minted_at_ms > V5_RW02_SIGN_IN_TICKET_TTL_MS)
    return signInStop("sign_in_ticket_stale");
  if (!driver) fail("code_driver_unavailable", "a code-step driver is required");
  for (const name of ["clickCodeField", "autofillSuggestionVisible", "clickAutofillSuggestion", "codeFieldFilled"])
    if (typeof driver[name] !== "function") fail("code_driver_incomplete", `the driver has no ${name}()`);
  while (state.attempts_used < V5_RW02_CODE_AUTOFILL_MAX_ATTEMPTS) {
    const attempt = ++state.attempts_used;
    await driver.clickCodeField();
    const suggestion = await driver.autofillSuggestionVisible();
    if (suggestion === true) await driver.clickAutofillSuggestion();
    else if (suggestion !== false) return signInStop("code_fill_state_unobservable", { attempts: attempt });
    const filled = await driver.codeFieldFilled();
    if (filled === true) return freeze({ answer_kind: "rw02-code-autofill.v1", decision: "filled",
      reason_id: "os_autofill_filled", attempts: attempt, credential_entry_performed: false, effects: EFFECTS });
    if (filled !== false) return signInStop("code_fill_state_unobservable", { attempts: attempt });
  }
  return signInStop("code_autofill_not_filled", { attempts: state.attempts_used });
}

// ---------------------------------------------------------------------------
// 3. Reconciliation: presence and partner membership only (c04ac197, c0014a37).
// ---------------------------------------------------------------------------

function normalizeOpportunity(value, path) {
  // Callers hand in a structuredClone, so every field below is plain data read
  // exactly once into a local and validated as that local (no getter can swap
  // a value between the check and the use).
  const raw = closed(value, ["opportunity_id", "name", "owner_ref", "team_member_refs"], path);
  const { opportunity_id, name, owner_ref, team_member_refs } = raw;
  if (typeof opportunity_id !== "string" || !OPPORTUNITY_ID.test(opportunity_id))
    fail("invalid_opportunity_id", `${path}.opportunity_id is not an opportunity id`, { path });
  if (!Array.isArray(team_member_refs) || team_member_refs.length > 64)
    fail("invalid_shape", `${path}.team_member_refs must be a list`, { path });
  const team = [...team_member_refs];
  return {
    opportunity_id,
    name: safeText(name, `${path}.name`),
    owner_ref: stableId(owner_ref, `${path}.owner_ref`),
    team_member_refs: team.map((r, i) => stableId(r, `${path}.team_member_refs[${i}]`)).sort(),
  };
}

function normalizeCarrDeal(value, path) {
  const { deal_id, name, salesforce_id } = closed(value, ["deal_id", "name", "salesforce_id"], path);
  if (typeof deal_id !== "string" || !UUID.test(deal_id)) fail("invalid_deal_id", `${path}.deal_id`, { path });
  const sf = salesforce_id ?? null;
  if (sf !== null && (typeof sf !== "string" || !OPPORTUNITY_ID.test(sf)))
    fail("invalid_opportunity_id", `${path}.salesforce_id`, { path });
  return { deal_id, name: safeText(name, `${path}.name`), salesforce_id: sf };
}

/**
 * Pure. Compares opportunities read from Dell's org with CARR's open deals, by
 * opportunity id ONLY (a name match is a human question, never a join).
 * `complete` says the read reached the list's end; without it nothing is
 * concluded ABSENT, because an unread page is not an empty one.
 */
export function reconcileSalesforceDeals({ salesforce, carr, joeUserRef, complete } = {}) {
  stableId(joeUserRef, "joeUserRef");
  if (typeof complete !== "boolean") fail("invalid_shape", "complete must be a boolean");
  if (!Array.isArray(salesforce) || !Array.isArray(carr)) fail("invalid_shape", "salesforce and carr are lists");
  // ONE read of every input: structuredClone evaluates each getter once and
  // yields plain data, so what is validated is exactly what is used.
  let sfCopy, carrCopy;
  try { sfCopy = structuredClone(salesforce); carrCopy = structuredClone(carr); }
  catch { fail("invalid_shape", "reconciliation inputs must be plain data"); }
  const seen = new Map();
  sfCopy.forEach((value, i) => {
    const o = normalizeOpportunity(value, `salesforce[${i}]`);
    const prior = seen.get(o.opportunity_id);
    if (prior && digest(prior) !== digest(o)) fail("inconsistent_result",
      "one opportunity was read twice with different facts", { opportunity_id: o.opportunity_id });
    seen.set(o.opportunity_id, o);
  });
  const deals = carrCopy.map((d, i) => normalizeCarrDeal(d, `carr[${i}]`));
  const bySf = new Map(deals.filter(d => d.salesforce_id).map(d => [d.salesforce_id, d]));
  const opportunities = [...seen.values()].sort((a, b) => a.opportunity_id.localeCompare(b.opportunity_id));

  const missing_joe = opportunities
    .filter(o => o.owner_ref !== joeUserRef && !o.team_member_refs.includes(joeUserRef))
    .map(o => ({ opportunity_id: o.opportunity_id, name: o.name,
      carr_deal_id: bySf.get(o.opportunity_id)?.deal_id ?? null, action: "get_joe_added_to_deal" }));
  const unknown_to_carr = opportunities.filter(o => !bySf.has(o.opportunity_id))
    .map(o => ({ opportunity_id: o.opportunity_id, name: o.name }));
  // A read that reached the end but saw NO opportunity is inconclusive (an
  // empty list view, a filter, a render failure): it proves nothing absent.
  const concluded = complete && opportunities.length > 0;
  const absent_from_salesforce = !concluded ? [] : deals
    .filter(d => !d.salesforce_id || !seen.has(d.salesforce_id))
    .map(d => ({ deal_id: d.deal_id, name: d.name, salesforce_id: d.salesforce_id,
      reason_id: d.salesforce_id ? "salesforce_id_not_seen" : "no_salesforce_link" }));

  return freeze({ schema_version: V5_RW02_BROWSER_READ_SCHEMA_VERSION, answer_kind: "rw02-reconciliation.v1",
    opportunities_read: opportunities.length, carr_deals_compared: deals.length,
    missing_joe, unknown_to_carr, absent_from_salesforce, absence_concluded: concluded,
    absence_scope: "open_deals_only_no_invoiced_marker_exposed",
    field_sync: "not_performed_presence_and_membership_only", effects: EFFECTS });
}

// ---------------------------------------------------------------------------
// 4. The 5-consecutive-clean counter (493de438). Data + a pure function.
// ---------------------------------------------------------------------------

/**
 * `runs` is the ordered attended-run history (oldest first). Only runs of
 * `action_kind` count; another kind's runs neither count nor reset (no trust
 * inheritance). Any unclean run of the kind resets the count to zero.
 * Meeting the threshold makes a kind ELIGIBLE for a human promotion review;
 * nothing here promotes it.
 */
export function evaluateAutonomyCounter({ action_kind, runs } = {}) {
  if (!V5_RW02_ACTION_KIND_KEYS.includes(action_kind))
    fail("unknown_action_kind", "action_kind is not a registered RW02 kind", { action_kind });
  if (!Array.isArray(runs)) fail("invalid_shape", "runs must be a list");
  const refs = new Set();
  let consecutive_clean = 0;
  let last_reset_run_ref = null;
  runs.forEach((value, i) => {
    const r = closed(value, ["run_ref", "action_kind", "outcome", "execution_mode"], `runs[${i}]`);
    stableId(r.run_ref, `runs[${i}].run_ref`);
    if (refs.has(r.run_ref)) fail("run_counted_twice", "a run may be counted once", { run_ref: r.run_ref });
    refs.add(r.run_ref);
    if (r.execution_mode !== "attended") fail("unattended_run_refused",
      "only attended runs can count toward autonomy", { run_ref: r.run_ref });
    if (!V5_RW02_RUN_OUTCOMES.includes(r.outcome)) fail("unknown_outcome",
      "outcome is not a registered run outcome", { run_ref: r.run_ref });
    if (r.action_kind !== action_kind) return;
    if (r.outcome === "clean") consecutive_clean += 1;
    else { consecutive_clean = 0; last_reset_run_ref = r.run_ref; }
  });
  return freeze({ answer_kind: "rw02-autonomy-counter.v1", action_kind, consecutive_clean,
    threshold: V5_RW02_AUTONOMY_THRESHOLD, threshold_met: consecutive_clean >= V5_RW02_AUTONOMY_THRESHOLD,
    last_reset_run_ref, decision_ref: "493de438", promotion: "not_performed_in_this_slice",
    autonomy_active: false, outward_effect_granted: false, effects: EFFECTS });
}

// ---------------------------------------------------------------------------
// 5. The read run.
// ---------------------------------------------------------------------------

const RUN_OPTIONS = Object.freeze(["reader", "bindingSource", "carrDeals", "recorder", "clock"]);

function refused(reason_id, detail = {}) {
  return freeze({ schema_version: V5_RW02_BROWSER_READ_SCHEMA_VERSION, decision: "refused", reason_id,
    ...detail, salesforce_writes: 0, findings_recorded: 0, effects: EFFECTS });
}

function normalizeBinding(raw) {
  if (raw === null || raw === undefined) return { refusal: refused("org_binding_unavailable",
    { seam: V5_RW02_ORG_BINDING_SEAM }) };
  const b = closed(raw, ["source_partner", "org", "ui_contract_digest", "joe_user_ref", "dell_consent"], "binding");
  if (b.source_partner !== "dell") return { refusal: refused("source_not_dell", { decision_ref: "c04ac197" }) };
  if (!plain(b.dell_consent) || b.dell_consent.granted !== true || typeof b.dell_consent.decision_ref !== "string")
    return { refusal: refused("dell_consent_absent", { decision_ref: "9ea10e5b" }) };
  const org = closed(b.org, ["origin", "org_id", "account_ref"], "binding.org");
  return { binding: { org: { origin: org.origin, org_id: stableId(org.org_id, "binding.org.org_id"),
    account_ref: stableId(org.account_ref, "binding.org.account_ref") },
  ui_contract_digest: b.ui_contract_digest, joe_user_ref: stableId(b.joe_user_ref, "binding.joe_user_ref") } };
}

function kernelPage(binding, pageObservation, challengeOverride) {
  const observation = { ...object(pageObservation, "observe().page") };
  if (challengeOverride) observation.challenge = challengeOverride;
  return { execution_mode: "attended",
    binding: { expected_origin: binding.org.origin, expected_org_id: binding.org.org_id,
      expected_account_ref: binding.org.account_ref, expected_ui_contract_digest: binding.ui_contract_digest },
    observation };
}

/**
 * One attended READ of Dell's opportunity list, page by page, then the
 * reconciliation pass. Every page runs the kernel stop ladder; a stopping page
 * is recorded through record-salesforce-page-stop and ends the run with NO
 * findings (a partial read concludes nothing). A complete read records:
 *   missing Joe      -> add-loop team_loop for Dell ("Add Joe to <deal>")
 *   unknown to CARR  -> add-loop open_loop for Joe (new-deal is humanOnly)
 *   absent from SF   -> record-finding found:false on the CARR deal
 */
export async function runSalesforceBrowserReadReconciliation(options = {}) {
  object(options, "options");
  const unknown = Object.keys(options).filter(k => !RUN_OPTIONS.includes(k));
  if (unknown.length) fail("unknown_option",
    "the read run takes no mode, writer, partner, org, tenant or capability option", { unknown });
  const { bindingSource, carrDeals, clock = () => new Date().toISOString() } = options;
  const recorder = readModeRecorder(options.recorder);
  if (!bindingSource || typeof bindingSource.read !== "function") fail("binding_source_unavailable",
    "a server-side binding source is required");
  if (typeof carrDeals !== "function") fail("carr_source_unavailable", "a CARR deal source is required");

  const { binding, refusal } = normalizeBinding(await bindingSource.read());
  if (refusal) return refusal;
  const reader = readOnlyReader(options.reader);
  const started_at = clock();
  const run_ref = digest({ kind: "rw02-browser-read-run.v1", org: binding.org, started_at }).slice(7, 31);

  const pages = [];
  const opportunities = [];
  for (let index = 0; ; index++) {
    if (index >= V5_RW02_READ_PAGE_CAP) return freeze({ ...refused("page_cap_reached"),
      decision: "stopped", run_ref, pages_read: index });
    // ONE read of the page: a plain-data copy, so no getter can answer the
    // validation differently from the use.
    let snapshot;
    try { snapshot = structuredClone(await reader.observe()); }
    catch { fail("invalid_shape", "observe() must return plain data"); }
    object(snapshot, "observe()");
    closed(snapshot, ["sign_in", "page", "opportunities", "has_next"], "observe()");
    const signIn = classifySignIn(snapshot.sign_in, { systemStartedSignIn: false });
    const challenge = signIn.decision === "continue" ? null
      : KERNEL_CHALLENGE[snapshot.sign_in.state] ?? "unstated";
    const kernel = kernelPage(binding, snapshot.page, challenge);
    const verdict = evaluatePageObservation(kernel);
    if (signIn.decision !== "continue" || verdict.decision !== "continue") {
      const reason_id = signIn.decision !== "continue" ? signIn.reason_id : verdict.reason_id;
      await recorder.record("record-salesforce-page-stop",
        { idempotency_key: `rw02-read:${run_ref}:page-stop:${index}`, page: kernel });
      return freeze({ schema_version: V5_RW02_BROWSER_READ_SCHEMA_VERSION, decision: "stopped",
        reason_id, run_ref, pages_read: index,
        page_stop: { page_index: index, kernel_reason_id: verdict.reason_id, challenge: kernel.observation.challenge },
        resolution_owner: "partner_at_the_browser", salesforce_writes: 0, findings_recorded: 0, effects: EFFECTS });
    }
    if (!Array.isArray(snapshot.opportunities)) fail("invalid_shape", "observe().opportunities must be a list");
    opportunities.push(...snapshot.opportunities);
    pages.push({ page_index: index, evidence_digest: digest(verdict), opportunities: snapshot.opportunities.length });
    const hasNext = snapshot.has_next;
    if (hasNext !== true && hasNext !== false) fail("invalid_shape", "observe().has_next must be a boolean");
    if (hasNext === false) break;
    await reader.nextPage();
  }

  const result = reconcileSalesforceDeals({ salesforce: opportunities, carr: await carrDeals(),
    joeUserRef: binding.joe_user_ref, complete: true });
  // EVERY argument below is identical on a repeat run for the same finding:
  // withEnvelope hashes the arguments and refuses a known key that arrives
  // with different ones (key_reuse). So nothing run-specific (run_ref, time)
  // and nothing volatile (the opportunity NAME, which partners rename) goes
  // into a key, a title, a body or a source. Keys bind the org and the id.
  const org_id = binding.org.org_id;
  const key = (kind, id, extra = null) =>
    `rw02-${kind}:${digest({ kind, org_id, id, extra }).slice(7, 39)}`;
  const source_note = "V5-RW02 attended browser read of Dell's Salesforce (decisions c04ac197, c0014a37)";
  let findings_recorded = 0;
  for (const f of result.missing_joe) {
    await recorder.record("add-loop", {
      idempotency_key: key("missing-joe", f.opportunity_id),
      kind: "team_loop", owner: "Dell", domain: "deals",
      title: `Add Joe to Salesforce opportunity ${f.opportunity_id}`,
      body: `Joe is not the owner or a team member on opportunity ${f.opportunity_id} in Dell's Salesforce.`,
      unblocks: "Salesforce reconciliation (decision c04ac197): Joe on every deal",
      source_note });
    findings_recorded++;
  }
  for (const f of result.unknown_to_carr) {
    await recorder.record("add-loop", {
      idempotency_key: key("unknown-to-carr", f.opportunity_id),
      kind: "open_loop", owner: "Joe", domain: "deals",
      body: `Salesforce opportunity ${f.opportunity_id} in Dell's org has no CARR deal.`,
      blocker: "human_only",
      blocker_detail: `Joe or Dell decides whether ${f.opportunity_id} becomes a CARR deal; creating a deal is humanOnly`,
      source_note });
    findings_recorded++;
  }
  for (const f of result.absent_from_salesforce) {
    // record-finding keeps no value when found is false, so the reason rides
    // in `kind` and `source`, both of which the row keeps. Keyed per deal and
    // reason: a repeat run replays the first row instead of adding one.
    await recorder.record("record-finding", {
      idempotency_key: key("absent", f.deal_id, f.reason_id), subject: f.deal_id,
      kind: `salesforce_presence:${f.reason_id}`, found: false, internal: true, epistemic_status: "observed",
      source: `${source_note}; reason ${f.reason_id}` });
    findings_recorded++;
  }
  return freeze({ schema_version: V5_RW02_BROWSER_READ_SCHEMA_VERSION, decision: "reconciled", run_ref,
    pages_read: pages.length, pages,
    counts: { missing_joe: result.missing_joe.length, unknown_to_carr: result.unknown_to_carr.length,
      absent_from_salesforce: result.absent_from_salesforce.length },
    reconciliation: result, findings_recorded, salesforce_writes: 0, effects: EFFECTS });
}
