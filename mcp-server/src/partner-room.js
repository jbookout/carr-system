// The partner room (Idea 78): one shared, append-only turn log that two brains
// write and a human watches — raw turns, never a recap.
//
// WHY THIS LIVES IN THE WORKER. The spike (spikes/partner-line-78, receipts
// 1–3) proved the local half: a process can drop a partner's turn into a live
// Claude Code session, and the wall on one Mac is same-UID. The moment the line
// crosses machines something else must be the boundary, and the Worker already
// IS that boundary for both partners — same OAuth identities, same locked
// profiles, no new inbound port, no tunnel, no new secret class. Each Mac polls
// the room and injects locally; the room is the transport AND the spectator
// surface. Council turns (grok, sol, hermes) are the same rows with a different
// seat, which is why their visibility costs nothing extra.
//
// ATTRIBUTION IS SERVER-DERIVED, the same refusal the spike relay states:
// whose name a turn lands under comes from the verified credential
// (personalScopeForActor), never from an argument. `seat` says which brain on
// that partner's side spoke; it is claimed, and rendered as claimed, under the
// verified sponsor. Consent posture for what a receiving Mac DOES with a turn
// is decision 351b9995 (auto-inject, visible, abortable) and is enforced
// client-side; the room itself only records.
//
// The room is the live wire, not the record: durable outcomes reached on the
// line still go through log-decision / add-loop / the deal verbs.

import { personalScopeForActor } from "./identity.js";

export const DEFAULT_ROOM = "partner-line";
const SLUG = /^[a-z0-9][a-z0-9-]{0,31}$/;
const UUID = /^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$/;
export const ROOM_BODY_MAX = 20000;
export const ROOM_READ_DEFAULT = 50;
export const ROOM_READ_MAX = 200;
export const ROOM_KINDS = ["turn", "system", "receipt"];

/** null when the name is not a room slug; the normalized name otherwise. */
export function normalizeRoomName(value) {
  const room = value === undefined || value === null ? DEFAULT_ROOM : String(value).trim().toLowerCase();
  return SLUG.test(room) ? room : null;
}

/** Clamp a caller's paging arguments to the room's own read contract. */
export function normalizeRoomPaging({ after_seq, limit }) {
  const after = Number.isInteger(after_seq) && after_seq > 0 ? after_seq : 0;
  const capped = Number.isInteger(limit)
    ? Math.min(Math.max(limit, 1), ROOM_READ_MAX) : ROOM_READ_DEFAULT;
  return { after, limit: capped };
}

// THE ONE ROOM READ (rule a8c55a47 — a manual path and an automated path that
// do the same job must be the same code). The read-room verb and the Deal
// Room's /api/room/turns endpoint are two doors onto the same wire; if the
// query were copied into the browser endpoint, the observatory could quietly
// drift from what every desk and every session sees. Both call this.
export async function readRoomTurns(c, args = {}) {
  const room = normalizeRoomName(args.room);
  if (room === null) return { ok: false, error: "room_invalid" };
  const { after, limit } = normalizeRoomPaging(args);
  const r = await c.query(
    `select id as seq, room_id, to_jsonb(at)#>>'{}' as at, sponsor, seat, kind, body, msg_id
       from v_partner_room_turn
      where room_id=$1 and id > $2
      order by id asc limit $3 /* partner-room:read */`,
    [room, after, limit],
  );
  const turns = r.rows;
  return { ok: true, room, turns,
    latest_seq: turns.length ? turns[turns.length - 1].seq : after,
    more: turns.length === limit };
}

// THE ONE ROOM APPEND, for the same reason. Sponsor is always resolved by the
// CALLER from a verified credential and passed in — this function never reads
// it off an argument object that came from a request body.
export async function appendRoomTurn(c, { room, sponsor, seat, kind, body, msgId }) {
  const ins = await c.query(
    `insert into partner_room_turn (room_id, sponsor, seat, kind, body, msg_id)
     values ($1,$2,$3,$4,$5,$6)
     on conflict (msg_id) do nothing
     returning id, to_jsonb(at)#>>'{}' as at /* partner-room:say */`,
    [room, sponsor, seat, kind, body, msgId],
  );
  if (!ins.rows.length) {
    const prior = await c.query(
      "select id, room_id, sponsor, seat from partner_room_turn where msg_id=$1 /* partner-room:dedup-read */",
      [msgId],
    );
    if (!prior.rows.length) return { ok: false, error: "dedup_row_vanished", msg_id: msgId };
    const p = prior.rows[0];
    return { ok: true, deduplicated: true, room: p.room_id, seq: p.id,
      sponsor: p.sponsor, seat: p.seat, msg_id: msgId };
  }
  return { ok: true, room, seq: ins.rows[0].id, at: ins.rows[0].at,
    sponsor, seat, kind, msg_id: msgId };
}

export function partnerRoomTools({ withEnvelope, ToolError }) {
  return {
    "add-room-turn": {
      write: true,
      description: "Append one raw turn to a partner room — the shared, human-watchable transcript the partner line and the council talks write into (Idea 78). The turn lands verbatim, attributed to the VERIFIED partner behind the calling credential (server-derived, never caller-supplied); `seat` names which brain on that partner's side spoke (claude, human, grok, sol, hermes, codex). Pass msg_id only when relaying a turn that already has one, so a second transport dedups instead of double-landing. The room is the live wire, not the record — durable outcomes still go through log-decision and the loop/deal verbs.",
      inputSchema: { type: "object", properties: {
        idempotency_key: { type: "string" },
        body: { type: "string", description: "the turn, raw and unsummarised" },
        seat: { type: "string", description: "which brain on your side is speaking: claude | human | grok | sol | hermes | codex" },
        room: { type: "string", description: "room name; default partner-line" },
        kind: { type: "string", enum: ["turn", "system", "receipt"] },
        msg_id: { type: "string", description: "optional UUID carried across transports for dedup; a fresh one is minted when absent" },
      }, required: ["idempotency_key", "body", "seat"] },
      handler: async (c, actor, args) => withEnvelope(c, actor, "add-room-turn", args, async () => {
        const seat = String(args.seat ?? "").trim().toLowerCase();
        if (!SLUG.test(seat))
          throw new ToolError({ error: "seat_invalid",
            hint: "a seat is a plain slug: claude, human, grok, sol, hermes, codex" });
        const room = normalizeRoomName(args.room);
        if (room === null) throw new ToolError({ error: "room_invalid" });
        const kind = args.kind ?? "turn";
        if (!ROOM_KINDS.includes(kind)) throw new ToolError({ error: "kind_invalid" });
        const body = typeof args.body === "string" ? args.body : "";
        if (!body.trim()) throw new ToolError({ error: "body_required" });
        if (body.length > ROOM_BODY_MAX)
          throw new ToolError({ error: "body_too_long", limit: ROOM_BODY_MAX, got: body.length });
        if (args.msg_id !== undefined && !UUID.test(String(args.msg_id)))
          throw new ToolError({ error: "msg_id_invalid", hint: "msg_id must be a UUID" });

        const scope = personalScopeForActor(actor);
        if (scope.status !== "personal")
          throw new ToolError({ error: "no_sponsoring_partner",
            hint: "the room only takes turns from a credential a partner sponsors; this one has no partner behind it" });

        const msgId = args.msg_id === undefined ? crypto.randomUUID() : String(args.msg_id).toLowerCase();
        const appended = await appendRoomTurn(c, { room, sponsor: scope.sponsor, seat, kind, body, msgId });
        if (appended.ok !== true) { const { ok: _ok, ...failure } = appended; throw new ToolError(failure); }
        return appended;
      }),
    },

    "read-room": {
      description: "Read a partner room's turns after a cursor, oldest first — the poll half of the partner line and the spectator view's source. Returns raw turns exactly as written (who, when, seat, text — never a recap), the new cursor (latest_seq), and whether more rows are waiting. A quiet room returns an empty list, honestly; poll by passing the last latest_seq back as after_seq.",
      inputSchema: { type: "object", properties: {
        room: { type: "string", description: "room name; default partner-line" },
        after_seq: { type: "integer", description: "return turns with seq greater than this; 0 or absent reads from the start" },
        limit: { type: "integer", description: "max turns to return; default 50, cap 200" },
      } },
      handler: async (c, _actor, args) => {
        const read = await readRoomTurns(c, args);
        if (read.ok !== true) { const { ok: _ok, ...failure } = read; throw new ToolError(failure); }
        return read;
      },
    },
  };
}
