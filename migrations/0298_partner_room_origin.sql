-- 0298_partner_room_origin.sql — source authority for partner-room queue ingress
--
-- `seat` remains claimed display metadata.  Queue authority instead comes
-- from these two server-stamped columns: MCP writes are model-origin even when
-- their claimed seat says "human"; authenticated Observatory posts are the
-- only browser-human origin. Existing history cannot truthfully claim either,
-- so it remains explicitly legacy and cannot enqueue.

begin;

alter table partner_room_turn
  add column if not exists origin_channel text not null default 'legacy'
    constraint partner_room_turn_origin_channel_known
      check (origin_channel in ('legacy', 'mcp', 'browser-human')),
  add column if not exists origin_actor text not null default 'legacy'
    constraint partner_room_turn_origin_actor_slug
      check (origin_actor ~ '^[a-z0-9][a-z0-9-]{0,31}$');

comment on column partner_room_turn.origin_channel is
  'Server-derived ingress channel: legacy for pre-0298 history, mcp for tool writes, browser-human for authenticated Observatory posts.';
comment on column partner_room_turn.origin_actor is
  'Server-derived authenticated actor slug. Never supplied by a room command or queue grammar.';

create or replace view v_partner_room_turn as
  select id, room_id, at, sponsor, seat, kind, body, msg_id, origin_channel, origin_actor
    from partner_room_turn;

comment on view v_partner_room_turn is
  'read-room surface: append-only partner-room turns plus server-derived origin provenance. carr_reader holds no grant on the table itself.';

commit;
