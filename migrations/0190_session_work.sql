-- 0190_session_work.sql
-- Session CRM v0: a staff-owned book of SESSION WORK in Neon.
--
-- WHAT THIS IS. The Chief of Staff needs a glanceable view of what is doing,
-- what is stale, and what promises are standing — across git worktrees, open
-- PRs, Hermes sessions, the carr-build kanban, and a small seeded promises
-- list. v0 stores that book in Neon (not a file next to Hermes, not markdown)
-- so every future surface reads the same rows.
--
-- NOT a people CRM. Neon already holds clients, leads, vendors, deals. This
-- table tracks WORK ITEMS — worktrees, PRs, sessions, kanban tasks, and
-- promises — each with the same shape regardless of source.
--
-- DESIGN. One row per work item, keyed by a stable id derived from
-- (kind, source_key). The harvest is deterministic (no model): it shells out
-- to git, gh, and hermes, parses the output, and upserts. last_seen is
-- refreshed on every harvest so staleness is queryable. open_loop is a boolean
-- (is this item still active?). next_seat is who or what owns the next step.
-- sources is a text array (which harvest sources mentioned this item).
-- promise is nullable text (non-null only for promise-kind rows).
--
-- GRANTS. carr_jobs gets insert+update (the harvest writes through
-- CARR_DB_JOBS_URL). carr_reader gets select (the brief and future surfaces
-- read through the exporter credential). No delete — consistent with the
-- schema's no-delete posture (A9).

begin;

create table if not exists session_work (
    id          text primary key,           -- stable: kind ':' source_key
    kind        text not null,              -- worktree, pr, hermes_session, kanban, promise
    title       text not null,              -- human-readable label
    last_seen   timestamptz not null default now(),
    open_loop   boolean not null default true,
    next_seat   text,                       -- who/what owns the next step (nullable)
    sources     text[] not null default '{}',  -- which harvest sources saw this
    promise     text,                       -- non-null only for promise rows
    kin         text,                       -- optional grouping hint (v0: null)
    version     int not null default 1,     -- [A2] bumped by trg_touch_row on update
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create index if not exists session_work_kind_idx      on session_work (kind);
create index if not exists session_work_open_loop_idx  on session_work (open_loop) where open_loop;
create index if not exists session_work_last_seen_idx  on session_work (last_seen);

-- touch trigger (same pattern as every mutating table in this schema)
create trigger session_work_touch before update on session_work
    for each row execute function trg_touch_row();

comment on table session_work is
  'Session CRM v0 (2026-08-19). A staff-owned book of SESSION WORK — worktrees, '
  'PRs, Hermes sessions, kanban tasks, and promises — harvested deterministically '
  'and upserted by pipelines/session_crm_harvest.py. NOT a people CRM; Neon holds '
  'clients/leads/vendors elsewhere. Reads through carr_reader (exporter credential), '
  'writes through carr_jobs (CARR_DB_JOBS_URL). No delete (A9).';

comment on column session_work.id is
  'Stable id: kind '':'''': source_key. source_key is the native identifier from '
  'the harvest source (worktree path, PR number, session id, kanban task id, '
  'promise slug).';

comment on column session_work.kind is
  'One of: worktree, pr, hermes_session, kanban, promise. ';

comment on column session_work.last_seen is
  'Refreshed on every harvest run. Staleness is now() - last_seen. The brief '
  'flags items where this exceeds 4 hours.';

comment on column session_work.sources is
  'Which harvest sources mentioned this item. An item can appear in multiple '
  'sources (e.g. a kanban task that also has a worktree and a PR).';

comment on column session_work.promise is
  'Non-null only for promise-kind rows. The promise text itself, e.g. '
  '"Codex control-plane trees stay".';

-- ── grants ───────────────────────────────────────────────────────────────────
-- carr_jobs: the harvest writes (insert + update). No delete, ever.
grant insert, update on session_work to carr_jobs;

-- carr_reader: the brief and future surfaces read through the exporter credential.
grant select on session_work to carr_reader;

-- ── guards ────────────────────────────────────────────────────────────────────
do $$
declare n int;
begin
  -- carr_jobs must NOT have delete on session_work
  if exists (select 1 from information_schema.role_table_grants
              where grantee = 'carr_jobs' and table_name = 'session_work'
                and privilege_type = 'DELETE') then
    raise exception 'carr_jobs holds DELETE on session_work — no role in this schema deletes (A9)';
  end if;

  -- carr_jobs has exactly insert + update (2 write grants)
  select count(*) into n
    from information_schema.role_table_grants
   where grantee = 'carr_jobs' and table_name = 'session_work'
     and privilege_type in ('INSERT','UPDATE','DELETE');
  if n <> 2 then
    raise exception 'carr_jobs write grants on session_work = % (expected 2: insert + update)', n;
  end if;

  -- kind must be one of the known values (checked at harvest time too, but
  -- a CHECK constraint is the structural backstop)
  -- Deliberately NOT a CHECK constraint: the kind vocabulary is open like the
  -- rest of this schema (insert a row, not a migration). The harvest enforces
  -- the known set; a future source adds a new kind without a migration.

  raise notice '0189: session_work created, carr_jobs has insert+update (no delete), carr_reader has select';
end $$;
