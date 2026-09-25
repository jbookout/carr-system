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
// boundaries, no \b or \y, lookahead, and the one lookbehind in access_code), and
// test/tour-client-share-allowlist.test.mjs asserts the SQL functions carry
// exactly these strings. The same test and the Postgres proof run one shared
// corpus of ordinary CRE text and of smuggled contact/access text through both
// engines. Change a rule here and you must change it there in the same commit.
//
// The rule is aimed at text a person would type, not at deliberate evasion.
// Documented residuals, left to the human review every projection gets before
// it is sealed (and pinned in the corpus's `residual` list so any change to
// them is visible): digits spelled out or swapped for look-alike letters
// ("two five one 555 0100", "251-555-O1OO"), and a suite word before a dash
// pair whose second number has no leading zero ("Suite 555-1234").
//
// Refusal is the only outcome. Nothing here rewrites or redacts a value: a
// value that fails is not shown, and the seal that would have sealed it
// refuses, naming the field and the rule.

export const CLIENT_TEXT_MAX_CHARS = 120;
export const CLIENT_ROUTE_LABEL_PATTERN = "^[A-Za-z0-9]{1,3}$";

// CHARACTER ALLOWLIST. After Unicode compatibility normalization (NFKC:
// JavaScript normalize('NFKC'), PostgreSQL normalize(t, NFKC)) a client value
// may hold ONLY:
//   printable ASCII                              U+0020-U+007E
//   section sign, degree sign, plus-minus        U+00A7 U+00B0 U+00B1
//   Latin-1 letters and the multiplication sign  U+00C0-U+00F6 (x = U+00D7)
//   Latin-1 and Latin Extended-A letters         U+00F8-U+017F
//   en and em dash                               U+2013 U+2014
//   curly quotes                                 U+2018 U+2019 U+201C U+201D
//   bullet, ellipsis                             U+2022 U+2026
// NFKC first turns the no-break and other compatibility spaces into ASCII
// spaces, full-width letters, digits and @ into ASCII, and superscript, circled
// and mathematical digits into plain digits. Anything else -- direction
// overrides and isolates, zero-width and other invisible or default-ignorable
// characters, variation selectors, tags, fillers, Braille blank, combining
// marks, other scripts' digits -- is refused, and the refusal names the first
// such code point and its position ("character:U+202E at position 1"). This replaces the earlier denylist of
// invisible characters: a character nobody has thought of yet is refused by
// default.
export const CLIENT_TEXT_DISALLOWED =
  "[^ -~\\u00a7\\u00b0\\u00b1\\u00c0-\\u00f6\\u00f8-\\u017f\\u2013\\u2014\\u2018\\u2019\\u201c\\u201d\\u2022\\u2026]";
// The same set, checked on the value AS STORED, before NFKC, plus the only
// characters the fold is trusted to map into it: the no-break and other
// Unicode spaces (U+00A0, U+2000-U+200A, U+202F, U+205F, U+3000), full-width
// ASCII (U+FF01-U+FF5E) and the Unicode hyphen and non-breaking hyphen
// (U+2010, U+2011, read as "-"). The three Latin Extended-A letters whose NFKC
// form leaves the allowlist (U+013F, U+0140, U+0149) are left out, so every
// character this set admits folds into the allowlist and a refusal's position
// is always a position in the value as stored. Everything else is refused
// before NFKC runs. The reason is
// parity: JavaScript and PostgreSQL ship different Unicode versions, and a
// character assigned after PostgreSQL's (U+A7F1, U+1CCEB, the outlined digits
// U+1CCF0-U+1CCF9) folds to ASCII in one engine and not the other. The
// decompositions of these old characters are frozen by Unicode's stability
// policy, so both engines read them identically.
export const CLIENT_TEXT_DISALLOWED_SOURCE =
  "[^ -~\\u00a0\\u00a7\\u00b0\\u00b1\\u00c0-\\u00f6\\u00f8-\\u013e\\u0141-\\u0148\\u014a-\\u017f\\u2000-\\u200a\\u2010\\u2011\\u2013\\u2014\\u2018\\u2019\\u201c\\u201d\\u2022\\u2026\\u202f\\u205f\\u3000\\uff01-\\uff5e]";

// The dashes that survive the allowlist: ASCII hyphen, en and em dash (the
// ASCII hyphen last, as a bracket expression needs). NFKC folds the small and
// full-width hyphen-minus to ASCII; the other Unicode dashes are refused.
const DASHES = "\\u2013\\u2014-";

// Before the phone rules read a value, a number shaped 3-3-4 is joined across
// separators of up to five characters of space ( ) . x, a dash, / | : * ~ _ ;
// + and the allowlisted symbols x (U+00D7), bullet, plus-minus, degree,
// section sign and the straight and curly quotes (251 . . 555 . . 0100,
// 251 ( 555 ) 0100, 251 / 555 / 0100, 251 \u2022 555 \u2022 0100,
// 251\u00d7555\u00d70100). They are listed one by one: a blanket
// non-alphanumeric class would also join "Suites 201-204, 1200 SF". Or it is
// joined across exactly one of , ! ? & = ^ $ % \ ` { } < > (251,555,0100,
// 251?555?0100). A country code or
// leading +1 in front stays outside the join and the ten digits still read as
// a phone. Only the 3-3-4 shape joins across wide separators, so year and count
// ranges do not ("Renovated 2021 - 2026 (12 suites)"), and a comma joins only
// a single-comma 3-3-4 group, so thousands do not (120,000 SF).
export const CLIENT_TEXT_PHONE_JOIN = Object.freeze({
  pattern: `(^|[^0-9])([0-9]{3})(?:[ ().xX\\u00d7\\u2022\\u00b1\\u00b0\\u00a7\\u2018\\u2019\\u201c\\u201d"'/|:*~_;+${DASHES}]{0,5}|[,!?&=^$%\\\\\`{}<>])([0-9]{3})(?:[ ().xX\\u00d7\\u2022\\u00b1\\u00b0\\u00a7\\u2018\\u2019\\u201c\\u201d"'/|:*~_;+${DASHES}]{0,5}|[,!?&=^$%\\\\\`{}<>])([0-9]{4})(?![0-9])`,
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

// A web domain ending: the common ones and the real-estate and health ones.
const DOMAIN_ENDINGS = "com|net|org|io|biz|info|us|co|ai|app|realty|health|properties|homes|law|care|clinic|gov|edu|me";

// Case-insensitive, checked in this order; the first rule that matches names
// the refusal. `target` is the text the rule reads: the normalized value
// (raw), with digit groups joined (digits), or with suite ranges set aside
// (nosuite).
export const CLIENT_TEXT_RULES = Object.freeze([
  // an email address: a dotted domain after an @ (spaced or not); a word
  // against an @ with at most one side spaced ("bob@landlord", "bob @landlord");
  // or (at) / [at] before a dotted domain. "4,200 RSF @ $28.50/SF" and
  // "Rate @ market" are not one.
  Object.freeze({ rule: "email", target: "raw", pattern: "[A-Za-z0-9._%+-] ?@ ?[A-Za-z0-9_-]+([.][A-Za-z0-9_-]+)*[.][A-Za-z]{2,}|[A-Za-z0-9._%+-]( @|@ ?)[A-Za-z0-9_-]*[A-Za-z]|[A-Za-z0-9._%+-] ?[(\\[] ?at ?[)\\]] ?[A-Za-z0-9_-]+([.][A-Za-z0-9_-]+)*[.][A-Za-z]{2,}" }),
  // a URL (http, https, ftp), a bare web domain (its name holds a letter:
  // "Hwy 90.US 29" is a road), a spaced .com/.net/.org ("landlord .com"), a
  // spelled-out "dot com" / "[dot] com", a bracketed dot ("landlord[.]com"),
  // or a link shortener's name followed by a path ("bit.ly/abc", "goo.gl/x",
  // "is.gd/x"; "tinyurl.com/abc" is a bare domain already). Only shortener
  // endings: "$24.00/sq.ft/yr" and "Dr.Smith/Jones" are asking rent and names
  Object.freeze({ rule: "url", target: "raw", pattern: `(https?|ftp)://|www[.]|[A-Za-z0-9-]*[A-Za-z][A-Za-z0-9-]*[.](${DOMAIN_ENDINGS})([^A-Za-z0-9]|$)|[A-Za-z0-9-]*[A-Za-z][A-Za-z0-9-]* ?[.] ?(com|net|org)([^A-Za-z0-9]|$)|(^|[^A-Za-z])[(\\[]? ?dot ?[)\\]]? ?(com|net|org|co)([^A-Za-z]|$)|[A-Za-z0-9-]*[A-Za-z][A-Za-z0-9-]* ?[(\\[] ?[.] ?[)\\]] ?[A-Za-z]{2,}|[A-Za-z0-9-]*[A-Za-z][A-Za-z0-9-]*[.](ly|gl|gd|cc|gy|be)/[A-Za-z0-9]` }),
  // a North-American phone number, however its ten digits are grouped
  Object.freeze({ rule: "phone", target: "digits", pattern: "(^|[^0-9])1?[2-9][0-9]{9}([^0-9]|$)" }),
  // a seven-digit local number: 555-0100, 555 - 0100 (exchange 2-9, as NANP requires)
  Object.freeze({ rule: "local_phone", target: "nosuite", pattern: `(^|[^0-9])[2-9][0-9]{2} ?[.${DASHES}] ?[0-9]{4}([^0-9]|$)` }),
  // an international number: +44 20 7946 0958, + 44 ..., 011 44 ...
  Object.freeze({ rule: "international_phone", target: "digits", pattern: "[+] ?[0-9]{8,}|(^|[^0-9])(011|00)[1-9][0-9]{6,}([^0-9]|$)" }),
  // access-code wording, as whole words: gate code, door combo, entry PIN,
  // alarm code, keypad code ("Westgate Pines", "Fire alarm system" pass); or a
  // gate/door/keypad/alarm/lock/combo/PIN/passcode/key/entry/access/code word
  // before three or more digits, joined by up to four of space : # = * quotes
  // ( [ and dashes, with an optional "is" or "no." ("Gate: #4411", "PIN-4411",
  // "Gate is 4411", "Gate (4411)", "Gate no. 4411"). "Door 3", "Gate 2
  // parking" and "garage 250 spaces" pass. After "code" a year is not a code
  // ("Building code 2021"), "area code 251" is not one, and "zip code" passes
  // only before a real zip (five digits, or ZIP+4): "Zip code 36602" passes,
  // "Zip code 4411" is refused. Lookbehind and lookahead read alike in
  // PostgreSQL ARE and JavaScript.
  Object.freeze({ rule: "access_code", target: "raw", pattern: `(^|[^A-Za-z])(gate|door|key|entry|garage|alarm|keypad|access|lock)[ -]?(codes?|combos?|combination|pins?|passwords?)([^A-Za-z]|$)|(^|[^A-Za-z])(gate|door|keypad|alarm|lock|combo|pin|passcode|key|entry|access)[ :#=*\"'(\\[\\u201c\\u201d\\u2018\\u2019${DASHES}]{0,4}((is|no[.]?)[ :#=*\"'(\\[\\u201c\\u201d\\u2018\\u2019${DASHES}]{0,4})?[0-9]{3,}|(^|[^A-Za-z])(?<!zip )(?<!zip)(?<!zip-)(?<!area )(?<!area)(?<!area-)code[ :#=*\"'(\\[\\u201c\\u201d\\u2018\\u2019${DASHES}]{0,4}((is|no[.]?)[ :#=*\"'(\\[\\u201c\\u201d\\u2018\\u2019${DASHES}]{0,4})?(?!(19|20)[0-9]{2}([^0-9]|$))[0-9]{3,}|(^|[^A-Za-z])zip[ -]?code[ :#=*\"'(\\[\\u201c\\u201d\\u2018\\u2019${DASHES}]{0,4}((is|no[.]?)[ :#=*\"'(\\[\\u201c\\u201d\\u2018\\u2019${DASHES}]{0,4})?(?![0-9]{5}(-[0-9]{4})?([^0-9]|$))[0-9]{3,}` }),
  Object.freeze({ rule: "lockbox", target: "raw", pattern: "(^|[^A-Za-z])(lock[ -]?box(es)?|passcodes?)([^A-Za-z]|$)" }),
  // internal-note wording (a free-text note cannot be recognised in general;
  // the length cap bounds the rest)
  Object.freeze({ rule: "internal_note", target: "raw", pattern: "(^|[^A-Za-z])(internal[ -]?(notes?|only|use)|confidential|do not (share|disclose)|broker[ -]only|not for (the )?clients?)([^A-Za-z]|$)" }),
]);

const RULES = CLIENT_TEXT_RULES.map(entry => ({ ...entry, regex: new RegExp(entry.pattern, "i") }));
const DISALLOWED = new RegExp(CLIENT_TEXT_DISALLOWED);
const DISALLOWED_SOURCE = new RegExp(CLIENT_TEXT_DISALLOWED_SOURCE);
const PHONE_JOIN = new RegExp(CLIENT_TEXT_PHONE_JOIN.pattern, "g");
const DIGIT_JOIN = new RegExp(CLIENT_TEXT_DIGIT_JOIN.pattern, "g");
const SUITE_RANGE = new RegExp(CLIENT_TEXT_SUITE_RANGE.pattern, "gi");
const ROUTE_LABEL = new RegExp(CLIENT_ROUTE_LABEL_PATTERN);
// C0 controls (tab, newline and carriage return included), DEL and the C1
// controls -- the same explicit code points the database checks.
const CONTROL = /[\u0000-\u001F\u007F-\u009F]/;

/**
 * NFKC, then the Unicode hyphen (U+2010, which NFKC also makes of the
 * non-breaking hyphen U+2011) read as the ASCII hyphen. Mirrors
 * ops.tour_client_text_fold.
 */
function foldClientText(value) {
  return value.normalize("NFKC").replace(/\u2010/g, "-");
}

/**
 * The text every client rule reads and every client surface shows: folded
 * (NFKC, Unicode hyphen as ASCII), space runs collapsed to one space, ends
 * trimmed. Only meaningful for a value the allowlist admits. Mirrors
 * ops.tour_client_text_normalize.
 */
export function normalizeClientText(value) {
  return foldClientText(value).replace(/ +/g, " ").replace(/^ | $/g, "");
}

/**
 * "U+00AD at position 7" for the first character outside the allowlist, or
 * null. The position counts code points from 1, in the value as stored (the
 * source set is checked there first); a character that only the fold produces
 * is reported at its position in the folded text.
 */
export function firstDisallowedCodePoint(value) {
  for (const [text, pattern] of [[value, DISALLOWED_SOURCE], [foldClientText(value), DISALLOWED]]) {
    const match = pattern.exec(text);
    if (match) {
      const hex = text.codePointAt(match.index).toString(16).toUpperCase().padStart(4, "0");
      return `U+${hex} at position ${Array.from(text.slice(0, match.index)).length + 1}`;
    }
  }
  return null;
}

/**
 * The name of the first client text rule `value` breaks, or null when a client
 * may see it inside an allowlisted field. Mirrors ops.tour_client_text_violation.
 */
export function clientTextViolation(value, maximum = CLIENT_TEXT_MAX_CHARS) {
  if (typeof value !== "string") return "not_text";
  if (CONTROL.test(value)) return "control_character";
  const disallowed = firstDisallowedCodePoint(value);
  if (disallowed) return `character:${disallowed}`;
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
