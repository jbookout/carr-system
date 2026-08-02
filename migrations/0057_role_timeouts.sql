-- 0057_role_timeouts.sql — a read can no longer run forever on Joe's dime.
--
-- THE GAP, verified rather than assumed. `grep -rn 'statement_timeout\|lock_timeout\|
-- idle_in_transaction' ~/carr-system` returns nothing: not in the migrations, not in the
-- MCP server, not in the exporters, not in the pipelines. Postgres ships all three at 0,
-- which means UNLIMITED, so every credential this system has ever handed out can hold a
-- statement open until the client gives up or the connection dies.
--
-- WHY THAT COSTS MONEY HERE SPECIFICALLY. Neon bills compute-seconds and suspends an idle
-- compute to zero. A statement that never finishes is a compute that never idles, so the
-- meter runs at full rate on work whose caller stopped listening minutes ago. The obvious
-- candidate is `who-do-we-know`, which walks party_link up to 3 hops; today the graph is
-- small and the walk is trivial, but the walk is bounded by hops and NOT by rows, so its
-- cost is whatever the edge count grows into. A bound that only exists in the query author's
-- head is not a bound.
--
-- ONE PLACE, NOT PER QUERY. Role settings are applied by Postgres at connect time from
-- pg_db_role_setting, before the client sends anything, so a per-role default covers every
-- verb in tools.js, every exporter, every board and every future query with no code change
-- and nothing to remember. Per-query timeouts would need touching ~35 verbs and would be
-- missing from verb 36.
--
-- THE TRAP THAT MAKES THE OBVIOUS VERSION OF THIS MIGRATION A NO-OP, and the reason the
-- role list below looks nothing like the role list in the design docs. `carr_reader`,
-- `carr_writer` and `carr_exporter` are NOLOGIN privilege bundles (0004, 0006) — nobody
-- connects as them. The roles that actually authenticate are `app_reader`, `app_writer`,
-- `app_exporter_local` (created via neonctl and GRANTed a bundle, so no password ever
-- appears in SQL) and `carr_jobs` (0021). Postgres looks up role settings for the role that
-- LOGGED IN and does not inherit them through role membership — `alter role carr_reader set
-- statement_timeout` would apply to exactly zero sessions while reading like a fix. The
-- setting has to land on the login roles, and the guard below asserts that every login role
-- holding a bundle actually carries one, so a login role added later without a timeout fails
-- a migration instead of quietly running unbounded.
--
-- THE VALUES, MEASURED THEN ROUNDED UP HARD.
--   Timed on the rehearse-verbs-20260802 branch (a copy of live data): v_ref_index 52ms,
--   v_integrity_digest 58ms, v_capture_coverage 67ms, v_contact 32ms. The heaviest
--   reader-facing surface in the system is under a tenth of a second.
--   · app_reader        15s  — 200x the measured worst case. A human waiting on `find` or a
--                             board gave up ten seconds ago, so past this point the query is
--                             buying nothing and costing compute. Nothing legitimate is
--                             anywhere near it; if this ever fires it is a pathology, not a
--                             slow day.
--   · app_writer        60s  — 4x the reader, because a write holds row locks and blocking
--                             the verb layer is worse than being slow. Still low enough that
--                             a wedged write surfaces as an error a human sees rather than a
--                             session nobody notices.
--   · app_exporter_local 60s — YES, exports get their own value, and higher than reads: an
--                             export legitimately scans whole views (candidate_pool alone is
--                             9,860 rows) and nobody is sitting in front of it, so the
--                             interactive argument for 15s does not apply. It is NOT set as
--                             high as the jobs role, because an export runs on a schedule
--                             that expects it back.
--   · carr_jobs        120s  — the loosest, and deliberately so. Unattended nightly batch,
--                             no human waiting, legitimately the longest single statements
--                             in the system. Two minutes is still far short of "forever": on
--                             this data volume a statement past 120s is hung, not busy.
--   · neondb_owner      NOT SET, on purpose. This is the migration and admin role. A DDL
--                             statement killed halfway is the one failure mode worse than a
--                             slow one, and a human is always watching a migration run.
--
-- idle_in_transaction_session_timeout IS THE OTHER HALF, and it is the one that actually
-- bites on Neon. A session that opens a transaction and then dies (a crashed Worker, a
-- laptop that slept mid-verb) leaves an idle-in-transaction backend that pins the xmin
-- horizon so vacuum cannot clean anything, AND keeps the compute from scaling to zero,
-- forever, on zero work. statement_timeout does not touch it because no statement is
-- running. Reader and writer get 60s and 120s; exporter and jobs get 300s because those
-- processes do slow non-database work (file writes, R2 puts) between statements and a
-- tighter value would kill honest runs.
--
-- lock_timeout IS DELIBERATELY NOT SET HERE, twice over. For these four roles it would be
-- wrong: they run DML, and a lock_timeout turns ordinary row contention into spurious
-- errors. For production DDL it is right, but it belongs in tools/migrate.py (set it on the
-- runner's session before applying, so a migration that cannot get its lock fails fast
-- instead of queueing behind a long read and blocking every reader behind it). That file is
-- owned by another seat this session and is NOT touched here; it is called out in the
-- handover instead.
--
-- THIS IS A GUARDRAIL, NOT A SECURITY BOUNDARY, and saying so plainly matters. Both settings
-- are USERSET: any session can raise its own with `set statement_timeout`. That is a feature
-- — `set local statement_timeout = '5min'` inside one transaction is the correct escape
-- hatch for a genuinely long operation, and it is far better than raising the role default
-- and losing the protection for everything else. What this stops is the accident, which is
-- the only thing that has ever happened.

begin;

-- Interactive reads. The reader login role is a member of carr_reader (views only, zero
-- base-table grants), so this covers find, the boards, who-do-we-know and every future verb.
alter role app_reader          set statement_timeout                     = '15s';
alter role app_reader          set idle_in_transaction_session_timeout    = '60s';

-- The MCP write path.
alter role app_writer          set statement_timeout                     = '60s';
alter role app_writer          set idle_in_transaction_session_timeout    = '120s';

-- Exports: longer statements, longer idle, because they interleave slow file and object
-- work between queries.
alter role app_exporter_local  set statement_timeout                     = '60s';
alter role app_exporter_local  set idle_in_transaction_session_timeout    = '300s';

-- Unattended nightly jobs.
alter role carr_jobs           set statement_timeout                     = '120s';
alter role carr_jobs           set idle_in_transaction_session_timeout    = '300s';

-- Operational visibility, so this is checkable between migrations rather than only at apply
-- time. Role names and timeout values carry no contact data, so the reader grant is safe
-- under the amendment-11 posture: everything in a reader-visible view is visible to a
-- reader-scoped session, and this is the whole content.
create or replace view v_role_timeouts as
select r.rolname                                             as role_name,
       r.rolcanlogin                                         as can_login,
       (select string_agg(g.rolname, ', ' order by g.rolname)
          from pg_auth_members m join pg_roles g on g.oid = m.roleid
         where m.member = r.oid and g.rolname like 'carr\_%') as bundles,
       (select s from unnest(coalesce(r.rolconfig, '{}'::text[])) s
         where s like 'statement_timeout=%')                  as statement_timeout,
       (select s from unnest(coalesce(r.rolconfig, '{}'::text[])) s
         where s like 'idle\_in\_transaction\_session\_timeout=%') as idle_timeout
  from pg_roles r
 where r.rolname in ('app_reader', 'app_writer', 'app_exporter_local', 'carr_jobs',
                     'carr_reader', 'carr_writer', 'carr_exporter', 'neondb_owner');

grant select on v_role_timeouts to carr_reader;

comment on view v_role_timeouts is
  'Per-role statement and idle-transaction timeouts (0057). Read it to answer "is anything '
  'able to run forever?" without waiting for the next migration guard. The bundles column '
  'is the point: a NOLOGIN bundle row showing no timeout is CORRECT — Postgres applies role '
  'settings to the role that logged in and never inherits them through membership, so the '
  'value has to sit on the login role. A login role with a bundle and a blank '
  'statement_timeout is the bug.';

-- guards BEFORE commit, so a failure rolls the whole thing back. (0043's lesson is that a
-- guard which cannot roll back is a report, not a guard; 0046 and 0056 state the lesson but
-- place the block after commit, where migrate.py has already ended the transaction and the
-- work stands regardless. 0052 and 0055 do it correctly and this follows them.)
do $$
declare
  unbounded  text;
  st         text;
  idle       text;
  owner_st   text;
begin
  -- Every LOGIN role that holds one of the privilege bundles must carry a statement_timeout.
  -- This is the check that catches a login role created later by neonctl and granted a
  -- bundle without anyone remembering this file exists.
  --
  -- neondb_owner is excluded, and it is excluded because this guard CAUGHT IT on its first
  -- run rather than because anyone predicted it: 0005 grants neondb_owner membership in
  -- carr_reader and carr_writer so it can SET ROLE for testing, which makes the admin role
  -- look exactly like an app role to a membership test. It is the one login role that must
  -- stay unbounded (see the header), and it gets its own opposite assertion below, so
  -- naming it here narrows the check instead of weakening it.
  select string_agg(t.role_name, ', ' order by t.role_name) into unbounded
    from v_role_timeouts t
   where t.can_login and t.bundles is not null and t.statement_timeout is null
     and t.role_name <> 'neondb_owner';
  if unbounded is not null then
    raise exception 'login role(s) with a privilege bundle and NO statement_timeout: % '
                    '(role settings do not inherit through membership — set it on the '
                    'login role, not on carr_reader/carr_writer/carr_exporter)', unbounded;
  end if;

  -- The reader value specifically, because it is the one the money argument rests on.
  select t.statement_timeout, t.idle_timeout into st, idle
    from v_role_timeouts t where t.role_name = 'app_reader';
  if st is distinct from 'statement_timeout=15s' then
    raise exception 'app_reader statement_timeout is %, expected 15s', coalesce(st, '(unset)');
  end if;
  if idle is distinct from 'idle_in_transaction_session_timeout=60s' then
    raise exception 'app_reader idle timeout is %, expected 60s', coalesce(idle, '(unset)');
  end if;

  -- Exports and jobs must be LOOSER than interactive reads. If a later hand tightens them to
  -- match the reader, honest long-running work starts failing at 3am with nobody watching.
  if (select statement_timeout from v_role_timeouts where role_name = 'app_exporter_local')
       is distinct from 'statement_timeout=60s' then
    raise exception 'app_exporter_local statement_timeout is not 60s — exports run longer '
                    'than interactive reads on purpose';
  end if;
  if (select statement_timeout from v_role_timeouts where role_name = 'carr_jobs')
       is distinct from 'statement_timeout=120s' then
    raise exception 'carr_jobs statement_timeout is not 120s';
  end if;

  -- The admin role must stay unbounded. A timeout here kills migrations mid-DDL.
  select statement_timeout into owner_st from v_role_timeouts where role_name = 'neondb_owner';
  if owner_st is not null then
    raise exception 'neondb_owner carries % — the migration role must stay unbounded so a '
                    'long DDL is never killed halfway', owner_st;
  end if;

  raise notice 'timeouts live: app_reader 15s / app_writer 60s / app_exporter_local 60s / '
               'carr_jobs 120s, idle-in-transaction 60s/120s/300s/300s, neondb_owner '
               'unbounded by design. Sessions pick these up on their NEXT connect, not on '
               'open ones. lock_timeout for production DDL still belongs in tools/migrate.py.';
end $$;

commit;
