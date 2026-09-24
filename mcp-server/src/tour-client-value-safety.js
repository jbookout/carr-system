// V5-J303 client value safety: the ONE rule for what text may sit inside an
// allowlisted client Tour field (CLIENT_TOUR_FIELD_KEYS). The key allowlist
// says WHICH fields a client sees; this rule says what those fields may
// CONTAIN, so an owner phone, an email, a gate code or an internal note typed
// into `parking` or `availability` cannot ride an allowed key to a client.
//
// Three enforcement points use this rule and must not drift:
//   1. the database, at seal time and on every client read, through
//      ops.tour_client_text_safe() (migrations/0591) -- the authority;
//   2. clientStop in tour-sharing.js, the last filter on the browser share;
//   3. the PDF packet renderer (tour-packet-render.js).
// The patterns below are written in the subset of regex syntax that
// JavaScript and PostgreSQL ARE read identically (no \b, no \y, no lookbehind,
// explicit [^A-Za-z0-9] boundaries), and test/tour-client-share-allowlist
// .test.mjs asserts the SQL function carries exactly these strings. Change one
// and you must change the other in the same commit.
//
// Refusal is the only outcome. Nothing here rewrites or redacts a value: a
// value that fails is not shown, and the seal that would have sealed it
// refuses.

export const CLIENT_TEXT_MAX_CHARS = 120;
export const CLIENT_ROUTE_LABEL_PATTERN = "^[A-Za-z0-9]{1,3}$";

// Case-insensitive. Each entry names what it catches.
export const CLIENT_TEXT_FORBIDDEN_PATTERNS = Object.freeze([
  // any email address, or any stray @ handle
  "@",
  // a URL or a bare web domain
  "https?://|www[.]|[A-Za-z0-9-][.](com|net|org|io|co|us|biz|info|me)([^A-Za-z0-9]|$)",
  // a North-American phone number: 251-555-0100, (251) 555-0100, 2515550100
  "(^|[^0-9])[(]?[0-9]{3}[)]?[-. ]?[0-9]{3}[-. ][0-9]{4}([^0-9]|$)",
  "(^|[^0-9])[0-9]{10,11}([^0-9]|$)",
  // a seven-digit local number: 555-0100
  "(^|[^0-9])[0-9]{3}[-.][0-9]{4}([^0-9]|$)",
  // an international number: +44 20 7946 0958
  "[+][0-9][0-9 ().-]{7,}[0-9]",
  // access-code wording
  "(gate|door|key|entry|garage|alarm|keypad|access)[ -]?(code|combo|combination|pin|password)",
  "lock[ -]?box|alarm|keypad|passcode",
  // internal-note wording (a free-text note cannot be recognised in general;
  // the length cap bounds the rest)
  "internal[ -]?(note|only|use)|confidential|do not (share|disclose)|broker[ -]only|not for (the )?client",
]);

const FORBIDDEN = CLIENT_TEXT_FORBIDDEN_PATTERNS.map(source => new RegExp(source, "i"));
const ROUTE_LABEL = new RegExp(CLIENT_ROUTE_LABEL_PATTERN);
// C0 controls (tab, newline and carriage return included) and DEL.
const CONTROL = /[\u0000-\u001F\u007F]/;

/** True when `value` is text a client may see inside an allowlisted field. */
export function isClientSafeText(value, maximum = CLIENT_TEXT_MAX_CHARS) {
  if (typeof value !== "string") return false;
  if (CONTROL.test(value)) return false;
  const text = value.trim();
  if (!text || text.length > maximum) return false;
  for (let index = 0; index < FORBIDDEN.length; index++) if (FORBIDDEN[index].test(text)) return false;
  return true;
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
    out[key] = typeof part === "string" ? part.trim() : part;
  }
  if (out.value === undefined && out.min === undefined && out.max === undefined) return undefined;
  if (typeof out.min === "number" && typeof out.max === "number" && out.min > out.max) return undefined;
  return out;
}
