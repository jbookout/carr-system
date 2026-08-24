-- 0286_the_incident_ledger_gets_a_front_door.sql
-- THE OPERATIONAL INCIDENT LEDGER GETS A VERB SURFACE, AND THIS FILE IS THE
-- PERMISSION HALF OF THAT.
--
-- WHAT PROMPTED IT. The 2026-08-23 rules-and-verbs council seated two chairs
-- independently and both put the same item first: ops.incident is a durable
-- operational ledger with 22 open rows and NO verb surface at all. Reading it
-- means tools/ops-record.py on Joe's Mac. Closing one means the receipted
-- break-glass credential (CARR_BREAK_GLASS=1 tools/db-tap.py), because
-- cmd_resolve refuses to run without DATABASE_URL and an agent session's
-- permission classifier refuses that command. Grok's chair stated the cost in
-- one line: an evidence-complete SEV-1 close had to be handed to a human-
-- approved session. Codex's chair stated the rule it violates: "every durable
-- operational ledger exposes authorized read, evidence, transition, and close
-- paths; local CLI or break-glass credentials cannot be the sole normal route."
--
-- The break-glass path is NOT retired by this. It stays exactly where it is,
-- for the emergency it was built for. What changes is that it stops being the
-- ordinary way a partner closes an incident.
--
-- ── WHY A MIGRATION AT ALL, WHEN THE VERBS ARE JAVASCRIPT ───────────────────
-- RULE 5409731b: a new writer changes the permission surface of every table it
-- touches, and every one of them has to be grant-checked. 0117 is that rule
-- arriving one run late — `assess` shipped, ran against production, and died on
-- `permission denied for table incident`. 0122 is the same rule obeyed on time:
-- it added NO grant statement at all and instead ASSERTED, from the catalog,
-- that carr_writer already held everything the Worker's failure recorder was
-- about to use. This file is the third instance, and it is deliberately shaped
-- like 0122 rather than 0117: the five new verbs touch six relations, all six
-- assertions are below, and if any one of them is false this migration fails
-- instead of the verb failing in production at the moment somebody needs it.
--
-- ── THE ONE STRUCTURAL ADDITION: duplicate_of_id ────────────────────────────
-- Both chairs asked adjudication to record a duplicate-of decision, and the
-- ledger had nowhere to put one. ops.incident_link's `kind` check does not
-- admit 'incident', and the alternative — writing "duplicate of INC-X" into
-- root_cause and hoping a reader parses it — is exactly the prose-matching
-- 0116 removed from the dedup path for stated reasons that apply again here.
--
-- IT IS NOT A SECOND CLOSE PATH. Setting duplicate_of_id records a judgment;
-- it does not resolve the row. close-incident remains the only way an incident
-- reaches 'resolved', and it reads this column as the thing that satisfies its
-- evidence requirement — "what shows it is safe to close" is "this is the same
-- event as INC-X, which carries the watch." One close path, one set of guards.
--
-- WHAT THE COLLECTOR CANNOT DO WITH IT, and it falls out for free. carr_jobs
-- holds a COLUMN-SCOPED update on ops.incident (0117), naming six columns.
-- A column added today is not among them and cannot be added to a grant by
-- accident. So a machine reading an exit code can still report a recovery and
-- still cannot decide that two failures are the same event — which is the same
-- boundary 0117 drew around severity and root cause, extended to the newest
-- judgment column without a single new line of policy. Asserted below.

begin;

alter table ops.incident
  add column if not exists duplicate_of_id uuid references ops.incident(id);

comment on column ops.incident.duplicate_of_id is
  'A partner adjudication: this incident is the same operational event as '
  'another one, which carries the investigation and the monitoring window. '
  'Set by the adjudicate-incident verb (partner authority) and read by '
  'close-incident as evidence. NOT a state: a row with this set is still open '
  'until close-incident closes it. Null for every incident that stands alone.';

-- An incident cannot be a duplicate of itself. Without this the column accepts
-- i.id = i.duplicate_of_id, which reads as "this is a duplicate" while pointing
-- at nothing that could carry the investigation, and every downstream join
-- silently resolves to the row it started from.
alter table ops.incident
  drop constraint if exists a_duplicate_points_at_another_incident;
alter table ops.incident
  add constraint a_duplicate_points_at_another_incident
  check (duplicate_of_id is null or duplicate_of_id <> id);

-- The board reads "how many rows point at this one" per canonical incident, and
-- get-incident reads the same set. Partial: the overwhelming majority of rows
-- carry null here and indexing them buys nothing.
create index if not exists incident_duplicate_of_idx
  on ops.incident (duplicate_of_id)
  where duplicate_of_id is not null;

commit;

-- ── proof, in the same run ───────────────────────────────────────────────────
-- TWO KINDS OF PROOF, for the same reason 0117 gives. The GRANT half is read
-- from the catalog with has_table_privilege / has_column_privilege, because
-- executing it needs `set role`, and widening the role graph for the length of
-- a migration to prove a narrowing is the wrong trade. The CONSTRAINT half is
-- executed for real: a constraint nobody has watched refuse anything is a
-- comment with punctuation.
do $$
declare
  v_service uuid;
  v_a       uuid;
  v_b       uuid;
  v_need    record;
begin
  -- 1. THE GRANT SURFACE OF THE FIVE NEW VERBS, relation by relation.
  --
  --    READS (incident-board, get-incident) run on DATABASE_URL_READER, which
  --    authenticates as carr_reader. WRITES (open-incident, close-incident,
  --    adjudicate-incident) run on DATABASE_URL_WRITER as carr_writer — the
  --    same connection and the same role the Worker's existing failure
  --    recorder (mcp-server/src/trace.js) already uses against these tables.
  --
  --    Every relation either verb family touches is named here. This list IS
  --    the grant check rule 5409731b asks for; nothing is assumed from the fact
  --    that a sibling table happened to be granted in the same statement years
  --    ago.
  --    EACH ROW NAMES ITSELF. A loop that only reports "something is missing"
  --    hands the next reader the same search this file exists to have already
  --    done, so the failure message carries the role, the relation and the
  --    privilege that was absent.
  for v_need in
    select * from (values
      ('carr_reader', 'ops.incident',            'select', 'incident-board / get-incident read the rows'),
      ('carr_reader', 'ops.incident_fact',       'select', 'get-incident returns the evidence trail'),
      ('carr_reader', 'ops.incident_hypothesis', 'select', 'get-incident returns what was believed, separately from what is known'),
      ('carr_reader', 'ops.incident_link',       'select', 'the board counts linked ledger rows as occurrences'),
      ('carr_reader', 'ops.incident_service',    'select', 'get-incident names the services an incident touches'),
      ('carr_reader', 'ops.service',             'select', 'open-incident resolves a service key; the reads name it back'),
      ('carr_reader', 'ops.v_trace',             'select', 'get-incident returns the correlated journey')
    ) as t(grantee, relation, privilege, why)
    where not has_table_privilege(t.grantee, t.relation, t.privilege)
  loop
    raise exception '0286 FAILED: % lacks % on % — %. The incident READ verbs run on '
                    'DATABASE_URL_READER and would answer permission denied.',
                    v_need.grantee, v_need.privilege, v_need.relation, v_need.why;
  end loop;

  for v_need in
    select * from (values
      ('carr_writer', 'ops.incident',         'select', 'every write verb finds its row first'),
      ('carr_writer', 'ops.incident',         'insert', 'open-incident mints'),
      ('carr_writer', 'ops.incident',         'update', 'close-incident and adjudicate-incident write the row'),
      ('carr_writer', 'ops.incident_fact',    'select', 'the occurrence de-dupe reads existing facts'),
      ('carr_writer', 'ops.incident_fact',    'insert', 'every occurrence, early close and adjudication leaves a fact'),
      ('carr_writer', 'ops.incident_link',    'select', 'open-incident does not double-link one ledger row'),
      ('carr_writer', 'ops.incident_link',    'insert', 'open-incident links the run or deployment it was told about'),
      ('carr_writer', 'ops.incident_service', 'select', 'open-incident does not double-attach a service'),
      ('carr_writer', 'ops.incident_service', 'insert', 'open-incident attaches the service it resolved'),
      ('carr_writer', 'ops.service',          'select', 'open-incident refuses to invent an unregistered service')
    ) as t(grantee, relation, privilege, why)
    where not has_table_privilege(t.grantee, t.relation, t.privilege)
  loop
    raise exception '0286 FAILED: % lacks % on % — %. The incident WRITE verbs would die on the '
                    'writer connection the way assess died in 0117.',
                    v_need.grantee, v_need.privilege, v_need.relation, v_need.why;
  end loop;

  -- The new column specifically. 0115 granted carr_writer table-level UPDATE,
  -- which covers a column added later — but "covers" is a claim about Postgres
  -- semantics, and the whole point of this file is that a grant claim is worth
  -- nothing until something has tried it.
  if not has_column_privilege('carr_writer', 'ops.incident', 'duplicate_of_id', 'update') then
    raise exception '0286 FAILED: carr_writer cannot write duplicate_of_id — a table-level UPDATE '
                    'grant did not reach a column added after it, so adjudicate-incident has no '
                    'writable home for its duplicate decision';
  end if;

  -- 1b. AND THE ROLES THAT ACTUALLY LOG IN, which are not the roles above.
  --
  -- carr_reader and carr_writer are BUNDLES. The Worker's secrets authenticate
  -- as app_reader and app_writer, which are granted those bundles
  -- (tools/provision-staging-app-writer.py: READER_ROLE 'app_reader',
  -- READER_BUNDLE_ROLE 'carr_reader'). Every assertion above is therefore about
  -- a role no connection uses, and it holds for the connection only if the
  -- membership inherits.
  --
  -- THAT DISTINCTION IS NOT ACADEMIC HERE. mcp-server/smoke-reads.sh exists
  -- because exactly this went wrong: `find` and `catch-me-up` queried base
  -- tables "carr_reader cannot see", and the gap "survived from build day"
  -- until a done-test tripped over it. A grant proof that stops at the bundle
  -- is the same proof that missed it.
  --
  -- SKIPPED, NOT FAILED, WHERE THE LOGIN ROLES ARE ABSENT: a disposable cluster
  -- built from db/schema.sql has the bundles (0115 creates them) and no login
  -- roles, because those are provisioned per environment rather than by a
  -- migration. Silence would be wrong, so it says which case it was.
  -- TWO HALVES, BECAUSE has_table_privilege ONLY ANSWERS ONE OF THEM. It counts
  -- privileges reachable through role MEMBERSHIP and does not model the INHERIT
  -- attribute at all: a login role granted its bundle WITH NOINHERIT still reads
  -- as privileged here, while its actual sessions hold nothing until they SET
  -- ROLE. Proven on a disposable cluster while writing this — `alter role
  -- app_reader noinherit` changed no answer below. So the membership half is the
  -- loop, and the inherit half is asserted separately and explicitly. Either one
  -- alone would be a check that claims more than it proves.
  --
  -- The existence test is INSIDE the loop body, not in the WHERE clause.
  -- has_table_privilege RAISES on a role that does not exist, and a planner is
  -- free to evaluate it before an `exists` predicate sitting beside it — which
  -- is how the first draft of this block died with `role "app_reader" does not
  -- exist` on a disposable cluster instead of skipping. A plpgsql IF is ordered;
  -- a WHERE conjunct is not.
  for v_need in select rolname from pg_roles
                 where rolname in ('app_reader', 'app_writer') and not rolinherit
  loop
    raise exception '0286 FAILED: login role % does not inherit its bundle. It is granted '
                    'carr_reader/carr_writer and reads as privileged to has_table_privilege, '
                    'which counts membership and ignores INHERIT — but its sessions hold nothing '
                    'until they SET ROLE, and the Worker never does.', v_need.rolname;
  end loop;

  for v_need in
    select * from (values
      ('app_reader', 'ops.incident', 'select'),
      ('app_reader', 'ops.incident_fact', 'select'),
      ('app_reader', 'ops.incident_link', 'select'),
      ('app_reader', 'ops.v_trace', 'select'),
      ('app_writer', 'ops.incident', 'insert'),
      ('app_writer', 'ops.incident', 'update'),
      ('app_writer', 'ops.incident_fact', 'insert'),
      ('app_writer', 'ops.incident_service', 'insert'),
      ('app_writer', 'ops.service', 'select')
    ) as t(grantee, relation, privilege)
  loop
    if exists (select 1 from pg_roles where rolname = v_need.grantee)
       and not has_table_privilege(v_need.grantee, v_need.relation, v_need.privilege) then
      raise exception '0286 FAILED: login role % cannot reach % on % through any membership it '
                      'holds — the connection the verbs actually open cannot do what the bundle '
                      'above says it can.',
                      v_need.grantee, v_need.privilege, v_need.relation;
    end if;
  end loop;
  if not exists (select 1 from pg_roles where rolname = 'app_reader') then
    raise notice '0286: app_reader/app_writer are absent here, so the login-role half of the '
                 'grant check did not run — the bundle half above did. Expected on a disposable '
                 'cluster; NOT expected in staging or production.';
  end if;

  -- 2. THE COLLECTOR STILL CANNOT ADJUDICATE. 0117 scoped carr_jobs to six
  --    columns so a machine could report a recovery and never reclassify an
  --    incident. duplicate_of_id is the newest judgment column and it must
  --    inherit that boundary by construction, not by anyone remembering.
  if has_column_privilege('carr_jobs', 'ops.incident', 'duplicate_of_id', 'update') then
    raise exception '0286 FAILED: carr_jobs can write duplicate_of_id — a collector could decide '
                    'two failures are the same event, which is 0117''s boundary breached by a '
                    'column added after it';
  end if;
  -- And the half that must still work, or the nightly recovery path is broken.
  if not has_column_privilege('carr_jobs', 'ops.incident', 'state', 'update') then
    raise exception '0286 FAILED: carr_jobs lost its recovery grant';
  end if;

  -- 3. THE CONSTRAINT, EXECUTED.
  insert into ops.service (key, name, family, criticality, owner_actor)
    values ('migration-0286-proof', 'proof', 'Data', 'low', 'system')
    returning id into v_service;

  insert into ops.incident (ref, title, severity, state, environment,
                            detected_source, source_kind, source_ref, signature)
    values ('INC-0286-PROOF-A', 'proof a', 'SEV-3', 'detected', 'local',
            'proof', 'operator', 'proof', 'sig-0286-a|local|job|boom')
    returning id into v_a;
  insert into ops.incident (ref, title, severity, state, environment,
                            detected_source, source_kind, source_ref, signature)
    values ('INC-0286-PROOF-B', 'proof b', 'SEV-3', 'detected', 'local',
            'proof', 'operator', 'proof', 'sig-0286-b|local|job|boom')
    returning id into v_b;

  begin
    update ops.incident set duplicate_of_id = v_a where id = v_a;
    raise exception '0286 FAILED: an incident was recorded as a duplicate of itself';
  exception when check_violation then null;
  end;

  -- The real adjudication is accepted, and it does NOT move the row's state:
  -- recording a judgment is not closing an incident.
  update ops.incident set duplicate_of_id = v_a where id = v_b;
  if not exists (select 1 from ops.incident
                  where id = v_b and duplicate_of_id = v_a and state = 'detected') then
    raise exception '0286 FAILED: a duplicate adjudication either did not take or moved the state';
  end if;

  -- 4. AND THE CLOSE GUARD IS UNTOUCHED. A duplicate is still not resolvable
  --    without the evidence and window 0115 demands; close-incident supplies
  --    them from this column rather than being excused from them.
  begin
    update ops.incident set state = 'resolved' where id = v_b;
    raise exception '0286 FAILED: a duplicate reached resolved with no evidence';
  exception when check_violation then null;
  end;

  delete from ops.incident where id in (v_a, v_b);
  delete from ops.service where id = v_service;

  raise notice '0286: the read and write roles hold every privilege the five incident verbs use, '
               'carr_jobs still cannot adjudicate a duplicate, a self-duplicate is refused, and '
               'recording a duplicate does not close anything';
end $$;
