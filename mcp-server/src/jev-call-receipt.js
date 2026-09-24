// Server-side Jev call log: the Worker makes the TypeSafe (Jev) call itself and
// appends a server-timestamped receipt before it hands the answers back.
//
// WHY THE WORKER AND NOT THE CALLER. Jev enforcement gates used to credit
// evidence the gated model could write: the session transcript and
// out/jev-calls.jsonl. The model shares one OS user with the hooks, so any
// local log or local credential is forgeable. Here the only party holding the
// TypeSafe key is the Worker (env.TYPESAFE_API_KEY, bound by hand), the call
// and the receipt happen inside the same writer transaction, and recorded_at is
// the database clock. A gate that credits only ops.jev_call_receipt rows
// (read back through `read-jev-call-receipts`) is crediting something the
// gated model could not have produced without actually asking Jev.
//
// TWO VERBS, ONE STORE (migrations/0587_jev_call_receipt.sql):
//   ask-jev                 write: validate, call Jev, record receipt, return answers
//   read-jev-call-receipts  read:  a session's receipts, ids/digests/probabilities only
//
// THE KEY NEVER LEAVES THIS MODULE. It is read from env once per binding, sent
// only in the Authorization header, and never echoed, logged, returned, or put
// in an error. An upstream error body is truncated and scrubbed of the key
// string before it can reach a ToolError. Break-glass (local-verb.mjs) calls
// executeRegisteredTool directly with a plain pg client that has no jevAsk, so
// that door refuses as jev_proxy_unconfigured instead of reading a key locally.

import { ToolError as LeafToolError } from "./tool-error.js";

const ENDPOINT = "https://api.typesafe.ai/v1/systemone";
const USER_AGENT = "carr-worker-jev-proxy/1.0";
const DEFAULT_MODEL = "jev-latest";
const TIMEOUT_MS = 25000;
const MAX_429_RETRIES = 2;
const RETRY_AFTER_CAP_MS = 5000;
const RETRY_AFTER_DEFAULT_MS = 1000;
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
// mcp.js attaches the result as client.jevAsk for tools marked jevProxy.
// `options` exists for tests (sleep, timeoutMs); production passes none.
export function jevAskBinding(env, fetchImpl = fetch, options = {}) {
  const key = env?.TYPESAFE_API_KEY;
  if (typeof key !== "string" || key.trim() === "") return null;
  const sleep = options.sleep ?? (ms => new Promise(resolve => setTimeout(resolve, ms)));
  const timeoutMs = options.timeoutMs ?? TIMEOUT_MS;
  return async function jevAsk({ state, model, questions }) {
    const body = JSON.stringify({ state, model, questions });
    for (let attempt = 0; ; attempt++) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
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
          clearTimeout(timer);
          await response.text().catch(() => "");
          await sleep(retryAfterMs(response));
          continue;
        }
        if (!response.ok) {
          const text = await response.text().catch(() => "");
          throw upstreamFailure(response.status, "http_status", text, key);
        }
        let parsed;
        try { parsed = await response.json(); }
        catch { throw upstreamFailure(response.status, "invalid_json", "", key); }
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

export function jevCallReceiptTools({ withEnvelope, ToolError }) {
  return {
    "ask-jev": {
      write: true,
      // mcp.js attaches client.jevAsk (the Worker-held TypeSafe caller) only to
      // tools carrying this flag.
      jevProxy: true,
      description: "Ask Jev (TypeSafe) through the Worker and record a server-timestamped, append-only receipt of the call before the answers are returned. The Worker holds the TypeSafe key; the caller never does. purpose 'call' is an ordinary Jev question set; purpose 'build_advisory' requires state.partner_request (a string) and also records prompt_sha256. Returns the answers, the answered model, usage, the receipt id and the server's recorded_at. Refuses jev_proxy_unconfigured when the Worker holds no key, jev_upstream_failed when Jev does not answer.",
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
        validateSessionId(ToolError, args.session_id);
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

        return withEnvelope(c, actor, "ask-jev", args, async () => {
          if (typeof c.jevAsk !== "function")
            throw new ToolError({ error: "jev_proxy_unconfigured",
              hint: "the Worker holds no TYPESAFE_API_KEY secret; Joe binds it by hand" });
          const answered = await c.jevAsk({ state, model, questions });
          const stateSha = await sha256Hex(stateJson);
          const questionsSha = await canonicalSha256(questions);
          const answersSha = await canonicalSha256(answered.answers);
          const promptSha = args.purpose === "build_advisory"
            ? await canonicalSha256(state.partner_request) : null;
          const questionIds = Object.keys(questions).sort(compareCodePoints);
          const row = (await c.query(
            `select r.receipt_id, to_jsonb(r.recorded_at)#>>'{}' as recorded_at
               from ops.record_jev_call_receipt($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14) r`,
            [args.session_id, args.purpose, questionIds, [...facets], model, answered.model,
              stateSha, questionsSha, answersSha, promptSha,
              JSON.stringify(answered.answers), answered.usage ? JSON.stringify(answered.usage) : null,
              actor?.slug || null, args.idempotency_key],
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
      description: "Read the server-recorded Jev call receipts for one session, oldest first: the most recent `limit` (default 200, max 500) receipts at or after `since`. Each receipt carries receipt_id, the server's recorded_at, purpose, question_ids, facets, model, state_sha256, prompt_sha256, and answers only for purpose build_advisory (null for call). No state text is stored or returned. server_now is the database clock at read time.",
      inputSchema: {
        type: "object", additionalProperties: false,
        properties: {
          session_id: { type: "string", minLength: 1, maxLength: MAX_SESSION_ID_CHARS },
          since: { type: "string" },
          limit: { type: "integer", minimum: 1, maximum: READ_LIMIT_MAX },
        },
        required: ["session_id"],
      },
      handler: async (c, _actor, args) => {
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
        const row = (await c.query(
          "select ops.read_jev_call_receipts($1, $2::timestamptz, $3) as result",
          [args.session_id, since, limit],
        )).rows[0];
        const result = typeof row?.result === "string" ? JSON.parse(row.result) : row?.result;
        if (!isPlainObject(result) || !Array.isArray(result.receipts))
          throw new ToolError({ error: "jev_call_receipts_unavailable" });
        return { ok: true, session_id: args.session_id, server_now: result.server_now,
          receipts: result.receipts };
      },
    },
  };
}
