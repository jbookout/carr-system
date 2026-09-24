// V5-J303 client value safety: the ONE rule for what text may sit inside an
// allowlisted client Tour field (CLIENT_TOUR_FIELD_KEYS). The key allowlist
// says WHICH fields a client sees; this rule says what those fields may
// CONTAIN, so an owner phone, an email, a gate code or an internal note typed
// into `parking` or `availability` cannot ride an allowed key to a client.
//
// Three enforcement points use this rule and must not drift:
//   1. the database, at seal time and on every client read, through
//      ops.tour_client_text_violation() (migrations/0591) -- the authority;
//   2. the browser share projections in tour-sharing.js;
//   3. the PDF packet renderer (tour-packet-render.js).
// The rules below are written in the subset of regex syntax that JavaScript
// and PostgreSQL ARE read identically (explicit [^A-Za-z] / [^0-9]
// boundaries, no \b or \y, lookahead only), and
// test/tour-client-share-allowlist.test.mjs asserts the SQL functions carry
// exactly these strings. The same test and the Postgres proof run one shared
// corpus of ordinary CRE text and of smuggled contact/access text through both
// engines. Change a rule here and you must change it there in the same commit.
//
// The rule is aimed at text a person would type, not at deliberate evasion:
// spelled-out contacts ("bob at gmail dot com", "two five one ...") are left
// to the human review every projection gets before it is sealed.
//
// Refusal is the only outcome. Nothing here rewrites or redacts a value: a
// value that fails is not shown, and the seal that would have sealed it
// refuses, naming the field and the rule.

export const CLIENT_TEXT_MAX_CHARS = 120;
export const CLIENT_ROUTE_LABEL_PATTERN = "^[A-Za-z0-9]{1,3}$";

// Before the phone rule reads a value, digits separated by one or two of
// space ( ) . - are joined, so 251 555 01 00, (251)5550100 and 251-5550100 all
// read as 2515550100. A slash or comma does not join (dates, 4,200 SF).
export const CLIENT_TEXT_DIGIT_JOIN = Object.freeze({ pattern: "([0-9])[ ().-]{1,2}(?=[0-9])", replacement: "$1" });
// Before the seven-digit local-number rule reads a value, a suite/unit range
// (Suites 100-1200, Ste 250-1200) is removed, so it is not read as 555-0100.
export const CLIENT_TEXT_SUITE_RANGE = Object.freeze({
  pattern: "(^|[^A-Za-z])(suites?|ste|units?|rooms?)[.:#]?[ ]*#?[0-9]{1,4}[ ]*[-.][ ]*[0-9]{1,4}(?![0-9])",
  replacement: "$1 ",
});

// Case-insensitive, checked in this order; the first rule that matches names
// the refusal. `target` is the text the rule reads: the trimmed value (raw),
// the value with digit groups joined (digits), or with suite ranges removed
// (nosuite).
export const CLIENT_TEXT_RULES = Object.freeze([
  // an email address (an @ between words; "4,200 RSF @ $28.50/SF" is not one)
  Object.freeze({ rule: "email", target: "raw", pattern: "[A-Za-z0-9._%+-] ?@ ?[A-Za-z0-9-]+([.][A-Za-z0-9-]+)*[.][A-Za-z]{2,}" }),
  // a URL or a bare web domain
  Object.freeze({ rule: "url", target: "raw", pattern: "https?://|www[.]|[A-Za-z0-9-]{2,}[.](com|net|org|io|biz|info|us)([^A-Za-z0-9]|$)" }),
  // a North-American phone number, however its ten digits are grouped
  Object.freeze({ rule: "phone", target: "digits", pattern: "(^|[^0-9])1?[2-9][0-9]{9}([^0-9]|$)" }),
  // a seven-digit local number: 555-0100 (exchange 2-9, as NANP requires)
  Object.freeze({ rule: "local_phone", target: "nosuite", pattern: "(^|[^0-9])[2-9][0-9]{2}[-.][0-9]{4}([^0-9]|$)" }),
  // an international number: +44 20 7946 0958
  Object.freeze({ rule: "international_phone", target: "raw", pattern: "[+][0-9][0-9 ().-]{7,}[0-9]" }),
  // access-code wording, as whole words: gate code, door combo, entry PIN,
  // alarm code, keypad code ("Westgate Pines", "Fire alarm system" pass)
  Object.freeze({ rule: "access_code", target: "raw", pattern: "(^|[^A-Za-z])(gate|door|key|entry|garage|alarm|keypad|access|lock)[ -]?(codes?|combos?|combination|pins?|passwords?)([^A-Za-z]|$)" }),
  Object.freeze({ rule: "lockbox", target: "raw", pattern: "(^|[^A-Za-z])(lock[ -]?box(es)?|passcodes?)([^A-Za-z]|$)" }),
  // internal-note wording (a free-text note cannot be recognised in general;
  // the length cap bounds the rest)
  Object.freeze({ rule: "internal_note", target: "raw", pattern: "(^|[^A-Za-z])(internal[ -]?(notes?|only|use)|confidential|do not (share|disclose)|broker[ -]only|not for (the )?clients?)([^A-Za-z]|$)" }),
]);

const RULES = CLIENT_TEXT_RULES.map(entry => ({ ...entry, regex: new RegExp(entry.pattern, "i") }));
const DIGIT_JOIN = new RegExp(CLIENT_TEXT_DIGIT_JOIN.pattern, "g");
const SUITE_RANGE = new RegExp(CLIENT_TEXT_SUITE_RANGE.pattern, "gi");
const ROUTE_LABEL = new RegExp(CLIENT_ROUTE_LABEL_PATTERN);
// C0 controls (tab, newline and carriage return included) and DEL.
const CONTROL = /[\u0000-\u001F\u007F]/;
// PostgreSQL btrim() trims spaces only; so does this, so both engines measure
// and match the same text.
const trimSpaces = value => value.replace(/^ +| +$/g, "");

/**
 * The name of the first client text rule `value` breaks, or null when a client
 * may see it inside an allowlisted field. Mirrors ops.tour_client_text_violation.
 */
export function clientTextViolation(value, maximum = CLIENT_TEXT_MAX_CHARS) {
  if (typeof value !== "string") return "not_text";
  if (CONTROL.test(value)) return "control_character";
  const text = trimSpaces(value);
  if (!text) return "empty";
  // Counted in code points, as PostgreSQL char_length() counts.
  if (Array.from(text).length > maximum) return "too_long";
  const targets = {
    raw: text,
    digits: text.replace(DIGIT_JOIN, CLIENT_TEXT_DIGIT_JOIN.replacement),
    nosuite: text.replace(SUITE_RANGE, CLIENT_TEXT_SUITE_RANGE.replacement),
  };
  for (const entry of RULES) if (entry.regex.test(targets[entry.target])) return entry.rule;
  return null;
}

/** True when `value` is text a client may see inside an allowlisted field. */
export function isClientSafeText(value, maximum = CLIENT_TEXT_MAX_CHARS) {
  return clientTextViolation(value, maximum) === null;
}

/** True when `value` is a short stop marker (A, B, 12). */
export function isClientRouteLabel(value) {
  return typeof value === "string" && ROUTE_LABEL.test(value);
}

const METRIC_NUMERIC_KEYS = Object.freeze(["value", "min", "max"]);
const METRIC_TEXT_KEYS = Object.freeze(["unit", "currency", "period", "label"]);
export const CLIENT_METRIC_KEYS = Object.freeze([...METRIC_NUMERIC_KEYS, ...METRIC_TEXT_KEYS]);

/**
 * The client-safe copy of a size / asking-economics metric, or undefined when
 * any part of it is unsafe. All-or-nothing on purpose: a metric with one
 * poisoned label is refused whole rather than shown with that part missing.
 */
export function clientSafeMetric(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) return undefined;
  const keys = Object.keys(value);
  if (!keys.length) return undefined;
  const out = {};
  for (const key of keys) {
    const part = value[key];
    if (METRIC_NUMERIC_KEYS.includes(key)) {
      if (typeof part === "number" ? !Number.isFinite(part) : !isClientSafeText(part)) return undefined;
    } else if (METRIC_TEXT_KEYS.includes(key)) {
      if (!isClientSafeText(part)) return undefined;
    } else {
      return undefined;
    }
    out[key] = typeof part === "string" ? trimSpaces(part) : part;
  }
  if (out.value === undefined && out.min === undefined && out.max === undefined) return undefined;
  if (typeof out.min === "number" && typeof out.max === "number" && out.min > out.max) return undefined;
  return out;
}
