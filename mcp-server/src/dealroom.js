// Deal Room polling surface. Authentication stays outside this module: callers
// pass the already-resolved actor and an injected query client. That keeps the
// contract mountable behind both OAuth and the Deal Room session-cookie gate.

const JSON_HEADERS = { "content-type": "application/json" };
const PLACEHOLDER_FIELDS = new Set([
  "sf_commission_placeholder",
  "sf_close_date_placeholder",
]);

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: JSON_HEADERS });

function base64UrlEncode(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function base64UrlDecode(text) {
  const padded = text.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (text.length % 4)) % 4);
  const binary = atob(padded);
  return new TextDecoder().decode(Uint8Array.from(binary, ch => ch.charCodeAt(0)));
}

export function encodePipelineCursor(recordedAt, id) {
  return base64UrlEncode(JSON.stringify({ recorded_at: recordedAt, id }));
}

export function decodePipelineCursor(cursor) {
  if (!cursor) return null;
  try {
    const decoded = JSON.parse(base64UrlDecode(cursor));
    if (!decoded || typeof decoded.recorded_at !== "string" || typeof decoded.id !== "string")
      throw new Error("bad cursor shape");
    if (Number.isNaN(Date.parse(decoded.recorded_at))) throw new Error("bad cursor time");
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(decoded.id))
      throw new Error("bad cursor id");
    return decoded;
  } catch {
    throw new Error("invalid_cursor");
  }
}

// Defense in depth over the SQL view: a historical field-less event may carry
// an object, so recursively remove placeholder keys from every response shape.
export function stripDealPlaceholders(value) {
  if (Array.isArray(value)) return value.map(stripDealPlaceholders);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value)
      .filter(([key]) => !PLACEHOLDER_FIELDS.has(key))
      .map(([key, nested]) => [key, stripDealPlaceholders(nested)]));
  }
  return value;
}

export async function pipelineChanges(request, client, actor, options = {}) {
  if (!actor) return json({ error: "unauthorized" }, 401);
  if (request.method !== "GET") return json({ error: "method_not_allowed" }, 405);

  const url = new URL(request.url);
  let cursor;
  try {
    cursor = decodePipelineCursor(url.searchParams.get("cursor"));
  } catch {
    return json({ error: "invalid_cursor" }, 400);
  }

  const limit = options.limit || 200;
  const events = await client.query(
    `select id, recorded_at, actor, verb, subject_type, subject_id, field, old_value, new_value
       from v_deal_room_event
      where ($1::timestamptz is null or (recorded_at, id) > ($1::timestamptz, $2::uuid))
      order by recorded_at asc, id asc
      limit $3`,
    [cursor?.recorded_at || null, cursor?.id || null, limit],
  );
  const presence = await client.query(
    `select actor, deal_id, field, expires_at
       from v_deal_room_presence
      where expires_at > now()
      order by actor, deal_id, field`,
  );

  const cleanEvents = events.rows
    .filter(row => !PLACEHOLDER_FIELDS.has(row.field))
    .map(stripDealPlaceholders);
  const last = cleanEvents.at(-1);
  const nextCursor = last
    ? encodePipelineCursor(new Date(last.recorded_at).toISOString(), String(last.id))
    : (url.searchParams.get("cursor") || "");

  return json({ events: cleanEvents, presence: presence.rows.map(stripDealPlaceholders), cursor: nextCursor });
}
