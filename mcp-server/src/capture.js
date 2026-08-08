import { randomString } from "./google-oidc.js";

const JSON_HEADERS = { "content-type": "application/json" };
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });

export const CAPTURE_SESSION_TTL_SECONDS = 24 * 60 * 60;
const STATES = ["recording", "transcribing", "distilling", "done"];
const KINDS = new Set(["phase_move", "next_step", "new_deal", "activity", "meeting_record"]);

async function sha256(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object")
    return Object.keys(value).sort().reduce((result, key) => {
      result[key] = canonical(value[key]);
      return result;
    }, {});
  return value;
}

function bearer(request) {
  return (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
}

function captureDevice(request, env) {
  const token = bearer(request);
  let tokens;
  try { tokens = JSON.parse(env.CAPTURE_TOKENS || "{}"); }
  catch { tokens = {}; }
  return Object.keys(tokens).find(slug => tokens[slug] && tokens[slug] === token) || null;
}

async function requestBody(request) {
  try { return await request.json(); }
  catch { return null; }
}

function validTime(value) {
  return typeof value === "string" && value.trim() && Number.isFinite(Date.parse(value));
}

function wordCount(value) {
  const trimmed = String(value ?? "").trim();
  return trimmed ? trimmed.split(/\s+/u).length : 0;
}

function hasForbiddenCaptureKey(value) {
  if (Array.isArray(value)) return value.some(hasForbiddenCaptureKey);
  if (!value || typeof value !== "object") return false;
  return Object.entries(value).some(([key, nested]) =>
    /transcript|human_quote|verbatim|raw_text|audio/iu.test(key) || hasForbiddenCaptureKey(nested));
}

function payloadStringsTooLarge(value) {
  if (typeof value === "string") return value.length > 4000;
  if (Array.isArray(value)) return value.some(payloadStringsTooLarge);
  if (!value || typeof value !== "object") return false;
  return Object.values(value).some(payloadStringsTooLarge);
}

function errorResponse(error) {
  if (error?.captureStatus) return json(error.payload, error.captureStatus);
  return json({ error: "database_unavailable", detail: String(error?.message || error).slice(0, 200) }, 503);
}

function refuse(status, payload) {
  const error = new Error(payload.error);
  error.captureStatus = status;
  error.payload = payload;
  throw error;
}

async function claim(request, env, dependencies) {
  // Authentication is intentionally first, before body size, JSON, nonce, or
  // consent checks, matching the ingest socket's information boundary.
  const device = captureDevice(request, env);
  if (!device) return json({ error: "unauthorized" }, 401);
  const body = await requestBody(request);
  if (!body) return json({ error: "invalid_json" }, 400);
  if (hasForbiddenCaptureKey(body)) return json({ error: "forbidden_capture_field" }, 400);
  if (!body.nonce || body.device_id !== device || body.mode !== "meeting" || !validTime(body.started_at))
    return json({ error: "invalid_claim" }, 400);
  if (!validTime(body.consent?.announced_at)) return json({ error: "consent_required" }, 400);

  let opaque;
  try {
    await dependencies.withWriter(async client => {
      const replay = await client.query(
        "select id from capture_session where nonce=$1 /* capture:claim-nonce */", [body.nonce]);
      if (replay.rows.length) refuse(409, { error: "nonce_replayed" });
      const actor = await client.query("select id from actor where slug=$1 /* capture:device-actor */", [device]);
      if (!actor.rows.length) refuse(503, { error: "device_actor_not_provisioned", device_id: device });
      opaque = dependencies.randomStringFn(32);
      const tokenHash = await sha256(opaque);
      await client.query(
        `insert into capture_session
           (nonce, device_id, actor_id, mode, started_at, consent_announced_at,
            session_token_hash, expires_at, state, state_at)
         values ($1,$2,$3,$4,$5::timestamptz,$6::timestamptz,$7,
                 now() + ($8 * interval '1 second'),'recording',$5::timestamptz)
         /* capture:claim-insert */`,
        [body.nonce, device, actor.rows[0].id, body.mode, body.started_at,
         body.consent.announced_at, tokenHash, CAPTURE_SESSION_TTL_SECONDS]);
    });
    return json({ session_token: opaque, ttl_seconds: CAPTURE_SESSION_TTL_SECONDS });
  } catch (error) {
    if (error?.code === "23505") return json({ error: "nonce_replayed" }, 409);
    return errorResponse(error);
  }
}

async function authenticatedSession(client, opaque, lock = false) {
  if (typeof opaque !== "string" || !opaque) refuse(401, { error: "unauthorized" });
  const tokenHash = await sha256(opaque);
  const result = await client.query(
    `select id, state, to_jsonb(state_at)#>>'{}' as state_at
       from capture_session
      where session_token_hash=$1 and expires_at > now()
      ${lock ? "for update" : ""} /* capture:session-auth */`, [tokenHash]);
  if (!result.rows.length) refuse(401, { error: "unauthorized" });
  return result.rows[0];
}

async function status(request, dependencies) {
  const body = await requestBody(request);
  if (!body) return json({ error: "invalid_json" }, 400);
  if (hasForbiddenCaptureKey(body)) return json({ error: "forbidden_capture_field" }, 400);
  if (![...STATES, "failed"].includes(body.state) || !validTime(body.at))
    return json({ error: "invalid_status" }, 400);
  if (body.detail != null && (typeof body.detail !== "string" || body.detail.length > 500))
    return json({ error: "invalid_status_detail" }, 400);
  try {
    const result = await dependencies.withWriter(async client => {
      const session = await authenticatedSession(client, body.session_token, true);
      if (session.state === "done" || session.state === "failed")
        refuse(409, { error: "terminal_state", state: session.state });
      const current = STATES.indexOf(session.state);
      const requested = STATES.indexOf(body.state);
      if (body.state !== "failed" && requested < current)
        refuse(409, { error: "backward_transition", state: session.state, requested: body.state });
      const updated = await client.query(
        `update capture_session set state=$2, state_at=$3::timestamptz, state_detail=$4
          where id=$1 returning state, to_jsonb(state_at)#>>'{}' as state_at
          /* capture:status-update */`,
        [session.id, body.state, body.at, body.detail || null]);
      return updated.rows[0];
    });
    return json({ ok: true, state: result.state, at: result.state_at });
  } catch (error) { return errorResponse(error); }
}

function validateCandidates(body) {
  if (!body || typeof body.idempotency_key !== "string" || !body.idempotency_key ||
      !Array.isArray(body.items) || body.items.length === 0 || body.items.length > 100)
    return { error: "invalid_candidates" };
  for (const item of body.items) {
    if (!item || !KINDS.has(item.kind) || !item.payload || typeof item.payload !== "object" || Array.isArray(item.payload) ||
        typeof item.evidence_quote !== "string" || typeof item.confidence !== "number" ||
        !Number.isFinite(item.confidence) || item.confidence < 0 || item.confidence > 1)
      return { error: "invalid_candidate" };
    if (hasForbiddenCaptureKey(item.payload))
      return { error: "transcript_field_forbidden" };
    if (JSON.stringify(item.payload).length > 8192 || payloadStringsTooLarge(item.payload))
      return { error: "candidate_payload_too_large" };
    if (wordCount(item.evidence_quote) > 15) return { error: "evidence_quote_too_long", max_words: 15 };
  }
  return null;
}

async function candidates(request, dependencies) {
  const body = await requestBody(request);
  if (!body) return json({ error: "invalid_json" }, 400);
  if (hasForbiddenCaptureKey(body)) return json({ error: "forbidden_capture_field" }, 400);
  const invalid = validateCandidates(body);
  if (invalid) return json(invalid, 400);
  const batchHash = await sha256(JSON.stringify(canonical(body.items)));
  try {
    const result = await dependencies.withWriter(async client => {
      const session = await authenticatedSession(client, body.session_token, true);
      const prior = await client.query(
        `select batch_hash from capture_candidate
          where session_id=$1 and idempotency_key=$2 limit 1
          /* capture:candidates-prior */`, [session.id, body.idempotency_key]);
      if (prior.rows.length && prior.rows[0].batch_hash !== batchHash)
        refuse(409, { error: "key_reuse" });
      await client.query(
        `insert into capture_candidate
           (session_id, idempotency_key, batch_hash, item_index, kind, payload, evidence_quote, confidence)
         select $1, $2, $3, item.ordinality - 1, item.value->>'kind', item.value->'payload',
                item.value->>'evidence_quote', (item.value->>'confidence')::numeric
           from jsonb_array_elements($4::jsonb) with ordinality as item(value, ordinality)
         on conflict (session_id, idempotency_key, item_index) do nothing
         /* capture:candidates-insert */`,
        [session.id, body.idempotency_key, batchHash, JSON.stringify(body.items)]);
      return client.query(
        `select id, item_index from capture_candidate
          where session_id=$1 and idempotency_key=$2 order by item_index
          /* capture:candidates-result */`, [session.id, body.idempotency_key]);
    });
    return json({ ok: true, candidate_ids: result.rows.map(row => row.id) });
  } catch (error) { return errorResponse(error); }
}

async function session(request, dependencies) {
  try {
    const result = await dependencies.withWriter(async client => {
      const authenticated = await authenticatedSession(client, bearer(request));
      return client.query(
        `select s.state,
                count(c.id) filter (where c.status='pending')::int as pending,
                count(c.id) filter (where c.status='confirmed')::int as confirmed,
                count(c.id) filter (where c.status='skipped')::int as skipped,
                max(c.resulting_ref) filter
                  (where c.kind='meeting_record' and c.status='confirmed') as meeting_record
           from capture_session s left join capture_candidate c on c.session_id=s.id
          where s.id=$1 group by s.id, s.state /* capture:session-read */`, [authenticated.id]);
    });
    const row = result.rows[0];
    return json({ state: row.state, candidates: {
      pending: row.pending, confirmed: row.confirmed, skipped: row.skipped,
    }, meeting_record: row.meeting_record || null });
  } catch (error) { return errorResponse(error); }
}

export function createCaptureHandler(overrides = {}) {
  const dependencies = { randomStringFn: randomString, ...overrides };
  return {
    async fetch(request, env) {
      const path = new URL(request.url).pathname;
      if (path === "/capture/claim" && request.method === "POST") return claim(request, env, dependencies);
      if (path === "/capture/status" && request.method === "POST") return status(request, dependencies);
      if (path === "/capture/candidates" && request.method === "POST") return candidates(request, dependencies);
      if (path === "/capture/session" && request.method === "GET") return session(request, dependencies);
      return json({ error: "not_found" }, 404);
    },
  };
}
