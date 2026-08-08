-- 0079_deal_room_api.sql — Deal Room polling, presence, field conflicts, and
-- append-only next-step context. DDL only; deployment is owned by orchestration.

begin;

alter table deal add column if not exists owner text references actor(slug);
alter table deal add column if not exists attention boolean not null default false;
alter table deal add column if not exists next_date date;

-- Preserve the board's existing lead attribution as the initial cell value.
update deal d
   set owner = a.slug,
       updated_by = p.set_by
  from deal_participant p join actor a on a.id = p.actor_id
 where p.deal_id = d.id and p.role = 'lead' and p.to_at is null
   and d.owner is null;

create table deal_presence_lease (
  actor_id  uuid not null references actor(id),
  deal_id   uuid not null references deal(id),
  field     text not null,
  expires_at timestamptz not null,
  primary key (actor_id, deal_id, field)
);
create index deal_presence_live_idx on deal_presence_lease (expires_at);

create table deal_note (
  id         uuid primary key default gen_random_uuid(),
  deal_id    uuid not null references deal(id),
  kind       text not null check (kind in ('note','next_step')),
  text       text not null check (btrim(text) <> ''),
  actor_id   uuid not null references actor(id),
  created_at timestamptz not null default now()
);
create index deal_note_thread_idx on deal_note (deal_id, created_at desc, id desc);

create table deal_conflict (
  id          uuid primary key default gen_random_uuid(),
  deal_id     uuid not null references deal(id),
  field       text not null check (field in ('phase','owner','attention','next_date')),
  value_a     jsonb,
  actor_a     uuid not null references actor(id),
  event_a     uuid not null references event(id),
  value_b     jsonb,
  actor_b     uuid not null references actor(id),
  status      text not null default 'open' check (status in ('open','resolved')),
  resolved_by uuid references actor(id),
  winner      text check (winner in ('a','b')),
  created_at  timestamptz not null default now(),
  resolved_at timestamptz,
  check ((status = 'open' and resolved_by is null and winner is null and resolved_at is null)
      or (status = 'resolved' and resolved_by is not null and winner is not null and resolved_at is not null))
);
create index deal_conflict_open_idx on deal_conflict (deal_id, field, created_at desc)
  where status = 'open';

-- The cursor's total order. The subject predicate is constant on this surface.
create index event_deal_cursor_idx on event (recorded_at, id)
  where subject_type = 'deal';

create view v_deal_room_event as
select e.id, e.recorded_at, a.slug as actor, e.verb, e.subject_type,
       e.subject_id, e.field,
       e.old_value - array['sf_commission_placeholder','sf_close_date_placeholder'] as old_value,
       e.new_value - array['sf_commission_placeholder','sf_close_date_placeholder'] as new_value
  from event e join actor a on a.id = e.actor_id
 where e.subject_type = 'deal'
   and e.field is distinct from 'sf_commission_placeholder'
   and e.field is distinct from 'sf_close_date_placeholder';

create view v_deal_room_presence as
select a.slug as actor, p.deal_id, p.field, p.expires_at
  from deal_presence_lease p join actor a on a.id = p.actor_id;

create view v_deal_room_deal as
select d.id, d.phase, d.owner, d.deal_type as type, d.city, d.segment,
       d.attention, d.next_date
  from deal d;

create view v_deal_room_note as
select n.id, n.deal_id, n.kind, n.text, a.slug as actor, n.created_at
  from deal_note n join actor a on a.id = n.actor_id;

create view v_deal_room_critical_date as
select c.id, c.deal_id, c.kind, c.due_on, c.note, c.source, c.status
  from critical_date c;

grant select, insert, update on deal_presence_lease, deal_conflict to carr_writer;
grant select, insert on deal_note to carr_writer;
grant select on v_deal_room_event, v_deal_room_presence, v_deal_room_deal,
                v_deal_room_note, v_deal_room_critical_date to carr_reader;

commit;
