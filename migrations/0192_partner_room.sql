-- 0192_partner_room.sql — the partner room: one shared, append-only turn log
-- two brains write and a human watches (Idea 78, "Partner line — live
-- Joe-brain ↔ Dell-brain, humans watch the transcript").
--
-- WHY A TABLE HERE AND NOT A SOCKET THERE. The spike (spikes/partner-line-78,
-- receipts 1–3) proved the local injection half and stopped at the machine
-- boundary: a same-UID socket is a wall on one Mac and nothing across two.
-- The Worker is already the authenticated surface both partners hold, so the
-- room lives behind it: each Mac polls `read-room` and injects locally, and
-- `add-room-turn` writes here. No new inbound port, no tunnel, no new secret
-- class. The room is the transport AND the spectator surface — raw turns,
-- who/when/exact text, never a recap (Joe: "i would actually like to be able
-- to see their discussions").
--
-- ATTRIBUTION: `sponsor` is written by the verb from the VERIFIED credential
-- (identity.js personalScopeForActor), never from an argument. `seat` is the
-- claimed brain on that partner's side (claude, human, grok, sol, hermes,
-- codex) and renders as claimed-under-verified-sponsor. Council turns are the
-- same rows with a different seat — their visibility costs nothing extra.
--
-- RETENTION is deliberately not decided here: the room is append-only and
-- immutable at this grain (no update/delete grants to anyone but the owner);
-- if volume ever warrants pruning, that is a later, deliberate migration.

begin;

create table partner_room_turn (
  id       bigint generated always as identity primary key,
  room_id  text not null default 'partner-line'
             constraint partner_room_turn_room_slug
               check (room_id ~ '^[a-z0-9][a-z0-9-]{0,31}$'),
  at       timestamptz not null default now(),
  sponsor  text not null
             constraint partner_room_turn_sponsor_known
               check (sponsor in ('joe','dell')),
  seat     text not null
             constraint partner_room_turn_seat_slug
               check (seat ~ '^[a-z0-9][a-z0-9-]{0,31}$'),
  kind     text not null default 'turn'
             constraint partner_room_turn_kind_known
               check (kind in ('turn','system','receipt')),
  body     text not null
             constraint partner_room_turn_body_bounds
               check (btrim(body) <> '' and length(body) <= 20000),
  msg_id   uuid not null
             constraint partner_room_turn_msg_id_unique unique
);

comment on table partner_room_turn is
  'Idea 78 partner room: append-only AI-to-AI turn log served by the Worker. sponsor is server-derived from the verified credential; seat is the claimed brain on that side. Raw text, never a summary. The live wire, not the record — durable outcomes go through decisions/loops.';

-- The poll path: read-room selects "where room_id=$1 and id > $2 order by id".
create index partner_room_turn_room_cursor on partner_room_turn (room_id, id);

-- The Worker's read connection is views-only by rule; read-room reads here.
create view v_partner_room_turn as
  select id, room_id, at, sponsor, seat, kind, body, msg_id
    from partner_room_turn;

comment on view v_partner_room_turn is
  'read-room''s read surface: the partner room verbatim, cursor over (room_id, id). carr_reader holds no grant on the table itself.';

-- GRANTS, deny-by-default (0180's pattern): revoke everything from everyone
-- first so no stale prior grant survives, then grant exactly the two paths the
-- verbs use. Append-only is enforced by grant shape: nobody below owner can
-- update or delete a landed turn.
revoke all on partner_room_turn, v_partner_room_turn from public;
revoke all on partner_room_turn, v_partner_room_turn
  from carr_jobs, carr_reader, carr_writer;

grant insert, select on partner_room_turn to carr_writer;   -- add-room-turn (insert + dedup read-back)
grant select on v_partner_room_turn to carr_reader;         -- read-room / spectator view
grant select on v_partner_room_turn to carr_writer;         -- read-room on a write-profile connection

commit;
