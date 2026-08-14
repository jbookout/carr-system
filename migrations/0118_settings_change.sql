-- 0118_settings_change.sql
-- A SETTINGS CHANGE RECORDS ITSELF, AND THE DATABASE REFUSES ONE THAT EXPLAINS
-- NOTHING.
--
-- THE INCIDENT, 2026-08-14. A session commissioned to fix main's merge treadmill
-- turned off the branch ruleset's "require branches to be up to date" flag —
-- a remedy Joe had named himself, under instructions that said "present the
-- finished configuration to Joe with what changed and why" — and was
-- INTERRUPTED before it reported. The change reached the repository. The reason
-- reached nothing. Eight hours later another session found the setting missing,
-- could not tell an authorised change from tampering, and rebuilt authorship out
-- of ruleset version history, a shell-history timestamp and a transcript search.
--
-- The crux is not that sessions fail to write things down. That session was told
-- to and would have. The record died because it depended on the session
-- SURVIVING TO THE END, and a session is the least durable thing here.
--
-- So hooks/settings-change-gate.py takes the record at the moment of the change,
-- and this table is where it lands. The gate refuses a change with no reason;
-- this refuses a ROW with no reason, so a future caller that bypasses the hook
-- still cannot store a change nobody can account for.
--
-- WHY NOT REUSE ops.deployment OR ops.run. A deployment places a release into an
-- environment; a run is a job that started and ended. A settings change is
-- neither — it has no duration, no release, and its subject is a control plane
-- (GitHub, launchd, git config) rather than a service this system deploys. The
-- consolidation rule says derive rather than duplicate, and there is nothing
-- here to derive from: none of the existing tables carry a `reason`, which is
-- the one column this whole table exists for.
--
-- NO SECRET VALUES, EVER. `gh secret set NEON_API_KEY` records that the secret
-- was set and which one. The value is never on the command line to begin with,
-- and `command` stores the invocation, truncated, not its stdin.

begin;

create table if not exists ops.settings_change (
  id           uuid primary key default gen_random_uuid(),
  recorded_at  timestamptz not null default now(),

  -- What class of control plane moved. Free text rather than a check: the list
  -- grows as this system acquires surfaces, and a fixed vocabulary would force
  -- a new one into an old bucket on the day it is first touched.
  kind         text not null,
  target       text not null,          -- the ruleset, variable, job or key. Never a value.

  -- THE COLUMN THE TABLE EXISTS FOR. NOT NULL, and long enough to say something:
  -- the gate rejects "x" and "test" before it gets here, and this makes the same
  -- refusal true of any caller that skips the gate.
  reason       text not null,
  constraint a_reason_has_to_say_something check (length(btrim(reason)) >= 8),

  -- A change that failed and a change that landed are different facts. Only the
  -- post-tool hook knows which happened, which is why the recording is split
  -- across two halves of one gate.
  outcome      text not null check (outcome in ('applied','failed')),

  session_id   text not null,          -- the thread back to the transcript
  actor        text,                   -- the human or machine principal, when known
  command      text,                   -- the invocation, truncated; never its stdin
  environment  text
    check (environment is null or environment in ('local','rehearsal','staging','production')),

  correlation_id uuid not null default gen_random_uuid()
);

comment on table ops.settings_change is
  'Every change to a control plane this system does not own — GitHub rulesets, '
  'Actions variables and secrets, branch protection, launchd jobs, git config, '
  'Worker secrets. Written by hooks/settings-change-gate.py AT THE MOMENT OF THE '
  'CHANGE, because a record that waits for the session to finish dies with it.';

comment on column ops.settings_change.reason is
  'Why the change was made, in the words of whoever made it. The 2026-08-14 '
  'ruleset incident was not a missing change log — it was a missing REASON: the '
  'change itself was discoverable from GitHub''s own version history within '
  'minutes, and still nobody could tell an authorised change from tampering.';

create index if not exists settings_change_recorded_idx
  on ops.settings_change (recorded_at desc);
create index if not exists settings_change_kind_idx
  on ops.settings_change (kind, recorded_at desc);

-- The hook runs locally as the jobs role; it appends and never rewrites, because
-- a ledger whose writer can edit history is not a ledger.
grant select, insert on ops.settings_change to carr_jobs, carr_writer;
grant select on ops.settings_change to carr_reader;

commit;

-- ── proof, in the same run ───────────────────────────────────────────────────
do $$
declare v_id uuid;
begin
  -- 1. A CHANGE THAT EXPLAINS NOTHING IS REFUSED.
  begin
    insert into ops.settings_change (kind, target, reason, outcome, session_id)
      values ('github_ruleset', 'rulesets/20824501', 'x', 'applied', 's');
    raise exception '0118 FAILED: a row with a one-character reason was accepted';
  exception when check_violation then null;
  end;

  begin
    insert into ops.settings_change (kind, target, reason, outcome, session_id)
      values ('github_ruleset', 'rulesets/20824501', '        ', 'applied', 's');
    raise exception '0118 FAILED: a whitespace reason was accepted';
  exception when check_violation then null;
  end;

  -- 2. AN UNDEFINED OUTCOME IS REFUSED — "it probably worked" is not an outcome.
  begin
    insert into ops.settings_change (kind, target, reason, outcome, session_id)
      values ('github_ruleset', 'rulesets/20824501', 'a real stated reason', 'maybe', 's');
    raise exception '0118 FAILED: an undefined outcome was accepted';
  exception when check_violation then null;
  end;

  -- 3. THE REAL ROW LANDS — the one this table was built the day it was missing.
  insert into ops.settings_change
      (kind, target, reason, outcome, session_id, command, environment)
    values ('github_ruleset', 'repos/jbookout/carr-system/rulesets/20824501',
            'drop the strict up-to-date requirement so ~9 concurrent sessions stop '
            'livelocking main; PR #44 needed an admin merge after three clean attempts',
            'applied', 'de829b6e-5bd7-4acc-9eb8-f9605985ef6b',
            'gh api -X PUT repos/jbookout/carr-system/rulesets/20824501',
            'production')
    returning id into v_id;

  if not exists (select 1 from ops.settings_change where id = v_id) then
    raise exception '0118 FAILED: a legal row was refused';
  end if;
  delete from ops.settings_change where id = v_id;

  raise notice '0118: a reasonless change is refused, an undefined outcome is refused, '
               'and a fully stated change is accepted';
end $$;
