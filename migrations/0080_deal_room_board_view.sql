-- 0080_deal_room_board_view.sql — the Deal Room board read as a view, and a
-- name on the deal-page view. Fixes "permission denied for table deal": the
-- reader role is views-only by design (wrangler.toml header), and the
-- deal-room-board verb shipped querying the deal table directly.

begin;

create view v_deal_room_board as
select d.id, d.name, d.deal_type as type, d.phase, d.owner, d.attention,
       d.next_date,
       (select n.text from deal_note n
         where n.deal_id = d.id and n.kind = 'next_step'
         order by n.created_at desc, n.id desc limit 1) as next_step
  from deal d
 where d.outcome is null;

-- create or replace keeps the column order and appends name at the end,
-- which is the only shape change replace permits.
create or replace view v_deal_room_deal as
select d.id, d.phase, d.owner, d.deal_type as type, d.city, d.segment,
       d.attention, d.next_date, d.name
  from deal d;

grant select on v_deal_room_board to carr_reader;

commit;
