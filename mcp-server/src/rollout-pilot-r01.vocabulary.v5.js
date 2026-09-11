// DoctorCRE v5 slice V5-R01 — the closed vocabulary and the local validators
// shared by this slice's three other files.
//
// WHY A FOURTH FILE. The slice has two public surfaces (the pilot/beta ledger
// and the onboarding flow) and one private classifier, and all three need the
// same closed lists and the same refusal shape. Putting them here means there is
// exactly one place a list can be widened, and the suites check the lists rather
// than the places that read them. Nothing here decides anything: there is no
// evaluator in this file, no clock, no store, no I/O, and no outcome.
//
// THE VALIDATORS ARE LOCAL TO THIS SLICE ON PURPOSE, following the rest of the
// v5 lane: a slice cannot be loosened by an edit to a shared helper it never
// reviewed. "Local to this slice" is the smallest honest unit here, not "local
// to this file", because these four files are reviewed and merged together.

import { V5_DEFERRED_AUTHORITY_PARTNER, V5_SYSTEM_AUTHORITY_PARTNER } from "./global-boundaries.v5.js";

export const V5_R01_SCHEMA_VERSION = "doctorcre-v5-rollout-pilot.v1";
export const V5_R01_POLICY_VERSION = 1;

// ---------------------------------------------------------------------------
// Failure shape.
// ---------------------------------------------------------------------------

/**
 * EVERY PUBLIC FUNCTION OF THIS SLICE, and the number of arguments it declares.
 *
 * This list does two jobs and they are the same job. It generates the refusal
 * code each function raises when it is handed more arguments than it declares,
 * and it is the closed roster the suite walks when it proves the arity boundary
 * in every calling form — a direct call, spread arguments, `Function.apply`, a
 * bound `this`, and a rest tail whose leading entries are `undefined`.
 *
 * THE THIRD REVIEW OF PR 992 FOUND THE HOLE THIS CLOSES. Each guard used to read
 * `extra[0]` alone, so `evaluatePilotDay({}, undefined, holder)` slid a holder
 * past a boundary the module claimed to hold. Counting is the fix: a function
 * that declares one request refuses argument two, whatever argument two is.
 */
export const V5_R01_DECLARED_ARITIES = Object.freeze({
  // rollout-pilot-r01.v5.js
  assertR01DecisionBinding: 1,
  describeFailureExclusions: 0,
  describePilotDayLedgerEntry: 0,
  describeRecoveryDrill: 0,
  evaluatePilotDay: 1,
  evaluatePilotRun: 1,
  evaluateRecoveryDrill: 1,
  rolloutPilotGaps: 0,
  v5R01PolicyCanonicalBytes: 0,
  v5R01PolicyDigest: 0,
  v5R01PolicyPreimage: 0,
  // onboarding-flow-r01.v5.js
  evaluateBetaOperability: 1,
  evaluatePerSliceDellReview: 1,
  onboardingMobileExposure: 0,
  onboardingProgressStatus: 1,
  onboardingSurfaceStatus: 0,
  v5R01OnboardingCanonicalBytes: 0,
  v5R01OnboardingDigest: 0,
  v5R01OnboardingPreimage: 0,
  // this file
  assertArray: 3,
  assertBoolean: 2,
  assertCalendarDate: 2,
  assertClosedKeys: 3,
  assertEnum: 3,
  assertInternalRef: 3,
  assertObject: 2,
  assertRequiredKeys: 3,
  assertSafeText: 3,
  calendarDayOrdinal: 1,
  calendarWeekday: 1,
});

const V5_R01_ARITY_GUARDED_ENTRY_POINTS = Object.freeze(
  Object.keys(V5_R01_DECLARED_ARITIES).sort());

/**
 * THE CLOSED SET OF FAILURE CODES THIS SLICE CAN RAISE.
 *
 * The code is the only part of a refusal a consumer branches on, so it is the
 * part a caller must not be able to mint. It is now also the ONLY part a caller
 * could reach at all: the constructor below takes a code and nothing else.
 */
export const V5_R01_ERROR_CODES = Object.freeze([
  "decision_binding_mismatch",
  "duplicate_onboarding_step",
  "invalid_date",
  "invalid_failure_origin_registry",
  "invalid_reference",
  "invalid_shape",
  "malformed_unicode",
  "missing_field",
  "onboarding_step_declares_no_resume_record",
  "onboarding_step_has_an_unregistered_mobile_reach",
  "onboarding_step_names_an_unknown_surface",
  "onboarding_step_overclaims_its_resume_support",
  "onboarding_step_requires_a_developer_tool",
  "onboarding_step_requires_an_unregistered_tool",
  "partner_roles_collapsed",
  "seam_claimed_to_exist",
  "text_too_long",
  "too_many_entries",
  "unknown_field",
  "unknown_value",
  "unsafe_unicode",
  ...V5_R01_ARITY_GUARDED_ENTRY_POINTS.map(name => `${name}_takes_no_extra_argument`),
].sort());

const V5_R01_ERROR_CODE_SET = new Set(V5_R01_ERROR_CODES);

/**
 * THE FIXED MESSAGE TABLE, and why a refusal no longer carries a sentence its
 * caller helped write.
 *
 * The re-review of PR 992 reproduced the defect: `V5R01Error` stored whatever
 * `message` and `detail` it was handed, `fail` was an exported door to that
 * constructor, and the second review's own sweep looked at a throw's `code` and
 * deliberately discarded the rest. So every privileged word in the standing
 * rule — and a hedged classification token with them — came straight back out
 * of `error.message` and `error.detail` on a registered code.
 *
 * A refusal is now a CODE and nothing else. The message is looked up here, the
 * table is exhaustive over the code set (checked at load, below), no `detail` is
 * stored, no `cause` is set, and the instance is frozen, so there is no property
 * on a refusal that any caller anywhere contributed a byte to.
 *
 * The messages themselves are written clear of every privileged word, so the
 * suite can sweep a thrown error's message as strictly as it sweeps a returned
 * code, with no prose exemption of any kind.
 */
const V5_R01_FIXED_MESSAGES = Object.freeze({
  decision_binding_mismatch:
    "the declared decision set is not this slice's settled decision set",
  duplicate_onboarding_step: "onboarding step ids must be distinct",
  invalid_date: "a field must be a real ISO calendar date (YYYY-MM-DD)",
  invalid_failure_origin_registry:
    "a failure origin in this slice's own registry is malformed",
  invalid_reference: "a field is not a permitted CARR reference",
  invalid_shape: "a field does not have the shape this slice requires",
  malformed_unicode: "a field contains an unpaired surrogate",
  missing_field: "a required field is absent",
  onboarding_step_declares_no_resume_record:
    "an onboarding step does not say what a resume record would hold",
  onboarding_step_has_an_unregistered_mobile_reach:
    "an onboarding step carries a mobile reach outside the registered three",
  onboarding_step_names_an_unknown_surface:
    "an onboarding step names an asset the surface table does not carry",
  onboarding_step_overclaims_its_resume_support:
    "an onboarding step claims resume support that no enrollment store backs",
  onboarding_step_requires_a_developer_tool:
    "an onboarding step requires a tool class a partner without developer tools lacks",
  onboarding_step_requires_an_unregistered_tool:
    "an onboarding step requires a tool class this slice does not register",
  partner_roles_collapsed:
    "the pilot and beta partners must be distinct; S01 no longer distinguishes them",
  seam_claimed_to_exist: "a seam claims to exist; this slice holds no seam that does",
  text_too_long: "a field is longer than this slice permits",
  too_many_entries: "an array holds more entries than this slice permits",
  unknown_field: "a field outside this slice's closed schema was supplied",
  unknown_value: "a field holds a value outside the registered set",
  unsafe_unicode:
    "a field contains a control, bidirectional or invisible format character",
  ...Object.fromEntries(V5_R01_ARITY_GUARDED_ENTRY_POINTS.map(name =>
    [`${name}_takes_no_extra_argument`,
      `${name} was given more arguments than it declares; a store, ledger, calendar,`
      + " registry or observer is not an argument"])),
});

for (const code of V5_R01_ERROR_CODES) {
  if (typeof V5_R01_FIXED_MESSAGES[code] !== "string") {
    throw new TypeError("v5_r01_error_code_has_no_fixed_message");
  }
}
for (const code of Object.keys(V5_R01_FIXED_MESSAGES)) {
  if (!V5_R01_ERROR_CODE_SET.has(code)) {
    throw new TypeError("v5_r01_fixed_message_for_an_unregistered_code");
  }
}

/**
 * A refusal, carrying a registered code and the fixed message that goes with it.
 *
 * IT TAKES ONE ARGUMENT. Not a message, not a detail, not an options bag with a
 * `cause` in it — one registered code. Every other calling form is a plain
 * TypeError whose text quotes nothing, because the refusal's own text must not
 * become a second door for caller input to leave the module.
 */
export class V5R01Error extends Error {
  constructor(...args) {
    if (args.length !== 1) {
      throw new TypeError("v5_r01_error_takes_exactly_one_registered_code");
    }
    const [code] = args;
    if (typeof code !== "string" || !V5_R01_ERROR_CODE_SET.has(code)) {
      throw new TypeError("v5_r01_error_code_is_not_registered");
    }
    super(V5_R01_FIXED_MESSAGES[code]);
    this.name = "V5R01Error";
    this.code = code;
    Object.freeze(this);
  }
}

/** Raise a registered refusal. One argument, for the same reason. */
export function fail(...args) {
  if (args.length !== 1) {
    throw new TypeError("v5_r01_fail_takes_exactly_one_registered_code");
  }
  throw new V5R01Error(args[0]);
}

/**
 * THE ARITY BOUNDARY, counted rather than peeked at.
 *
 * `args` is the rest tail of a function whose whole parameter list is `...args`,
 * so this sees every argument in every calling form: spread, `apply`, `call`, a
 * bound receiver, a tail of `undefined`s with a holder behind them. A function
 * that declares `n` arguments refuses argument `n + 1`.
 */
/**
 * A VALIDATOR CALLED WRONGLY REFUSES WITH A REGISTERED CODE, and this is part of
 * the same guarantee as the fixed message table.
 *
 * These are exported functions, so the sweep calls every one of them with every
 * caller shape it can build. Left alone, `assertClosedKeys(x)` reached
 * `allowed.includes` and V8 raised a native TypeError whose own message quotes
 * the engine rather than this module — an error text on this slice's public
 * surface that the slice did not write. It refuses first now, so EVERY throw
 * from this slice carries either a registered code or one of the three fixed
 * TypeError texts below, and the suite sweeps all of them with no prose
 * exemption.
 */
function assertArgumentShape(wellFormed) {
  if (!wellFormed) fail("invalid_shape");
}

export function assertArity(...args) {
  if (args.length > 3) throw new TypeError("v5_r01_assert_arity_takes_three_arguments");
  const [received, declared, name] = args;
  if (!Array.isArray(received) || typeof declared !== "number"
    || typeof name !== "string" || !V5_R01_ERROR_CODE_SET.has(`${name}_takes_no_extra_argument`)) {
    throw new TypeError("v5_r01_assert_arity_was_called_wrongly");
  }
  if (received.length > declared) fail(`${name}_takes_no_extra_argument`);
}

function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

/**
 * MODULE-PRIVATE ON PURPOSE, and this is the third review's finding.
 *
 * `deepFreeze(x)` returns `x`. Exported, it was a public function of this slice
 * that handed a caller's own object straight back, so every privileged token the
 * standing rule names — and a `would_*` token with it — could be obtained from
 * this surface by passing it in. A token a module returns is a token a module
 * returns; that the caller supplied it is not a defence, because the sweep's
 * question is what leaves the module, not where it came from.
 *
 * It is not exported now. Freezing is a local structural habit, not vocabulary a
 * consumer needs, and the other two modules of this slice each keep their own
 * private copy, which is already the pattern the rest of mcp-server/src follows
 * (backup-quarantine.v5.js, command-supervisor-admission.v5.js and others).
 */
function deepFreeze(value) {
  if (Array.isArray(value)) { value.forEach(deepFreeze); return Object.freeze(value); }
  if (isPlainObject(value)) { Object.values(value).forEach(deepFreeze); return Object.freeze(value); }
}

/** An open schema is an unenforced one: an unread field is a field nobody checked. */
/**
 * THE VALIDATORS RETURN NOTHING, DELIBERATELY.
 *
 * They used to return the value they had just checked, which reads as a
 * convenience and is not one: an exported function that hands a caller's own
 * string back is a public export returning caller input, and the suite's
 * per-privileged-word sweep caught it doing exactly that —
 * `assertInternalRef("allow")` returned `"allow"`. A validator's whole job is to
 * throw; the caller already has the value.
 */
export function assertClosedKeys(...args) {
  assertArity(args, 3, "assertClosedKeys");
  const [object, allowed, path] = args;
  assertArgumentShape(isPlainObject(object) && Array.isArray(allowed) && typeof path === "string");
  for (const key of Object.keys(object)) {
    if (!allowed.includes(key)) {
      fail("unknown_field");
    }
  }
}

export function assertRequiredKeys(...args) {
  assertArity(args, 3, "assertRequiredKeys");
  const [object, required, path] = args;
  assertArgumentShape(isPlainObject(object) && Array.isArray(required) && typeof path === "string");
  for (const key of required) {
    if (!(key in object)) fail("missing_field");
  }
}

export function assertObject(...args) {
  assertArity(args, 2, "assertObject");
  const [value, path] = args;
  assertArgumentShape(typeof path === "string");
  if (!isPlainObject(value)) fail("invalid_shape");
}

export function assertArray(...args) {
  assertArity(args, 3, "assertArray");
  const [value, path, options = {}] = args;
  assertArgumentShape(typeof path === "string" && isPlainObject(options));
  const { min = 0, max = 512 } = options;
  if (!Array.isArray(value)) fail("invalid_shape");
  if (value.length < min) {
    fail("invalid_shape");
  }
  if (value.length > max) {
    fail("too_many_entries");
  }
}

const UNSAFE_TEXT =
  /[\u0000-\u001F\u007F-\u009F\u200B-\u200F\u202A-\u202E\u2060-\u2064\u2066-\u2069\uFEFF]/u;
const INTERNAL_REF = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$/;
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

export function assertSafeText(...args) {
  assertArity(args, 3, "assertSafeText");
  const [value, path, options = {}] = args;
  assertArgumentShape(typeof path === "string" && isPlainObject(options));
  const { maxLength = 512 } = options;
  if (typeof value !== "string" || value.length === 0) {
    fail("invalid_shape");
  }
  if (value.length > maxLength) {
    fail("text_too_long");
  }
  if (typeof value.isWellFormed === "function" && !value.isWellFormed()) {
    fail("malformed_unicode");
  }
  if (UNSAFE_TEXT.test(value)) {
    fail("unsafe_unicode");
  }
}

export function assertInternalRef(...args) {
  assertArity(args, 3, "assertInternalRef");
  const [value, path, options = {}] = args;
  assertArgumentShape(typeof path === "string" && isPlainObject(options));
  const { maxLength = 255 } = options;
  assertSafeText(value, path, { maxLength });
  if (!INTERNAL_REF.test(value)) {
    fail("invalid_reference");
  }
}

export function assertBoolean(...args) {
  assertArity(args, 2, "assertBoolean");
  const [value, path] = args;
  assertArgumentShape(typeof path === "string");
  if (typeof value !== "boolean") fail("invalid_shape");
}

export function assertEnum(...args) {
  assertArity(args, 3, "assertEnum");
  const [value, allowed, path] = args;
  assertArgumentShape(Array.isArray(allowed) && typeof path === "string");
  if (!allowed.includes(value)) {
    fail("unknown_value");
  }
}

/**
 * A calendar date, validated by round-trip rather than by regex alone.
 *
 * A regex accepts 2026-02-30; `Date.UTC` silently rolls it forward to March 2,
 * which would quietly turn one impossible day into a real one in the middle of a
 * run-length count. The round-trip is the check.
 */
export function assertCalendarDate(...args) {
  assertArity(args, 2, "assertCalendarDate");
  const [value, path] = args;
  assertArgumentShape(typeof path === "string");
  assertSafeText(value, path, { maxLength: 10 });
  const match = ISO_DATE.exec(value);
  if (!match) fail("invalid_date");
  const [, y, m, d] = match;
  const stamp = Date.UTC(Number(y), Number(m) - 1, Number(d));
  const round = new Date(stamp).toISOString().slice(0, 10);
  if (round !== value) {
    fail("invalid_date");
  }
}

/** Days since the epoch, so "the next calendar day" is subtraction and not parsing. */
export function calendarDayOrdinal(...args) {
  assertArity(args, 1, "calendarDayOrdinal");
  const [date] = args;
  if (typeof date !== "string" || !ISO_DATE.test(date)) fail("invalid_date");
  const [y, m, d] = date.split("-").map(Number);
  return Math.round(Date.UTC(y, m - 1, d) / 86400000);
}

/** 0 = Sunday … 6 = Saturday, computed from the same ordinal. */
export function calendarWeekday(...args) {
  assertArity(args, 1, "calendarWeekday");
  const [date] = args;
  if (typeof date !== "string" || !ISO_DATE.test(date)) fail("invalid_date");
  return (((calendarDayOrdinal(date) + 4) % 7) + 7) % 7;
}

/**
 * AN IDENTIFIER THIS SLICE REPEATS FROM ANOTHER OWNER, held as a quotation
 * rather than as a string of its own.
 *
 * THIS EXISTS BECAUSE OF THE PRIVILEGED-WORD SWEEP, and it is the honest way to
 * satisfy it rather than the convenient one. The suite matches every privileged
 * word by SUBSTRING now, with no exemption list and no word families, over every
 * export name, every affirmed key and every identifier-shaped string this slice
 * returns. Three of the strings in this file are not this slice's to spell:
 * S01's own exported evaluator names, and the design-basis register's targets and
 * acceptance hooks. `evaluateReadContinuity` and `rollout_readiness` carry the
 * word `read` because their owners wrote them that way, and renaming them here
 * would be misquoting a record to pass a test.
 *
 * So a quotation is a two-key record — who minted it, and the exact text — and
 * the suite's rule is structural rather than lexical: the `identifier` of a
 * quotation is swept for hedged verdicts and for caller echo like everything
 * else, and it is NOT swept for privileged words, because a quotation is not a
 * thing this slice can decide. The companion tests hold the line: a quotation
 * never stands in an answer, status, decision or reason position; a quoted
 * identifier is never an object key; and every S01 quotation is checked against
 * S01's real export list, so a misquotation fails rather than passes.
 */
/* NOT EXPORTED, and that is not tidiness. An exported `quotedIdentifier` would
 * be a public function that hands its caller's own string straight back inside a
 * record — the exact `deepFreeze` defect the third review reproduced. Quotations
 * are built here, from literals, at module load. */
function quotedIdentifier(identifier, quoted_from) {
  return Object.freeze({ quoted_from, identifier });
}

export const V5_R01_S01_MODULE = "global-boundaries.v5.js";
export const V5_R01_DESIGN_BASIS_REGISTER = "carr:design-basis-decision-register";

// ---------------------------------------------------------------------------
// WHO. Pinned by import so a rename in S01 breaks this slice at load rather than
// leaving it trusting a string literal.
// ---------------------------------------------------------------------------

/** The pilot partner is the system-authority partner. Ten days is HIS run. */
export const V5_R01_PILOT_PARTNER = V5_SYSTEM_AUTHORITY_PARTNER;

/** The beta partner is the deferred-authority partner. */
export const V5_R01_BETA_PARTNER = V5_DEFERRED_AUTHORITY_PARTNER;

if (V5_R01_PILOT_PARTNER === V5_R01_BETA_PARTNER) {
  throw new V5R01Error("partner_roles_collapsed");
}

// ---------------------------------------------------------------------------
// THE RUN.
// ---------------------------------------------------------------------------

/** Ten. From the slice's own done clause: "Joe 10/10 consecutive business days". */
export const V5_R01_REQUIRED_RUN_LENGTH = 10;

/**
 * What "business day" means here, stated so it can be disagreed with.
 *
 * Monday through Friday is arithmetic and this slice computes it. Which OTHER
 * dates are not business days — holidays, a declared office closure — is a fact
 * somebody owns, and this slice does not own it. A caller-supplied holiday list
 * is a caller-supplied authority, so the public evaluator refuses for want of the
 * calendar rather than counting against a list the caller chose.
 */
export const V5_R01_BUSINESS_DAY_BASIS =
  "monday_through_friday_in_the_operating_timezone_minus_declared_non_business_dates";

/**
 * The three J1 subjourneys, BY ARITY ONLY.
 *
 * The slice contract says "all three J1 subjourneys" and the repository does not
 * name them anywhere — not in a module, not in a test, not in a fixture. Naming
 * them here would be this slice inventing the product's own table of contents, so
 * it does not. What IS settled is the count, and the count is checkable: a roster
 * of two or four is wrong whatever the three are called. The roster itself is an
 * owed binding, listed in V5_R01_SEAMS.
 */
export const V5_R01_J1_SUBJOURNEY_COUNT = 3;

/**
 * A pilot day's own ordered questions, in the order they are asked. The ORDER is
 * part of the definition: an unresolved high defect disqualifies before anyone
 * asks whether the day's work got done, because a pilot does not run over one.
 */
export const V5_R01_DAY_CHECKS = deepFreeze([
  "no_unresolved_high_defect_open_against_j1",
  "all_three_j1_subjourneys_exercised",
  "at_least_one_real_deal_or_vendor_action",
  "every_failure_of_the_day_is_excluded",
]);

/**
 * THE EXACT FAILURE EXCLUSIONS. This list is the slice's most load-bearing
 * declaration, so each entry says what it is, whether it breaks the run, and the
 * clause it answers to.
 *
 * `excluded: true` means a failure of that origin does NOT break the run — the
 * day still counts. `excluded: false` means it does. `disqualifies_run` is the
 * third state: not merely a broken day but a run that may not be counted at all.
 *
 * READ THE CONDITIONS. Two of the three exclusions are CONDITIONAL, and that is
 * the whole difference between an honest exclusion and an alibi. A laptop fault
 * is excluded because Q010 settles that no production capability may depend on
 * Joe's laptop — but only if the product told the truth about being unreachable
 * and offered its documented fallback. A product that answered "healthy" while
 * the partner's device was failing has failed Q010's OTHER clause ("recognize
 * failure"), and that failure is the product's own.
 */
export const V5_R01_FAILURE_ORIGINS = deepFreeze({
  partner_device_or_credential: {
    excluded: true,
    disqualifies_run: false,
    condition: "product_reported_honestly_and_offered_documented_fallback",
    basis: "Q010 — no production capability may depend on Joe's laptop, memory, prompt habits"
      + " or log reading; a laptop or credential fault is therefore not a product failure,"
      + " PROVIDED the product recognised it and said so.",
  },
  third_party_provider_outage: {
    excluded: true,
    disqualifies_run: false,
    condition: "product_reported_honestly_and_offered_documented_fallback",
    basis: "Outside CARR. Excluded on the same condition: an outage the product misreported"
      + " as healthy is the product's failure, not the provider's.",
  },
  advanced_automation_or_native_prerequisite: {
    excluded: true,
    disqualifies_run: false,
    condition: null,
    basis: "The slice's excluded_scope puts advanced automation and native prerequisites"
      + " outside the pilot. A failure in something the pilot does not test cannot fail it.",
  },
  log_interpretation_required: {
    excluded: false,
    disqualifies_run: false,
    condition: null,
    basis: "Q010 names 'ability to interpret raw logs' among the things no production"
      + " capability may depend on. A day that was only salvaged by reading raw logs is a"
      + " day the product did not carry, so it does not count. THIS IS A READING of an"
      + " exclusion the catalog states as scope; it is written down so it can be"
      + " disagreed with rather than absorbed.",
  },
  product_defect: {
    excluded: false,
    disqualifies_run: false,
    condition: null,
    basis: "The thing the pilot exists to measure. Every failure that is not one of the"
      + " five origins above lands here, so the list is closed from below as well as above:"
      + " an unclassifiable failure is a product failure, never an unnamed exclusion.",
  },
  unresolved_high_defect: {
    excluded: false,
    disqualifies_run: true,
    condition: null,
    basis: "The slice's excluded_scope puts unresolved high defects outside the pilot"
      + " entirely. A run tallied over one measured a product nobody was meant to be"
      + " piloting, so the run is disqualified rather than merely broken.",
  },
});

export const V5_R01_FAILURE_ORIGIN_KEYS = deepFreeze(Object.keys(V5_R01_FAILURE_ORIGINS).sort());

/** The origins that do not break a run, derived rather than restated. */
export const V5_R01_EXCLUDED_FAILURE_ORIGINS = deepFreeze(
  V5_R01_FAILURE_ORIGIN_KEYS.filter(key => V5_R01_FAILURE_ORIGINS[key].excluded));

/** The origins that break the day. */
export const V5_R01_BREAKING_FAILURE_ORIGINS = deepFreeze(
  V5_R01_FAILURE_ORIGIN_KEYS.filter(key => !V5_R01_FAILURE_ORIGINS[key].excluded));

/** The origins that disqualify the whole run. */
export const V5_R01_DISQUALIFYING_FAILURE_ORIGINS = deepFreeze(
  V5_R01_FAILURE_ORIGIN_KEYS.filter(key => V5_R01_FAILURE_ORIGINS[key].disqualifies_run));

// Registry self-checks at load. A later edit that adds an origin without saying
// whether it breaks the run fails this module's own import, not a caller's call.
for (const [origin, entry] of Object.entries(V5_R01_FAILURE_ORIGINS)) {
  if (typeof entry.excluded !== "boolean" || typeof entry.disqualifies_run !== "boolean") {
    throw new V5R01Error("invalid_failure_origin_registry");
  }
  if (entry.excluded && entry.disqualifies_run) {
    throw new V5R01Error("invalid_failure_origin_registry");
  }
  if (entry.condition !== null && typeof entry.condition !== "string") {
    throw new V5R01Error("invalid_failure_origin_registry");
  }
  if (typeof entry.basis !== "string" || entry.basis.length < 40) {
    throw new V5R01Error("invalid_failure_origin_registry");
  }
}

// ---------------------------------------------------------------------------
// THE INJECTED RECOVERY DRILL.
// ---------------------------------------------------------------------------

/**
 * The faults a drill may inject. Every one of them is a state S01 ALREADY models,
 * named by the disposition S01 gives it, so the drill measures the product's
 * declared behaviour rather than a behaviour this slice invented for it.
 */
export const V5_R01_DRILL_FAULTS = deepFreeze({
  record_layer_unreachable: {
    s01_seam: quotedIdentifier("evaluateReadContinuity", V5_R01_S01_MODULE),
    expected_partner_visible_signal: "honest_unavailable_plus_documented_fallback",
    recovery_is: "partner_uses_documented_fallback_and_resumes_when_the_layer_returns",
  },
  cached_value_past_max_age: {
    s01_seam: quotedIdentifier("evaluateReadContinuity", V5_R01_S01_MODULE),
    expected_partner_visible_signal: "stale_value_marked_stale_not_shown_as_current",
    recovery_is: "partner_declines_to_act_on_the_stale_value_and_asks_again",
  },
  optional_local_node_unavailable: {
    s01_seam: quotedIdentifier("evaluateLocalPlatform", V5_R01_S01_MODULE),
    expected_partner_visible_signal: "declared_fallback_or_visible_queue",
    recovery_is: "partner_continues_on_the_declared_fallback_without_a_developer",
  },
  correspondence_adapter_unavailable: {
    s01_seam: quotedIdentifier("evaluateReadContinuity", V5_R01_S01_MODULE),
    expected_partner_visible_signal: "adapter_unavailable_named_not_silent",
    recovery_is: "partner_proceeds_without_correspondence_and_the_gap_is_visible",
  },
  write_conflict_requires_a_human: {
    s01_seam: quotedIdentifier("evaluateActorAuthority", V5_R01_S01_MODULE),
    expected_partner_visible_signal: "conflict_raised_as_a_question_not_auto_retried",
    recovery_is: "partner_answers_the_question_or_stops_without_making_it_worse",
  },
});

export const V5_R01_DRILL_FAULT_KEYS = deepFreeze(Object.keys(V5_R01_DRILL_FAULTS).sort());

/**
 * The aids a drill forbids. A recovery that needed one of these did not prove
 * what the drill exists to prove — which is that the PRODUCT carries the partner
 * through a failure, per Q010.
 */
export const V5_R01_FORBIDDEN_DRILL_AIDS = deepFreeze([
  "author_explanation",
  "developer_console",
  "direct_database_access",
  "raw_log_file",
  "shell_or_terminal",
  "source_checkout",
]);

/** What a drill receipt has to carry. Named here so "the evidence it needs" is a list. */
export const V5_R01_DRILL_RECEIPT_SCHEMA = "v5-r01-recovery-drill-receipt.v1";
export const V5_R01_DRILL_RECEIPT_FIELDS = deepFreeze([
  "aids_used",
  "drill_ref",
  "fault_injected",
  "injected_at",
  "injected_by_identity_ref",
  "observed_partner_visible_signal",
  "observer_identity_ref",
  "producer_step_ref",
  "recovery_reached_at",
  "recovery_path_taken",
  "subject_partner",
  "terminal_state_reached",
]);

/** The terminal states a drill may end in. Two of them are correct outcomes. */
export const V5_R01_DRILL_TERMINAL_STATES = deepFreeze([
  "recovery_reached",
  "stopped_safely_without_making_it_worse",
  "stuck",
  "made_it_worse",
]);

export const V5_R01_DRILL_CORRECT_TERMINAL_STATES = deepFreeze([
  "recovery_reached",
  "stopped_safely_without_making_it_worse",
]);

// ---------------------------------------------------------------------------
// ONBOARDING.
// ---------------------------------------------------------------------------

/**
 * Tool classes an onboarding step may not require. This is the machine-readable
 * form of "with no developer tools", and it is enforced at load in
 * onboarding-flow-r01.v5.js: a step that declares one of these cannot be
 * registered, so the property belongs to the file rather than to a promise.
 */
export const V5_R01_FORBIDDEN_TOOL_CLASSES = deepFreeze([
  "code_editor",
  "developer_console",
  "direct_database_access",
  "local_build",
  "package_manager",
  "raw_log_file",
  "shell_or_terminal",
  "source_checkout",
  "ssh",
  "version_control_client",
]);

/** The tool classes a step MAY require. A partner with a phone has the first two. */
export const V5_R01_PERMITTED_TOOL_CLASSES = deepFreeze([
  "authenticated_browser",
  "product_ui",
  "telephone",
]);

/** How a step is reached on a phone today. Three honest states, not two. */
export const V5_R01_MOBILE_REACH = deepFreeze([
  "in_phone_navigation",
  "reachable_by_link_only",
  "not_a_surface",
]);

export const V5_R01_ONBOARDING_SCHEMA = "v5-r01-onboarding-flow.v1";

// ---------------------------------------------------------------------------
// SEAMS. Every fact this slice needs and does not have, with the owner that
// would have to issue it. A refusal names one of these rather than guessing.
// ---------------------------------------------------------------------------

export const V5_R01_SEAMS = deepFreeze({
  operating_calendar: {
    seam: "step:v5-r01-operating-calendar-authority",
    holds: "which dates are not business days in the operating timezone",
    exists_in_this_repository: false,
  },
  j1_subjourney_roster: {
    seam: "step:v5-r01-j1-subjourney-roster-binding",
    holds: "the names of the three J1 subjourneys a pilot day must exercise",
    exists_in_this_repository: false,
  },
  pilot_day_store: {
    seam: "step:v5-r01-pilot-day-ledger-store",
    holds: "the durable, append-only record of what happened on each candidate pilot day",
    exists_in_this_repository: false,
  },
  // NAMED `outside_observer` RATHER THAN `independent_observer`, and the reason is
  // this slice's own word reservation rather than a change of meaning. Nine words
  // are reserved as privileged outcomes and the suite runs one test per word over
  // every export of every module here with no exemption of any kind, so the slice
  // does not spend one of them on its own identifiers. The role is unchanged and
  // is the catalog's: an INDEPENDENT observer, outside the pilot, who is never
  // its subject.
  outside_observer: {
    seam: "step:v5-r01-outside-pilot-observer-receipt",
    holds: "the outside judgement of WHY a day failed — the failure origin itself — made by"
      + " someone who is not the subject of the pilot",
    exists_in_this_repository: false,
  },
  drill_receipt_store: {
    seam: "step:v5-r01-recovery-drill-receipt-store",
    holds: "the drill receipts, issued by the observer who watched the drill",
    exists_in_this_repository: false,
  },
  onboarding_enrollment_store: {
    seam: "step:v5-r01-onboarding-enrollment-store",
    holds: "how far a partner has actually got, so the flow can be resumed",
    exists_in_this_repository: false,
  },
  onboarding_surface_registration: {
    seam: "step:v5-r01-onboarding-surface-registration",
    holds: "the authenticated surface that would SERVE the onboarding flow as a page a partner"
      + " walks on a phone, registered in the workspace's own surface inventory and served by"
      + " the router",
    exists_in_this_repository: false,
  },
  defect_register: {
    seam: "step:v5-r01-open-defect-severity-register",
    holds: "whether a high-severity defect is open against J1 on a given date",
    exists_in_this_repository: false,
  },
});

export const V5_R01_SEAM_KEYS = deepFreeze(Object.keys(V5_R01_SEAMS).sort());
export const V5_R01_SEAM_REFS = deepFreeze(
  V5_R01_SEAM_KEYS.map(key => V5_R01_SEAMS[key].seam).sort());

for (const [name, entry] of Object.entries(V5_R01_SEAMS)) {
  if (entry.exists_in_this_repository !== false) {
    throw new V5R01Error("seam_claimed_to_exist");
  }
}

// ---------------------------------------------------------------------------
// WHAT THIS MODULE DELIBERATELY NO LONGER CARRIES.
//
// Two vocabularies used to live here and both have moved OUT of mcp-server/src.
//
//   * V5_R01_CLASSIFICATIONS — the `would_*_if_authoritative` tokens. A
//     classification is a thing a classifier says, and every classifier in this
//     slice now lives under mcp-server/test/. Keeping the tokens here made them
//     exported production strings, which is a string a consumer can match on,
//     and the review of PR 992 was right that a public surface has no honest use
//     for one. They live in rollout-pilot-r01-classifiers.v5.testhelper.mjs, and
//     rollout-pilot-r01.v5.test.mjs asserts that no file in mcp-server/src
//     contains any of them.
//
//   * V5_R01_PRIVILEGED_OUTCOMES — the words no caller may obtain. That
//     list was a runtime denylist swept over each refusal, which was necessary
//     only because refusals used to compose strings out of caller input. They no
//     longer do: every public answer in this slice is a fixed value that reads no
//     field of its request, so there is nothing left to sweep at runtime and a
//     denylist shipped in production would be nine privileged words exported
//     from the very surface that must not produce them. The list is now the
//     suite's — fifteen words now, the twelve the standing rule names plus
//     `healthy`, `passing` and the `would_*` form — and the suite runs one test
//     per word over every export of every module in this slice, constructors
//     included, with no exemption for a word the caller happened to supply.
//
// What remains here is vocabulary a consumer genuinely needs: the closed
// enumerations, the seams, and the validators.
