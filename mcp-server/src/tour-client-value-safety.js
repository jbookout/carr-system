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
// The rule is aimed at text a person would type, not at deliberate evasion.
// Documented residuals, left to the human review every projection gets before
// it is sealed (and pinned in the corpus's `residual` list so any change to
// them is visible): spelled-out contacts ("two five one 555 0100",
// "bob(at)landlord.co"), bare domains outside the url rule's endings
// ("bayside.health"), and a suite word before a dash pair whose second number
// has no leading zero ("Suite 555-1234").
//
// Refusal is the only outcome. Nothing here rewrites or redacts a value: a
// value that fails is not shown, and the seal that would have sealed it
// refuses, naming the field and the rule.

export const CLIENT_TEXT_MAX_CHARS = 120;
export const CLIENT_ROUTE_LABEL_PATTERN = "^[A-Za-z0-9]{1,3}$";


// Every dash a phone number may be written with: hyphen, non-breaking hyphen,
// figure dash, en/em dash, horizontal bar, minus sign, small and full-width
// hyphen-minus (the ASCII hyphen last, as a bracket expression needs).
const DASHES = "\\u2010-\\u2015\\u2212\\ufe58\\ufe63\\uff0d-";

// Before any rule reads a value it is put in the form a client reads:
//   1. Unicode compatibility normalization (NFKC): full-width digits and
//      letters, the full-width @ and the compatibility spaces become their
//      plain forms (JavaScript normalize('NFKC'), PostgreSQL
//      normalize(t, NFKC));
//   2. the invisible formatting characters -- soft hyphen, Mongolian vowel
//      separator, zero-width space/non-joiner/joiner, direction marks, word
//      joiner, invisible operators, BOM -- are removed;
//   3. every run of spaces, ASCII or Unicode, becomes ONE ASCII space and the
//      ends are trimmed.
// That text is what the browser share emits and the PDF prints, so every
// engine judges exactly what a client would see.
export const CLIENT_TEXT_FORMAT_CHARS = "[\\u00ad\\u180e\\u200b-\\u200f\\u2060-\\u2064\\ufeff]";
export const CLIENT_TEXT_SPACE_RUN = "[ \\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000]+";
// Before the phone rules read a value, a number shaped 3-3-4 is joined across
// separators of up to five characters of space ( ) . and any dash (251 . .
// 555 . . 0100, 251 ( 555 ) 0100), or across one slash, underscore or comma
// (251/555/0100, 251,555,0100). A country code or leading +1 in front stays
// outside the join and the ten digits still read as a phone. Only the 3-3-4
// shape joins across wide separators, so year and count ranges do not
// ("Renovated 2021 - 2026 (12 suites)"), and a comma joins only a single-comma
// 3-3-4 group, so thousands do not (120,000 SF).
export const CLIENT_TEXT_PHONE_JOIN = Object.freeze({
  pattern: `(^|[^0-9])([0-9]{3})(?:[ ().${DASHES}]{0,5}|[,/_])([0-9]{3})(?:[ ().${DASHES}]{0,5}|[,/_])([0-9]{4})(?![0-9])`,
  replacement: "$1$2$3$4",
});
// Then any digits separated by one or two of space . and any dash are joined,
// so 251 555 01 00, 2 5 1 5 5 5 0 1 0 0 and 1-251-555-0100 read as
// 2515550100. Parentheses join only inside the 3-3-4 shape above, so
// "Renovated 2021-2026 (12 suites)" stays text.
export const CLIENT_TEXT_DIGIT_JOIN = Object.freeze({ pattern: `([0-9])[ .${DASHES}]{1,2}(?=[0-9])`, replacement: "$1" });
// Before the seven-digit local-number rule reads a value, a suite/unit/room
// RANGE is set aside: a suite word, then two numbers of 1-4 digits joined by
// a dash, the second without a leading zero (Suites 100-1200, Suite
// 251-5550, Suites 250 - 2500 SF). A dot or a leading-zero second number is
// not a range, so Unit 555-0100 and Ste #555.0100 are still read as phones.
export const CLIENT_TEXT_SUITE_RANGE = Object.freeze({
  pattern: `(^|[^A-Za-z])(suites?|ste|units?|rooms?)[.:#]?[ ]?#?[0-9]{1,4}[ ]?[${DASHES}][ ]?[1-9][0-9]{0,3}(?![0-9])`,
  replacement: "$1 ",
});

// Case-insensitive, checked in this order; the first rule that matches names
// the refusal. `target` is the text the rule reads: the normalized value
// (raw), with digit groups joined (digits), or with suite ranges set aside
// (nosuite).
export const CLIENT_TEXT_RULES = Object.freeze([
  // an email address: a dotted domain after an @ (spaced or not), or a word
  // written tight against both sides of an @ ("bob@landlord");
  // "4,200 RSF @ $28.50/SF" and "2 suites @ 1,200 SF" are not one
  Object.freeze({ rule: "email", target: "raw", pattern: "[A-Za-z0-9._%+-] ?@ ?[A-Za-z0-9_-]+([.][A-Za-z0-9_-]+)*[.][A-Za-z]{2,}|[A-Za-z0-9._%+-]@[A-Za-z0-9_-]*[A-Za-z]" }),
  // a URL or a bare web domain (its name holds a letter: "Hwy 90.US 29" is a road)
  Object.freeze({ rule: "url", target: "raw", pattern: "https?://|www[.]|[A-Za-z0-9-]*[A-Za-z][A-Za-z0-9-]*[.](com|net|org|io|biz|info|us)([^A-Za-z0-9]|$)" }),
  // a North-American phone number, however its ten digits are grouped
  Object.freeze({ rule: "phone", target: "digits", pattern: "(^|[^0-9])1?[2-9][0-9]{9}([^0-9]|$)" }),
  // a seven-digit local number: 555-0100, 555 - 0100 (exchange 2-9, as NANP requires)
  Object.freeze({ rule: "local_phone", target: "nosuite", pattern: `(^|[^0-9])[2-9][0-9]{2} ?[.${DASHES}] ?[0-9]{4}([^0-9]|$)` }),
  // an international number: +44 20 7946 0958, + 44 ..., 011 44 ...
  Object.freeze({ rule: "international_phone", target: "digits", pattern: "[+] ?[0-9]{8,}|(^|[^0-9])(011|00)[1-9][0-9]{6,}([^0-9]|$)" }),
  // access-code wording, as whole words: gate code, door combo, entry PIN,
  // alarm code, keypad code ("Westgate Pines", "Fire alarm system" pass)
  Object.freeze({ rule: "access_code", target: "raw", pattern: "(^|[^A-Za-z])(gate|door|key|entry|garage|alarm|keypad|access|lock)[ -]?(codes?|combos?|combination|pins?|passwords?)([^A-Za-z]|$)" }),
  Object.freeze({ rule: "lockbox", target: "raw", pattern: "(^|[^A-Za-z])(lock[ -]?box(es)?|passcodes?)([^A-Za-z]|$)" }),
  // internal-note wording (a free-text note cannot be recognised in general;
  // the length cap bounds the rest)
  Object.freeze({ rule: "internal_note", target: "raw", pattern: "(^|[^A-Za-z])(internal[ -]?(notes?|only|use)|confidential|do not (share|disclose)|broker[ -]only|not for (the )?clients?)([^A-Za-z]|$)" }),
]);

const RULES = CLIENT_TEXT_RULES.map(entry => ({ ...entry, regex: new RegExp(entry.pattern, "i") }));
const FORMAT_CHARS = new RegExp(CLIENT_TEXT_FORMAT_CHARS, "g");
const SPACE_RUN = new RegExp(CLIENT_TEXT_SPACE_RUN, "g");
const PHONE_JOIN = new RegExp(CLIENT_TEXT_PHONE_JOIN.pattern, "g");
const DIGIT_JOIN = new RegExp(CLIENT_TEXT_DIGIT_JOIN.pattern, "g");
const SUITE_RANGE = new RegExp(CLIENT_TEXT_SUITE_RANGE.pattern, "gi");
const ROUTE_LABEL = new RegExp(CLIENT_ROUTE_LABEL_PATTERN);
// C0 controls (tab, newline and carriage return included) and DEL.
const CONTROL = /[\u0000-\u001F\u007F]/;

/**
 * The text every client rule reads and every client surface shows: NFKC,
 * formatting characters removed, space runs collapsed to one space, ends
 * trimmed. Mirrors ops.tour_client_text_normalize.
 */
export function normalizeClientText(value) {
  return value.normalize("NFKC").replace(FORMAT_CHARS, "").replace(SPACE_RUN, " ").replace(/^ | $/g, "");
}

/**
 * The name of the first client text rule `value` breaks, or null when a client
 * may see it inside an allowlisted field. Mirrors ops.tour_client_text_violation.
 */
export function clientTextViolation(value, maximum = CLIENT_TEXT_MAX_CHARS) {
  if (typeof value !== "string") return "not_text";
  if (CONTROL.test(value)) return "control_character";
  const text = normalizeClientText(value);
  if (!text) return "empty";
  // Counted in code points, as PostgreSQL char_length() counts.
  if (Array.from(text).length > maximum) return "too_long";
  const targets = {
    raw: text,
    digits: text.replace(PHONE_JOIN, CLIENT_TEXT_PHONE_JOIN.replacement).replace(DIGIT_JOIN, CLIENT_TEXT_DIGIT_JOIN.replacement),
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
    out[key] = typeof part === "string" ? normalizeClientText(part) : part;
  }
  if (out.value === undefined && out.min === undefined && out.max === undefined) return undefined;
  if (typeof out.min === "number" && typeof out.max === "number" && out.min > out.max) return undefined;
  return out;
}
