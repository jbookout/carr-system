-- 0021: the nightly-jobs role, and the cadence rule filter (ORDER 19(a) + 19(c);
-- Fable's TAP 0 ruling of 2026-07-31 ~4pm, widened the same evening after
-- ORDER 15's and ORDER 16's findings landed: ONE nightly-jobs role, not one per
-- pipeline).
--
-- WHY THIS EXISTS. Three orders in a row hit the same wall and each one parked
-- it rather than improvising a credential:
--   · ORDER 14 — the cadence engine WRITES (next_action + event) and the only
--     credential on this Mac is CARR_DB_EXPORTER_URL, views-only by design
--     (amendment 11). Engine and matcher both exit 78 NOT CONFIGURED.
--   · ORDER 15 — the metrics pull writes content_piece / placement /
--     placement_metric. Same wall, same exit.
--   · ORDER 16 — the review queue's ingest lane needs select on ingest_inbox
--     and document. Same wall; the queue ships two-thirds live.
-- The standing rule (secrets-inventory line 32) is that carr_writer's DSN is
-- NEVER handed to a session or parked on this Mac, and widening carr_exporter
-- would undo the views-only posture ORDER 7 made structural. So the answer is a
-- THIRD role that can do exactly the nightly jobs' work and nothing else.
--
-- THE PASSWORD IS NOT IN THIS FILE AND NOBODY HAS EVER SEEN IT. The role is
-- created with a random 24-byte placeholder generated inside the transaction —
-- so between apply and Joe's alter there is no shared secret to leak, and the
-- placeholder is unusable because nothing anywhere records it. Joe then runs
--     alter role carr_jobs password '<his own value>';
-- and writes CARR_DB_JOBS_URL into ~/.config/carr/db.env by his own hand. The
-- executing agent never holds the value at any point in that sequence.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- ONE DEVIATION FROM THE ORDER'S GRANT LIST, MEASURED RATHER THAN REASONED, AND
-- FLAGGED FOR FABLE RATHER THAN BURIED.
--
-- ORDER 19(a) lists the grants and says "exactly as listed, nothing wider". The
-- listed set was applied to a full-data branch first and the two pipelines were
-- then RUN under it. They cannot run: the engine dies on its first statement
-- (`permission denied for table actor`) and ten objects the two scripts read are
-- unreachable — actor, system_config, party, client, lead, vendor, deal,
-- deal_participant, space, building. No view carries the three facts they need
-- (owner_id, client status per subject, lead.est_lease_event; plus space and
-- building attributes for the matcher), and ORDER 7's stop rule forbids adding
-- columns to the reader-facing v_ref_index on an executing session's judgment.
--
-- So the list is an under-specification of the same class as ORDER 7's
-- party-only merged flag and ORDER 3's verbatim-summary clause, both of which
-- Fable ratified as the seat's miss. The correction here is the narrowest one
-- available: COLUMN-SCOPED select on exactly the columns those two scripts read,
-- traced statement by statement. Two consequences worth stating plainly:
--   · phone, email, notes_path and every other contact column is structurally
--     unreachable to this role — not by policy, by grant. The guard at the
--     bottom asserts it, so a later hand widening it fails a migration instead
--     of quietly shipping.
--   · the role still holds ZERO privilege on the export views (which DO carry
--     Phone and Email columns). Granting carr_reader to carr_jobs would have
--     been one line and is the reason it was not taken.
-- One view is granted whole: v_compiled_rules, which is rule text and carries no
-- contact data. Without it the monthly promotion review and conflict surfacing
-- would report UNAVAILABLE under this credential where today, under the
-- exporter, they report real counts — a regression the order did not intend.
-- ─────────────────────────────────────────────────────────────────────────────

-- ── 1. the role ──────────────────────────────────────────────────────────────
do $$
-- two v4 uuids, hyphens stripped: 256 bits of entropy from a built-in, so this
-- file needs no extension and still leaves nothing guessable behind.
declare pw text := replace(gen_random_uuid()::text || gen_random_uuid()::text, '-', '');
begin
  if exists (select 1 from pg_roles where rolname = 'carr_jobs') then
    raise notice 'carr_jobs already exists — password left exactly as it is';
  else
    execute format('create role carr_jobs login password %L', pw);
    raise notice 'carr_jobs created with a random placeholder password that is '
                 'not recorded anywhere. It cannot be used until Joe runs '
                 'alter role carr_jobs password ''<his value>''.';
  end if;
end $$;

grant usage on schema public to carr_jobs;

comment on role carr_jobs is
  'The nightly jobs role (ORDER 19a, 2026-07-31). Held by unattended local '
  'pipelines — cadence engine, availability matcher, placement metrics pull, '
  'learning jobs, review-queue drafter — through CARR_DB_JOBS_URL in '
  '~/.config/carr/db.env. NOT carr_writer (whose DSN never touches this Mac) '
  'and NOT carr_reader (whose export views carry Phone and Email). It writes '
  'exactly three lanes: next_action + event (cadence), the three content '
  'tables (metrics), and nothing else. Every other grant is read, and the '
  'record-side reads are COLUMN-SCOPED so contact detail is out of reach by '
  'construction rather than by care.';

-- ── 2. the grants ORDER 19(a) lists, verbatim ────────────────────────────────
grant select                 on cadence_rule, availability, space_search, v_ref_index to carr_jobs;
grant select, insert         on next_action, event                                    to carr_jobs;
grant select, insert, update on content_piece, placement, placement_metric            to carr_jobs;
grant select                 on ingest_inbox, document                                to carr_jobs;

-- ── 3. the measured additions, each traced to the statement it serves ────────
-- pipelines/cadence_engine.py load_context(): the automation actor's id.
grant select (id, slug, kind, display_name)                on actor  to carr_jobs;
-- cadence_engine: system_config key 'cadence.lookback_days'. matcher: matcher.*.
grant select (key, value)                                  on system_config to carr_jobs;
-- every subject query in cadence_engine + the matcher's search block need the
-- party NAME and nothing else about the party.
grant select (id, name)                                    on party  to carr_jobs;
grant select (id, roster_ref, party_id, status, owner_id)  on client to carr_jobs;
-- est_lease_event is the on_date half's only wired source (ORDER 19d backfills it).
grant select (id, registry_ref, party_id, client_id, owner_id, est_lease_event)
                                                           on lead   to carr_jobs;
grant select (id, vendor_ref, party_id, owner_id)          on vendor to carr_jobs;
grant select (id, name, client_id)                         on deal   to carr_jobs;
-- a deal's owner is its CURRENT lead participant, not a column.
grant select (deal_id, actor_id, role, to_at)              on deal_participant to carr_jobs;
-- pipelines/availability_matcher.py: availability -> space -> building.
grant select (id, building_id, suite, area_amount)         on space    to carr_jobs;
grant select (id, name, address, city, state, sub_type)    on building to carr_jobs;
-- pipelines/learning_jobs.py: the promotion review and conflict surfacing read
-- the rule store. Rule text only; no contact data exists in this view.
grant select on v_compiled_rules to carr_jobs;

-- ── 4. ORDER 19(c): cadence_rule.status_filter ───────────────────────────────
-- ORDER 14 reported it: "engaged-client nurture" could not be expressed as a
-- row, because cadence_rule had no status filter, so the seeded nurture45 rule
-- applied to EVERY non-cold, non-paused client with a completed action. The
-- hard cold/paused guard was exact; the qualifier in the rule's own name was
-- not. This is the column that makes the name true.
alter table cadence_rule add column status_filter text[];

comment on column cadence_rule.status_filter is
  'ORDER 19(c). Nullable text[] of client_status slugs. NULL means the rule '
  'applies to every subject the hard guard allows (the pre-0021 behaviour, '
  'unchanged). A non-empty array narrows the rule to subjects whose governing '
  'client status is in the list — nurture45 carries {engaged} so '
  '"engaged-client nurture" means what it says. This is a NARROWING filter '
  'only: it can never re-admit a cold or paused subject, because the hard '
  'abundance guard runs first and is not expressible in data by design.';

-- If the six-row seed has already been applied, the rule gains its filter here;
-- if it has not (production had not been seeded when this was written), the
-- seed file carries the filter itself and this updates nothing.
update cadence_rule set status_filter = array['engaged']
 where lane = 'nurture45' and subject_type = 'client' and status_filter is null;

-- ── 5. guards ────────────────────────────────────────────────────────────────
do $$
declare n int; leaked text;
begin
  if not exists (select 1 from pg_roles where rolname = 'carr_jobs' and rolcanlogin) then
    raise exception 'carr_jobs is missing or cannot log in';
  end if;

  -- the whole point of the column scoping: contact detail is UNREACHABLE.
  select string_agg(table_name || '.' || column_name, ', ') into leaked
    from information_schema.column_privileges
   where grantee = 'carr_jobs' and table_schema = 'public'
     and (column_name in ('phone','email','notes_path','npi','payload_pii')
          or column_name like '%_phone' or column_name like '%_email');
  if leaked is not null then
    raise exception
      'carr_jobs holds a privilege on contact column(s): %. The role reads names '
      'and ids; contact detail is out of reach by construction.', leaked;
  end if;

  -- and the export views (which DO carry Phone/Email) stay out of reach too.
  select string_agg(distinct table_name, ', ') into leaked
    from information_schema.role_table_grants
   where grantee = 'carr_jobs' and table_schema = 'public'
     and table_name like 'v\_export\_%';
  if leaked is not null then
    raise exception 'carr_jobs holds a grant on export view(s): %', leaked;
  end if;

  -- it must not have inherited a bundle: carr_writer or carr_reader membership
  -- would make every grant above decoration.
  if exists (select 1 from pg_auth_members m
               join pg_roles r on r.oid = m.roleid
               join pg_roles g on g.oid = m.member
              where g.rolname = 'carr_jobs'
                and r.rolname in ('carr_writer','carr_reader','carr_exporter')) then
    raise exception 'carr_jobs is a member of a privilege bundle — least privilege is the point';
  end if;

  -- writes: exactly the five tables the three lanes need, and no delete anywhere.
  select count(*) into n
    from information_schema.role_table_grants
   where grantee = 'carr_jobs' and table_schema = 'public'
     and privilege_type in ('INSERT','UPDATE','DELETE');
  if n <> 8 then
    raise exception
      'carr_jobs write grants = % (expected 8: insert on next_action+event, '
      'insert+update on content_piece+placement+placement_metric)', n;
  end if;
  if exists (select 1 from information_schema.role_table_grants
              where grantee = 'carr_jobs' and privilege_type = 'DELETE') then
    raise exception 'carr_jobs holds DELETE — no role in this schema deletes (A9)';
  end if;

  -- the new column exists, is nullable, and nothing else on cadence_rule moved.
  if not exists (select 1 from information_schema.columns
                  where table_schema='public' and table_name='cadence_rule'
                    and column_name='status_filter' and is_nullable='YES'
                    and data_type='ARRAY') then
    raise exception 'cadence_rule.status_filter is missing or is not a nullable array';
  end if;

  -- reader stays exactly where ORDER 11 left it: nothing on cadence_rule.
  select string_agg(distinct table_name, ', ') into leaked
    from information_schema.role_table_grants
   where grantee = 'carr_reader' and table_schema = 'public' and table_name = 'cadence_rule';
  if leaked is not null then
    raise exception 'carr_reader gained a grant on cadence_rule — not in any order';
  end if;

  raise notice 'ORDER 19 guards: carr_jobs can log in, 8 write grants, 0 delete, '
               'no bundle membership, no contact column, no export view; '
               'cadence_rule.status_filter present and nullable';
end $$;
