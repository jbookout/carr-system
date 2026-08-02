-- 0033_deal_inherits_client_touch.sql — a deal's last touch is its CLIENT's last touch.
--
-- Joe, 2026-08-02: "you've got notes on bhate pelvic floor and Peterson bc I've actually
-- used the system to work on them. So something is missing bt what you're seeing on that
-- database and the notes about deals we work on."
--
-- He was right, and this is half of why. v_last_touch computed each subject in isolation:
-- a deal's last_touch counted only activity rows carrying that deal_id. But the work is
-- logged against the CLIENT — Gulf Coast Pelvic Floor's client record holds 16 rows and
-- a last_touch of 2026-07-30, while its DEAL read null. All 40 deals carry a client_id,
-- so the link was always there; the view just never walked it.
--
-- A deal IS a client engagement. Contact with the client IS contact on the deal. Treating
-- them as unrelated subjects made the deal board look abandoned while the client file was
-- current. This unions the deal's own rows with its client's, and takes the later.
--
-- (The other half of the gap is that most of that client narrative is kind='analysis',
--  which is correctly NOT contact. That is the capture fix — pipelines/extract_contact_events.py
--  and capture-problem-2026-08-02.md — not this migration.)

begin;

create or replace view v_last_touch as
with contact as (
  select a.*
    from activity a
    join activity_kind k on k.slug = a.kind
   where k.is_contact
      or (a.kind = 'note' and a.summary = 'last-touch stamp (imported)')
      or (a.kind = 'note' and a.summary like 'Last touch carried from%')
),
-- a deal's touches: its own rows, PLUS its client's rows
deal_contact as (
  select d.id as deal_id, c.occurred_at
    from deal d join contact c on c.deal_id = d.id
  union all
  select d.id, c.occurred_at
    from deal d
    join client cl on cl.id = d.client_id
    join contact c on c.client_id = cl.id
)
select 'deal'::text as subject_type, deal_id as subject_id, max(occurred_at)::date as last_touch
  from deal_contact group by deal_id
union all
select 'client'::text, client_id, max(occurred_at)::date
  from contact where client_id is not null group by client_id
union all
select 'lead'::text, lead_id, max(occurred_at)::date
  from contact where lead_id is not null group by lead_id
union all
select 'vendor'::text, vendor_id, max(occurred_at)::date
  from contact where vendor_id is not null group by vendor_id;

commit;

-- guard: the inheritance must ADD deals without disturbing the other three subject types
do $$
declare d int; c int; l int; v int;
begin
  select count(*) into d from v_last_touch where subject_type='deal';
  select count(*) into c from v_last_touch where subject_type='client';
  select count(*) into l from v_last_touch where subject_type='lead';
  select count(*) into v from v_last_touch where subject_type='vendor';
  if c <> 10 then raise exception 'client last_touch disturbed: expected 10, got %', c; end if;
  if l <> 11 then raise exception 'lead last_touch disturbed: expected 11, got %', l; end if;
  if v <> 2  then raise exception 'vendor last_touch disturbed: expected 2, got %', v; end if;
  raise notice 'deals with an inherited last_touch: %', d;
end $$;
