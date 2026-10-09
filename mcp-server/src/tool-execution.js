import { ToolError } from "./tool-error.js";
import { MutationRegistryRefusal, assertRegisteredOperation } from "./mutation-registry.js";
import { TOOLS } from "./tool-registry.js";
import { BLOCKER_DECIDER_REASON, ESCALATION_REASON, classifyLoopText, loopRowText, needsDecider, parksADecision } from "./verb-gate-checks.js";
import { authorizationClassForActor } from "./identity.js";
import { canExercisePartnerAuthority } from "./partner-authority.js";
import { assertFoundationAssuranceOracleSeat, foundationAssuranceOracleLane } from "./foundation-assurance-minimum-registration.v5.js";
import { gateZeroOracleSeatLane } from "./gate-zero-outcome-store.v5.js";
import { V5BoundaryDoorRefusal, passBoundaryDoor } from "./global-boundaries-door.v5.js";

// DEFECT 2, HALF (b) (found 2026-08-13, decision 7026246b): a write whose bad
// input reaches the database raw (an enum this file never learned to validate,
// a foreign key nobody checked first) throws a driver error, not a ToolError —
// and mcp.js's top-level catch used to flatten EVERY non-ToolError into a bare
// {"error":"internal error"} with no field, no constraint, no allowed values.
// Half (a) closes the specific gap (marker, domain, above); this is the
// backstop for every OTHER enum/FK this file has not yet learned to check —
// Postgres SQLSTATE class 23 (integrity_constraint_violation) always names the
// constraint and usually the column, so that much can be surfaced honestly
// without ever touching the connection string (which lives in env bindings,
// never in a query or its error — nothing here reads or forwards env).
// Deliberately narrow: anything outside class 23 (a real connection or driver
// fault) returns null and falls through to the generic handler unchanged,
// because this is a translator for bad input, not a catch-all.
const PG_VIOLATION_KIND = Object.freeze({
  "23514": "check_violation",       // e.g. marker/kind fails its CHECK
  "23503": "foreign_key_violation", // e.g. domain names no row in loop_domain
  "23505": "unique_violation",
  "23502": "not_null_violation",
});

const CONNSTR_RE = /\b\w+:\/\/[^\s'"]+/gi;

// defense in depth; pg errors don't carry one today
export function redact(s) {
  return typeof s === "string" ? s.replace(CONNSTR_RE, "[redacted]") : s;
}

// THE TWO CLASSES THAT COST THE MOST ON 2026-08-21, neither of which is a
// constraint violation, so neither was translated and both reached the caller
// as a bare "internal error" naming nothing:
//
//   42501 insufficient_privilege — complete-capability-project took a row lock
//   on an append-only table the serving role holds only insert/select on. Row
//   locks need UPDATE. Every completion since the verb shipped died here, which
//   is why the AI Engineering Suite read 0 complete of 51 while six of its
//   projects were finished. Hours went into it because the error named nothing.
//
//   22P02 invalid_text_representation — a short id or a "#213"-style reference
//   reaching a uuid column. Hit twice in one session, on close-loop's successor
//   field and on update-decision, both of which document the human-readable
//   form in their own hints. Two defects filed under one class.
//
// A permission or a type mismatch is not less actionable than a CHECK
// violation, and it is far more likely to be a bug in the verb rather than in
// the caller's arguments — which is exactly why hiding it is expensive.
const PG_FAULT_KIND = Object.freeze({
  "42501": "insufficient_privilege",
  "22P02": "invalid_text_representation",
  "42703": "undefined_column",
  "42P01": "undefined_table",
  "42883": "undefined_function",
});

const PG_FAULT_HINT = Object.freeze({
  "42501": "the database refused this to the role the server connects as — the grant is missing, " +
           "or the statement takes a row lock (select ... for update) on a table this role may only " +
           "read. Read the handler's SQL before assuming a server fault; do not widen the grant " +
           "without checking whether the lock is needed at all.",
  "22P02": "a value did not parse as its column's type — most often a short id, a '#123' reference " +
           "or a name where a uuid is required. Check which form this field actually accepts; the " +
           "verb's own description may name a friendlier form than the code accepts.",
  "42703": "the statement names a column that does not exist — a schema change and this code have drifted.",
  "42P01": "the statement names a table that does not exist — a migration is missing on this database.",
  "42883": "the statement calls a function that does not exist — a migration is missing on this database.",
});

export function pgConstraintError(e) {
  const code = e && e.code;
  if (typeof code !== "string") return null;
  if (PG_VIOLATION_KIND[code]) {
    return new ToolError({ error: "invalid_field_value", violation: PG_VIOLATION_KIND[code],
      constraint: e.constraint || null, table: e.table || null, column: e.column || null,
      detail: redact(e.detail) || null,
      hint: "a value failed a database constraint — check it against this verb's documented " +
            "enum or required fields; this is the constraint the value actually violated, not a stack trace" });
  }
  if (PG_FAULT_KIND[code]) {
    return new ToolError({ error: "database_refused_the_statement", fault: PG_FAULT_KIND[code],
      sqlstate: code,
      table: e.table || null, column: e.column || null,
      message: redact(e.message) || null,
      detail: redact(e.detail) || null,
      hint: PG_FAULT_HINT[code] });
  }
  return null;
}

// Pure decision logic for the optimistic-lock check, isolated from the DB
// round trip so it is unit-testable without a connection. THE BUG THIS FIXES
// (found 2026-08-13, decision 7026246b): the old check was `current !==
// baseVersion`, a STRICT comparison with no coercion. `current` always comes
// back a genuine JS number (the loop_item.version column is `int`, and both
// the Worker's and local-verb's Pool driver parse int4 to Number), but
// `baseVersion` arrives verbatim from the caller's JSON payload — and MCP
// tool-call arguments are never validated against inputSchema server-side
// (see callTool in mcp.js: `rpc.params?.arguments` is passed straight
// through). A caller that sent base_version as the JSON STRING "1" instead
// of the number 1 — e.g. copying a value that had been rendered as text —
// produced `1 !== "1"` => true: a false version_conflict on a loop that had
// just been created and never touched again, reproduced twice on 2026-08-13
// (loop #350, base_version 1, current_version 1). Coercing both sides to
// Number before comparing fixes this without weakening the check: a REAL
// mismatch (e.g. 1 vs 3) still differs after coercion.
// THE OTHER HALF OF THE SAME DEFECT (found 2026-08-13, loop 353). compareVersion
// above fixed ONE field, base_version, against mistyped arrival. The cause it
// names — "MCP tool-call arguments are never validated against inputSchema
// server-side" — was never field-specific, and leaving it at one field meant the
// next mistyped argument was only a matter of which verb got called.
//
// It got called. `teach` decides a rule's SCOPE with `args.personal ? ... : null`
// (loose truthiness) and echoes it back with `args.personal === true` (strict).
// A boolean that arrived as the STRING "false" is truthy at the first line and
// false at the second, so the verb stored a SHARED rule as PERSONAL while
// reporting personal_requested:false. That is not a cosmetic disagreement: scope
// decides WHO a taught rule binds, the response said the caller got what it
// asked for, and only a hand comparison of two exported files caught it. Twice
// in one session.
//
// Fixing teach alone would have been the same mistake a second time: a sweep
// found 17 sites reading a declared boolean or number with loose truthiness or
// bare arithmetic, SEVEN of which write the wrong value straight to the database
// (drift_critical on add-loop and update-loop, also_listing_side on add-premises,
// found and internal on record-finding, close on score-campaign), and eight more
// that silently skip a dedup or plausibility gate. So the coercion happens ONCE,
// at the choke point every verb passes through, and no handler has to remember.
//
// STRICTLY SCHEMA-DRIVEN, NEVER VALUE-SNIFFING. Only a property whose declared
// type is exactly "boolean", "integer" or "number" is touched. A field declared
// "string" is never inspected, so free text that happens to read "true" (a
// human_quote, a note, a rationale) is untouchable by construction. A union
// (oneOf, anyOf, or type given as an array) is skipped rather than guessed at —
// log-decision's `about` takes string OR array, and patch-deal-field has
// nullable strings.
//
// IT THROWS RATHER THAN GUESSING. "true"/"false" and numeric strings map
// cleanly; anything else in a typed field is a caller error and now fails
// loudly, in the same spirit as invalid_base_version. Silently leaving an
// unmappable value in place is what produced this defect in the first place.
//
// Recursive, because two of the affected flags are not top-level: add-premises
// carries also_listing_side inside ownership[] and force_new one level deeper
// inside ownership[].new_party.
// ── THE MARKUP WRITE DOOR (2026-08-14) ───────────────────────────────────────
// ops/store-markup-scan.py has been catching this AFTER the fact for weeks: a
// caller composes several long fields as one block of text, a field swallows its
// own closing tag, and every parameter after that tag is stored NULL. Six active
// shared rules carried it on 2026-08-13, the oldest four days old, five with a
// partner's verbatim quote absorbed into the rule statement.
//
// A detector cannot un-write a NULL, and this record layer refuses to edit a
// closed row on purpose — "a closed loop is history" — so damage that lands and
// is then closed is permanent. The write door is the only place it can actually
// be stopped, which is here.
//
// CORRUPTION vs MENTION is the same structural test ops/store-markup-scan.py's
// classify() uses, deliberately, because two doors disagreeing about one row is
// worse than either rule alone: the field swallowed ITS OWN closing tag, or it
// carries a bare marker that ate whatever should have followed. Markers quoted
// in backticks on one line are prose ABOUT the defect — the rule documenting it,
// the loops that tracked the cleanup, this comment — and must stay writable.
const TOOL_CALL_MARKERS = ["<parameter", "</parameter", "<invoke", "</invoke"];

export function looksLikeToolCallMarkup(field, value) {
  if (typeof value !== "string" || value === "") return false;
  // close_outcome's own closing tag is </outcome>; the scan strips the close_
  // prefix the same way, and the two must not disagree about the same row.
  const ownCloser = `</${String(field).replace(/^close_/, "")}>`;
  if (value.includes(ownCloser)) return true;
  for (const marker of TOOL_CALL_MARKERS) {
    let idx = value.indexOf(marker);
    while (idx !== -1) {
      const before = value.lastIndexOf("`", idx);
      const after = value.indexOf("`", idx);
      const quoted = before !== -1 && after !== -1
        && !value.slice(before, after).includes("\n");
      if (!quoted) return true;
      idx = value.indexOf(marker, idx + 1);
    }
  }
  return false;
}

// REQUIRED ARGUMENTS, ENFORCED AT THE DOOR (2026-08-14).
//
// Every verb declares `required` in its inputSchema and, until this existed,
// nothing checked it. mcp.js hands rpc.params.arguments through untouched and
// the local CLI path does the same, so a required field that was misspelled or
// simply absent arrived as undefined and the handler ran regardless.
//
// WHAT THAT PRODUCED, measured live rather than imagined. search-doctrine builds
// websearch_to_tsquery('english', undefined), which Postgres does not treat as
// an error — it matches nothing:
//
//     search-doctrine {"query":"HIPAA"}  ->  ok:true, hits:[], total:0
//     search-doctrine {}                 ->  ok:true, hits:[], total:0
//     search-doctrine {"q":"HIPAA"}      ->  20 hits
//
// A call with NO ARGUMENTS AT ALL came back clean and empty.
//
// AN EMPTY RESULT IS INDISTINGUISHABLE FROM A GENUINE ABSENCE, which is what
// makes this worse than a crash. On 2026-08-14 a session searched doctrine for a
// settled council ruling, got total:0, concluded the ruling did not exist, and
// filed a defect claiming the doctrine read path was broken. The ruling was
// there; the parameter name was wrong. Rule c53beeaa already says an ok:true
// confirms the call PARSED and never that the values landed — this enforces that
// at the boundary instead of hoping each of a hundred handlers remembers.
//
// It sits beside coerceArgsToSchema deliberately, per the same 2026-08-13 ruling
// that put coercion at the choke point rather than in seventeen handlers.
//
// PRESENCE, not truthiness: `false` and `0` are arguments a caller meant. `null`
// and `""` are what an unfilled template produces and carry no instruction.
//
// The near-miss hint is not decoration. The observed failure was a caller who
// HAD the schema and still sent `query` for `q`, so the error names the field it
// wanted and echoes the unrecognised keys that were sent instead.
export function assertRequiredArgs(schema, args) {
  const required = schema && Array.isArray(schema.required) ? schema.required : null;
  if (!required || !required.length) return args;
  const bag = (args && typeof args === "object" && !Array.isArray(args)) ? args : {};
  // A field the schema DECLARES nullable (type ["string","null"], or an anyOf /
  // oneOf branch of type "null") takes null as a real answer: "no appointment",
  // "no prior route version". Refusing it made such verbs uncallable, since the
  // key is required AND its only honest value was null (append-tour-route-stop,
  // append-tour-route-stop-transition, search-tour-properties, 2026-09-23).
  // Absent keys and "" stay missing for every field.
  const props = (schema && schema.properties) || {};
  const nullable = (k) => {
    const p = props[k];
    if (!p || typeof p !== "object") return false;
    if (p.type === "null" || (Array.isArray(p.type) && p.type.includes("null"))) return true;
    return [p.anyOf, p.oneOf].some((alts) => Array.isArray(alts) && alts.some((a) => a && a.type === "null"));
  };
  const missing = required.filter((k) => {
    const v = bag[k];
    if (v === null && nullable(k)) return false;
    return v === undefined || v === null || v === "";
  });
  if (!missing.length) return args;
  const known = new Set(Object.keys(schema.properties || {}));
  const unrecognised = Object.keys(bag).filter((k) => !known.has(k));
  const payload = {
    error: "missing_required",
    missing,
    hint: `this verb requires ${missing.map((m) => JSON.stringify(m)).join(", ")}`,
  };
  if (unrecognised.length) {
    payload.unrecognised = unrecognised;
    payload.hint += `; it received ${unrecognised.map((u) => JSON.stringify(u)).join(", ")}` +
      `, which it does not accept — check the argument NAME against the schema before` +
      ` concluding the verb is broken or the answer is empty`;
  }
  throw new ToolError(payload);
}

export function coerceArgsToSchema(schema, args, path = "") {
  if (!schema || !args || typeof args !== "object" || Array.isArray(args)) return args;
  const props = schema.properties;
  if (!props) return args;
  for (const [key, spec] of Object.entries(props)) {
    if (!spec || !Object.prototype.hasOwnProperty.call(args, key)) continue;
    const v = args[key];
    if (v === undefined || v === null) continue;
    const where = path ? `${path}.${key}` : key;
    // Refused BEFORE any coercion or type branch: a leaked tag makes the value
    // wrong whatever its declared type, and the fields this lands in (body,
    // source_note, outcome) are plain strings that would otherwise sail through
    // untouched. The error names the field and the fix, because the caller that
    // does this is mid-way through composing several long fields and needs to be
    // told which one ran into the next.
    if (looksLikeToolCallMarkup(key, v)) {
      throw new ToolError({
        error: "tool_call_markup_in_value", field: where,
        hint: `${where} contains tool-call markup (a leaked </${String(key).replace(/^close_/, "")}> `
          + `or <parameter …> tag). The field ran into the one after it, and every field `
          + `after the leak would be stored empty. Pass each field as its own argument `
          + `rather than composing them as one block of text, then retry. Prose ABOUT `
          + `this defect is allowed when the markers are quoted in backticks.`,
      });
    }
    // A union is ambiguous by design; coercing one would destroy the other.
    if (spec.oneOf || spec.anyOf || Array.isArray(spec.type)) continue;
    if (spec.type === "boolean") {
      if (typeof v === "boolean") continue;
      if (typeof v === "string") {
        const s = v.trim().toLowerCase();
        if (s === "true") { args[key] = true; continue; }
        if (s === "false") { args[key] = false; continue; }
      }
      throw new ToolError({ error: "invalid_boolean", field: where, got: typeof v === "string" ? v : typeof v,
        hint: `${where} is declared boolean; pass true or false, not a quoted or numeric value` });
    }
    if (spec.type === "integer" || spec.type === "number") {
      if (typeof v === "number") continue;
      if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v.trim()))) {
        args[key] = Number(v.trim());
        continue;
      }
      throw new ToolError({ error: "invalid_number", field: where, got: typeof v === "string" ? v : typeof v,
        hint: `${where} is declared ${spec.type}; pass a number` });
    }
    if (spec.type === "object") { coerceArgsToSchema(spec, v, where); continue; }
    // A JSON-STRING ARRAY IS STILL AN ARRAY ARGUMENT, and until 2026-08-15 it fell
    // straight through here uncoerced, because this branch required Array.isArray
    // BEFORE it would look at anything. What that cost, live: doctrine-sections
    // measured `.length` on an ~80-character string and answered
    // "batch_too_large, max 50" for a batch of two, while a single id slipped
    // under the limit and died casting a string to uuid[]. claim-doctrine-sections
    // iterated the same string into characters, so no session could claim a
    // doctrine section and the single-writer write path was down for hours.
    //
    // Parsed HERE rather than in the handlers, per the 2026-08-13 ruling that put
    // coercion at the choke point instead of in seventeen of them. Every verb
    // taking an array gets this, not only the three that happened to be caught.
    let value = v;
    if (spec.type === "array" && typeof value === "string") {
      const text = value.trim();
      if (text.startsWith("[")) {
        try {
          const parsed = JSON.parse(text);
          if (Array.isArray(parsed)) { args[key] = value = parsed; }
        } catch { /* leave it; the handler's own validation refuses it by name */ }
      }
    }
    if (spec.type === "array" && Array.isArray(value) && spec.items) {
      value.forEach((item, i) => coerceArgsToSchema(spec.items, item, `${where}[${i}]`));
    }
  }
  return args;
}

// These names describe server-owned authority, never data a tool invocation may
// claim. This is deliberately TOP-LEVEL only: record findings, template maps,
// metrics, and correction payloads legitimately carry free-form business keys
// such as `action` or `profile`, and those nested keys cannot widen authority.
// call-verb recursion and composite dispatch each hand their inner arguments
// back to this boundary as a new top-level invocation.
const RESERVED_AUTHORITY_ARGUMENT_FIELDS = new Set([
  "tenant", "tenant_id", "organization_tenant_id", "sponsor", "sponsoring_human_id",
  "sponsoring_human_slug", "human_slug", "identity", "actor", "runtime_principal", "audience",
  "authorization", "authorization_class", "profile", "capability", "capabilities",
  "action", "actions", "action_authority", "action_authorities", "allowed_actions",
  "write", "writes_records", "calls_models", "call_models",
]);

// Authority and registry checks remain distinct. The server resolves and audits
// the sponsor separately from the runtime actor; caller fields can select
// neither. Joe's 2026-08-26 ruling retired the humanOnly refusal and admitted
// the two server-bound local partner doors, but reviewers, probes, Hermes, Grok,
// and unsponsored runtimes remain outside partner authority.
export function assertNoCallerAuthorityFields(args) {
  if (args && typeof args === "object" && !Array.isArray(args) &&
      Object.keys(args).some((key) => RESERVED_AUTHORITY_ARGUMENT_FIELDS.has(key)))
    throw new ToolError({ error: "caller_authority_field_forbidden" });
  return args;
}

// One registered-handler path for direct MCP calls and composite verbs. The
// MCP layer applies its profile gate first; this helper owns caller-authority,
// registry lookup, human-only, coercion, and handler/envelope gates. Keeping
// the first gate here makes direct MCP, call-verb recursion, and composites
// fail closed before a handler or database client can be used.
// EVERY DECLARED CLOSED VOCABULARY IS ENFORCED HERE, AND THIS IS THE ONLY
// PLACE THAT ENFORCES IT GENERICALLY.
//
// WHY IT EXISTS. There is no JSON-schema validator anywhere in this server --
// no ajv, no jsonschema -- so an `enum` in an inputSchema was documentation
// that nothing read. 73 of 89 enum fields were guarded BY HAND in their own
// handler with one(); the other 16, across 15 verbs, passed whatever they were
// given straight through to Postgres. Thirteen of the columns behind them
// carry no check constraint either, so for those the declared vocabulary was
// enforced at NO layer.
//
// THE COST, MEASURED 2026-09-18: v_code_finding.epistemic_status holds
// 'human_stated', which is not one of the nine values record-finding declares.
// It is a legitimate value of a DIFFERENT closed vocabulary, event.cause, and
// nothing anywhere noticed the two being confused. Three of the nine declared
// values have never been used at all.
//
// A HAND-WRITTEN GUARD PER FIELD WOULD CLOSE THE 16 AND NOT THE SEVENTEENTH.
// The gap reappears the next time someone declares an enum and forgets the
// one() call, which is precisely how these 16 arose. Reading the schema the
// verb already publishes closes the class, including for verbs not yet
// written.
//
// THE REFUSAL NAMES THE FIELD AND THE ALLOWED VALUES, which the database
// cannot do: 219 check constraints span more than one column, so Postgres
// reports `column: null` and the caller is told a rule broke without being
// told which of their inputs broke it. This refusal happens BEFORE the
// database and can say exactly what to send instead.
function assertDeclaredVocabularies(name, tool, args) {
  const properties = tool?.inputSchema?.properties;
  if (!properties || !args || typeof args !== "object" || Array.isArray(args)) return;
  for (const [field, spec] of Object.entries(properties)) {
    const allowed = spec?.enum || spec?.items?.enum;
    if (!Array.isArray(allowed) || allowed.length === 0) continue;
    const value = args[field];
    // Absent and null are the field not being sent. Optionality is the
    // schema's business, and required-field checks already run elsewhere;
    // this one rules on VALUE only.
    if (value === undefined || value === null) continue;
    const sent = spec?.items?.enum && Array.isArray(value) ? value : [value];
    for (const one of sent) {
      if (allowed.includes(one)) continue;
      throw new ToolError({
        error: "value_not_in_declared_vocabulary",
        verb: name, field,
        received: typeof one === "string" ? one : typeof one,
        allowed,
        hint: `\`${field}\` is a closed vocabulary on ${name}. Send one of the ` +
              `values in \`allowed\`. This refusal comes from the verb's own ` +
              `published schema, before any database write, so nothing was ` +
              `recorded and nothing needs undoing.`,
      });
    }
  }
}

export async function assertRegisteredToolInput(name, tool, args = {}) {
  assertDeclaredVocabularies(name, tool, args);
  try {
    await assertRegisteredOperation(name, tool, args);
  } catch (error) {
    if (error instanceof MutationRegistryRefusal)
      throw new ToolError({ error: error.error, operation: error.operation,
        ...(error.fields ? { fields: error.fields } : {}) });
    throw error;
  }
}

export async function executeRegisteredTool(client, actor, name, args = {}) {
  const tool = TOOLS[name];
  if (!tool) throw new ToolError({ error: "unknown_tool", name });
  assertNoCallerAuthorityFields(args);
  // ── PORTED VERB GATES (bypass audit C33/C34, 2026-09-24), MOVED HERE
  // (Opus re-review, 2026-09-24) from mcp.js's callTool(). callTool() is NOT
  // the one choke point: mcp-server/local-verb.mjs's BREAK-GLASS mode
  // (a direct DATABASE_URL connection, used for local/rehearsal calls) calls
  // executeRegisteredTool() DIRECTLY and never goes through callTool() at
  // all, so the check placed there missed that door entirely. THIS function
  // is the actual chokepoint: local-verb.mjs's own comment at its call site
  // says so ("the one choke point that also applies argument type coercion
  // and raw-DB-error translation"), and callTool()'s read AND write branches
  // both call it too. One placement, three doors: callTool() read, callTool()
  // write, and local-verb.mjs break-glass (both its read and write shapes).
  // Still purely in memory, before any client.query call in this function —
  // same testability as the callTool()-level placement had.
  if (name === "add-loop") {
    if (needsDecider(args))
      throw new ToolError({ error: "capability_no_decider", hint: BLOCKER_DECIDER_REASON });
    if (parksADecision(args)) {
      const { allow, why } = classifyLoopText(loopRowText(args));
      if (!allow)
        throw new ToolError({ error: "internal_decision_parked", why, hint: ESCALATION_REASON });
    }
  }
  // HUMAN-ONLY MEANS PARTNER AUTHORITY, NOT A SECOND CHAT WINDOW. A verified
  // partner passes directly. A native Codex/Claude grant or local machine door
  // passes only when partner-authority.js can derive its sponsor from
  // server-held identity state and bind it to that sponsor's authority DB
  // credential. Probe, review, unknown, unverified, and merely caller-claimed
  // sponsors still refuse before schema validation or database access.
  //
  // This restores the useful part of Joe's 2026-08-26 ruling and removes the
  // WR-000021 overcorrection: the ledger still keeps acting_actor_slug as the
  // actual machine actor while mcp.js separately records the verified sponsor.
  // The registry-wide test covers every present and future humanOnly verb.
  if (tool.humanOnly === true) {
    const actorClass = authorizationClassForActor(actor);
    // Identity merges require the human caller even when a machine holds
    // sponsor-scoped partner authority. Other partner-authority verbs retain
    // their existing native/local agent route.
    if (actorClass !== "verified_partner" &&
        (name === "confirm-merge" || !canExercisePartnerAuthority(actor)))
      throw new ToolError({ error: "human_only_verb_requires_verified_partner",
        verb: name, actor_class: actorClass,
        hint: name === "confirm-merge" ? "confirm-merge requires a verified human partner; machine identities cannot confirm identity merges." :
              "this verb records a partner-authority act and requires either the verified partner " +
              "or a server-verified native/local agent bound to that partner's sponsor-scoped " +
              "authority connection." });
  }
  // ORACLE-SEAT-ONLY IS ENFORCED HERE, AND THIS IS THE ONLY PLACE THAT
  // ENFORCES IT (2026-09-13, DoctorCRE v5 slice V5-A02 Step B). It is a THIRD
  // authority shape beside humanOnly and the ordinary sponsored-agent surface,
  // and it exists because r7 registers a producer whose role is
  // `independent_control_plane_oracle` and whose seat is a machine: the
  // independent Codex reviewer lane, whose derived class is `review_agent`.
  // Joe ruled on 2026-09-13 (decision d4e5f6a7-b8c9-4d0e-9f1a-2b3c4d5e6f70)
  // that this seat records the Gate Zero outcome on its own authority, with no
  // partner countersign.
  //
  // IT IS THE MIRROR IMAGE OF humanOnly, not a relaxation of it. humanOnly
  // refuses every machine; this refuses every human AND every machine but one.
  // A verified partner is refused here on purpose: a partner signing an
  // independent oracle's receipt is the exact thing the oracle exists to
  // prevent, and the partner's own act in this chain sits downstream on the
  // benchmark manifest, which is still humanOnly.
  //
  // THE CLASS IS NOT THE TEST. `grok-reviewer` authenticates through the same
  // review-token door and derives the same `review_agent` class, so admitting
  // the class would hand the Gate Zero signature to a lane nobody ruled on.
  // deriveGateZeroProducerSeat checks the class AND the seat, reading the lane
  // off the frozen registration's DERIVED holder ref -- so putting that seat
  // back to unstaffed closes this door too, with nothing else touched.
  //
  // ENFORCED IN THREE INDEPENDENT PLACES, deliberately: here, again inside the
  // handler, and a third time in SQL by ops.gate_zero_producer_actor_id(). The
  // flag alone would be a label, which is the defect WR-000021 found on
  // humanOnly between 2026-08-26 and 2026-09-11.
  if (tool.oracleSeatOnly === true) {
    const foundation = tool.oracleFamily === "foundation-assurance";
    const lane = foundation ? foundationAssuranceOracleLane(name) : gateZeroOracleSeatLane();
    const actorClass = authorizationClassForActor(actor);
    if (lane === null || actorClass !== "review_agent" || actor?.human === true || actor?.slug !== lane)
      throw new ToolError({ error: "oracle_seat_verb_requires_the_staffed_seat",
        verb: name, actor_class: actorClass,
        seat_staffed: lane !== null,
        hint: "this verb records an independent control-plane oracle's receipt and refuses every actor except " +
              "the one review-token seat that holds it — including verified partners, sponsored agents, and a " +
              "second review-token lane deriving the same authority class. Report what you would have recorded " +
              "and let the seat run it." });
    if (foundation) {
      try { assertFoundationAssuranceOracleSeat(actor, name); }
      catch (error) {
        throw new ToolError({ error: error.code || "foundation_assurance_oracle_seat_required",
          ...(error.detail || {}) });
      }
    }
  }
  await assertRegisteredToolInput(name, tool, args);
  // TYPE COERCION AT THE CHOKE POINT (loop 353, 2026-08-13). See
  // coerceArgsToSchema above for what this fixes and why it is here rather than
  // in the seventeen handlers that would otherwise each need to remember. It
  // runs before the humanOnly-passed handler sees anything, so no verb can read
  // a declared boolean or number in the wrong JS type.
  coerceArgsToSchema(tool.inputSchema, args);
  // REQUIRED-ARGUMENT CHECK, immediately after coercion so a value that only
  // becomes present once coerced is judged in its final form. See
  // assertRequiredArgs above: a missing required field used to reach the
  // handler as undefined and come back as a confident empty answer.
  assertRequiredArgs(tool.inputSchema, args);
  // V5-S01 GLOBAL BOUNDARIES, AT THE ONE SEAM EVERY DOOR PASSES (2026-09-25).
  // Evaluated here, after coercion and the required-argument check so the
  // verdict reads the arguments in their final form, and before the handler
  // so nothing a boundary refuses can reach the database. The door only ever
  // ADDS a refusal; every gate above and every check inside the handler still
  // runs. SHADOW until Joe approves enforcement: V5_BOUNDARY_DOOR_MODE in
  // global-boundaries-door.v5.js is the constant "shadow", passBoundaryDoor
  // records and logs a refusal verdict and never throws in that mode, and the
  // catch below is reachable only once that constant is flipped to "enforce".
  // The door context (connectivity "online") is the server's, never a field a
  // caller sent.
  try {
    passBoundaryDoor({ verb: name, write: tool.write === true, actor, args, now: new Date().toISOString() });
  } catch (error) {
    if (error instanceof V5BoundaryDoorRefusal) throw new ToolError(error.payload);
    throw error;
  }
  // DEFECT 2, HALF (b): every verb funnels through here — the one choke point
  // where a raw DB error can be translated into a clean ToolError before it
  // ever reaches the transport (mcp.js's callTool/dispatch, or local-verb.mjs),
  // so the fix lands once instead of being re-implemented per caller. A
  // ToolError a handler threw on purpose passes straight through unchanged;
  // only an UNTRANSLATED error gets a look from pgConstraintError, and only a
  // recognized class-23 violation gets rewritten — anything else (a real
  // connection or driver fault) still surfaces as-is for the transport's own
  // generic handling.
  try {
    // THE AUTHENTICATED CALL IS NOT ESTABLISHED HERE ANY MORE (amendment 9,
    // 2026-09-14). It used to be: this line asked identity.js for THIS actor's
    // dispatcher and ran the handler inside it. The fifth review round found
    // what that required identity.js to publish — `dispatchFor(actor)`, a
    // callable that enters a context — and that a probe composing it with the
    // equally public review door ran its own code as `review_agent`.
    //
    // SO THE ENTRY MOVED INTO identity.js, where the bearer is matched and the
    // context entered in one module-internal act — and since the sixth
    // correction round (2026-09-15) what runs inside it is named rather than
    // handed over: `serveAuthenticatedCall(bearer, correlationId, entryName)`
    // resolves the name in a frozen map of the server's own entries. No verb
    // dispatched through this function runs inside that context, including this
    // one; nothing is threaded through here, so there is nothing here for a
    // caller to aim.
    //
    // WHAT THAT NARROWS, stated rather than discovered later: a verb reached
    // through any OTHER door — the OAuth grant path, the agent, Hermes,
    // continuity and local bearers, `./run.sh call`, a direct import — runs with
    // NO authenticated call at all, so r7's receipt surfaces refuse. That is
    // correct for the Gate Zero producer, whose candidate is the deployed
    // Worker's own build stamp (build-stamp.js) and which has nothing to say
    // about a local checkout.
    return await tool.handler(client, actor, args);
  } catch (e) {
    if (e instanceof ToolError) throw e;
    throw pgConstraintError(e) || e;
  }
}

