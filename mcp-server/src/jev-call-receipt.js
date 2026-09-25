// Server-side Jev call log: the Worker makes the TypeSafe (Jev) call itself and
// appends a server-timestamped receipt before it hands the answers back.
//
// WHY THE WORKER AND NOT THE CALLER. Jev enforcement gates used to credit
// evidence the gated model could write: the session transcript and
// out/jev-calls.jsonl. The model shares one OS user with the hooks, so any
// local log or local credential is forgeable. Here the only party holding the
// TypeSafe key is the Worker (env.TYPESAFE_API_KEY, bound by hand), the
// receipt is appended by the Worker's writer transaction, and recorded_at is
// the database clock.
//
// DETECTABLE, NOT PREVENTED. This store raises the cost of forging a Jev call;
// it does not make forgery impossible. The model's OS user can read the
// database owner credential, and with it can insert, backdate or delete rows
// directly, or disable the append-only trigger. What makes that DETECTABLE:
//   - read-jev-call-receipts credits a receipt only when the envelope ledger
//     (public.tool_call) holds the matching ask-jev row -- same idempotency
//     key, same server-derived actor, and a stored response naming this
//     receipt_id. A row inserted straight into ops.jev_call_receipt has no
//     such partner and is not credited.
//   - read-jev-call-receipt-integrity (wired into ./run.sh health) counts
//     receipts with no matching tool_call row and reports whether the
//     append-only triggers are enabled right now. A disable-then-re-enable
//     between two polls is not seen.
//
// THE VENDOR CALL HAPPENS OUTSIDE THE WRITER TRANSACTION. mcp.js validates an
// ask-jev request and calls Jev (prefetchJevAnswer) BEFORE it connects the
// writer pool or opens a transaction, then hands the result to the handler as
// client.jevPrefetched. The handler only appends the receipt, in a short
// transaction; it never calls the vendor. On an envelope replay the prefetched
// answer is discarded and the stored response returned. Break-glass
// (local-verb.mjs) calls executeRegisteredTool directly with no prefetched
// answer, so that door refuses as jev_proxy_unconfigured instead of reading a
// key locally.
//
// THREE VERBS, ONE STORE (migrations/0587_jev_call_receipt.sql):
//   ask-jev                            write: record the Worker's Jev call, return answers
//   read-jev-call-receipts             read:  the caller's credited receipts for a session
//   read-jev-call-receipt-integrity    read:  the detection audit
//
// THE KEY NEVER LEAVES THIS MODULE. It is read from env once per binding, sent
// only in the Authorization header, and never echoed, logged, returned, or put
// in an error. An upstream error body is truncated and scrubbed of the key
// string before it can reach a ToolError.

import { ToolError as LeafToolError } from "./tool-error.js";

const ENDPOINT = "https://api.typesafe.ai/v1/systemone";
const USER_AGENT = "carr-worker-jev-proxy/1.0";
const DEFAULT_MODEL = "jev-latest";
// Total upstream budget, every attempt and the retry wait included. The local
// client budgets ~14s for the whole path under a 20s UserPromptSubmit hook.
const TOTAL_BUDGET_MS = 10000;
const MAX_429_RETRIES = 1;
const RETRY_AFTER_CAP_MS = 5000;
const RETRY_AFTER_DEFAULT_MS = 1000;
// A retry is only worth making with at least this much budget left after the wait.
const MIN_ATTEMPT_MS = 1000;
const MAX_ERROR_BODY_CHARS = 300;
const MAX_STATE_CHARS = 96000;
const MAX_QUESTIONS = 64;
const MAX_SESSION_ID_CHARS = 200;
const READ_LIMIT_DEFAULT = 200;
const READ_LIMIT_MAX = 500;

export const JEV_PURPOSES = Object.freeze(["call", "build_advisory"]);
export const JEV_QUESTION_TYPES = Object.freeze(["noul", "choice", "score"]);
export const JEV_FACETS = Object.freeze([
  "architecture_or_design", "semantic_creation", "diagnosis",
  "verification_selection", "evidence_matching", "next_action_priority",
]);

// Python's sort_keys orders by code point; JS's default sort orders by UTF-16
// code unit. The two disagree only when an astral character (surrogate pair)
// meets a BMP character at or above U+E000, but a digest that must equal
// hashlib's has to agree there too.
function compareCodePoints(left, right) {
  const a = Array.from(left), b = Array.from(right);
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    const x = a[i].codePointAt(0), y = b[i].codePointAt(0);
    if (x !== y) return x - y;
  }
  return a.length - b.length;
}

// Canonical JSON: recursively sorted object keys, no whitespace, JSON.stringify
// string escaping. Equal, byte for byte, to Python's
//   json.dumps(x, sort_keys=True, separators=(",",":"), ensure_ascii=False)
// for strings, objects, arrays, integers, booleans and null. (Floats are not
// promised: the two languages print some of them differently, e.g. 1e-05.)
export function canonicalJson(value) {
  if (value === null || typeof value !== "object") {
    const encoded = JSON.stringify(value);
    return encoded === undefined ? "null" : encoded;
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const keys = Object.keys(value).filter(key => value[key] !== undefined).sort(compareCodePoints);
  return `{${keys.map(key => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
}

export async function sha256Hex(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

export async function canonicalSha256(value) {
  return sha256Hex(canonicalJson(value));
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function retryAfterMs(response) {
  const raw = response?.headers?.get?.("retry-after");
  if (raw === null || raw === undefined || String(raw).trim() === "") return RETRY_AFTER_DEFAULT_MS;
  const seconds = Number(String(raw).trim());
  let ms;
  if (Number.isFinite(seconds)) ms = seconds * 1000;
  else {
    const when = Date.parse(String(raw));
    ms = Number.isFinite(when) ? when - Date.now() : RETRY_AFTER_DEFAULT_MS;
  }
  return Math.min(RETRY_AFTER_CAP_MS, Math.max(0, ms));
}

function upstreamFailure(status, reason, body, key) {
  let text = typeof body === "string" ? body : "";
  // An upstream that echoes the request back must not carry the key out.
  if (key) text = text.split(key).join("[redacted]");
  text = text.slice(0, MAX_ERROR_BODY_CHARS);
  return new LeafToolError({ error: "jev_upstream_failed", status: status ?? null, reason,
    ...(text ? { body: text } : {}) });
}

// Returns the Worker's Jev caller, or null when the Worker holds no key.
// mcp.js calls it before the writer transaction opens (prefetchJevAnswer).
// `options` exists for tests (sleep, now, budgetMs); production passes none.
export function jevAskBinding(env, fetchImpl = fetch, options = {}) {
  const key = env?.TYPESAFE_API_KEY;
  if (typeof key !== "string" || key.trim() === "") return null;
  const sleep = options.sleep ?? (ms => new Promise(resolve => setTimeout(resolve, ms)));
  const now = options.now ?? (() => Date.now());
  const budgetMs = options.budgetMs ?? TOTAL_BUDGET_MS;
  return async function jevAsk({ state, model, questions }) {
    const body = JSON.stringify({ state, model, questions });
    const deadline = now() + budgetMs;
    for (let attempt = 0; ; attempt++) {
      const remaining = deadline - now();
      if (remaining <= 0) throw upstreamFailure(null, "timeout", "", key);
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), remaining);
      let response;
      try {
        response = await fetchImpl(ENDPOINT, {
          method: "POST",
          headers: {
            authorization: `Bearer ${key}`,
            "content-type": "application/json",
            "user-agent": USER_AGENT,
          },
          body,
          signal: controller.signal,
        });
      } catch (error) {
        clearTimeout(timer);
        // Never the error's own message: a fetch failure can quote its request.
        throw upstreamFailure(null, controller.signal.aborted || error?.name === "AbortError"
          ? "timeout" : "network", "", key);
      }
      try {
        if (response.status === 429 && attempt < MAX_429_RETRIES) {
          const wait = retryAfterMs(response);
          if (deadline - now() - wait >= MIN_ATTEMPT_MS) {
            clearTimeout(timer);
            await response.text().catch(() => "");
            await sleep(wait);
            continue;
          }
        }
        if (!response.ok) {
          const text = await response.text().catch(() => "");
          throw upstreamFailure(response.status, "http_status", text, key);
        }
        let parsed;
        try { parsed = await response.json(); }
        catch { throw upstreamFailure(response.status, controller.signal.aborted ? "timeout" : "invalid_json", "", key); }
        if (!isPlainObject(parsed) || !isPlainObject(parsed.answers) || typeof parsed.model !== "string" ||
            parsed.model.trim() === "")
          throw upstreamFailure(response.status, "invalid_answer_shape", "", key);
        return {
          model: parsed.model,
          answers: parsed.answers,
          usage: isPlainObject(parsed.usage) ? parsed.usage : null,
        };
      } catch (error) {
        if (error instanceof LeafToolError) throw error;
        throw upstreamFailure(response?.status ?? null,
          controller.signal.aborted ? "timeout" : "network", "", key);
      } finally {
        clearTimeout(timer);
      }
    }
  };
}

function validateSessionId(ToolError, value) {
  if (typeof value !== "string" || value.length < 1 || value.length > MAX_SESSION_ID_CHARS)
    throw new ToolError({ error: "jev_session_id_invalid",
      hint: `session_id must be a string of 1..${MAX_SESSION_ID_CHARS} characters` });
}

function validateQuestions(ToolError, questions) {
  if (!isPlainObject(questions))
    throw new ToolError({ error: "jev_questions_invalid", hint: "questions must be an object of question id -> question" });
  const entries = Object.entries(questions);
  if (entries.length < 1 || entries.length > MAX_QUESTIONS)
    throw new ToolError({ error: "jev_questions_invalid",
      hint: `questions must hold 1..${MAX_QUESTIONS} entries; got ${entries.length}` });
  for (const [id, question] of entries) {
    if (id.length === 0 || !isPlainObject(question) || !JEV_QUESTION_TYPES.includes(question.type) ||
        typeof question.instructions !== "string" || question.instructions.trim() === "")
      throw new ToolError({ error: "jev_questions_invalid", question_id: id.slice(0, 200),
        hint: `each question needs type in ${JEV_QUESTION_TYPES.join("|")} and a non-empty string instructions` });
  }
}

// Every ask-jev input check that does not need the database. Run by the
// handler and, before any vendor call, by prefetchJevAnswer -- so an invalid
// request never reaches Jev.
export function validateAskJevArgs(args, ToolError = LeafToolError) {
  validateSessionId(ToolError, args?.session_id);
  if (!JEV_PURPOSES.includes(args.purpose))
    throw new ToolError({ error: "jev_purpose_invalid", allowed: [...JEV_PURPOSES] });
  const state = args.state;
  if (typeof state !== "string" && !isPlainObject(state))
    throw new ToolError({ error: "jev_state_invalid", hint: "state must be a string or an object" });
  const stateJson = canonicalJson(state);
  if (stateJson.length > MAX_STATE_CHARS)
    throw new ToolError({ error: "jev_state_too_large", limit: MAX_STATE_CHARS, got: stateJson.length });
  if (args.purpose === "build_advisory" &&
      (!isPlainObject(state) || typeof state.partner_request !== "string"))
    throw new ToolError({ error: "jev_build_advisory_state_invalid",
      hint: "purpose build_advisory needs state to be an object with a string partner_request" });
  const questions = args.questions;
  validateQuestions(ToolError, questions);
  const facets = args.facets === undefined || args.facets === null ? [] : args.facets;
  if (!Array.isArray(facets) || facets.some(facet => !JEV_FACETS.includes(facet)))
    throw new ToolError({ error: "jev_facets_invalid", allowed: [...JEV_FACETS] });
  const model = args.model === undefined || args.model === null ? DEFAULT_MODEL : args.model;
  if (typeof model !== "string" || model.trim() === "" || model.length > 200)
    throw new ToolError({ error: "jev_model_invalid" });
  return { state, stateJson, questions, facets: [...facets], model };
}

// Called by mcp.js BEFORE the writer transaction: validate, then ask Jev.
// Validation failures throw; an upstream failure is returned (not thrown) so
// the handler can raise it inside the envelope, where a same-key replay still
// returns the stored response instead.
export async function prefetchJevAnswer(args, ask) {
  const { state, model, questions } = validateAskJevArgs(args);
  try {
    return { ok: true, result: await ask({ state, model, questions }) };
  } catch (error) {
    if (error instanceof LeafToolError) return { ok: false, error: error.payload };
    return { ok: false, error: { error: "jev_upstream_failed", status: null, reason: "network" } };
  }
}

export function jevCallReceiptTools({ withEnvelope, ToolError }) {
  return {
    "ask-jev": {
      write: true,
      // mcp.js asks Jev for tools carrying this flag before it opens the writer
      // transaction, and hands the answer over as client.jevPrefetched.
      jevProxy: true,
      description: "Ask Jev (TypeSafe) through the Worker and record a server-timestamped, append-only receipt of the call before the answers are returned. The Worker holds the TypeSafe key; the caller never does. purpose 'call' is an ordinary Jev question set; purpose 'build_advisory' requires state.partner_request (a string) and also records prompt_sha256. Returns the answers, the answered model, usage, the receipt id and the server's recorded_at. Refuses jev_proxy_unconfigured when the Worker holds no key, jev_upstream_failed when Jev does not answer within 10 seconds.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          idempotency_key: { type: "string" },
          session_id: { type: "string", minLength: 1, maxLength: MAX_SESSION_ID_CHARS },
          purpose: { type: "string", enum: [...JEV_PURPOSES] },
          state: { type: ["string", "object"] },
          questions: { type: "object" },
          facets: { type: "array", items: { type: "string", enum: [...JEV_FACETS] } },
          model: { type: "string" },
        },
        required: ["idempotency_key", "session_id", "purpose", "state", "questions"],
      },
      handler: async (c, actor, args) => {
        const { state, stateJson, questions, facets, model } = validateAskJevArgs(args, ToolError);
        return withEnvelope(c, actor, "ask-jev", args, async () => {
          const prefetched = c.jevPrefetched;
          if (!prefetched)
            throw new ToolError({ error: "jev_proxy_unconfigured",
              hint: "the Worker holds no TYPESAFE_API_KEY secret; Joe binds it by hand" });
          if (prefetched.ok !== true) throw new ToolError(prefetched.error);
          const answered = prefetched.result;
          if (!actor?.id || !actor?.slug)
            throw new ToolError({ error: "jev_call_receipt_actor_unresolved" });
          const stateSha = await sha256Hex(stateJson);
          const questionsSha = await canonicalSha256(questions);
          const answersSha = await canonicalSha256(answered.answers);
          const promptSha = args.purpose === "build_advisory"
            ? await canonicalSha256(state.partner_request) : null;
          const questionIds = Object.keys(questions).sort(compareCodePoints);
          // actor id and slug are the server-authenticated actor's, never a
          // caller argument; the door re-checks them against the actor table.
          const row = (await c.query(
            `select r.receipt_id, to_jsonb(r.recorded_at)#>>'{}' as recorded_at
               from ops.record_jev_call_receipt($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15) r`,
            [args.session_id, args.purpose, questionIds, facets, model, answered.model,
              stateSha, questionsSha, answersSha, promptSha,
              JSON.stringify(answered.answers), answered.usage ? JSON.stringify(answered.usage) : null,
              actor.id, actor.slug, args.idempotency_key],
          )).rows[0];
          if (!row?.receipt_id) throw new ToolError({ error: "jev_call_receipt_refused" });
          return {
            ok: true,
            receipt_id: row.receipt_id,
            recorded_at: row.recorded_at,
            purpose: args.purpose,
            session_id: args.session_id,
            model: answered.model,
            answers: answered.answers,
            usage: answered.usage ?? null,
            state_sha256: stateSha,
            prompt_sha256: promptSha,
          };
        });
      },
    },

    "read-jev-call-receipts": {
      write: false,
      description: "Read the calling actor's credited Jev call receipts for one session, oldest first, from `since` (default: 24 hours before the server's clock). A receipt is credited only when the envelope ledger holds its matching ask-jev call by the same actor. Returns at most `limit` (default 200, max 500) receipts -- the OLDEST ones from since -- and truncated: true when more exist. Each receipt carries receipt_id, the server's recorded_at, purpose, question_ids, facets, model, state_sha256, prompt_sha256, and answers only for purpose build_advisory (null for call). No state text is stored or returned. server_now is the database clock at read time.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          session_id: { type: "string", minLength: 1, maxLength: MAX_SESSION_ID_CHARS },
          since: { type: "string" },
          limit: { type: "integer", minimum: 1, maximum: READ_LIMIT_MAX },
        },
        required: ["session_id"],
      },
      handler: async (c, actor, args) => {
        validateSessionId(ToolError, args.session_id);
        let since = null;
        if (args.since !== undefined && args.since !== null) {
          if (typeof args.since !== "string" || !/^\d{4}-\d{2}-\d{2}/.test(args.since) ||
              !Number.isFinite(Date.parse(args.since)))
            throw new ToolError({ error: "jev_since_invalid", hint: "since must be an ISO-8601 timestamp" });
          since = args.since;
        }
        const limit = args.limit === undefined || args.limit === null ? READ_LIMIT_DEFAULT : args.limit;
        if (!Number.isInteger(limit) || limit < 1 || limit > READ_LIMIT_MAX)
          throw new ToolError({ error: "jev_limit_invalid", hint: `limit must be an integer 1..${READ_LIMIT_MAX}` });
        if (!actor?.slug) throw new ToolError({ error: "jev_call_receipt_actor_unresolved" });
        const row = (await c.query(
          "select ops.read_jev_call_receipts($1, $2::timestamptz, $3, $4) as result",
          [args.session_id, since, limit, actor.slug],
        )).rows[0];
        const result = typeof row?.result === "string" ? JSON.parse(row.result) : row?.result;
        if (!isPlainObject(result) || !Array.isArray(result.receipts))
          throw new ToolError({ error: "jev_call_receipts_unavailable" });
        return { ok: true, session_id: args.session_id, since: result.since,
          server_now: result.server_now, truncated: result.truncated === true,
          receipts: result.receipts };
      },
    },

    "read-jev-call-receipt-integrity": {
      write: false,
      description: "Audit the server-side Jev call log for tampering that the store detects but cannot prevent: how many receipts exist, how many have no matching ask-jev row in the envelope ledger (count plus up to 20 receipt ids), and whether the append-only triggers are enabled right now. A disable-then-re-enable between two audits is not seen. ./run.sh health runs this.",
      inputSchema: { type: "object", additionalProperties: false, properties: {} },
      handler: async (c) => {
        const row = (await c.query("select ops.jev_call_receipt_integrity() as result")).rows[0];
        const result = typeof row?.result === "string" ? JSON.parse(row.result) : row?.result;
        if (!isPlainObject(result) || !isPlainObject(result.receipts_without_tool_call))
          throw new ToolError({ error: "jev_call_receipt_integrity_unavailable" });
        return { ok: true, ...result };
      },
    },
  };
}
