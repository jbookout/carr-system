-- 0115_ops_observability.sql
-- PROGRAM 3, THE SUBSTRATE: correlation, runs, deployments, incidents, and the
-- one traceable view the phase gate asks for.
--
-- THE GATE, verbatim from the ordered implementation program: "one traceable
-- view explains a failed critical journey without terminal archaeology." The
-- executable form of that sentence was written BEFORE this file and lives at
-- ops/program3-trace-gate.py (rule e65efc68). Everything below exists to make
-- those five assertions pass.
--
-- WHAT WAS MISSING, verified 2026-08-14 by reading the tree rather than the
-- roadmap. Of Program 3's nine named elements, ONE was built:
--   correlation IDs        — absent from the Worker entirely; the only hits in
--                            the repo are a prototype's client-side JS and an
--                            unrelated hook.
--   structured logs        — out/nightly.log is append-only text on one Mac,
--                            written by a say() shell function.
--   metrics, traces        — absent.
--   synthetic golden flows — bin/smoke-and-record.sh exists and is one of the
--                            eight scripts the Program 0 inventory found with
--                            NO CALLER anywhere.
--   job-run ledger         — absent. bin/nightly.sh's step() wrapper already
--                            computes the right outcome per step, including a
--                            distinct SKIP code, and then throws it away into
--                            a text file. The 15 launchd jobs expose only a
--                            last exit status, and the 18 cloud scheduled tasks
--                            land nowhere queryable at all.
--   service/env catalog    — exists as PROSE (the Program 0 inventory) and as
--                            frozen JSON contracts. No rows anywhere.
--   deployment markers     — the one built element: /release reports env,
--                            git_sha, verb_count, schema ledger and doctrine
--                            generation, each with a reason when null. But it
--                            is a LIVE POLL, not a marker: nothing records a
--                            deployment when one happens, so there is no
--                            history to overlay and no way to ask what changed
--                            near the time of a failure.
--   incident records       — absent. The `defect` table (0103) is adjacent and
--                            is NOT this: it records claims the SYSTEM made and
--                            the truth they collided with. It has no severity,
--                            no service, no environment, no lifecycle and no
--                            separation of fact from hypothesis. Both are kept.
--
-- WHY ops AND NOT public. 0114 opened the ops schema and stated the argument:
-- one database, one store, a NAMESPACE that lets an ops-scoped role hold
-- operational tables without ever holding business tables. Every table here is
-- operational metadata about the machine, so every one of them belongs there.
-- No business payload lives in any of these tables — evidence is a REFERENCE.
--
-- THE ONE DESIGN DECISION THAT MATTERS MOST: HEALTH IS DERIVED, NEVER STORED.
-- The Control Room premortem names false health from stale collectors as the
-- second most likely catastrophic failure, and the risk register names the
-- proof: "Collector-off fixture renders Unknown and alert." A stored health
-- column is precisely the mechanism that fails that test — it keeps its last
-- value forever and a dead collector reads green. So there is no health column
-- in this migration. Health is a VIEW over the latest terminal observation and
-- its freshness, and when the observation has aged past its cadence the view
-- answers `unknown`. The last green result cannot survive its own staleness
-- because it is never written down as health in the first place.
--
-- ONE RUN TABLE, TWO KINDS. The frozen Phase 0 contract
-- (control-room/contracts/entity-schemas.v1.json) defines a single `Run` shape
-- and projects it to both CheckRunList and JobRunList. This follows that: one
-- ops.run with kind in ('job','check'), and a view per kind. Two tables with
-- one shape would be the maintain-two-versions choice rule d367188d rejects.

begin;

-- ── freshness, in one place ──────────────────────────────────────────────────
-- Every operational row in this schema answers the same question the same way,
-- so the answer is a function rather than four copies of a CASE expression.
-- The four states are the contract's own enum and each one is a DIFFERENT
-- statement: missing means nothing was ever observed; unknown means something
-- was observed and nobody declared when it goes off; stale means it aged out;
-- fresh means it is inside its window. Collapsing unknown into stale, or into
-- fresh, is how a system starts lying politely.
create or replace function ops.freshness(observed_at timestamptz, expires_at timestamptz)
returns text
language sql
stable
as $$
  select case
    when observed_at is null then 'missing'
    when expires_at  is null then 'unknown'
    when now() > expires_at  then 'stale'
    else 'fresh'
  end
$$;

comment on function ops.freshness(timestamptz, timestamptz) is
  'The four freshness states from control-room/contracts/entity-schemas.v1.json. '
  'unknown is NOT stale: it means the observation carried no expiry, so nobody '
  'can say. Never collapse the two.';

-- ── the service catalog ──────────────────────────────────────────────────────
create table if not exists ops.service (
  id            uuid primary key default gen_random_uuid(),
  key           text not null unique,   -- 'carr-mcp', 'nightly-record-layer'
  name          text not null,
  purpose       text,

  -- The families are the BDUF's own list (section 10.2). Free text rather than
  -- a check, because the list grows as services ship and a fixed vocabulary
  -- would force a new service into an old family on the day it is registered.
  family        text,

  criticality   text not null default 'medium'
    check (criticality in ('low','medium','high','critical')),
  owner_actor   text not null,

  repo_path     text,                   -- where its code lives, or null if outside the repo
  runtime       text,                   -- 'cloudflare-worker', 'launchd', 'cloud-session'
  runbook_ref   text,

  registered_at timestamptz not null default now(),
  retired_at    timestamptz,            -- a retired service is not deleted; nothing silently rots
  updated_at    timestamptz not null default now()
);

comment on table ops.service is
  'The service catalog as ROWS. It existed as prose in the Program 0 inventory '
  'and as frozen JSON contracts; a catalog nothing can query is a document.';

create table if not exists ops.service_dependency (
  service_id    uuid not null references ops.service(id) on delete cascade,
  depends_on_id uuid not null references ops.service(id) on delete cascade,
  note          text,
  primary key (service_id, depends_on_id),
  constraint a_service_does_not_depend_on_itself check (service_id <> depends_on_id)
);

comment on table ops.service_dependency is
  'Upstream/downstream blast radius. The BDUF requires every dependency graphic '
  'to have an accessible list equivalent; this table IS that list, and the '
  'graphic is derived from it rather than the other way round.';

create table if not exists ops.service_environment (
  service_id  uuid not null references ops.service(id) on delete cascade,
  environment text not null
    check (environment in ('local','rehearsal','staging','production')),
  primary key (service_id, environment),

  endpoint          text,
  deploy_mechanism  text,

  -- THE CADENCE IS WHAT MAKES SILENCE DETECTABLE. It is how long an observation
  -- stays believable when the collector itself does not declare an expiry. A
  -- service with no cadence is not scheduled, and its runs must carry their own
  -- expires_at or read unknown forever — which is the honest answer.
  expected_cadence_seconds int
    check (expected_cadence_seconds is null or expected_cadence_seconds > 0),

  -- Grace before a late observation is called stale. Weekly jobs on a weekday-
  -- only schedule need it; see tools/health-check.py, which learned this the
  -- expensive way with its weekend padding.
  cadence_grace_seconds    int not null default 0
    check (cadence_grace_seconds >= 0),

  notes       text,
  updated_at  timestamptz not null default now()
);

comment on column ops.service_environment.expected_cadence_seconds is
  'How long an observation of this service in this environment stays believable '
  'when the run does not declare its own expiry. NULL means unscheduled, which '
  'makes every observation unknown rather than fresh — deliberately.';

-- ── runs: jobs and synthetic golden-workflow checks ──────────────────────────
create table if not exists ops.run (
  id             uuid primary key default gen_random_uuid(),

  -- ONE ID CONNECTS THE WHOLE JOURNEY. The observability contract requires a
  -- single correlation ID across UI request, Worker route, record verb, database
  -- operation, agent session, approval, release and incident. It defaults to a
  -- fresh uuid rather than allowing null, so a collector that has not been
  -- taught to thread one still produces a row that can be pointed at — an
  -- unthreaded run is a chain of one, never a chain of none.
  correlation_id uuid not null default gen_random_uuid(),

  kind           text not null check (kind in ('job','check')),
  service_id     uuid not null references ops.service(id),
  environment    text not null
    check (environment in ('local','rehearsal','staging','production')),

  run_key        text not null,   -- 'nightly.exports', 'golden.record-lookup'

  state          text not null
    check (state in ('scheduled','queued','running','succeeded','failed',
                     'timed_out','cancelled','skipped','stale','unknown')),

  -- A FAILURE THAT CANNOT NAME ITS CLASS IS A SHRUG. The operational data model
  -- requires a failure class on every Run, and the whole value of a ledger is
  -- that the tenth occurrence of one class is visible as a pattern.
  failure_class  text,
  exit_code      int,
  attempt        int not null default 1 check (attempt >= 1),

  started_at     timestamptz,
  ended_at       timestamptz,
  duration_ms    int generated always as (
                   case when started_at is not null and ended_at is not null
                     then (extract(epoch from (ended_at - started_at)) * 1000)::int
                   end) stored,

  -- Provenance and freshness on the row itself. A state with no source is a
  -- number somebody typed.
  source_kind    text not null
    check (source_kind in ('collector','registry','wrapper','operator')),
  source_ref     text not null,          -- 'bin/nightly.sh', never a payload
  observed_at    timestamptz not null default now(),
  expires_at     timestamptz,

  evidence_ref   text,                   -- a POINTER to evidence, never its content
  detail         text,                   -- one redacted line; no secrets, no client content

  constraint a_failure_names_its_class check (
    state not in ('failed','timed_out') or failure_class is not null
  ),
  constraint a_terminal_run_has_ended check (
    state not in ('succeeded','failed','timed_out','cancelled','skipped')
      or ended_at is not null
  ),
  constraint a_run_that_ended_also_started check (
    ended_at is null or started_at is not null
  )
);

comment on table ops.run is
  'The job-run ledger and the synthetic golden-workflow ledger, one shape, as '
  'the frozen Run contract defines them. Holds NO business payload: evidence_ref '
  'points, detail is one redacted line.';

create index if not exists run_correlation_idx on ops.run (correlation_id);
create index if not exists run_service_env_observed_idx
  on ops.run (service_id, environment, observed_at desc);
create index if not exists run_open_idx on ops.run (state, observed_at desc)
  where state in ('scheduled','queued','running');

comment on index ops.run_open_idx is
  'Partial on purpose: terminal runs accumulate forever and the common question '
  'is what is in flight or stuck.';

create or replace view ops.v_job_run as
  select * from ops.run where kind = 'job';
create or replace view ops.v_check_run as
  select * from ops.run where kind = 'check';

-- ── deployment markers ───────────────────────────────────────────────────────
create table if not exists ops.deployment (
  id             uuid primary key default gen_random_uuid(),
  correlation_id uuid not null default gen_random_uuid(),

  service_id     uuid not null references ops.service(id),
  environment    text not null
    check (environment in ('local','rehearsal','staging','production')),

  -- The lifecycle from BDUF 16.2, side states included.
  state          text not null
    check (state in ('planned','rehearsing','ready','awaiting_approval','deploying',
                     'verifying','complete','failed','aborted','rolled_back','superseded')),

  git_sha        text,
  release_ref    text,
  deployed_by_actor text,

  -- What /release can say about this deploy, recorded AT the deploy rather than
  -- polled later. The endpoint answers for the deployment currently serving;
  -- these columns answer for a deployment that has since been replaced, which
  -- is the question a failure investigation actually asks.
  verb_count                 int,
  schema_highest_migration   text,
  doctrine_generation        int,

  started_at     timestamptz,
  ended_at       timestamptz,

  -- THE COMPLETION BAR, from the BDUF verbatim: "A successful deployment command
  -- without live verification remains Verifying or Failed, never Complete."
  -- Enforced here rather than trusted, because the failure it prevents is a
  -- deploy that reports success while serving the wrong thing — which is exactly
  -- what happened on 2026-08-14 when a staging deploy inherited the production
  -- domains and served api.doctorcre.com from an empty database.
  read_back_at   timestamptz,
  verification_evidence_ref text,

  failure_class  text,
  rollback_of    uuid references ops.deployment(id),

  source_kind    text not null
    check (source_kind in ('collector','registry','wrapper','operator')),
  source_ref     text not null,
  observed_at    timestamptz not null default now(),
  expires_at     timestamptz,
  detail         text,

  constraint complete_requires_a_production_read_back check (
    state <> 'complete' or read_back_at is not null
  ),
  constraint a_failed_deployment_names_its_class check (
    state <> 'failed' or failure_class is not null
  ),
  constraint a_terminal_deployment_has_ended check (
    state not in ('complete','failed','aborted','rolled_back','superseded')
      or ended_at is not null
  )
);

comment on table ops.deployment is
  'One attempt to place a release into one environment, RECORDED when it happens. '
  '/release answers what is serving now; this answers what was serving then, '
  'which is the question a failure investigation asks.';

create index if not exists deployment_correlation_idx on ops.deployment (correlation_id);
create index if not exists deployment_env_observed_idx
  on ops.deployment (environment, observed_at desc);

-- ── incidents ────────────────────────────────────────────────────────────────
create table if not exists ops.incident (
  id             uuid primary key default gen_random_uuid(),
  ref            text not null unique,
  correlation_id uuid not null default gen_random_uuid(),

  title          text not null,
  severity       text not null check (severity ~ '^SEV-[0-4]$'),
  state          text not null default 'detected'
    check (state in ('detected','triaged','investigating','mitigating',
                     'monitoring','resolved','reviewed')),
  environment    text not null
    check (environment in ('local','rehearsal','staging','production')),

  owner_actor    text,
  next_action    text,
  business_impact text,
  banner_state   text,

  detected_source text not null,
  detected_at     timestamptz not null default now(),

  -- Resolved requires recovery evidence AND a monitoring interval; Reviewed
  -- requires a follow-up disposition. Both are the doctrine's words, and both
  -- are the difference between an incident that taught something and one that
  -- was merely closed.
  monitoring_until       timestamptz,
  recovery_evidence_ref  text,
  resolved_at            timestamptz,
  root_cause             text,
  reviewed_at            timestamptz,
  followup_disposition   text,

  source_kind    text not null
    check (source_kind in ('collector','registry','wrapper','operator')),
  source_ref     text not null,
  observed_at    timestamptz not null default now(),
  expires_at     timestamptz,

  constraint resolved_requires_recovery_evidence_and_monitoring check (
    state not in ('resolved','reviewed')
      or (recovery_evidence_ref is not null and monitoring_until is not null
          and resolved_at is not null)
  ),
  constraint reviewed_requires_a_followup_disposition check (
    state <> 'reviewed' or (followup_disposition is not null and reviewed_at is not null)
  )
);

comment on table ops.incident is
  'The operational incident. NOT the same thing as public.defect, which records '
  'claims the system made and the truth they collided with — that table has no '
  'severity, service, environment or lifecycle, and is kept unchanged.';

create index if not exists incident_correlation_idx on ops.incident (correlation_id);
create index if not exists incident_open_idx on ops.incident (state, detected_at desc)
  where state <> 'reviewed';

create table if not exists ops.incident_service (
  incident_id uuid not null references ops.incident(id) on delete cascade,
  service_id  uuid not null references ops.service(id),
  primary key (incident_id, service_id)
);

-- FACTS AND HYPOTHESES ARE SEPARATE TABLES because the doctrine requires them
-- "stored and displayed separately". One table with a boolean is how a guess
-- becomes a fact during an outage: nobody re-reads the flag under pressure.
create table if not exists ops.incident_fact (
  id          uuid primary key default gen_random_uuid(),
  incident_id uuid not null references ops.incident(id) on delete cascade,
  text        text not null,
  source_ref  text not null,          -- a fact with no source is a hypothesis
  recorded_at timestamptz not null default now()
);

create table if not exists ops.incident_hypothesis (
  id          uuid primary key default gen_random_uuid(),
  incident_id uuid not null references ops.incident(id) on delete cascade,
  text        text not null,
  confidence  text not null
    check (confidence in ('unconfirmed','low','medium','high')),
  recorded_at timestamptz not null default now(),
  settled_as  text check (settled_as in ('confirmed','refuted'))
);

comment on table ops.incident_hypothesis is
  'Separate from incident_fact by doctrine. A hypothesis can be settled, and a '
  'settled one keeps its own row rather than being promoted into a fact — the '
  'history of what was believed during an outage is the review material.';

create table if not exists ops.incident_link (
  incident_id uuid not null references ops.incident(id) on delete cascade,
  kind        text not null check (kind in ('run','deployment','work_request','defect','decision')),
  ref         text not null,
  note        text,
  primary key (incident_id, kind, ref)
);

-- ── correlation reaches the work request ─────────────────────────────────────
-- 0114 opened ops.work_request before correlation existed. The chain the
-- observability contract describes ends at a work request, so the column goes
-- on here rather than in a later migration nobody remembers to write.
alter table ops.work_request
  add column if not exists correlation_id uuid;

comment on column ops.work_request.correlation_id is
  'Nullable on purpose: rows predate correlation, and a work request captured by '
  'hand has no journey behind it. Present when the request came out of a traced '
  'failure.';

-- ── THE TRACEABLE VIEW — the gate itself ─────────────────────────────────────
-- ONE query, ONE correlation id, the whole journey in time order. This is the
-- replacement for reading out/nightly.log over somebody's shoulder.
--
-- Every arm reports the same six things about itself: what kind of link it is,
-- what it refers to, what state it reached, when it happened, where the state
-- came from, and how old that is. An arm that cannot answer all six does not
-- belong in the view, because a chain is only as trustworthy as its least
-- explained link.
create or replace view ops.v_trace as
  select
    d.correlation_id,
    'deployment'::text                                    as kind,
    coalesce(d.release_ref, left(d.git_sha, 12), d.id::text) as ref,
    d.state,
    coalesce(d.ended_at, d.started_at, d.observed_at)      as occurred_at,
    d.environment,
    s.key                                                  as service_key,
    d.failure_class,
    d.detail,
    d.source_kind,
    d.source_ref,
    d.observed_at,
    d.expires_at,
    ops.freshness(d.observed_at, d.expires_at)             as freshness_state,
    d.id                                                   as row_id
  from ops.deployment d
  join ops.service s on s.id = d.service_id

  union all

  select
    r.correlation_id,
    r.kind,
    r.run_key,
    r.state,
    coalesce(r.ended_at, r.started_at, r.observed_at),
    r.environment,
    s.key,
    r.failure_class,
    r.detail,
    r.source_kind,
    r.source_ref,
    r.observed_at,
    r.expires_at,
    ops.freshness(r.observed_at, r.expires_at),
    r.id
  from ops.run r
  join ops.service s on s.id = r.service_id

  union all

  select
    i.correlation_id,
    'incident'::text,
    i.ref,
    i.state,
    i.detected_at,
    i.environment,
    null::text,
    null::text,
    i.title,
    i.source_kind,
    i.source_ref,
    i.observed_at,
    i.expires_at,
    ops.freshness(i.observed_at, i.expires_at),
    i.id
  from ops.incident i

  union all

  select
    w.correlation_id,
    'work_request'::text,
    w.ref,
    w.state,
    w.captured_at,
    null::text,
    null::text,
    w.blocker_code,
    w.title,
    'registry'::text,
    'ops.work_request'::text,
    w.updated_at,
    null::timestamptz,
    ops.freshness(w.updated_at, null),
    w.id
  from ops.work_request w
  where w.correlation_id is not null;

comment on view ops.v_trace is
  'THE PROGRAM 3 GATE. One correlation id returns the whole journey — deploy, '
  'golden-workflow check, job run, incident, work request — in time order, every '
  'link carrying its own source and freshness. Read-only by construction: a view '
  'over four tables with no insert rule.';

-- ── DERIVED HEALTH — the answer to false green ───────────────────────────────
-- There is no health column anywhere above. This view is the only place health
-- is expressed, and it is computed from the latest TERMINAL observation and that
-- observation's freshness. A collector that stops reporting cannot leave a green
-- value behind, because no green value was ever stored.
--
-- Non-terminal states are deliberately excluded from the health judgement: a job
-- that is currently running says nothing about whether the service is well, and
-- treating "running" as healthy is how a hung job reads green for six hours.
create or replace view ops.v_service_environment_health as
  with latest as (
    select distinct on (r.service_id, r.environment)
           r.service_id, r.environment, r.state, r.observed_at, r.expires_at,
           r.run_key, r.failure_class, r.source_kind, r.source_ref, r.correlation_id
      from ops.run r
     where r.state in ('succeeded','failed','timed_out','cancelled','skipped')
     order by r.service_id, r.environment, r.observed_at desc
  )
  select
    se.service_id,
    s.key                       as service_key,
    s.name                      as service_name,
    s.criticality,
    se.environment,
    l.run_key                   as last_run_key,
    l.state                     as last_run_state,
    l.failure_class             as last_failure_class,
    l.correlation_id            as last_correlation_id,
    l.observed_at,
    -- The effective expiry: what the run itself declared, or the cadence the
    -- environment was registered with. A service with neither can never be
    -- called fresh, and that is the honest outcome rather than a defect.
    coalesce(
      l.expires_at,
      l.observed_at + make_interval(secs =>
        se.expected_cadence_seconds + se.cadence_grace_seconds)
    )                           as expires_at,
    ops.freshness(
      l.observed_at,
      coalesce(l.expires_at,
               l.observed_at + make_interval(secs =>
                 se.expected_cadence_seconds + se.cadence_grace_seconds))
    )                           as freshness_state,
    coalesce(l.source_kind, 'registry') as source_kind,
    coalesce(l.source_ref, 'ops.service_environment') as source_ref,
    case
      -- Nothing was ever observed, or what was observed has aged out, or nobody
      -- said when it ages out. All three are unknown, and none of them is green.
      when l.state is null then 'unknown'
      when ops.freshness(
             l.observed_at,
             coalesce(l.expires_at,
                      l.observed_at + make_interval(secs =>
                        se.expected_cadence_seconds + se.cadence_grace_seconds))
           ) <> 'fresh' then 'unknown'
      when l.state in ('failed','timed_out')  then 'unavailable'
      when l.state in ('skipped','cancelled') then 'degraded'
      when l.state = 'succeeded'              then 'healthy'
      else 'unknown'
    end                         as health
  from ops.service_environment se
  join ops.service s on s.id = se.service_id
  left join latest l
    on l.service_id = se.service_id and l.environment = se.environment
  where s.retired_at is null;

comment on view ops.v_service_environment_health is
  'THE ONLY PLACE HEALTH IS EXPRESSED, and it is derived. The premortem names '
  'false health from stale collectors as the second most likely catastrophe; a '
  'stored health column is that failure mechanism. A silent collector reads '
  'unknown here because no green was ever written down.';

-- ── the roles the grants below need ──────────────────────────────────────────
-- FOUND WHILE APPLYING THIS FILE, 2026-08-14, and it is a real gap rather than a
-- detail of this migration. bin/schema-snapshot.sh dumps with --no-owner
-- --no-acl, so db/schema.sql carries STRUCTURE AND NO PRIVILEGES. Every
-- environment built from that snapshot — the isolated staging project and every
-- throwaway CI database — therefore has no carr_reader, no carr_writer and no
-- carr_jobs at all. This file's first apply to staging died on
-- `role "carr_reader" does not exist`, and any future migration carrying a grant
-- would have died the same way.
--
-- The consequence is larger than one failed apply: least privilege is currently
-- UNTESTABLE anywhere except production, because outside production the roles
-- the privileges attach to do not exist. Doctrine requires that "a fresh
-- non-production environment can be reconstructed from repository declarations"
-- (production-maturity-baseline s06), and a reconstruction missing its whole
-- authorization model does not satisfy that.
--
-- So the roles are declared here, in the repo, using 0021's idiom: an existing
-- role is left exactly as it is — no password touched, no attribute altered —
-- and a missing one is created NOLOGIN. NOLOGIN is the deliberate difference
-- from 0021: 0021 was minting a credential a human would then set a password
-- for, whereas these are being created only so that privileges have somewhere to
-- attach in a rebuilt environment. A role that cannot log in cannot become an
-- unnoticed door, and production is untouched because production already has all
-- three with their real attributes.
do $$
declare r text;
begin
  foreach r in array array['carr_reader','carr_writer','carr_jobs'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      raise notice '0115: role % already exists — left exactly as it is', r;
    else
      execute format('create role %I nologin', r);
      raise notice '0115: role % created NOLOGIN so privileges have somewhere to '
                   'attach in an environment rebuilt from db/schema.sql, which '
                   'carries no ACLs', r;
    end if;
  end loop;
end $$;

grant usage on schema ops to carr_reader, carr_writer, carr_jobs;

-- ── least privilege ──────────────────────────────────────────────────────────
-- The reader reads. The Control Room foundation this unblocks is READ-ONLY by
-- phase rule ("no mutation routes exist"), and the database half of that rule is
-- a role that structurally cannot write.
grant select on ops.service, ops.service_dependency, ops.service_environment,
                ops.run, ops.deployment, ops.incident, ops.incident_service,
                ops.incident_fact, ops.incident_hypothesis, ops.incident_link
  to carr_reader;
grant select on ops.v_trace, ops.v_service_environment_health,
                ops.v_job_run, ops.v_check_run
  to carr_reader;

grant select, insert, update on ops.service, ops.service_dependency,
                ops.service_environment, ops.deployment, ops.incident,
                ops.incident_service, ops.incident_fact, ops.incident_hypothesis,
                ops.incident_link
  to carr_writer;
grant select, insert on ops.run to carr_writer;
grant select on ops.v_trace, ops.v_service_environment_health,
                ops.v_job_run, ops.v_check_run
  to carr_writer;

-- The collectors run as carr_jobs. They append runs and they update nothing:
-- a ledger whose writer can rewrite history is not a ledger.
grant select, insert on ops.run to carr_jobs;
grant select on ops.service, ops.service_environment to carr_jobs;
grant insert on ops.deployment to carr_jobs;

commit;

-- ── proof, in the same run ───────────────────────────────────────────────────
-- Every invariant is PROVEN to bite. A constraint nobody has seen refuse
-- anything is a comment with punctuation (0114's rule, kept).
do $$
declare
  v_service uuid;
  v_state   text;
  v_health  text;
  v_fresh   text;
begin
  insert into ops.service (key, name, family, criticality, owner_actor)
    values ('migration-0115-proof', 'proof', 'Data', 'low', 'system')
    returning id into v_service;
  insert into ops.service_environment (service_id, environment, expected_cadence_seconds)
    values (v_service, 'local', 3600);

  -- 1. A failure must name its class.
  begin
    insert into ops.run (kind, service_id, environment, run_key, state,
                         started_at, ended_at, source_kind, source_ref)
      values ('job', v_service, 'local', 'p1', 'failed', now(), now(), 'wrapper', 'proof');
    raise exception '0115 FAILED: a failed run was accepted with no failure_class';
  exception when check_violation then null;
  end;

  -- 2. A terminal run must have ended.
  begin
    insert into ops.run (kind, service_id, environment, run_key, state,
                         started_at, source_kind, source_ref)
      values ('job', v_service, 'local', 'p2', 'succeeded', now(), 'wrapper', 'proof');
    raise exception '0115 FAILED: a terminal run was accepted with no ended_at';
  exception when check_violation then null;
  end;

  -- 3. complete requires a production read-back.
  begin
    insert into ops.deployment (service_id, environment, state, started_at, ended_at,
                                source_kind, source_ref)
      values (v_service, 'local', 'complete', now(), now(), 'wrapper', 'proof');
    raise exception '0115 FAILED: a deployment claimed complete with no read-back';
  exception when check_violation then null;
  end;

  -- 4. resolved requires recovery evidence and a monitoring interval.
  begin
    insert into ops.incident (ref, title, severity, state, environment,
                              detected_source, source_kind, source_ref)
      values ('INC-0115-PROOF-1', 'proof', 'SEV-3', 'resolved', 'local',
              'proof', 'operator', 'proof');
    raise exception '0115 FAILED: an incident resolved with no recovery evidence';
  exception when check_violation then null;
  end;

  -- 5. An undefined severity is refused.
  begin
    insert into ops.incident (ref, title, severity, state, environment,
                              detected_source, source_kind, source_ref)
      values ('INC-0115-PROOF-2', 'proof', 'CRITICAL', 'detected', 'local',
              'proof', 'operator', 'proof');
    raise exception '0115 FAILED: a severity outside SEV-0..4 was accepted';
  exception when check_violation then null;
  end;

  -- 6. THE CENTRAL ONE: a stale success does not read healthy.
  insert into ops.run (kind, service_id, environment, run_key, state,
                       started_at, ended_at, source_kind, source_ref,
                       observed_at, expires_at)
    values ('job', v_service, 'local', 'stale-proof', 'succeeded',
            now() - interval '2 days', now() - interval '2 days',
            'wrapper', 'proof',
            now() - interval '2 days', now() - interval '2 days' + interval '1 hour');

  select health, freshness_state into v_health, v_fresh
    from ops.v_service_environment_health
   where service_id = v_service and environment = 'local';

  if v_health is distinct from 'unknown' then
    raise exception '0115 FAILED: a two-day-old success reads health=% (expected unknown)', v_health;
  end if;
  if v_fresh is distinct from 'stale' then
    raise exception '0115 FAILED: a two-day-old observation reads freshness=% (expected stale)', v_fresh;
  end if;

  -- 7. And the control: a fresh success DOES read healthy, or the view is
  --    simply answering unknown to everything and proving nothing.
  insert into ops.run (kind, service_id, environment, run_key, state,
                       started_at, ended_at, source_kind, source_ref,
                       observed_at, expires_at)
    values ('job', v_service, 'local', 'fresh-proof', 'succeeded', now(), now(),
            'wrapper', 'proof', now(), now() + interval '1 hour');

  select health into v_health
    from ops.v_service_environment_health
   where service_id = v_service and environment = 'local';
  if v_health is distinct from 'healthy' then
    raise exception '0115 FAILED: a fresh success reads health=% (expected healthy)', v_health;
  end if;

  -- 8. The trace view returns the chain for one correlation id.
  select count(*)::text into v_state from ops.v_trace
   where correlation_id = (select correlation_id from ops.run
                            where service_id = v_service and run_key = 'fresh-proof');
  if v_state::int < 1 then
    raise exception '0115 FAILED: v_trace returned nothing for a run that exists';
  end if;

  delete from ops.run where service_id = v_service;
  delete from ops.service_environment where service_id = v_service;
  delete from ops.service where id = v_service;

  raise notice '0115: eight proofs passed — five invariants refused their violation, '
               'a stale success read unknown, a fresh success read healthy, and '
               'v_trace resolved a correlation id';
end $$;
