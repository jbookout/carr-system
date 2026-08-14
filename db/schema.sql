--
-- CARR ROLE PREAMBLE (bin/schema-snapshot.sh) — not produced by pg_dump.
--
-- This dump is --no-owner --no-acl, so it names no roles and grants nothing.
-- The roles still have to EXIST before the pending migrations that grant to
-- them run, and they can no longer be got by replaying 0115: once this
-- snapshot's ledger passed 0115 that migration stopped being pending anywhere.
-- carr_exporter aged into the same trap by way of 0006 and joined the list on
-- 2026-08-14, when the grants section below started carrying its privileges.
-- An existing role is left exactly as it is; a missing one is created NOLOGIN
-- purely so privileges have somewhere to attach in a rebuilt environment.
--
do $$
declare r text;
begin
  foreach r in array array['carr_reader','carr_writer','carr_jobs','carr_exporter'] loop
    if not exists (select 1 from pg_roles where rolname = r) then
      execute format('create role %I nologin', r);
    end if;
  end loop;
end $$;

--
-- PostgreSQL database dump
--



SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: neon_auth; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA neon_auth;


--
-- Name: ops; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA ops;


--
-- Name: SCHEMA ops; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA ops IS 'Operational metadata for the Control Room: work requests, and later plans, approvals, releases and job runs. Separated from public so an ops-scoped role can be granted operational tables without ever being granted business tables (decision a4a299fb). Never stores secrets, DSNs, raw transcripts, business payloads or unrestricted logs — references to business records only.';


--
-- Name: pg_trgm; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;


--
-- Name: EXTENSION pg_trgm; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pg_trgm IS 'text similarity measurement and index searching based on trigrams';


--
-- Name: freshness(timestamp with time zone, timestamp with time zone); Type: FUNCTION; Schema: ops; Owner: -
--

CREATE FUNCTION ops.freshness(observed_at timestamp with time zone, expires_at timestamp with time zone) RETURNS text
    LANGUAGE sql STABLE
    AS $$
  select case
    when observed_at is null then 'missing'
    when expires_at  is null then 'unknown'
    when now() > expires_at  then 'stale'
    else 'fresh'
  end
$$;


--
-- Name: FUNCTION freshness(observed_at timestamp with time zone, expires_at timestamp with time zone); Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON FUNCTION ops.freshness(observed_at timestamp with time zone, expires_at timestamp with time zone) IS 'The four freshness states from control-room/contracts/entity-schemas.v1.json. unknown is NOT stale: it means the observation carried no expiry, so nobody can say. Never collapse the two.';


--
-- Name: assert_no_orphaned_edges(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.assert_no_orphaned_edges() RETURNS void
    LANGUAGE plpgsql
    AS $$
declare n int; worst text;
begin
  select count(*), min(edge || ' -> ' || coalesce(party_ref, '?') || ' ' || coalesce(name, ''))
    into n, worst from v_orphaned_edge;
  if n > 0 then
    raise exception 'v_orphaned_edge is not empty: % edge(s) point at a merged or deleted '
                    'party (e.g. %). Repoint or retire them before merging anything else — a '
                    'merge on top of a stale edge produces a graph that walks to a party which '
                    'no longer resolves.', n, worst;
  end if;
end
$$;


--
-- Name: FUNCTION assert_no_orphaned_edges(); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.assert_no_orphaned_edges() IS 'Raises unless v_orphaned_edge is empty (0062). Call it from a migration guard alongside the v_orphaned_role check, and from any future merge path, so the survivorship rule''s mandatory sweep is enforced rather than remembered. Companion to assert_view_disjoint (0058): the same trade — a paragraph claiming an invariant becomes a call that fails.';


--
-- Name: assert_view_disjoint(text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.assert_view_disjoint(source text, key_expr text) RETURNS void
    LANGUAGE plpgsql
    AS $_$
declare
  dup_keys bigint;
  surplus  bigint;
  worst    text;
begin
  -- A bare identifier must resolve to a real relation. A parenthesised expression is taken
  -- as a caller-supplied subquery and must carry its own alias, which is a Postgres rule
  -- rather than one invented here.
  if source !~ '^\s*\(' and to_regclass(source) is null then
    raise exception 'assert_view_disjoint: no such relation %', source;
  end if;

  -- One pass: how many distinct key values repeat, how many surplus rows that is (the actual
  -- size of the inflation), and one sample to name in the error.
  execute format(
    $q$ select count(*), coalesce(sum(d.n - 1), 0), coalesce(min(d.k), '(null key)')
          from (select (%s)::text as k, count(*) as n
                  from %s
                 group by 1
                having count(*) > 1) d $q$,
    key_expr, source)
    into dup_keys, surplus, worst;

  if dup_keys > 0 then
    raise exception '% is NOT disjoint on %: % key(s) appear more than once, % surplus '
                    'row(s) (e.g. %). A union all branch overlaps another, so every consumer '
                    'of this view is counting something twice.',
                    source, key_expr, dup_keys, surplus, worst;
  end if;
end
$_$;


--
-- Name: FUNCTION assert_view_disjoint(source text, key_expr text); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.assert_view_disjoint(source text, key_expr text) IS 'Raises unless every key value in `source` (a relation name, or a parenthesised subquery with an alias) appears exactly once — 0058. Call it from a migration''s guard block whenever you add or change a `union all` branch, INSTEAD of writing a paragraph arguing the branches cannot overlap. 0056 wrote the paragraph and the paragraph was right; the next one may not be, and an inflated denominator is invisible. Migration-time only: it does a full scan and belongs nowhere near a hot path. Null key values are grouped and reported like any other, never skipped, so key on something non-null in every branch.';


--
-- Name: campaign_channels_valid(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.campaign_channels_valid() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
declare bad text;
begin
  select string_agg(ch, ', ') into bad
    from unnest(new.channels) ch
   where ch not in (select slug from marketing_subject
                     where subject_type = 'platform' and retired_at is null);
  if bad is not null then
    raise exception 'campaign.channels holds unknown platform slug(s): %. Known live '
                    'platforms: %. Register a new platform in marketing_subject before '
                    'naming it here.', bad,
                    (select string_agg(slug, ', ' order by slug) from marketing_subject
                      where subject_type = 'platform' and retired_at is null);
  end if;
  return new;
end $$;


--
-- Name: capture_call_context(uuid[]); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.capture_call_context(requested_deal_ids uuid[]) RETURNS TABLE(deal_id uuid, deal_name text, owner text, operating_state text, participant_party_id uuid, participant_party_ref text, participant_name text, participant_email text, participant_role text)
    LANGUAGE sql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public'
    AS $$
  select d.id, d.name, d.owner, d.operating_state, dp.party_id, p.ref, p.name, p.email, dp.role
    from public.deal d
    left join public.deal_participant dp on dp.deal_id=d.id and dp.to_at is null
    left join public.party p on p.id=dp.party_id
   where d.id = any(requested_deal_ids)
     and d.outcome is null
     and d.operating_state = 'active'
$$;


--
-- Name: carr_business_days(date, date); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.carr_business_days(a date, b date) RETURNS integer
    LANGUAGE sql IMMUTABLE
    AS $$
  select case when a is null or b is null then null
         else (case when b >= a then 1 else -1 end) * (
           select count(*)::int
             from generate_series(least(a,b), greatest(a,b) - 1, interval '1 day') d
            where extract(isodow from d) < 6)
         end;
$$;


--
-- Name: FUNCTION carr_business_days(a date, b date); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.carr_business_days(a date, b date) IS 'Whole business days from a to b, negative when a is later than b. Weekends excluded per rule 236ca227; holidays deliberately not modelled, because CARR holds no holiday calendar and a guessed one would be a second source of truth. THE one business-day primitive: route human obligation aging, staleness and overdue display through this rather than raw date subtraction, and leave machine liveness cadences in elapsed time where a weekend is still a weekend.';


--
-- Name: org_identity_key(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.org_identity_key(p_name text) RETURNS text
    LANGUAGE sql IMMUTABLE
    AS $$
  select case
    when p_name is null                then null
    when btrim(p_name) = ''            then null
    when btrim(p_name) like '(%'       then null   -- '(TBD)', '(new practice, relocating…)'
    when p_name ~* '\mtbd\M'
      or p_name ~* '\munknown\M'
      or p_name ~* '\mn/a\M'           then null   -- 'Startup dental practice (entity name TBD)'
    else lower(regexp_replace(btrim(p_name), '\s+', ' ', 'g'))
  end
$$;


--
-- Name: FUNCTION org_identity_key(p_name text); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.org_identity_key(p_name text) IS 'The identity of an ORGANISATION, normalised (0059): trim, collapse whitespace, lowercase, and NULL for a placeholder. Null means "this string names no organisation" — six rows are literally called "(TBD — enrich)" and collapsing them would assert that six unrelated people share an employer, which is a fabricated fact. Null keys are excluded from the merge and from party_org_identity_uniq, so placeholders stay separate and new ones stay legal. Deliberately does NOT strip legal suffixes or parentheticals: "Carr Riggs Ingram" and "Carr Riggs Ingram (advisory)" are different rows on purpose. The enrichment step that wants domain as an exact match key should EXTEND this function rather than invent a second normalisation rule.';


--
-- Name: org_party_id(text, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.org_party_id(p_name text, p_actor uuid) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
declare k text; found uuid;
begin
  k := org_identity_key(p_name);

  -- A placeholder has no identity, so it gets a fresh row exactly as today. Six unknowns are
  -- six unknowns, not one company.
  if k is null then
    insert into party (kind, name, created_by, updated_by)
         values ('org', p_name, p_actor, p_actor) returning id into found;
    return found;
  end if;

  select id into found from party
   where kind = 'org' and merged_into is null and deleted_at is null
     and org_identity_key(name) = k
   limit 1;
  if found is not null then return found; end if;

  begin
    insert into party (kind, name, created_by, updated_by)
         values ('org', btrim(p_name), p_actor, p_actor) returning id into found;
  exception when unique_violation then
    -- Two sessions created the same organisation at once. The index decided; re-read.
    select id into found from party
     where kind = 'org' and merged_into is null and deleted_at is null
       and org_identity_key(name) = k
     limit 1;
  end;
  return found;
end
$$;


--
-- Name: FUNCTION org_party_id(p_name text, p_actor uuid); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.org_party_id(p_name text, p_actor uuid) IS 'Find-or-create an organisation party by normalised name, atomically (0059). Replaces the blind `insert into party ... values (''org'', $1, ...)` at mcp-server/src/tools.js:1156 (add-party) and :1281 (add-premises), which is what minted 17 Henry Schein rows. Race-safe against party_org_identity_uniq: the loser of a concurrent insert re-reads rather than failing. A placeholder name still mints a fresh row, on purpose.';


--
-- Name: state_as_of(text, uuid, timestamp with time zone); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.state_as_of(p_type text, p_id uuid, p_at timestamp with time zone) RETURNS TABLE(field text, value_at jsonb, changed_at timestamp with time zone, changed_by text, verb text, later_changes integer, current_value jsonb)
    LANGUAGE sql STABLE
    AS $$
  with hist as (
    select h.field, h.new_value, h.occurred_at, h.actor, h.verb
      from v_field_history h
     where h.subject_type = p_type and h.subject_id = p_id
  ), at_time as (
    select distinct on (field) field, new_value, occurred_at, actor, verb
      from hist where occurred_at <= p_at
     order by field, occurred_at desc
  ), now_val as (
    select distinct on (field) field, new_value
      from hist order by field, occurred_at desc
  )
  select a.field, a.new_value, a.occurred_at, a.actor, a.verb,
         -- THE COLUMN THAT MAKES THE ANSWER SAFE TO ACT ON. A value with later changes is a
         -- historical value; one with none is still current. Without this a caller cannot
         -- tell "this was true then and still is" from "this was true then and has moved",
         -- which is the entire question rule 3fa17fa0 makes a session stop and ask about.
         (select count(*)::int from hist h
           where h.field = a.field and h.occurred_at > p_at) as later_changes,
         n.new_value
    from at_time a left join now_val n on n.field = a.field
   order by a.field;
$$;


--
-- Name: FUNCTION state_as_of(p_type text, p_id uuid, p_at timestamp with time zone); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.state_as_of(p_type text, p_id uuid, p_at timestamp with time zone) IS 'What the record said about one subject at one instant (0106, loop #281). Returns the newest value of each field at or before the cutoff, plus later_changes and the current value beside it — because "this was true then and still is" and "this was true then and has since moved" are different answers and a caller must never have to guess which it holds.';


--
-- Name: trg_availability_norm(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trg_availability_norm() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
  if new.rate_amount is not null
     and new.rate_basis in ('usd_mo_gross','usd_yr_gross')
     and new.rate_norm_gross_sf_yr is null then
    new.norm_owed := true;                       -- [A5] normalized-or-owed, enforced
  end if;
  return new;
end $$;


--
-- Name: trg_deal_participant_side(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trg_deal_participant_side() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
declare s text;
begin
  select side into s from participant_role where slug = new.role;

  if s = 'actor' then
    if new.actor_id is null then
      raise exception 'deal_participant.role=% is an ACTOR-side role (a CARR employee) and '
                      'needs actor_id. See participant_role.side.', new.role;
    end if;
    if new.party_id is not null then
      raise exception 'deal_participant.role=% is an ACTOR-side role and must NOT carry '
                      'party_id. role=''lead'' means the deal''s OWNING AGENT (joe or dell), '
                      'not the client — v_deal_board reads lead_owner off actor_id and never '
                      'looks at party_id. Writing the client''s party here makes one row '
                      'assert two different owners. If you want the client''s person on the '
                      'deal, that is role=''client_contact'', or just follow '
                      'deal -> client -> client.party_id, which is already exact and 1:1.',
                      new.role;
    end if;

  elsif s = 'party' then
    if new.party_id is null then
      raise exception 'deal_participant.role=% is a PARTY-side role (someone outside CARR) '
                      'and needs party_id. See participant_role.side.', new.role;
    end if;
    if new.actor_id is not null then
      raise exception 'deal_participant.role=% is a PARTY-side role and must NOT carry '
                      'actor_id — an actor is a CARR employee and this role is a '
                      'counterparty.', new.role;
    end if;
  end if;

  return new;
end
$$;


--
-- Name: FUNCTION trg_deal_participant_side(); Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON FUNCTION public.trg_deal_participant_side() IS 'Enforces participant_role.side on deal_participant (0060): an actor-side role carries actor_id and never party_id, a party-side role the reverse, a role with a null side is left alone. Deliberately a trigger and not a CHECK: a CHECK cannot read participant_role, so it would have to hardcode the role names and would silently fail to cover any role added later. Here the vocabulary IS the rule, so declaring a new role''s side is all that is needed to constrain it.';


--
-- Name: trg_touch_row(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.trg_touch_row() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
  new.updated_at := now();
  new.version    := old.version + 1;
  return new;
end $$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: account; Type: TABLE; Schema: neon_auth; Owner: -
--

CREATE TABLE neon_auth.account (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    "accountId" text NOT NULL,
    "providerId" text NOT NULL,
    "userId" uuid NOT NULL,
    "accessToken" text,
    "refreshToken" text,
    "idToken" text,
    "accessTokenExpiresAt" timestamp with time zone,
    "refreshTokenExpiresAt" timestamp with time zone,
    scope text,
    password text,
    "createdAt" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updatedAt" timestamp with time zone NOT NULL
);


--
-- Name: invitation; Type: TABLE; Schema: neon_auth; Owner: -
--

CREATE TABLE neon_auth.invitation (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    "organizationId" uuid NOT NULL,
    email text NOT NULL,
    role text,
    status text NOT NULL,
    "expiresAt" timestamp with time zone NOT NULL,
    "createdAt" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "inviterId" uuid NOT NULL
);


--
-- Name: jwks; Type: TABLE; Schema: neon_auth; Owner: -
--

CREATE TABLE neon_auth.jwks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    "publicKey" text NOT NULL,
    "privateKey" text NOT NULL,
    "createdAt" timestamp with time zone NOT NULL,
    "expiresAt" timestamp with time zone
);


--
-- Name: member; Type: TABLE; Schema: neon_auth; Owner: -
--

CREATE TABLE neon_auth.member (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    "organizationId" uuid NOT NULL,
    "userId" uuid NOT NULL,
    role text NOT NULL,
    "createdAt" timestamp with time zone NOT NULL
);


--
-- Name: organization; Type: TABLE; Schema: neon_auth; Owner: -
--

CREATE TABLE neon_auth.organization (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    slug text NOT NULL,
    logo text,
    "createdAt" timestamp with time zone NOT NULL,
    metadata text
);


--
-- Name: project_config; Type: TABLE; Schema: neon_auth; Owner: -
--

CREATE TABLE neon_auth.project_config (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    endpoint_id text NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    trusted_origins jsonb NOT NULL,
    social_providers jsonb NOT NULL,
    email_provider jsonb,
    email_and_password jsonb,
    allow_localhost boolean NOT NULL,
    plugin_configs jsonb,
    webhook_config jsonb
);


--
-- Name: session; Type: TABLE; Schema: neon_auth; Owner: -
--

CREATE TABLE neon_auth.session (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    "expiresAt" timestamp with time zone NOT NULL,
    token text NOT NULL,
    "createdAt" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updatedAt" timestamp with time zone NOT NULL,
    "ipAddress" text,
    "userAgent" text,
    "userId" uuid NOT NULL,
    "impersonatedBy" text,
    "activeOrganizationId" text
);


--
-- Name: user; Type: TABLE; Schema: neon_auth; Owner: -
--

CREATE TABLE neon_auth."user" (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    email text NOT NULL,
    "emailVerified" boolean NOT NULL,
    image text,
    "createdAt" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updatedAt" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    role text,
    banned boolean,
    "banReason" text,
    "banExpires" timestamp with time zone
);


--
-- Name: verification; Type: TABLE; Schema: neon_auth; Owner: -
--

CREATE TABLE neon_auth.verification (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    identifier text NOT NULL,
    value text NOT NULL,
    "expiresAt" timestamp with time zone NOT NULL,
    "createdAt" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    "updatedAt" timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: deployment; Type: TABLE; Schema: ops; Owner: -
--

CREATE TABLE ops.deployment (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    correlation_id uuid DEFAULT gen_random_uuid() NOT NULL,
    service_id uuid NOT NULL,
    environment text NOT NULL,
    state text NOT NULL,
    git_sha text,
    release_ref text,
    deployed_by_actor text,
    verb_count integer,
    schema_highest_migration text,
    doctrine_generation integer,
    started_at timestamp with time zone,
    ended_at timestamp with time zone,
    read_back_at timestamp with time zone,
    verification_evidence_ref text,
    failure_class text,
    rollback_of uuid,
    source_kind text NOT NULL,
    source_ref text NOT NULL,
    observed_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone,
    detail text,
    CONSTRAINT a_failed_deployment_names_its_class CHECK (((state <> 'failed'::text) OR (failure_class IS NOT NULL))),
    CONSTRAINT a_terminal_deployment_has_ended CHECK (((state <> ALL (ARRAY['complete'::text, 'failed'::text, 'aborted'::text, 'rolled_back'::text, 'superseded'::text])) OR (ended_at IS NOT NULL))),
    CONSTRAINT complete_requires_a_production_read_back CHECK (((state <> 'complete'::text) OR (read_back_at IS NOT NULL))),
    CONSTRAINT deployment_environment_check CHECK ((environment = ANY (ARRAY['local'::text, 'rehearsal'::text, 'staging'::text, 'production'::text]))),
    CONSTRAINT deployment_source_kind_check CHECK ((source_kind = ANY (ARRAY['collector'::text, 'registry'::text, 'wrapper'::text, 'operator'::text]))),
    CONSTRAINT deployment_state_check CHECK ((state = ANY (ARRAY['planned'::text, 'rehearsing'::text, 'ready'::text, 'awaiting_approval'::text, 'deploying'::text, 'verifying'::text, 'complete'::text, 'failed'::text, 'aborted'::text, 'rolled_back'::text, 'superseded'::text])))
);


--
-- Name: TABLE deployment; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON TABLE ops.deployment IS 'One attempt to place a release into one environment, RECORDED when it happens. /release answers what is serving now; this answers what was serving then, which is the question a failure investigation asks.';


--
-- Name: incident; Type: TABLE; Schema: ops; Owner: -
--

CREATE TABLE ops.incident (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ref text NOT NULL,
    correlation_id uuid DEFAULT gen_random_uuid() NOT NULL,
    title text NOT NULL,
    severity text NOT NULL,
    state text DEFAULT 'detected'::text NOT NULL,
    environment text NOT NULL,
    owner_actor text,
    next_action text,
    business_impact text,
    banner_state text,
    detected_source text NOT NULL,
    detected_at timestamp with time zone DEFAULT now() NOT NULL,
    monitoring_until timestamp with time zone,
    recovery_evidence_ref text,
    resolved_at timestamp with time zone,
    root_cause text,
    reviewed_at timestamp with time zone,
    followup_disposition text,
    source_kind text NOT NULL,
    source_ref text NOT NULL,
    observed_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone,
    signature text,
    CONSTRAINT incident_environment_check CHECK ((environment = ANY (ARRAY['local'::text, 'rehearsal'::text, 'staging'::text, 'production'::text]))),
    CONSTRAINT incident_severity_check CHECK ((severity ~ '^SEV-[0-4]$'::text)),
    CONSTRAINT incident_source_kind_check CHECK ((source_kind = ANY (ARRAY['collector'::text, 'registry'::text, 'wrapper'::text, 'operator'::text]))),
    CONSTRAINT incident_state_check CHECK ((state = ANY (ARRAY['detected'::text, 'triaged'::text, 'investigating'::text, 'mitigating'::text, 'monitoring'::text, 'resolved'::text, 'reviewed'::text]))),
    CONSTRAINT resolved_requires_recovery_evidence_and_monitoring CHECK (((state <> ALL (ARRAY['resolved'::text, 'reviewed'::text])) OR ((recovery_evidence_ref IS NOT NULL) AND (monitoring_until IS NOT NULL) AND (resolved_at IS NOT NULL)))),
    CONSTRAINT reviewed_requires_a_followup_disposition CHECK (((state <> 'reviewed'::text) OR ((followup_disposition IS NOT NULL) AND (reviewed_at IS NOT NULL))))
);


--
-- Name: TABLE incident; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON TABLE ops.incident IS 'The operational incident. NOT the same thing as public.defect, which records claims the system made and the truth they collided with — that table has no severity, service, environment or lifecycle, and is kept unchanged.';


--
-- Name: COLUMN incident.signature; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON COLUMN ops.incident.signature IS 'service|environment|run_key|failure_class — the identity of a recurring failure. Two OPEN incidents cannot share one (see the partial unique index). Null is allowed for incidents a human opens by hand, which have no automatic recurrence to collapse.';


--
-- Name: incident_fact; Type: TABLE; Schema: ops; Owner: -
--

CREATE TABLE ops.incident_fact (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    incident_id uuid NOT NULL,
    text text NOT NULL,
    source_ref text NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: incident_hypothesis; Type: TABLE; Schema: ops; Owner: -
--

CREATE TABLE ops.incident_hypothesis (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    incident_id uuid NOT NULL,
    text text NOT NULL,
    confidence text NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    settled_as text,
    CONSTRAINT incident_hypothesis_confidence_check CHECK ((confidence = ANY (ARRAY['unconfirmed'::text, 'low'::text, 'medium'::text, 'high'::text]))),
    CONSTRAINT incident_hypothesis_settled_as_check CHECK ((settled_as = ANY (ARRAY['confirmed'::text, 'refuted'::text])))
);


--
-- Name: TABLE incident_hypothesis; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON TABLE ops.incident_hypothesis IS 'Separate from incident_fact by doctrine. A hypothesis can be settled, and a settled one keeps its own row rather than being promoted into a fact — the history of what was believed during an outage is the review material.';


--
-- Name: incident_link; Type: TABLE; Schema: ops; Owner: -
--

CREATE TABLE ops.incident_link (
    incident_id uuid NOT NULL,
    kind text NOT NULL,
    ref text NOT NULL,
    note text,
    CONSTRAINT incident_link_kind_check CHECK ((kind = ANY (ARRAY['run'::text, 'deployment'::text, 'work_request'::text, 'defect'::text, 'decision'::text])))
);


--
-- Name: incident_service; Type: TABLE; Schema: ops; Owner: -
--

CREATE TABLE ops.incident_service (
    incident_id uuid NOT NULL,
    service_id uuid NOT NULL
);


--
-- Name: run; Type: TABLE; Schema: ops; Owner: -
--

CREATE TABLE ops.run (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    correlation_id uuid DEFAULT gen_random_uuid() NOT NULL,
    kind text NOT NULL,
    service_id uuid NOT NULL,
    environment text NOT NULL,
    run_key text NOT NULL,
    state text NOT NULL,
    failure_class text,
    exit_code integer,
    attempt integer DEFAULT 1 NOT NULL,
    started_at timestamp with time zone,
    ended_at timestamp with time zone,
    duration_ms integer GENERATED ALWAYS AS (
CASE
    WHEN ((started_at IS NOT NULL) AND (ended_at IS NOT NULL)) THEN ((EXTRACT(epoch FROM (ended_at - started_at)) * (1000)::numeric))::integer
    ELSE NULL::integer
END) STORED,
    source_kind text NOT NULL,
    source_ref text NOT NULL,
    observed_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone,
    evidence_ref text,
    detail text,
    CONSTRAINT a_failure_names_its_class CHECK (((state <> ALL (ARRAY['failed'::text, 'timed_out'::text])) OR (failure_class IS NOT NULL))),
    CONSTRAINT a_run_that_ended_also_started CHECK (((ended_at IS NULL) OR (started_at IS NOT NULL))),
    CONSTRAINT a_terminal_run_has_ended CHECK (((state <> ALL (ARRAY['succeeded'::text, 'failed'::text, 'timed_out'::text, 'cancelled'::text, 'skipped'::text])) OR (ended_at IS NOT NULL))),
    CONSTRAINT run_attempt_check CHECK ((attempt >= 1)),
    CONSTRAINT run_environment_check CHECK ((environment = ANY (ARRAY['local'::text, 'rehearsal'::text, 'staging'::text, 'production'::text]))),
    CONSTRAINT run_kind_check CHECK ((kind = ANY (ARRAY['job'::text, 'check'::text]))),
    CONSTRAINT run_source_kind_check CHECK ((source_kind = ANY (ARRAY['collector'::text, 'registry'::text, 'wrapper'::text, 'operator'::text]))),
    CONSTRAINT run_state_check CHECK ((state = ANY (ARRAY['scheduled'::text, 'queued'::text, 'running'::text, 'succeeded'::text, 'failed'::text, 'timed_out'::text, 'cancelled'::text, 'skipped'::text, 'stale'::text, 'unknown'::text])))
);


--
-- Name: TABLE run; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON TABLE ops.run IS 'The job-run ledger and the synthetic golden-workflow ledger, one shape, as the frozen Run contract defines them. Holds NO business payload: evidence_ref points, detail is one redacted line.';


--
-- Name: service; Type: TABLE; Schema: ops; Owner: -
--

CREATE TABLE ops.service (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    key text NOT NULL,
    name text NOT NULL,
    purpose text,
    family text,
    criticality text DEFAULT 'medium'::text NOT NULL,
    owner_actor text NOT NULL,
    repo_path text,
    runtime text,
    runbook_ref text,
    registered_at timestamp with time zone DEFAULT now() NOT NULL,
    retired_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT service_criticality_check CHECK ((criticality = ANY (ARRAY['low'::text, 'medium'::text, 'high'::text, 'critical'::text])))
);


--
-- Name: TABLE service; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON TABLE ops.service IS 'The service catalog as ROWS. It existed as prose in the Program 0 inventory and as frozen JSON contracts; a catalog nothing can query is a document.';


--
-- Name: service_dependency; Type: TABLE; Schema: ops; Owner: -
--

CREATE TABLE ops.service_dependency (
    service_id uuid NOT NULL,
    depends_on_id uuid NOT NULL,
    note text,
    CONSTRAINT a_service_does_not_depend_on_itself CHECK ((service_id <> depends_on_id))
);


--
-- Name: TABLE service_dependency; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON TABLE ops.service_dependency IS 'Upstream/downstream blast radius. The BDUF requires every dependency graphic to have an accessible list equivalent; this table IS that list, and the graphic is derived from it rather than the other way round.';


--
-- Name: service_environment; Type: TABLE; Schema: ops; Owner: -
--

CREATE TABLE ops.service_environment (
    service_id uuid NOT NULL,
    environment text NOT NULL,
    endpoint text,
    deploy_mechanism text,
    expected_cadence_seconds integer,
    cadence_grace_seconds integer DEFAULT 0 NOT NULL,
    notes text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT service_environment_cadence_grace_seconds_check CHECK ((cadence_grace_seconds >= 0)),
    CONSTRAINT service_environment_environment_check CHECK ((environment = ANY (ARRAY['local'::text, 'rehearsal'::text, 'staging'::text, 'production'::text]))),
    CONSTRAINT service_environment_expected_cadence_seconds_check CHECK (((expected_cadence_seconds IS NULL) OR (expected_cadence_seconds > 0)))
);


--
-- Name: COLUMN service_environment.expected_cadence_seconds; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON COLUMN ops.service_environment.expected_cadence_seconds IS 'How long an observation of this service in this environment stays believable when the run does not declare its own expiry. NULL means unscheduled, which makes every observation unknown rather than fresh — deliberately.';


--
-- Name: settings_change; Type: TABLE; Schema: ops; Owner: -
--

CREATE TABLE ops.settings_change (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    kind text NOT NULL,
    target text NOT NULL,
    reason text NOT NULL,
    outcome text NOT NULL,
    session_id text NOT NULL,
    actor text,
    command text,
    environment text,
    correlation_id uuid DEFAULT gen_random_uuid() NOT NULL,
    CONSTRAINT a_reason_has_to_say_something CHECK ((length(btrim(reason)) >= 8)),
    CONSTRAINT settings_change_environment_check CHECK (((environment IS NULL) OR (environment = ANY (ARRAY['local'::text, 'rehearsal'::text, 'staging'::text, 'production'::text])))),
    CONSTRAINT settings_change_outcome_check CHECK ((outcome = ANY (ARRAY['applied'::text, 'failed'::text])))
);


--
-- Name: TABLE settings_change; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON TABLE ops.settings_change IS 'Every change to a control plane this system does not own — GitHub rulesets, Actions variables and secrets, branch protection, launchd jobs, git config, Worker secrets. Written by hooks/settings-change-gate.py AT THE MOMENT OF THE CHANGE, because a record that waits for the session to finish dies with it.';


--
-- Name: COLUMN settings_change.reason; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON COLUMN ops.settings_change.reason IS 'Why the change was made, in the words of whoever made it. The 2026-08-14 ruleset incident was not a missing change log — it was a missing REASON: the change itself was discoverable from GitHub''s own version history within minutes, and still nobody could tell an authorised change from tampering.';


--
-- Name: v_check_run; Type: VIEW; Schema: ops; Owner: -
--

CREATE VIEW ops.v_check_run AS
 SELECT id,
    correlation_id,
    kind,
    service_id,
    environment,
    run_key,
    state,
    failure_class,
    exit_code,
    attempt,
    started_at,
    ended_at,
    duration_ms,
    source_kind,
    source_ref,
    observed_at,
    expires_at,
    evidence_ref,
    detail
   FROM ops.run
  WHERE (kind = 'check'::text);


--
-- Name: v_job_run; Type: VIEW; Schema: ops; Owner: -
--

CREATE VIEW ops.v_job_run AS
 SELECT id,
    correlation_id,
    kind,
    service_id,
    environment,
    run_key,
    state,
    failure_class,
    exit_code,
    attempt,
    started_at,
    ended_at,
    duration_ms,
    source_kind,
    source_ref,
    observed_at,
    expires_at,
    evidence_ref,
    detail
   FROM ops.run
  WHERE (kind = 'job'::text);


--
-- Name: v_service_environment_health; Type: VIEW; Schema: ops; Owner: -
--

CREATE VIEW ops.v_service_environment_health AS
 WITH latest AS (
         SELECT DISTINCT ON (r.service_id, r.environment) r.service_id,
            r.environment,
            r.state,
            r.observed_at,
            r.expires_at,
            r.run_key,
            r.failure_class,
            r.source_kind,
            r.source_ref,
            r.correlation_id
           FROM ops.run r
          WHERE (r.state = ANY (ARRAY['succeeded'::text, 'failed'::text, 'timed_out'::text, 'cancelled'::text, 'skipped'::text]))
          ORDER BY r.service_id, r.environment, r.observed_at DESC
        )
 SELECT se.service_id,
    s.key AS service_key,
    s.name AS service_name,
    s.criticality,
    se.environment,
    l.run_key AS last_run_key,
    l.state AS last_run_state,
    l.failure_class AS last_failure_class,
    l.correlation_id AS last_correlation_id,
    l.observed_at,
    COALESCE(l.expires_at, (l.observed_at + make_interval(secs => ((se.expected_cadence_seconds + se.cadence_grace_seconds))::double precision))) AS expires_at,
    ops.freshness(l.observed_at, COALESCE(l.expires_at, (l.observed_at + make_interval(secs => ((se.expected_cadence_seconds + se.cadence_grace_seconds))::double precision)))) AS freshness_state,
    COALESCE(l.source_kind, 'registry'::text) AS source_kind,
    COALESCE(l.source_ref, 'ops.service_environment'::text) AS source_ref,
        CASE
            WHEN (l.state IS NULL) THEN 'unknown'::text
            WHEN (ops.freshness(l.observed_at, COALESCE(l.expires_at, (l.observed_at + make_interval(secs => ((se.expected_cadence_seconds + se.cadence_grace_seconds))::double precision)))) <> 'fresh'::text) THEN 'unknown'::text
            WHEN (l.state = ANY (ARRAY['failed'::text, 'timed_out'::text])) THEN 'unavailable'::text
            WHEN (l.state = ANY (ARRAY['skipped'::text, 'cancelled'::text])) THEN 'degraded'::text
            WHEN (l.state = 'succeeded'::text) THEN 'healthy'::text
            ELSE 'unknown'::text
        END AS health
   FROM ((ops.service_environment se
     JOIN ops.service s ON ((s.id = se.service_id)))
     LEFT JOIN latest l ON (((l.service_id = se.service_id) AND (l.environment = se.environment))))
  WHERE (s.retired_at IS NULL);


--
-- Name: VIEW v_service_environment_health; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON VIEW ops.v_service_environment_health IS 'THE ONLY PLACE HEALTH IS EXPRESSED, and it is derived. The premortem names false health from stale collectors as the second most likely catastrophe; a stored health column is that failure mechanism. A silent collector reads unknown here because no green was ever written down.';


--
-- Name: work_request; Type: TABLE; Schema: ops; Owner: -
--

CREATE TABLE ops.work_request (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ref text NOT NULL,
    state text DEFAULT 'captured'::text NOT NULL,
    title text NOT NULL,
    desired_outcome text,
    acceptance_criteria jsonb DEFAULT '[]'::jsonb NOT NULL,
    origin_ref text,
    requester_actor text NOT NULL,
    owner_actor text,
    executor_actor text,
    blocker_code text,
    blocker_detail text,
    verification_accepted_at timestamp with time zone,
    verification_evidence_ref text,
    exit_reason text,
    superseded_by uuid,
    captured_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    started_at timestamp with time zone,
    closed_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    correlation_id uuid,
    CONSTRAINT blocked_needs_a_named_blocker CHECK (((state <> 'blocked'::text) OR ((blocker_code IS NOT NULL) AND (blocker_detail IS NOT NULL)))),
    CONSTRAINT confirmed_close_needs_accepted_verification CHECK (((state <> 'confirmed_closed'::text) OR (verification_accepted_at IS NOT NULL))),
    CONSTRAINT side_exits_record_a_reason CHECK (((state <> ALL (ARRAY['declined'::text, 'superseded'::text, 'failed'::text])) OR (exit_reason IS NOT NULL))),
    CONSTRAINT superseded_names_its_successor CHECK (((state <> 'superseded'::text) OR (superseded_by IS NOT NULL))),
    CONSTRAINT terminal_rows_are_closed CHECK (((state = ANY (ARRAY['confirmed_closed'::text, 'declined'::text, 'superseded'::text])) = (closed_at IS NOT NULL))),
    CONSTRAINT work_request_state_check CHECK ((state = ANY (ARRAY['captured'::text, 'triaged'::text, 'ready'::text, 'claimed'::text, 'in_progress'::text, 'verification'::text, 'awaiting_release'::text, 'released'::text, 'confirmed_closed'::text, 'needs_joe'::text, 'blocked'::text, 'declined'::text, 'superseded'::text, 'failed'::text])))
);


--
-- Name: TABLE work_request; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON TABLE ops.work_request IS 'The canonical Work Request. States come from control-room/contracts/state-machines.v1.json; the Workspace projection is defined by control-room/contracts/work-request-projection.v1.json and is derived, never stored. TRANSITIONS ARE SERVER-ENFORCED, not enforced here: doctrine requires it and the guards need evidence a CHECK cannot see. This table guarantees only that a row is never in an undefined state and never violates an invariant the machine declares about states.';


--
-- Name: COLUMN work_request.correlation_id; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON COLUMN ops.work_request.correlation_id IS 'Nullable on purpose: rows predate correlation, and a work request captured by hand has no journey behind it. Present when the request came out of a traced failure.';


--
-- Name: v_trace; Type: VIEW; Schema: ops; Owner: -
--

CREATE VIEW ops.v_trace AS
 SELECT d.correlation_id,
    'deployment'::text AS kind,
    COALESCE(d.release_ref, "left"(d.git_sha, 12), (d.id)::text) AS ref,
    d.state,
    COALESCE(d.ended_at, d.started_at, d.observed_at) AS occurred_at,
    d.environment,
    s.key AS service_key,
    d.failure_class,
    d.detail,
    d.source_kind,
    d.source_ref,
    d.observed_at,
    d.expires_at,
    ops.freshness(d.observed_at, d.expires_at) AS freshness_state,
    d.id AS row_id
   FROM (ops.deployment d
     JOIN ops.service s ON ((s.id = d.service_id)))
UNION ALL
 SELECT r.correlation_id,
    r.kind,
    r.run_key AS ref,
    r.state,
    COALESCE(r.ended_at, r.started_at, r.observed_at) AS occurred_at,
    r.environment,
    s.key AS service_key,
    r.failure_class,
    r.detail,
    r.source_kind,
    r.source_ref,
    r.observed_at,
    r.expires_at,
    ops.freshness(r.observed_at, r.expires_at) AS freshness_state,
    r.id AS row_id
   FROM (ops.run r
     JOIN ops.service s ON ((s.id = r.service_id)))
UNION ALL
 SELECT i.correlation_id,
    'incident'::text AS kind,
    i.ref,
    i.state,
    i.detected_at AS occurred_at,
    i.environment,
    NULL::text AS service_key,
    NULL::text AS failure_class,
    i.title AS detail,
    i.source_kind,
    i.source_ref,
    i.observed_at,
    i.expires_at,
    ops.freshness(i.observed_at, i.expires_at) AS freshness_state,
    i.id AS row_id
   FROM ops.incident i
UNION ALL
 SELECT w.correlation_id,
    'work_request'::text AS kind,
    w.ref,
    w.state,
    w.captured_at AS occurred_at,
    NULL::text AS environment,
    NULL::text AS service_key,
    w.blocker_code AS failure_class,
    w.title AS detail,
    'registry'::text AS source_kind,
    'ops.work_request'::text AS source_ref,
    w.updated_at AS observed_at,
    NULL::timestamp with time zone AS expires_at,
    ops.freshness(w.updated_at, NULL::timestamp with time zone) AS freshness_state,
    w.id AS row_id
   FROM ops.work_request w
  WHERE (w.correlation_id IS NOT NULL);


--
-- Name: VIEW v_trace; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON VIEW ops.v_trace IS 'THE PROGRAM 3 GATE. One correlation id returns the whole journey — deploy, golden-workflow check, job run, incident, work request — in time order, every link carrying its own source and freshness. Read-only by construction: a view over four tables with no insert rule.';


--
-- Name: activity; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.activity (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    actor_id uuid NOT NULL,
    kind text NOT NULL,
    summary text NOT NULL,
    detail text,
    owed text,
    deal_id uuid,
    client_id uuid,
    lead_id uuid,
    vendor_id uuid,
    source text DEFAULT 'stated'::text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid,
    connected boolean
);


--
-- Name: COLUMN activity.connected; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.activity.connected IS 'Did this contact actually reach the person? TRUE = spoke/replied, FALSE = attempt only (voicemail, no answer, unanswered text), NULL = not recorded. Only meaningful for kinds whose direction is ambiguous — call and text. meeting/tour/loi/lease_signed are two-way by definition; email_in and counter_received prove a reply by existing. Stored because only the person who made the call knows, and NULL is honestly unknown rather than no.';


--
-- Name: activity_kind; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.activity_kind (
    slug text NOT NULL,
    label text NOT NULL,
    is_contact boolean NOT NULL
);


--
-- Name: TABLE activity_kind; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.activity_kind IS 'Activity vocabulary. is_contact answers ONE question: does a row of this kind count as having TOUCHED the subject? v_last_touch aggregates only these. note and task are deliberately false — an internal annotation is not a touch (the 2026-07-30 freeze shipped 13 rulings as activity rows and stamped cold records warm; that is the defect this flag closes).';


--
-- Name: actor; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.actor (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    slug text NOT NULL,
    kind text NOT NULL,
    display_name text NOT NULL,
    email text,
    active boolean DEFAULT true NOT NULL,
    phone text,
    CONSTRAINT actor_kind_check CHECK ((kind = ANY (ARRAY['human'::text, 'automation'::text, 'system'::text])))
);


--
-- Name: actor_profile; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.actor_profile (
    actor_id uuid NOT NULL,
    key text NOT NULL,
    value jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: agreement; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agreement (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    client_id uuid NOT NULL,
    kind text NOT NULL,
    signed_on date,
    expires_on date,
    tail_months integer,
    status text NOT NULL,
    doc_attachment uuid,
    note text,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid,
    CONSTRAINT agreement_kind_check CHECK ((kind = ANY (ARRAY['etl'::text, 'buyer_rep'::text, 'listing_referral'::text, 'other'::text]))),
    CONSTRAINT agreement_status_check CHECK ((status = ANY (ARRAY['none_deliberate'::text, 'draft'::text, 'sent'::text, 'signed'::text, 'expired'::text]))),
    CONSTRAINT agreement_tail_months_check CHECK (((tail_months >= 0) AND (tail_months <= 36)))
);


--
-- Name: ammo_item; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ammo_item (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    kind text NOT NULL,
    body text NOT NULL,
    provenance text NOT NULL,
    expires_on date,
    status text DEFAULT 'untested'::text NOT NULL,
    evidence jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ammo_item_kind_check CHECK ((kind = ANY (ARRAY['hook'::text, 'stat'::text, 'proof_point'::text, 'concept'::text, 'angle'::text]))),
    CONSTRAINT ammo_item_status_check CHECK ((status = ANY (ARRAY['untested'::text, 'testing'::text, 'proven'::text, 'archived'::text, 'failed'::text])))
);


--
-- Name: attachment; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.attachment (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    subject_type text NOT NULL,
    subject_id uuid NOT NULL,
    r2_key text NOT NULL,
    filename text NOT NULL,
    mime text NOT NULL,
    sha256 text NOT NULL,
    bytes bigint NOT NULL,
    deleted_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL
);


--
-- Name: availability; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.availability (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    space_id uuid NOT NULL,
    observed_at timestamp with time zone DEFAULT now() NOT NULL,
    source text NOT NULL,
    status text NOT NULL,
    rate_amount numeric(12,2),
    rate_basis text,
    rate_norm_sf_yr numeric(12,2) GENERATED ALWAYS AS (
CASE rate_basis
    WHEN 'usd_sf_yr'::text THEN rate_amount
    WHEN 'usd_sf_mo'::text THEN (rate_amount * (12)::numeric)
    ELSE NULL::numeric
END) STORED,
    rate_norm_gross_sf_yr numeric(12,2),
    norm_owed boolean DEFAULT false NOT NULL,
    opex_sf_yr numeric(8,2),
    available_on date,
    note text,
    CONSTRAINT availability_check CHECK (((rate_amount IS NULL) OR (rate_basis IS NOT NULL))),
    CONSTRAINT availability_opex_sf_yr_check CHECK (((opex_sf_yr >= (0)::numeric) AND (opex_sf_yr <= (60)::numeric))),
    CONSTRAINT availability_rate_amount_check CHECK ((rate_amount > (0)::numeric)),
    CONSTRAINT availability_rate_basis_check CHECK ((rate_basis = ANY (ARRAY['usd_sf_yr'::text, 'usd_sf_mo'::text, 'usd_mo_gross'::text, 'usd_yr_gross'::text]))),
    CONSTRAINT availability_rate_norm_gross_sf_yr_check CHECK (((rate_norm_gross_sf_yr IS NULL) OR ((rate_norm_gross_sf_yr >= (2)::numeric) AND (rate_norm_gross_sf_yr <= (250)::numeric)))),
    CONSTRAINT availability_rate_norm_sf_yr_check CHECK (((rate_norm_sf_yr IS NULL) OR ((rate_norm_sf_yr >= (2)::numeric) AND (rate_norm_sf_yr <= (250)::numeric)))),
    CONSTRAINT availability_status_check CHECK ((status = ANY (ARRAY['available'::text, 'pending'::text, 'leased'::text, 'off_market'::text])))
);


--
-- Name: building; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.building (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    parcel_id uuid,
    address text NOT NULL,
    city text,
    state text,
    zip text,
    name text,
    class text,
    sub_type text,
    year_built integer,
    stories integer,
    status text,
    status_source text,
    merged_into uuid,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid NOT NULL,
    CONSTRAINT building_stories_check CHECK (((stories >= 1) AND (stories <= 60))),
    CONSTRAINT building_year_built_check CHECK (((year_built >= 1800) AND (year_built <= 2100)))
);


--
-- Name: building_ownership; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.building_ownership (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    building_id uuid NOT NULL,
    party_id uuid NOT NULL,
    kind text NOT NULL,
    from_on date,
    to_on date,
    source text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL,
    CONSTRAINT building_ownership_kind_check CHECK ((kind = ANY (ARRAY['owner'::text, 'landlord_rep'::text, 'property_manager'::text, 'listing_agent'::text])))
);


--
-- Name: cadence_rule; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cadence_rule (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    lane text NOT NULL,
    subject_type text NOT NULL,
    trigger text NOT NULL,
    interval_days integer,
    action_template text,
    active boolean DEFAULT true NOT NULL,
    status_filter text[],
    CONSTRAINT cadence_rule_subject_type_check CHECK ((subject_type = ANY (ARRAY['deal'::text, 'client'::text, 'lead'::text, 'vendor'::text]))),
    CONSTRAINT cadence_rule_trigger_check CHECK ((trigger = ANY (ARRAY['on_complete'::text, 'on_date'::text])))
);


--
-- Name: TABLE cadence_rule; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.cadence_rule IS 'Cadence engine rules (ORDER 11f, wave2-design §2e). One row = one rule; the engine spawns next_action rows from them, so subject_type mirrors next_action''s own vocabulary exactly — a rule naming a subject_type that next_action cannot hold is dead on arrival. lane is deliberately un-CHECKed: the design names nurture45 / nurture90 / vendor_maintenance / lease_event / custom, and 0017''s direction of travel is vocabularies as ROWS, so a lane ref table is a later row-add, never a redeploy. HARD DEFAULT from Joe''s 2026-07-31 cold/paused ruling (rule store + decision-history): cold-class clients are EXCLUDED from automatic nurture cadences — never pester a ghost. A cold or paused client re-enters cadence only by a human act. Engine thresholds and settings live in system_config, never in a second config table.';


--
-- Name: COLUMN cadence_rule.trigger; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.cadence_rule.trigger IS 'on_complete = fires when the subject''s next_action completes; on_date = fires on a dated trigger (lease event, critical date).';


--
-- Name: COLUMN cadence_rule.interval_days; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.cadence_rule.interval_days IS 'Nullable on purpose: an on_date rule has no interval.';


--
-- Name: COLUMN cadence_rule.status_filter; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.cadence_rule.status_filter IS 'ORDER 19(c). Nullable text[] of client_status slugs. NULL means the rule applies to every subject the hard guard allows (the pre-0021 behaviour, unchanged). A non-empty array narrows the rule to subjects whose governing client status is in the list — nurture45 carries {engaged} so "engaged-client nurture" means what it says. This is a NARROWING filter only: it can never re-admit a cold or paused subject, because the hard abundance guard runs first and is not expressible in data by design.';


--
-- Name: campaign; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.campaign (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    goal text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    starts_on date NOT NULL,
    ends_on date,
    success_criterion text NOT NULL,
    channels text[] DEFAULT '{}'::text[] NOT NULL,
    outcome_verdict text,
    outcome_note text,
    coverage_at_scoring jsonb,
    scored_at timestamp with time zone,
    scored_by uuid,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid,
    CONSTRAINT campaign_channels_nonempty_check CHECK ((cardinality(channels) > 0)),
    CONSTRAINT campaign_closed_is_scored_check CHECK (((status <> 'closed'::text) OR (scored_at IS NOT NULL))),
    CONSTRAINT campaign_scored_pair_check CHECK (((scored_at IS NULL) = (outcome_verdict IS NULL))),
    CONSTRAINT campaign_status_check CHECK ((status = ANY (ARRAY['active'::text, 'paused'::text, 'closed'::text]))),
    CONSTRAINT campaign_verdict_check CHECK (((outcome_verdict IS NULL) OR (outcome_verdict = ANY (ARRAY['worked'::text, 'did_not_work'::text, 'inconclusive'::text])))),
    CONSTRAINT campaign_window_check CHECK (((ends_on IS NULL) OR (ends_on >= starts_on)))
);


--
-- Name: COLUMN campaign.goal; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.campaign.goal IS 'The objective, in one sentence — what this campaign is FOR. NOT NULL since 0066. Reused as the objective rather than joined by a second `objective` column: two homes for one fact is the 0045 fault.';


--
-- Name: COLUMN campaign.success_criterion; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.campaign.success_criterion IS 'What would have to be observably true for this to have worked, written so it can be CHECKED rather than admired. NOT NULL since 0066, and score-campaign quotes it back before accepting a verdict — a criterion invented after the results are in is not a criterion. The single most important column in this table: 259 metric rows exist today and none of them can answer "did it work" because nothing ever stated what working meant.';


--
-- Name: COLUMN campaign.channels; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.campaign.channels IS 'Where this campaign runs. Validated against marketing_subject platform slugs by trigger, not by CHECK — Postgres has no array-element foreign key. Never empty.';


--
-- Name: COLUMN campaign.coverage_at_scoring; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.campaign.coverage_at_scoring IS 'The measurement coverage SNAPSHOT taken at the moment of scoring: how many of the campaign''s placements actually carried metrics when the verdict was formed. Stored so a verdict can never be re-read as better-evidenced than it was — a "worked" over 3 measured placements out of 40 is a different claim from a "worked" over 40 of 40, and six months later nothing else in the record would tell them apart.';


--
-- Name: candidate_pool; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.candidate_pool (
    id uuid DEFAULT gen_random_uuid() CONSTRAINT prospect_pool_id_not_null NOT NULL,
    source text CONSTRAINT prospect_pool_source_not_null NOT NULL,
    source_key text CONSTRAINT prospect_pool_source_key_not_null NOT NULL,
    source_seq integer,
    source_row jsonb CONSTRAINT prospect_pool_source_row_not_null NOT NULL,
    name text CONSTRAINT prospect_pool_name_not_null NOT NULL,
    org_name text,
    vertical text,
    address text,
    city text,
    county text,
    state text,
    email text,
    phone text,
    segment text,
    segment_play text,
    score numeric(5,2),
    score_basis text,
    est_lease_event date,
    est_basis text,
    status text DEFAULT 'pool'::text CONSTRAINT prospect_pool_status_not_null NOT NULL,
    promoted_lead_id uuid,
    dup_tier text,
    dup_subject_type text,
    dup_subject_id uuid,
    dup_ref text,
    dup_basis text,
    dup_do_not_contact boolean DEFAULT false CONSTRAINT prospect_pool_dup_do_not_contact_not_null NOT NULL,
    version integer DEFAULT 1 CONSTRAINT prospect_pool_version_not_null NOT NULL,
    created_at timestamp with time zone DEFAULT now() CONSTRAINT prospect_pool_created_at_not_null NOT NULL,
    created_by uuid CONSTRAINT prospect_pool_created_by_not_null NOT NULL,
    updated_at timestamp with time zone DEFAULT now() CONSTRAINT prospect_pool_updated_at_not_null NOT NULL,
    updated_by uuid CONSTRAINT prospect_pool_updated_by_not_null NOT NULL,
    declined_at timestamp with time zone,
    declined_by uuid,
    decline_reason text,
    CONSTRAINT candidate_pool_declined_has_reason CHECK (((status = 'declined'::text) = ((decline_reason IS NOT NULL) AND (decline_reason <> ''::text)))),
    CONSTRAINT candidate_pool_declined_has_stamp CHECK (((status = 'declined'::text) = (declined_at IS NOT NULL))),
    CONSTRAINT candidate_pool_status_check CHECK ((status = ANY (ARRAY['pool'::text, 'promoted'::text, 'suppressed_dup'::text, 'declined'::text]))),
    CONSTRAINT pool_dup_tier_pairs_with_pointer CHECK (((dup_tier IS NULL) = (dup_subject_type IS NULL))),
    CONSTRAINT pool_promoted_has_lead CHECK (((status = 'promoted'::text) = (promoted_lead_id IS NOT NULL))),
    CONSTRAINT pool_suppressed_iff_tier_suppressed CHECK (((status = 'suppressed_dup'::text) = (COALESCE(dup_tier, ''::text) = 'suppressed'::text))),
    CONSTRAINT prospect_pool_dup_subject_type_check CHECK ((dup_subject_type = ANY (ARRAY['lead'::text, 'client'::text]))),
    CONSTRAINT prospect_pool_dup_tier_check CHECK ((dup_tier = ANY (ARRAY['suppressed'::text, 'review'::text])))
);


--
-- Name: TABLE candidate_pool; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.candidate_pool IS 'The candidate reservoir: raw, unjudged rows from licence sweeps and radar lanes. 9,860 at rename. Deliberately carries NO party_id — a candidate has no identity until promote-pool mints one, which is why 9,860 rows cost nothing and why merging never has to consider them. Qualifying context lives here as STRUCTURED columns (source_row 100%, score_basis 100%, segment_play 87%) rather than as a dossier: candidate context is uniform because it came from a list, and narrative context begins at promotion, when a human starts working an individual. Renamed from prospect_pool in 0048 to free the word "prospect" for the funnel stage after lead.';


--
-- Name: COLUMN candidate_pool.score; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.candidate_pool.score IS 'PRESENTED, never filtering. Nothing may use this to decide whether a row lands.';


--
-- Name: COLUMN candidate_pool.dup_do_not_contact; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.candidate_pool.dup_do_not_contact IS 'The renewal-radar suppressor DROPS a do-not-contact match. The pool keeps the row and raises this flag instead — never deleted, never re-presented.';


--
-- Name: COLUMN candidate_pool.decline_reason; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.candidate_pool.decline_reason IS 'WHY a human said no, in his own words. Required when status is declined. This is the input to the lane-retirement decision: a lane whose declines are all "no contact channel" has a fixable defect, a lane whose declines are all "out of territory" is mis-scoped, and a lane whose declines are all "not a fit" is working correctly and simply has a low hit rate. Without it every lane looks identical from the outside.';


--
-- Name: capture_candidate; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.capture_candidate (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    session_id uuid NOT NULL,
    idempotency_key text NOT NULL,
    batch_hash text NOT NULL,
    item_index integer NOT NULL,
    kind text NOT NULL,
    payload jsonb NOT NULL,
    evidence_quote text NOT NULL,
    confidence numeric NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    resolved_by uuid,
    resolution_note text,
    resulting_ref text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone,
    CONSTRAINT capture_candidate_check CHECK ((((status = 'pending'::text) AND (resolved_by IS NULL) AND (resolved_at IS NULL) AND (resulting_ref IS NULL)) OR ((status = 'skipped'::text) AND (resolved_by IS NOT NULL) AND (resolved_at IS NOT NULL) AND (resulting_ref IS NULL)) OR ((status = 'confirmed'::text) AND (resolved_by IS NOT NULL) AND (resolved_at IS NOT NULL) AND (resulting_ref IS NOT NULL)))),
    CONSTRAINT capture_candidate_confidence_check CHECK (((confidence >= (0)::numeric) AND (confidence <= (1)::numeric))),
    CONSTRAINT capture_candidate_evidence_quote_check CHECK ((array_length(regexp_split_to_array(btrim(evidence_quote), '\s+'::text), 1) <= 15)),
    CONSTRAINT capture_candidate_item_index_check CHECK ((item_index >= 0)),
    CONSTRAINT capture_candidate_kind_check CHECK ((kind = ANY (ARRAY['phase_move'::text, 'next_step'::text, 'new_deal'::text, 'activity'::text, 'meeting_record'::text]))),
    CONSTRAINT capture_candidate_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT capture_candidate_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'confirmed'::text, 'skipped'::text])))
);


--
-- Name: capture_post_call_action; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.capture_post_call_action (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    candidate_id uuid NOT NULL,
    deal_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    due_on date,
    description text NOT NULL,
    accepted_by uuid NOT NULL,
    accepted_at timestamp with time zone DEFAULT now() NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT capture_post_call_action_check CHECK ((((status = 'done'::text) AND (completed_at IS NOT NULL)) OR ((status = ANY (ARRAY['open'::text, 'dropped'::text])) AND (completed_at IS NULL)))),
    CONSTRAINT capture_post_call_action_description_check CHECK ((btrim(description) <> ''::text)),
    CONSTRAINT capture_post_call_action_status_check CHECK ((status = ANY (ARRAY['open'::text, 'done'::text, 'dropped'::text])))
);


--
-- Name: TABLE capture_post_call_action; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.capture_post_call_action IS 'Additive, human-accepted Call Mode action. It never replaces or drops an existing next_action.';


--
-- Name: capture_post_call_candidate; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.capture_post_call_candidate (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    session_id uuid NOT NULL,
    idempotency_key text NOT NULL,
    batch_hash text NOT NULL,
    item_index integer NOT NULL,
    kind text NOT NULL,
    deal_id uuid NOT NULL,
    assignee_slug text,
    action_description text,
    due_on date,
    recipient_party_id uuid,
    recipient_ref text,
    email_subject text,
    body_sha256 text,
    evidence_quote text NOT NULL,
    confidence numeric NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    resolved_by uuid,
    resolution_note text,
    resulting_ref text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone,
    CONSTRAINT capture_post_call_candidate_check CHECK ((((kind = 'assigned_action'::text) AND (assignee_slug = ANY (ARRAY['joe'::text, 'dell'::text])) AND (action_description IS NOT NULL) AND (btrim(action_description) <> ''::text) AND (recipient_party_id IS NULL) AND (recipient_ref IS NULL) AND (email_subject IS NULL) AND (body_sha256 IS NULL)) OR ((kind = 'email_draft'::text) AND (assignee_slug IS NULL) AND (action_description IS NULL) AND (due_on IS NULL) AND (recipient_party_id IS NOT NULL) AND (recipient_ref ~ '^P-[0-9]+$'::text) AND (email_subject IS NOT NULL) AND (btrim(email_subject) <> ''::text) AND (body_sha256 ~ '^[0-9a-f]{64}$'::text)))),
    CONSTRAINT capture_post_call_candidate_check1 CHECK ((((status = 'pending'::text) AND (resolved_by IS NULL) AND (resolved_at IS NULL) AND (resulting_ref IS NULL)) OR ((status = 'skipped'::text) AND (resolved_by IS NOT NULL) AND (resolved_at IS NOT NULL) AND (resulting_ref IS NULL)) OR ((status = 'confirmed'::text) AND (resolved_by IS NOT NULL) AND (resolved_at IS NOT NULL) AND ((resulting_ref IS NOT NULL) OR (kind = 'email_draft'::text))))),
    CONSTRAINT capture_post_call_candidate_confidence_check CHECK (((confidence >= (0)::numeric) AND (confidence <= (1)::numeric))),
    CONSTRAINT capture_post_call_candidate_evidence_quote_check CHECK ((array_length(regexp_split_to_array(btrim(evidence_quote), '\s+'::text), 1) <= 15)),
    CONSTRAINT capture_post_call_candidate_item_index_check CHECK ((item_index >= 0)),
    CONSTRAINT capture_post_call_candidate_kind_check CHECK ((kind = ANY (ARRAY['assigned_action'::text, 'email_draft'::text]))),
    CONSTRAINT capture_post_call_candidate_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'confirmed'::text, 'skipped'::text])))
);


--
-- Name: TABLE capture_post_call_candidate; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.capture_post_call_candidate IS 'Remote-safe Call Mode proposals. Transcript and Outlook draft bodies are local only; this table stores exact refs, action or draft metadata, a hash, and short evidence.';


--
-- Name: capture_post_call_report; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.capture_post_call_report (
    session_id uuid NOT NULL,
    idempotency_key text NOT NULL,
    report_sha256 text NOT NULL,
    candidate_count integer NOT NULL,
    filed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT capture_post_call_report_candidate_count_check CHECK ((candidate_count >= 0)),
    CONSTRAINT capture_post_call_report_report_sha256_check CHECK ((report_sha256 ~ '^[0-9a-f]{64}$'::text))
);


--
-- Name: TABLE capture_post_call_report; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.capture_post_call_report IS 'Aggregate post-call filing marker. It contains only a local report hash and candidate count.';


--
-- Name: capture_session; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.capture_session (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    nonce text NOT NULL,
    device_id text NOT NULL,
    actor_id uuid NOT NULL,
    mode text NOT NULL,
    started_at timestamp with time zone NOT NULL,
    consent_announced_at timestamp with time zone NOT NULL,
    session_token_hash text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    state text DEFAULT 'recording'::text NOT NULL,
    state_at timestamp with time zone NOT NULL,
    state_detail text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    post_call boolean DEFAULT false NOT NULL,
    CONSTRAINT capture_session_mode_check CHECK ((mode = 'meeting'::text)),
    CONSTRAINT capture_session_state_check CHECK ((state = ANY (ARRAY['recording'::text, 'transcribing'::text, 'distilling'::text, 'done'::text, 'failed'::text])))
);


--
-- Name: client; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.client (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    roster_ref text,
    party_id uuid NOT NULL,
    client_type text,
    vertical text,
    subtype text,
    status text,
    etl_status text,
    acquisition_source text,
    acquisition_detail text,
    notes_path text,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid NOT NULL,
    owner_id uuid,
    owner_label text,
    merged_into uuid,
    contact_label text,
    deal_type_label text,
    possible_duplicate_label text,
    notes text,
    specialty_type_label text
);


--
-- Name: client_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.client_status (
    slug text NOT NULL,
    label text NOT NULL,
    sort integer DEFAULT 100 NOT NULL,
    is_active_pipeline boolean DEFAULT false NOT NULL,
    note text
);


--
-- Name: COLUMN client_status.is_active_pipeline; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.client_status.is_active_pipeline IS 'Does this status alone put a client in the active book? Membership is derived (open deal OR this flag) -- never stored per-client. Joe owns these values.';


--
-- Name: COLUMN client_status.note; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.client_status.note IS 'Why this status exists and when to use it. Read before assigning a status.';


--
-- Name: client_type; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.client_type (
    slug text NOT NULL,
    label text NOT NULL
);


--
-- Name: code_subject; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.code_subject (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    repo text NOT NULL,
    commit_sha text,
    label text,
    note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid,
    CONSTRAINT code_subject_repo_shape CHECK (((repo = lower(btrim(repo))) AND (repo ~ '^[a-z0-9._-]+/[a-z0-9._-]+$'::text))),
    CONSTRAINT code_subject_sha_shape CHECK (((commit_sha IS NULL) OR (commit_sha ~ '^[0-9a-f]{7,40}$'::text)))
);


--
-- Name: TABLE code_subject; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.code_subject IS 'One stable uuid per reviewable code object — a repo, or a repo at one commit — so record_flag''s existing (subject_type, subject_id) pointer can address code evidence the same way it addresses a client. commit_sha NULL = the repo itself. UNLIKE marketing_subject (0066) this registry is minted on demand by record-finding: a sha is self-evidencing, not a taxonomy, so there is no vocabulary for a typo to pollute.';


--
-- Name: commission; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.commission (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    deal_id uuid NOT NULL,
    gross_amount numeric(14,2) NOT NULL,
    status text NOT NULL,
    invoiced_on date,
    received_on date,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid,
    CONSTRAINT commission_gross_amount_check CHECK ((gross_amount >= (0)::numeric)),
    CONSTRAINT commission_status_check CHECK ((status = ANY (ARRAY['expected'::text, 'invoiced'::text, 'received'::text])))
);


--
-- Name: commission_allocation; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.commission_allocation (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    commission_id uuid NOT NULL,
    parent_id uuid,
    actor_id uuid,
    party_id uuid,
    kind text NOT NULL,
    fraction numeric(6,5) NOT NULL,
    computed_amount numeric(14,2),
    CONSTRAINT commission_allocation_check CHECK (((actor_id IS NOT NULL) OR (party_id IS NOT NULL))),
    CONSTRAINT commission_allocation_fraction_check CHECK (((fraction > (0)::numeric) AND (fraction <= (1)::numeric))),
    CONSTRAINT commission_allocation_kind_check CHECK ((kind = ANY (ARRAY['referral_fee'::text, 'partner_split'::text, 'house'::text, 'other'::text])))
);


--
-- Name: comp; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.comp (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    space_id uuid,
    building_id uuid,
    executed_on date,
    term_months integer,
    rate_amount numeric(12,2) NOT NULL,
    rate_basis text NOT NULL,
    rate_norm_sf_yr numeric(12,2),
    ti_amount numeric(12,2),
    escalator text,
    is_estimate boolean DEFAULT false NOT NULL,
    source text NOT NULL,
    source_row jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL,
    CONSTRAINT comp_rate_amount_check CHECK ((rate_amount > (0)::numeric)),
    CONSTRAINT comp_rate_basis_check CHECK ((rate_basis = ANY (ARRAY['usd_sf_yr'::text, 'usd_sf_mo'::text, 'usd_mo_gross'::text, 'usd_yr_gross'::text]))),
    CONSTRAINT comp_rate_norm_sf_yr_check CHECK (((rate_norm_sf_yr IS NULL) OR ((rate_norm_sf_yr >= (2)::numeric) AND (rate_norm_sf_yr <= (250)::numeric)))),
    CONSTRAINT comp_term_months_check CHECK (((term_months >= 1) AND (term_months <= 480)))
);


--
-- Name: contact_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contact_state (
    slug text NOT NULL,
    label text NOT NULL,
    contactable boolean NOT NULL,
    sort integer NOT NULL
);


--
-- Name: content_piece; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.content_piece (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    campaign_id uuid,
    author_id uuid NOT NULL,
    kind text NOT NULL,
    topic text,
    features jsonb DEFAULT '{}'::jsonb NOT NULL,
    body_path text,
    status text DEFAULT 'idea'::text NOT NULL,
    lint_passed boolean,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid,
    CONSTRAINT content_piece_status_check CHECK ((status = ANY (ARRAY['idea'::text, 'drafted'::text, 'in_review'::text, 'approved'::text, 'edited_approved'::text, 'rejected'::text, 'scheduled'::text, 'live'::text, 'measured'::text, 'retired'::text])))
);


--
-- Name: critical_date; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.critical_date (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    deal_id uuid NOT NULL,
    kind text NOT NULL,
    due_on date NOT NULL,
    note text,
    source text NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid,
    CONSTRAINT critical_date_status_check CHECK ((status = ANY (ARRAY['open'::text, 'passed'::text, 'cleared'::text])))
);


--
-- Name: deal; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deal (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    client_id uuid NOT NULL,
    name text NOT NULL,
    salesforce_id text,
    deal_type text NOT NULL,
    phase text NOT NULL,
    segment text,
    outcome text,
    closed_on date,
    won_value numeric(14,2),
    sf_commission_placeholder numeric(14,2),
    sf_close_date_placeholder date,
    notes_path text,
    source_row jsonb,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid NOT NULL,
    lane text,
    city text,
    owner text,
    attention boolean DEFAULT false NOT NULL,
    next_date date,
    operating_state text DEFAULT 'active'::text NOT NULL,
    parking_reason text,
    parking_note text,
    parked_at timestamp with time zone,
    parked_by uuid,
    CONSTRAINT deal_operating_state_check CHECK ((operating_state = ANY (ARRAY['active'::text, 'parked'::text]))),
    CONSTRAINT deal_outcome_check CHECK ((outcome = ANY (ARRAY['won'::text, 'lost'::text, 'paused'::text]))),
    CONSTRAINT deal_parking_shape_check CHECK ((((operating_state = 'active'::text) AND (parking_reason IS NULL) AND (parking_note IS NULL) AND (parked_at IS NULL) AND (parked_by IS NULL)) OR ((operating_state = 'parked'::text) AND (parking_reason IS NOT NULL) AND (parking_reason = ANY (ARRAY['prospect_never_active'::text, 'client_paused'::text, 'other'::text])) AND (parked_at IS NOT NULL) AND (parked_by IS NOT NULL))))
);


--
-- Name: COLUMN deal.lane; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.deal.lane IS 'Territory or national (0061). Transcribed verbatim from deal.source_row->>''lane'', which the Salesforce import has always carried and which the importer discarded. Distinct from segment ON PURPOSE (Joe, 2026-08-02: "the brand is NOT a segment. Segment holds the vertical; the national flag is a lane; the account is the parent-client link") — before this column existed, two deals whose source segment was blank had the string ''national'' written into deal.segment instead, which is how a lane came to be mistaken for a vertical.';


--
-- Name: COLUMN deal.city; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.deal.city IS 'City of transaction (0074). Transcribed from deal.source_row->>''city'', which the Salesforce import has always carried and the importer discarded. Before this column existed no verb could set a city, so salesforce-diff could report a city move but never apply one, and any deal created by new-deal was born with no city at all.';


--
-- Name: COLUMN deal.operating_state; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.deal.operating_state IS 'Whether this Salesforce-linked row belongs in active operating work. Parking is reversible and never means closed, lost, or deleted.';


--
-- Name: COLUMN deal.parking_reason; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.deal.parking_reason IS 'Why the row is parked now: prospect_never_active, client_paused, or other. Event history preserves prior reasons after reactivation.';


--
-- Name: deal_conflict; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deal_conflict (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    deal_id uuid NOT NULL,
    field text NOT NULL,
    value_a jsonb,
    actor_a uuid NOT NULL,
    event_a uuid NOT NULL,
    value_b jsonb,
    actor_b uuid NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    resolved_by uuid,
    winner text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone,
    CONSTRAINT deal_conflict_check CHECK ((((status = 'open'::text) AND (resolved_by IS NULL) AND (winner IS NULL) AND (resolved_at IS NULL)) OR ((status = 'resolved'::text) AND (resolved_by IS NOT NULL) AND (winner IS NOT NULL) AND (resolved_at IS NOT NULL)))),
    CONSTRAINT deal_conflict_field_check CHECK ((field = ANY (ARRAY['phase'::text, 'owner'::text, 'attention'::text, 'next_date'::text, 'operating_state'::text]))),
    CONSTRAINT deal_conflict_status_check CHECK ((status = ANY (ARRAY['open'::text, 'resolved'::text]))),
    CONSTRAINT deal_conflict_winner_check CHECK ((winner = ANY (ARRAY['a'::text, 'b'::text])))
);


--
-- Name: deal_lane; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deal_lane (
    slug text NOT NULL,
    label text NOT NULL,
    sort integer NOT NULL,
    note text
);


--
-- Name: deal_market_assignment; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deal_market_assignment (
    deal_id uuid NOT NULL,
    agent_name text NOT NULL,
    agent_party_id uuid,
    market text,
    source text NOT NULL,
    set_by uuid NOT NULL,
    set_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT deal_market_assignment_agent_name_check CHECK ((btrim(agent_name) <> ''::text))
);


--
-- Name: TABLE deal_market_assignment; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.deal_market_assignment IS 'The local CARR agent assigned to a national-account transaction. agent_name is the stated agenda label; agent_party_id is filled only when identity is verified.';


--
-- Name: deal_note; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deal_note (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    deal_id uuid NOT NULL,
    kind text NOT NULL,
    text text NOT NULL,
    actor_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT deal_note_kind_check CHECK ((kind = ANY (ARRAY['note'::text, 'next_step'::text]))),
    CONSTRAINT deal_note_text_check CHECK ((btrim(text) <> ''::text))
);


--
-- Name: deal_participant; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deal_participant (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    deal_id uuid NOT NULL,
    actor_id uuid,
    party_id uuid,
    role text NOT NULL,
    from_at timestamp with time zone DEFAULT now() NOT NULL,
    to_at timestamp with time zone,
    set_by uuid NOT NULL,
    CONSTRAINT deal_participant_check CHECK (((actor_id IS NOT NULL) OR (party_id IS NOT NULL)))
);


--
-- Name: deal_phase; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deal_phase (
    slug text NOT NULL,
    label text NOT NULL,
    sort integer DEFAULT 100 NOT NULL
);


--
-- Name: deal_presence_lease; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deal_presence_lease (
    actor_id uuid NOT NULL,
    deal_id uuid NOT NULL,
    field text NOT NULL,
    expires_at timestamp with time zone NOT NULL
);


--
-- Name: deal_reattach_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deal_reattach_log (
    deal_id uuid NOT NULL,
    from_client uuid NOT NULL,
    to_client uuid NOT NULL,
    from_segment text,
    reason text NOT NULL,
    moved_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE deal_reattach_log; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.deal_reattach_log IS 'Deal -> old client -> new client, one row per re-point (0061). The record that makes a client re-attachment reversible: deal.client_id is a single column and overwriting it destroys the only copy of where the deal used to sit. Append-only; a record of what happened, not a work queue.';


--
-- Name: deal_review_item; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deal_review_item (
    session_id uuid NOT NULL,
    deal_id uuid NOT NULL,
    disposition text NOT NULL,
    note text,
    reviewed_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT deal_review_item_disposition_check CHECK ((disposition = ANY (ARRAY['reviewed'::text, 'skipped'::text])))
);


--
-- Name: deal_review_session; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deal_review_session (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workspace_kind text NOT NULL,
    account_client_id uuid,
    started_by uuid NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    ended_at timestamp with time zone,
    status text DEFAULT 'open'::text NOT NULL,
    summary text,
    CONSTRAINT deal_review_session_check CHECK ((((workspace_kind = 'team'::text) AND (account_client_id IS NULL)) OR ((workspace_kind = 'national_account'::text) AND (account_client_id IS NOT NULL)))),
    CONSTRAINT deal_review_session_check1 CHECK ((((status = 'open'::text) AND (ended_at IS NULL)) OR ((status <> 'open'::text) AND (ended_at IS NOT NULL)))),
    CONSTRAINT deal_review_session_status_check CHECK ((status = ANY (ARRAY['open'::text, 'completed'::text, 'abandoned'::text]))),
    CONSTRAINT deal_review_session_workspace_kind_check CHECK ((workspace_kind = ANY (ARRAY['team'::text, 'national_account'::text])))
);


--
-- Name: deal_type_ref; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deal_type_ref (
    slug text NOT NULL,
    label text NOT NULL,
    sort integer NOT NULL
);


--
-- Name: TABLE deal_type_ref; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.deal_type_ref IS 'Deal transaction types (amendment 1). Labels are the vault''s own verbatim capitalization where the legacy files had one — the exports render legacy passthrough, so a label that disagrees with the source is a drift generator.';


--
-- Name: defect; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.defect (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    occurred_on date DEFAULT CURRENT_DATE NOT NULL,
    defect_class text NOT NULL,
    claimed text NOT NULL,
    actual text NOT NULL,
    source_unread text,
    rule_violated text,
    detected_by text NOT NULL,
    session_key text,
    cost_note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid,
    CONSTRAINT defect_class_shape CHECK (((defect_class = lower(btrim(defect_class))) AND (defect_class <> ''::text) AND (defect_class !~ '\s\s'::text))),
    CONSTRAINT defect_detected_by_check CHECK ((detected_by = ANY (ARRAY['human'::text, 'self'::text, 'gate'::text, 'check'::text, 'peer_review'::text, 'downstream'::text]))),
    CONSTRAINT defect_states_a_contradiction CHECK (((btrim(claimed) <> ''::text) AND (btrim(actual) <> ''::text) AND (lower(btrim(claimed)) <> lower(btrim(actual)))))
);


--
-- Name: TABLE defect; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.defect IS 'The system''s memory of its own failures (0103, loop #185). One row = one claim the system made that was not true, with what was true beside it. Every prospective safeguard in this repo guesses at future failure; this is the only retrospective one. detected_by is NOT NULL and closed-vocabulary on purpose: a log where every row reads human is a log saying the self-checks do not work, and that is only visible if counted.';


--
-- Name: deprecation; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deprecation (
    object_name text NOT NULL,
    object_kind text NOT NULL,
    replaced_by text,
    reason text NOT NULL,
    deprecated_at date DEFAULT CURRENT_DATE NOT NULL,
    safe_to_drop_after date,
    dropped_at date
);


--
-- Name: TABLE deprecation; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.deprecation IS 'Things kept alive only for compatibility. A row here is a debt with a payoff date. `run.sh health` reads it, greps the repo, and reports whether anything still references each object — so deleting is a decision made on evidence rather than nerve. Nothing here is dropped automatically: dropping is irreversible, detecting is free.';


--
-- Name: diagnostic_route; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.diagnostic_route (
    route_key text NOT NULL,
    signal_kind text,
    from_kind text NOT NULL,
    relation text NOT NULL,
    to_kind text NOT NULL,
    test_verb text NOT NULL,
    input_contract jsonb DEFAULT '{}'::jsonb NOT NULL,
    minimum_effect numeric,
    active boolean DEFAULT true NOT NULL,
    created_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT diagnostic_route_from_kind_check CHECK ((btrim(from_kind) <> ''::text)),
    CONSTRAINT diagnostic_route_input_contract_check CHECK ((jsonb_typeof(input_contract) = 'object'::text)),
    CONSTRAINT diagnostic_route_relation_check CHECK ((btrim(relation) <> ''::text)),
    CONSTRAINT diagnostic_route_route_key_check CHECK ((btrim(route_key) <> ''::text)),
    CONSTRAINT diagnostic_route_test_verb_check CHECK ((btrim(test_verb) <> ''::text)),
    CONSTRAINT diagnostic_route_to_kind_check CHECK ((btrim(to_kind) <> ''::text))
);


--
-- Name: TABLE diagnostic_route; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.diagnostic_route IS 'Allowlisted hypothesis edges. test_verb names a registered read capability; raw SQL is deliberately not stored or executed from this table.';


--
-- Name: doc_template; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.doc_template (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    slug text NOT NULL,
    name text NOT NULL,
    source_path text NOT NULL,
    template_version text NOT NULL,
    field_map jsonb NOT NULL,
    output_kinds text[] DEFAULT '{working,pdf}'::text[] NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL
);


--
-- Name: doctrine_change_item; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.doctrine_change_item (
    change_set_id uuid NOT NULL,
    section_id uuid NOT NULL,
    op text DEFAULT 'write'::text NOT NULL,
    expected_version bigint NOT NULL,
    proposed_body jsonb,
    CONSTRAINT doctrine_change_item_op_check CHECK ((op = ANY (ARRAY['write'::text, 'move'::text, 'retire'::text])))
);


--
-- Name: doctrine_change_set; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.doctrine_change_set (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    actor_id uuid NOT NULL,
    session_key text,
    idempotency_key text NOT NULL,
    state text DEFAULT 'prepared'::text NOT NULL,
    gate_run_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    committed_at timestamp with time zone,
    CONSTRAINT doctrine_change_set_state_check CHECK ((state = ANY (ARRAY['prepared'::text, 'committed'::text, 'rejected'::text])))
);


--
-- Name: TABLE doctrine_change_set; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.doctrine_change_set IS 'Multi-section atomic write unit: every section in the set commits in one transaction or none do (the half-applied-SOP-rename preventer). Replaying an idempotency_key returns the original result, never a second revision.';


--
-- Name: doctrine_claim; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.doctrine_claim (
    section_id uuid NOT NULL,
    holder_actor_id uuid NOT NULL,
    holder_session_key text NOT NULL,
    purpose text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE doctrine_claim; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.doctrine_claim IS 'Cooperative expiring claims (default TTL 300s, max 1800s): a foreign unexpired claim blocks a write so two agents do not spend tokens preparing the same section. Never a correctness mechanism — base_version is. Expired claims are free; no sweeper needed (expires_at predicate).';


--
-- Name: doctrine_document; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.doctrine_document (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    slug text NOT NULL,
    title text NOT NULL,
    content_class text NOT NULL,
    visibility text DEFAULT 'shared'::text NOT NULL,
    owner_actor_id uuid,
    review_policy_id uuid,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT doctrine_document_content_class_check CHECK ((content_class = ANY (ARRAY['playbook'::text, 'sop'::text, 'index'::text, 'reference'::text, 'dossier_narrative'::text, 'distillation'::text, 'rule'::text]))),
    CONSTRAINT doctrine_document_visibility_check CHECK ((visibility = ANY (ARRAY['shared'::text, 'personal'::text])))
);


--
-- Name: TABLE doctrine_document; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.doctrine_document IS 'A prose document migrated out of vault markdown (0075, doctrine-store P1). content_class ''rule'' is RESERVED for the P7 rule-store study — nothing writes it before that ruling.';


--
-- Name: doctrine_edge; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.doctrine_edge (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    source_section_id uuid NOT NULL,
    target_section_id uuid NOT NULL,
    edge_type text NOT NULL,
    scope jsonb DEFAULT '{}'::jsonb NOT NULL,
    introduced_by_revision_id uuid,
    retired_by_revision_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: doctrine_edge_type; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.doctrine_edge_type (
    edge_type text NOT NULL,
    acyclic boolean NOT NULL,
    precedence_rank integer,
    description text NOT NULL
);


--
-- Name: doctrine_gate_check; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.doctrine_gate_check (
    check_key text NOT NULL,
    description text NOT NULL,
    severity text NOT NULL,
    applies_to jsonb DEFAULT '{}'::jsonb NOT NULL,
    impl_key text NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT doctrine_gate_check_severity_check CHECK ((severity = ANY (ARRAY['block'::text, 'warn'::text])))
);


--
-- Name: TABLE doctrine_gate_check; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.doctrine_gate_check IS 'The validation registry: a check is a code function (impl_key, deployed with the connector) plus this row. A NEW GATE IS A FUNCTION AND A ROW, never a verb rewrite. Only deterministic synchronous checks may be severity=block; a block finding aborts the commit transaction.';


--
-- Name: doctrine_gate_finding; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.doctrine_gate_finding (
    run_id uuid NOT NULL,
    check_key text NOT NULL,
    severity text NOT NULL,
    passed boolean NOT NULL,
    message text NOT NULL,
    path text DEFAULT ''::text NOT NULL
);


--
-- Name: doctrine_gate_run; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.doctrine_gate_run (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    change_set_id uuid,
    dry_run boolean DEFAULT false NOT NULL,
    actor_id uuid NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    result text,
    report jsonb DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT doctrine_gate_run_result_check CHECK ((result = ANY (ARRAY['pass'::text, 'fail'::text])))
);


--
-- Name: doctrine_link; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.doctrine_link (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    source_section_id uuid NOT NULL,
    target_kind text NOT NULL,
    target_id uuid NOT NULL,
    role text DEFAULT 'citation'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT doctrine_link_role_check CHECK ((role = ANY (ARRAY['citation'::text, 'related'::text, 'example'::text, 'source'::text]))),
    CONSTRAINT doctrine_link_target_kind_check CHECK ((target_kind = ANY (ARRAY['doctrine_document'::text, 'doctrine_section'::text, 'party'::text, 'deal'::text, 'decision'::text, 'rule'::text, 'loop'::text, 'capture'::text])))
);


--
-- Name: doctrine_meta; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.doctrine_meta (
    id integer NOT NULL,
    generation bigint DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT doctrine_meta_id_check CHECK ((id = 1))
);


--
-- Name: TABLE doctrine_meta; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.doctrine_meta IS 'Singleton generation counter: bumps once per successful doctrine commit. The cache key and the snapshot coherence token for fleet reads.';


--
-- Name: doctrine_migration_batch; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.doctrine_migration_batch (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    batch_no integer NOT NULL,
    phase text NOT NULL,
    source_paths text[] NOT NULL,
    source_hashes jsonb DEFAULT '{}'::jsonb NOT NULL,
    state text DEFAULT 'pending'::text NOT NULL,
    row_counts jsonb,
    error text,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    CONSTRAINT doctrine_migration_batch_phase_check CHECK ((phase = ANY (ARRAY['forced_early'::text, 'bounded'::text, 'cutoff'::text]))),
    CONSTRAINT doctrine_migration_batch_state_check CHECK ((state = ANY (ARRAY['pending'::text, 'running'::text, 'verified'::text, 'failed'::text])))
);


--
-- Name: TABLE doctrine_migration_batch; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.doctrine_migration_batch IS 'The migration ledger (P4/P5): every former vault path is accounted for in exactly one batch, with pre-import source hashes so reconciliation is a comparison, not a memory. The cutoff batch closes dual-read.';


--
-- Name: doctrine_review_policy; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.doctrine_review_policy (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    max_age_days integer,
    revalidate_on_dep_change boolean DEFAULT true NOT NULL,
    content_classes text[]
);


--
-- Name: TABLE doctrine_review_policy; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.doctrine_review_policy IS 'Staleness is COMPUTED (review_after cursor on the section), never lifecycle rows — the council held the no-CMS verdict at trajectory scale. max_age_days null = no calendar staleness.';


--
-- Name: doctrine_revision; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.doctrine_revision (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    section_id uuid NOT NULL,
    version bigint NOT NULL,
    parent_revision_id uuid,
    change_set_id uuid,
    actor_id uuid NOT NULL,
    session_key text,
    body jsonb NOT NULL,
    plain_text text NOT NULL,
    content_hash text NOT NULL,
    commit_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig, plain_text)) STORED
);


--
-- Name: TABLE doctrine_revision; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.doctrine_revision IS 'Append-only. body is the structured form (JSON schema per content_class, enforced by the body_schema gate); plain_text is the searchable rendering of the same content. content_hash detects no-op writes and drift.';


--
-- Name: doctrine_section; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.doctrine_section (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    document_id uuid NOT NULL,
    section_key text NOT NULL,
    title text,
    ordinal integer DEFAULT 0 NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    current_revision_id uuid,
    current_version bigint DEFAULT 0 NOT NULL,
    body_hash text,
    review_after timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT doctrine_section_status_check CHECK ((status = ANY (ARRAY['active'::text, 'retired'::text])))
);


--
-- Name: TABLE doctrine_section; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.doctrine_section IS 'Stable-address unit of doctrine. section_key never changes; reordering changes ordinal only. current_version is the optimistic-concurrency token every write verb must present (base_version), the same contract as the rest of the record layer.';


--
-- Name: doctrine_slug_alias; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.doctrine_slug_alias (
    alias_slug text NOT NULL,
    document_id uuid NOT NULL,
    replaced_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE doctrine_slug_alias; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.doctrine_slug_alias IS 'Old slugs keep resolving after a rename — prompts, logs and links carry slugs for years (trajectory verdict: slug history ships day one).';


--
-- Name: doctrine_snapshot; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.doctrine_snapshot (
    document_id uuid NOT NULL,
    generation bigint NOT NULL,
    snapshot_json jsonb NOT NULL,
    content_hash text NOT NULL,
    built_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE doctrine_snapshot; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.doctrine_snapshot IS 'Read-through cache in Postgres (day-one fleet answer; Redis stays behind its measured trigger). Rebuilt on commit; doc.read prefers it.';


--
-- Name: document; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.document (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    template_id uuid NOT NULL,
    deal_id uuid,
    client_id uuid,
    prepared_at timestamp with time zone DEFAULT now() NOT NULL,
    prepared_by uuid NOT NULL,
    working_attachment uuid,
    pdf_attachment uuid,
    lint_passed boolean,
    leak_check_passed boolean,
    sent_status text DEFAULT 'draft'::text NOT NULL,
    note text,
    CONSTRAINT document_sent_status_check CHECK ((sent_status = ANY (ARRAY['draft'::text, 'handed_to_joe'::text, 'sent'::text])))
);


--
-- Name: event; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    actor_id uuid NOT NULL,
    verb text NOT NULL,
    subject_type text NOT NULL,
    subject_id uuid NOT NULL,
    field text,
    old_value jsonb,
    new_value jsonb,
    cause text NOT NULL,
    human_quote text,
    agent_rationale text,
    idempotency_key text,
    via text,
    client_id text,
    sponsoring_human_slug text,
    personal_scope text,
    authorization_class text,
    organization_tenant_id text,
    correlation_id uuid,
    CONSTRAINT event_cause_check CHECK ((cause = ANY (ARRAY['human_stated'::text, 'human_correction'::text, 'ingest_email'::text, 'ingest_calendar'::text, 'ingest_webhook'::text, 'import_migration'::text, 'import_salesforce'::text, 'automation_job'::text, 'learning_job'::text, 'system'::text]))),
    CONSTRAINT event_personal_scope_check CHECK (((personal_scope IS NULL) OR (personal_scope = ANY (ARRAY['joe-personal'::text, 'dell-personal'::text, 'none'::text])))),
    CONSTRAINT event_sponsoring_human_slug_check CHECK (((sponsoring_human_slug IS NULL) OR (sponsoring_human_slug = ANY (ARRAY['joe'::text, 'dell'::text]))))
);


--
-- Name: COLUMN event.via; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event.via IS 'How the caller authenticated (oauth-google | partner-token-legacy). Server-derived from the grant props; never accepted as a verb argument. Null on rows written before 0037. This is NOT a human-vs-agent flag — read human_quote for that.';


--
-- Name: COLUMN event.client_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event.client_id IS 'The OAuth client holding the grant: which SURFACE made the write. Server-derived, not caller-assertable. Null before 0037 and on the legacy token path, which has no client.';


--
-- Name: COLUMN event.sponsoring_human_slug; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event.sponsoring_human_slug IS 'Verified partner sponsoring the runtime agent. Null means unsponsored or pre-0095 unknown.';


--
-- Name: COLUMN event.personal_scope; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event.personal_scope IS 'Rule brain loaded for this event: joe-personal, dell-personal, none, or null before 0095.';


--
-- Name: COLUMN event.authorization_class; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event.authorization_class IS 'Server-derived authority class, distinct from request-side operational profile.';


--
-- Name: COLUMN event.organization_tenant_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event.organization_tenant_id IS 'Server-derived CARR organization tenant. Null before 0095 is historically unknown.';


--
-- Name: COLUMN event.correlation_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.event.correlation_id IS 'Same column, same source, same nullability reasoning as tool_call.correlation_id — see that comment. Threaded through writeEvent() and log-decision''s own direct insert, the only two places that write this table.';


--
-- Name: experiment; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.experiment (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    hypothesis text NOT NULL,
    piece_ids uuid[],
    started_on date NOT NULL,
    verdict text,
    verdict_note text,
    decided_on date,
    CONSTRAINT experiment_verdict_check CHECK ((verdict = ANY (ARRAY['win'::text, 'loss'::text, 'inconclusive'::text])))
);


--
-- Name: export_run; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.export_run (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    target text NOT NULL,
    ran_at timestamp with time zone DEFAULT now() NOT NULL,
    row_count integer NOT NULL,
    checksum text NOT NULL,
    status text NOT NULL,
    file_sha text,
    CONSTRAINT export_run_status_check CHECK ((status = ANY (ARRAY['ok'::text, 'failed'::text, 'validation_failed'::text])))
);


--
-- Name: COLUMN export_run.file_sha; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.export_run.file_sha IS 'sha256 of the WRITTEN FILE''s bytes at export (0073). The data checksum proves what the DB said; this proves what the file was. A live file whose bytes no longer match was edited by something other than the exporter — the V-BNK-050 clobber class, now machine-detectable.';


--
-- Name: growth_snapshot; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.growth_snapshot (
    taken_on date NOT NULL,
    table_name text NOT NULL,
    row_count bigint NOT NULL
);


--
-- Name: TABLE growth_snapshot; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.growth_snapshot IS 'Daily row counts for the accumulating tables (0071). Written by the health check (carr_jobs); v_growth_slope reads consecutive snapshots. Append-only by convention; a day''s row is idempotent via ON CONFLICT DO NOTHING.';


--
-- Name: ingest_inbox; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ingest_inbox (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    source text NOT NULL,
    external_id text,
    payload jsonb NOT NULL,
    status text DEFAULT 'new'::text NOT NULL,
    filed_refs jsonb,
    triage_note text,
    CONSTRAINT ingest_inbox_status_check CHECK ((status = ANY (ARRAY['new'::text, 'triaged'::text, 'filed'::text, 'rejected'::text, 'duplicate'::text])))
);


--
-- Name: investigation_branch; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.investigation_branch (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    run_id uuid NOT NULL,
    parent_branch_id uuid,
    route_key text NOT NULL,
    depth integer NOT NULL,
    hypothesis text NOT NULL,
    test_input jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    effect_size numeric,
    adjudication text,
    adjudicated_by uuid,
    adjudicated_at timestamp with time zone,
    opened_by uuid NOT NULL,
    opened_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT investigation_branch_check CHECK ((((status = 'open'::text) AND (adjudicated_at IS NULL) AND (adjudicated_by IS NULL)) OR ((status <> 'open'::text) AND (adjudicated_at IS NOT NULL) AND (adjudicated_by IS NOT NULL)))),
    CONSTRAINT investigation_branch_depth_check CHECK (((depth >= 1) AND (depth <= 6))),
    CONSTRAINT investigation_branch_hypothesis_check CHECK ((btrim(hypothesis) <> ''::text)),
    CONSTRAINT investigation_branch_status_check CHECK ((status = ANY (ARRAY['open'::text, 'verified'::text, 'rejected'::text, 'pruned'::text, 'inconclusive'::text]))),
    CONSTRAINT investigation_branch_test_input_check CHECK ((jsonb_typeof(test_input) = 'object'::text))
);


--
-- Name: investigation_evidence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.investigation_evidence (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    branch_id uuid NOT NULL,
    contributor_id uuid NOT NULL,
    scope text NOT NULL,
    query_or_tool text NOT NULL,
    raw_facts jsonb NOT NULL,
    evidence_refs jsonb NOT NULL,
    uncertainty text,
    nothing_found boolean DEFAULT false NOT NULL,
    exclusions jsonb DEFAULT '[]'::jsonb NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT investigation_evidence_check CHECK ((nothing_found OR (jsonb_array_length(evidence_refs) > 0))),
    CONSTRAINT investigation_evidence_check1 CHECK ((nothing_found OR (jsonb_array_length(raw_facts) > 0))),
    CONSTRAINT investigation_evidence_evidence_refs_check CHECK ((jsonb_typeof(evidence_refs) = 'array'::text)),
    CONSTRAINT investigation_evidence_exclusions_check CHECK ((jsonb_typeof(exclusions) = 'array'::text)),
    CONSTRAINT investigation_evidence_query_or_tool_check CHECK ((btrim(query_or_tool) <> ''::text)),
    CONSTRAINT investigation_evidence_raw_facts_check CHECK ((jsonb_typeof(raw_facts) = 'array'::text)),
    CONSTRAINT investigation_evidence_scope_check CHECK ((btrim(scope) <> ''::text))
);


--
-- Name: TABLE investigation_evidence; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.investigation_evidence IS 'Worker return packets: scoped raw facts and evidence only. Global recommendations are absent by schema so the investigation owner retains coherent judgment.';


--
-- Name: investigation_run; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.investigation_run (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    signal_id uuid NOT NULL,
    objective text NOT NULL,
    owner_actor_id uuid NOT NULL,
    max_depth integer DEFAULT 3 NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    conclusion text,
    confidence numeric,
    strongest_alternative text,
    alternative_disposition text,
    termination_reason text,
    opened_at timestamp with time zone DEFAULT now() NOT NULL,
    closed_at timestamp with time zone,
    CONSTRAINT investigation_run_check CHECK ((((status = 'open'::text) AND (closed_at IS NULL)) OR ((status <> 'open'::text) AND (closed_at IS NOT NULL)))),
    CONSTRAINT investigation_run_confidence_check CHECK (((confidence >= (0)::numeric) AND (confidence <= (1)::numeric))),
    CONSTRAINT investigation_run_max_depth_check CHECK (((max_depth >= 1) AND (max_depth <= 6))),
    CONSTRAINT investigation_run_objective_check CHECK ((btrim(objective) <> ''::text)),
    CONSTRAINT investigation_run_status_check CHECK ((status = ANY (ARRAY['open'::text, 'completed'::text, 'abandoned'::text]))),
    CONSTRAINT investigation_run_termination_reason_check CHECK ((termination_reason = ANY (ARRAY['root_cause_found'::text, 'budget_exhausted'::text, 'insufficient_evidence'::text, 'signal_invalid'::text, 'superseded'::text])))
);


--
-- Name: lead; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lead (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    registry_ref text,
    party_id uuid NOT NULL,
    lane text,
    stage text NOT NULL,
    score numeric(5,2),
    source_type text,
    suppressed boolean DEFAULT false NOT NULL,
    est_lease_event date,
    last_touch date,
    next_action_date date,
    client_id uuid,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid NOT NULL,
    source_detail text,
    segment text,
    report_back_due date,
    drip_campaign text,
    drip_added date,
    sf_deal text,
    notes_path text,
    notes text,
    event_source text,
    event_confidence text,
    owner_id uuid,
    owner_label text,
    est_lease_event_raw text,
    report_back_due_raw text,
    drip_added_raw text
);


--
-- Name: lead_lane; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lead_lane (
    slug text NOT NULL,
    label text NOT NULL
);


--
-- Name: lead_stage; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lead_stage (
    slug text NOT NULL,
    label text NOT NULL,
    sort integer DEFAULT 100 NOT NULL
);


--
-- Name: TABLE lead_stage; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.lead_stage IS 'The lead funnel vocabulary. closed_lost and do_not_contact (0087) are the two TERMINAL stages and they are deliberately distinct: closed_lost is an ordinary outcome and is re-openable, because a practice that renewed this year is a real prospect in three. do_not_contact is a standing instruction from the human and must survive every future sweep, import and re-score, which is why the verb that sets it also sets lead.suppressed rather than relying on the stage alone.';


--
-- Name: lease; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lease (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    deal_id uuid,
    client_id uuid,
    premises_id uuid,
    executed_on date,
    commencement_on date,
    expiration_on date,
    term_months integer,
    rate_amount numeric(12,2),
    rate_basis text,
    rate_norm_sf_yr numeric(12,2) GENERATED ALWAYS AS (
CASE rate_basis
    WHEN 'usd_sf_yr'::text THEN rate_amount
    WHEN 'usd_sf_mo'::text THEN (rate_amount * (12)::numeric)
    ELSE NULL::numeric
END) STORED,
    escalator text,
    ti_amount numeric(12,2),
    free_rent_months numeric(4,1),
    options_note text,
    opex_structure text,
    doc_attachment uuid,
    source text DEFAULT 'stated'::text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid,
    CONSTRAINT lease_check CHECK (((rate_amount IS NULL) OR (rate_basis IS NOT NULL))),
    CONSTRAINT lease_rate_amount_check CHECK ((rate_amount > (0)::numeric)),
    CONSTRAINT lease_rate_basis_check CHECK ((rate_basis = ANY (ARRAY['usd_sf_yr'::text, 'usd_sf_mo'::text, 'usd_mo_gross'::text, 'usd_yr_gross'::text]))),
    CONSTRAINT lease_term_months_check CHECK (((term_months >= 1) AND (term_months <= 480)))
);


--
-- Name: loop_block; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.loop_block (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    rel_path text NOT NULL,
    kind text NOT NULL,
    seq integer NOT NULL,
    block_key text,
    prose_md text DEFAULT ''::text NOT NULL,
    header_cols text[],
    col_order text[],
    renders_closed boolean DEFAULT false NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid NOT NULL,
    CONSTRAINT loop_block_header_needs_columns CHECK (((header_cols IS NULL) OR (col_order IS NOT NULL))),
    CONSTRAINT loop_block_kind_check CHECK ((kind = ANY (ARRAY['open_loop'::text, 'team_loop'::text, 'action_required'::text, 'idea'::text]))),
    CONSTRAINT loop_block_prose_has_no_columns CHECK (((block_key IS NULL) = (col_order IS NULL)))
);


--
-- Name: TABLE loop_block; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.loop_block IS 'File scaffolding for the generated loop renders: the prose, the section order and the table headers of open-loops.md, open-loops-backlog.md, action-required.md and team-loops.md, stored as data so the render reproduces the file and the doctrine prose stays editable by the human rather than by a code change.';


--
-- Name: COLUMN loop_block.col_order; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.loop_block.col_order IS 'Positional semantic column names. Vocabulary: number, owner, title, body, since_text, unblocks, source_note, closed_text, outcome, and extra:<key> for a cell the canonical set has no home for. A row may override this with its own col_order when the source row''s width disagrees with the header.';


--
-- Name: loop_domain; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.loop_domain (
    slug text NOT NULL,
    label text NOT NULL,
    sort integer NOT NULL
);


--
-- Name: loop_item; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.loop_item (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    kind text NOT NULL,
    number text NOT NULL,
    block_id uuid NOT NULL,
    render_seq integer NOT NULL,
    col_order text[],
    title text,
    body text,
    owner text,
    since_text text,
    unblocks text,
    source_note text,
    closed_text text,
    outcome text,
    extra_cells jsonb DEFAULT '{}'::jsonb NOT NULL,
    marker text DEFAULT 'none'::text NOT NULL,
    marker_literal text,
    due_on date,
    drift_critical boolean DEFAULT false NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    close_outcome text,
    closed_by uuid,
    closed_at timestamp with time zone,
    tier text NOT NULL,
    personal_to uuid,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid NOT NULL,
    domain text,
    blocker_class text,
    blocker_detail text,
    CONSTRAINT loop_item_blocker_class_known CHECK (((blocker_class IS NULL) OR (blocker_class = ANY (ARRAY['human_only'::text, 'counterparty'::text, 'ruling'::text, 'external_event'::text, 'other_lane'::text, 'capability'::text])))),
    CONSTRAINT loop_item_blocker_detail_present CHECK (((blocker_class IS NULL) OR ((blocker_detail IS NOT NULL) AND (length(btrim(blocker_detail)) >= 12)))),
    CONSTRAINT loop_item_closed_has_outcome CHECK (((status = 'open'::text) OR (close_outcome IS NOT NULL))),
    CONSTRAINT loop_item_closed_stamped CHECK (((status = 'open'::text) = (closed_at IS NULL))),
    CONSTRAINT loop_item_kind_check CHECK ((kind = ANY (ARRAY['open_loop'::text, 'team_loop'::text, 'action_required'::text, 'idea'::text]))),
    CONSTRAINT loop_item_marker_check CHECK ((marker = ANY (ARRAY['bell'::text, 'dated'::text, 'decision'::text, 'none'::text]))),
    CONSTRAINT loop_item_owner_known CHECK (((owner IS NULL) OR (owner = ANY (ARRAY['joe'::text, 'dell'::text, 'claude'::text, 'joint'::text])))),
    CONSTRAINT loop_item_personal_tier CHECK (((tier = 'personal'::text) = (personal_to IS NOT NULL))),
    CONSTRAINT loop_item_status_check CHECK ((status = ANY (ARRAY['open'::text, 'done'::text, 'dropped'::text]))),
    CONSTRAINT loop_item_tier_check CHECK ((tier = ANY (ARRAY['personal'::text, 'shared'::text])))
);


--
-- Name: TABLE loop_item; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.loop_item IS 'The three markdown accumulators as records (one-writer Phase A). One row per item in open-loops.md, open-loops-backlog.md, action-required.md and team-loops.md. Items change via the loop verbs (add-loop, update-loop, close-loop); the four files are rendered views of this table. NO SESSION HAND-EDITS THOSE FOUR FILES after the live flip.';


--
-- Name: COLUMN loop_item.kind; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.loop_item.kind IS 'open_loop / team_loop / action_required (0024) plus idea (0031, ORDER 40): a candidate idea awaiting a decision to act, rendered into 00_Context/idea-bank.md. An idea is deliberately NOT an open_loop — the bank holds what has no owner and no commitment yet, which is the distinction the file was created to preserve.';


--
-- Name: COLUMN loop_item.number; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.loop_item.number IS 'The visible ref, verbatim and NOT unique — the source files contain real collisions (#111 twice in open-loops.md; #103/#95/#88/#108 across hot and backlog; T34 across Open and Done). Renumbering is a content change nobody ruled, so the collisions are reported, not resolved here.';


--
-- Name: COLUMN loop_item.owner; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.loop_item.owner IS 'An ownership LABEL as the file states it (Joe/Claude, Joe→Dell, Dell''s brain→Joe), not a foreign key. Resolving it to actor rows would drop what the label says.';


--
-- Name: COLUMN loop_item.close_outcome; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.loop_item.close_outcome IS 'Required to leave open. A closed row with no outcome is how the asker stops finding out, which is the failure team-loops was built to end.';


--
-- Name: COLUMN loop_item.domain; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.loop_item.domain IS 'deals | prospecting | networking | marketing | business | system (loop_domain, Joe''s vocabulary 2026-08-02). NULL = not yet classified, and that renders as its own unsorted section rather than defaulting into a domain — a guessed classification would bury exactly what this column exists to surface. BOUNDARY RULE: classify by WHAT THE WORK IS, not who appears in it. A vendor introducing a PROSPECT normally means real intent and goes straight to DEALS; it is PROSPECTING only while no deal has formed and it is still conversion work (the Renalus C-125 case). A vendor introducing a VENDOR is networking. Connecting a prospect to a vendor is networking (reciprocity). Connecting a client to a vendor on a LIVE deal is deals. Prospecting is drawn narrowly on purpose: it carries the most volume, so anything adjacent that lands there drowns the lead work it exists to hold.';


--
-- Name: COLUMN loop_item.blocker_class; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.loop_item.blocker_class IS 'Why the session that opened this loop could not do the work itself, from a closed list of states of the world OUTSIDE the session: human_only, counterparty, ruling, external_event, other_lane, capability. NULL on rows opened before migration 0081 (2026-08-09) and on kinds the gate does not cover (team_loop, action_required, idea). There is deliberately no value meaning "later" — a session that cannot name one of these can do the work.';


--
-- Name: COLUMN loop_item.blocker_detail; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.loop_item.blocker_detail IS 'The specific thing named: which person, which ruling, which date, which credential. "the landlord" is not a counterparty; "Sanders, the listing broker on C-112" is. Required by add-loop whenever blocker_class is set.';


--
-- Name: CONSTRAINT loop_item_owner_known ON loop_item; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON CONSTRAINT loop_item_owner_known ON public.loop_item IS 'One spelling per owner. Added 0089 after Joe 31 / joe 22 and Claude 4 / claude 92 meant every owner filter returned a fraction of the pile — including the autonomous drain queue, which selects on owner=claude and had been silently omitting four rows it was entitled to work. NOT VALID on purpose: it binds every future write without failing the migration on any historical row a human still needs to look at.';


--
-- Name: marketing_subject; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.marketing_subject (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    subject_type text NOT NULL,
    slug text NOT NULL,
    label text NOT NULL,
    note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid,
    retired_at timestamp with time zone,
    CONSTRAINT marketing_subject_slug_check CHECK (((slug = lower(btrim(slug))) AND (slug <> ''::text) AND (slug !~ '\s'::text))),
    CONSTRAINT marketing_subject_subject_type_check CHECK ((subject_type = ANY (ARRAY['platform'::text, 'pillar'::text, 'format'::text])))
);


--
-- Name: TABLE marketing_subject; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.marketing_subject IS 'The non-party things a marketing finding can be about (0066): a PLATFORM, a content PILLAR, a FORMAT. One stable uuid each, so record_flag''s existing (subject_type, subject_id) pointer reaches them unchanged — this table exists to supply the uuid, not to introduce a parallel addressing scheme. A campaign is NOT in here: it already has a uuid. Rows are seeded ONLY from values the record already contains; the pillar branch is deliberately empty because zero pillars are evidenced anywhere in the record layer and seeding a taxonomy would invent the very judgment this table is meant to hold. Retire by setting retired_at, never by deleting: a finding filed against a platform must stay readable after the platform stops being used.';


--
-- Name: media_recommendation; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.media_recommendation (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    title text NOT NULL,
    author text,
    kind text DEFAULT 'book'::text NOT NULL,
    why text NOT NULL,
    observed_pattern text NOT NULL,
    tags text[] DEFAULT '{}'::text[] NOT NULL,
    priority text DEFAULT 'normal'::text NOT NULL,
    status text DEFAULT 'new'::text NOT NULL,
    recommended_on date DEFAULT CURRENT_DATE NOT NULL,
    finished_on date,
    personal_to text DEFAULT 'joe'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid,
    version integer DEFAULT 1 NOT NULL,
    CONSTRAINT media_recommendation_finish_shape CHECK (((status = 'done'::text) = (finished_on IS NOT NULL))),
    CONSTRAINT media_recommendation_kind_check CHECK ((kind = ANY (ARRAY['book'::text, 'media'::text, 'article'::text, 'course'::text]))),
    CONSTRAINT media_recommendation_priority_check CHECK ((priority = ANY (ARRAY['now'::text, 'normal'::text]))),
    CONSTRAINT media_recommendation_status_check CHECK ((status = ANY (ARRAY['new'::text, 'reading'::text, 'done'::text, 'dropped'::text]))),
    CONSTRAINT media_recommendation_why_is_real CHECK (((btrim(why) <> ''::text) AND (btrim(observed_pattern) <> ''::text)))
);


--
-- Name: TABLE media_recommendation; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.media_recommendation IS 'What Doc recommends a partner read or watch, and why (0111, loop #122). The curriculum board renders FROM here — it claimed this table as its source of truth from the day it shipped and the table did not exist, so the page was hand-edited HTML asserting a provenance it did not have. why and observed_pattern are both NOT NULL because the board''s premise is that an item appears only when a pattern earns one, and a row that cannot say why it exists must not be writable.';


--
-- Name: national_account_owner; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.national_account_owner (
    account_client_id uuid NOT NULL,
    owner_actor_id uuid NOT NULL,
    set_by uuid NOT NULL,
    set_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE national_account_owner; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.national_account_owner IS 'The partner accountable for a national-account portfolio. This is not the owner of every market deal; individual deals keep their own owner.';


--
-- Name: negotiation_claim; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.negotiation_claim (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    round_id uuid NOT NULL,
    claim_type text NOT NULL,
    loggable boolean DEFAULT false NOT NULL,
    stated_floor numeric(12,2),
    stated_floor_basis text,
    quote text,
    note text,
    source text DEFAULT 'stated'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL,
    CONSTRAINT negotiation_claim_check CHECK (((stated_floor IS NULL) OR (stated_floor_basis IS NOT NULL))),
    CONSTRAINT negotiation_claim_loggable_check CHECK ((loggable IS FALSE)),
    CONSTRAINT negotiation_claim_stated_floor_basis_check CHECK (((stated_floor_basis IS NULL) OR (stated_floor_basis = ANY (ARRAY['usd_sf_yr'::text, 'usd_sf_mo'::text, 'usd_mo_gross'::text, 'usd_yr_gross'::text, 'usd_total'::text, 'usd_sf_total'::text])))),
    CONSTRAINT negotiation_claim_stated_floor_check CHECK (((stated_floor IS NULL) OR (stated_floor > (0)::numeric)))
);


--
-- Name: TABLE negotiation_claim; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.negotiation_claim IS 'One row per claim a side made ABOUT its own position on a given round (0063) — "best and final", "the owner will not go below X", "we walk Friday". An OBSERVATION, never a characterisation: nothing here says what anyone is like, only what they said and when. Whether a claim was later contradicted is computed in v_counterparty_bluff from the rounds that followed, and is stored nowhere. A round may carry several claims, which is why this is a table and not a column — a side routinely makes three at once and a single enum column would keep one and discard two.';


--
-- Name: COLUMN negotiation_claim.claim_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.negotiation_claim.claim_type IS 'References negotiation_claim_type, but only its derived=false rows: the composite FK (claim_type, loggable) forbids claim_type = ''deadline'' because a deadline already lives on negotiation_round.expires_on and two homes for one fact is the 0045 fault.';


--
-- Name: COLUMN negotiation_claim.stated_floor; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.negotiation_claim.stated_floor IS 'The number named in an authority claim, when it differs from the round''s own rate — "the owner will not go below 18" while offering 19. NULL means the claim was about the round''s own position, and v_counterparty_bluff falls back to that. Never a guess.';


--
-- Name: COLUMN negotiation_claim.quote; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.negotiation_claim.quote IS 'Their words, as close to verbatim as was heard. Evidence for a reader, never parsed: no view reads this column and no score may ever depend on its text.';


--
-- Name: negotiation_claim_type; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.negotiation_claim_type (
    slug text NOT NULL,
    label text NOT NULL,
    falsifiable boolean NOT NULL,
    derived boolean DEFAULT false NOT NULL,
    reversal_test text NOT NULL,
    sort integer NOT NULL
);


--
-- Name: TABLE negotiation_claim_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.negotiation_claim_type IS 'What a side can CLAIM about its own position, as rows (0063; the 0017 vocabulary-as-rows pattern). Two columns carry rules that would otherwise be hardcoded in a view: falsifiable says whether the claim can ever be checked, and derived says the record already holds it elsewhere. 0052 hardcoded a kind list into a view and 0053 had to rewrite the view to correct the list — this is that mistake, not repeated.';


--
-- Name: COLUMN negotiation_claim_type.falsifiable; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.negotiation_claim_type.falsifiable IS 'FALSE means no observation could ever contradict this claim, so it must never move a score. Exactly one row is false today: competing_interest. v_counterparty_bluff filters on THIS COLUMN, so marking a future claim type unfalsifiable is enough to keep it out of the numbers — no view edit, no second copy of the rule.';


--
-- Name: COLUMN negotiation_claim_type.derived; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.negotiation_claim_type.derived IS 'TRUE means the record already holds this claim on another column and logging it here would be a second home for one fact. Only deadline, which IS negotiation_round.expires_on. The composite FK from negotiation_claim makes inserting a derived type impossible; the 0063 guard proves the refusal by attempting it. Flipping a type from false to true while claim rows exist is refused by that same FK, which is correct — you cannot retroactively declare a logged class derived.';


--
-- Name: negotiation_round; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.negotiation_round (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    deal_id uuid NOT NULL,
    round_no integer NOT NULL,
    side text NOT NULL,
    proposed_on date NOT NULL,
    rate_amount numeric(12,2),
    rate_basis text,
    rate_norm_sf_yr numeric(12,2) GENERATED ALWAYS AS (
CASE rate_basis
    WHEN 'usd_sf_yr'::text THEN rate_amount
    WHEN 'usd_sf_mo'::text THEN (rate_amount * (12)::numeric)
    ELSE NULL::numeric
END) STORED,
    ti_amount numeric(12,2),
    ti_basis text,
    free_rent_months numeric(4,1),
    term_months integer,
    options_note text,
    escalator text,
    opex_note text,
    expires_on date,
    note text,
    source text DEFAULT 'stated'::text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid,
    submarket_condition text,
    CONSTRAINT negotiation_round_check CHECK (((rate_amount IS NULL) OR (rate_basis IS NOT NULL))),
    CONSTRAINT negotiation_round_free_rent_months_check CHECK (((free_rent_months >= (0)::numeric) AND (free_rent_months <= (36)::numeric))),
    CONSTRAINT negotiation_round_rate_amount_check CHECK ((rate_amount > (0)::numeric)),
    CONSTRAINT negotiation_round_rate_basis_check CHECK ((rate_basis = ANY (ARRAY['usd_sf_yr'::text, 'usd_sf_mo'::text, 'usd_mo_gross'::text, 'usd_yr_gross'::text, 'usd_total'::text, 'usd_sf_total'::text]))),
    CONSTRAINT negotiation_round_round_no_check CHECK (((round_no >= 1) AND (round_no <= 99))),
    CONSTRAINT negotiation_round_side_check CHECK ((side = ANY (ARRAY['tenant'::text, 'landlord'::text, 'buyer'::text, 'seller'::text]))),
    CONSTRAINT negotiation_round_term_months_check CHECK (((term_months >= 1) AND (term_months <= 480))),
    CONSTRAINT negotiation_round_ti_basis_check CHECK ((ti_basis = ANY (ARRAY['usd_total'::text, 'usd_sf'::text])))
);


--
-- Name: COLUMN negotiation_round.rate_basis; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.negotiation_round.rate_basis IS 'What the rate_amount MEANS. Rent bases: usd_sf_yr, usd_sf_mo, usd_mo_gross, usd_yr_gross. Purchase bases (0022): usd_total = a total price; usd_sf_total = a total price stated per SF. Purchase bases carry no rate_norm_sf_yr by design — a price is not a rent and must never land in a rent comparison.';


--
-- Name: COLUMN negotiation_round.submarket_condition; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.negotiation_round.submarket_condition IS 'The submarket as it stood when THIS round was proposed (0063). On the round rather than the deal because a submarket moves mid-negotiation — a competing tenant appears in week three and the same building is a different building — and a deal-level column freezes the first answer anyone gave. It need only be recorded once: v_negotiation_deal reads the LATEST non-null value on the deal. NULL means not recorded, never "balanced".';


--
-- Name: next_action; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.next_action (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    subject_type text NOT NULL,
    subject_id uuid NOT NULL,
    owner_id uuid NOT NULL,
    due_on date,
    description text NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid,
    hold_until date,
    CONSTRAINT next_action_status_check CHECK ((status = ANY (ARRAY['open'::text, 'done'::text, 'dropped'::text]))),
    CONSTRAINT next_action_subject_type_check CHECK ((subject_type = ANY (ARRAY['deal'::text, 'client'::text, 'lead'::text, 'vendor'::text])))
);


--
-- Name: COLUMN next_action.hold_until; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.next_action.hold_until IS 'Explicit do-not-surface date. A row with hold_until in the future never appears in v_today_triage, however it is dated. Added 0032 because two rows carried "do not surface" as PROSE and the queue surfaced them anyway — an instruction living only in text is not an instruction the system can obey.';


--
-- Name: org_merge_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.org_merge_log (
    party_id uuid NOT NULL,
    from_org uuid NOT NULL,
    to_org uuid NOT NULL,
    identity_key text NOT NULL,
    merged_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE org_merge_log; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.org_merge_log IS 'Person → old org → surviving org, one row per repoint (0059). This is what makes the org consolidation reversible in effect: merged_into records that the org rows collapsed, but only this records WHICH person came from WHICH row, and the repoint destroys that fact otherwise. Reversal is two statements, spelled out in 0059''s header. Append-only; it is a record of what happened, not a work queue.';


--
-- Name: parcel; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.parcel (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    county text NOT NULL,
    state text NOT NULL,
    parcel_no text NOT NULL,
    source text NOT NULL
);


--
-- Name: participant_role; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.participant_role (
    slug text NOT NULL,
    label text NOT NULL,
    side text,
    CONSTRAINT participant_role_side_check CHECK ((side = ANY (ARRAY['actor'::text, 'party'::text])))
);


--
-- Name: TABLE participant_role; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.participant_role IS 'Who a party is ON a deal. investor and capital_partner exist because of Joe''s ruling on C-023 Tubbs (amendment 9): a vet who funds other vets'' startups for equity or profit share is a party to the deal, and referring_agent misstates him. Adding a role is now a row, not a migration.';


--
-- Name: COLUMN participant_role.side; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.participant_role.side IS 'Which side of the deal this role sits on, and therefore WHICH COLUMN of deal_participant carries its subject (0060). ''actor'' = a CARR employee, subject in actor_id, party_id MUST be null — role=''lead'' is the deal''s owning agent (joe or dell), which is what v_deal_board exposes as lead_owner and what set-lead writes. ''party'' = someone outside CARR, subject in party_id, actor_id MUST be null. NULL means the side has not been established and the row is left unconstrained; four roles have no rows and no call site, and referring_agent is genuinely ambiguous (a CARR actor or an outside broker are opposite sides of this table). Declare the side when the first real row appears, not before. Enforced by trg_deal_participant_side.';


--
-- Name: party; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.party (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    kind text NOT NULL,
    name text NOT NULL,
    org_id uuid,
    phone text,
    email text,
    city text,
    state text,
    npi text,
    notes_path text,
    merged_into uuid,
    deleted_at timestamp with time zone,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid NOT NULL,
    specialty text,
    county text,
    title text,
    ref text,
    contact_state text DEFAULT 'active'::text NOT NULL,
    contact_state_reason text,
    contact_state_until date,
    contact_state_cadence text,
    contact_state_set_by uuid,
    contact_state_set_at timestamp with time zone,
    cell text,
    CONSTRAINT party_kind_check CHECK ((kind = ANY (ARRAY['person'::text, 'org'::text])))
);


--
-- Name: COLUMN party.ref; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.party.ref IS 'P-#### — the PERSON''s stable identity, added 0046. Sits BENEATH the role refs rather than replacing them: L-/C-/V- keep working and keep meaning what they mean, so every document citing L-163 still resolves. Assigned by created_at order, so a re-run on a restored dump reproduces the same refs.';


--
-- Name: COLUMN party.contact_state; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.party.contact_state IS 'active | nurture | paused | do_not_contact. STORED, never derived — "this person asked not to be contacted" is a fact only a human knows and it must survive every stage change. Eligibility for a given campaign IS derived, from this plus role state; storing that instead is what produced the 0045 fault.';


--
-- Name: COLUMN party.contact_state_set_by; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.party.contact_state_set_by IS 'who recorded the state (who_initiated). Matters when the answer is do_not_contact: a client asking to be left alone and Joe deciding to pause outreach are different facts with different half-lives.';


--
-- Name: COLUMN party.cell; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.party.cell IS 'Mobile line, distinct from phone (office). Written by update-party-contact only — a narrow contact-facts verb; identity fields (name, org, npi, specialty) are deliberately out of its reach per rule 5d44d3f3. The placeholder rule applies: a CARR agent''s own number is never stored here.';


--
-- Name: party_link; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.party_link (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    from_party uuid NOT NULL,
    to_party uuid NOT NULL,
    kind text NOT NULL,
    note text,
    source text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL,
    via_party uuid,
    occurred_on date,
    CONSTRAINT party_link_check CHECK ((from_party <> to_party))
);


--
-- Name: COLUMN party_link.via_party; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.party_link.via_party IS 'WHO made the connection. Null = direct, no broker. Added 0051 because an introduction is ternary (A introduced B to C) and a binary edge cannot record "we introduced these two" — the reciprocity that earns referrals back, which was exactly the half Joe most wanted.';


--
-- Name: COLUMN party_link.occurred_on; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.party_link.occurred_on IS 'When it happened. An offer and a completed introduction are different events and the gap between them is the follow-up.';


--
-- Name: party_link_kind; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.party_link_kind (
    slug text NOT NULL,
    label text NOT NULL,
    sort integer NOT NULL
);


--
-- Name: TABLE party_link_kind; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.party_link_kind IS 'Intro-graph edge kinds (ORDER 18). SIX kinds after the 2026-07-31 mapping: intro_sent collapsed into intro and referred into referral — the same fact said twice is how two vocabularies start. party_link.kind FKs here; the link-parties verb validates against this table and has no enum of its own.';


--
-- Name: placement; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.placement (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    piece_id uuid NOT NULL,
    platform text NOT NULL,
    external_id text,
    url text,
    scheduled_at timestamp with time zone,
    live_at timestamp with time zone
);


--
-- Name: placement_measurement; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.placement_measurement (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    placement_id uuid NOT NULL,
    attempted_at timestamp with time zone DEFAULT now() NOT NULL,
    source text NOT NULL,
    outcome text NOT NULL,
    reason text,
    metric_kinds text[] DEFAULT '{}'::text[] NOT NULL,
    note text,
    recorded_by uuid NOT NULL,
    CONSTRAINT placement_measurement_check CHECK ((((outcome = 'recorded'::text) AND (cardinality(metric_kinds) > 0)) OR ((outcome = 'unavailable'::text) AND (cardinality(metric_kinds) = 0) AND (btrim(COALESCE(reason, ''::text)) <> ''::text)))),
    CONSTRAINT placement_measurement_outcome_check CHECK ((outcome = ANY (ARRAY['recorded'::text, 'unavailable'::text])))
);


--
-- Name: TABLE placement_measurement; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.placement_measurement IS 'One row per attempt to measure one placement (0066). The analogue of record-finding''s found:false, in the measurement domain: it makes "we pulled and the platform gave nothing" a RECORD rather than an absence, so it stops being indistinguishable from "nobody has pulled". As of 2026-08-02 that distinction covers 73 of 89 placements, including all 42 on X. Written by the measure-placement verb; pipelines/pull_placement_metrics.py should write one per pull too and does not yet.';


--
-- Name: placement_metric; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.placement_metric (
    placement_id uuid NOT NULL,
    observed_at timestamp with time zone DEFAULT now() NOT NULL,
    kind text NOT NULL,
    value numeric(14,2) NOT NULL,
    source text DEFAULT 'blotato_api'::text NOT NULL
);


--
-- Name: premises; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.premises (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    deal_id uuid,
    label text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL
);


--
-- Name: premises_space; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.premises_space (
    premises_id uuid NOT NULL,
    space_id uuid NOT NULL
);


--
-- Name: prospect_pool; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.prospect_pool AS
 SELECT id,
    source,
    source_key,
    source_seq,
    source_row,
    name,
    org_name,
    vertical,
    address,
    city,
    county,
    state,
    email,
    phone,
    segment,
    segment_play,
    score,
    score_basis,
    est_lease_event,
    est_basis,
    status,
    promoted_lead_id,
    dup_tier,
    dup_subject_type,
    dup_subject_id,
    dup_ref,
    dup_basis,
    dup_do_not_contact,
    version,
    created_at,
    created_by,
    updated_at,
    updated_by
   FROM public.candidate_pool;


--
-- Name: VIEW prospect_pool; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.prospect_pool IS 'DEPRECATED compatibility shim for the 0048 rename. Auto-updatable, so old callers keep working. Drop it once nothing references the old name — that drop is what proves the rename finished.';


--
-- Name: record_flag; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.record_flag (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    subject_type text NOT NULL,
    subject_id uuid NOT NULL,
    kind text NOT NULL,
    value jsonb NOT NULL,
    source text NOT NULL,
    observed_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_on date,
    created_by uuid NOT NULL,
    CONSTRAINT record_flag_subject_type_check CHECK ((subject_type = ANY (ARRAY['lead'::text, 'client'::text, 'vendor'::text, 'party'::text, 'deal'::text, 'campaign'::text, 'platform'::text, 'pillar'::text, 'format'::text, 'repo'::text, 'commit'::text])))
);


--
-- Name: record_source; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.record_source (
    entity_type text NOT NULL,
    entity_id uuid NOT NULL,
    source_system text NOT NULL,
    external_key text NOT NULL,
    imported_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ref_client_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ref_client_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ref_lead_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ref_lead_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ref_party_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ref_party_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ref_vendor_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ref_vendor_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: registration; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.registration (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    deal_id uuid NOT NULL,
    premises_id uuid,
    registered_with_party uuid NOT NULL,
    registered_on date NOT NULL,
    method text,
    doc_attachment uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL
);


--
-- Name: rule; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.rule (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    statement text NOT NULL,
    human_quote text,
    taught_by uuid NOT NULL,
    scope jsonb DEFAULT '{}'::jsonb NOT NULL,
    personal_to uuid,
    status text DEFAULT 'proposed'::text NOT NULL,
    activated_by uuid,
    activated_at timestamp with time zone,
    enforcement text DEFAULT 'prose'::text NOT NULL,
    supersedes uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT rule_check CHECK (((status <> 'active'::text) OR (activated_by IS NOT NULL))),
    CONSTRAINT rule_enforcement_check CHECK ((enforcement = ANY (ARRAY['prose'::text, 'checklist'::text, 'gate'::text, 'constraint'::text, 'code'::text]))),
    CONSTRAINT rule_status_check CHECK ((status = ANY (ARRAY['proposed'::text, 'active'::text, 'retired'::text, 'superseded'::text])))
);


--
-- Name: COLUMN rule.human_quote; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.rule.human_quote IS 'verbatim words of the teacher. IMMUTABLE once non-empty — amend-rule may fill a NULL, never overwrite. NULL means imported doctrine, not a paraphrase passed off as a quote.';


--
-- Name: COLUMN rule.version; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.rule.version IS '[A2] optimistic-concurrency token for amend-rule; bumped by trg_touch_row on every update';


--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_migrations (
    filename text NOT NULL,
    sha256 text NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: search_candidate; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.search_candidate (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    search_id uuid NOT NULL,
    premises_id uuid NOT NULL,
    tier text NOT NULL,
    rank integer,
    reason text NOT NULL,
    confirmed_by_joe boolean DEFAULT false NOT NULL,
    CONSTRAINT search_candidate_tier_check CHECK ((tier = ANY (ARRAY['tour'::text, 'look'::text, 'ruled_out'::text])))
);


--
-- Name: sensitive_blob; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sensitive_blob (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    value jsonb,
    scrubbed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: signal_event; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.signal_event (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    producer text NOT NULL,
    signal_key text NOT NULL,
    signal_kind text NOT NULL,
    subject_type text NOT NULL,
    subject_ref text NOT NULL,
    metric_name text NOT NULL,
    observed_value numeric NOT NULL,
    baseline_value numeric,
    threshold_value numeric NOT NULL,
    comparison text NOT NULL,
    severity text NOT NULL,
    detected_at timestamp with time zone NOT NULL,
    evidence_refs jsonb NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    created_by uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT signal_event_comparison_check CHECK ((comparison = ANY (ARRAY['gt'::text, 'gte'::text, 'lt'::text, 'lte'::text, 'delta_abs_gte'::text]))),
    CONSTRAINT signal_event_evidence_refs_check CHECK (((jsonb_typeof(evidence_refs) = 'array'::text) AND (jsonb_array_length(evidence_refs) > 0))),
    CONSTRAINT signal_event_metric_name_check CHECK ((btrim(metric_name) <> ''::text)),
    CONSTRAINT signal_event_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT signal_event_producer_check CHECK ((btrim(producer) <> ''::text)),
    CONSTRAINT signal_event_severity_check CHECK ((severity = ANY (ARRAY['info'::text, 'warning'::text, 'critical'::text]))),
    CONSTRAINT signal_event_signal_key_check CHECK ((btrim(signal_key) <> ''::text)),
    CONSTRAINT signal_event_signal_kind_check CHECK ((btrim(signal_kind) <> ''::text)),
    CONSTRAINT signal_event_status_check CHECK ((status = ANY (ARRAY['open'::text, 'claimed'::text, 'resolved'::text, 'dismissed'::text]))),
    CONSTRAINT signal_event_subject_ref_check CHECK ((btrim(subject_ref) <> ''::text)),
    CONSTRAINT signal_event_subject_type_check CHECK ((btrim(subject_type) <> ''::text))
);


--
-- Name: TABLE signal_event; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.signal_event IS 'Signals established by deterministic code before an LLM is asked to reason. The producer and signal_key make repeated scheduled detection idempotent across sessions.';


--
-- Name: source_capture; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.source_capture (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    captured_on date NOT NULL,
    session text NOT NULL,
    source_url text,
    visibility text DEFAULT 'public'::text NOT NULL,
    status text DEFAULT 'merged'::text NOT NULL,
    merge_note text DEFAULT ''::text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid NOT NULL,
    CONSTRAINT source_capture_status_check CHECK ((status = ANY (ARRAY['merged'::text, 'declined'::text, 'queued'::text]))),
    CONSTRAINT source_capture_visibility_check CHECK ((visibility = ANY (ARRAY['public'::text, 'member_gated'::text, 'colleague'::text, 'internal'::text])))
);


--
-- Name: TABLE source_capture; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.source_capture IS 'The Source Material capture log as records (0070). One row per learning source captured (podcast, article, video, portal session). Its ONE job is the dedup guard: log-capture checks here before any capture starts. Knowledge never lives here — it merges into the domain playbooks; merge_note records where. Renders to DNA/Marketing/Source Material/INDEX.md. No delete grant: a capture log never shrinks (a wrong row is corrected in place, versioned).';


--
-- Name: space; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.space (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    building_id uuid NOT NULL,
    suite text,
    floor integer,
    area_amount numeric(10,1),
    area_basis text,
    condition text,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid NOT NULL,
    CONSTRAINT space_area_amount_check CHECK (((area_amount >= (50)::numeric) AND (area_amount <= (500000)::numeric))),
    CONSTRAINT space_area_basis_check CHECK ((area_basis = ANY (ARRAY['rentable'::text, 'usable'::text, 'county_heated'::text, 'listed_unverified'::text])))
);


--
-- Name: space_search; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.space_search (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    client_id uuid NOT NULL,
    deal_id uuid,
    spec jsonb NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    report_path text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL,
    CONSTRAINT space_search_status_check CHECK ((status = ANY (ARRAY['open'::text, 'delivered'::text, 'closed'::text])))
);


--
-- Name: submarket_condition; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.submarket_condition (
    slug text NOT NULL,
    label text NOT NULL,
    tightness smallint NOT NULL,
    note text NOT NULL,
    sort integer NOT NULL
);


--
-- Name: TABLE submarket_condition; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.submarket_condition IS 'How the submarket stood when a negotiation round was proposed (0063). Exists to keep LEVERAGE separable from SKILL in v_counterparty_scorecard: a landlord in a tight market concedes nothing because he need not, and scoring that as toughness credits the market to the man. Three values on purpose — a human applies soft/balanced/tight consistently from memory, and five invites false precision on a judgment nobody measures.';


--
-- Name: COLUMN submarket_condition.tightness; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.submarket_condition.tightness IS 'Signed, so it sorts and averages: -1 favours our side (tenant/buyer), 0 balanced, +1 favours theirs. CARR represents tenants and buyers only, so the sign is unambiguous and never needs flipping per deal.';


--
-- Name: system_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.system_config (
    key text NOT NULL,
    value jsonb NOT NULL,
    note text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid
);


--
-- Name: tool_call; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tool_call (
    idempotency_key text NOT NULL,
    verb text NOT NULL,
    actor_id uuid NOT NULL,
    request_hash text NOT NULL,
    response jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    via text,
    client_id text,
    sponsoring_human_slug text,
    personal_scope text,
    authorization_class text,
    organization_tenant_id text,
    correlation_id uuid,
    CONSTRAINT tool_call_personal_scope_check CHECK (((personal_scope IS NULL) OR (personal_scope = ANY (ARRAY['joe-personal'::text, 'dell-personal'::text, 'none'::text])))),
    CONSTRAINT tool_call_sponsoring_human_slug_check CHECK (((sponsoring_human_slug IS NULL) OR (sponsoring_human_slug = ANY (ARRAY['joe'::text, 'dell'::text]))))
);


--
-- Name: COLUMN tool_call.sponsoring_human_slug; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tool_call.sponsoring_human_slug IS 'Verified partner sponsoring the runtime agent. Null means unsponsored or pre-0095 unknown.';


--
-- Name: COLUMN tool_call.personal_scope; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tool_call.personal_scope IS 'Rule brain loaded for this tool call: joe-personal, dell-personal, none, or null before 0095.';


--
-- Name: COLUMN tool_call.authorization_class; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tool_call.authorization_class IS 'Server-derived authority class, distinct from request-side operational profile.';


--
-- Name: COLUMN tool_call.organization_tenant_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tool_call.organization_tenant_id IS 'Server-derived CARR organization tenant. Null before 0095 is historically unknown.';


--
-- Name: COLUMN tool_call.correlation_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tool_call.correlation_id IS 'The x-correlation-id of the Worker request that produced this write, set from env.CORRELATION_ID (mcp-server/src/correlation.js) via the actor object mcp.js''s dispatch() decorates it onto. Nullable: rows predate this column, and a write made through local-verb.mjs''s direct-database break-glass path or any caller outside this Worker carries no Worker-minted id.';


--
-- Name: tool_read_call; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tool_read_call (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    verb text NOT NULL,
    actor_slug text NOT NULL,
    actor_id uuid,
    ok boolean DEFAULT true NOT NULL,
    error_kind text,
    via text,
    client_id text,
    organization_tenant_id text,
    sponsoring_human_slug text,
    personal_scope text,
    authorization_class text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT tool_read_call_check CHECK ((((ok = true) AND (error_kind IS NULL)) OR ((ok = false) AND (error_kind IS NOT NULL)))),
    CONSTRAINT tool_read_call_personal_scope_check CHECK (((personal_scope IS NULL) OR (personal_scope = ANY (ARRAY['joe-personal'::text, 'dell-personal'::text, 'none'::text])))),
    CONSTRAINT tool_read_call_sponsoring_human_slug_check CHECK (((sponsoring_human_slug IS NULL) OR (sponsoring_human_slug = ANY (ARRAY['joe'::text, 'dell'::text]))))
);


--
-- Name: TABLE tool_read_call; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.tool_read_call IS 'Phase 1 (0108). One row per READ-verb call through the MCP Worker — the read-side sibling of tool_call''s write-replay log, not a reuse of it (see file header for why they are separate tables). Identity/provenance only: verb, who, via which surface, when, ok/error_kind. Never an argument value or a response body. No DELETE — observed through growth_snapshot (0071) like every other audit-shaped accumulator, never pruned in place.';


--
-- Name: COLUMN tool_read_call.error_kind; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.tool_read_call.error_kind IS 'A short caller-facing ToolError code only (e.g. not_found, unknown_marker) when ok=false. Never a raw exception message or constraint detail, which can carry argument values through it.';


--
-- Name: v_placement_metric_latest; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_placement_metric_latest AS
 SELECT DISTINCT ON (placement_id, kind) placement_id,
    kind,
    value,
    observed_at,
    source
   FROM public.placement_metric
  ORDER BY placement_id, kind, observed_at DESC;


--
-- Name: VIEW v_placement_metric_latest; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_placement_metric_latest IS 'The newest snapshot per (placement, kind) (0066). placement_metric keeps every pull as its own row, so summing it directly double-counts any placement pulled more than once — 621 vs the true 490 Instagram views on 2026-08-02. Roll up through here, always.';


--
-- Name: v_placement_measurement; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_placement_measurement AS
 WITH m AS (
         SELECT v_placement_metric_latest.placement_id,
            count(*) AS kind_count,
            min(v_placement_metric_latest.observed_at) AS first_observed,
            max(v_placement_metric_latest.observed_at) AS last_observed,
            array_agg(DISTINCT v_placement_metric_latest.source ORDER BY v_placement_metric_latest.source) AS sources
           FROM public.v_placement_metric_latest
          GROUP BY v_placement_metric_latest.placement_id
        ), a AS (
         SELECT DISTINCT ON (placement_measurement.placement_id) placement_measurement.placement_id,
            placement_measurement.attempted_at,
            placement_measurement.outcome,
            placement_measurement.reason,
            placement_measurement.source
           FROM public.placement_measurement
          ORDER BY placement_measurement.placement_id, placement_measurement.attempted_at DESC
        )
 SELECT p.id AS placement_id,
    p.platform,
    p.external_id,
    p.url,
    p.live_at,
    cp.id AS piece_id,
    cp.kind AS piece_kind,
    cp.status AS piece_status,
    cp.campaign_id,
    c.name AS campaign_name,
    (m.placement_id IS NOT NULL) AS measured,
    COALESCE(m.kind_count, (0)::bigint) AS metric_kind_count,
    m.first_observed,
    m.last_observed,
    m.sources,
    a.attempted_at AS last_attempt_at,
    a.outcome AS last_attempt_outcome,
    a.source AS last_attempt_source,
        CASE
            WHEN (m.placement_id IS NOT NULL) THEN NULL::text
            WHEN (a.outcome = 'unavailable'::text) THEN a.reason
            WHEN (a.placement_id IS NULL) THEN 'no measurement attempt recorded'::text
            ELSE 'an attempt was recorded but no metric rows exist — investigate'::text
        END AS unmeasured_reason
   FROM ((((public.placement p
     JOIN public.content_piece cp ON ((cp.id = p.piece_id)))
     LEFT JOIN public.campaign c ON ((c.id = cp.campaign_id)))
     LEFT JOIN m ON ((m.placement_id = p.id)))
     LEFT JOIN a ON ((a.placement_id = p.id)));


--
-- Name: VIEW v_placement_measurement; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_placement_measurement IS 'Every placement with its measurement state stated rather than implied (0066). Read `measured` BEFORE any metric number: metric_kind_count is 0 for an unmeasured placement and that 0 means "no data", never "zero views". unmeasured_reason distinguishes "nobody pulled" from "the platform returned nothing". As of 2026-08-02: 89 rows, 16 measured, 73 not — including every one of the 42 X placements.';


--
-- Name: v_campaign_scorecard; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_campaign_scorecard AS
 SELECT c.id AS campaign_id,
    c.name,
    c.status,
    c.goal,
    c.success_criterion,
    c.starts_on,
    c.ends_on,
    c.channels,
    c.outcome_verdict,
    c.outcome_note,
    c.scored_at,
    count(v.placement_id) AS placements,
    count(DISTINCT v.piece_id) AS pieces,
    count(*) FILTER (WHERE v.measured) AS measured_placements,
    count(*) FILTER (WHERE (NOT v.measured)) AS unmeasured_placements,
        CASE
            WHEN (count(v.placement_id) = 0) THEN NULL::numeric
            ELSE round(((100.0 * (count(*) FILTER (WHERE v.measured))::numeric) / (count(v.placement_id))::numeric), 1)
        END AS coverage_pct,
        CASE
            WHEN (count(*) FILTER (WHERE v.measured) = 0) THEN NULL::numeric
            ELSE sum(l.views)
        END AS views_total,
        CASE
            WHEN (count(*) FILTER (WHERE v.measured) = 0) THEN NULL::numeric
            ELSE sum(l.interactions)
        END AS interactions_total
   FROM ((public.campaign c
     LEFT JOIN public.v_placement_measurement v ON ((v.campaign_id = c.id)))
     LEFT JOIN LATERAL ( SELECT sum(lm.value) FILTER (WHERE (lm.kind = 'views_count'::text)) AS views,
            sum(lm.value) FILTER (WHERE (lm.kind = 'interactions_sum'::text)) AS interactions
           FROM public.v_placement_metric_latest lm
          WHERE (lm.placement_id = v.placement_id)) l ON (true))
  GROUP BY c.id, c.name, c.status, c.goal, c.success_criterion, c.starts_on, c.ends_on, c.channels, c.outcome_verdict, c.outcome_note, c.scored_at;


--
-- Name: VIEW v_campaign_scorecard; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_campaign_scorecard IS 'One row per campaign: its stated criterion beside what was actually measured (0066). Totals are NULL when measured_placements is 0, so an unmeasured campaign can never be read as a campaign that earned nothing. score-campaign reads coverage_pct here and refuses an unconfirmed "worked" verdict beneath the confidence floor.';


--
-- Name: v_capture_candidate_queue; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_capture_candidate_queue AS
 SELECT c.id,
    c.session_id,
    c.kind,
    c.payload,
    c.evidence_quote,
    c.confidence,
    c.created_at,
    COALESCE(direct_deal.name, named_deal.name) AS deal_name
   FROM (((public.capture_candidate c
     JOIN public.capture_session s ON ((s.id = c.session_id)))
     LEFT JOIN public.deal direct_deal ON (((direct_deal.id)::text = COALESCE((c.payload ->> 'deal'::text), (c.payload ->> 'ref'::text)))))
     LEFT JOIN LATERAL ( SELECT d.name
           FROM public.deal d
          WHERE (d.name ~~* COALESCE((c.payload ->> 'deal'::text), (c.payload ->> 'ref'::text)))
          ORDER BY d.name
         LIMIT 1) named_deal ON ((direct_deal.id IS NULL)))
  WHERE ((c.status = 'pending'::text) AND (s.expires_at > now()));


--
-- Name: v_last_touch; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_last_touch AS
 WITH contact AS (
         SELECT a.id,
            a.occurred_at,
            a.recorded_at,
            a.actor_id,
            a.kind,
            a.summary,
            a.detail,
            a.owed,
            a.deal_id,
            a.client_id,
            a.lead_id,
            a.vendor_id,
            a.source,
            a.version,
            a.updated_at,
            a.updated_by
           FROM (public.activity a
             JOIN public.activity_kind k ON ((k.slug = a.kind)))
          WHERE (k.is_contact OR ((a.kind = 'note'::text) AND (a.summary = 'last-touch stamp (imported)'::text)) OR ((a.kind = 'note'::text) AND (a.summary ~~ 'Last touch carried from%'::text)))
        ), deal_contact AS (
         SELECT d.id AS deal_id,
            c.occurred_at
           FROM (public.deal d
             JOIN contact c ON ((c.deal_id = d.id)))
        UNION ALL
         SELECT d.id,
            c.occurred_at
           FROM ((public.deal d
             JOIN public.client cl ON ((cl.id = d.client_id)))
             JOIN contact c ON ((c.client_id = cl.id)))
        )
 SELECT 'deal'::text AS subject_type,
    deal_contact.deal_id AS subject_id,
    (max(deal_contact.occurred_at))::date AS last_touch
   FROM deal_contact
  GROUP BY deal_contact.deal_id
UNION ALL
 SELECT 'client'::text AS subject_type,
    contact.client_id AS subject_id,
    (max(contact.occurred_at))::date AS last_touch
   FROM contact
  WHERE (contact.client_id IS NOT NULL)
  GROUP BY contact.client_id
UNION ALL
 SELECT 'lead'::text AS subject_type,
    contact.lead_id AS subject_id,
    (max(contact.occurred_at))::date AS last_touch
   FROM contact
  WHERE (contact.lead_id IS NOT NULL)
  GROUP BY contact.lead_id
UNION ALL
 SELECT 'vendor'::text AS subject_type,
    contact.vendor_id AS subject_id,
    (max(contact.occurred_at))::date AS last_touch
   FROM contact
  WHERE (contact.vendor_id IS NOT NULL)
  GROUP BY contact.vendor_id;


--
-- Name: VIEW v_last_touch; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_last_touch IS 'Last CONTACT per subject, derived (never hand-stamped). Counts activity kinds flagged is_contact, plus the legacy imported stamps the freeze carried in as notes (amendment 10). A note logged from here on does NOT stamp a touch.';


--
-- Name: vendor; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vendor (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    vendor_ref text,
    party_id uuid NOT NULL,
    category text NOT NULL,
    verticals text[],
    stage text,
    owner_id uuid,
    referral_active boolean,
    territory text,
    offers text,
    seeking text,
    rivalry_group text,
    originated text,
    enrich boolean,
    out_of_market boolean DEFAULT false NOT NULL,
    last_touch date,
    intro_notes text,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by uuid NOT NULL,
    owner_label text,
    links_label text,
    relationship_level integer,
    disposition text DEFAULT 'active'::text NOT NULL,
    is_target boolean DEFAULT false NOT NULL,
    category_slug text,
    merged_into uuid
);


--
-- Name: COLUMN vendor.relationship_level; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.vendor.relationship_level IS '0 Prospective / 1 Building / 2 Established / 3 Core (vendor_relationship_level). NULL = NOT YET JUDGED, which is a different fact from 0 and must stay distinguishable: 59 of 290 vendors were `unrated` at 0047 and collapsing them into 0 would have hidden that one in five has never been assessed.';


--
-- Name: COLUMN vendor.disposition; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.vendor.disposition IS 'active | parked | avoid. A RULING, deliberately not a relationship level — the 0045 fault was a decision stored in a stage field, where it contradicted three others.';


--
-- Name: COLUMN vendor.is_target; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.vendor.is_target IS 'We want this one. Separate from level because target_not_yet_met was depth 0 PLUS intent, and folding intent into the level loses what 41 vendors were flagged for.';


--
-- Name: COLUMN vendor.category_slug; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.vendor.category_slug IS 'FK to vendor_category. Replaces the free-text `category`, which is how a stage value ("Target (not yet met)", 41 rows) got stored as a profession. NULL means NOT YET CATEGORISED — never "Misc", which is a false statement that hid 22 vendors. Adding a rare type is an INSERT here, not a migration, which is what makes Joe''s "they deserve their own category" affordable.';


--
-- Name: COLUMN vendor.merged_into; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.vendor.merged_into IS 'Vendor-row merge pointer, written only by merge-vendor-rows (humanOnly). Covers the case confirm-merge structurally cannot: two vendor rows riding ONE party (a party-level merge that moved role rows, or a double import). A row with merged_into set is a tombstone: excluded from renders and reports, still resolvable through v_ref_index with merged=true so a search learns where it went.';


--
-- Name: v_capture_coverage; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_capture_coverage AS
 WITH subj AS (
         SELECT 'deal'::text AS subject_type,
            d.id
           FROM public.deal d
          WHERE ((d.outcome IS NULL) AND (d.phase <> 'closed'::text))
        UNION ALL
         SELECT 'client'::text,
            c.id
           FROM public.client c
          WHERE (c.merged_into IS NULL)
        UNION ALL
         SELECT 'lead'::text,
            l.id
           FROM public.lead l
        UNION ALL
         SELECT 'vendor'::text,
            v.id
           FROM public.vendor v
        )
 SELECT s.subject_type,
    count(*) AS records,
    count(lt.last_touch) AS with_any_touch,
    round(((100.0 * (count(lt.last_touch))::numeric) / (NULLIF(count(*), 0))::numeric), 1) AS pct_with_touch,
    count(*) FILTER (WHERE (lt.last_touch >= (CURRENT_DATE - 90))) AS touched_90d,
    round(((100.0 * (count(*) FILTER (WHERE (lt.last_touch >= (CURRENT_DATE - 90))))::numeric) / (NULLIF(count(*), 0))::numeric), 1) AS pct_touched_90d,
    max(lt.last_touch) AS most_recent
   FROM (subj s
     LEFT JOIN public.v_last_touch lt ON (((lt.subject_type = s.subject_type) AND (lt.subject_id = s.id))))
  GROUP BY s.subject_type;


--
-- Name: VIEW v_capture_coverage; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_capture_coverage IS 'Capture coverage per subject type. Added 0034 after a night in which every broken detector reported all-clear. pct_with_touch is the honest health of the input layer: when it is low, staleness / reciprocity / delivery scoring / the graph are all guessing, and their silence means nothing.';


--
-- Name: v_capture_session_status; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_capture_session_status AS
 SELECT id AS session_id,
    device_id,
    state,
    started_at,
    state_at
   FROM public.capture_session s
  WHERE (expires_at > now());


--
-- Name: v_claim_card; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_claim_card AS
 SELECT id AS pool_id,
    version AS base_version,
    source AS lane,
    name AS display_name,
    org_name,
    vertical,
    city,
    county,
    state,
    segment,
    segment_play,
    score,
    score_basis,
    est_lease_event,
    est_basis,
    dup_tier,
    dup_ref,
    dup_basis,
    (((email IS NOT NULL) AND (email <> ''::text)) OR ((phone IS NOT NULL) AND (phone <> ''::text))) AS has_channel,
    (NOT (((email IS NOT NULL) AND (email <> ''::text)) OR ((phone IS NOT NULL) AND (phone <> ''::text)))) AS needs_contact,
    (est_lease_event - CURRENT_DATE) AS days_to_window,
    created_at
   FROM public.candidate_pool cp
  WHERE ((status = 'pool'::text) AND (NOT dup_do_not_contact));


--
-- Name: VIEW v_claim_card; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_claim_card IS 'The claimable slice of the candidate reservoir: status pool, not do-not-contact. Promoted, suppressed and DECLINED rows are gone by construction, which is what lets the card shrink as Joe works it — the property the 9,783-row list never had. Deliberately UNBOUNDED and unfiltered on channel: the view ranks nothing away, the caller decides how many to present and whether to split the needs_contact bucket. Never add email, phone, address or source_row here.';


--
-- Name: v_client_account; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_client_account AS
 SELECT c.id AS client_id,
    c.roster_ref AS client_ref,
    p.name AS client_name,
    c.client_type,
    c.status,
    org.id AS account_party_id,
    org.ref AS account_party_ref,
    org.name AS account_name,
    pc.id AS account_client_id,
    pc.roster_ref AS account_client_ref,
    pc.client_type AS account_client_type,
    ((pc.id IS NOT NULL) AND (pc.id <> c.id)) AS is_sub_client,
    ( SELECT count(*) AS count
           FROM public.deal d
          WHERE (d.client_id = c.id)) AS deals
   FROM (((public.client c
     JOIN public.party p ON ((p.id = c.party_id)))
     LEFT JOIN public.party org ON (((org.id = p.org_id) AND (org.merged_into IS NULL))))
     LEFT JOIN public.client pc ON (((pc.party_id = org.id) AND (pc.merged_into IS NULL) AND (pc.client_type = 'national_account'::text))))
  WHERE (c.merged_into IS NULL);


--
-- Name: VIEW v_client_account; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_client_account IS 'Every live client with the national account it sits under, if any (0061). The parent link is NOT a column: it resolves client -> party -> party.org_id -> the org''s client_type=''national_account'' client row, which is the chain 0059 already populated and every other consumer already uses to reach an org. Deliberately no client.parent_client_id — a second representation of the same fact has to be kept in agreement forever, and on the day the two disagree there is no way to tell which is right. is_sub_client is the test Joe''s ruling turns on: a franchisee is its OWN client under the account, never a line item on it, and deals hang off the sub-client. Reopen the column question if an account ever spans two org parties, or a client needs a parent that is not its employer.';


--
-- Name: v_code_finding; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_code_finding AS
 SELECT k.repo,
    k.commit_sha,
    f.subject_type,
    f.id AS flag_id,
    f.kind,
    COALESCE(((f.value ->> 'found'::text))::boolean, true) AS found,
    (f.value ->> 'epistemic_status'::text) AS epistemic_status,
    f.value,
    f.source,
    f.observed_at,
    a.slug AS recorded_by
   FROM ((public.record_flag f
     JOIN public.code_subject k ON ((k.id = f.subject_id)))
     LEFT JOIN public.actor a ON ((a.id = f.created_by)))
  WHERE (f.subject_type = ANY (ARRAY['repo'::text, 'commit'::text]));


--
-- Name: VIEW v_code_finding; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_code_finding IS 'Findings filed against code (0101), with repo and commit_sha as first-class columns so "what did review ever find about this commit" is a query rather than a label match. A row with found=false is a review that ran and found nothing, which is the signal the Review Council could not previously persist anywhere but a local sidecar.';


--
-- Name: v_compiled_rules; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_compiled_rules AS
 SELECT r.statement,
    r.human_quote,
    teacher.display_name AS taught_by,
    owner.slug AS personal_to,
    r.enforcement,
    r.activated_at,
    r.scope,
    r.id
   FROM ((public.rule r
     JOIN public.actor teacher ON ((teacher.id = r.taught_by)))
     LEFT JOIN public.actor owner ON ((owner.id = r.personal_to)))
  WHERE (r.status = 'active'::text)
  ORDER BY r.activated_at;


--
-- Name: v_contact; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_contact AS
 WITH roles AS (
         SELECT p.id AS party_id,
            p.ref,
            p.name,
            p.title,
            p.contact_state,
            ( SELECT c.roster_ref
                   FROM public.client c
                  WHERE ((c.party_id = p.id) AND (c.merged_into IS NULL))
                 LIMIT 1) AS client_ref,
            ( SELECT c.status
                   FROM public.client c
                  WHERE ((c.party_id = p.id) AND (c.merged_into IS NULL))
                 LIMIT 1) AS client_status,
            ( SELECT l.registry_ref
                   FROM public.lead l
                  WHERE (l.party_id = p.id)
                 LIMIT 1) AS lead_ref,
            ( SELECT l.stage
                   FROM public.lead l
                  WHERE (l.party_id = p.id)
                 LIMIT 1) AS lead_stage,
            ( SELECT v.vendor_ref
                   FROM public.vendor v
                  WHERE (v.party_id = p.id)
                 LIMIT 1) AS vendor_ref,
            ( SELECT v.relationship_level
                   FROM public.vendor v
                  WHERE (v.party_id = p.id)
                 LIMIT 1) AS vendor_level
           FROM public.party p
          WHERE ((p.merged_into IS NULL) AND (p.deleted_at IS NULL))
        ), axes AS (
         SELECT r.party_id,
            r.ref,
            r.name,
            r.title,
            r.contact_state,
            r.client_ref,
            r.client_status,
            r.lead_ref,
            r.lead_stage,
            r.vendor_ref,
            r.vendor_level,
            (r.vendor_ref IS NOT NULL) AS is_vendor,
                CASE
                    WHEN (r.client_status = 'past_client'::text) THEN 'past_client'::text
                    WHEN (r.client_ref IS NOT NULL) THEN 'client'::text
                    WHEN (r.lead_stage = ANY (ARRAY['qualified'::text, 'engaged'::text, 'active_deal'::text])) THEN 'prospect'::text
                    WHEN (r.lead_ref IS NOT NULL) THEN 'lead'::text
                    ELSE NULL::text
                END AS journey_stage
           FROM roles r
        )
 SELECT ref,
    name,
    title,
    contact_state,
    journey_stage,
    is_vendor,
    client_ref,
    lead_ref,
    vendor_ref,
    vendor_level,
    NULLIF(TRIM(BOTH ' + '::text FROM concat_ws(' + '::text,
        CASE
            WHEN is_vendor THEN 'Vendor'::text
            ELSE NULL::text
        END,
        CASE journey_stage
            WHEN 'past_client'::text THEN 'Past Client'::text
            WHEN 'client'::text THEN 'Client'::text
            WHEN 'prospect'::text THEN 'Prospect'::text
            WHEN 'lead'::text THEN 'Lead'::text
            ELSE NULL::text
        END)), ''::text) AS contact_type,
    ((journey_stage = 'past_client'::text) AND (contact_state = ANY (ARRAY['active'::text, 'nurture'::text]))) AS prospect_by_default
   FROM axes;


--
-- Name: VIEW v_contact; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_contact IS 'One row per living party with contact type DERIVED from two independent axes (Joe, 2026-08-02): journey_stage from the role records, is_vendor from whether a vendor record exists. contact_type renders "Vendor + Client" etc. without anyone typing it, so it cannot drift. prospect_by_default implements "any past client is also a prospect for a future deal" as a DEFAULT, suppressed when contact_state is paused or do_not_contact — because "if a deal ended badly we may not handle them like a prospect due to the context".';


--
-- Name: v_negotiation_position; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_negotiation_position AS
 WITH basis AS (
         SELECT negotiation_round.deal_id,
            count(*) FILTER (WHERE ((negotiation_round.rate_basis IS NOT NULL) AND (negotiation_round.rate_norm_sf_yr IS NULL))) AS unnormed,
            count(DISTINCT negotiation_round.rate_basis) FILTER (WHERE (negotiation_round.rate_basis IS NOT NULL)) AS rate_bases,
            count(DISTINCT negotiation_round.ti_basis) FILTER (WHERE (negotiation_round.ti_basis IS NOT NULL)) AS ti_bases
           FROM public.negotiation_round
          GROUP BY negotiation_round.deal_id
        ), pos AS (
         SELECT nr.id,
            nr.deal_id,
            nr.round_no,
            nr.side,
            nr.proposed_on,
            nr.expires_on,
            nr.free_rent_months,
            nr.term_months,
            nr.escalator,
            nr.submarket_condition,
                CASE
                    WHEN (nr.side = ANY (ARRAY['tenant'::text, 'buyer'::text])) THEN 'ours'::text
                    ELSE 'theirs'::text
                END AS camp,
            row_number() OVER (PARTITION BY nr.deal_id ORDER BY nr.proposed_on, nr.created_at, nr.id) AS seq,
                CASE
                    WHEN (b.unnormed = 0) THEN nr.rate_norm_sf_yr
                    WHEN (b.rate_bases = 1) THEN nr.rate_amount
                    ELSE NULL::numeric
                END AS rate_cmp,
                CASE
                    WHEN (b.unnormed = 0) THEN 'usd_sf_yr_norm'::text
                    WHEN (b.rate_bases = 1) THEN nr.rate_basis
                    ELSE NULL::text
                END AS rate_cmp_basis,
                CASE
                    WHEN (b.ti_bases <= 1) THEN nr.ti_amount
                    ELSE NULL::numeric
                END AS ti_cmp,
                CASE
                    WHEN (b.ti_bases <= 1) THEN nr.ti_basis
                    ELSE NULL::text
                END AS ti_cmp_basis
           FROM (public.negotiation_round nr
             JOIN basis b ON ((b.deal_id = nr.deal_id)))
        )
 SELECT p.id,
    p.deal_id,
    p.round_no,
    p.side,
    p.camp,
    p.seq,
    p.proposed_on,
    p.expires_on,
    p.rate_cmp,
    p.rate_cmp_basis,
    p.ti_cmp,
    p.ti_cmp_basis,
    p.free_rent_months,
    p.term_months,
    p.escalator,
    p.submarket_condition,
    prev.seq AS prior_opposing_seq,
    ((prev.seq IS NOT NULL) AND ((p.rate_cmp IS DISTINCT FROM prev.rate_cmp) OR (p.ti_cmp IS DISTINCT FROM prev.ti_cmp) OR (p.free_rent_months IS DISTINCT FROM prev.free_rent_months) OR (p.term_months IS DISTINCT FROM prev.term_months))) AS moves_off_standing
   FROM (pos p
     LEFT JOIN LATERAL ( SELECT p2.seq,
            p2.rate_cmp,
            p2.ti_cmp,
            p2.free_rent_months,
            p2.term_months
           FROM pos p2
          WHERE ((p2.deal_id = p.deal_id) AND (p2.camp <> p.camp) AND (p2.seq < p.seq))
          ORDER BY p2.seq DESC
         LIMIT 1) prev ON (true));


--
-- Name: VIEW v_negotiation_position; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_negotiation_position IS 'One row per negotiation round, normalised so two rounds can be subtracted (0064). camp is ours/theirs and is derived from side, because CARR represents tenants and buyers only and never landlords or sellers. rate_cmp is NULL when the deal mixes rate bases — a purchase price is not a rent (0022) and a wrong comparison is worse than no comparison. moves_off_standing is the primitive behind the exclusion rule: it is TRUE only when this round differs from the other side''s standing position on a NUMBER, so a same-terms acceptance never reads as a counter.';


--
-- Name: v_counterparty_bluff; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_counterparty_bluff AS
 WITH their_rounds AS (
         SELECT p_1.id,
            p_1.deal_id,
            p_1.round_no,
            p_1.side,
            p_1.camp,
            p_1.seq,
            p_1.proposed_on,
            p_1.expires_on,
            p_1.rate_cmp,
            p_1.rate_cmp_basis,
            p_1.ti_cmp,
            p_1.ti_cmp_basis,
            p_1.free_rent_months,
            p_1.term_months,
            p_1.escalator,
            p_1.submarket_condition,
            p_1.prior_opposing_seq,
            p_1.moves_off_standing,
            cp.party_id AS counterparty_id
           FROM (public.v_negotiation_position p_1
             JOIN ( SELECT deal_participant.deal_id,
                    deal_participant.party_id
                   FROM public.deal_participant
                  WHERE ((deal_participant.role = 'listing_side'::text) AND (deal_participant.to_at IS NULL) AND (deal_participant.party_id IS NOT NULL))) cp ON ((cp.deal_id = p_1.deal_id)))
          WHERE (p_1.camp = 'theirs'::text)
        ), logged AS (
         SELECT tr.counterparty_id,
            tr.deal_id,
            tr.id AS round_id,
            tr.seq,
            tr.expires_on,
            tr.rate_cmp,
            tr.ti_cmp,
            tr.free_rent_months,
            c.claim_type,
            t_1.falsifiable,
                CASE
                    WHEN (c.stated_floor IS NULL) THEN tr.rate_cmp
                    WHEN ((tr.rate_cmp_basis = 'usd_sf_yr_norm'::text) AND (c.stated_floor_basis = 'usd_sf_yr'::text)) THEN c.stated_floor
                    WHEN ((tr.rate_cmp_basis = 'usd_sf_yr_norm'::text) AND (c.stated_floor_basis = 'usd_sf_mo'::text)) THEN (c.stated_floor * (12)::numeric)
                    WHEN (tr.rate_cmp_basis = c.stated_floor_basis) THEN c.stated_floor
                    ELSE NULL::numeric
                END AS floor_cmp
           FROM ((their_rounds tr
             JOIN public.negotiation_claim c ON ((c.round_id = tr.id)))
             JOIN public.negotiation_claim_type t_1 ON ((t_1.slug = c.claim_type)))
        ), derived_deadline AS (
         SELECT tr.counterparty_id,
            tr.deal_id,
            tr.id AS round_id,
            tr.seq,
            tr.expires_on,
            tr.rate_cmp,
            tr.ti_cmp,
            tr.free_rent_months,
            'deadline'::text AS claim_type,
            true AS falsifiable,
            NULL::numeric AS floor_cmp
           FROM their_rounds tr
          WHERE (tr.expires_on IS NOT NULL)
        ), claims AS (
         SELECT logged.counterparty_id,
            logged.deal_id,
            logged.round_id,
            logged.seq,
            logged.expires_on,
            logged.rate_cmp,
            logged.ti_cmp,
            logged.free_rent_months,
            logged.claim_type,
            logged.falsifiable,
            logged.floor_cmp
           FROM logged
        UNION ALL
         SELECT derived_deadline.counterparty_id,
            derived_deadline.deal_id,
            derived_deadline.round_id,
            derived_deadline.seq,
            derived_deadline.expires_on,
            derived_deadline.rate_cmp,
            derived_deadline.ti_cmp,
            derived_deadline.free_rent_months,
            derived_deadline.claim_type,
            derived_deadline.falsifiable,
            derived_deadline.floor_cmp
           FROM derived_deadline
        ), tested AS (
         SELECT cl.counterparty_id,
            cl.deal_id,
            cl.round_id,
            cl.seq,
            cl.expires_on,
            cl.rate_cmp,
            cl.ti_cmp,
            cl.free_rent_months,
            cl.claim_type,
            cl.falsifiable,
            cl.floor_cmp,
                CASE cl.claim_type
                    WHEN 'walk_away'::text THEN (EXISTS ( SELECT 1
                       FROM their_rounds t2
                      WHERE ((t2.deal_id = cl.deal_id) AND (t2.counterparty_id = cl.counterparty_id) AND (t2.seq > cl.seq))))
                    WHEN 'finality'::text THEN (EXISTS ( SELECT 1
                       FROM their_rounds t2
                      WHERE ((t2.deal_id = cl.deal_id) AND (t2.counterparty_id = cl.counterparty_id) AND (t2.seq > cl.seq) AND ((t2.rate_cmp < cl.rate_cmp) OR (t2.ti_cmp > cl.ti_cmp) OR (t2.free_rent_months > cl.free_rent_months)))))
                    WHEN 'authority'::text THEN
                    CASE
                        WHEN (cl.floor_cmp IS NOT NULL) THEN (EXISTS ( SELECT 1
                           FROM their_rounds t2
                          WHERE ((t2.deal_id = cl.deal_id) AND (t2.counterparty_id = cl.counterparty_id) AND (t2.seq > cl.seq) AND (t2.rate_cmp < cl.floor_cmp))))
                        ELSE NULL::boolean
                    END
                    WHEN 'deadline'::text THEN (EXISTS ( SELECT 1
                       FROM their_rounds t2
                      WHERE ((t2.deal_id = cl.deal_id) AND (t2.counterparty_id = cl.counterparty_id) AND (t2.seq > cl.seq) AND (t2.proposed_on > cl.expires_on))))
                    ELSE NULL::boolean
                END AS reversed
           FROM claims cl
        )
 SELECT t.counterparty_id,
    p.name AS counterparty_name,
    t.claim_type,
    ct.falsifiable,
    count(*) AS claims_made,
    count(*) FILTER (WHERE (t.reversed IS NOT NULL)) AS claims_testable,
    count(*) FILTER (WHERE t.reversed) AS claims_reversed,
        CASE
            WHEN (count(*) FILTER (WHERE (t.reversed IS NOT NULL)) = 0) THEN (('made '::text || count(*)) || ', none testable'::text)
            ELSE ((('reversed '::text || count(*) FILTER (WHERE t.reversed)) || ' of '::text) || count(*) FILTER (WHERE (t.reversed IS NOT NULL)))
        END AS as_observed
   FROM ((tested t
     JOIN public.negotiation_claim_type ct ON ((ct.slug = t.claim_type)))
     JOIN public.party p ON ((p.id = t.counterparty_id)))
  GROUP BY t.counterparty_id, p.name, t.claim_type, ct.falsifiable;


--
-- Name: VIEW v_counterparty_bluff; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_counterparty_bluff IS 'Claims a counterparty made about their own position, against claims their own later rounds contradicted (0064). An OBSERVATION count — no row here says anyone bluffs, and no column stores a characterisation of a named person. Unfalsifiable claims are kept out of the rate by joining negotiation_claim_type on falsifiable, so marking a future claim type unfalsifiable is enough; the rule lives in the data, not in a list copied into this view. deadline claims come from negotiation_round.expires_on, which is why 0063 makes them unloggable as rows. Returns 0 rows until claims are captured.';


--
-- Name: v_counterparty_history; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_counterparty_history AS
 SELECT DISTINCT p.id AS party_id,
    p.name AS party_name,
    p.kind AS party_kind,
    p.city AS party_city,
    p.state AS party_state,
    dp.role AS relationship,
    NULL::text AS building_address,
    NULL::text AS building_city,
    NULL::text AS building_state,
    d.id AS deal_id,
    d.name AS deal_name,
    d.deal_type,
    d.phase,
    d.outcome,
    d.closed_on,
    c.roster_ref AS client_ref
   FROM (((public.deal_participant dp
     JOIN public.party p ON ((p.id = dp.party_id)))
     JOIN public.deal d ON ((d.id = dp.deal_id)))
     JOIN public.client c ON ((c.id = d.client_id)))
  WHERE ((dp.role = ANY (ARRAY['listing_side'::text, 'referring_agent'::text])) AND (p.merged_into IS NULL))
UNION ALL
 SELECT DISTINCT p.id AS party_id,
    p.name AS party_name,
    p.kind AS party_kind,
    p.city AS party_city,
    p.state AS party_state,
    bo.kind AS relationship,
    b.address AS building_address,
    b.city AS building_city,
    b.state AS building_state,
    d.id AS deal_id,
    d.name AS deal_name,
    d.deal_type,
    d.phase,
    d.outcome,
    d.closed_on,
    c.roster_ref AS client_ref
   FROM (((((((public.building_ownership bo
     JOIN public.party p ON ((p.id = bo.party_id)))
     JOIN public.building b ON ((b.id = bo.building_id)))
     LEFT JOIN public.space s ON ((s.building_id = b.id)))
     LEFT JOIN public.premises_space ps ON ((ps.space_id = s.id)))
     LEFT JOIN public.premises pr ON ((pr.id = ps.premises_id)))
     LEFT JOIN public.deal d ON ((d.id = pr.deal_id)))
     LEFT JOIN public.client c ON ((c.id = d.client_id)))
  WHERE ((p.merged_into IS NULL) AND (bo.to_on IS NULL));


--
-- Name: VIEW v_counterparty_history; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_counterparty_history IS '0026 / ORDER 27 EXT (d): counterparty relationships (listing agents, landlords, owners, property managers, referring agents) walked to deals. SAFE COLUMNS ONLY — the column list is a security boundary; adding phone/email/notes is a design call, not an edit. [D5]: internal-seat only, NEVER an export target or client-facing surface.';


--
-- Name: v_negotiation_deal; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_negotiation_deal AS
 WITH cp AS (
         SELECT dp.deal_id,
            dp.party_id
           FROM public.deal_participant dp
          WHERE ((dp.role = 'listing_side'::text) AND (dp.to_at IS NULL) AND (dp.party_id IS NOT NULL))
        ), cp_n AS (
         SELECT cp.deal_id,
            count(DISTINCT cp.party_id) AS n_cp
           FROM cp
          GROUP BY cp.deal_id
        ), agg AS (
         SELECT p.deal_id,
            count(*) FILTER (WHERE (p.camp = 'ours'::text)) AS rounds_ours,
            count(*) FILTER (WHERE (p.camp = 'theirs'::text)) AS rounds_theirs,
            count(*) AS rounds_total,
            bool_or(((p.camp = 'ours'::text) AND p.moves_off_standing)) AS counter_tested,
            max(p.rate_cmp_basis) AS rate_cmp_basis,
            min(p.proposed_on) AS first_round_on,
            max(p.proposed_on) AS last_round_on,
            (array_agg(p.rate_cmp ORDER BY p.seq) FILTER (WHERE ((p.camp = 'theirs'::text) AND (p.rate_cmp IS NOT NULL))))[1] AS their_open_rate,
            (array_agg(p.rate_cmp ORDER BY p.seq DESC) FILTER (WHERE ((p.camp = 'theirs'::text) AND (p.rate_cmp IS NOT NULL))))[1] AS their_last_rate,
            (array_agg(p.rate_cmp ORDER BY p.seq) FILTER (WHERE ((p.camp = 'ours'::text) AND (p.rate_cmp IS NOT NULL))))[1] AS our_open_rate,
            (array_agg(p.rate_cmp ORDER BY p.seq DESC) FILTER (WHERE ((p.camp = 'ours'::text) AND (p.rate_cmp IS NOT NULL))))[1] AS our_last_rate,
            min(p.rate_cmp) FILTER (WHERE (p.camp = 'theirs'::text)) AS their_best_rate,
            (array_agg(p.ti_cmp ORDER BY p.seq) FILTER (WHERE ((p.camp = 'theirs'::text) AND (p.ti_cmp IS NOT NULL))))[1] AS their_open_ti,
            max(p.ti_cmp) FILTER (WHERE (p.camp = 'theirs'::text)) AS their_best_ti,
            (array_agg(p.free_rent_months ORDER BY p.seq) FILTER (WHERE ((p.camp = 'theirs'::text) AND (p.free_rent_months IS NOT NULL))))[1] AS their_open_free,
            max(p.free_rent_months) FILTER (WHERE (p.camp = 'theirs'::text)) AS their_best_free,
            count(*) FILTER (WHERE ((p.camp = 'theirs'::text) AND (p.rate_cmp IS NOT NULL))) AS their_rate_rows,
            count(*) FILTER (WHERE ((p.camp = 'theirs'::text) AND (p.ti_cmp IS NOT NULL))) AS their_ti_rows,
            count(*) FILTER (WHERE ((p.camp = 'theirs'::text) AND (p.free_rent_months IS NOT NULL))) AS their_free_rows,
            count(*) FILTER (WHERE ((p.camp = 'theirs'::text) AND (p.term_months IS NOT NULL))) AS their_term_rows,
            count(*) FILTER (WHERE ((p.camp = 'theirs'::text) AND (p.escalator IS NOT NULL))) AS their_esc_rows,
            count(DISTINCT p.term_months) FILTER (WHERE ((p.camp = 'theirs'::text) AND (p.term_months IS NOT NULL))) AS their_term_values,
            count(DISTINCT p.escalator) FILTER (WHERE ((p.camp = 'theirs'::text) AND (p.escalator IS NOT NULL))) AS their_esc_values,
            (array_agg(p.submarket_condition ORDER BY p.seq DESC) FILTER (WHERE (p.submarket_condition IS NOT NULL)))[1] AS submarket_condition
           FROM public.v_negotiation_position p
          GROUP BY p.deal_id
        )
 SELECT a.deal_id,
    d.name AS deal_name,
    d.deal_type,
    d.phase,
    c.party_id AS counterparty_id,
    COALESCE(n.n_cp, (0)::bigint) AS listing_side_parties,
    a.rounds_ours,
    a.rounds_theirs,
    a.rounds_total,
    a.first_round_on,
    a.last_round_on,
    a.counter_tested,
    (a.counter_tested AND (COALESCE(n.n_cp, (0)::bigint) = 1)) AS qualifies,
        CASE
            WHEN (NOT a.counter_tested) THEN 'our side never countered — the counterparty was not tested'::text
            WHEN (COALESCE(n.n_cp, (0)::bigint) = 0) THEN 'no listing_side party recorded on this deal'::text
            WHEN (n.n_cp > 1) THEN 'more than one live listing_side party — movement cannot be attributed'::text
            ELSE NULL::text
        END AS exclusion_reason,
    a.rate_cmp_basis,
    a.their_open_rate,
    a.their_last_rate,
    a.our_open_rate,
    a.our_last_rate,
    (a.their_open_rate - a.their_last_rate) AS their_movement,
    (a.our_last_rate - a.our_open_rate) AS our_movement,
        CASE
            WHEN ((a.their_open_rate IS NOT NULL) AND (a.their_last_rate IS NOT NULL) AND (a.our_open_rate IS NOT NULL) AND (a.our_last_rate IS NOT NULL) AND (((a.their_open_rate - a.their_last_rate) + (a.our_last_rate - a.our_open_rate)) > (0)::numeric)) THEN round(((a.our_last_rate - a.our_open_rate) / ((a.their_open_rate - a.their_last_rate) + (a.our_last_rate - a.our_open_rate))), 4)
            ELSE NULL::numeric
        END AS our_share_of_movement,
        CASE
            WHEN ((a.their_rate_rows >= 2) AND (a.their_open_rate > (0)::numeric)) THEN round(GREATEST((0)::numeric, LEAST((1)::numeric, ((a.their_open_rate - a.their_last_rate) / a.their_open_rate))), 4)
            ELSE NULL::numeric
        END AS their_concession_frac,
    COALESCE((a.their_last_rate = a.our_last_rate), false) AS converged,
        CASE
            WHEN ((a.their_last_rate IS NOT NULL) AND (a.their_last_rate = a.our_last_rate) AND (a.their_last_rate > (0)::numeric) AND (a.their_open_rate IS NOT NULL)) THEN round(((a.their_open_rate - a.their_last_rate) / a.their_last_rate), 4)
            ELSE NULL::numeric
        END AS opening_spread_frac,
    (a.their_rate_rows >= 2) AS rate_tested,
    ((a.their_rate_rows >= 2) AND (a.their_best_rate >= a.their_open_rate)) AS rate_held,
    (a.their_ti_rows >= 2) AS ti_tested,
    ((a.their_ti_rows >= 2) AND (a.their_best_ti <= a.their_open_ti)) AS ti_held,
    (a.their_free_rows >= 2) AS free_rent_tested,
    ((a.their_free_rows >= 2) AND (a.their_best_free <= a.their_open_free)) AS free_rent_held,
    (a.their_term_rows >= 2) AS term_tested,
    ((a.their_term_rows >= 2) AND (a.their_term_values = 1)) AS term_fixed,
    (a.their_esc_rows >= 2) AS escalator_tested,
    ((a.their_esc_rows >= 2) AND (a.their_esc_values = 1)) AS escalator_fixed,
    a.submarket_condition
   FROM (((agg a
     JOIN public.deal d ON ((d.id = a.deal_id)))
     LEFT JOIN cp c ON ((c.deal_id = a.deal_id)))
     LEFT JOIN cp_n n ON ((n.deal_id = a.deal_id)));


--
-- Name: VIEW v_negotiation_deal; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_negotiation_deal IS 'One row per deal per live listing_side counterparty (0064). `qualifies` is the exclusion gate and everything downstream passes through it: a deal where OUR side never countered contributes NOTHING to any score, because a counterparty who was never pushed has not been shown to be hard to move. The one negotiation in production is a round-1 acceptance and is excluded by exactly that rule. Deals with no listing_side party, or with more than one, are excluded too and say so in exclusion_reason rather than disappearing.';


--
-- Name: COLUMN v_negotiation_deal.our_share_of_movement; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_negotiation_deal.our_share_of_movement IS 'Of all the ground that closed between the two opening positions, the fraction WE gave. Joe''s single best measure of how hard someone is to move. NULL when total movement is zero or negative, never 0 — no movement is not the same as no concession by us.';


--
-- Name: v_counterparty_scorecard; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_counterparty_scorecard AS
 WITH bluff AS (
         SELECT v_counterparty_bluff.counterparty_id,
            sum(v_counterparty_bluff.claims_made) AS claims_made,
            sum(v_counterparty_bluff.claims_testable) AS claims_testable,
            sum(v_counterparty_bluff.claims_reversed) AS claims_reversed
           FROM public.v_counterparty_bluff
          WHERE v_counterparty_bluff.falsifiable
          GROUP BY v_counterparty_bluff.counterparty_id
        ), rolled AS (
         SELECT d.counterparty_id,
            count(*) AS deals_seen,
            count(*) FILTER (WHERE d.qualifies) AS n,
            count(*) FILTER (WHERE (NOT d.qualifies)) AS deals_excluded,
            count(*) FILTER (WHERE (NOT d.counter_tested)) AS deals_untested,
            count(*) FILTER (WHERE (d.listing_side_parties > 1)) AS deals_ambiguous_side,
            sum(d.rounds_ours) AS rounds_ours_total,
            sum(d.rounds_theirs) AS rounds_theirs_total,
            avg(d.our_share_of_movement) FILTER (WHERE d.qualifies) AS asym,
            avg(d.their_concession_frac) FILTER (WHERE d.qualifies) AS concession_frac,
            avg(d.opening_spread_frac) FILTER (WHERE d.qualifies) AS opening_spread,
            avg((d.rounds_total)::numeric) FILTER (WHERE (d.qualifies AND d.converged)) AS rounds_to_settle,
            count(*) FILTER (WHERE (d.qualifies AND d.rate_tested)) AS rate_tested,
            count(*) FILTER (WHERE (d.qualifies AND d.rate_held)) AS rate_held,
            count(*) FILTER (WHERE (d.qualifies AND d.ti_tested)) AS ti_tested,
            count(*) FILTER (WHERE (d.qualifies AND d.ti_held)) AS ti_held,
            count(*) FILTER (WHERE (d.qualifies AND d.free_rent_tested)) AS free_tested,
            count(*) FILTER (WHERE (d.qualifies AND d.free_rent_held)) AS free_held,
            count(*) FILTER (WHERE (d.qualifies AND d.term_tested)) AS term_tested,
            count(*) FILTER (WHERE (d.qualifies AND d.term_fixed)) AS term_fixed,
            count(*) FILTER (WHERE (d.qualifies AND d.escalator_tested)) AS esc_tested,
            count(*) FILTER (WHERE (d.qualifies AND d.escalator_fixed)) AS esc_fixed,
            avg((sc.tightness)::numeric) FILTER (WHERE d.qualifies) AS avg_tightness,
            count(*) FILTER (WHERE (d.qualifies AND (d.submarket_condition IS NULL))) AS deals_condition_unrecorded
           FROM (public.v_negotiation_deal d
             LEFT JOIN public.submarket_condition sc ON ((sc.slug = d.submarket_condition)))
          WHERE (d.counterparty_id IS NOT NULL)
          GROUP BY d.counterparty_id
        ), comps AS (
         SELECT r.counterparty_id,
            r.deals_seen,
            r.n,
            r.deals_excluded,
            r.deals_untested,
            r.deals_ambiguous_side,
            r.rounds_ours_total,
            r.rounds_theirs_total,
            r.asym,
            r.concession_frac,
            r.opening_spread,
            r.rounds_to_settle,
            r.rate_tested,
            r.rate_held,
            r.ti_tested,
            r.ti_held,
            r.free_tested,
            r.free_held,
            r.term_tested,
            r.term_fixed,
            r.esc_tested,
            r.esc_fixed,
            r.avg_tightness,
            r.deals_condition_unrecorded,
            b.claims_made,
            b.claims_testable,
            b.claims_reversed,
                CASE
                    WHEN (r.asym IS NOT NULL) THEN LEAST((1)::numeric, GREATEST((0)::numeric, r.asym))
                    ELSE NULL::numeric
                END AS c_asym,
            ((1)::numeric - r.concession_frac) AS c_grip,
                CASE
                    WHEN (COALESCE(b.claims_testable, (0)::numeric) > (0)::numeric) THEN ((1)::numeric - (b.claims_reversed / b.claims_testable))
                    ELSE NULL::numeric
                END AS c_bluff,
                CASE
                    WHEN (((r.rate_tested + r.ti_tested) + r.free_tested) > 0) THEN ((((r.rate_held + r.ti_held) + r.free_held))::numeric / (((r.rate_tested + r.ti_tested) + r.free_tested))::numeric)
                    ELSE NULL::numeric
                END AS c_hold
           FROM (rolled r
             LEFT JOIN bluff b ON ((b.counterparty_id = r.counterparty_id)))
        ), weighted AS (
         SELECT c_1.counterparty_id,
            c_1.deals_seen,
            c_1.n,
            c_1.deals_excluded,
            c_1.deals_untested,
            c_1.deals_ambiguous_side,
            c_1.rounds_ours_total,
            c_1.rounds_theirs_total,
            c_1.asym,
            c_1.concession_frac,
            c_1.opening_spread,
            c_1.rounds_to_settle,
            c_1.rate_tested,
            c_1.rate_held,
            c_1.ti_tested,
            c_1.ti_held,
            c_1.free_tested,
            c_1.free_held,
            c_1.term_tested,
            c_1.term_fixed,
            c_1.esc_tested,
            c_1.esc_fixed,
            c_1.avg_tightness,
            c_1.deals_condition_unrecorded,
            c_1.claims_made,
            c_1.claims_testable,
            c_1.claims_reversed,
            c_1.c_asym,
            c_1.c_grip,
            c_1.c_bluff,
            c_1.c_hold,
            ((((45 * ((c_1.c_asym IS NOT NULL))::integer) + (20 * ((c_1.c_grip IS NOT NULL))::integer)) + (15 * ((c_1.c_bluff IS NOT NULL))::integer)) + (20 * ((c_1.c_hold IS NOT NULL))::integer)) AS weight_sum,
            (((((45)::numeric * COALESCE(c_1.c_asym, (0)::numeric)) + ((20)::numeric * COALESCE(c_1.c_grip, (0)::numeric))) + ((15)::numeric * COALESCE(c_1.c_bluff, (0)::numeric))) + ((20)::numeric * COALESCE(c_1.c_hold, (0)::numeric))) AS weight_num,
            (((((c_1.c_asym IS NOT NULL))::integer + ((c_1.c_grip IS NOT NULL))::integer) + ((c_1.c_bluff IS NOT NULL))::integer) + ((c_1.c_hold IS NOT NULL))::integer) AS components
           FROM comps c_1
        ), scored AS (
         SELECT w.counterparty_id,
            w.deals_seen,
            w.n,
            w.deals_excluded,
            w.deals_untested,
            w.deals_ambiguous_side,
            w.rounds_ours_total,
            w.rounds_theirs_total,
            w.asym,
            w.concession_frac,
            w.opening_spread,
            w.rounds_to_settle,
            w.rate_tested,
            w.rate_held,
            w.ti_tested,
            w.ti_held,
            w.free_tested,
            w.free_held,
            w.term_tested,
            w.term_fixed,
            w.esc_tested,
            w.esc_fixed,
            w.avg_tightness,
            w.deals_condition_unrecorded,
            w.claims_made,
            w.claims_testable,
            w.claims_reversed,
            w.c_asym,
            w.c_grip,
            w.c_bluff,
            w.c_hold,
            w.weight_sum,
            w.weight_num,
            w.components,
                CASE
                    WHEN ((w.components >= 2) AND (w.weight_sum > 0)) THEN round((((100)::numeric * w.weight_num) / (w.weight_sum)::numeric), 1)
                    ELSE NULL::numeric
                END AS hardness_absolute
           FROM weighted w
        ), eligible AS (
         SELECT s.counterparty_id,
            s.deals_seen,
            s.n,
            s.deals_excluded,
            s.deals_untested,
            s.deals_ambiguous_side,
            s.rounds_ours_total,
            s.rounds_theirs_total,
            s.asym,
            s.concession_frac,
            s.opening_spread,
            s.rounds_to_settle,
            s.rate_tested,
            s.rate_held,
            s.ti_tested,
            s.ti_held,
            s.free_tested,
            s.free_held,
            s.term_tested,
            s.term_fixed,
            s.esc_tested,
            s.esc_fixed,
            s.avg_tightness,
            s.deals_condition_unrecorded,
            s.claims_made,
            s.claims_testable,
            s.claims_reversed,
            s.c_asym,
            s.c_grip,
            s.c_bluff,
            s.c_hold,
            s.weight_sum,
            s.weight_num,
            s.components,
            s.hardness_absolute,
            ((s.n >= 3) AND (s.hardness_absolute IS NOT NULL)) AS curve_eligible
           FROM scored s
        ), curved AS (
         SELECT e.counterparty_id,
            e.deals_seen,
            e.n,
            e.deals_excluded,
            e.deals_untested,
            e.deals_ambiguous_side,
            e.rounds_ours_total,
            e.rounds_theirs_total,
            e.asym,
            e.concession_frac,
            e.opening_spread,
            e.rounds_to_settle,
            e.rate_tested,
            e.rate_held,
            e.ti_tested,
            e.ti_held,
            e.free_tested,
            e.free_held,
            e.term_tested,
            e.term_fixed,
            e.esc_tested,
            e.esc_fixed,
            e.avg_tightness,
            e.deals_condition_unrecorded,
            e.claims_made,
            e.claims_testable,
            e.claims_reversed,
            e.c_asym,
            e.c_grip,
            e.c_bluff,
            e.c_hold,
            e.weight_sum,
            e.weight_num,
            e.components,
            e.hardness_absolute,
            e.curve_eligible,
            sum((e.curve_eligible)::integer) OVER () AS field_n,
                CASE
                    WHEN e.curve_eligible THEN percent_rank() OVER (PARTITION BY e.curve_eligible ORDER BY e.hardness_absolute)
                    ELSE NULL::double precision
                END AS pr
           FROM eligible e
        ), banded AS (
         SELECT c_1.counterparty_id,
            c_1.deals_seen,
            c_1.n,
            c_1.deals_excluded,
            c_1.deals_untested,
            c_1.deals_ambiguous_side,
            c_1.rounds_ours_total,
            c_1.rounds_theirs_total,
            c_1.asym,
            c_1.concession_frac,
            c_1.opening_spread,
            c_1.rounds_to_settle,
            c_1.rate_tested,
            c_1.rate_held,
            c_1.ti_tested,
            c_1.ti_held,
            c_1.free_tested,
            c_1.free_held,
            c_1.term_tested,
            c_1.term_fixed,
            c_1.esc_tested,
            c_1.esc_fixed,
            c_1.avg_tightness,
            c_1.deals_condition_unrecorded,
            c_1.claims_made,
            c_1.claims_testable,
            c_1.claims_reversed,
            c_1.c_asym,
            c_1.c_grip,
            c_1.c_bluff,
            c_1.c_hold,
            c_1.weight_sum,
            c_1.weight_num,
            c_1.components,
            c_1.hardness_absolute,
            c_1.curve_eligible,
            c_1.field_n,
            c_1.pr,
                CASE
                    WHEN (c_1.curve_eligible AND (c_1.field_n >= 3)) THEN round((((1)::double precision + ((9)::double precision * c_1.pr)))::numeric, 1)
                    ELSE NULL::numeric
                END AS threat_rating,
            LEAST(4.5, GREATEST(0.5, round((6.0 / sqrt((GREATEST(c_1.n, (1)::bigint))::numeric)), 2))) AS half_width
           FROM curved c_1
        )
 SELECT c.counterparty_id,
    p.name AS counterparty_name,
    c.n,
        CASE
            WHEN (c.n >= 6) THEN 'rated'::text
            WHEN (c.n >= 3) THEN 'provisional'::text
            ELSE 'unrated'::text
        END AS n_band,
    c.hardness_absolute,
    c.threat_rating,
        CASE
            WHEN (c.threat_rating IS NOT NULL) THEN GREATEST(1.0, round((c.threat_rating - c.half_width), 1))
            ELSE NULL::numeric
        END AS rating_low,
        CASE
            WHEN (c.threat_rating IS NOT NULL) THEN LEAST(10.0, round((c.threat_rating + c.half_width), 1))
            ELSE NULL::numeric
        END AS rating_high,
    c.field_n,
    jsonb_strip_nulls(jsonb_build_object('rate',
        CASE
            WHEN (c.rate_tested >= 2) THEN jsonb_build_object('verdict',
            CASE
                WHEN (c.rate_held = c.rate_tested) THEN 'holds'::text
                WHEN (c.rate_held = 0) THEN 'concedes'::text
                ELSE 'mixed'::text
            END, 'deals_tested', c.rate_tested, 'deals_held', c.rate_held)
            ELSE NULL::jsonb
        END, 'ti',
        CASE
            WHEN (c.ti_tested >= 2) THEN jsonb_build_object('verdict',
            CASE
                WHEN (c.ti_held = c.ti_tested) THEN 'holds'::text
                WHEN (c.ti_held = 0) THEN 'concedes'::text
                ELSE 'mixed'::text
            END, 'deals_tested', c.ti_tested, 'deals_held', c.ti_held)
            ELSE NULL::jsonb
        END, 'free_rent',
        CASE
            WHEN (c.free_tested >= 2) THEN jsonb_build_object('verdict',
            CASE
                WHEN (c.free_held = c.free_tested) THEN 'holds'::text
                WHEN (c.free_held = 0) THEN 'concedes'::text
                ELSE 'mixed'::text
            END, 'deals_tested', c.free_tested, 'deals_held', c.free_held)
            ELSE NULL::jsonb
        END, 'term',
        CASE
            WHEN (c.term_tested >= 2) THEN jsonb_build_object('verdict',
            CASE
                WHEN (c.term_fixed = c.term_tested) THEN 'fixed'::text
                WHEN (c.term_fixed = 0) THEN 'flexible'::text
                ELSE 'mixed'::text
            END, 'deals_tested', c.term_tested, 'deals_fixed', c.term_fixed)
            ELSE NULL::jsonb
        END, 'escalator',
        CASE
            WHEN (c.esc_tested >= 2) THEN jsonb_build_object('verdict',
            CASE
                WHEN (c.esc_fixed = c.esc_tested) THEN 'fixed'::text
                WHEN (c.esc_fixed = 0) THEN 'flexible'::text
                ELSE 'mixed'::text
            END, 'deals_tested', c.esc_tested, 'deals_fixed', c.esc_fixed)
            ELSE NULL::jsonb
        END)) AS category_profile,
    NULLIF(concat_ws('; '::text,
        CASE
            WHEN (c.rate_tested >= 2) THEN (
            CASE
                WHEN (c.rate_held = c.rate_tested) THEN 'holds'::text
                WHEN (c.rate_held = 0) THEN 'concedes'::text
                ELSE 'mixed on'::text
            END || ' rate'::text)
            ELSE NULL::text
        END,
        CASE
            WHEN (c.ti_tested >= 2) THEN (
            CASE
                WHEN (c.ti_held = c.ti_tested) THEN 'holds'::text
                WHEN (c.ti_held = 0) THEN 'concedes'::text
                ELSE 'mixed on'::text
            END || ' TI'::text)
            ELSE NULL::text
        END,
        CASE
            WHEN (c.free_tested >= 2) THEN (
            CASE
                WHEN (c.free_held = c.free_tested) THEN 'holds'::text
                WHEN (c.free_held = 0) THEN 'concedes'::text
                ELSE 'mixed on'::text
            END || ' free rent'::text)
            ELSE NULL::text
        END,
        CASE
            WHEN (c.term_tested >= 2) THEN (
            CASE
                WHEN (c.term_fixed = c.term_tested) THEN 'fixed'::text
                WHEN (c.term_fixed = 0) THEN 'flexible'::text
                ELSE 'mixed'::text
            END || ' term'::text)
            ELSE NULL::text
        END,
        CASE
            WHEN (c.esc_tested >= 2) THEN (
            CASE
                WHEN (c.esc_fixed = c.esc_tested) THEN 'fixed'::text
                WHEN (c.esc_fixed = 0) THEN 'flexible'::text
                ELSE 'mixed'::text
            END || ' escalator'::text)
            ELSE NULL::text
        END), ''::text) AS profile_line,
    round(c.asym, 3) AS avg_our_share_of_movement,
    round(c.concession_frac, 3) AS avg_their_concession_frac,
    round(c.opening_spread, 3) AS avg_opening_spread_frac,
    round(c.rounds_to_settle, 1) AS avg_rounds_to_settle,
    c.claims_made,
    c.claims_testable,
    c.claims_reversed,
        CASE
            WHEN (COALESCE(c.claims_testable, (0)::numeric) > (0)::numeric) THEN ((('reversed '::text || c.claims_reversed) || ' of '::text) || c.claims_testable)
            WHEN (COALESCE(c.claims_made, (0)::numeric) > (0)::numeric) THEN (('made '::text || c.claims_made) || ', none testable yet'::text)
            ELSE NULL::text
        END AS bluff_as_observed,
    c.components AS composite_components,
    round(c.avg_tightness, 2) AS avg_submarket_tightness,
    c.deals_condition_unrecorded,
    c.deals_seen,
    c.deals_excluded,
    c.deals_untested,
    c.deals_ambiguous_side,
    c.rounds_ours_total,
    c.rounds_theirs_total,
        CASE
            WHEN ((c.n = 0) AND (c.deals_untested = c.deals_seen)) THEN 'our side never countered on any recorded deal — this counterparty has not been tested and no number is available'::text
            WHEN (c.n = 0) THEN (((('no deal qualifies: '::text || c.deals_untested) || ' untested, '::text) || c.deals_ambiguous_side) || ' with an ambiguous listing side'::text)
            WHEN (c.n < 3) THEN (('n = '::text || c.n) || ' — below the floor for a curved rating; the absolute and the raw rounds are all that is honest here'::text)
            WHEN (c.field_n < 3) THEN 'the field holds fewer than 3 rated counterparties, so there is nothing to curve against'::text
            WHEN (c.hardness_absolute IS NULL) THEN 'fewer than two composite components have data'::text
            ELSE NULL::text
        END AS why_no_number
   FROM (banded c
     JOIN public.party p ON ((p.id = c.counterparty_id)));


--
-- Name: VIEW v_counterparty_scorecard; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_counterparty_scorecard IS 'How hard a listing agent is to move: absolute metrics, a read-time 1-10 threat rating, and a category profile (0064). 10 = hardest. A THREAT RATING, not a performance review. Nothing here is stored — hardness_absolute is a fixed formula independent of who else is in the table, and threat_rating is percent_rank over the CURRENT field, so a new toughest agent becomes the new 10.0 without rescaling one stored value. Deals where OUR side never countered contribute NOTHING, which is why the single negotiation in production yields no number: a counterparty who was never pushed has not been shown to be hard to move.';


--
-- Name: COLUMN v_counterparty_scorecard.threat_rating; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_counterparty_scorecard.threat_rating IS '1-10, computed at READ TIME as percent_rank over every counterparty with n >= 3. NULL below n = 3, and NULL whenever the field itself holds fewer than 3 rated counterparties — a curve over one person is a number about nobody. Always read it beside n and field_n.';


--
-- Name: COLUMN v_counterparty_scorecard.rating_low; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_counterparty_scorecard.rating_low IS 'A SPREAD HEURISTIC, 6.0/sqrt(n) clamped to [0.5, 4.5] — deliberately NOT a confidence interval. There is no distribution here to build one from, and a statistical-looking band on n = 4 would be the same false precision the bands exist to prevent.';


--
-- Name: COLUMN v_counterparty_scorecard.category_profile; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_counterparty_scorecard.category_profile IS 'Per-term-category verdict with the deal count it rests on. rate / TI / free rent use holds-concedes-mixed because the direction of a concession is unambiguous. term and escalator use fixed-flexible-mixed because it is NOT — a longer term is not obviously worse for a tenant — and those two are excluded from the composite for that reason. A category needs 2 tested deals before it says anything.';


--
-- Name: COLUMN v_counterparty_scorecard.avg_rounds_to_settle; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_counterparty_scorecard.avg_rounds_to_settle IS 'Reported, never scored. Many rounds can mean they were immovable or that we ground them down, and nothing in the record distinguishes those two. Feeding it into the composite would put an ambiguous signal behind a decisive-looking number.';


--
-- Name: COLUMN v_counterparty_scorecard.deals_seen; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.v_counterparty_scorecard.deals_seen IS 'Deals against this counterparty on which ANY negotiation round is recorded — not every deal they appear on. A listing agent on a deal where nobody logged a round is invisible here, correctly: this view measures negotiations, and an unlogged negotiation is not one. deals_seen minus n is what was thrown away, and deals_untested / deals_ambiguous_side say why.';


--
-- Name: v_deal_board; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_deal_board AS
 SELECT d.id,
    d.name,
    c.roster_ref AS client_ref,
    pc.name AS client_name,
    d.deal_type,
    d.phase,
    ph.sort AS phase_sort,
    d.segment,
    d.outcome,
    lead_actor.slug AS lead_owner,
    lt.last_touch,
    d.notes_path
   FROM ((((((public.deal d
     JOIN public.client c ON ((c.id = d.client_id)))
     JOIN public.party pc ON ((pc.id = c.party_id)))
     JOIN public.deal_phase ph ON ((ph.slug = d.phase)))
     LEFT JOIN public.deal_participant dp ON (((dp.deal_id = d.id) AND (dp.role = 'lead'::text) AND (dp.to_at IS NULL))))
     LEFT JOIN public.actor lead_actor ON ((lead_actor.id = dp.actor_id)))
     LEFT JOIN public.v_last_touch lt ON (((lt.subject_type = 'deal'::text) AND (lt.subject_id = d.id))));


--
-- Name: v_deal_reconciliation_read; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_deal_reconciliation_read AS
 SELECT id,
    name,
    salesforce_id,
    version AS base_version,
    phase,
    outcome,
    closed_on
   FROM public.deal d;


--
-- Name: v_deal_room_account; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_deal_room_account AS
 SELECT c.id AS account_client_id,
    c.roster_ref AS account_client_ref,
    p.name AS account_name,
    a.slug AS account_owner,
    count(d.id) FILTER (WHERE ((d.outcome IS NULL) AND (d.operating_state = 'active'::text))) AS open_deals,
    count(d.id) FILTER (WHERE ((d.outcome IS NULL) AND (d.operating_state = 'active'::text) AND d.attention)) AS attention_deals,
    count(d.id) FILTER (WHERE ((d.outcome IS NULL) AND (d.operating_state = 'active'::text) AND (d.next_date < CURRENT_DATE))) AS overdue_deals,
    count(d.id) FILTER (WHERE ((d.outcome IS NULL) AND (d.operating_state = 'active'::text) AND ((lt.last_touch IS NULL) OR (lt.last_touch < (CURRENT_DATE - 14))))) AS stale_deals,
    ( SELECT max(rs.ended_at) AS max
           FROM public.deal_review_session rs
          WHERE ((rs.account_client_id = c.id) AND (rs.status = 'completed'::text))) AS last_review_at,
    count(d.id) FILTER (WHERE ((d.outcome IS NULL) AND (d.operating_state = 'parked'::text))) AS parked_deals
   FROM ((((((public.client c
     JOIN public.party p ON ((p.id = c.party_id)))
     LEFT JOIN public.national_account_owner nao ON ((nao.account_client_id = c.id)))
     LEFT JOIN public.actor a ON ((a.id = nao.owner_actor_id)))
     LEFT JOIN public.v_client_account vca ON (((vca.account_client_id = c.id) AND vca.is_sub_client)))
     LEFT JOIN public.deal d ON ((d.client_id = vca.client_id)))
     LEFT JOIN public.v_last_touch lt ON (((lt.subject_type = 'deal'::text) AND (lt.subject_id = d.id))))
  WHERE ((c.client_type = 'national_account'::text) AND (c.merged_into IS NULL))
  GROUP BY c.id, c.roster_ref, p.name, a.slug;


--
-- Name: v_deal_room_action; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_deal_room_action AS
 SELECT n.id,
    n.subject_id AS deal_id,
    a.slug AS owner,
    n.description,
    n.due_on,
    n.status,
    n.updated_at
   FROM (public.next_action n
     JOIN public.actor a ON ((a.id = n.owner_id)))
  WHERE (n.subject_type = 'deal'::text)
UNION ALL
 SELECT pca.id,
    pca.deal_id,
    a.slug AS owner,
    pca.description,
    pca.due_on,
    pca.status,
    pca.updated_at
   FROM (public.capture_post_call_action pca
     JOIN public.actor a ON ((a.id = pca.owner_id)));


--
-- Name: v_deal_room_activity; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_deal_room_activity AS
 SELECT x.id,
    x.deal_id,
    x.occurred_at,
    a.slug AS actor,
    x.kind,
    x.summary,
    x.detail,
    x.source
   FROM (public.activity x
     JOIN public.actor a ON ((a.id = x.actor_id)))
  WHERE (x.deal_id IS NOT NULL);


--
-- Name: v_deal_room_board; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_deal_room_board AS
 SELECT d.id,
    d.name,
    d.deal_type AS type,
    d.phase,
    d.owner,
    d.attention,
    d.next_date,
    COALESCE(( SELECT n.description
           FROM public.next_action n
          WHERE ((n.subject_type = 'deal'::text) AND (n.subject_id = d.id) AND (n.status = 'open'::text))
          ORDER BY n.updated_at DESC, n.id DESC
         LIMIT 1), ( SELECT n.text
           FROM public.deal_note n
          WHERE ((n.deal_id = d.id) AND (n.kind = 'next_step'::text))
          ORDER BY n.created_at DESC, n.id DESC
         LIMIT 1)) AS next_step,
    d.city AS market,
    d.segment,
    c.id AS client_id,
    c.roster_ref AS client_ref,
    cp.name AS client_name,
    vca.account_client_id,
    vca.account_client_ref,
    vca.account_name,
    ao.slug AS account_owner,
    dma.agent_name AS market_agent,
    dma.agent_party_id AS market_agent_party_id,
    lt.last_touch,
    ( SELECT max(i.reviewed_at) AS max
           FROM (public.deal_review_item i
             JOIN public.deal_review_session s ON ((s.id = i.session_id)))
          WHERE ((i.deal_id = d.id) AND (i.disposition = 'reviewed'::text) AND (s.status = 'completed'::text))) AS last_review_at,
        CASE
            WHEN (vca.account_client_id IS NULL) THEN 'team'::text
            ELSE 'national_account'::text
        END AS workspace_kind,
    d.operating_state,
    d.parking_reason,
    d.parking_note,
    d.parked_at,
    pa.slug AS parked_by
   FROM ((((((((public.deal d
     JOIN public.client c ON ((c.id = d.client_id)))
     JOIN public.party cp ON ((cp.id = c.party_id)))
     LEFT JOIN public.v_client_account vca ON (((vca.client_id = c.id) AND vca.is_sub_client)))
     LEFT JOIN public.national_account_owner nao ON ((nao.account_client_id = vca.account_client_id)))
     LEFT JOIN public.actor ao ON ((ao.id = nao.owner_actor_id)))
     LEFT JOIN public.deal_market_assignment dma ON ((dma.deal_id = d.id)))
     LEFT JOIN public.v_last_touch lt ON (((lt.subject_type = 'deal'::text) AND (lt.subject_id = d.id))))
     LEFT JOIN public.actor pa ON ((pa.id = d.parked_by)))
  WHERE (d.outcome IS NULL);


--
-- Name: v_deal_room_critical_date; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_deal_room_critical_date AS
 SELECT id,
    deal_id,
    kind,
    due_on,
    note,
    source,
    status
   FROM public.critical_date c;


--
-- Name: v_deal_room_deal; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_deal_room_deal AS
 SELECT id,
    phase,
    owner,
    deal_type AS type,
    city,
    segment,
    attention,
    next_date,
    name,
    operating_state,
    parking_reason,
    parking_note,
    parked_at
   FROM public.deal d;


--
-- Name: v_deal_room_document; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_deal_room_document AS
 SELECT id,
    deal_id,
    sent_status,
    lint_passed,
    leak_check_passed,
    prepared_at,
    note
   FROM public.document
  WHERE (deal_id IS NOT NULL);


--
-- Name: v_deal_room_event; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_deal_room_event AS
 SELECT e.id,
    e.recorded_at,
    a.slug AS actor,
    e.verb,
    e.subject_type,
    e.subject_id,
    e.field,
    (e.old_value - ARRAY['sf_commission_placeholder'::text, 'sf_close_date_placeholder'::text]) AS old_value,
    (e.new_value - ARRAY['sf_commission_placeholder'::text, 'sf_close_date_placeholder'::text]) AS new_value
   FROM (public.event e
     JOIN public.actor a ON ((a.id = e.actor_id)))
  WHERE ((e.subject_type = 'deal'::text) AND (e.field IS DISTINCT FROM 'sf_commission_placeholder'::text) AND (e.field IS DISTINCT FROM 'sf_close_date_placeholder'::text));


--
-- Name: v_deal_room_negotiation; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_deal_room_negotiation AS
 SELECT id,
    deal_id,
    round_no,
    side,
    proposed_on,
    rate_amount,
    rate_basis,
    rate_norm_sf_yr,
    ti_amount,
    ti_basis,
    free_rent_months,
    term_months,
    escalator,
    opex_note,
    expires_on,
    note,
    source
   FROM public.negotiation_round;


--
-- Name: v_deal_room_note; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_deal_room_note AS
 SELECT n.id,
    n.deal_id,
    n.kind,
    n.text,
    a.slug AS actor,
    n.created_at
   FROM (public.deal_note n
     JOIN public.actor a ON ((a.id = n.actor_id)));


--
-- Name: v_deal_room_participant; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_deal_room_participant AS
 SELECT dp.id,
    dp.deal_id,
    dp.role,
    COALESCE(a.display_name, p.name) AS name,
    a.slug AS actor,
    p.id AS party_id
   FROM ((public.deal_participant dp
     LEFT JOIN public.actor a ON ((a.id = dp.actor_id)))
     LEFT JOIN public.party p ON ((p.id = dp.party_id)))
  WHERE (dp.to_at IS NULL);


--
-- Name: v_deal_room_premises; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_deal_room_premises AS
 SELECT pr.id,
    pr.deal_id,
    pr.label,
    b.name AS building_name,
    b.address,
    b.city,
    b.state,
    s.suite,
    s.area_amount,
    s.area_basis,
    pr.created_at
   FROM (((public.premises pr
     LEFT JOIN public.premises_space ps ON ((ps.premises_id = pr.id)))
     LEFT JOIN public.space s ON ((s.id = ps.space_id)))
     LEFT JOIN public.building b ON ((b.id = s.building_id)));


--
-- Name: v_deal_room_presence; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_deal_room_presence AS
 SELECT a.slug AS actor,
    p.deal_id,
    p.field,
    p.expires_at
   FROM (public.deal_presence_lease p
     JOIN public.actor a ON ((a.id = p.actor_id)));


--
-- Name: v_deal_room_session; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_deal_room_session AS
SELECT
    NULL::uuid AS session_id,
    NULL::text AS workspace_kind,
    NULL::uuid AS account_client_id,
    NULL::text AS started_by,
    NULL::timestamp with time zone AS started_at,
    NULL::timestamp with time zone AS ended_at,
    NULL::text AS status,
    NULL::text AS summary,
    NULL::bigint AS reviewed_count,
    NULL::bigint AS skipped_count;


--
-- Name: v_decision_entry; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_decision_entry AS
 SELECT rs.external_key,
    split_part(rs.external_key, '#'::text, 1) AS source_file,
    split_part(rs.external_key, '#'::text, 2) AS session_key,
    (e.occurred_at)::date AS entry_date,
    act.slug AS author,
    (e.new_value ->> 'title'::text) AS title,
    e.human_quote,
    e.agent_rationale,
    e.cause,
    ((e.new_value ->> 'quote_absent'::text))::boolean AS quote_absent,
    (e.new_value ->> 'provenance'::text) AS provenance,
    e.subject_id AS decision_id,
    e.id AS event_id,
    (e.new_value ->> 'cost_delta'::text) AS cost_delta,
    (e.new_value ->> 'quality_delta'::text) AS quality_delta,
    (e.new_value ? 'cost_delta'::text) AS priced,
    e.occurred_at
   FROM ((public.record_source rs
     JOIN public.event e ON (((e.id = rs.entity_id) AND (rs.entity_type = 'event'::text))))
     JOIN public.actor act ON ((act.id = e.actor_id)))
  WHERE (rs.source_system = 'decision-history'::text);


--
-- Name: VIEW v_decision_entry; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_decision_entry IS 'One row per logged decision, as decision-history.md renders it. 0085 added cost_delta / quality_delta / priced: what a build cost and what it bought, recorded at the moment it shipped. 0110 added occurred_at, the full timestamp behind entry_date: ORDER BY occurred_at DESC, never by entry_date alone, or entries logged on the same day sort arbitrarily and a byte-budgeted render drops whichever ones luck puts last. entry_date remains a date and remains what the render prints as a heading.';


--
-- Name: v_defect; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_defect AS
 SELECT d.id,
    d.occurred_on,
    d.defect_class,
    d.claimed,
    d.actual,
    d.source_unread,
    d.rule_violated,
    d.detected_by,
    d.session_key,
    d.cost_note,
    r.statement AS rule_statement,
    a.slug AS recorded_by,
    d.created_at
   FROM ((public.defect d
     LEFT JOIN public.actor a ON ((a.id = d.created_by)))
     LEFT JOIN public.rule r ON ((((r.id)::text = d.rule_violated) OR ("left"((r.id)::text, 8) = lower(btrim(COALESCE(d.rule_violated, ''::text)))))));


--
-- Name: VIEW v_defect; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_defect IS 'Every defect with its rule statement and author resolved (0103). carr_reader holds no grant on any base table, so this is the only way a read session sees the log at all.';


--
-- Name: v_defect_class; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_defect_class AS
 SELECT defect_class,
    (count(*))::integer AS occurrences,
    min(occurred_on) AS first_seen,
    max(occurred_on) AS last_seen,
    (count(*) FILTER (WHERE (detected_by = 'human'::text)))::integer AS caught_by_human,
    (array_agg(DISTINCT source_unread) FILTER (WHERE (source_unread IS NOT NULL)))[1:5] AS sources_unread,
    (array_agg(DISTINCT rule_violated) FILTER (WHERE (rule_violated IS NOT NULL)))[1:5] AS rules_violated
   FROM public.defect
  GROUP BY defect_class;


--
-- Name: VIEW v_defect_class; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_defect_class IS 'One row per defect class: how many times, when first and last, how many the HUMAN had to catch, and the artifacts that keep going unread (0103, loop #185). This is what a session is handed at start instead of being handed the prose rules and trusted.';


--
-- Name: v_ref_index; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_ref_index AS
 SELECT 'lead'::text AS subject_type,
    l.id AS subject_id,
    l.registry_ref AS ref,
    p.name AS display_name,
    org.name AS org_name,
    p.city,
    p.specialty,
    l.stage AS status,
    (p.merged_into IS NOT NULL) AS merged,
    NULL::text AS client_ref,
    p.id AS party_id
   FROM ((public.lead l
     JOIN public.party p ON ((p.id = l.party_id)))
     LEFT JOIN public.party org ON ((org.id = p.org_id)))
UNION ALL
 SELECT 'client'::text AS subject_type,
    c.id AS subject_id,
    c.roster_ref AS ref,
    p.name AS display_name,
    org.name AS org_name,
    p.city,
    p.specialty,
    c.status,
    (COALESCE(c.merged_into, p.merged_into) IS NOT NULL) AS merged,
    NULL::text AS client_ref,
    p.id AS party_id
   FROM ((public.client c
     JOIN public.party p ON ((p.id = c.party_id)))
     LEFT JOIN public.party org ON ((org.id = p.org_id)))
UNION ALL
 SELECT 'vendor'::text AS subject_type,
    v.id AS subject_id,
    v.vendor_ref AS ref,
    p.name AS display_name,
    org.name AS org_name,
    p.city,
    p.specialty,
    v.stage AS status,
    ((p.merged_into IS NOT NULL) OR (v.merged_into IS NOT NULL)) AS merged,
    NULL::text AS client_ref,
    p.id AS party_id
   FROM ((public.vendor v
     JOIN public.party p ON ((p.id = v.party_id)))
     LEFT JOIN public.party org ON ((org.id = p.org_id)))
UNION ALL
 SELECT 'deal'::text AS subject_type,
    d.id AS subject_id,
    NULL::text AS ref,
    d.name AS display_name,
    NULL::text AS org_name,
    NULL::text AS city,
    NULL::text AS specialty,
    d.phase AS status,
    false AS merged,
    c.roster_ref AS client_ref,
    NULL::uuid AS party_id
   FROM (public.deal d
     LEFT JOIN public.client c ON ((c.id = d.client_id)))
UNION ALL
 SELECT 'party'::text AS subject_type,
    p.id AS subject_id,
    p.ref,
    p.name AS display_name,
    org.name AS org_name,
    p.city,
    p.specialty,
    p.contact_state AS status,
    (p.merged_into IS NOT NULL) AS merged,
    NULL::text AS client_ref,
    p.id AS party_id
   FROM (public.party p
     LEFT JOIN public.party org ON ((org.id = p.org_id)))
  WHERE ((p.deleted_at IS NULL) AND (NOT (EXISTS ( SELECT 1
           FROM public.lead l
          WHERE (l.party_id = p.id)))) AND (NOT (EXISTS ( SELECT 1
           FROM public.client c
          WHERE (c.party_id = p.id)))) AND (NOT (EXISTS ( SELECT 1
           FROM public.vendor v
          WHERE (v.party_id = p.id)))));


--
-- Name: VIEW v_ref_index; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_ref_index IS 'Resolver surface for find/resolveSubject under the views-only reader role (amendment 11). SAFE COLUMNS ONLY — never add phone, email, notes, or any contact detail here; a reader-scoped session sees everything in this view. Five branches: lead/client/vendor/deal key a ROLE, and party (0056) keys a PERSON OR ORG holding no role at all — without it the view indexed roles, not subjects, and 432 of 1,084 parties including every org were unreachable by the primary lookup verb, which answered "no record matches that name" for 17 live Henry Schein rows. The party branch is disjoint from the other three by construction (no lead/client/vendor row exists for it), so nothing double-counts.';


--
-- Name: v_stale_records; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_stale_records AS
 SELECT 'deal'::text AS subject_type,
    d.id,
    d.name,
    lt.last_touch,
    (CURRENT_DATE - lt.last_touch) AS days_quiet
   FROM (public.deal d
     LEFT JOIN public.v_last_touch lt ON (((lt.subject_type = 'deal'::text) AND (lt.subject_id = d.id))))
  WHERE ((d.outcome IS NULL) AND (d.phase <> 'closed'::text) AND ((lt.last_touch IS NULL) OR (lt.last_touch < (CURRENT_DATE - 14))));


--
-- Name: v_today_triage; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_today_triage AS
 SELECT 'next_action'::text AS item_kind,
    na.id,
    na.subject_type,
    na.subject_id,
    owner.slug AS owner,
    na.description AS what,
    na.due_on,
    COALESCE(r.display_name, r.org_name) AS subject_name,
    r.ref AS subject_ref,
    public.carr_business_days(na.due_on, CURRENT_DATE) AS business_days_overdue
   FROM ((public.next_action na
     JOIN public.actor owner ON ((owner.id = na.owner_id)))
     LEFT JOIN public.v_ref_index r ON (((r.subject_id = na.subject_id) AND (r.subject_type = na.subject_type))))
  WHERE ((na.status = 'open'::text) AND (na.due_on IS NOT NULL) AND (na.due_on <= CURRENT_DATE) AND ((na.hold_until IS NULL) OR (na.hold_until <= CURRENT_DATE)))
UNION ALL
 SELECT 'post_call_action'::text AS item_kind,
    pca.id,
    'deal'::text AS subject_type,
    pca.deal_id AS subject_id,
    owner.slug AS owner,
    pca.description AS what,
    pca.due_on,
    COALESCE(r.display_name, r.org_name) AS subject_name,
    r.ref AS subject_ref,
    public.carr_business_days(pca.due_on, CURRENT_DATE) AS business_days_overdue
   FROM ((public.capture_post_call_action pca
     JOIN public.actor owner ON ((owner.id = pca.owner_id)))
     LEFT JOIN public.v_ref_index r ON (((r.subject_id = pca.deal_id) AND (r.subject_type = 'deal'::text))))
  WHERE ((pca.status = 'open'::text) AND (pca.due_on IS NOT NULL) AND (pca.due_on <= CURRENT_DATE))
UNION ALL
 SELECT 'critical_date'::text AS item_kind,
    cd.id,
    'deal'::text AS subject_type,
    cd.deal_id AS subject_id,
    NULL::text AS owner,
    (cd.kind || COALESCE((': '::text || cd.note), ''::text)) AS what,
    cd.due_on,
    COALESCE(r.display_name, r.org_name) AS subject_name,
    r.ref AS subject_ref,
    public.carr_business_days(cd.due_on, CURRENT_DATE) AS business_days_overdue
   FROM (public.critical_date cd
     LEFT JOIN public.v_ref_index r ON (((r.subject_id = cd.deal_id) AND (r.subject_type = 'deal'::text))))
  WHERE ((cd.status = 'open'::text) AND (cd.due_on <= (CURRENT_DATE + 14)))
UNION ALL
 SELECT 'ingest'::text AS item_kind,
    i.id,
    'inbox'::text AS subject_type,
    i.id AS subject_id,
    NULL::text AS owner,
    COALESCE(NULLIF(TRIM(BOTH FROM (i.payload ->> 'summary'::text)), ''::text), NULLIF(TRIM(BOTH FROM (i.payload ->> 'title'::text)), ''::text), NULLIF(TRIM(BOTH FROM (i.payload ->> 'subject'::text)), ''::text), (i.source || ' item awaiting triage'::text)) AS what,
    (i.received_at)::date AS due_on,
    NULLIF(TRIM(BOTH FROM (i.payload ->> 'organizer'::text)), ''::text) AS subject_name,
    NULL::text AS subject_ref,
    public.carr_business_days((i.received_at)::date, CURRENT_DATE) AS business_days_overdue
   FROM public.ingest_inbox i
  WHERE (i.status = 'new'::text);


--
-- Name: VIEW v_today_triage; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_today_triage IS 'What needs attention now, and legible enough to act on: every row carries the subject NAME and its ref, not only a uuid (rule 3a9dbafd), and an age in BUSINESS days (rule 236ca227). The ingest branch reads a title out of the stored payload for DISPLAY only — the payload stays untrusted and nothing here acts on what it says. Which rows appear is unchanged from 0032: dated, due, not held. The ~194 undated open commitments are still invisible, which is a separate defect whose fix is requiring a date at the door.';


--
-- Name: v_detector_health; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_detector_health AS
 SELECT 'stale-records'::text AS detector,
    ( SELECT count(*) AS count
           FROM public.v_stale_records) AS hits,
    ( SELECT count(*) AS count
           FROM public.deal
          WHERE ((deal.outcome IS NULL) AND (deal.phase <> 'closed'::text))) AS searchable,
    ( SELECT count(*) AS count
           FROM public.v_last_touch
          WHERE (v_last_touch.subject_type = 'deal'::text)) AS with_input,
        CASE
            WHEN (( SELECT count(*) AS count
               FROM public.v_last_touch
              WHERE (v_last_touch.subject_type = 'deal'::text)) = 0) THEN 'BLIND — no deal carries a last_touch, so silence proves nothing'::text
            WHEN (( SELECT count(*) AS count
               FROM public.v_stale_records) = 0) THEN 'CLEAR — evaluated against real input'::text
            ELSE 'REPORTING'::text
        END AS verdict
UNION ALL
 SELECT 'today-triage'::text AS detector,
    ( SELECT count(*) AS count
           FROM public.v_today_triage) AS hits,
    ( SELECT count(*) AS count
           FROM public.next_action
          WHERE (next_action.status = 'open'::text)) AS searchable,
    ( SELECT count(*) AS count
           FROM public.next_action
          WHERE ((next_action.status = 'open'::text) AND (next_action.due_on IS NOT NULL))) AS with_input,
        CASE
            WHEN (( SELECT count(*) AS count
               FROM public.next_action
              WHERE ((next_action.status = 'open'::text) AND (next_action.due_on IS NOT NULL))) = 0) THEN 'BLIND — no open action carries a due date'::text
            WHEN (( SELECT count(*) AS count
               FROM public.v_today_triage) = 0) THEN 'CLEAR — evaluated against real input'::text
            ELSE 'REPORTING'::text
        END AS verdict
UNION ALL
 SELECT 'capture-coverage'::text AS detector,
    ( SELECT count(*) AS count
           FROM public.v_capture_coverage
          WHERE (v_capture_coverage.pct_with_touch < (25)::numeric)) AS hits,
    ( SELECT count(*) AS count
           FROM public.v_capture_coverage) AS searchable,
    ( SELECT COALESCE(sum(v_capture_coverage.with_any_touch), (0)::numeric) AS "coalesce"
           FROM public.v_capture_coverage) AS with_input,
        CASE
            WHEN (( SELECT count(*) AS count
               FROM public.v_capture_coverage
              WHERE (v_capture_coverage.pct_with_touch < (25)::numeric)) > 0) THEN 'DEGRADED — a subject type is under 25% touch coverage; detectors downstream are guessing'::text
            ELSE 'CLEAR — evaluated against real input'::text
        END AS verdict;


--
-- Name: VIEW v_detector_health; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_detector_health IS 'Every detector with the DENOMINATOR it searched. A zero result against a zero-input population is BLIND, never CLEAR. Added 0034 because stale-records reported all-clear for two days while every deal it could have flagged had a falsified last_touch.';


--
-- Name: v_drip_conflict; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_drip_conflict AS
 SELECT l.registry_ref,
    p.name,
    l.stage,
    l.drip_campaign,
    c.roster_ref AS client_ref,
    c.status AS client_status
   FROM ((public.lead l
     JOIN public.party p ON ((p.id = l.party_id)))
     JOIN public.client c ON ((c.id = l.client_id)))
  WHERE ((l.drip_campaign IS NOT NULL) AND (l.drip_campaign !~~* '%client care%'::text) AND (l.suppressed = false) AND (c.status = ANY (ARRAY['active_deal'::text, 'client'::text, 'past_client'::text])));


--
-- Name: VIEW v_drip_conflict; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_drip_conflict IS 'Leads queued on a PROSPECTING drip whose linked client is live. Empty is the correct state. Added 0045 after four clients in active deals were found on the Monthly Newsletter — latent, since loop #47 (first send) has never fired, which is exactly why nothing caught it. "not ilike client care" rather than a list of prospecting campaigns: a list goes stale the first time someone adds a campaign and forgets this view exists.';


--
-- Name: v_expired_verification; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_expired_verification AS
 WITH floor_cfg AS (
         SELECT COALESCE(( SELECT ((system_config.value #>> '{}'::text[]))::integer AS int4
                   FROM public.system_config
                  WHERE (system_config.key = 'forgetting.age_floor_days'::text)), 30) AS days
        ), touches AS (
         SELECT COALESCE(a.vendor_id, a.client_id, a.lead_id, a.deal_id) AS subject_id,
            count(*) AS touch_count
           FROM public.activity a
          GROUP BY COALESCE(a.vendor_id, a.client_id, a.lead_id, a.deal_id)
        )
 SELECT f.id AS flag_id,
    f.subject_type,
    f.subject_id,
    f.kind,
    f.observed_at,
    f.expires_on,
        CASE
            WHEN ((f.expires_on IS NOT NULL) AND (f.expires_on < CURRENT_DATE)) THEN 'expired'::text
            ELSE 'unstamped_volatile'::text
        END AS reason,
    COALESCE(t.touch_count, (0)::bigint) AS subject_touches,
    ((f.expires_on IS NOT NULL) AND (f.expires_on < (CURRENT_DATE - ( SELECT floor_cfg.days
           FROM floor_cfg)))) AS past_age_floor
   FROM (public.record_flag f
     LEFT JOIN touches t ON ((t.subject_id = f.subject_id)))
  WHERE ((((f.expires_on IS NOT NULL) AND (f.expires_on < CURRENT_DATE)) OR ((f.kind = ANY (ARRAY['verified'::text, 'title'::text, 'email'::text, 'cell'::text, 'office_phone'::text])) AND (f.expires_on IS NULL) AND (f.observed_at < (now() - '180 days'::interval)))) AND (NOT (EXISTS ( SELECT 1
           FROM public.record_flag g
          WHERE ((g.subject_type = f.subject_type) AND (g.subject_id = f.subject_id) AND (g.id <> f.id) AND (g.observed_at > f.observed_at) AND ((g.kind = f.kind) OR (g.kind = 'verified'::text)) AND (g.expires_on IS NOT NULL) AND (g.expires_on >= CURRENT_DATE) AND ((g.value ->> 'found'::text) IS DISTINCT FROM 'false'::text))))))
  ORDER BY ((f.expires_on IS NOT NULL) AND (f.expires_on < (CURRENT_DATE - ( SELECT floor_cfg.days
           FROM floor_cfg)))) DESC, COALESCE(t.touch_count, (0)::bigint) DESC, f.observed_at;


--
-- Name: VIEW v_expired_verification; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_expired_verification IS 'The re-verify queue (0071, ordering 0073, supersession 0104). expired = the row said when it stops being trustworthy and that day passed; unstamped_volatile = a volatile-kind verification (title/contact facts age with promotions and job moves) never given an expiry and now older than 180 days. Either way the fact reads as UNVERIFIED for decisions until re-checked — nothing is deleted. 0104: a row leaves this queue once a NEWER flag on the same subject re-verifies it (same kind, or an umbrella ''verified'' pass) AND that newer flag is itself stamped and unexpired and is not a not-found row. Before 0104 the queue could never drain, because record-finding only adds rows — re-verifying a subject left the stale row in place forever.';


--
-- Name: v_export_clients; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_export_clients AS
 SELECT c.roster_ref AS "Client ID",
    p.name AS "Name",
    org.name AS "Practice / Entity",
    COALESCE(c.owner_label, owner.display_name) AS "Owner",
        CASE
            WHEN (c.merged_into IS NOT NULL) THEN ('Merged into '::text || mt.roster_ref)
            WHEN (c.status = 'active_deal'::text) THEN COALESCE(('Active deal – '::text || ( SELECT ph.label
               FROM (public.deal d
                 JOIN public.deal_phase ph ON ((ph.slug = d.phase)))
              WHERE ((d.client_id = c.id) AND (d.outcome IS NULL))
              ORDER BY d.created_at DESC, d.id DESC
             LIMIT 1)), 'Active deal – no deal on file'::text)
            ELSE cs.label
        END AS "Status",
    c.specialty_type_label AS "Specialty / Type",
    (COALESCE(p.city, ''::text) || COALESCE((', '::text || p.state), ''::text)) AS "Market / Location",
    c.deal_type_label AS "Deal Type",
    c.acquisition_source AS "Referral Source",
    c.contact_label AS "Contact",
    p.phone AS "Phone",
    p.email AS "Email",
    c.possible_duplicate_label AS "Possible Duplicate Of",
    c.notes_path AS "Detail File",
    c.notes AS "Notes"
   FROM (((((public.client c
     JOIN public.party p ON ((p.id = c.party_id)))
     LEFT JOIN public.client_status cs ON ((cs.slug = c.status)))
     LEFT JOIN public.client mt ON ((mt.id = c.merged_into)))
     LEFT JOIN public.party org ON ((org.id = p.org_id)))
     LEFT JOIN public.actor owner ON ((owner.id = c.owner_id)))
  WHERE (c.roster_ref IS NOT NULL);


--
-- Name: v_export_clients_active; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_export_clients_active AS
 SELECT COALESCE(c.owner_label, owner.display_name) AS "Owner",
    pc.name AS "Name",
    c.roster_ref AS "C-ID",
        CASE
            WHEN (c.status = 'active_deal'::text) THEN COALESCE(('Active deal – '::text || ( SELECT ph.label
               FROM (public.deal d
                 JOIN public.deal_phase ph ON ((ph.slug = d.phase)))
              WHERE ((d.client_id = c.id) AND (d.outcome IS NULL))
              ORDER BY d.created_at DESC, d.id DESC
             LIMIT 1)), 'Active deal – no deal on file'::text)
            ELSE cs.label
        END AS "Status",
    c.deal_type_label AS "Deal Type",
    COALESCE(c.specialty_type_label, c.vertical, ''::text) AS "Specialty",
    (COALESCE(pc.city, ''::text) || COALESCE((', '::text || pc.state), ''::text)) AS "Location",
    lt.last_touch AS "Last Touch",
    na.description AS "Next Step",
    c.notes_path AS "Detail"
   FROM (((((public.client c
     JOIN public.party pc ON ((pc.id = c.party_id)))
     LEFT JOIN public.client_status cs ON ((cs.slug = c.status)))
     LEFT JOIN public.actor owner ON ((owner.id = c.owner_id)))
     LEFT JOIN public.v_last_touch lt ON (((lt.subject_type = 'client'::text) AND (lt.subject_id = c.id))))
     LEFT JOIN LATERAL ( SELECT n.description
           FROM public.next_action n
          WHERE ((n.subject_type = 'client'::text) AND (n.subject_id = c.id) AND (n.status = 'open'::text))
          ORDER BY (n.owner_id = c.owner_id) DESC NULLS LAST, n.due_on, n.created_at
         LIMIT 1) na ON (true))
  WHERE ((c.merged_into IS NULL) AND (c.roster_ref IS NOT NULL) AND (COALESCE(cs.is_active_pipeline, false) OR (EXISTS ( SELECT 1
           FROM public.deal d
          WHERE ((d.client_id = c.id) AND (d.outcome IS NULL) AND (d.phase <> 'closed'::text))))));


--
-- Name: v_export_deals; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_export_deals AS
 SELECT d.id,
    d.name,
    d.salesforce_id,
    d.deal_type,
    ph.label AS phase,
    d.segment,
    d.outcome,
    d.closed_on,
    d.notes_path,
    c.roster_ref AS client_ref,
    pc.name AS client_name,
    initcap(lead_actor.slug) AS owner,
    d.sf_commission_placeholder AS "PLACEHOLDER_sf_commission_never_sum",
    d.sf_close_date_placeholder AS "PLACEHOLDER_sf_close_date_never_forecast",
    d.source_row,
    d.city,
    d.lane
   FROM (((((public.deal d
     JOIN public.client c ON ((c.id = d.client_id)))
     JOIN public.party pc ON ((pc.id = c.party_id)))
     JOIN public.deal_phase ph ON ((ph.slug = d.phase)))
     LEFT JOIN public.deal_participant dp ON (((dp.deal_id = d.id) AND (dp.role = 'lead'::text) AND (dp.to_at IS NULL))))
     LEFT JOIN public.actor lead_actor ON ((lead_actor.id = dp.actor_id)));


--
-- Name: v_export_dossier_analysis; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_export_dossier_analysis AS
 WITH dossier AS (
         SELECT c.id AS subject_id,
            'client'::text AS subject_type,
            c.notes_path AS rel_path
           FROM public.client c
          WHERE (c.notes_path IS NOT NULL)
        UNION ALL
         SELECT l.id,
            'lead'::text,
            ('DNA/Clients/prospects/'::text || regexp_replace(l.notes_path, '^.*/'::text, ''::text))
           FROM public.lead l
          WHERE (l.notes_path IS NOT NULL)
        )
 SELECT d.rel_path,
    d.subject_type,
    d.subject_id,
    a.id AS analysis_id,
    a.occurred_at,
    a.recorded_at,
    a.summary AS title,
    a.detail AS body,
    a.owed,
    act.slug AS author,
    a.source,
    row_number() OVER (PARTITION BY d.rel_path ORDER BY a.occurred_at DESC, a.recorded_at DESC, a.id) AS recency_rank
   FROM ((dossier d
     JOIN public.activity a ON ((((d.subject_type = 'client'::text) AND (a.client_id = d.subject_id)) OR ((d.subject_type = 'lead'::text) AND (a.lead_id = d.subject_id)))))
     JOIN public.actor act ON ((act.id = a.actor_id)))
  WHERE (a.kind = 'analysis'::text);


--
-- Name: VIEW v_export_dossier_analysis; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_export_dossier_analysis IS '0028 (ORDER 36 Phase B): kind=analysis activity rows per dossier file, newest-first via recency_rank. rank 1 renders in full, ranks > 1 collapse to title + date + author. Legacy imported rows and live verb writes share one partition by design.';


--
-- Name: v_export_dossier_subject; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_export_dossier_subject AS
 SELECT c.notes_path AS rel_path,
    regexp_replace(c.notes_path, '^.*/'::text, ''::text) AS file_name,
    c.id AS client_id,
    c.roster_ref AS client_ref,
    p.name AS subject_name,
    c.status AS client_status,
    c.vertical,
    c.subtype,
    c.specialty_type_label,
    c.client_type,
    c.deal_type_label,
    c.owner_label,
    c.contact_label,
    c.acquisition_source,
    c.acquisition_detail,
    c.etl_status,
    c.notes AS record_notes,
    c.updated_at AS record_updated_at,
    l.id AS lead_id,
    lt.last_touch
   FROM (((public.client c
     JOIN public.party p ON ((p.id = c.party_id)))
     LEFT JOIN public.lead l ON (((l.notes_path IS NOT NULL) AND (regexp_replace(l.notes_path, '^.*/'::text, ''::text) = regexp_replace(c.notes_path, '^.*/'::text, ''::text)))))
     LEFT JOIN public.v_last_touch lt ON (((lt.subject_type = 'client'::text) AND (lt.subject_id = c.id))))
  WHERE (c.notes_path IS NOT NULL);


--
-- Name: VIEW v_export_dossier_subject; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_export_dossier_subject IS '0028 (ORDER 36 Phase B): one row per hand-maintained dossier file — the 20 clients carrying notes_path. Supplies the render''s structured header from record fields. Read-only render surface; NOT a Joe-browse surface (ruling 6).';


--
-- Name: v_export_leads; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_export_leads AS
 SELECT l.registry_ref AS "Lead ID",
    (l.created_at)::date AS "Date In",
    COALESCE(l.owner_label, owner.display_name) AS "Owner",
    ls.label AS "Stage",
    l.segment AS "Segment",
    p.name AS "Contact Name",
    org.name AS "Practice",
    p.specialty AS "Specialty",
    p.city AS "City/Market",
    p.county AS "County",
    p.email AS "Email",
    p.phone AS "Phone",
    l.source_type AS "Source Type",
    l.source_detail AS "Source Detail (V-ID / event / referrer)",
    COALESCE((l.report_back_due)::text, l.report_back_due_raw) AS "Report-Back Due",
    l.drip_campaign AS "Drip Campaign",
    COALESCE((l.drip_added)::text, l.drip_added_raw) AS "Drip Added",
    na.description AS "Next Action",
    na.due_on AS "Next Action Date",
    lt.last_touch AS "Last Touch",
    l.sf_deal AS "SF Deal",
    l.notes_path AS "Detail File",
    l.notes AS "Notes",
    COALESCE((l.est_lease_event)::text, l.est_lease_event_raw) AS "Est-Lease-Event",
    l.event_source AS "Event-Source",
    l.event_confidence AS "Event-Confidence",
    l.suppressed AS _suppressed
   FROM ((((((public.lead l
     JOIN public.party p ON ((p.id = l.party_id)))
     JOIN public.lead_stage ls ON ((ls.slug = l.stage)))
     LEFT JOIN public.party org ON ((org.id = p.org_id)))
     LEFT JOIN public.actor owner ON ((owner.id = l.owner_id)))
     LEFT JOIN public.next_action na ON (((na.subject_type = 'lead'::text) AND (na.subject_id = l.id) AND (na.status = 'open'::text))))
     LEFT JOIN public.v_last_touch lt ON (((lt.subject_type = 'lead'::text) AND (lt.subject_id = l.id))));


--
-- Name: v_export_loops; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_export_loops AS
 SELECT lb.rel_path,
    lb.kind,
    lb.seq AS block_seq,
    lb.block_key,
    lb.prose_md,
    lb.header_cols,
    lb.renders_closed,
    lb.col_order AS block_col_order,
    li.id AS loop_id,
    li.render_seq,
    li.col_order AS row_col_order,
    li.number,
    li.owner,
    li.title,
    li.body,
    li.since_text,
    li.unblocks,
    li.source_note,
    li.closed_text,
    li.outcome,
    li.marker_literal,
    li.extra_cells,
    li.status,
    li.domain,
    ld.label AS domain_label,
    COALESCE(ld.sort, 999) AS domain_sort
   FROM ((public.loop_block lb
     LEFT JOIN public.loop_item li ON (((li.block_id = lb.id) AND ((li.status = 'open'::text) OR lb.renders_closed))))
     LEFT JOIN public.loop_domain ld ON ((ld.slug = li.domain)))
  ORDER BY lb.rel_path, lb.seq, COALESCE(ld.sort, 999), li.render_seq;


--
-- Name: VIEW v_export_loops; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_export_loops IS 'Render source for the four loop files. A block renders its OPEN items, except the inline DONE / Done tables (renders_closed), which are today''s file content and must round-trip. Closing an open_loop takes it off the render, which is what moving the row to open-loops-closed.md always did. Since 0042 it also carries domain / domain_label / domain_sort so the render can group by lane — deals first, system last, unclassified (sort 999) last of all. Label and sort come from loop_domain so headings and ordering are rows a human edits, never exporter code.';


--
-- Name: v_export_loops_closed; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_export_loops_closed AS
 SELECT lb.rel_path,
    lb.kind,
    lb.block_key,
    li.number,
    li.owner,
    li.title,
    li.body,
    li.close_outcome,
    li.closed_at,
    a.slug AS closed_by
   FROM ((public.loop_item li
     JOIN public.loop_block lb ON ((lb.id = li.block_id)))
     LEFT JOIN public.actor a ON ((a.id = li.closed_by)))
  WHERE (li.status <> 'open'::text)
  ORDER BY li.closed_at DESC NULLS LAST, li.number;


--
-- Name: v_export_pool; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_export_pool AS
 SELECT source_seq,
    source_key,
    source_row,
    segment AS "SEGMENT",
    segment_play AS "THE PLAY",
    name AS "Name",
    vertical AS "Profession",
    address AS "Practice Address",
    city AS "City",
    county AS "County",
    email AS "Email",
    phone AS "Phone",
    status AS _status,
    dup_tier AS _dup_tier,
    dup_ref AS _dup_ref
   FROM public.candidate_pool pp
  WHERE (source = 'lead-router'::text);


--
-- Name: VIEW v_export_pool; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_export_pool IS 'Export target #8 (the lead-router xlsx). DB-owned columns are named; every other sheet column passes through source_row verbatim, the build_deals fidelity rule. DEATH SENTENCE: this surface retires at the Wave 4 repoint once the board view is confirmed the only reader (amendment-5 shim pattern).';


--
-- Name: v_export_pool_all; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_export_pool_all AS
 SELECT source,
    source_seq,
    source_key,
    source_row,
    segment AS "SEGMENT",
    segment_play AS "THE PLAY",
    name AS "Name",
    vertical AS "Profession",
    address AS "Practice Address",
    city AS "City",
    county AS "County",
    email AS "Email",
    phone AS "Phone",
    status AS _status,
    dup_tier AS _dup_tier,
    dup_ref AS _dup_ref,
    est_lease_event,
    est_basis,
    score,
    score_basis
   FROM public.candidate_pool;


--
-- Name: VIEW v_export_pool_all; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_export_pool_all IS '0025: ALL-source pool read for the lead board''s records mode (ORDER 26 flip). v_export_pool stays router-scoped because it is export target #8; this view is the consumer read path and is NOT an export target.';


--
-- Name: v_export_source_captures; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_export_source_captures AS
 SELECT id,
    captured_on,
    session,
    source_url,
    visibility,
    status,
    merge_note
   FROM public.source_capture
  ORDER BY captured_on, created_at;


--
-- Name: vendor_category; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vendor_category (
    slug text NOT NULL,
    label text NOT NULL,
    sort integer NOT NULL
);


--
-- Name: vendor_stage; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vendor_stage (
    slug text NOT NULL,
    label text NOT NULL,
    sort integer DEFAULT 100 NOT NULL
);


--
-- Name: v_export_vendors; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_export_vendors AS
 SELECT v.vendor_ref AS "ID",
    p.name AS "Name",
    org.name AS "Company",
    COALESCE(vc.label, v.category) AS "Category",
    array_to_string(v.verticals, ', '::text) AS "Vertical",
    p.title AS "Title",
    COALESCE(v.owner_label, owner.display_name) AS "Owner",
    vs.label AS "Stage",
    lt.last_touch AS "Last Touch",
    na.description AS "Next Step",
        CASE
            WHEN v.referral_active THEN 'Yes'::text
            WHEN (NOT v.referral_active) THEN 'No'::text
            ELSE NULL::text
        END AS "Referral-active?",
    v.territory AS "Territory",
    p.state AS "State",
    v.offers AS "Offers",
    v.seeking AS "Seeking",
    v.links_label AS "Links",
    v.rivalry_group AS "Rivalry Group",
    v.originated AS "Originated / Referred",
    p.phone AS "Phone",
    p.email AS "Email",
    v.intro_notes AS "Notes",
        CASE
            WHEN v.enrich THEN 'Yes'::text
            WHEN (NOT v.enrich) THEN 'No'::text
            ELSE NULL::text
        END AS "Enrich?",
    v.out_of_market AS _out_of_market
   FROM (((((((public.vendor v
     JOIN public.party p ON ((p.id = v.party_id)))
     LEFT JOIN public.vendor_stage vs ON ((vs.slug = v.stage)))
     LEFT JOIN public.vendor_category vc ON ((vc.slug = v.category_slug)))
     LEFT JOIN public.party org ON ((org.id = p.org_id)))
     LEFT JOIN public.actor owner ON ((owner.id = v.owner_id)))
     LEFT JOIN public.next_action na ON (((na.subject_type = 'vendor'::text) AND (na.subject_id = v.id) AND (na.status = 'open'::text))))
     LEFT JOIN public.v_last_touch lt ON (((lt.subject_type = 'vendor'::text) AND (lt.subject_id = v.id))))
  WHERE (v.merged_into IS NULL);


--
-- Name: v_field_history; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_field_history AS
 SELECT e.subject_type,
    e.subject_id,
    e.field,
    e.old_value,
    e.new_value,
    e.occurred_at,
    e.recorded_at,
    e.verb,
    a.slug AS actor,
    e.cause,
    e.human_quote,
    (e.new_value ? '__sensitive_ref'::text) AS redacted
   FROM (public.event e
     LEFT JOIN public.actor a ON ((a.id = e.actor_id)))
  WHERE (e.field IS NOT NULL);


--
-- Name: VIEW v_field_history; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_field_history IS 'One row per field of one subject changing once (0106, loop #281). event has carried old/new since 0001 and nothing exposed it — v_subject_timeline drops both columns and carr_reader cannot see a base table, so the replay material existed and was unreachable. `redacted` is lifted out of the jsonb because a value withheld under 0001 addendum A9 must not read like a value that was never set.';


--
-- Name: v_growth_slope; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_growth_slope AS
 SELECT cur.table_name,
    cur.taken_on,
    cur.row_count,
    prev.taken_on AS prev_taken_on,
    prev.row_count AS prev_row_count,
    (cur.row_count - prev.row_count) AS delta,
    (cur.taken_on - prev.taken_on) AS days_between
   FROM (public.growth_snapshot cur
     LEFT JOIN LATERAL ( SELECT p.taken_on,
            p.row_count
           FROM public.growth_snapshot p
          WHERE ((p.table_name = cur.table_name) AND (p.taken_on < cur.taken_on))
          ORDER BY p.taken_on DESC
         LIMIT 1) prev ON (true));


--
-- Name: v_ingest_backlog; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_ingest_backlog AS
 SELECT status,
    count(*) AS n,
    min(received_at) AS oldest,
    (EXTRACT(day FROM (now() - min(received_at))))::integer AS oldest_age_days
   FROM public.ingest_inbox
  GROUP BY status;


--
-- Name: v_integrity_digest; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_integrity_digest AS
 SELECT 'row_counts'::text AS line,
    jsonb_build_object('deals', ( SELECT count(*) AS count
           FROM public.deal), 'clients', ( SELECT count(*) AS count
           FROM public.client), 'leads', ( SELECT count(*) AS count
           FROM public.lead), 'vendors', ( SELECT count(*) AS count
           FROM public.vendor), 'activities_7d', ( SELECT count(*) AS count
           FROM public.activity
          WHERE (activity.recorded_at > (now() - '7 days'::interval))), 'events_24h', ( SELECT count(*) AS count
           FROM public.event
          WHERE (event.recorded_at > (now() - '24:00:00'::interval)))) AS value
UNION ALL
 SELECT 'writes_by_dell_24h'::text AS line,
    to_jsonb(( SELECT count(*) AS count
           FROM (public.event e
             JOIN public.actor a ON ((a.id = e.actor_id)))
          WHERE ((a.slug = 'dell'::text) AND (e.recorded_at > (now() - '24:00:00'::interval))))) AS value
UNION ALL
 SELECT 'export_freshness'::text AS line,
    COALESCE(( SELECT jsonb_object_agg(t.target, jsonb_build_object('last_ok', t.last_ok, 'stale',
                CASE
                    WHEN (t.last_ok IS NULL) THEN NULL::boolean
                    ELSE (t.last_ok < (now() - '26:00:00'::interval))
                END, 'state',
                CASE
                    WHEN (t.last_ok IS NULL) THEN 'never_succeeded'::text
                    WHEN (t.last_ok < (now() - '26:00:00'::interval)) THEN 'stale'::text
                    ELSE 'fresh'::text
                END, 'last_attempt', t.last_any, 'last_attempt_status', t.last_status)) AS jsonb_object_agg
           FROM ( SELECT export_run.target,
                    max(export_run.ran_at) FILTER (WHERE (export_run.status = 'ok'::text)) AS last_ok,
                    max(export_run.ran_at) AS last_any,
                    (array_agg(export_run.status ORDER BY export_run.ran_at DESC))[1] AS last_status
                   FROM public.export_run
                  GROUP BY export_run.target) t), '{}'::jsonb) AS value
UNION ALL
 SELECT 'norm_owed_open'::text AS line,
    to_jsonb(( SELECT count(*) AS count
           FROM public.availability
          WHERE availability.norm_owed)) AS value
UNION ALL
 SELECT 'merge_queue'::text AS line,
    to_jsonb(( SELECT count(*) AS count
           FROM public.ingest_inbox
          WHERE (ingest_inbox.status = 'new'::text))) AS value;


--
-- Name: v_investigation; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_investigation AS
SELECT
    NULL::uuid AS id,
    NULL::uuid AS signal_id,
    NULL::text AS signal_kind,
    NULL::text AS subject_type,
    NULL::text AS subject_ref,
    NULL::text AS objective,
    NULL::text AS owner,
    NULL::integer AS max_depth,
    NULL::text AS status,
    NULL::text AS conclusion,
    NULL::numeric AS confidence,
    NULL::text AS strongest_alternative,
    NULL::text AS alternative_disposition,
    NULL::text AS termination_reason,
    NULL::timestamp with time zone AS opened_at,
    NULL::timestamp with time zone AS closed_at,
    NULL::bigint AS branch_count,
    NULL::bigint AS open_branch_count;


--
-- Name: v_lead_client_link; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_lead_client_link AS
 SELECT l.id AS lead_id,
    l.registry_ref AS lead_ref,
    lp.name AS lead_name,
    c.id AS client_id,
    c.roster_ref AS client_ref,
    cp.name AS client_name,
    'conversion'::text AS link_basis,
    1 AS basis_rank,
    ((lp.merged_into IS NOT NULL) OR (COALESCE(c.merged_into, cp.merged_into) IS NOT NULL)) AS either_merged
   FROM (((public.lead l
     JOIN public.party lp ON ((lp.id = l.party_id)))
     JOIN public.client c ON ((c.id = l.client_id)))
     JOIN public.party cp ON ((cp.id = c.party_id)))
UNION
 SELECT l.id AS lead_id,
    l.registry_ref AS lead_ref,
    lp.name AS lead_name,
    c.id AS client_id,
    c.roster_ref AS client_ref,
    cp.name AS client_name,
    'same_party'::text AS link_basis,
    2 AS basis_rank,
    ((lp.merged_into IS NOT NULL) OR (COALESCE(c.merged_into, cp.merged_into) IS NOT NULL)) AS either_merged
   FROM (((public.lead l
     JOIN public.party lp ON ((lp.id = l.party_id)))
     JOIN public.party cp ON ((COALESCE(cp.merged_into, cp.id) = COALESCE(lp.merged_into, lp.id))))
     JOIN public.client c ON ((c.party_id = cp.id)))
  WHERE ((lp.deleted_at IS NULL) AND (cp.deleted_at IS NULL))
UNION
 SELECT l.id AS lead_id,
    l.registry_ref AS lead_ref,
    lp.name AS lead_name,
    c.id AS client_id,
    c.roster_ref AS client_ref,
    cp.name AS client_name,
    'same_org'::text AS link_basis,
    3 AS basis_rank,
    ((lp.merged_into IS NOT NULL) OR (COALESCE(c.merged_into, cp.merged_into) IS NOT NULL)) AS either_merged
   FROM (((public.lead l
     JOIN public.party lp ON ((lp.id = l.party_id)))
     JOIN public.party cp ON (((cp.id = lp.org_id) OR (cp.org_id = lp.org_id))))
     JOIN public.client c ON ((c.party_id = cp.id)))
  WHERE ((lp.org_id IS NOT NULL) AND (lp.deleted_at IS NULL) AND (cp.deleted_at IS NULL) AND (COALESCE(lp.merged_into, lp.id) <> COALESCE(cp.merged_into, cp.id)));


--
-- Name: VIEW v_lead_client_link; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_lead_client_link IS 'Every lead that is linked to a client, by EXACT KEY only — never by name (0102, loop #127). link_basis says which: conversion = lead.client_id, the explicit pointer set at conversion; same_party = both records hang off one party with merges resolved; same_org = the lead sits under the client''s org or under the client''s own party, which answers "is this practice already a client" and is NOT a conversion. An empty result for a lead whose practice is obviously a client usually means that practice exists as several party rows, which is the party dedup (loop #125), not a missing link.';


--
-- Name: v_lead_client_best; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_lead_client_best AS
 SELECT DISTINCT ON (lead_id) lead_id,
    lead_ref,
    lead_name,
    client_id,
    client_ref,
    client_name,
    link_basis,
    either_merged
   FROM public.v_lead_client_link
  ORDER BY lead_id, basis_rank, client_ref;


--
-- Name: VIEW v_lead_client_best; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_lead_client_best IS 'One row per linked lead: its strongest link only, conversion beating same_party beating same_org (0102). The read verbs traverse THIS so the ranking lives in one place.';


--
-- Name: v_lead_hot; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_lead_hot AS
 SELECT l.id,
    l.registry_ref,
    p.name,
    p.specialty,
    p.city,
    p.county,
    p.state,
    l.lane,
    l.stage,
    l.score,
    l.segment,
    l.suppressed,
    l.est_lease_event,
    l.event_confidence,
    lt.last_touch,
    l.next_action_date
   FROM ((public.lead l
     JOIN public.party p ON ((p.id = l.party_id)))
     LEFT JOIN public.v_last_touch lt ON (((lt.subject_type = 'lead'::text) AND (lt.subject_id = l.id))))
  WHERE (NOT l.suppressed);


--
-- Name: v_loop_bell_cap; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_loop_bell_cap AS
 SELECT COALESCE(domain, '(unclassified)'::text) AS domain,
    count(*) AS bells,
    3 AS cap,
    (count(*) > 3) AS over_cap
   FROM public.loop_item li
  WHERE ((kind = 'open_loop'::text) AND (status = 'open'::text) AND (marker = 'bell'::text))
  GROUP BY COALESCE(domain, '(unclassified)'::text);


--
-- Name: VIEW v_loop_bell_cap; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_loop_bell_cap IS 'Bells per domain against the cap of 3 (Joe, 2026-08-02, replacing the old global cap of 5 that predated domains). over_cap = re-tier, do not stack. Reported, never enforced: a constraint would refuse a write at the worst possible moment.';


--
-- Name: v_loop_no_blocker; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_loop_no_blocker AS
 SELECT li.id,
    li.number,
    li.kind,
    li.domain,
    li.owner,
    li.marker,
    li.since_text,
    lb.block_key AS section,
    "left"(COALESCE(li.body, li.title, ''::text), 160) AS gist,
    (li.created_at < '2026-08-09 22:20:28.647+00'::timestamp with time zone) AS predates_gate
   FROM (public.loop_item li
     JOIN public.loop_block lb ON ((lb.id = li.block_id)))
  WHERE ((li.kind = 'open_loop'::text) AND (li.status = 'open'::text) AND (li.blocker_class IS NULL))
  ORDER BY li.created_at;


--
-- Name: VIEW v_loop_no_blocker; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_loop_no_blocker IS 'Open open_loop rows carrying no named blocker. BOUND ACTION: each row is a candidate to DO or to CLOSE, never to re-file — nobody ever established that the work needed deferring. predates_gate separates rows opened before the gate reached production (2026-08-09 22:20:28Z, Worker c3370cfc) from anything opened after it; a FALSE row means add-loop was bypassed and is a defect to investigate, not a backlog item. Boundary corrected by 0083 — 0081 used midnight and so reported 14 same-day rows as bypasses.';


--
-- Name: v_loop_promotion_due; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_loop_promotion_due AS
 SELECT li.id AS loop_id,
    li.number,
    li.marker_literal,
    li.due_on,
    li.owner,
    li.title,
    li.body,
    lb.block_key AS currently_in,
    (CURRENT_DATE - li.due_on) AS days_due
   FROM (public.loop_item li
     JOIN public.loop_block lb ON ((lb.id = li.block_id)))
  WHERE ((li.kind = 'open_loop'::text) AND (li.status = 'open'::text) AND (li.marker = 'dated'::text) AND (li.due_on IS NOT NULL) AND (li.due_on <= CURRENT_DATE) AND (lb.block_key <> 'hot'::text));


--
-- Name: VIEW v_loop_promotion_due; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_loop_promotion_due IS 'Backlog rows whose dated marker has arrived. The heartbeat reads this and calls update-loop to move each one; nothing here relocates a row by itself.';


--
-- Name: v_loop_proximity; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_loop_proximity AS
 SELECT li.id,
    li.number,
    li.kind,
    li.domain,
    li.owner,
    li.marker,
    li.drift_critical,
    li.due_on,
    COALESCE(li.blocker_class, 'unclassified'::text) AS blocker_class,
    li.blocker_detail,
        CASE COALESCE(li.blocker_class, 'unclassified'::text)
            WHEN 'ruling'::text THEN 1
            WHEN 'human_only'::text THEN 2
            WHEN 'other_lane'::text THEN 3
            WHEN 'counterparty'::text THEN 4
            WHEN 'external_event'::text THEN 5
            WHEN 'capability'::text THEN 6
            ELSE 9
        END AS proximity_rank,
        CASE COALESCE(li.blocker_class, 'unclassified'::text)
            WHEN 'ruling'::text THEN 'one sentence from Joe'::text
            WHEN 'human_only'::text THEN 'one sitting of a partner'::text
            WHEN 'other_lane'::text THEN 'our own other work'::text
            WHEN 'counterparty'::text THEN 'someone outside; nudge only'::text
            WHEN 'external_event'::text THEN 'a date; nothing to do'::text
            WHEN 'capability'::text THEN 'needs a build first'::text
            ELSE 'UNCLASSIFIED — predates the blocker gate'::text
        END AS proximity_label,
    (li.blocker_class IS NULL) AS unscored,
    (CURRENT_DATE - (li.created_at)::date) AS days_open,
    lb.block_key AS section,
    "left"(COALESCE(li.body, li.title, ''::text), 160) AS gist
   FROM (public.loop_item li
     JOIN public.loop_block lb ON ((lb.id = li.block_id)))
  WHERE ((li.kind = 'open_loop'::text) AND (li.status = 'open'::text))
  ORDER BY
        CASE COALESCE(li.blocker_class, 'unclassified'::text)
            WHEN 'ruling'::text THEN 1
            WHEN 'human_only'::text THEN 2
            WHEN 'other_lane'::text THEN 3
            WHEN 'counterparty'::text THEN 4
            WHEN 'external_event'::text THEN 5
            WHEN 'capability'::text THEN 6
            ELSE 9
        END, li.created_at;


--
-- Name: VIEW v_loop_proximity; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_loop_proximity IS 'Open loops ordered by effort-to-close, derived from blocker_class, which is a different axis from the bell/dated/decision tiers that order by urgency. BOUND ACTION: when a partner has a spare sitting, take from the top of this list, because those are the rows a single act finishes. Rank 9 rows are UNSCORED, never "furthest away" — they predate the blocker gate and nobody ever established what they are waiting on. Read v_loop_proximity_coverage before trusting the head of this list as representative of the backlog.';


--
-- Name: v_loop_proximity_coverage; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_loop_proximity_coverage AS
 SELECT count(*) AS open_loops,
    count(*) FILTER (WHERE (NOT unscored)) AS scored,
    count(*) FILTER (WHERE unscored) AS unscored,
    round(((100.0 * (count(*) FILTER (WHERE (NOT unscored)))::numeric) / (NULLIF(count(*), 0))::numeric), 1) AS scored_pct
   FROM public.v_loop_proximity;


--
-- Name: VIEW v_loop_proximity_coverage; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_loop_proximity_coverage IS 'What share of the open backlog v_loop_proximity can actually rank. Exists so the ranking can never be read as covering more than it does. Coverage rises as old loops are touched and given a blocker; it is expected to start near 4% the day it ships. BOUND ACTION: if scored_pct is still under 50% once the backlog has turned over, the gate is being satisfied with a throwaway class rather than a real one, and the blocker vocabulary needs review rather than the ranking.';


--
-- Name: v_loops; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_loops AS
 SELECT li.id AS loop_id,
    li.kind,
    li.number,
    lb.rel_path AS renders_into,
    lb.block_key AS section,
    li.render_seq,
    li.title,
    li.body,
    li.owner,
    li.since_text,
    li.unblocks,
    li.source_note,
    li.closed_text,
    li.outcome,
    li.marker,
    li.marker_literal,
    li.due_on,
    li.drift_critical,
    li.status,
    li.close_outcome,
    li.closed_at,
    li.tier,
    a.slug AS personal_to,
    li.created_at,
    li.updated_at,
    li.version
   FROM ((public.loop_item li
     JOIN public.loop_block lb ON ((lb.id = li.block_id)))
     LEFT JOIN public.actor a ON ((a.id = li.personal_to)));


--
-- Name: VIEW v_loops; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_loops IS 'Reader surface for the loop accumulators. SAFE COLUMNS ONLY (v_ref_index precedent): no block internals, no extra_cells, no actor uuids. tier and personal_to are carried BECAUSE the boundary that matters here is the personal/shared split — open-loops.md is Joe-personal, action-required.md and team-loops.md are shared. The consumer filters; the reader never sees the base table. This column list is a security boundary.';


--
-- Name: v_marketing_measurement_coverage; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_marketing_measurement_coverage AS
 SELECT v.platform,
    count(*) AS placements,
    count(*) FILTER (WHERE v.measured) AS measured_placements,
    count(*) FILTER (WHERE (NOT v.measured)) AS unmeasured_placements,
    round(((100.0 * (count(*) FILTER (WHERE v.measured))::numeric) / (NULLIF(count(*), 0))::numeric), 1) AS coverage_pct,
    min(v.live_at) AS first_live_at,
    max(v.live_at) AS last_live_at,
        CASE
            WHEN (count(*) FILTER (WHERE v.measured) = 0) THEN NULL::numeric
            ELSE sum(l.views)
        END AS views_total,
        CASE
            WHEN (count(*) FILTER (WHERE v.measured) = 0) THEN NULL::numeric
            ELSE sum(l.interactions)
        END AS interactions_total
   FROM (public.v_placement_measurement v
     LEFT JOIN LATERAL ( SELECT sum(lm.value) FILTER (WHERE (lm.kind = 'views_count'::text)) AS views,
            sum(lm.value) FILTER (WHERE (lm.kind = 'interactions_sum'::text)) AS interactions
           FROM public.v_placement_metric_latest lm
          WHERE (lm.placement_id = v.placement_id)) l ON (true))
  GROUP BY v.platform;


--
-- Name: VIEW v_marketing_measurement_coverage; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_marketing_measurement_coverage IS 'Measurement coverage per platform (0066). views_total and interactions_total are NULL — never 0 — on a platform where nothing was measured, because a 0 there reads as "earned nothing" when the truth is "was never measured", and 73 of 89 placements are in exactly that state. coverage_pct IS 0.0 rather than null for such a platform: 0% coverage is a real, known measurement about the measuring, not a missing value.';


--
-- Name: v_md_ledger_entry; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_md_ledger_entry AS
 SELECT rs.external_key,
    split_part(rs.external_key, '#'::text, 1) AS ledger,
    split_part(split_part(rs.external_key, '#'::text, 2), ':'::text, 1) AS entry_id,
    (a.occurred_at)::date AS entry_date,
    act.slug AS author,
    ri.ref AS subject_ref,
    ri.display_name AS subject_name,
    ri.subject_type,
    a.summary,
    a.detail,
    a.id AS activity_id
   FROM (((public.record_source rs
     JOIN public.activity a ON (((a.id = rs.entity_id) AND (rs.entity_type = 'activity'::text))))
     JOIN public.actor act ON ((act.id = a.actor_id)))
     LEFT JOIN LATERAL ( SELECT r.ref,
            r.display_name,
            r.subject_type
           FROM public.v_ref_index r
          WHERE (r.subject_id = COALESCE(a.vendor_id, a.client_id, a.lead_id, a.deal_id))
          ORDER BY r.ref
         LIMIT 1) ri ON (true))
  WHERE (rs.source_system = 'md-ledger'::text);


--
-- Name: VIEW v_md_ledger_entry; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_md_ledger_entry IS 'ORDER 39. Imported markdown-ledger entries (hunt-ledger, deals-reciprocity) read back as ledger rows. One row per (entry, subject) — a hunt entry touching five subjects is five rows, which is what the import wrote. PARTIAL by design until the seven parked/review-listed entries are ruled on.';


--
-- Name: v_media_recommendation; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_media_recommendation AS
 SELECT m.id,
    m.title,
    m.author,
    m.kind,
    m.why,
    m.observed_pattern,
    m.tags,
    m.priority,
    m.status,
    m.recommended_on,
    m.finished_on,
    m.personal_to,
    a.slug AS recommended_by,
    m.version,
    m.updated_at
   FROM (public.media_recommendation m
     LEFT JOIN public.actor a ON ((a.id = m.created_by)));


--
-- Name: VIEW v_media_recommendation; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_media_recommendation IS 'The curriculum board''s read surface (0111). carr_reader holds no grant on base tables, so the exporter and every read session come through here.';


--
-- Name: v_orphaned_edge; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_orphaned_edge AS
 SELECT 'party_link.to_party'::text AS edge,
    pl.id AS edge_id,
    pl.kind AS detail,
    p.ref AS party_ref,
    p.name,
    sv.ref AS survivor_ref,
    (p.merged_into IS NOT NULL) AS party_merged,
    (p.deleted_at IS NOT NULL) AS party_deleted
   FROM ((public.party_link pl
     JOIN public.party p ON ((p.id = pl.to_party)))
     LEFT JOIN public.party sv ON ((sv.id = p.merged_into)))
  WHERE ((p.merged_into IS NOT NULL) OR (p.deleted_at IS NOT NULL))
UNION ALL
 SELECT 'party_link.from_party'::text AS edge,
    pl.id AS edge_id,
    pl.kind AS detail,
    p.ref AS party_ref,
    p.name,
    sv.ref AS survivor_ref,
    (p.merged_into IS NOT NULL) AS party_merged,
    (p.deleted_at IS NOT NULL) AS party_deleted
   FROM ((public.party_link pl
     JOIN public.party p ON ((p.id = pl.from_party)))
     LEFT JOIN public.party sv ON ((sv.id = p.merged_into)))
  WHERE ((p.merged_into IS NOT NULL) OR (p.deleted_at IS NOT NULL))
UNION ALL
 SELECT 'party_link.via_party'::text AS edge,
    pl.id AS edge_id,
    pl.kind AS detail,
    p.ref AS party_ref,
    p.name,
    sv.ref AS survivor_ref,
    (p.merged_into IS NOT NULL) AS party_merged,
    (p.deleted_at IS NOT NULL) AS party_deleted
   FROM ((public.party_link pl
     JOIN public.party p ON ((p.id = pl.via_party)))
     LEFT JOIN public.party sv ON ((sv.id = p.merged_into)))
  WHERE ((p.merged_into IS NOT NULL) OR (p.deleted_at IS NOT NULL))
UNION ALL
 SELECT 'deal_participant.party_id'::text AS edge,
    dp.id AS edge_id,
    dp.role AS detail,
    p.ref AS party_ref,
    p.name,
    sv.ref AS survivor_ref,
    (p.merged_into IS NOT NULL) AS party_merged,
    (p.deleted_at IS NOT NULL) AS party_deleted
   FROM ((public.deal_participant dp
     JOIN public.party p ON ((p.id = dp.party_id)))
     LEFT JOIN public.party sv ON ((sv.id = p.merged_into)))
  WHERE ((p.merged_into IS NOT NULL) OR (p.deleted_at IS NOT NULL))
UNION ALL
 SELECT 'building_ownership.party_id'::text AS edge,
    bo.id AS edge_id,
    bo.kind AS detail,
    p.ref AS party_ref,
    p.name,
    sv.ref AS survivor_ref,
    (p.merged_into IS NOT NULL) AS party_merged,
    (p.deleted_at IS NOT NULL) AS party_deleted
   FROM ((public.building_ownership bo
     JOIN public.party p ON ((p.id = bo.party_id)))
     LEFT JOIN public.party sv ON ((sv.id = p.merged_into)))
  WHERE ((p.merged_into IS NOT NULL) OR (p.deleted_at IS NOT NULL))
UNION ALL
 SELECT 'commission_allocation.party_id'::text AS edge,
    ca.id AS edge_id,
    ca.kind AS detail,
    p.ref AS party_ref,
    p.name,
    sv.ref AS survivor_ref,
    (p.merged_into IS NOT NULL) AS party_merged,
    (p.deleted_at IS NOT NULL) AS party_deleted
   FROM ((public.commission_allocation ca
     JOIN public.party p ON ((p.id = ca.party_id)))
     LEFT JOIN public.party sv ON ((sv.id = p.merged_into)))
  WHERE ((p.merged_into IS NOT NULL) OR (p.deleted_at IS NOT NULL))
UNION ALL
 SELECT 'registration.registered_with_party'::text AS edge,
    r.id AS edge_id,
    r.method AS detail,
    p.ref AS party_ref,
    p.name,
    sv.ref AS survivor_ref,
    (p.merged_into IS NOT NULL) AS party_merged,
    (p.deleted_at IS NOT NULL) AS party_deleted
   FROM ((public.registration r
     JOIN public.party p ON ((p.id = r.registered_with_party)))
     LEFT JOIN public.party sv ON ((sv.id = p.merged_into)))
  WHERE ((p.merged_into IS NOT NULL) OR (p.deleted_at IS NOT NULL))
UNION ALL
 SELECT 'party.org_id'::text AS edge,
    holder.id AS edge_id,
    holder.ref AS detail,
    p.ref AS party_ref,
    p.name,
    sv.ref AS survivor_ref,
    (p.merged_into IS NOT NULL) AS party_merged,
    (p.deleted_at IS NOT NULL) AS party_deleted
   FROM ((public.party holder
     JOIN public.party p ON ((p.id = holder.org_id)))
     LEFT JOIN public.party sv ON ((sv.id = p.merged_into)))
  WHERE ((p.merged_into IS NOT NULL) OR (p.deleted_at IS NOT NULL));


--
-- Name: VIEW v_orphaned_edge; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_orphaned_edge IS 'The EDGE half of the pre-merge orphan sweep (0062); v_orphaned_role (0055) is the ROLE half. Every non-role foreign key to party(id) that points at a merged or deleted party. RUN IT BEFORE AND AFTER EVERY MERGE — it must read zero both times, and confirm-merge must leave it at zero. It exists because the sweep was a remembered procedure and got skipped: one stale party_link edge to a merged Tyrer sat in the intro graph for two days while who-do-we-know went on offering Joe a path to a party that no longer resolves. DELIBERATELY EXCLUDES org_merge_log, whose whole job is to point at merged org rows so 0059 stays reversible. DOES NOT FLAG the seven tombstoned client rows whose party is merged — those are correct (a tombstoned client pointing at the losing party is what a tombstone IS) and this view does not read the client table at all.';


--
-- Name: v_orphaned_role; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_orphaned_role AS
 SELECT 'vendor'::text AS role,
    v.vendor_ref AS ref,
    p.name,
    p.ref AS party_ref,
    (p.merged_into IS NOT NULL) AS party_merged
   FROM (public.vendor v
     JOIN public.party p ON ((p.id = v.party_id)))
  WHERE ((p.merged_into IS NOT NULL) OR (p.deleted_at IS NOT NULL))
UNION ALL
 SELECT 'client'::text AS role,
    c.roster_ref AS ref,
    p.name,
    p.ref AS party_ref,
    (p.merged_into IS NOT NULL) AS party_merged
   FROM (public.client c
     JOIN public.party p ON ((p.id = c.party_id)))
  WHERE ((c.merged_into IS NULL) AND ((p.merged_into IS NOT NULL) OR (p.deleted_at IS NOT NULL)))
UNION ALL
 SELECT 'lead'::text AS role,
    l.registry_ref AS ref,
    p.name,
    p.ref AS party_ref,
    (p.merged_into IS NOT NULL) AS party_merged
   FROM (public.lead l
     JOIN public.party p ON ((p.id = l.party_id)))
  WHERE ((p.merged_into IS NOT NULL) OR (p.deleted_at IS NOT NULL));


--
-- Name: VIEW v_orphaned_role; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_orphaned_role IS 'Role records whose party was merged away or deleted. Found at 0054 when v_contact showed 287 vendors instead of 290: merging a party does NOT cascade to its role rows, so a vendor can outlive its person and disappear from every party-based view while still existing. Watch this through the 42 pending lead/client merges.';


--
-- Name: v_party_graph; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_party_graph AS
 WITH party_ref AS (
         SELECT DISTINCT ON (r.party_id) r.party_id,
            r.ref
           FROM ( SELECT c.party_id,
                    c.roster_ref AS ref,
                    1 AS pref
                   FROM public.client c
                UNION ALL
                 SELECT v.party_id,
                    v.vendor_ref,
                    2
                   FROM public.vendor v
                UNION ALL
                 SELECT l.party_id,
                    l.registry_ref,
                    3
                   FROM public.lead l) r
          ORDER BY r.party_id, (r.ref IS NULL), r.pref
        )
 SELECT fr.ref AS from_ref,
    fp.name AS from_name,
    tr.ref AS to_ref,
    tp.name AS to_name,
    pl.kind,
    pl.note,
    pl.created_at AS linked_at
   FROM ((((public.party_link pl
     JOIN public.party fp ON ((fp.id = pl.from_party)))
     JOIN public.party tp ON ((tp.id = pl.to_party)))
     LEFT JOIN party_ref fr ON ((fr.party_id = pl.from_party)))
     LEFT JOIN party_ref tr ON ((tr.party_id = pl.to_party)));


--
-- Name: VIEW v_party_graph; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_party_graph IS 'The intro graph under the views-only reader role (ORDER 18(c), v_ref_index precedent). SAFE COLUMNS ONLY — never add phone, email, or record notes here; a reader-scoped session sees everything in this view. `note` is the edge provenance, which for backfilled edges is the vendor label verbatim.';


--
-- Name: v_pool; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_pool AS
 SELECT pp.id AS pool_id,
    pp.source,
    pp.source_key,
    pp.name AS display_name,
    pp.org_name,
    pp.vertical,
    pp.city,
    pp.county,
    pp.state,
    pp.segment,
    pp.segment_play,
    pp.score,
    pp.score_basis,
    pp.est_lease_event,
    pp.est_basis,
    pp.status,
    l.registry_ref AS promoted_ref,
    pp.dup_tier,
    pp.dup_subject_type,
    pp.dup_ref,
    pp.dup_basis,
    pp.dup_do_not_contact,
    ((pp.email IS NOT NULL) AND (pp.email <> ''::text)) AS has_email,
    ((pp.phone IS NOT NULL) AND (pp.phone <> ''::text)) AS has_phone,
    pp.created_at,
    pp.version
   FROM (public.candidate_pool pp
     LEFT JOIN public.lead l ON ((l.id = pp.promoted_lead_id)));


--
-- Name: VIEW v_pool; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_pool IS 'Reader surface for the prospect pool. SAFE COLUMNS ONLY — never add email, phone, address or source_row here; a reader-scoped session sees everything in this view, and this view covers thousands of uncontacted third parties.';


--
-- Name: v_precedent; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_precedent AS
 SELECT decision_id,
    entry_date,
    title,
    human_quote,
    agent_rationale,
    author,
    session_key,
    provenance,
    cost_delta,
    quality_delta,
    ((((((COALESCE(title, ''::text) || ' '::text) || COALESCE(title, ''::text)) || ' '::text) || COALESCE(human_quote, ''::text)) || ' '::text) || COALESCE(agent_rationale, ''::text)) AS haystack
   FROM public.v_decision_entry d;


--
-- Name: VIEW v_precedent; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_precedent IS 'Every recorded ruling with one searchable haystack (0106, loop #279). The title is deliberately doubled so a title match outranks a body match with no scoring coefficient to tune, and the human_quote is included because a fork is often findable only by how the partner phrased it. Read by the find-precedent verb; not a second definition of what a decision is — it reads v_decision_entry.';


--
-- Name: v_rate_normalized; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_rate_normalized AS
 SELECT id,
    space_id,
    observed_at,
    source,
    status,
    rate_amount,
    rate_basis,
    COALESCE(rate_norm_sf_yr, rate_norm_gross_sf_yr) AS rate_sf_yr,
    norm_owed,
    opex_sf_yr,
    available_on,
    note
   FROM public.availability a;


--
-- Name: v_record_flag_subject; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_record_flag_subject AS
 SELECT f.id AS flag_id,
    f.subject_type,
    f.subject_id,
        CASE f.subject_type
            WHEN 'campaign'::text THEN ( SELECT c.name
               FROM public.campaign c
              WHERE (c.id = f.subject_id))
            WHEN 'platform'::text THEN ( SELECT m.label
               FROM public.marketing_subject m
              WHERE (m.id = f.subject_id))
            WHEN 'pillar'::text THEN ( SELECT m.label
               FROM public.marketing_subject m
              WHERE (m.id = f.subject_id))
            WHEN 'format'::text THEN ( SELECT m.label
               FROM public.marketing_subject m
              WHERE (m.id = f.subject_id))
            WHEN 'repo'::text THEN ( SELECT COALESCE(k.label, k.repo) AS "coalesce"
               FROM public.code_subject k
              WHERE (k.id = f.subject_id))
            WHEN 'commit'::text THEN ( SELECT COALESCE(k.label, ((k.repo || '@'::text) || "left"(k.commit_sha, 7))) AS "coalesce"
               FROM public.code_subject k
              WHERE (k.id = f.subject_id))
            ELSE ( SELECT r.display_name
               FROM public.v_ref_index r
              WHERE ((r.subject_type = f.subject_type) AND (r.subject_id = f.subject_id))
             LIMIT 1)
        END AS subject_label,
        CASE
            WHEN (f.subject_type = ANY (ARRAY['campaign'::text, 'platform'::text, 'pillar'::text, 'format'::text])) THEN NULL::text
            WHEN (f.subject_type = 'repo'::text) THEN ( SELECT k.repo
               FROM public.code_subject k
              WHERE (k.id = f.subject_id))
            WHEN (f.subject_type = 'commit'::text) THEN ( SELECT ((k.repo || '@'::text) || k.commit_sha)
               FROM public.code_subject k
              WHERE (k.id = f.subject_id))
            ELSE ( SELECT r.ref
               FROM public.v_ref_index r
              WHERE ((r.subject_type = f.subject_type) AND (r.subject_id = f.subject_id))
             LIMIT 1)
        END AS subject_ref,
    f.kind,
    COALESCE(((f.value ->> 'found'::text))::boolean, true) AS found,
    (f.value ? 'proposes_correction'::text) AS proposes_correction,
    f.value,
    f.source,
    f.observed_at,
    f.expires_on,
    ((f.expires_on IS NOT NULL) AND (f.expires_on < CURRENT_DATE)) AS expired,
    a.slug AS recorded_by
   FROM (public.record_flag f
     LEFT JOIN public.actor a ON ((a.id = f.created_by)));


--
-- Name: VIEW v_record_flag_subject; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_record_flag_subject IS 'Every record_flag with its subject resolved to a NAME, across all eleven branches (0066 added four marketing branches, 0101 added repo and commit). The read side of the finding store: without it a platform or code finding is an opaque uuid, and carr_reader cannot see record_flag at all. `found` is lifted out of the jsonb on purpose — a searched-and-empty finding must not read like an absent one.';


--
-- Name: v_role_timeouts; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_role_timeouts AS
 SELECT rolname AS role_name,
    rolcanlogin AS can_login,
    ( SELECT string_agg((g.rolname)::text, ', '::text ORDER BY ((g.rolname)::text)) AS string_agg
           FROM (pg_auth_members m
             JOIN pg_roles g ON ((g.oid = m.roleid)))
          WHERE ((m.member = r.oid) AND (g.rolname ~~ 'carr\_%'::text))) AS bundles,
    ( SELECT s.s
           FROM unnest(COALESCE(r.rolconfig, '{}'::text[])) s(s)
          WHERE (s.s ~~ 'statement_timeout=%'::text)) AS statement_timeout,
    ( SELECT s.s
           FROM unnest(COALESCE(r.rolconfig, '{}'::text[])) s(s)
          WHERE (s.s ~~ 'idle\_in\_transaction\_session\_timeout=%'::text)) AS idle_timeout
   FROM pg_roles r
  WHERE (rolname = ANY (ARRAY['app_reader'::name, 'app_writer'::name, 'app_exporter_local'::name, 'carr_jobs'::name, 'carr_reader'::name, 'carr_writer'::name, 'carr_exporter'::name, 'neondb_owner'::name]));


--
-- Name: VIEW v_role_timeouts; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_role_timeouts IS 'Per-role statement and idle-transaction timeouts (0057). Read it to answer "is anything able to run forever?" without waiting for the next migration guard. The bundles column is the point: a NOLOGIN bundle row showing no timeout is CORRECT — Postgres applies role settings to the role that logged in and never inherits them through membership, so the value has to sit on the login role. A login role with a bundle and a blank statement_timeout is the bug.';


--
-- Name: v_schema_ledger; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_schema_ledger AS
 SELECT filename,
    applied_at
   FROM public.schema_migrations;


--
-- Name: VIEW v_schema_ledger; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_schema_ledger IS 'The /release schema field''s read surface (0113). carr_reader holds no grant on schema_migrations itself — views-only stays intact — so the Worker reads highest-applied-migration and applied-count through here.';


--
-- Name: v_signal_queue; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_signal_queue AS
 SELECT s.id,
    s.producer,
    s.signal_key,
    s.signal_kind,
    s.subject_type,
    s.subject_ref,
    s.metric_name,
    s.observed_value,
    s.baseline_value,
    s.threshold_value,
    s.comparison,
    s.severity,
    s.detected_at,
    s.evidence_refs,
    s.payload,
    s.status,
    a.slug AS created_by
   FROM (public.signal_event s
     JOIN public.actor a ON ((a.id = s.created_by)));


--
-- Name: v_source_attribution; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_source_attribution AS
 WITH lead_lane AS (
         SELECT l.id AS lead_id,
            l.client_id,
            l.created_at,
            COALESCE(pp.source, ('direct:'::text || l.lane), 'direct:unknown'::text) AS lane
           FROM (public.lead l
             LEFT JOIN LATERAL ( SELECT candidate_pool.source
                   FROM public.candidate_pool
                  WHERE (candidate_pool.promoted_lead_id = l.id)
                 LIMIT 1) pp ON (true))
        ), deal_attrib AS (
         SELECT d.id AS deal_id,
            d.outcome,
            d.won_value,
            COALESCE(( SELECT ll_1.lane
                   FROM lead_lane ll_1
                  WHERE (ll_1.client_id = d.client_id)
                  ORDER BY ll_1.created_at, ll_1.lead_id
                 LIMIT 1), '__unattributed__'::text) AS lane
           FROM public.deal d
        ), commission_by_deal AS (
         SELECT commission.deal_id,
            sum(commission.gross_amount) FILTER (WHERE (commission.status = 'received'::text)) AS received,
            sum(commission.gross_amount) FILTER (WHERE (commission.status = ANY (ARRAY['expected'::text, 'invoiced'::text]))) AS open
           FROM public.commission
          GROUP BY commission.deal_id
        ), pool_stage AS (
         SELECT candidate_pool.source AS lane,
            count(*) AS pool_rows,
            count(*) FILTER (WHERE (candidate_pool.status = 'promoted'::text)) AS promoted
           FROM public.candidate_pool
          GROUP BY candidate_pool.source
        ), lane_leads AS (
         SELECT lead_lane.lane,
            count(*) AS leads_total,
            count(DISTINCT lead_lane.client_id) AS clients_converted
           FROM lead_lane
          GROUP BY lead_lane.lane
        ), lane_deals AS (
         SELECT da.lane,
            count(*) AS deals,
            count(*) FILTER (WHERE (da.outcome = 'won'::text)) AS deals_won,
            sum(da.won_value) FILTER (WHERE (da.outcome = 'won'::text)) AS won_value_total,
            sum(cb.received) AS commission_received,
            sum(cb.open) AS commission_open
           FROM (deal_attrib da
             LEFT JOIN commission_by_deal cb ON ((cb.deal_id = da.deal_id)))
          GROUP BY da.lane
        )
 SELECT COALESCE(ps.lane, ll.lane, ld.lane) AS lane,
    COALESCE(ps.pool_rows, (0)::bigint) AS pool_rows,
    COALESCE(ps.promoted, (0)::bigint) AS promoted,
    COALESCE(ll.leads_total, (0)::bigint) AS leads_total,
    COALESCE(ll.clients_converted, (0)::bigint) AS clients_converted,
    COALESCE(ld.deals, (0)::bigint) AS deals,
    COALESCE(ld.deals_won, (0)::bigint) AS deals_won,
    ld.won_value_total,
    ld.commission_received,
    ld.commission_open
   FROM ((pool_stage ps
     FULL JOIN lane_leads ll ON ((ll.lane = ps.lane)))
     FULL JOIN lane_deals ld ON ((ld.lane = COALESCE(ps.lane, ll.lane))));


--
-- Name: VIEW v_source_attribution; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_source_attribution IS '0026+0027 / ORDER 33: per-lane funnel. One lane per deal (earliest linked lead); deals with no lead linkage appear as lane=__unattributed__ (dunder sentinel — cannot collide with a real pool source name) so totals reconcile against the whole book. Money columns read the commission table ONLY.';


--
-- Name: v_subject_timeline; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_subject_timeline AS
 SELECT 'activity'::text AS entry_kind,
    a.id,
    a.occurred_at,
    a.recorded_at,
    act.slug AS actor,
    a.kind AS verb,
    a.summary,
    a.detail,
    a.owed,
    COALESCE(a.deal_id, a.client_id, a.lead_id, a.vendor_id) AS subject_id,
        CASE
            WHEN (a.deal_id IS NOT NULL) THEN 'deal'::text
            WHEN (a.client_id IS NOT NULL) THEN 'client'::text
            WHEN (a.lead_id IS NOT NULL) THEN 'lead'::text
            ELSE 'vendor'::text
        END AS subject_type
   FROM (public.activity a
     JOIN public.actor act ON ((act.id = a.actor_id)))
UNION ALL
 SELECT 'event'::text AS entry_kind,
    e.id,
    e.occurred_at,
    e.recorded_at,
    act.slug AS actor,
    e.verb,
    COALESCE(NULLIF(btrim((e.new_value ->> 'summary'::text)), ''::text), e.field, e.verb) AS summary,
    e.human_quote AS detail,
    NULL::text AS owed,
    e.subject_id,
    e.subject_type
   FROM (public.event e
     JOIN public.actor act ON ((act.id = e.actor_id)));


--
-- Name: v_vendor_level_suggestion; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_vendor_level_suggestion AS
 WITH contact AS (
         SELECT v.id AS vendor_id,
            count(*) FILTER (WHERE ((a.kind = ANY (ARRAY['meeting'::text, 'tour'::text, 'loi'::text, 'lease_signed'::text, 'email_in'::text, 'counter_received'::text])) OR ((a.kind = ANY (ARRAY['call'::text, 'text'::text])) AND (a.connected IS TRUE)))) AS two_way,
            count(*) FILTER (WHERE ((a.kind = 'email_out'::text) OR ((a.kind = ANY (ARRAY['call'::text, 'text'::text])) AND (a.connected IS NOT TRUE)))) AS attempts_only
           FROM (public.vendor v
             LEFT JOIN public.activity a ON ((a.vendor_id = v.id)))
          GROUP BY v.id
        ), value_moved AS (
         SELECT v.id AS vendor_id,
            count(*) FILTER (WHERE (pl.via_party = v.party_id)) AS they_gave,
            count(*) FILTER (WHERE ((pl.via_party IN ( SELECT party.id
                   FROM public.party
                  WHERE (party.name = ANY (ARRAY['Joe Bookout'::text, 'Dell McCraney'::text])))) AND ((pl.from_party = v.party_id) OR (pl.to_party = v.party_id)))) AS we_gave
           FROM (public.vendor v
             LEFT JOIN public.party_link pl ON (((pl.kind = ANY (ARRAY['introduced'::text, 'referred'::text])) AND ((pl.via_party = v.party_id) OR (pl.from_party = v.party_id) OR (pl.to_party = v.party_id)))))
          GROUP BY v.id
        ), scored AS (
         SELECT v.vendor_ref,
            p.name,
            v.relationship_level AS recorded,
            c.two_way,
            c.attempts_only,
            vm.they_gave,
            vm.we_gave,
            (((c.two_way + c.attempts_only) + vm.they_gave) + vm.we_gave) AS evidence_events,
                CASE
                    WHEN ((((c.two_way + c.attempts_only) + vm.they_gave) + vm.we_gave) = 0) THEN NULL::integer
                    WHEN ((vm.they_gave > 1) AND (vm.we_gave > 1)) THEN 3
                    WHEN ((vm.they_gave > 0) OR (vm.we_gave > 0)) THEN 2
                    WHEN (c.two_way > 0) THEN 1
                    ELSE 0
                END AS suggested
           FROM (((public.vendor v
             JOIN public.party p ON ((p.id = v.party_id)))
             JOIN contact c ON ((c.vendor_id = v.id)))
             JOIN value_moved vm ON ((vm.vendor_id = v.id)))
          WHERE ((v.disposition = 'active'::text) AND (v.merged_into IS NULL))
        )
 SELECT vendor_ref,
    name,
    recorded,
    suggested,
    ((recorded IS NOT NULL) AND (suggested IS NOT NULL) AND (recorded <> suggested)) AS disagrees,
        CASE
            WHEN (evidence_events = 0) THEN 'no_evidence'::text
            WHEN (recorded IS NULL) THEN 'unjudged_with_evidence'::text
            WHEN (recorded = suggested) THEN 'agrees'::text
            WHEN (suggested > recorded) THEN 'evidence_exceeds_recorded'::text
            ELSE 'recorded_exceeds_evidence'::text
        END AS signal,
    evidence_events,
    two_way,
    attempts_only,
    they_gave,
    we_gave
   FROM scored s;


--
-- Name: VIEW v_vendor_level_suggestion; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_vendor_level_suggestion IS 'Recorded relationship level against what the evidence supports (0052/0053/0065 lineage; 0069 excludes merged vendor rows so a tombstone never double-counts its survivor''s evidence). Reports only — the level stays a human judgment.';


--
-- Name: v_vendor_needs_type; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.v_vendor_needs_type AS
 SELECT v.vendor_ref,
    p.name,
    p.title,
    v.category AS old_value,
    v.is_target,
    v.relationship_level,
    v.territory
   FROM (public.vendor v
     JOIN public.party p ON ((p.id = v.party_id)))
  WHERE ((v.category_slug IS NULL) AND (v.disposition = 'active'::text) AND (v.merged_into IS NULL))
  ORDER BY v.is_target DESC, v.relationship_level DESC NULLS LAST, p.name;


--
-- Name: VIEW v_vendor_needs_type; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON VIEW public.v_vendor_needs_type IS 'Active vendors with no real category. 63 at 0050: 22 were "Misc" and 41 were "Target (not yet met)", a stage stored in a type field. A profession cannot be inferred from a record, so these wait for Joe or Dell rather than being guessed. Sorted targets-first and deepest-relationship-first, because those are the ones whose type is worth knowing soonest.';


--
-- Name: vendor_disposition; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vendor_disposition (
    slug text NOT NULL,
    label text NOT NULL,
    workable boolean NOT NULL,
    sort integer NOT NULL
);


--
-- Name: vendor_relationship_level; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vendor_relationship_level (
    level integer NOT NULL,
    label text NOT NULL,
    note text NOT NULL
);


--
-- Name: account account_pkey; Type: CONSTRAINT; Schema: neon_auth; Owner: -
--

ALTER TABLE ONLY neon_auth.account
    ADD CONSTRAINT account_pkey PRIMARY KEY (id);


--
-- Name: invitation invitation_pkey; Type: CONSTRAINT; Schema: neon_auth; Owner: -
--

ALTER TABLE ONLY neon_auth.invitation
    ADD CONSTRAINT invitation_pkey PRIMARY KEY (id);


--
-- Name: jwks jwks_pkey; Type: CONSTRAINT; Schema: neon_auth; Owner: -
--

ALTER TABLE ONLY neon_auth.jwks
    ADD CONSTRAINT jwks_pkey PRIMARY KEY (id);


--
-- Name: member member_pkey; Type: CONSTRAINT; Schema: neon_auth; Owner: -
--

ALTER TABLE ONLY neon_auth.member
    ADD CONSTRAINT member_pkey PRIMARY KEY (id);


--
-- Name: organization organization_pkey; Type: CONSTRAINT; Schema: neon_auth; Owner: -
--

ALTER TABLE ONLY neon_auth.organization
    ADD CONSTRAINT organization_pkey PRIMARY KEY (id);


--
-- Name: organization organization_slug_key; Type: CONSTRAINT; Schema: neon_auth; Owner: -
--

ALTER TABLE ONLY neon_auth.organization
    ADD CONSTRAINT organization_slug_key UNIQUE (slug);


--
-- Name: project_config project_config_endpoint_id_key; Type: CONSTRAINT; Schema: neon_auth; Owner: -
--

ALTER TABLE ONLY neon_auth.project_config
    ADD CONSTRAINT project_config_endpoint_id_key UNIQUE (endpoint_id);


--
-- Name: project_config project_config_pkey; Type: CONSTRAINT; Schema: neon_auth; Owner: -
--

ALTER TABLE ONLY neon_auth.project_config
    ADD CONSTRAINT project_config_pkey PRIMARY KEY (id);


--
-- Name: session session_pkey; Type: CONSTRAINT; Schema: neon_auth; Owner: -
--

ALTER TABLE ONLY neon_auth.session
    ADD CONSTRAINT session_pkey PRIMARY KEY (id);


--
-- Name: session session_token_key; Type: CONSTRAINT; Schema: neon_auth; Owner: -
--

ALTER TABLE ONLY neon_auth.session
    ADD CONSTRAINT session_token_key UNIQUE (token);


--
-- Name: user user_email_key; Type: CONSTRAINT; Schema: neon_auth; Owner: -
--

ALTER TABLE ONLY neon_auth."user"
    ADD CONSTRAINT user_email_key UNIQUE (email);


--
-- Name: user user_pkey; Type: CONSTRAINT; Schema: neon_auth; Owner: -
--

ALTER TABLE ONLY neon_auth."user"
    ADD CONSTRAINT user_pkey PRIMARY KEY (id);


--
-- Name: verification verification_pkey; Type: CONSTRAINT; Schema: neon_auth; Owner: -
--

ALTER TABLE ONLY neon_auth.verification
    ADD CONSTRAINT verification_pkey PRIMARY KEY (id);


--
-- Name: deployment deployment_pkey; Type: CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.deployment
    ADD CONSTRAINT deployment_pkey PRIMARY KEY (id);


--
-- Name: incident_fact incident_fact_pkey; Type: CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.incident_fact
    ADD CONSTRAINT incident_fact_pkey PRIMARY KEY (id);


--
-- Name: incident_hypothesis incident_hypothesis_pkey; Type: CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.incident_hypothesis
    ADD CONSTRAINT incident_hypothesis_pkey PRIMARY KEY (id);


--
-- Name: incident_link incident_link_pkey; Type: CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.incident_link
    ADD CONSTRAINT incident_link_pkey PRIMARY KEY (incident_id, kind, ref);


--
-- Name: incident incident_pkey; Type: CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.incident
    ADD CONSTRAINT incident_pkey PRIMARY KEY (id);


--
-- Name: incident incident_ref_key; Type: CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.incident
    ADD CONSTRAINT incident_ref_key UNIQUE (ref);


--
-- Name: incident_service incident_service_pkey; Type: CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.incident_service
    ADD CONSTRAINT incident_service_pkey PRIMARY KEY (incident_id, service_id);


--
-- Name: run run_pkey; Type: CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.run
    ADD CONSTRAINT run_pkey PRIMARY KEY (id);


--
-- Name: service_dependency service_dependency_pkey; Type: CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.service_dependency
    ADD CONSTRAINT service_dependency_pkey PRIMARY KEY (service_id, depends_on_id);


--
-- Name: service_environment service_environment_pkey; Type: CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.service_environment
    ADD CONSTRAINT service_environment_pkey PRIMARY KEY (service_id, environment);


--
-- Name: service service_key_key; Type: CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.service
    ADD CONSTRAINT service_key_key UNIQUE (key);


--
-- Name: service service_pkey; Type: CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.service
    ADD CONSTRAINT service_pkey PRIMARY KEY (id);


--
-- Name: settings_change settings_change_pkey; Type: CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.settings_change
    ADD CONSTRAINT settings_change_pkey PRIMARY KEY (id);


--
-- Name: work_request work_request_pkey; Type: CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.work_request
    ADD CONSTRAINT work_request_pkey PRIMARY KEY (id);


--
-- Name: work_request work_request_ref_key; Type: CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.work_request
    ADD CONSTRAINT work_request_ref_key UNIQUE (ref);


--
-- Name: activity_kind activity_kind_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.activity_kind
    ADD CONSTRAINT activity_kind_pkey PRIMARY KEY (slug);


--
-- Name: activity activity_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.activity
    ADD CONSTRAINT activity_pkey PRIMARY KEY (id);


--
-- Name: actor actor_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actor
    ADD CONSTRAINT actor_pkey PRIMARY KEY (id);


--
-- Name: actor_profile actor_profile_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actor_profile
    ADD CONSTRAINT actor_profile_pkey PRIMARY KEY (actor_id, key);


--
-- Name: actor actor_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actor
    ADD CONSTRAINT actor_slug_key UNIQUE (slug);


--
-- Name: agreement agreement_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agreement
    ADD CONSTRAINT agreement_pkey PRIMARY KEY (id);


--
-- Name: ammo_item ammo_item_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ammo_item
    ADD CONSTRAINT ammo_item_pkey PRIMARY KEY (id);


--
-- Name: attachment attachment_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachment
    ADD CONSTRAINT attachment_pkey PRIMARY KEY (id);


--
-- Name: attachment attachment_r2_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachment
    ADD CONSTRAINT attachment_r2_key_key UNIQUE (r2_key);


--
-- Name: availability availability_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.availability
    ADD CONSTRAINT availability_pkey PRIMARY KEY (id);


--
-- Name: building_ownership building_ownership_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.building_ownership
    ADD CONSTRAINT building_ownership_pkey PRIMARY KEY (id);


--
-- Name: building building_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.building
    ADD CONSTRAINT building_pkey PRIMARY KEY (id);


--
-- Name: cadence_rule cadence_rule_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cadence_rule
    ADD CONSTRAINT cadence_rule_pkey PRIMARY KEY (id);


--
-- Name: campaign campaign_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign
    ADD CONSTRAINT campaign_pkey PRIMARY KEY (id);


--
-- Name: capture_candidate capture_candidate_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_candidate
    ADD CONSTRAINT capture_candidate_pkey PRIMARY KEY (id);


--
-- Name: capture_candidate capture_candidate_session_id_idempotency_key_item_index_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_candidate
    ADD CONSTRAINT capture_candidate_session_id_idempotency_key_item_index_key UNIQUE (session_id, idempotency_key, item_index);


--
-- Name: capture_post_call_action capture_post_call_action_candidate_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_post_call_action
    ADD CONSTRAINT capture_post_call_action_candidate_id_key UNIQUE (candidate_id);


--
-- Name: capture_post_call_action capture_post_call_action_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_post_call_action
    ADD CONSTRAINT capture_post_call_action_pkey PRIMARY KEY (id);


--
-- Name: capture_post_call_candidate capture_post_call_candidate_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_post_call_candidate
    ADD CONSTRAINT capture_post_call_candidate_pkey PRIMARY KEY (id);


--
-- Name: capture_post_call_candidate capture_post_call_candidate_session_id_idempotency_key_item_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_post_call_candidate
    ADD CONSTRAINT capture_post_call_candidate_session_id_idempotency_key_item_key UNIQUE (session_id, idempotency_key, item_index);


--
-- Name: capture_post_call_report capture_post_call_report_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_post_call_report
    ADD CONSTRAINT capture_post_call_report_pkey PRIMARY KEY (session_id);


--
-- Name: capture_post_call_report capture_post_call_report_session_id_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_post_call_report
    ADD CONSTRAINT capture_post_call_report_session_id_idempotency_key_key UNIQUE (session_id, idempotency_key);


--
-- Name: capture_session capture_session_nonce_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_session
    ADD CONSTRAINT capture_session_nonce_key UNIQUE (nonce);


--
-- Name: capture_session capture_session_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_session
    ADD CONSTRAINT capture_session_pkey PRIMARY KEY (id);


--
-- Name: capture_session capture_session_session_token_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_session
    ADD CONSTRAINT capture_session_session_token_hash_key UNIQUE (session_token_hash);


--
-- Name: client client_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client
    ADD CONSTRAINT client_pkey PRIMARY KEY (id);


--
-- Name: client client_roster_ref_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client
    ADD CONSTRAINT client_roster_ref_key UNIQUE (roster_ref);


--
-- Name: client_status client_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client_status
    ADD CONSTRAINT client_status_pkey PRIMARY KEY (slug);


--
-- Name: client_type client_type_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client_type
    ADD CONSTRAINT client_type_pkey PRIMARY KEY (slug);


--
-- Name: code_subject code_subject_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.code_subject
    ADD CONSTRAINT code_subject_pkey PRIMARY KEY (id);


--
-- Name: commission_allocation commission_allocation_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commission_allocation
    ADD CONSTRAINT commission_allocation_pkey PRIMARY KEY (id);


--
-- Name: commission commission_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commission
    ADD CONSTRAINT commission_pkey PRIMARY KEY (id);


--
-- Name: comp comp_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comp
    ADD CONSTRAINT comp_pkey PRIMARY KEY (id);


--
-- Name: contact_state contact_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_state
    ADD CONSTRAINT contact_state_pkey PRIMARY KEY (slug);


--
-- Name: contact_state contact_state_sort_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contact_state
    ADD CONSTRAINT contact_state_sort_key UNIQUE (sort);


--
-- Name: content_piece content_piece_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_piece
    ADD CONSTRAINT content_piece_pkey PRIMARY KEY (id);


--
-- Name: critical_date critical_date_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.critical_date
    ADD CONSTRAINT critical_date_pkey PRIMARY KEY (id);


--
-- Name: deal_conflict deal_conflict_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_conflict
    ADD CONSTRAINT deal_conflict_pkey PRIMARY KEY (id);


--
-- Name: deal_lane deal_lane_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_lane
    ADD CONSTRAINT deal_lane_pkey PRIMARY KEY (slug);


--
-- Name: deal_market_assignment deal_market_assignment_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_market_assignment
    ADD CONSTRAINT deal_market_assignment_pkey PRIMARY KEY (deal_id);


--
-- Name: deal_note deal_note_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_note
    ADD CONSTRAINT deal_note_pkey PRIMARY KEY (id);


--
-- Name: deal_participant deal_participant_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_participant
    ADD CONSTRAINT deal_participant_pkey PRIMARY KEY (id);


--
-- Name: deal_phase deal_phase_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_phase
    ADD CONSTRAINT deal_phase_pkey PRIMARY KEY (slug);


--
-- Name: deal deal_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal
    ADD CONSTRAINT deal_pkey PRIMARY KEY (id);


--
-- Name: deal_presence_lease deal_presence_lease_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_presence_lease
    ADD CONSTRAINT deal_presence_lease_pkey PRIMARY KEY (actor_id, deal_id, field);


--
-- Name: deal_reattach_log deal_reattach_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_reattach_log
    ADD CONSTRAINT deal_reattach_log_pkey PRIMARY KEY (deal_id, from_client);


--
-- Name: deal_review_item deal_review_item_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_review_item
    ADD CONSTRAINT deal_review_item_pkey PRIMARY KEY (session_id, deal_id);


--
-- Name: deal_review_session deal_review_session_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_review_session
    ADD CONSTRAINT deal_review_session_pkey PRIMARY KEY (id);


--
-- Name: deal deal_salesforce_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal
    ADD CONSTRAINT deal_salesforce_id_key UNIQUE (salesforce_id);


--
-- Name: deal_type_ref deal_type_ref_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_type_ref
    ADD CONSTRAINT deal_type_ref_pkey PRIMARY KEY (slug);


--
-- Name: defect defect_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.defect
    ADD CONSTRAINT defect_pkey PRIMARY KEY (id);


--
-- Name: deprecation deprecation_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deprecation
    ADD CONSTRAINT deprecation_pkey PRIMARY KEY (object_name);


--
-- Name: diagnostic_route diagnostic_route_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diagnostic_route
    ADD CONSTRAINT diagnostic_route_pkey PRIMARY KEY (route_key);


--
-- Name: doc_template doc_template_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doc_template
    ADD CONSTRAINT doc_template_pkey PRIMARY KEY (id);


--
-- Name: doc_template doc_template_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doc_template
    ADD CONSTRAINT doc_template_slug_key UNIQUE (slug);


--
-- Name: doctrine_change_item doctrine_change_item_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_change_item
    ADD CONSTRAINT doctrine_change_item_pkey PRIMARY KEY (change_set_id, section_id);


--
-- Name: doctrine_change_set doctrine_change_set_actor_id_idempotency_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_change_set
    ADD CONSTRAINT doctrine_change_set_actor_id_idempotency_key_key UNIQUE (actor_id, idempotency_key);


--
-- Name: doctrine_change_set doctrine_change_set_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_change_set
    ADD CONSTRAINT doctrine_change_set_pkey PRIMARY KEY (id);


--
-- Name: doctrine_claim doctrine_claim_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_claim
    ADD CONSTRAINT doctrine_claim_pkey PRIMARY KEY (section_id);


--
-- Name: doctrine_document doctrine_document_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_document
    ADD CONSTRAINT doctrine_document_pkey PRIMARY KEY (id);


--
-- Name: doctrine_document doctrine_document_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_document
    ADD CONSTRAINT doctrine_document_slug_key UNIQUE (slug);


--
-- Name: doctrine_edge doctrine_edge_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_edge
    ADD CONSTRAINT doctrine_edge_pkey PRIMARY KEY (id);


--
-- Name: doctrine_edge_type doctrine_edge_type_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_edge_type
    ADD CONSTRAINT doctrine_edge_type_pkey PRIMARY KEY (edge_type);


--
-- Name: doctrine_gate_check doctrine_gate_check_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_gate_check
    ADD CONSTRAINT doctrine_gate_check_pkey PRIMARY KEY (check_key);


--
-- Name: doctrine_gate_finding doctrine_gate_finding_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_gate_finding
    ADD CONSTRAINT doctrine_gate_finding_pkey PRIMARY KEY (run_id, check_key, path);


--
-- Name: doctrine_gate_run doctrine_gate_run_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_gate_run
    ADD CONSTRAINT doctrine_gate_run_pkey PRIMARY KEY (id);


--
-- Name: doctrine_link doctrine_link_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_link
    ADD CONSTRAINT doctrine_link_pkey PRIMARY KEY (id);


--
-- Name: doctrine_meta doctrine_meta_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_meta
    ADD CONSTRAINT doctrine_meta_pkey PRIMARY KEY (id);


--
-- Name: doctrine_migration_batch doctrine_migration_batch_batch_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_migration_batch
    ADD CONSTRAINT doctrine_migration_batch_batch_no_key UNIQUE (batch_no);


--
-- Name: doctrine_migration_batch doctrine_migration_batch_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_migration_batch
    ADD CONSTRAINT doctrine_migration_batch_pkey PRIMARY KEY (id);


--
-- Name: doctrine_review_policy doctrine_review_policy_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_review_policy
    ADD CONSTRAINT doctrine_review_policy_name_key UNIQUE (name);


--
-- Name: doctrine_review_policy doctrine_review_policy_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_review_policy
    ADD CONSTRAINT doctrine_review_policy_pkey PRIMARY KEY (id);


--
-- Name: doctrine_revision doctrine_revision_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_revision
    ADD CONSTRAINT doctrine_revision_pkey PRIMARY KEY (id);


--
-- Name: doctrine_revision doctrine_revision_section_id_version_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_revision
    ADD CONSTRAINT doctrine_revision_section_id_version_key UNIQUE (section_id, version);


--
-- Name: doctrine_section doctrine_section_document_id_section_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_section
    ADD CONSTRAINT doctrine_section_document_id_section_key_key UNIQUE (document_id, section_key);


--
-- Name: doctrine_section doctrine_section_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_section
    ADD CONSTRAINT doctrine_section_pkey PRIMARY KEY (id);


--
-- Name: doctrine_slug_alias doctrine_slug_alias_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_slug_alias
    ADD CONSTRAINT doctrine_slug_alias_pkey PRIMARY KEY (alias_slug);


--
-- Name: doctrine_snapshot doctrine_snapshot_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_snapshot
    ADD CONSTRAINT doctrine_snapshot_pkey PRIMARY KEY (document_id);


--
-- Name: document document_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document
    ADD CONSTRAINT document_pkey PRIMARY KEY (id);


--
-- Name: event event_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event
    ADD CONSTRAINT event_pkey PRIMARY KEY (id);


--
-- Name: experiment experiment_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.experiment
    ADD CONSTRAINT experiment_pkey PRIMARY KEY (id);


--
-- Name: export_run export_run_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.export_run
    ADD CONSTRAINT export_run_pkey PRIMARY KEY (id);


--
-- Name: growth_snapshot growth_snapshot_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.growth_snapshot
    ADD CONSTRAINT growth_snapshot_pkey PRIMARY KEY (taken_on, table_name);


--
-- Name: ingest_inbox ingest_inbox_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingest_inbox
    ADD CONSTRAINT ingest_inbox_pkey PRIMARY KEY (id);


--
-- Name: ingest_inbox ingest_inbox_source_external_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingest_inbox
    ADD CONSTRAINT ingest_inbox_source_external_id_key UNIQUE (source, external_id);


--
-- Name: investigation_branch investigation_branch_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.investigation_branch
    ADD CONSTRAINT investigation_branch_pkey PRIMARY KEY (id);


--
-- Name: investigation_evidence investigation_evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.investigation_evidence
    ADD CONSTRAINT investigation_evidence_pkey PRIMARY KEY (id);


--
-- Name: investigation_run investigation_run_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.investigation_run
    ADD CONSTRAINT investigation_run_pkey PRIMARY KEY (id);


--
-- Name: lead_lane lead_lane_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lead_lane
    ADD CONSTRAINT lead_lane_pkey PRIMARY KEY (slug);


--
-- Name: lead lead_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lead
    ADD CONSTRAINT lead_pkey PRIMARY KEY (id);


--
-- Name: lead lead_registry_ref_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lead
    ADD CONSTRAINT lead_registry_ref_key UNIQUE (registry_ref);


--
-- Name: lead_stage lead_stage_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lead_stage
    ADD CONSTRAINT lead_stage_pkey PRIMARY KEY (slug);


--
-- Name: lease lease_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lease
    ADD CONSTRAINT lease_pkey PRIMARY KEY (id);


--
-- Name: loop_block loop_block_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.loop_block
    ADD CONSTRAINT loop_block_pkey PRIMARY KEY (id);


--
-- Name: loop_block loop_block_rel_path_seq_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.loop_block
    ADD CONSTRAINT loop_block_rel_path_seq_key UNIQUE (rel_path, seq);


--
-- Name: loop_domain loop_domain_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.loop_domain
    ADD CONSTRAINT loop_domain_pkey PRIMARY KEY (slug);


--
-- Name: loop_domain loop_domain_sort_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.loop_domain
    ADD CONSTRAINT loop_domain_sort_key UNIQUE (sort);


--
-- Name: loop_item loop_item_block_id_render_seq_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.loop_item
    ADD CONSTRAINT loop_item_block_id_render_seq_key UNIQUE (block_id, render_seq);


--
-- Name: loop_item loop_item_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.loop_item
    ADD CONSTRAINT loop_item_pkey PRIMARY KEY (id);


--
-- Name: marketing_subject marketing_subject_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.marketing_subject
    ADD CONSTRAINT marketing_subject_pkey PRIMARY KEY (id);


--
-- Name: marketing_subject marketing_subject_subject_type_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.marketing_subject
    ADD CONSTRAINT marketing_subject_subject_type_slug_key UNIQUE (subject_type, slug);


--
-- Name: media_recommendation media_recommendation_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_recommendation
    ADD CONSTRAINT media_recommendation_pkey PRIMARY KEY (id);


--
-- Name: national_account_owner national_account_owner_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.national_account_owner
    ADD CONSTRAINT national_account_owner_pkey PRIMARY KEY (account_client_id);


--
-- Name: negotiation_claim negotiation_claim_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.negotiation_claim
    ADD CONSTRAINT negotiation_claim_pkey PRIMARY KEY (id);


--
-- Name: negotiation_claim negotiation_claim_round_id_claim_type_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.negotiation_claim
    ADD CONSTRAINT negotiation_claim_round_id_claim_type_key UNIQUE (round_id, claim_type);


--
-- Name: negotiation_claim_type negotiation_claim_type_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.negotiation_claim_type
    ADD CONSTRAINT negotiation_claim_type_pkey PRIMARY KEY (slug);


--
-- Name: negotiation_claim_type negotiation_claim_type_slug_derived_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.negotiation_claim_type
    ADD CONSTRAINT negotiation_claim_type_slug_derived_key UNIQUE (slug, derived);


--
-- Name: negotiation_claim_type negotiation_claim_type_sort_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.negotiation_claim_type
    ADD CONSTRAINT negotiation_claim_type_sort_key UNIQUE (sort);


--
-- Name: negotiation_round negotiation_round_deal_id_round_no_side_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.negotiation_round
    ADD CONSTRAINT negotiation_round_deal_id_round_no_side_key UNIQUE (deal_id, round_no, side);


--
-- Name: negotiation_round negotiation_round_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.negotiation_round
    ADD CONSTRAINT negotiation_round_pkey PRIMARY KEY (id);


--
-- Name: next_action next_action_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.next_action
    ADD CONSTRAINT next_action_pkey PRIMARY KEY (id);


--
-- Name: org_merge_log org_merge_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.org_merge_log
    ADD CONSTRAINT org_merge_log_pkey PRIMARY KEY (party_id, from_org);


--
-- Name: parcel parcel_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parcel
    ADD CONSTRAINT parcel_pkey PRIMARY KEY (id);


--
-- Name: parcel parcel_state_county_parcel_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.parcel
    ADD CONSTRAINT parcel_state_county_parcel_no_key UNIQUE (state, county, parcel_no);


--
-- Name: participant_role participant_role_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.participant_role
    ADD CONSTRAINT participant_role_pkey PRIMARY KEY (slug);


--
-- Name: party_link_kind party_link_kind_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.party_link_kind
    ADD CONSTRAINT party_link_kind_pkey PRIMARY KEY (slug);


--
-- Name: party_link party_link_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.party_link
    ADD CONSTRAINT party_link_pkey PRIMARY KEY (id);


--
-- Name: party party_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.party
    ADD CONSTRAINT party_pkey PRIMARY KEY (id);


--
-- Name: placement_measurement placement_measurement_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.placement_measurement
    ADD CONSTRAINT placement_measurement_pkey PRIMARY KEY (id);


--
-- Name: placement_measurement placement_measurement_placement_id_source_attempted_at_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.placement_measurement
    ADD CONSTRAINT placement_measurement_placement_id_source_attempted_at_key UNIQUE (placement_id, source, attempted_at);


--
-- Name: placement_metric placement_metric_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.placement_metric
    ADD CONSTRAINT placement_metric_pkey PRIMARY KEY (placement_id, kind, observed_at);


--
-- Name: placement placement_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.placement
    ADD CONSTRAINT placement_pkey PRIMARY KEY (id);


--
-- Name: premises premises_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.premises
    ADD CONSTRAINT premises_pkey PRIMARY KEY (id);


--
-- Name: premises_space premises_space_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.premises_space
    ADD CONSTRAINT premises_space_pkey PRIMARY KEY (premises_id, space_id);


--
-- Name: candidate_pool prospect_pool_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_pool
    ADD CONSTRAINT prospect_pool_pkey PRIMARY KEY (id);


--
-- Name: candidate_pool prospect_pool_source_source_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_pool
    ADD CONSTRAINT prospect_pool_source_source_key_key UNIQUE (source, source_key);


--
-- Name: record_flag record_flag_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_flag
    ADD CONSTRAINT record_flag_pkey PRIMARY KEY (id);


--
-- Name: record_source record_source_source_system_external_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_source
    ADD CONSTRAINT record_source_source_system_external_key_key UNIQUE (source_system, external_key);


--
-- Name: registration registration_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registration
    ADD CONSTRAINT registration_pkey PRIMARY KEY (id);


--
-- Name: rule rule_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule
    ADD CONSTRAINT rule_pkey PRIMARY KEY (id);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (filename);


--
-- Name: search_candidate search_candidate_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.search_candidate
    ADD CONSTRAINT search_candidate_pkey PRIMARY KEY (id);


--
-- Name: sensitive_blob sensitive_blob_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sensitive_blob
    ADD CONSTRAINT sensitive_blob_pkey PRIMARY KEY (id);


--
-- Name: signal_event signal_event_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_event
    ADD CONSTRAINT signal_event_pkey PRIMARY KEY (id);


--
-- Name: signal_event signal_event_producer_signal_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_event
    ADD CONSTRAINT signal_event_producer_signal_key_key UNIQUE (producer, signal_key);


--
-- Name: source_capture source_capture_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_capture
    ADD CONSTRAINT source_capture_pkey PRIMARY KEY (id);


--
-- Name: space space_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.space
    ADD CONSTRAINT space_pkey PRIMARY KEY (id);


--
-- Name: space_search space_search_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.space_search
    ADD CONSTRAINT space_search_pkey PRIMARY KEY (id);


--
-- Name: submarket_condition submarket_condition_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submarket_condition
    ADD CONSTRAINT submarket_condition_pkey PRIMARY KEY (slug);


--
-- Name: submarket_condition submarket_condition_sort_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.submarket_condition
    ADD CONSTRAINT submarket_condition_sort_key UNIQUE (sort);


--
-- Name: system_config system_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_config
    ADD CONSTRAINT system_config_pkey PRIMARY KEY (key);


--
-- Name: tool_call tool_call_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tool_call
    ADD CONSTRAINT tool_call_pkey PRIMARY KEY (idempotency_key);


--
-- Name: tool_read_call tool_read_call_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tool_read_call
    ADD CONSTRAINT tool_read_call_pkey PRIMARY KEY (id);


--
-- Name: vendor_category vendor_category_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vendor_category
    ADD CONSTRAINT vendor_category_pkey PRIMARY KEY (slug);


--
-- Name: vendor_category vendor_category_sort_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vendor_category
    ADD CONSTRAINT vendor_category_sort_key UNIQUE (sort);


--
-- Name: vendor_disposition vendor_disposition_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vendor_disposition
    ADD CONSTRAINT vendor_disposition_pkey PRIMARY KEY (slug);


--
-- Name: vendor_disposition vendor_disposition_sort_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vendor_disposition
    ADD CONSTRAINT vendor_disposition_sort_key UNIQUE (sort);


--
-- Name: vendor vendor_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vendor
    ADD CONSTRAINT vendor_pkey PRIMARY KEY (id);


--
-- Name: vendor_relationship_level vendor_relationship_level_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vendor_relationship_level
    ADD CONSTRAINT vendor_relationship_level_pkey PRIMARY KEY (level);


--
-- Name: vendor_stage vendor_stage_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vendor_stage
    ADD CONSTRAINT vendor_stage_pkey PRIMARY KEY (slug);


--
-- Name: vendor vendor_vendor_ref_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vendor
    ADD CONSTRAINT vendor_vendor_ref_key UNIQUE (vendor_ref);


--
-- Name: account_userId_idx; Type: INDEX; Schema: neon_auth; Owner: -
--

CREATE INDEX "account_userId_idx" ON neon_auth.account USING btree ("userId");


--
-- Name: invitation_email_idx; Type: INDEX; Schema: neon_auth; Owner: -
--

CREATE INDEX invitation_email_idx ON neon_auth.invitation USING btree (email);


--
-- Name: invitation_organizationId_idx; Type: INDEX; Schema: neon_auth; Owner: -
--

CREATE INDEX "invitation_organizationId_idx" ON neon_auth.invitation USING btree ("organizationId");


--
-- Name: member_organizationId_idx; Type: INDEX; Schema: neon_auth; Owner: -
--

CREATE INDEX "member_organizationId_idx" ON neon_auth.member USING btree ("organizationId");


--
-- Name: member_userId_idx; Type: INDEX; Schema: neon_auth; Owner: -
--

CREATE INDEX "member_userId_idx" ON neon_auth.member USING btree ("userId");


--
-- Name: organization_slug_uidx; Type: INDEX; Schema: neon_auth; Owner: -
--

CREATE UNIQUE INDEX organization_slug_uidx ON neon_auth.organization USING btree (slug);


--
-- Name: session_userId_idx; Type: INDEX; Schema: neon_auth; Owner: -
--

CREATE INDEX "session_userId_idx" ON neon_auth.session USING btree ("userId");


--
-- Name: verification_identifier_idx; Type: INDEX; Schema: neon_auth; Owner: -
--

CREATE INDEX verification_identifier_idx ON neon_auth.verification USING btree (identifier);


--
-- Name: deployment_correlation_idx; Type: INDEX; Schema: ops; Owner: -
--

CREATE INDEX deployment_correlation_idx ON ops.deployment USING btree (correlation_id);


--
-- Name: deployment_env_observed_idx; Type: INDEX; Schema: ops; Owner: -
--

CREATE INDEX deployment_env_observed_idx ON ops.deployment USING btree (environment, observed_at DESC);


--
-- Name: incident_correlation_idx; Type: INDEX; Schema: ops; Owner: -
--

CREATE INDEX incident_correlation_idx ON ops.incident USING btree (correlation_id);


--
-- Name: incident_one_open_per_signature; Type: INDEX; Schema: ops; Owner: -
--

CREATE UNIQUE INDEX incident_one_open_per_signature ON ops.incident USING btree (signature) WHERE (state <> ALL (ARRAY['resolved'::text, 'reviewed'::text]));


--
-- Name: INDEX incident_one_open_per_signature; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON INDEX ops.incident_one_open_per_signature IS 'The deduplication rule as a constraint. monitoring counts as OPEN on purpose: a service that fails again while we are watching it recover is the same incident continuing, not a second one starting.';


--
-- Name: incident_open_idx; Type: INDEX; Schema: ops; Owner: -
--

CREATE INDEX incident_open_idx ON ops.incident USING btree (state, detected_at DESC) WHERE (state <> 'reviewed'::text);


--
-- Name: run_correlation_idx; Type: INDEX; Schema: ops; Owner: -
--

CREATE INDEX run_correlation_idx ON ops.run USING btree (correlation_id);


--
-- Name: run_open_idx; Type: INDEX; Schema: ops; Owner: -
--

CREATE INDEX run_open_idx ON ops.run USING btree (state, observed_at DESC) WHERE (state = ANY (ARRAY['scheduled'::text, 'queued'::text, 'running'::text]));


--
-- Name: INDEX run_open_idx; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON INDEX ops.run_open_idx IS 'Partial on purpose: terminal runs accumulate forever and the common question is what is in flight or stuck.';


--
-- Name: run_service_env_observed_idx; Type: INDEX; Schema: ops; Owner: -
--

CREATE INDEX run_service_env_observed_idx ON ops.run USING btree (service_id, environment, observed_at DESC);


--
-- Name: settings_change_kind_idx; Type: INDEX; Schema: ops; Owner: -
--

CREATE INDEX settings_change_kind_idx ON ops.settings_change USING btree (kind, recorded_at DESC);


--
-- Name: settings_change_recorded_idx; Type: INDEX; Schema: ops; Owner: -
--

CREATE INDEX settings_change_recorded_idx ON ops.settings_change USING btree (recorded_at DESC);


--
-- Name: work_request_state_idx; Type: INDEX; Schema: ops; Owner: -
--

CREATE INDEX work_request_state_idx ON ops.work_request USING btree (state) WHERE (state <> ALL (ARRAY['confirmed_closed'::text, 'declined'::text, 'superseded'::text]));


--
-- Name: INDEX work_request_state_idx; Type: COMMENT; Schema: ops; Owner: -
--

COMMENT ON INDEX ops.work_request_state_idx IS 'Partial on purpose: the common query is open work, and terminal rows accumulate forever. Indexing only live rows keeps it small as history grows.';


--
-- Name: activity_deal_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX activity_deal_idx ON public.activity USING btree (deal_id, occurred_at DESC);


--
-- Name: building_addr_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX building_addr_trgm ON public.building USING gin (address public.gin_trgm_ops);


--
-- Name: building_merged_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX building_merged_idx ON public.building USING btree (merged_into) WHERE (merged_into IS NOT NULL);


--
-- Name: building_ownership_bldg_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX building_ownership_bldg_idx ON public.building_ownership USING btree (building_id) WHERE (to_on IS NULL);


--
-- Name: campaign_name_uniq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX campaign_name_uniq ON public.campaign USING btree (lower(btrim(name)));


--
-- Name: candidate_pool_declined_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX candidate_pool_declined_idx ON public.candidate_pool USING btree (declined_at) WHERE (status = 'declined'::text);


--
-- Name: capture_candidate_queue_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX capture_candidate_queue_idx ON public.capture_candidate USING btree (session_id, status, confidence DESC, created_at, id);


--
-- Name: capture_post_call_candidate_session_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX capture_post_call_candidate_session_idx ON public.capture_post_call_candidate USING btree (session_id, status, created_at, id);


--
-- Name: capture_session_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX capture_session_active_idx ON public.capture_session USING btree (expires_at, state);


--
-- Name: client_merged_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX client_merged_idx ON public.client USING btree (merged_into) WHERE (merged_into IS NOT NULL);


--
-- Name: code_subject_identity_uniq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX code_subject_identity_uniq ON public.code_subject USING btree (repo, COALESCE(commit_sha, ''::text));


--
-- Name: deal_conflict_open_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deal_conflict_open_idx ON public.deal_conflict USING btree (deal_id, field, created_at DESC) WHERE (status = 'open'::text);


--
-- Name: deal_note_thread_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deal_note_thread_idx ON public.deal_note USING btree (deal_id, created_at DESC, id DESC);


--
-- Name: deal_one_current_lead; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deal_one_current_lead ON public.deal_participant USING btree (deal_id) WHERE ((role = 'lead'::text) AND (to_at IS NULL));


--
-- Name: deal_participant_one_current_lead; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deal_participant_one_current_lead ON public.deal_participant USING btree (deal_id) WHERE ((role = 'lead'::text) AND (to_at IS NULL));


--
-- Name: INDEX deal_participant_one_current_lead; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON INDEX public.deal_participant_one_current_lead IS 'One CURRENT lead agent per deal (0060). Closed rows (to_at not null) are history and accumulate freely — Sonography Studios legitimately holds a closed row abutting its open one, which is set-lead recording a handoff, not a duplicate. This index is what set-lead''s description has been claiming since it was written; before 0060 the invariant was upheld only by that verb''s own close-then-insert, which is not atomic against a second concurrent caller and is bypassed entirely by a direct insert (0036 hand-rolled a `not exists` guard for exactly this reason).';


--
-- Name: deal_presence_live_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deal_presence_live_idx ON public.deal_presence_lease USING btree (expires_at);


--
-- Name: deal_review_item_deal_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX deal_review_item_deal_idx ON public.deal_review_item USING btree (deal_id, reviewed_at DESC);


--
-- Name: deal_review_one_open_per_actor_workspace; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX deal_review_one_open_per_actor_workspace ON public.deal_review_session USING btree (started_by, workspace_kind, COALESCE(account_client_id, '00000000-0000-0000-0000-000000000000'::uuid)) WHERE (status = 'open'::text);


--
-- Name: defect_class_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX defect_class_idx ON public.defect USING btree (defect_class, occurred_on DESC);


--
-- Name: diagnostic_route_neighborhood_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX diagnostic_route_neighborhood_idx ON public.diagnostic_route USING btree (from_kind, signal_kind) WHERE active;


--
-- Name: doctrine_claim_expiry_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX doctrine_claim_expiry_idx ON public.doctrine_claim USING btree (expires_at);


--
-- Name: doctrine_document_class_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX doctrine_document_class_idx ON public.doctrine_document USING btree (content_class, updated_at DESC);


--
-- Name: doctrine_edge_live_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX doctrine_edge_live_uq ON public.doctrine_edge USING btree (source_section_id, target_section_id, edge_type) WHERE (retired_by_revision_id IS NULL);


--
-- Name: doctrine_edge_source_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX doctrine_edge_source_idx ON public.doctrine_edge USING btree (source_section_id, edge_type);


--
-- Name: doctrine_edge_target_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX doctrine_edge_target_idx ON public.doctrine_edge USING btree (target_section_id, edge_type);


--
-- Name: doctrine_link_source_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX doctrine_link_source_idx ON public.doctrine_link USING btree (source_section_id);


--
-- Name: doctrine_link_target_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX doctrine_link_target_idx ON public.doctrine_link USING btree (target_kind, target_id);


--
-- Name: doctrine_revision_search_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX doctrine_revision_search_idx ON public.doctrine_revision USING gin (search_vector);


--
-- Name: doctrine_revision_section_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX doctrine_revision_section_idx ON public.doctrine_revision USING btree (section_id, created_at DESC);


--
-- Name: doctrine_section_doc_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX doctrine_section_doc_idx ON public.doctrine_section USING btree (document_id, ordinal);


--
-- Name: doctrine_section_review_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX doctrine_section_review_idx ON public.doctrine_section USING btree (review_after) WHERE (review_after IS NOT NULL);


--
-- Name: event_actor_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX event_actor_idx ON public.event USING btree (actor_id, occurred_at);


--
-- Name: event_correlation_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX event_correlation_idx ON public.event USING btree (correlation_id) WHERE (correlation_id IS NOT NULL);


--
-- Name: event_deal_cursor_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX event_deal_cursor_idx ON public.event USING btree (recorded_at, id) WHERE (subject_type = 'deal'::text);


--
-- Name: event_decision_text_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX event_decision_text_trgm ON public.event USING gin ((((((COALESCE((new_value ->> 'title'::text), ''::text) || ' '::text) || COALESCE(human_quote, ''::text)) || ' '::text) || COALESCE(agent_rationale, ''::text))) public.gin_trgm_ops) WHERE (verb = 'log-decision'::text);


--
-- Name: event_idem_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX event_idem_idx ON public.event USING btree (idempotency_key);


--
-- Name: event_subject_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX event_subject_idx ON public.event USING btree (subject_type, subject_id, occurred_at);


--
-- Name: investigation_branch_run_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX investigation_branch_run_idx ON public.investigation_branch USING btree (run_id, depth, opened_at);


--
-- Name: investigation_evidence_branch_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX investigation_evidence_branch_idx ON public.investigation_evidence USING btree (branch_id, recorded_at);


--
-- Name: investigation_one_open_per_signal; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX investigation_one_open_per_signal ON public.investigation_run USING btree (signal_id) WHERE (status = 'open'::text);


--
-- Name: loop_block_key_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX loop_block_key_idx ON public.loop_block USING btree (rel_path, block_key) WHERE (block_key IS NOT NULL);


--
-- Name: loop_item_block_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX loop_item_block_idx ON public.loop_item USING btree (block_id, render_seq);


--
-- Name: loop_item_domain_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX loop_item_domain_idx ON public.loop_item USING btree (domain, status);


--
-- Name: loop_item_drift_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX loop_item_drift_idx ON public.loop_item USING btree (drift_critical) WHERE drift_critical;


--
-- Name: loop_item_due_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX loop_item_due_idx ON public.loop_item USING btree (due_on) WHERE (due_on IS NOT NULL);


--
-- Name: loop_item_kind_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX loop_item_kind_idx ON public.loop_item USING btree (kind, status);


--
-- Name: loop_item_number_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX loop_item_number_idx ON public.loop_item USING btree (number);


--
-- Name: loop_item_open_number_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX loop_item_open_number_unique ON public.loop_item USING btree (kind, number) WHERE (status = 'open'::text);


--
-- Name: loop_item_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX loop_item_status_idx ON public.loop_item USING btree (status);


--
-- Name: media_recommendation_title_uniq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX media_recommendation_title_uniq ON public.media_recommendation USING btree (lower(btrim(title)), personal_to);


--
-- Name: negotiation_claim_round_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX negotiation_claim_round_idx ON public.negotiation_claim USING btree (round_id);


--
-- Name: negotiation_claim_type_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX negotiation_claim_type_idx ON public.negotiation_claim USING btree (claim_type);


--
-- Name: one_open_next_action_per_owner; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX one_open_next_action_per_owner ON public.next_action USING btree (subject_type, subject_id, owner_id) WHERE (status = 'open'::text);


--
-- Name: party_contact_state_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX party_contact_state_idx ON public.party USING btree (contact_state);


--
-- Name: party_link_from_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX party_link_from_idx ON public.party_link USING btree (from_party, kind);


--
-- Name: party_link_from_to_kind_uidx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX party_link_from_to_kind_uidx ON public.party_link USING btree (from_party, to_party, kind);


--
-- Name: INDEX party_link_from_to_kind_uidx; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON INDEX public.party_link_from_to_kind_uidx IS 'ORDER 18(b): one edge per (from, to, kind). link-parties upserts against this index and returns the existing edge rather than a second row.';


--
-- Name: party_link_to_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX party_link_to_idx ON public.party_link USING btree (to_party, kind);


--
-- Name: party_link_via_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX party_link_via_idx ON public.party_link USING btree (via_party, kind);


--
-- Name: party_merged_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX party_merged_idx ON public.party USING btree (merged_into) WHERE (merged_into IS NOT NULL);


--
-- Name: party_name_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX party_name_trgm ON public.party USING gin (name public.gin_trgm_ops);


--
-- Name: party_org_identity_uniq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX party_org_identity_uniq ON public.party USING btree (public.org_identity_key(name)) WHERE ((kind = 'org'::text) AND (merged_into IS NULL) AND (deleted_at IS NULL) AND (public.org_identity_key(name) IS NOT NULL));


--
-- Name: INDEX party_org_identity_uniq; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON INDEX public.party_org_identity_uniq IS 'One live row per organisation (0059). This is the fix; the consolidation is only the backlog. A genuine same-name collision (two unrelated "Lighthouse Dental") is resolved by disambiguating the NAME — the data already does this with "Carr Riggs Ingram (advisory)" — never by weakening the key. WARNING: add-party and add-premises do a blind insert and will raise unique_violation until both call sites use org_party_id().';


--
-- Name: party_ref_uniq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX party_ref_uniq ON public.party USING btree (ref);


--
-- Name: placement_measurement_placement_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX placement_measurement_placement_idx ON public.placement_measurement USING btree (placement_id, attempted_at DESC);


--
-- Name: prospect_pool_county_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX prospect_pool_county_idx ON public.candidate_pool USING btree (county);


--
-- Name: prospect_pool_event_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX prospect_pool_event_idx ON public.candidate_pool USING btree (est_lease_event) WHERE (est_lease_event IS NOT NULL);


--
-- Name: prospect_pool_segment_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX prospect_pool_segment_idx ON public.candidate_pool USING btree (segment);


--
-- Name: prospect_pool_source_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX prospect_pool_source_idx ON public.candidate_pool USING btree (source, source_seq);


--
-- Name: prospect_pool_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX prospect_pool_status_idx ON public.candidate_pool USING btree (status);


--
-- Name: record_flag_subject_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX record_flag_subject_idx ON public.record_flag USING btree (subject_type, subject_id, kind);


--
-- Name: record_source_entity_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX record_source_entity_idx ON public.record_source USING btree (entity_type, entity_id);


--
-- Name: signal_event_queue_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX signal_event_queue_idx ON public.signal_event USING btree (status, severity, detected_at DESC);


--
-- Name: signal_event_subject_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX signal_event_subject_idx ON public.signal_event USING btree (subject_type, subject_ref, detected_at DESC);


--
-- Name: source_capture_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX source_capture_date_idx ON public.source_capture USING btree (captured_on);


--
-- Name: source_capture_session_trgm; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX source_capture_session_trgm ON public.source_capture USING gin (session public.gin_trgm_ops);


--
-- Name: tool_call_correlation_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tool_call_correlation_idx ON public.tool_call USING btree (correlation_id) WHERE (correlation_id IS NOT NULL);


--
-- Name: tool_read_call_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tool_read_call_created_at_idx ON public.tool_read_call USING btree (created_at);


--
-- Name: tool_read_call_verb_sponsor_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX tool_read_call_verb_sponsor_idx ON public.tool_read_call USING btree (verb, sponsoring_human_slug, created_at DESC);


--
-- Name: vendor_category_slug_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX vendor_category_slug_idx ON public.vendor USING btree (category_slug);


--
-- Name: vendor_merged_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX vendor_merged_idx ON public.vendor USING btree (merged_into) WHERE (merged_into IS NOT NULL);


--
-- Name: vendor_relationship_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX vendor_relationship_idx ON public.vendor USING btree (relationship_level, disposition);


--
-- Name: v_deal_room_session _RETURN; Type: RULE; Schema: public; Owner: -
--

CREATE OR REPLACE VIEW public.v_deal_room_session AS
 SELECT s.id AS session_id,
    s.workspace_kind,
    s.account_client_id,
    a.slug AS started_by,
    s.started_at,
    s.ended_at,
    s.status,
    s.summary,
    count(i.deal_id) FILTER (WHERE (i.disposition = 'reviewed'::text)) AS reviewed_count,
    count(i.deal_id) FILTER (WHERE (i.disposition = 'skipped'::text)) AS skipped_count
   FROM ((public.deal_review_session s
     JOIN public.actor a ON ((a.id = s.started_by)))
     LEFT JOIN public.deal_review_item i ON ((i.session_id = s.id)))
  GROUP BY s.id, a.slug;


--
-- Name: v_investigation _RETURN; Type: RULE; Schema: public; Owner: -
--

CREATE OR REPLACE VIEW public.v_investigation AS
 SELECT r.id,
    r.signal_id,
    s.signal_kind,
    s.subject_type,
    s.subject_ref,
    r.objective,
    a.slug AS owner,
    r.max_depth,
    r.status,
    r.conclusion,
    r.confidence,
    r.strongest_alternative,
    r.alternative_disposition,
    r.termination_reason,
    r.opened_at,
    r.closed_at,
    count(b.id) AS branch_count,
    count(b.id) FILTER (WHERE (b.status = 'open'::text)) AS open_branch_count
   FROM (((public.investigation_run r
     JOIN public.signal_event s ON ((s.id = r.signal_id)))
     JOIN public.actor a ON ((a.id = r.owner_actor_id)))
     LEFT JOIN public.investigation_branch b ON ((b.run_id = r.id)))
  GROUP BY r.id, s.id, a.slug;


--
-- Name: activity activity_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER activity_touch BEFORE UPDATE ON public.activity FOR EACH ROW EXECUTE FUNCTION public.trg_touch_row();


--
-- Name: agreement agreement_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER agreement_touch BEFORE UPDATE ON public.agreement FOR EACH ROW EXECUTE FUNCTION public.trg_touch_row();


--
-- Name: availability availability_norm; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER availability_norm BEFORE INSERT OR UPDATE ON public.availability FOR EACH ROW EXECUTE FUNCTION public.trg_availability_norm();


--
-- Name: building building_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER building_touch BEFORE UPDATE ON public.building FOR EACH ROW EXECUTE FUNCTION public.trg_touch_row();


--
-- Name: campaign campaign_channels_check; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER campaign_channels_check BEFORE INSERT OR UPDATE OF channels ON public.campaign FOR EACH ROW EXECUTE FUNCTION public.campaign_channels_valid();


--
-- Name: campaign campaign_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER campaign_touch BEFORE UPDATE ON public.campaign FOR EACH ROW EXECUTE FUNCTION public.trg_touch_row();


--
-- Name: client client_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER client_touch BEFORE UPDATE ON public.client FOR EACH ROW EXECUTE FUNCTION public.trg_touch_row();


--
-- Name: commission commission_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER commission_touch BEFORE UPDATE ON public.commission FOR EACH ROW EXECUTE FUNCTION public.trg_touch_row();


--
-- Name: content_piece content_piece_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER content_piece_touch BEFORE UPDATE ON public.content_piece FOR EACH ROW EXECUTE FUNCTION public.trg_touch_row();


--
-- Name: critical_date critical_date_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER critical_date_touch BEFORE UPDATE ON public.critical_date FOR EACH ROW EXECUTE FUNCTION public.trg_touch_row();


--
-- Name: deal_participant deal_participant_side; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER deal_participant_side BEFORE INSERT OR UPDATE OF role, actor_id, party_id ON public.deal_participant FOR EACH ROW EXECUTE FUNCTION public.trg_deal_participant_side();


--
-- Name: deal deal_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER deal_touch BEFORE UPDATE ON public.deal FOR EACH ROW EXECUTE FUNCTION public.trg_touch_row();


--
-- Name: lead lead_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER lead_touch BEFORE UPDATE ON public.lead FOR EACH ROW EXECUTE FUNCTION public.trg_touch_row();


--
-- Name: lease lease_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER lease_touch BEFORE UPDATE ON public.lease FOR EACH ROW EXECUTE FUNCTION public.trg_touch_row();


--
-- Name: loop_block loop_block_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER loop_block_touch BEFORE UPDATE ON public.loop_block FOR EACH ROW EXECUTE FUNCTION public.trg_touch_row();


--
-- Name: loop_item loop_item_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER loop_item_touch BEFORE UPDATE ON public.loop_item FOR EACH ROW EXECUTE FUNCTION public.trg_touch_row();


--
-- Name: negotiation_round negotiation_round_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER negotiation_round_touch BEFORE UPDATE ON public.negotiation_round FOR EACH ROW EXECUTE FUNCTION public.trg_touch_row();


--
-- Name: next_action next_action_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER next_action_touch BEFORE UPDATE ON public.next_action FOR EACH ROW EXECUTE FUNCTION public.trg_touch_row();


--
-- Name: party party_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER party_touch BEFORE UPDATE ON public.party FOR EACH ROW EXECUTE FUNCTION public.trg_touch_row();


--
-- Name: candidate_pool prospect_pool_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER prospect_pool_touch BEFORE UPDATE ON public.candidate_pool FOR EACH ROW EXECUTE FUNCTION public.trg_touch_row();


--
-- Name: rule rule_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER rule_touch BEFORE UPDATE ON public.rule FOR EACH ROW EXECUTE FUNCTION public.trg_touch_row();


--
-- Name: source_capture source_capture_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER source_capture_touch BEFORE UPDATE ON public.source_capture FOR EACH ROW EXECUTE FUNCTION public.trg_touch_row();


--
-- Name: space space_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER space_touch BEFORE UPDATE ON public.space FOR EACH ROW EXECUTE FUNCTION public.trg_touch_row();


--
-- Name: vendor vendor_touch; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER vendor_touch BEFORE UPDATE ON public.vendor FOR EACH ROW EXECUTE FUNCTION public.trg_touch_row();


--
-- Name: account account_userId_fkey; Type: FK CONSTRAINT; Schema: neon_auth; Owner: -
--

ALTER TABLE ONLY neon_auth.account
    ADD CONSTRAINT "account_userId_fkey" FOREIGN KEY ("userId") REFERENCES neon_auth."user"(id) ON DELETE CASCADE;


--
-- Name: invitation invitation_inviterId_fkey; Type: FK CONSTRAINT; Schema: neon_auth; Owner: -
--

ALTER TABLE ONLY neon_auth.invitation
    ADD CONSTRAINT "invitation_inviterId_fkey" FOREIGN KEY ("inviterId") REFERENCES neon_auth."user"(id) ON DELETE CASCADE;


--
-- Name: invitation invitation_organizationId_fkey; Type: FK CONSTRAINT; Schema: neon_auth; Owner: -
--

ALTER TABLE ONLY neon_auth.invitation
    ADD CONSTRAINT "invitation_organizationId_fkey" FOREIGN KEY ("organizationId") REFERENCES neon_auth.organization(id) ON DELETE CASCADE;


--
-- Name: member member_organizationId_fkey; Type: FK CONSTRAINT; Schema: neon_auth; Owner: -
--

ALTER TABLE ONLY neon_auth.member
    ADD CONSTRAINT "member_organizationId_fkey" FOREIGN KEY ("organizationId") REFERENCES neon_auth.organization(id) ON DELETE CASCADE;


--
-- Name: member member_userId_fkey; Type: FK CONSTRAINT; Schema: neon_auth; Owner: -
--

ALTER TABLE ONLY neon_auth.member
    ADD CONSTRAINT "member_userId_fkey" FOREIGN KEY ("userId") REFERENCES neon_auth."user"(id) ON DELETE CASCADE;


--
-- Name: session session_userId_fkey; Type: FK CONSTRAINT; Schema: neon_auth; Owner: -
--

ALTER TABLE ONLY neon_auth.session
    ADD CONSTRAINT "session_userId_fkey" FOREIGN KEY ("userId") REFERENCES neon_auth."user"(id) ON DELETE CASCADE;


--
-- Name: deployment deployment_rollback_of_fkey; Type: FK CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.deployment
    ADD CONSTRAINT deployment_rollback_of_fkey FOREIGN KEY (rollback_of) REFERENCES ops.deployment(id);


--
-- Name: deployment deployment_service_id_fkey; Type: FK CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.deployment
    ADD CONSTRAINT deployment_service_id_fkey FOREIGN KEY (service_id) REFERENCES ops.service(id);


--
-- Name: incident_fact incident_fact_incident_id_fkey; Type: FK CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.incident_fact
    ADD CONSTRAINT incident_fact_incident_id_fkey FOREIGN KEY (incident_id) REFERENCES ops.incident(id) ON DELETE CASCADE;


--
-- Name: incident_hypothesis incident_hypothesis_incident_id_fkey; Type: FK CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.incident_hypothesis
    ADD CONSTRAINT incident_hypothesis_incident_id_fkey FOREIGN KEY (incident_id) REFERENCES ops.incident(id) ON DELETE CASCADE;


--
-- Name: incident_link incident_link_incident_id_fkey; Type: FK CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.incident_link
    ADD CONSTRAINT incident_link_incident_id_fkey FOREIGN KEY (incident_id) REFERENCES ops.incident(id) ON DELETE CASCADE;


--
-- Name: incident_service incident_service_incident_id_fkey; Type: FK CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.incident_service
    ADD CONSTRAINT incident_service_incident_id_fkey FOREIGN KEY (incident_id) REFERENCES ops.incident(id) ON DELETE CASCADE;


--
-- Name: incident_service incident_service_service_id_fkey; Type: FK CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.incident_service
    ADD CONSTRAINT incident_service_service_id_fkey FOREIGN KEY (service_id) REFERENCES ops.service(id);


--
-- Name: run run_service_id_fkey; Type: FK CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.run
    ADD CONSTRAINT run_service_id_fkey FOREIGN KEY (service_id) REFERENCES ops.service(id);


--
-- Name: service_dependency service_dependency_depends_on_id_fkey; Type: FK CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.service_dependency
    ADD CONSTRAINT service_dependency_depends_on_id_fkey FOREIGN KEY (depends_on_id) REFERENCES ops.service(id) ON DELETE CASCADE;


--
-- Name: service_dependency service_dependency_service_id_fkey; Type: FK CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.service_dependency
    ADD CONSTRAINT service_dependency_service_id_fkey FOREIGN KEY (service_id) REFERENCES ops.service(id) ON DELETE CASCADE;


--
-- Name: service_environment service_environment_service_id_fkey; Type: FK CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.service_environment
    ADD CONSTRAINT service_environment_service_id_fkey FOREIGN KEY (service_id) REFERENCES ops.service(id) ON DELETE CASCADE;


--
-- Name: work_request work_request_superseded_by_fkey; Type: FK CONSTRAINT; Schema: ops; Owner: -
--

ALTER TABLE ONLY ops.work_request
    ADD CONSTRAINT work_request_superseded_by_fkey FOREIGN KEY (superseded_by) REFERENCES ops.work_request(id);


--
-- Name: activity activity_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.activity
    ADD CONSTRAINT activity_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actor(id);


--
-- Name: activity activity_client_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.activity
    ADD CONSTRAINT activity_client_fk FOREIGN KEY (client_id) REFERENCES public.client(id);


--
-- Name: activity activity_deal_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.activity
    ADD CONSTRAINT activity_deal_fk FOREIGN KEY (deal_id) REFERENCES public.deal(id);


--
-- Name: activity activity_kind_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.activity
    ADD CONSTRAINT activity_kind_fkey FOREIGN KEY (kind) REFERENCES public.activity_kind(slug);


--
-- Name: activity activity_lead_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.activity
    ADD CONSTRAINT activity_lead_fk FOREIGN KEY (lead_id) REFERENCES public.lead(id);


--
-- Name: activity activity_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.activity
    ADD CONSTRAINT activity_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.actor(id);


--
-- Name: activity activity_vendor_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.activity
    ADD CONSTRAINT activity_vendor_fk FOREIGN KEY (vendor_id) REFERENCES public.vendor(id);


--
-- Name: actor_profile actor_profile_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.actor_profile
    ADD CONSTRAINT actor_profile_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actor(id);


--
-- Name: agreement agreement_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agreement
    ADD CONSTRAINT agreement_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.client(id);


--
-- Name: agreement agreement_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agreement
    ADD CONSTRAINT agreement_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: agreement agreement_doc_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agreement
    ADD CONSTRAINT agreement_doc_fk FOREIGN KEY (doc_attachment) REFERENCES public.attachment(id);


--
-- Name: agreement agreement_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agreement
    ADD CONSTRAINT agreement_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.actor(id);


--
-- Name: attachment attachment_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.attachment
    ADD CONSTRAINT attachment_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: availability availability_space_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.availability
    ADD CONSTRAINT availability_space_id_fkey FOREIGN KEY (space_id) REFERENCES public.space(id);


--
-- Name: building building_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.building
    ADD CONSTRAINT building_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: building building_merged_into_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.building
    ADD CONSTRAINT building_merged_into_fkey FOREIGN KEY (merged_into) REFERENCES public.building(id);


--
-- Name: building_ownership building_ownership_building_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.building_ownership
    ADD CONSTRAINT building_ownership_building_id_fkey FOREIGN KEY (building_id) REFERENCES public.building(id);


--
-- Name: building_ownership building_ownership_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.building_ownership
    ADD CONSTRAINT building_ownership_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: building_ownership building_ownership_party_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.building_ownership
    ADD CONSTRAINT building_ownership_party_id_fkey FOREIGN KEY (party_id) REFERENCES public.party(id);


--
-- Name: building building_parcel_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.building
    ADD CONSTRAINT building_parcel_id_fkey FOREIGN KEY (parcel_id) REFERENCES public.parcel(id);


--
-- Name: building building_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.building
    ADD CONSTRAINT building_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.actor(id);


--
-- Name: campaign campaign_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign
    ADD CONSTRAINT campaign_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: campaign campaign_scored_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign
    ADD CONSTRAINT campaign_scored_by_fkey FOREIGN KEY (scored_by) REFERENCES public.actor(id);


--
-- Name: campaign campaign_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign
    ADD CONSTRAINT campaign_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.actor(id);


--
-- Name: candidate_pool candidate_pool_declined_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_pool
    ADD CONSTRAINT candidate_pool_declined_by_fkey FOREIGN KEY (declined_by) REFERENCES public.actor(id);


--
-- Name: capture_candidate capture_candidate_resolved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_candidate
    ADD CONSTRAINT capture_candidate_resolved_by_fkey FOREIGN KEY (resolved_by) REFERENCES public.actor(id);


--
-- Name: capture_candidate capture_candidate_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_candidate
    ADD CONSTRAINT capture_candidate_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.capture_session(id);


--
-- Name: capture_post_call_action capture_post_call_action_accepted_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_post_call_action
    ADD CONSTRAINT capture_post_call_action_accepted_by_fkey FOREIGN KEY (accepted_by) REFERENCES public.actor(id);


--
-- Name: capture_post_call_action capture_post_call_action_candidate_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_post_call_action
    ADD CONSTRAINT capture_post_call_action_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES public.capture_post_call_candidate(id);


--
-- Name: capture_post_call_action capture_post_call_action_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_post_call_action
    ADD CONSTRAINT capture_post_call_action_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deal(id);


--
-- Name: capture_post_call_action capture_post_call_action_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_post_call_action
    ADD CONSTRAINT capture_post_call_action_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES public.actor(id);


--
-- Name: capture_post_call_candidate capture_post_call_candidate_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_post_call_candidate
    ADD CONSTRAINT capture_post_call_candidate_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deal(id);


--
-- Name: capture_post_call_candidate capture_post_call_candidate_recipient_party_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_post_call_candidate
    ADD CONSTRAINT capture_post_call_candidate_recipient_party_id_fkey FOREIGN KEY (recipient_party_id) REFERENCES public.party(id);


--
-- Name: capture_post_call_candidate capture_post_call_candidate_resolved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_post_call_candidate
    ADD CONSTRAINT capture_post_call_candidate_resolved_by_fkey FOREIGN KEY (resolved_by) REFERENCES public.actor(id);


--
-- Name: capture_post_call_candidate capture_post_call_candidate_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_post_call_candidate
    ADD CONSTRAINT capture_post_call_candidate_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.capture_session(id);


--
-- Name: capture_post_call_report capture_post_call_report_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_post_call_report
    ADD CONSTRAINT capture_post_call_report_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.capture_session(id);


--
-- Name: capture_session capture_session_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.capture_session
    ADD CONSTRAINT capture_session_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actor(id);


--
-- Name: client client_client_type_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client
    ADD CONSTRAINT client_client_type_fkey FOREIGN KEY (client_type) REFERENCES public.client_type(slug);


--
-- Name: client client_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client
    ADD CONSTRAINT client_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: client client_merged_into_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client
    ADD CONSTRAINT client_merged_into_fkey FOREIGN KEY (merged_into) REFERENCES public.client(id);


--
-- Name: client client_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client
    ADD CONSTRAINT client_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES public.actor(id);


--
-- Name: client client_party_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client
    ADD CONSTRAINT client_party_id_fkey FOREIGN KEY (party_id) REFERENCES public.party(id);


--
-- Name: client client_status_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client
    ADD CONSTRAINT client_status_fkey FOREIGN KEY (status) REFERENCES public.client_status(slug);


--
-- Name: client client_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.client
    ADD CONSTRAINT client_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.actor(id);


--
-- Name: code_subject code_subject_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.code_subject
    ADD CONSTRAINT code_subject_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: commission_allocation commission_allocation_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commission_allocation
    ADD CONSTRAINT commission_allocation_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actor(id);


--
-- Name: commission_allocation commission_allocation_commission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commission_allocation
    ADD CONSTRAINT commission_allocation_commission_id_fkey FOREIGN KEY (commission_id) REFERENCES public.commission(id);


--
-- Name: commission_allocation commission_allocation_parent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commission_allocation
    ADD CONSTRAINT commission_allocation_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.commission_allocation(id);


--
-- Name: commission_allocation commission_allocation_party_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commission_allocation
    ADD CONSTRAINT commission_allocation_party_id_fkey FOREIGN KEY (party_id) REFERENCES public.party(id);


--
-- Name: commission commission_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commission
    ADD CONSTRAINT commission_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: commission commission_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commission
    ADD CONSTRAINT commission_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deal(id);


--
-- Name: commission commission_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commission
    ADD CONSTRAINT commission_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.actor(id);


--
-- Name: comp comp_building_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comp
    ADD CONSTRAINT comp_building_id_fkey FOREIGN KEY (building_id) REFERENCES public.building(id);


--
-- Name: comp comp_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comp
    ADD CONSTRAINT comp_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: comp comp_space_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.comp
    ADD CONSTRAINT comp_space_id_fkey FOREIGN KEY (space_id) REFERENCES public.space(id);


--
-- Name: content_piece content_piece_author_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_piece
    ADD CONSTRAINT content_piece_author_id_fkey FOREIGN KEY (author_id) REFERENCES public.actor(id);


--
-- Name: content_piece content_piece_campaign_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_piece
    ADD CONSTRAINT content_piece_campaign_id_fkey FOREIGN KEY (campaign_id) REFERENCES public.campaign(id);


--
-- Name: content_piece content_piece_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.content_piece
    ADD CONSTRAINT content_piece_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.actor(id);


--
-- Name: critical_date critical_date_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.critical_date
    ADD CONSTRAINT critical_date_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: critical_date critical_date_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.critical_date
    ADD CONSTRAINT critical_date_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deal(id);


--
-- Name: critical_date critical_date_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.critical_date
    ADD CONSTRAINT critical_date_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.actor(id);


--
-- Name: deal deal_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal
    ADD CONSTRAINT deal_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.client(id);


--
-- Name: deal_conflict deal_conflict_actor_a_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_conflict
    ADD CONSTRAINT deal_conflict_actor_a_fkey FOREIGN KEY (actor_a) REFERENCES public.actor(id);


--
-- Name: deal_conflict deal_conflict_actor_b_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_conflict
    ADD CONSTRAINT deal_conflict_actor_b_fkey FOREIGN KEY (actor_b) REFERENCES public.actor(id);


--
-- Name: deal_conflict deal_conflict_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_conflict
    ADD CONSTRAINT deal_conflict_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deal(id);


--
-- Name: deal_conflict deal_conflict_event_a_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_conflict
    ADD CONSTRAINT deal_conflict_event_a_fkey FOREIGN KEY (event_a) REFERENCES public.event(id);


--
-- Name: deal_conflict deal_conflict_resolved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_conflict
    ADD CONSTRAINT deal_conflict_resolved_by_fkey FOREIGN KEY (resolved_by) REFERENCES public.actor(id);


--
-- Name: deal deal_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal
    ADD CONSTRAINT deal_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: deal deal_deal_type_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal
    ADD CONSTRAINT deal_deal_type_fkey FOREIGN KEY (deal_type) REFERENCES public.deal_type_ref(slug);


--
-- Name: deal deal_lane_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal
    ADD CONSTRAINT deal_lane_fkey FOREIGN KEY (lane) REFERENCES public.deal_lane(slug);


--
-- Name: deal_market_assignment deal_market_assignment_agent_party_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_market_assignment
    ADD CONSTRAINT deal_market_assignment_agent_party_id_fkey FOREIGN KEY (agent_party_id) REFERENCES public.party(id);


--
-- Name: deal_market_assignment deal_market_assignment_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_market_assignment
    ADD CONSTRAINT deal_market_assignment_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deal(id);


--
-- Name: deal_market_assignment deal_market_assignment_set_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_market_assignment
    ADD CONSTRAINT deal_market_assignment_set_by_fkey FOREIGN KEY (set_by) REFERENCES public.actor(id);


--
-- Name: deal_note deal_note_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_note
    ADD CONSTRAINT deal_note_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actor(id);


--
-- Name: deal_note deal_note_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_note
    ADD CONSTRAINT deal_note_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deal(id);


--
-- Name: deal deal_owner_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal
    ADD CONSTRAINT deal_owner_fkey FOREIGN KEY (owner) REFERENCES public.actor(slug);


--
-- Name: deal deal_parked_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal
    ADD CONSTRAINT deal_parked_by_fkey FOREIGN KEY (parked_by) REFERENCES public.actor(id);


--
-- Name: deal_participant deal_participant_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_participant
    ADD CONSTRAINT deal_participant_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actor(id);


--
-- Name: deal_participant deal_participant_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_participant
    ADD CONSTRAINT deal_participant_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deal(id);


--
-- Name: deal_participant deal_participant_party_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_participant
    ADD CONSTRAINT deal_participant_party_id_fkey FOREIGN KEY (party_id) REFERENCES public.party(id);


--
-- Name: deal_participant deal_participant_role_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_participant
    ADD CONSTRAINT deal_participant_role_fkey FOREIGN KEY (role) REFERENCES public.participant_role(slug);


--
-- Name: deal_participant deal_participant_set_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_participant
    ADD CONSTRAINT deal_participant_set_by_fkey FOREIGN KEY (set_by) REFERENCES public.actor(id);


--
-- Name: deal deal_phase_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal
    ADD CONSTRAINT deal_phase_fkey FOREIGN KEY (phase) REFERENCES public.deal_phase(slug);


--
-- Name: deal_presence_lease deal_presence_lease_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_presence_lease
    ADD CONSTRAINT deal_presence_lease_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actor(id);


--
-- Name: deal_presence_lease deal_presence_lease_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_presence_lease
    ADD CONSTRAINT deal_presence_lease_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deal(id);


--
-- Name: deal_reattach_log deal_reattach_log_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_reattach_log
    ADD CONSTRAINT deal_reattach_log_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deal(id);


--
-- Name: deal_reattach_log deal_reattach_log_from_client_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_reattach_log
    ADD CONSTRAINT deal_reattach_log_from_client_fkey FOREIGN KEY (from_client) REFERENCES public.client(id);


--
-- Name: deal_reattach_log deal_reattach_log_to_client_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_reattach_log
    ADD CONSTRAINT deal_reattach_log_to_client_fkey FOREIGN KEY (to_client) REFERENCES public.client(id);


--
-- Name: deal_review_item deal_review_item_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_review_item
    ADD CONSTRAINT deal_review_item_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deal(id);


--
-- Name: deal_review_item deal_review_item_session_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_review_item
    ADD CONSTRAINT deal_review_item_session_id_fkey FOREIGN KEY (session_id) REFERENCES public.deal_review_session(id);


--
-- Name: deal_review_session deal_review_session_account_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_review_session
    ADD CONSTRAINT deal_review_session_account_client_id_fkey FOREIGN KEY (account_client_id) REFERENCES public.client(id);


--
-- Name: deal_review_session deal_review_session_started_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal_review_session
    ADD CONSTRAINT deal_review_session_started_by_fkey FOREIGN KEY (started_by) REFERENCES public.actor(id);


--
-- Name: deal deal_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deal
    ADD CONSTRAINT deal_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.actor(id);


--
-- Name: defect defect_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.defect
    ADD CONSTRAINT defect_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: diagnostic_route diagnostic_route_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.diagnostic_route
    ADD CONSTRAINT diagnostic_route_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: doc_template doc_template_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doc_template
    ADD CONSTRAINT doc_template_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: doctrine_change_item doctrine_change_item_change_set_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_change_item
    ADD CONSTRAINT doctrine_change_item_change_set_id_fkey FOREIGN KEY (change_set_id) REFERENCES public.doctrine_change_set(id);


--
-- Name: doctrine_change_item doctrine_change_item_section_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_change_item
    ADD CONSTRAINT doctrine_change_item_section_id_fkey FOREIGN KEY (section_id) REFERENCES public.doctrine_section(id);


--
-- Name: doctrine_change_set doctrine_change_set_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_change_set
    ADD CONSTRAINT doctrine_change_set_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actor(id);


--
-- Name: doctrine_change_set doctrine_change_set_gate_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_change_set
    ADD CONSTRAINT doctrine_change_set_gate_fk FOREIGN KEY (gate_run_id) REFERENCES public.doctrine_gate_run(id);


--
-- Name: doctrine_claim doctrine_claim_holder_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_claim
    ADD CONSTRAINT doctrine_claim_holder_actor_id_fkey FOREIGN KEY (holder_actor_id) REFERENCES public.actor(id);


--
-- Name: doctrine_claim doctrine_claim_section_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_claim
    ADD CONSTRAINT doctrine_claim_section_id_fkey FOREIGN KEY (section_id) REFERENCES public.doctrine_section(id);


--
-- Name: doctrine_document doctrine_document_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_document
    ADD CONSTRAINT doctrine_document_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: doctrine_document doctrine_document_owner_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_document
    ADD CONSTRAINT doctrine_document_owner_actor_id_fkey FOREIGN KEY (owner_actor_id) REFERENCES public.actor(id);


--
-- Name: doctrine_document doctrine_document_review_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_document
    ADD CONSTRAINT doctrine_document_review_fk FOREIGN KEY (review_policy_id) REFERENCES public.doctrine_review_policy(id);


--
-- Name: doctrine_edge doctrine_edge_edge_type_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_edge
    ADD CONSTRAINT doctrine_edge_edge_type_fkey FOREIGN KEY (edge_type) REFERENCES public.doctrine_edge_type(edge_type);


--
-- Name: doctrine_edge doctrine_edge_introduced_by_revision_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_edge
    ADD CONSTRAINT doctrine_edge_introduced_by_revision_id_fkey FOREIGN KEY (introduced_by_revision_id) REFERENCES public.doctrine_revision(id);


--
-- Name: doctrine_edge doctrine_edge_retired_by_revision_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_edge
    ADD CONSTRAINT doctrine_edge_retired_by_revision_id_fkey FOREIGN KEY (retired_by_revision_id) REFERENCES public.doctrine_revision(id);


--
-- Name: doctrine_edge doctrine_edge_source_section_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_edge
    ADD CONSTRAINT doctrine_edge_source_section_id_fkey FOREIGN KEY (source_section_id) REFERENCES public.doctrine_section(id);


--
-- Name: doctrine_edge doctrine_edge_target_section_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_edge
    ADD CONSTRAINT doctrine_edge_target_section_id_fkey FOREIGN KEY (target_section_id) REFERENCES public.doctrine_section(id);


--
-- Name: doctrine_gate_finding doctrine_gate_finding_check_key_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_gate_finding
    ADD CONSTRAINT doctrine_gate_finding_check_key_fkey FOREIGN KEY (check_key) REFERENCES public.doctrine_gate_check(check_key);


--
-- Name: doctrine_gate_finding doctrine_gate_finding_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_gate_finding
    ADD CONSTRAINT doctrine_gate_finding_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.doctrine_gate_run(id);


--
-- Name: doctrine_gate_run doctrine_gate_run_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_gate_run
    ADD CONSTRAINT doctrine_gate_run_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actor(id);


--
-- Name: doctrine_gate_run doctrine_gate_run_change_set_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_gate_run
    ADD CONSTRAINT doctrine_gate_run_change_set_id_fkey FOREIGN KEY (change_set_id) REFERENCES public.doctrine_change_set(id);


--
-- Name: doctrine_link doctrine_link_source_section_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_link
    ADD CONSTRAINT doctrine_link_source_section_id_fkey FOREIGN KEY (source_section_id) REFERENCES public.doctrine_section(id);


--
-- Name: doctrine_revision doctrine_revision_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_revision
    ADD CONSTRAINT doctrine_revision_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actor(id);


--
-- Name: doctrine_revision doctrine_revision_change_set_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_revision
    ADD CONSTRAINT doctrine_revision_change_set_id_fkey FOREIGN KEY (change_set_id) REFERENCES public.doctrine_change_set(id);


--
-- Name: doctrine_revision doctrine_revision_parent_revision_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_revision
    ADD CONSTRAINT doctrine_revision_parent_revision_id_fkey FOREIGN KEY (parent_revision_id) REFERENCES public.doctrine_revision(id);


--
-- Name: doctrine_revision doctrine_revision_section_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_revision
    ADD CONSTRAINT doctrine_revision_section_id_fkey FOREIGN KEY (section_id) REFERENCES public.doctrine_section(id);


--
-- Name: doctrine_section doctrine_section_current_rev_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_section
    ADD CONSTRAINT doctrine_section_current_rev_fk FOREIGN KEY (current_revision_id) REFERENCES public.doctrine_revision(id);


--
-- Name: doctrine_section doctrine_section_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_section
    ADD CONSTRAINT doctrine_section_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.doctrine_document(id);


--
-- Name: doctrine_slug_alias doctrine_slug_alias_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_slug_alias
    ADD CONSTRAINT doctrine_slug_alias_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.doctrine_document(id);


--
-- Name: doctrine_snapshot doctrine_snapshot_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.doctrine_snapshot
    ADD CONSTRAINT doctrine_snapshot_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.doctrine_document(id);


--
-- Name: document document_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document
    ADD CONSTRAINT document_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.client(id);


--
-- Name: document document_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document
    ADD CONSTRAINT document_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deal(id);


--
-- Name: document document_pdf_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document
    ADD CONSTRAINT document_pdf_fk FOREIGN KEY (pdf_attachment) REFERENCES public.attachment(id);


--
-- Name: document document_prepared_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document
    ADD CONSTRAINT document_prepared_by_fkey FOREIGN KEY (prepared_by) REFERENCES public.actor(id);


--
-- Name: document document_template_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document
    ADD CONSTRAINT document_template_id_fkey FOREIGN KEY (template_id) REFERENCES public.doc_template(id);


--
-- Name: document document_working_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.document
    ADD CONSTRAINT document_working_fk FOREIGN KEY (working_attachment) REFERENCES public.attachment(id);


--
-- Name: event event_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event
    ADD CONSTRAINT event_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actor(id);


--
-- Name: investigation_branch investigation_branch_adjudicated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.investigation_branch
    ADD CONSTRAINT investigation_branch_adjudicated_by_fkey FOREIGN KEY (adjudicated_by) REFERENCES public.actor(id);


--
-- Name: investigation_branch investigation_branch_opened_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.investigation_branch
    ADD CONSTRAINT investigation_branch_opened_by_fkey FOREIGN KEY (opened_by) REFERENCES public.actor(id);


--
-- Name: investigation_branch investigation_branch_parent_branch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.investigation_branch
    ADD CONSTRAINT investigation_branch_parent_branch_id_fkey FOREIGN KEY (parent_branch_id) REFERENCES public.investigation_branch(id);


--
-- Name: investigation_branch investigation_branch_route_key_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.investigation_branch
    ADD CONSTRAINT investigation_branch_route_key_fkey FOREIGN KEY (route_key) REFERENCES public.diagnostic_route(route_key);


--
-- Name: investigation_branch investigation_branch_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.investigation_branch
    ADD CONSTRAINT investigation_branch_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.investigation_run(id);


--
-- Name: investigation_evidence investigation_evidence_branch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.investigation_evidence
    ADD CONSTRAINT investigation_evidence_branch_id_fkey FOREIGN KEY (branch_id) REFERENCES public.investigation_branch(id);


--
-- Name: investigation_evidence investigation_evidence_contributor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.investigation_evidence
    ADD CONSTRAINT investigation_evidence_contributor_id_fkey FOREIGN KEY (contributor_id) REFERENCES public.actor(id);


--
-- Name: investigation_run investigation_run_owner_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.investigation_run
    ADD CONSTRAINT investigation_run_owner_actor_id_fkey FOREIGN KEY (owner_actor_id) REFERENCES public.actor(id);


--
-- Name: investigation_run investigation_run_signal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.investigation_run
    ADD CONSTRAINT investigation_run_signal_id_fkey FOREIGN KEY (signal_id) REFERENCES public.signal_event(id);


--
-- Name: lead lead_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lead
    ADD CONSTRAINT lead_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.client(id);


--
-- Name: lead lead_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lead
    ADD CONSTRAINT lead_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: lead lead_lane_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lead
    ADD CONSTRAINT lead_lane_fkey FOREIGN KEY (lane) REFERENCES public.lead_lane(slug);


--
-- Name: lead lead_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lead
    ADD CONSTRAINT lead_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES public.actor(id);


--
-- Name: lead lead_party_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lead
    ADD CONSTRAINT lead_party_id_fkey FOREIGN KEY (party_id) REFERENCES public.party(id);


--
-- Name: lead lead_stage_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lead
    ADD CONSTRAINT lead_stage_fkey FOREIGN KEY (stage) REFERENCES public.lead_stage(slug);


--
-- Name: lead lead_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lead
    ADD CONSTRAINT lead_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.actor(id);


--
-- Name: lease lease_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lease
    ADD CONSTRAINT lease_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.client(id);


--
-- Name: lease lease_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lease
    ADD CONSTRAINT lease_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: lease lease_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lease
    ADD CONSTRAINT lease_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deal(id);


--
-- Name: lease lease_doc_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lease
    ADD CONSTRAINT lease_doc_fk FOREIGN KEY (doc_attachment) REFERENCES public.attachment(id);


--
-- Name: lease lease_premises_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lease
    ADD CONSTRAINT lease_premises_id_fkey FOREIGN KEY (premises_id) REFERENCES public.premises(id);


--
-- Name: lease lease_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lease
    ADD CONSTRAINT lease_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.actor(id);


--
-- Name: loop_block loop_block_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.loop_block
    ADD CONSTRAINT loop_block_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: loop_block loop_block_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.loop_block
    ADD CONSTRAINT loop_block_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.actor(id);


--
-- Name: loop_item loop_item_block_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.loop_item
    ADD CONSTRAINT loop_item_block_id_fkey FOREIGN KEY (block_id) REFERENCES public.loop_block(id);


--
-- Name: loop_item loop_item_closed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.loop_item
    ADD CONSTRAINT loop_item_closed_by_fkey FOREIGN KEY (closed_by) REFERENCES public.actor(id);


--
-- Name: loop_item loop_item_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.loop_item
    ADD CONSTRAINT loop_item_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: loop_item loop_item_domain_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.loop_item
    ADD CONSTRAINT loop_item_domain_fkey FOREIGN KEY (domain) REFERENCES public.loop_domain(slug);


--
-- Name: loop_item loop_item_personal_to_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.loop_item
    ADD CONSTRAINT loop_item_personal_to_fkey FOREIGN KEY (personal_to) REFERENCES public.actor(id);


--
-- Name: loop_item loop_item_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.loop_item
    ADD CONSTRAINT loop_item_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.actor(id);


--
-- Name: marketing_subject marketing_subject_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.marketing_subject
    ADD CONSTRAINT marketing_subject_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: media_recommendation media_recommendation_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_recommendation
    ADD CONSTRAINT media_recommendation_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: media_recommendation media_recommendation_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_recommendation
    ADD CONSTRAINT media_recommendation_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.actor(id);


--
-- Name: national_account_owner national_account_owner_account_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.national_account_owner
    ADD CONSTRAINT national_account_owner_account_client_id_fkey FOREIGN KEY (account_client_id) REFERENCES public.client(id);


--
-- Name: national_account_owner national_account_owner_owner_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.national_account_owner
    ADD CONSTRAINT national_account_owner_owner_actor_id_fkey FOREIGN KEY (owner_actor_id) REFERENCES public.actor(id);


--
-- Name: national_account_owner national_account_owner_set_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.national_account_owner
    ADD CONSTRAINT national_account_owner_set_by_fkey FOREIGN KEY (set_by) REFERENCES public.actor(id);


--
-- Name: negotiation_claim negotiation_claim_claim_type_loggable_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.negotiation_claim
    ADD CONSTRAINT negotiation_claim_claim_type_loggable_fkey FOREIGN KEY (claim_type, loggable) REFERENCES public.negotiation_claim_type(slug, derived);


--
-- Name: negotiation_claim negotiation_claim_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.negotiation_claim
    ADD CONSTRAINT negotiation_claim_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: negotiation_claim negotiation_claim_round_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.negotiation_claim
    ADD CONSTRAINT negotiation_claim_round_id_fkey FOREIGN KEY (round_id) REFERENCES public.negotiation_round(id);


--
-- Name: negotiation_round negotiation_round_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.negotiation_round
    ADD CONSTRAINT negotiation_round_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: negotiation_round negotiation_round_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.negotiation_round
    ADD CONSTRAINT negotiation_round_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deal(id);


--
-- Name: negotiation_round negotiation_round_submarket_condition_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.negotiation_round
    ADD CONSTRAINT negotiation_round_submarket_condition_fkey FOREIGN KEY (submarket_condition) REFERENCES public.submarket_condition(slug);


--
-- Name: negotiation_round negotiation_round_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.negotiation_round
    ADD CONSTRAINT negotiation_round_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.actor(id);


--
-- Name: next_action next_action_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.next_action
    ADD CONSTRAINT next_action_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: next_action next_action_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.next_action
    ADD CONSTRAINT next_action_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES public.actor(id);


--
-- Name: next_action next_action_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.next_action
    ADD CONSTRAINT next_action_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.actor(id);


--
-- Name: org_merge_log org_merge_log_from_org_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.org_merge_log
    ADD CONSTRAINT org_merge_log_from_org_fkey FOREIGN KEY (from_org) REFERENCES public.party(id);


--
-- Name: org_merge_log org_merge_log_party_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.org_merge_log
    ADD CONSTRAINT org_merge_log_party_id_fkey FOREIGN KEY (party_id) REFERENCES public.party(id);


--
-- Name: org_merge_log org_merge_log_to_org_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.org_merge_log
    ADD CONSTRAINT org_merge_log_to_org_fkey FOREIGN KEY (to_org) REFERENCES public.party(id);


--
-- Name: party party_contact_state_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.party
    ADD CONSTRAINT party_contact_state_fkey FOREIGN KEY (contact_state) REFERENCES public.contact_state(slug);


--
-- Name: party party_contact_state_set_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.party
    ADD CONSTRAINT party_contact_state_set_by_fkey FOREIGN KEY (contact_state_set_by) REFERENCES public.actor(id);


--
-- Name: party party_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.party
    ADD CONSTRAINT party_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: party_link party_link_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.party_link
    ADD CONSTRAINT party_link_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: party_link party_link_from_party_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.party_link
    ADD CONSTRAINT party_link_from_party_fkey FOREIGN KEY (from_party) REFERENCES public.party(id);


--
-- Name: party_link party_link_kind_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.party_link
    ADD CONSTRAINT party_link_kind_fkey FOREIGN KEY (kind) REFERENCES public.party_link_kind(slug);


--
-- Name: party_link party_link_to_party_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.party_link
    ADD CONSTRAINT party_link_to_party_fkey FOREIGN KEY (to_party) REFERENCES public.party(id);


--
-- Name: party_link party_link_via_party_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.party_link
    ADD CONSTRAINT party_link_via_party_fkey FOREIGN KEY (via_party) REFERENCES public.party(id);


--
-- Name: party party_merged_into_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.party
    ADD CONSTRAINT party_merged_into_fkey FOREIGN KEY (merged_into) REFERENCES public.party(id);


--
-- Name: party party_org_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.party
    ADD CONSTRAINT party_org_id_fkey FOREIGN KEY (org_id) REFERENCES public.party(id);


--
-- Name: party party_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.party
    ADD CONSTRAINT party_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.actor(id);


--
-- Name: placement_measurement placement_measurement_placement_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.placement_measurement
    ADD CONSTRAINT placement_measurement_placement_id_fkey FOREIGN KEY (placement_id) REFERENCES public.placement(id);


--
-- Name: placement_measurement placement_measurement_recorded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.placement_measurement
    ADD CONSTRAINT placement_measurement_recorded_by_fkey FOREIGN KEY (recorded_by) REFERENCES public.actor(id);


--
-- Name: placement_metric placement_metric_placement_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.placement_metric
    ADD CONSTRAINT placement_metric_placement_id_fkey FOREIGN KEY (placement_id) REFERENCES public.placement(id);


--
-- Name: placement placement_piece_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.placement
    ADD CONSTRAINT placement_piece_id_fkey FOREIGN KEY (piece_id) REFERENCES public.content_piece(id);


--
-- Name: premises premises_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.premises
    ADD CONSTRAINT premises_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: premises premises_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.premises
    ADD CONSTRAINT premises_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deal(id);


--
-- Name: premises_space premises_space_premises_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.premises_space
    ADD CONSTRAINT premises_space_premises_id_fkey FOREIGN KEY (premises_id) REFERENCES public.premises(id);


--
-- Name: premises_space premises_space_space_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.premises_space
    ADD CONSTRAINT premises_space_space_id_fkey FOREIGN KEY (space_id) REFERENCES public.space(id);


--
-- Name: candidate_pool prospect_pool_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_pool
    ADD CONSTRAINT prospect_pool_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: candidate_pool prospect_pool_promoted_lead_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_pool
    ADD CONSTRAINT prospect_pool_promoted_lead_id_fkey FOREIGN KEY (promoted_lead_id) REFERENCES public.lead(id);


--
-- Name: candidate_pool prospect_pool_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_pool
    ADD CONSTRAINT prospect_pool_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.actor(id);


--
-- Name: record_flag record_flag_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.record_flag
    ADD CONSTRAINT record_flag_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: registration registration_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registration
    ADD CONSTRAINT registration_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: registration registration_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registration
    ADD CONSTRAINT registration_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deal(id);


--
-- Name: registration registration_doc_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registration
    ADD CONSTRAINT registration_doc_fk FOREIGN KEY (doc_attachment) REFERENCES public.attachment(id);


--
-- Name: registration registration_premises_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registration
    ADD CONSTRAINT registration_premises_id_fkey FOREIGN KEY (premises_id) REFERENCES public.premises(id);


--
-- Name: registration registration_registered_with_party_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.registration
    ADD CONSTRAINT registration_registered_with_party_fkey FOREIGN KEY (registered_with_party) REFERENCES public.party(id);


--
-- Name: rule rule_activated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule
    ADD CONSTRAINT rule_activated_by_fkey FOREIGN KEY (activated_by) REFERENCES public.actor(id);


--
-- Name: rule rule_personal_to_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule
    ADD CONSTRAINT rule_personal_to_fkey FOREIGN KEY (personal_to) REFERENCES public.actor(id);


--
-- Name: rule rule_supersedes_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule
    ADD CONSTRAINT rule_supersedes_fkey FOREIGN KEY (supersedes) REFERENCES public.rule(id);


--
-- Name: rule rule_taught_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.rule
    ADD CONSTRAINT rule_taught_by_fkey FOREIGN KEY (taught_by) REFERENCES public.actor(id);


--
-- Name: search_candidate search_candidate_premises_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.search_candidate
    ADD CONSTRAINT search_candidate_premises_id_fkey FOREIGN KEY (premises_id) REFERENCES public.premises(id);


--
-- Name: search_candidate search_candidate_search_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.search_candidate
    ADD CONSTRAINT search_candidate_search_id_fkey FOREIGN KEY (search_id) REFERENCES public.space_search(id);


--
-- Name: signal_event signal_event_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_event
    ADD CONSTRAINT signal_event_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: source_capture source_capture_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_capture
    ADD CONSTRAINT source_capture_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: source_capture source_capture_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_capture
    ADD CONSTRAINT source_capture_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.actor(id);


--
-- Name: space space_building_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.space
    ADD CONSTRAINT space_building_id_fkey FOREIGN KEY (building_id) REFERENCES public.building(id);


--
-- Name: space space_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.space
    ADD CONSTRAINT space_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: space_search space_search_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.space_search
    ADD CONSTRAINT space_search_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.client(id);


--
-- Name: space_search space_search_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.space_search
    ADD CONSTRAINT space_search_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: space_search space_search_deal_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.space_search
    ADD CONSTRAINT space_search_deal_id_fkey FOREIGN KEY (deal_id) REFERENCES public.deal(id);


--
-- Name: space space_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.space
    ADD CONSTRAINT space_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.actor(id);


--
-- Name: system_config system_config_updated_by_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_config
    ADD CONSTRAINT system_config_updated_by_fk FOREIGN KEY (updated_by) REFERENCES public.actor(id);


--
-- Name: tool_call tool_call_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tool_call
    ADD CONSTRAINT tool_call_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actor(id);


--
-- Name: tool_read_call tool_read_call_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tool_read_call
    ADD CONSTRAINT tool_read_call_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.actor(id);


--
-- Name: vendor vendor_category_slug_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vendor
    ADD CONSTRAINT vendor_category_slug_fkey FOREIGN KEY (category_slug) REFERENCES public.vendor_category(slug);


--
-- Name: vendor vendor_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vendor
    ADD CONSTRAINT vendor_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.actor(id);


--
-- Name: vendor vendor_disposition_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vendor
    ADD CONSTRAINT vendor_disposition_fkey FOREIGN KEY (disposition) REFERENCES public.vendor_disposition(slug);


--
-- Name: vendor vendor_merged_into_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vendor
    ADD CONSTRAINT vendor_merged_into_fkey FOREIGN KEY (merged_into) REFERENCES public.vendor(id);


--
-- Name: vendor vendor_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vendor
    ADD CONSTRAINT vendor_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES public.actor(id);


--
-- Name: vendor vendor_party_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vendor
    ADD CONSTRAINT vendor_party_id_fkey FOREIGN KEY (party_id) REFERENCES public.party(id);


--
-- Name: vendor vendor_relationship_level_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vendor
    ADD CONSTRAINT vendor_relationship_level_fkey FOREIGN KEY (relationship_level) REFERENCES public.vendor_relationship_level(level);


--
-- Name: vendor vendor_stage_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vendor
    ADD CONSTRAINT vendor_stage_fkey FOREIGN KEY (stage) REFERENCES public.vendor_stage(slug);


--
-- Name: vendor vendor_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vendor
    ADD CONSTRAINT vendor_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.actor(id);


--
-- PostgreSQL database dump complete
--


--
-- CARR GRANTS (bin/schema-snapshot.sh) — not produced by pg_dump.
--
-- The app roles' privileges, read from production's catalogs. Without them a
-- database built from this file has the roles holding nothing, and CI's
-- migration class answers has_table_privilege() with false for everything —
-- the 2026-08-14 gap. Grantees are scoped to the preamble's four roles (plus
-- neondb_owner, membership bundles only) so no other principal's ACLs can
-- enter the tree. Shapes pinned by tools/test-schema-snapshot-grants.py.
--

grant usage on schema ops to carr_jobs;
grant usage on schema ops to carr_reader;
grant usage on schema ops to carr_writer;
grant usage on schema public to carr_exporter;
grant usage on schema public to carr_jobs;
grant usage on schema public to carr_reader;
grant usage on schema public to carr_writer;
grant insert on table ops.deployment to carr_jobs;
grant select on table ops.deployment to carr_reader;
grant insert, select, update on table ops.deployment to carr_writer;
grant insert, select on table ops.incident to carr_jobs;
grant select on table ops.incident to carr_reader;
grant insert, select, update on table ops.incident to carr_writer;
grant insert, select on table ops.incident_fact to carr_jobs;
grant select on table ops.incident_fact to carr_reader;
grant insert, select, update on table ops.incident_fact to carr_writer;
grant select on table ops.incident_hypothesis to carr_jobs;
grant select on table ops.incident_hypothesis to carr_reader;
grant insert, select, update on table ops.incident_hypothesis to carr_writer;
grant insert, select on table ops.incident_link to carr_jobs;
grant select on table ops.incident_link to carr_reader;
grant insert, select, update on table ops.incident_link to carr_writer;
grant select on table ops.incident_service to carr_jobs;
grant select on table ops.incident_service to carr_reader;
grant insert, select, update on table ops.incident_service to carr_writer;
grant insert, select on table ops.run to carr_jobs;
grant select on table ops.run to carr_reader;
grant insert, select on table ops.run to carr_writer;
grant insert, select, update on table ops.service to carr_exporter;
grant select on table ops.service to carr_jobs;
grant select on table ops.service to carr_reader;
grant insert, select, update on table ops.service to carr_writer;
grant insert, select, update on table ops.service_dependency to carr_exporter;
grant select on table ops.service_dependency to carr_reader;
grant insert, select, update on table ops.service_dependency to carr_writer;
grant insert, select, update on table ops.service_environment to carr_exporter;
grant select on table ops.service_environment to carr_jobs;
grant select on table ops.service_environment to carr_reader;
grant insert, select, update on table ops.service_environment to carr_writer;
grant insert, select on table ops.settings_change to carr_jobs;
grant select on table ops.settings_change to carr_reader;
grant insert, select on table ops.settings_change to carr_writer;
grant select on table ops.v_check_run to carr_reader;
grant select on table ops.v_check_run to carr_writer;
grant select on table ops.v_job_run to carr_reader;
grant select on table ops.v_job_run to carr_writer;
grant select on table ops.v_service_environment_health to carr_reader;
grant select on table ops.v_service_environment_health to carr_writer;
grant select on table ops.v_trace to carr_reader;
grant select on table ops.v_trace to carr_writer;
grant select on table ops.work_request to carr_reader;
grant insert, select, update on table ops.work_request to carr_writer;
grant insert, select, update on table public.activity to carr_writer;
grant insert, select, update on table public.actor to carr_writer;
grant insert, select, update on table public.actor_profile to carr_writer;
grant insert, select, update on table public.agreement to carr_writer;
grant insert, select, update on table public.ammo_item to carr_writer;
grant insert, select, update on table public.attachment to carr_writer;
grant select on table public.availability to carr_jobs;
grant insert, select, update on table public.availability to carr_writer;
grant insert, select, update on table public.building to carr_writer;
grant insert, select, update on table public.building_ownership to carr_writer;
grant select on table public.cadence_rule to carr_jobs;
grant insert, select, update on table public.cadence_rule to carr_writer;
grant insert, select, update on table public.campaign to carr_writer;
grant insert, select, update on table public.candidate_pool to carr_writer;
grant insert, select, update on table public.capture_candidate to carr_writer;
grant insert, select, update on table public.capture_post_call_action to carr_writer;
grant insert, select, update on table public.capture_post_call_candidate to carr_writer;
grant insert, select, update on table public.capture_post_call_report to carr_writer;
grant insert, select, update on table public.capture_session to carr_writer;
grant insert, select, update on table public.client to carr_writer;
grant insert, select, update on table public.client_status to carr_writer;
grant insert, select, update on table public.client_type to carr_writer;
grant select on table public.code_subject to carr_exporter;
grant select on table public.code_subject to carr_reader;
grant insert, select on table public.code_subject to carr_writer;
grant insert, select, update on table public.commission to carr_writer;
grant insert, select, update on table public.commission_allocation to carr_writer;
grant insert, select, update on table public.comp to carr_writer;
grant insert, select, update on table public.content_piece to carr_jobs;
grant insert, select, update on table public.content_piece to carr_writer;
grant insert, select, update on table public.critical_date to carr_writer;
grant insert, select, update on table public.deal to carr_writer;
grant insert, select, update on table public.deal_conflict to carr_writer;
grant insert, select, update on table public.deal_market_assignment to carr_writer;
grant insert, select on table public.deal_note to carr_writer;
grant insert, select, update on table public.deal_participant to carr_writer;
grant insert, select, update on table public.deal_phase to carr_writer;
grant insert, select, update on table public.deal_presence_lease to carr_writer;
grant insert, select, update on table public.deal_review_item to carr_writer;
grant insert, select, update on table public.deal_review_session to carr_writer;
grant select on table public.defect to carr_exporter;
grant select on table public.defect to carr_reader;
grant insert, select on table public.defect to carr_writer;
grant select on table public.diagnostic_route to carr_reader;
grant select on table public.diagnostic_route to carr_writer;
grant insert, select, update on table public.doc_template to carr_writer;
grant insert, select, update on table public.doctrine_change_item to carr_writer;
grant insert, select, update on table public.doctrine_change_set to carr_writer;
grant delete, insert, select, update on table public.doctrine_claim to carr_writer;
grant select on table public.doctrine_document to carr_exporter;
grant select on table public.doctrine_document to carr_reader;
grant insert, select, update on table public.doctrine_document to carr_writer;
grant select on table public.doctrine_edge to carr_exporter;
grant select on table public.doctrine_edge to carr_reader;
grant insert, select, update on table public.doctrine_edge to carr_writer;
grant select on table public.doctrine_edge_type to carr_exporter;
grant select on table public.doctrine_edge_type to carr_reader;
grant select on table public.doctrine_edge_type to carr_writer;
grant select on table public.doctrine_gate_check to carr_exporter;
grant select on table public.doctrine_gate_check to carr_reader;
grant insert, select, update on table public.doctrine_gate_check to carr_writer;
grant select on table public.doctrine_gate_finding to carr_exporter;
grant insert, select, update on table public.doctrine_gate_finding to carr_writer;
grant select on table public.doctrine_gate_run to carr_exporter;
grant insert, select, update on table public.doctrine_gate_run to carr_writer;
grant select on table public.doctrine_link to carr_exporter;
grant select on table public.doctrine_link to carr_reader;
grant delete, insert, select, update on table public.doctrine_link to carr_writer;
grant select on table public.doctrine_meta to carr_exporter;
grant select on table public.doctrine_meta to carr_reader;
grant insert, select, update on table public.doctrine_meta to carr_writer;
grant select on table public.doctrine_migration_batch to carr_exporter;
grant insert, select, update on table public.doctrine_migration_batch to carr_writer;
grant select on table public.doctrine_review_policy to carr_exporter;
grant select on table public.doctrine_review_policy to carr_reader;
grant insert, select, update on table public.doctrine_review_policy to carr_writer;
grant select on table public.doctrine_revision to carr_exporter;
grant select on table public.doctrine_revision to carr_reader;
grant insert, select, update on table public.doctrine_revision to carr_writer;
grant select on table public.doctrine_section to carr_exporter;
grant select on table public.doctrine_section to carr_reader;
grant insert, select, update on table public.doctrine_section to carr_writer;
grant select on table public.doctrine_slug_alias to carr_reader;
grant insert, select, update on table public.doctrine_slug_alias to carr_writer;
grant select on table public.doctrine_snapshot to carr_exporter;
grant select on table public.doctrine_snapshot to carr_reader;
grant insert, select, update on table public.doctrine_snapshot to carr_writer;
grant select on table public.document to carr_jobs;
grant insert, select, update on table public.document to carr_writer;
grant insert, select on table public.event to carr_jobs;
grant insert, select, update on table public.event to carr_writer;
grant insert, select, update on table public.experiment to carr_writer;
grant insert, select on table public.export_run to carr_exporter;
grant insert, select, update on table public.export_run to carr_writer;
grant insert, select on table public.growth_snapshot to carr_jobs;
grant select on table public.growth_snapshot to carr_reader;
grant select on table public.ingest_inbox to carr_jobs;
grant insert, select, update on table public.ingest_inbox to carr_writer;
grant insert, select, update on table public.investigation_branch to carr_writer;
grant insert, select, update on table public.investigation_evidence to carr_writer;
grant insert, select, update on table public.investigation_run to carr_writer;
grant insert, select, update on table public.lead to carr_writer;
grant insert, select, update on table public.lead_lane to carr_writer;
grant insert, select, update on table public.lead_stage to carr_writer;
grant insert, select, update on table public.lease to carr_writer;
grant select on table public.loop_block to carr_exporter;
grant select on table public.loop_block to carr_reader;
grant insert, select, update on table public.loop_block to carr_writer;
grant select on table public.loop_domain to carr_exporter;
grant select on table public.loop_domain to carr_reader;
grant select on table public.loop_domain to carr_writer;
grant select on table public.loop_item to carr_exporter;
grant select on table public.loop_item to carr_reader;
grant insert, select, update on table public.loop_item to carr_writer;
grant select on table public.marketing_subject to carr_exporter;
grant select on table public.marketing_subject to carr_reader;
grant insert, select, update on table public.marketing_subject to carr_writer;
grant select on table public.media_recommendation to carr_exporter;
grant select on table public.media_recommendation to carr_reader;
grant insert, select, update on table public.media_recommendation to carr_writer;
grant insert, select, update on table public.national_account_owner to carr_writer;
grant select on table public.negotiation_claim to carr_reader;
grant insert, select, update on table public.negotiation_claim to carr_writer;
grant select on table public.negotiation_claim_type to carr_reader;
grant select on table public.negotiation_claim_type to carr_writer;
grant insert, select, update on table public.negotiation_round to carr_writer;
grant insert, select on table public.next_action to carr_jobs;
grant insert, select, update on table public.next_action to carr_writer;
grant insert, select, update on table public.parcel to carr_writer;
grant insert, select, update on table public.party to carr_writer;
grant insert, select, update on table public.party_link to carr_writer;
grant select on table public.party_link_kind to carr_writer;
grant insert, select, update on table public.placement to carr_jobs;
grant insert, select, update on table public.placement to carr_writer;
grant select on table public.placement_measurement to carr_exporter;
grant insert, select on table public.placement_measurement to carr_jobs;
grant select on table public.placement_measurement to carr_reader;
grant insert, select on table public.placement_measurement to carr_writer;
grant insert, select, update on table public.placement_metric to carr_jobs;
grant insert, select, update on table public.placement_metric to carr_writer;
grant insert, select, update on table public.premises to carr_writer;
grant insert, select, update on table public.premises_space to carr_writer;
grant insert, select, update on table public.record_flag to carr_writer;
grant insert, select, update on table public.record_source to carr_writer;
grant select, usage on sequence public.ref_client_seq to carr_writer;
grant select, usage on sequence public.ref_lead_seq to carr_writer;
grant select, usage on sequence public.ref_vendor_seq to carr_writer;
grant insert, select, update on table public.registration to carr_writer;
grant insert, select, update on table public.rule to carr_writer;
grant insert, select, update on table public.schema_migrations to carr_writer;
grant insert, select, update on table public.search_candidate to carr_writer;
grant insert, select, update on table public.sensitive_blob to carr_writer;
grant insert, select, update on table public.signal_event to carr_writer;
grant insert, select, update on table public.source_capture to carr_writer;
grant insert, select, update on table public.space to carr_writer;
grant select on table public.space_search to carr_jobs;
grant insert, select, update on table public.space_search to carr_writer;
grant select on table public.submarket_condition to carr_reader;
grant select on table public.submarket_condition to carr_writer;
grant select on table public.system_config to carr_exporter;
grant insert, select, update on table public.system_config to carr_writer;
grant insert, select, update on table public.tool_call to carr_writer;
grant select on table public.tool_read_call to carr_exporter;
grant select on table public.tool_read_call to carr_reader;
grant insert on table public.tool_read_call to carr_writer;
grant select on table public.v_campaign_scorecard to carr_exporter;
grant select on table public.v_campaign_scorecard to carr_reader;
grant select on table public.v_campaign_scorecard to carr_writer;
grant select on table public.v_capture_candidate_queue to carr_reader;
grant select on table public.v_capture_session_status to carr_reader;
grant select on table public.v_claim_card to carr_exporter;
grant select on table public.v_claim_card to carr_jobs;
grant select on table public.v_claim_card to carr_reader;
grant select on table public.v_claim_card to carr_writer;
grant select on table public.v_code_finding to carr_exporter;
grant select on table public.v_code_finding to carr_reader;
grant select on table public.v_code_finding to carr_writer;
grant select on table public.v_compiled_rules to carr_jobs;
grant select on table public.v_compiled_rules to carr_reader;
grant select on table public.v_counterparty_bluff to carr_reader;
grant select on table public.v_counterparty_bluff to carr_writer;
grant select on table public.v_counterparty_history to carr_reader;
grant select on table public.v_counterparty_history to carr_writer;
grant select on table public.v_counterparty_scorecard to carr_reader;
grant select on table public.v_counterparty_scorecard to carr_writer;
grant select on table public.v_deal_board to carr_reader;
grant select on table public.v_deal_reconciliation_read to carr_reader;
grant select on table public.v_deal_room_account to carr_reader;
grant select on table public.v_deal_room_account to carr_writer;
grant select on table public.v_deal_room_action to carr_reader;
grant select on table public.v_deal_room_action to carr_writer;
grant select on table public.v_deal_room_activity to carr_reader;
grant select on table public.v_deal_room_activity to carr_writer;
grant select on table public.v_deal_room_board to carr_reader;
grant select on table public.v_deal_room_board to carr_writer;
grant select on table public.v_deal_room_critical_date to carr_reader;
grant select on table public.v_deal_room_deal to carr_reader;
grant select on table public.v_deal_room_deal to carr_writer;
grant select on table public.v_deal_room_document to carr_reader;
grant select on table public.v_deal_room_document to carr_writer;
grant select on table public.v_deal_room_event to carr_reader;
grant select on table public.v_deal_room_negotiation to carr_reader;
grant select on table public.v_deal_room_negotiation to carr_writer;
grant select on table public.v_deal_room_note to carr_reader;
grant select on table public.v_deal_room_participant to carr_reader;
grant select on table public.v_deal_room_participant to carr_writer;
grant select on table public.v_deal_room_premises to carr_reader;
grant select on table public.v_deal_room_premises to carr_writer;
grant select on table public.v_deal_room_presence to carr_reader;
grant select on table public.v_deal_room_session to carr_reader;
grant select on table public.v_deal_room_session to carr_writer;
grant select on table public.v_decision_entry to carr_exporter;
grant select on table public.v_decision_entry to carr_reader;
grant select on table public.v_decision_entry to carr_writer;
grant select on table public.v_defect to carr_exporter;
grant select on table public.v_defect to carr_reader;
grant select on table public.v_defect to carr_writer;
grant select on table public.v_defect_class to carr_exporter;
grant select on table public.v_defect_class to carr_reader;
grant select on table public.v_defect_class to carr_writer;
grant select on table public.v_expired_verification to carr_jobs;
grant select on table public.v_expired_verification to carr_reader;
grant select on table public.v_expired_verification to carr_writer;
grant select on table public.v_export_clients to carr_reader;
grant select on table public.v_export_clients_active to carr_reader;
grant select on table public.v_export_deals to carr_reader;
grant select on table public.v_export_dossier_analysis to carr_exporter;
grant select on table public.v_export_dossier_subject to carr_exporter;
grant select on table public.v_export_leads to carr_reader;
grant select on table public.v_export_loops to carr_exporter;
grant select on table public.v_export_loops_closed to carr_exporter;
grant select on table public.v_export_pool to carr_exporter;
grant select on table public.v_export_pool_all to carr_exporter;
grant select on table public.v_export_source_captures to carr_reader;
grant select on table public.v_export_source_captures to carr_writer;
grant select on table public.v_export_vendors to carr_reader;
grant select on table public.v_field_history to carr_exporter;
grant select on table public.v_field_history to carr_reader;
grant select on table public.v_field_history to carr_writer;
grant select on table public.v_growth_slope to carr_jobs;
grant select on table public.v_growth_slope to carr_reader;
grant select on table public.v_ingest_backlog to carr_jobs;
grant select on table public.v_ingest_backlog to carr_reader;
grant select on table public.v_integrity_digest to carr_reader;
grant select on table public.v_investigation to carr_reader;
grant select on table public.v_investigation to carr_writer;
grant select on table public.v_last_touch to carr_reader;
grant select on table public.v_lead_client_best to carr_exporter;
grant select on table public.v_lead_client_best to carr_reader;
grant select on table public.v_lead_client_best to carr_writer;
grant select on table public.v_lead_client_link to carr_exporter;
grant select on table public.v_lead_client_link to carr_reader;
grant select on table public.v_lead_client_link to carr_writer;
grant select on table public.v_lead_hot to carr_reader;
grant select on table public.v_loop_no_blocker to carr_exporter;
grant select on table public.v_loop_no_blocker to carr_reader;
grant select on table public.v_loop_no_blocker to carr_writer;
grant select on table public.v_loop_promotion_due to carr_reader;
grant select on table public.v_loop_promotion_due to carr_writer;
grant select on table public.v_loops to carr_reader;
grant select on table public.v_loops to carr_writer;
grant select on table public.v_marketing_measurement_coverage to carr_exporter;
grant select on table public.v_marketing_measurement_coverage to carr_reader;
grant select on table public.v_marketing_measurement_coverage to carr_writer;
grant select on table public.v_md_ledger_entry to carr_exporter;
grant select on table public.v_md_ledger_entry to carr_reader;
grant select on table public.v_md_ledger_entry to carr_writer;
grant select on table public.v_media_recommendation to carr_exporter;
grant select on table public.v_media_recommendation to carr_reader;
grant select on table public.v_media_recommendation to carr_writer;
grant select on table public.v_negotiation_deal to carr_reader;
grant select on table public.v_negotiation_deal to carr_writer;
grant select on table public.v_negotiation_position to carr_reader;
grant select on table public.v_negotiation_position to carr_writer;
grant select on table public.v_party_graph to carr_reader;
grant select on table public.v_party_graph to carr_writer;
grant select on table public.v_placement_measurement to carr_exporter;
grant select on table public.v_placement_measurement to carr_reader;
grant select on table public.v_placement_measurement to carr_writer;
grant select on table public.v_placement_metric_latest to carr_exporter;
grant select on table public.v_placement_metric_latest to carr_reader;
grant select on table public.v_placement_metric_latest to carr_writer;
grant select on table public.v_pool to carr_reader;
grant select on table public.v_precedent to carr_exporter;
grant select on table public.v_precedent to carr_reader;
grant select on table public.v_precedent to carr_writer;
grant select on table public.v_rate_normalized to carr_reader;
grant select on table public.v_record_flag_subject to carr_exporter;
grant select on table public.v_record_flag_subject to carr_reader;
grant select on table public.v_record_flag_subject to carr_writer;
grant select on table public.v_ref_index to carr_jobs;
grant select on table public.v_ref_index to carr_reader;
grant select on table public.v_ref_index to carr_writer;
grant select on table public.v_role_timeouts to carr_reader;
grant select on table public.v_schema_ledger to carr_exporter;
grant select on table public.v_schema_ledger to carr_reader;
grant select on table public.v_signal_queue to carr_reader;
grant select on table public.v_signal_queue to carr_writer;
grant select on table public.v_source_attribution to carr_reader;
grant select on table public.v_source_attribution to carr_writer;
grant select on table public.v_stale_records to carr_reader;
grant select on table public.v_subject_timeline to carr_reader;
grant select on table public.v_today_triage to carr_reader;
grant select on table public.v_vendor_level_suggestion to carr_reader;
grant select on table public.v_vendor_needs_type to carr_reader;
grant insert, select, update on table public.vendor to carr_writer;
grant select on table public.vendor_category to carr_writer;
grant select on table public.vendor_disposition to carr_writer;
grant select on table public.vendor_relationship_level to carr_writer;
grant insert, select, update on table public.vendor_stage to carr_writer;
grant update (state, next_action, monitoring_until, recovery_evidence_ref, observed_at, expires_at) on table ops.incident to carr_jobs;
grant select (id, slug, kind, display_name) on table public.actor to carr_jobs;
grant select (id, slug) on table public.actor to carr_reader;
grant select (id, address, city, state, name, sub_type) on table public.building to carr_jobs;
grant select (id, roster_ref, party_id, status, owner_id) on table public.client to carr_jobs;
grant select (id, client_id, name) on table public.deal to carr_jobs;
grant select (deal_id, actor_id, role, to_at) on table public.deal_participant to carr_jobs;
grant select (id, registry_ref, party_id, est_lease_event, client_id, owner_id) on table public.lead to carr_jobs;
grant select (slug, side) on table public.participant_role to carr_writer;
grant select (id, name) on table public.party to carr_jobs;
grant select (id, building_id, suite, area_amount) on table public.space to carr_jobs;
grant select (key, value) on table public.system_config to carr_jobs;
grant select (id, vendor_ref, party_id, owner_id) on table public.vendor to carr_jobs;
grant execute on function public.capture_call_context(requested_deal_ids uuid[]) to carr_reader;
grant execute on function public.capture_call_context(requested_deal_ids uuid[]) to carr_writer;
grant execute on function public.state_as_of(p_type text, p_id uuid, p_at timestamp with time zone) to carr_exporter;
grant execute on function public.state_as_of(p_type text, p_id uuid, p_at timestamp with time zone) to carr_reader;
grant execute on function public.state_as_of(p_type text, p_id uuid, p_at timestamp with time zone) to carr_writer;
grant carr_exporter to neondb_owner;
grant carr_exporter to neondb_owner;
grant carr_jobs to neondb_owner;
grant carr_reader to carr_exporter;
grant carr_reader to neondb_owner;
grant carr_reader to neondb_owner;
grant carr_writer to neondb_owner;
grant carr_writer to neondb_owner;
--
-- PostgreSQL database dump
--



SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Data for Name: schema_migrations; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.schema_migrations (filename, sha256, applied_at) FROM stdin;
0002_seed.sql	1e1e10193569937cd55cae65a4cf5266123c26fe4a5ecf329458124f46189317	2026-07-30 16:38:47.602591+00
0003_wave1_registry_columns.sql	5f3e85261ac5b193527d1b4726f6acb17821bbc800a953185a5df638549f6587	2026-07-30 16:50:32.216051+00
0004_roles_views.sql	f7a7aa660e82a26f90a83aebc197054db68cf6d252edf6874d8f0c6008590cac	2026-07-30 16:50:32.41411+00
0005_role_admin_grants.sql	e5dd8791977f103df20087ac73e28d310eea8f80ed91a451ad52d7d13de9eceb	2026-07-30 16:50:32.772307+00
0006_exporter_role.sql	2ac146111970f41434056a8371fd298438e12c25dbc0a09ae13dbb40609fafb1	2026-07-30 19:19:14.747227+00
0007_owner_columns.sql	2e8a4867dd0750d55ef008c52a8cb17716961e47eb3c6e9623f1f34f2c87aaeb	2026-07-30 19:31:51.770968+00
0008_client_merged_into.sql	1eb56e4b518ffee5fe49a5140a3444cc639547675e3c1bd08c3c1ff2c9262284	2026-07-30 19:40:17.318972+00
0009_fidelity_columns.sql	01c8dd79c74e20907ab9bc16b6189d36c82cb4e77551de8b7ef92b2c63a9b0ae	2026-07-30 19:45:24.023703+00
0010_verbatim_labels.sql	73436b4640496414af84f0af212f1b7b65704d5fcf13b4ccd5c7634d99638246	2026-07-30 19:50:36.856831+00
0011_roster_notes.sql	27d534cfb082deb4b5d61c1a7847eeb24f9f5a8fc97f0d952e20b4027a3ecbb7	2026-07-30 20:06:35.462919+00
0012_specialty_label.sql	9a7b51d9f99c9bf6fb3b52c1c6547586713027443d8c167162efbd6c33c9756d	2026-07-30 20:18:06.960754+00
0013_active_book_derived.sql	9a8730bb933ee1221eb6b0d22f2619ca84ce4b11b1583258a92206562f71ce53	2026-07-31 16:23:37.395658+00
0014_active_flags_and_semantics.sql	2830a82f27b7ae76821a823eccfb14dad2e1001f7ad6cf6301023808209e1d31	2026-07-31 16:23:37.395658+00
0015_compiled_rules_view.sql	0fa7152fcac1fd7ccbd8f43d3c528e1a704656941c1704bd530cc3c7ad3799e7	2026-07-31 16:23:37.395658+00
0016_ref_index_resolver_view.sql	d821701d1b6eceaaea7005519b774ab73579b19ed6ca84ff7fefbcb63e001d2a	2026-07-31 16:23:37.395658+00
0017_vocab_ref_tables.sql	714af9bfc3b0bba4943fc224e74435de84ca5ff8f5c64bb3517c81f9f05fbe0a	2026-07-31 16:23:49.074943+00
0018_client_status_rationalization.sql	3ee4a58a4c41340d060558cd4becb5d194cd897262ffeb5c77cb9399cec0aaea	2026-07-31 17:33:03.520177+00
0019_wave2_machinery_tables.sql	95de20717cecd1482cfff618f64da017fd8639c116e84760bf02ed600596c071	2026-07-31 18:09:16.884822+00
0020_party_link_kind_vocabulary.sql	bfcfdc26f30f0556139f27d5a727f4a057c2c1c169e1c109a9d22a5c51845d4d	2026-07-31 19:22:18.658623+00
0021_jobs_role_and_cadence_inputs.sql	447d36c4058ced54a45cdcc1a8b85c42774c8dc9e90590ca0cb597a987dea5a2	2026-07-31 20:51:47.490444+00
0022_purchase_price_basis.sql	356c15b8920b45b85d845865a41d5852e0ad3bf3081578fa88e52ebe1120e312	2026-08-01 00:00:04.371229+00
0023_prospect_pool.sql	22d5f8577f310c6373dc2d4789d63b30bb8cd71bc50e713adad38e843e477ea3	2026-08-01 00:35:39.405736+00
0024_loop_item.sql	590f1e9bbe67f6d973eb50fd8079bcaec73d866996f1ee0691a15ceef43fd9b2	2026-08-01 01:17:34.325834+00
0025_exporter_grants_pool_all.sql	f1742ee05d20ca4ea534c8bcbe3d82c295845ca4512250013cc8e4f9c252a81a	2026-08-01 01:21:36.25126+00
0026_counterparty_and_attribution_views.sql	89f8d843bcf0974a15c25a0ba8cff95a1d38c0118be868b1c344010eaee39155	2026-08-01 03:06:04.71739+00
0027_review_fixes_graph_views.sql	33ec2d144e84a14ed8e8683a85f99aac25dd4f664a4f1cff874f50a529b9d91e	2026-08-01 03:26:52.90654+00
0028_dossier_analysis.sql	47191484c11885b31cc099ab365f72f6ebe58d8f94c82244f3d10ef3061fa482	2026-08-01 15:13:04.526926+00
0029_compiled_rules_scope.sql	5492ce8287fa6b866a22232f70e0cfc87e2a5df0de3d74edaa5640894ddc09cb	2026-08-01 15:13:04.667501+00
0030_md_ledger_render.sql	c01f2f50314658d4fd7abce2b8f4ab3dc712863620d115a1649124cf3e6af6f4	2026-08-01 15:13:05.006783+00
0031_decision_events_and_idea_loops.sql	595be5d13f1c60af0ee268e709bc700612318cdd5be65126929a406b15d9b717	2026-08-01 16:18:58.308797+00
0032_audit_repairs.sql	774e2c39ba1ad2a90d5ec174347a48b3a5a65d86d35610275a563970296bce9a	2026-08-02 03:41:09.549698+00
0033_deal_inherits_client_touch.sql	437ae5c1f8b65a1e6eb120b16f76c04eebdf039e3cb366eced725fc0e2ae26db	2026-08-02 14:02:10.36658+00
0034_coverage_and_denominators.sql	2e23a8fa416bef2726a5acd1f8e4bdae941b4e2a6fde54a8931a476dc489c452	2026-08-02 14:02:10.567596+00
0035_phase_order_legal_before_dd.sql	dc30d8840d49e65c0a95ce1ddb4c14644498762ba174c39efd53b819f69bf3a0	2026-08-02 14:02:10.714312+00
0036_sonography_lead_owner_dell.sql	b14c93dc12e2eab4dd782cb6b40b7204fa442add60a24a1cb2acec843c84a064	2026-08-02 14:16:13.382326+00
0037_write_provenance.sql	591ec419b0ae401bb001ff53d0039dabb766b52986965b6fc1103eb237c3b86a	2026-08-02 14:43:05.268362+00
0038_loop_domain.sql	a813c2da68e02b3759683e56cd4f19664d33cdbe22c5e03adeb7907bb3a587bb	2026-08-02 15:31:09.302516+00
0039_loop_domain_six.sql	5e982489d3e5f281a83bd27328b00d3ad80809122ee64817c6d85376d41d96c8	2026-08-02 16:00:00.588915+00
0040_loop_domain_intro_rule.sql	33a27592fafafeb1f645bbcd8f08c70b3ea5f54430ce55e7bc0c14894d6e879d	2026-08-02 16:07:15.567276+00
0041_classify_loops.sql	485d9bc2310d702391f882d159a7cef18d16d165d9ae35297bf9112a004117d2	2026-08-02 16:28:04.97472+00
0042_export_loops_domain.sql	4a34ca1cc23e34401f16d671072e7172674c6561c9b736e001703ea0f4e75b07	2026-08-02 16:31:54.432914+00
0043_loop_prose_cap.sql	f68b85a77bf4f58d966840f1ea041b8295f9ddc1ac8b899dee313e08c284f055	2026-08-02 16:36:31.994004+00
0044_fix_registry_pointers.sql	13ada7eafe4620feb8fc145fa1f5ac5ec509f843289cff3dbd1462a538587732	2026-08-02 16:48:19.001992+00
0045_drip_conflict.sql	41fd3d423d3adb19700a3b2337b7b569d0ec1e7a60cd9975c5c0b6731670b45a	2026-08-02 16:50:27.050842+00
0046_party_identity.sql	5daf0c3d626de9f09d30a16e8c7808e102fb0f954af93eee6446b971748b14d4	2026-08-02 17:49:57.383312+00
0047_vendor_relationship.sql	ee52249a33d8c557a7a3659c8a2d3d283733a47619e528431fa883113ea880d6	2026-08-02 17:53:12.389283+00
0048_candidate_pool.sql	378a56d12dd4f19e10c6c7a60e5fa7ecbef12ec3fa950a6655371fa16c627f54	2026-08-02 17:56:14.540881+00
0049_deprecation_register.sql	bc36b68f84e5fe8a24ac440b6ea268aa149f1de10e6942e80c1345ff853278f8	2026-08-02 17:57:46.981675+00
0050_vendor_category.sql	973501637dd4e0897b8002d2386820f59bca8565c4fa1fba1e4267ff6ec1babc	2026-08-02 18:23:24.52409+00
0051_intro_graph.sql	6ebe5196706477fd1b68fd2debd505cba311ad2e02fe2b016585a223fa49559e	2026-08-02 18:24:56.163485+00
0052_vendor_level_triggers.sql	f55455977ffc90370654ddf7272f19e4dd2377e763cff0b58c0a278d72d1a68d	2026-08-02 18:31:50.131704+00
0053_activity_connected.sql	a64deacfde0964e54bc634959713b47c10d382a4590d5e5e140be1c501ad655f	2026-08-02 18:34:01.184753+00
0054_contact_type.sql	789e1773363281fb73bbfdadd5dd97b54713206e68590a4546a6c7d881954745	2026-08-02 18:42:38.570376+00
0055_repair_orphaned_roles.sql	4035dad033bb35f0d2378d3ed3fbe8a4c2192608a9cd78724b68cf5343180269	2026-08-02 18:51:40.524982+00
0056_ref_index_party_branch.sql	1600d3ac8f27fa7ce1956be2da718a256d28d7a4d30a0c231788d5e65603f0a9	2026-08-02 21:44:29.525586+00
0057_role_timeouts.sql	d704ad6febe6f39418366d024a82561bcb2de306f73fb2d2ce45219b658188d4	2026-08-02 22:13:57.002024+00
0058_assert_view_disjoint.sql	81bbb6a3b2ef92cdfb420539c62d4fc2cc02a4a559cfe3dae66160b102353fc6	2026-08-02 22:13:57.234313+00
0059_org_identity.sql	bbef263245261b51d3a42d2504e96af8772310503abfe41eaa8d4d22c69f8616	2026-08-02 22:13:57.669688+00
0060_participant_sides.sql	8b8893ab869987c4c140b516ec2cfb1de8f476d6292e1e0466c1c2a8d68d4f13	2026-08-03 00:50:25.051853+00
0061_national_account.sql	0e2feea603aa5f2dc2c02a059ebace36388ef201d79988ae43a5f24b88cde4ea	2026-08-03 00:50:25.540324+00
0062_orphaned_edge.sql	64421fbd3cee3063f0d0944038e046366c0cf22df9965b0b4b2ad889e822825d	2026-08-03 00:50:25.846843+00
0063_counterparty_observation.sql	8da6c18fd91f92627fb5ce851c8c4b5fe4c9c0d3186bb7d3dd2b2e99bc5470fa	2026-08-03 00:50:26.209565+00
0064_counterparty_scorecard.sql	a8e0dbcbaf9743ca54e2ad43c1115f58dbee4bc24efef253916ab919c919af24	2026-08-03 00:50:26.585964+00
0065_vendor_level_evidence.sql	370b9717d24b4267b5fba34ef7882cc34dfc57ce655a499cb71f8ee2490b9e5d	2026-08-03 00:50:26.90426+00
0066_marketing_campaign_and_measurement.sql	893eb737693d179074f4008f74011b1a347c0de69c79ca8fcd9717a335e1ffca	2026-08-03 01:49:48.256599+00
0067_compiled_rules_id.sql	d4a3248f499b9b77a554c654ab02cf16e1a5fb878fc7accbf7e0c38308ac9442	2026-08-03 01:59:11.939517+00
0068_rule_version.sql	eb16d1a3d52a9728ef0ca41bccc937e0eab4e170fa020a248a17ecbd5fafac76	2026-08-03 02:21:33.141098+00
0001_init.sql	877284179e1bd77fd6f008b1c764016f5a43218267256efa061a6cf700df5a48	2026-07-30 16:38:46.9876+00
0069_enrichment_write_gap.sql	35d8a0cd2072ac7657fb15287b6d2795ea816cc3c15d54d20c2e31ed7b365a1e	2026-08-06 14:59:07.124217+00
0070_source_capture.sql	af7d146327bde5369893e575670eddc9e37c4b7ff47127bad7aa0002c120cd5c	2026-08-07 02:50:23.605478+00
0071_forgetting_policy.sql	8d4db137309f6003f40e4ec97eba1758da350ee93e4527cbde3281cebd9cdd12	2026-08-07 02:50:23.998651+00
0072_vendor_category_grant.sql	c035fd52a553b4671f05068d472a7179723c6627783224c21e632746dbbd731e	2026-08-07 03:03:44.919859+00
0073_wave1_research.sql	5ca1332c955ab056dd2150934e533c5632421318ec2b585d3f5a6ad3c42e2eec	2026-08-07 03:54:34.794921+00
0074_deal_city_lane.sql	785b3ea5992b18698563d792e64de8cdcb7251b5be3b16af5064f66c8b612c72	2026-08-08 03:10:19.081899+00
0075_doctrine_store.sql	72f32794d245317cc3ba5d6e6303e574ca4a59d7c6b963ad7caac055fd00bbee	2026-08-08 03:20:20.951425+00
0076_doctrine_grants.sql	b5385df6eac2c7d6a1e55e3571629f748ed0d91e50da6bf4b2ba9973b49da4d5	2026-08-08 03:57:15.212545+00
0077_reader_actor_columns.sql	d81eaec56ad603c5e62c737efe98fac4ca6ba8606b7da3bada7f4b8e70fd0dfe	2026-08-08 04:18:00.288267+00
0078_writer_participant_role_grant.sql	d566b86309130bbcf3faf422d5d8704a8c639a8f87a64cd2a9b2302c6b25474d	2026-08-08 04:25:55.120546+00
0078_exporter_doctrine_select.sql	92afb9da32a85c347f86b17f010b5e8acad653d543c8ee61de732ab5b9c0a0fc	2026-08-08 04:42:32.092009+00
0079_review_clock_backfill.sql	21b85ace1be20bb1f9f8273407d1d58a40f393e62eeced513571644dc682df8c	2026-08-08 04:56:09.289671+00
0080_reader_briefing_grants.sql	780e66787c186887466f795ab78c73184bb25055fa1552eb0aa5df39dc7559ba	2026-08-08 05:10:01.202221+00
0079_deal_room_api.sql	3bc8e46d6022c3cf74ccc7878ab2d6fd227b814fca5105bbffc06fadcc0ef0ba	2026-08-08 16:48:36.768348+00
0080_deal_room_board_view.sql	5c4a9fa12838a114a4d5742c4dfd307c26d3cefe0966ff4719a08e8b7d336b22	2026-08-08 17:30:59.868641+00
0081_capture_bridge.sql	e31f98b982d7ad2edadd7379bd81188245eb9cf2e047afef3266c31ab6a2a05b	2026-08-08 19:57:53.27764+00
0081_loop_blocker.sql	828b40c3186b39a675d7563884d1667fee384789a28cc38e1ba1e7b399470daf	2026-08-09 21:53:14.585775+00
0074_outside_model_actors.sql	35d4eb1c2d31b536cfbe2964b36c3c6e1a5551801fb7d8d2f8c3740ecf4a86e4	2026-08-09 21:55:40.214353+00
0082_timeline_event_summary.sql	f78e5f4c72523a308d33195bfd76709211b1feabdc472ad8cc5d851933326a09	2026-08-09 22:11:20.593349+00
0083_loop_gate_cutover.sql	0cdb8e568d8dd10488a8ba8e1c255f8cac938dee5c97e8a1187963e6b290e109	2026-08-09 22:22:46.5206+00
0084_loop_proximity.sql	bb392636289594ef80d3c0a3734f9eb1911c886ae4cd0ce449689063d2070ce8	2026-08-10 01:49:32.406608+00
0085_decision_price.sql	cc27d55dbe53ce67689f56ff47334e0b408def583a84cd47d0d68d2cd1707da1	2026-08-10 01:54:26.802652+00
0086_candidate_claim_card.sql	3b18009e794a950b04e36c8fae7086a2a95592fa5e6941561841b58503d47ebd	2026-08-10 03:53:59.90347+00
0087_outreach_disposition.sql	6b9ca2513233f51c270d2d4e01b730555102cc22ca8d6487c9b098ee14c9b80f	2026-08-10 04:12:54.161538+00
0088_triage_names_and_age.sql	b9421e343c703d9891a94a6306ace7c039151119ef02dc01c86e68d0945c6eab	2026-08-10 05:24:01.897288+00
0089_loop_owner_normalise.sql	d44309e7f64498971195fa8d5cc6f19cca4d4cbc98c4b94851983d559c58e96e	2026-08-10 06:03:23.414331+00
0090_deal_room_workspaces.sql	6599fdf05e4e140bb0ea354993c1904d73a5dc0dc2f2bb197d32feb1c60df17d	2026-08-10 14:36:38.465436+00
0091_deal_reconciliation_read.sql	0848d02b3e148e388b24ad052acc2cf85f4015b7fbedc3917c51a975aa88a16a	2026-08-10 16:56:18.889238+00
0092_deal_operating_state.sql	879342c676627b0a4993d148a2e6e8fd3563c9a1653530155ebaf28ed5ad5fe9	2026-08-10 16:56:19.194134+00
0093_deal_parking_shape_hardening.sql	2621d4e82c2fa3e9c18f23b63e7d8779cc8be70e96b26086bf56b8c3f94e45d5	2026-08-10 16:56:19.395025+00
0094_call_mode_post_call.sql	8fce2faee082588ffd6705735b97dc6e8c3e98745ca4b2c0a0823ae4ccc2a6e4	2026-08-10 18:25:01.095849+00
0095_sponsor_runtime_audit.sql	7cd301e7a199d6ea3842fe4578ef01fde45be2231780f20fb950fbb56903eaaf	2026-08-11 13:00:21.327955+00
0095_vendor_lookup_grants.sql	52fd28050658a6f963e3f00297ef371a5730cbade0d02897fdb17f453d405cc3	2026-08-11 17:55:33.041028+00
0096_loop_owner_legacy_repair.sql	325013b201f7eb9982dc0ef0786e3f025efdf3ba958b9da387bb1ed891107af3	2026-08-11 18:26:02.361992+00
0097_doctrine_review_clock_backfill.sql	b008ac83d6fd82d8c618702991cdd442783bbb960103df0b1336eec78a03c79f	2026-08-11 18:42:35.927417+00
0098_investigation_control_plane.sql	a449b98bb7112cf6635cb9050e378097d2edd09b8a344bed74ae532c0f03caba	2026-08-13 07:19:27.754741+00
0099_deal_stagnation_routes.sql	3bb29568b7060afce56e9496d288b79ef1621f22fab1948fb739ee198d61eb16	2026-08-13 07:19:28.024532+00
0100_doctrine_review_clock_batch_door.sql	4234503cea6b37efe16197e0595b431a4f774d773946980241040c935578f7a6	2026-08-13 07:19:28.228234+00
0101_code_review_subject.sql	af695656bf9f8b2c17fe150c71b45c467de71460e7d98e3eb8dd23245188700c	2026-08-13 16:17:00.334346+00
0102_lead_client_link.sql	166166376221f82182a78f7dbc4f9525bc2b35cc2fb419a891ba49d46a4500a6	2026-08-13 16:17:00.646625+00
0103_defect_log.sql	11b8a6eaa981d9baab939b51dac517cd7b528aba45e37b270699aea377f93c0f	2026-08-13 16:23:34.163531+00
0104_reverify_queue_supersession.sql	6cb855d81d5d6f13de72bc045da5d0f0fd8d0db21cc0f1b1c2a281a85518815c	2026-08-13 17:19:44.292634+00
0105_placement_measurement_jobs_read.sql	8a741c5939be146d2b34ba4e2adaf5f4993507f9cd7d168d657307abb14a7057	2026-08-13 17:20:56.844752+00
0106_precedent_and_point_in_time.sql	f268a02115913fabeeaeebc390f4af9cafce68232ee8e627f215ee01d04c38f3	2026-08-13 18:23:16.043471+00
0107_loop_domain_grant.sql	6de7b079502b1d52024096a4bcccbb91fcc25f62baa2114df7c0f457bbaf2961	2026-08-13 18:50:27.092661+00
0108_tool_read_call.sql	900992e4ce356ccdbd433b7e718a0b0789151d94f67ecf29d4ac7495e9cc307a	2026-08-13 19:23:34.340957+00
0109_integrity_digest_unregistered_targets.sql	172db70b5cf452190ac9d43257bcb15077c3d687f595b49c1ced78fe8f73a1b5	2026-08-13 20:02:40.860688+00
0110_decision_entry_occurred_at.sql	959da3c8137c9dabb1f1f0f170b2ff7f2bd8c12aab6b59878acaa42fa9b680a9	2026-08-13 20:24:27.946763+00
0111_media_recommendation.sql	253418f110136039b00b14542d6d7770dcf9d0dc98ce4e930749c2144cd989d9	2026-08-13 20:24:28.17042+00
0112_loop_number_unique_open.sql	88e6a043ecdd923b84f1118993a0351a0f7ee6100d28efc786575eb455c18ac9	2026-08-13 21:30:15.468462+00
0113_schema_ledger_view.sql	6e496cd2b0f9ad33eb1c576a4d025cc9ac1426f96e4c7b0536b59f296a488a76	2026-08-13 21:30:15.682358+00
0114_ops_schema_work_request.sql	a89e520d34ca3ade8f3f234d17684ddfa45c997dd17c3cbf8de61028ffbe5921	2026-08-13 21:34:54.264624+00
0013a_historical_client_status_vocabulary.sql	efb2bdec24e9ed8ea9597a38e7bec5b06681a36bb95b4e305aa8cf59a5522ce6	2026-08-14 02:00:09.337726+00
0115_ops_observability.sql	b4962c412c5de6c70002ea1b74a8d75f585cbf260a5622e8dd1bd41989c29478	2026-08-14 02:00:09.88795+00
0116_incident_signature.sql	9267e4799295a3c888a053ce018607140bcca5115b9c38ab77d151f098743070	2026-08-14 11:07:52.632609+00
0117_collector_incident_grants.sql	25f46a6ade52dbea6a2e6e13072cec0510b992cbfd68424f8f310d0db2bb0e07	2026-08-14 11:21:39.42866+00
0118_settings_change.sql	062bcfbb196bcd131a185fd045cd4194846623b16e9650858eed2495b1fe8996	2026-08-14 13:08:24.514561+00
0119_backup_role.sql	49494c9b14ea35a5896c33633fb94db39beeb3c69f2efccce8d804e66bfc3525	2026-08-14 14:26:48.084134+00
0120_backup_role_sequences.sql	a8b20e5a2a8602394ded89852e976df3e8a644bedb8dcfe6c04001cd31ec3254	2026-08-14 16:14:27.222819+00
0121_registry_writer_grants.sql	29940125bc7e286eb5bddef93edc4de5ec25205ae747891f9210f3b67b00f2c6	2026-08-14 19:50:27.228399+00
0122_worker_trace.sql	e8f18df1ce59c92efb8433f8c6935b5592705aa1a2ff048411143642e2a6b7ce	2026-08-14 21:21:12.203596+00
\.


--
-- PostgreSQL database dump complete
--


--
-- PostgreSQL database dump
--



SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Data for Name: activity_kind; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.activity_kind (slug, label, is_contact) FROM stdin;
call	Call	t
email_out	Email Out	t
email_in	Email In	t
meeting	Meeting	t
tour	Tour	t
text	Text	t
counter_sent	Counter Sent	t
counter_received	Counter Received	t
loi	LOI	t
lease_signed	Lease Signed	t
note	Note	f
task	Task	f
analysis	Analysis	f
\.


--
-- Data for Name: actor; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.actor (id, slug, kind, display_name, email, active, phone) FROM stdin;
722901b8-efb3-4bd6-bef0-c5d481c0a35e	dell	human	Dell McCraney	\N	t	\N
c659db8e-1920-4ddf-b795-ae30b8bd3380	automation	automation	Scheduled jobs	\N	t	\N
c16ce9d1-0b9e-4306-baab-dabaedda9961	system	system	System (migrations, exporters)	\N	t	\N
b6c38b27-d006-4fad-9c38-49edf3130a07	joe	human	Joe Bookout	joe.bookout@carr.us	t	850.361.2208
8876e348-71da-48d9-92e9-15d9a87d4529	smoke-probe	automation	Smoke Probe	\N	t	\N
65385eb1-033c-42d2-97df-cb1511927d9f	codex-reviewer	automation	Codex Reviewer (Automatic Review Council)	\N	t	\N
c585dd75-5aa0-44e1-8947-71d9688abb91	grok-reviewer	automation	Grok Reviewer (Automatic Review Council)	\N	t	\N
eec6654d-4433-4a93-9b22-61decbd3aa4e	quill-joe-mac	automation	Quill capture rig (Joe MacBook Pro)	\N	t	\N
9e45d3ef-1f24-45c8-b5d8-cd31fafceb2f	codex	automation	Codex CLI (outside-model agent surface, loop #227)	\N	t	\N
3118c9e4-82a4-45c9-bf36-b7ebaba0549d	grok	automation	Grok Build CLI (outside-model agent surface, loop #227)	\N	t	\N
88c9d50d-1ed0-4cc1-a779-68de9bba4554	claude	automation	Claude Code (sponsored runtime agent, 0095)	\N	t	\N
63923291-cea4-426f-8a78-d21512e15a45	joe-local	automation	Joe (local)	\N	t	\N
\.


--
-- Data for Name: client_status; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.client_status (slug, label, sort, is_active_pipeline, note) FROM stdin;
roster	Roster	10	f	In the book, unworked. Bulk universe: on the roster, not on the daily active book. Nobody has committed to working this record yet.
cold	Cold	20	f	Approached, no traction. NOT on the active book, and that is deliberate (Joe, 2026-07-31 ~12:30pm CT — this SUPERSEDES 0014's note, which said "ON THE WORKING BOOK ... Appears in clients-active.md"). His reasoning: a cold client is usually ghosting, you never pester, and the abundance mindset says work the live ones. Cold is a separate not-top-of-mind category, not a queue to grind. It comes back the moment there is a deal.
engaged	Engaged	30	t	Live relationship, no open deal. On the active book.
active_deal	Active deal	40	t	Open deal in progress. On the active book.
paused	Paused	50	f	Deliberately on hold. NOT on the active book (Joe, 2026-07-31 ~12:30pm CT — SUPERSEDES 0014, which flagged paused pipeline-active). A pause can run longer than a year; a paused client sitting in the daily book is noise. An open deal still pulls the client back on regardless of this status.
past_client	Past client	60	f	Deals concluded, relationship kept. Off the active book until a new deal opens; a past client is a referral source, not a task.
\.


--
-- Data for Name: client_type; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.client_type (slug, label) FROM stdin;
independent	Independent
group	Group practice
dso	DSO
franchise	Franchise
regional_system	Regional system
national_account	National account
\.


--
-- Data for Name: contact_state; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.contact_state (slug, label, contactable, sort) FROM stdin;
active	Active — normal contact	t	10
nurture	Nurture — long-cycle cadence only	t	20
paused	Paused — do not contact until a date	f	30
do_not_contact	Do not contact — standing, with a reason	f	40
\.


--
-- Data for Name: deal_lane; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.deal_lane (slug, label, sort, note) FROM stdin;
territory	Territory	10	Inside Joe and Dell's own market: South Alabama through the Florida Panhandle. The default lane and the one the pipeline work is built around.
national	National	20	A national-account deal, worked under a brand-level relationship and usually outside the home territory. A LANE, not a segment and not a vertical: a national deal still has a vertical, and a territory deal can belong to a national account. National accounts are a separate business model (DNA/Leads/pipeline-craft.md Part C).
\.


--
-- Data for Name: deal_phase; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.deal_phase (slug, label, sort) FROM stdin;
pending	Pending	10
research	Research	20
site_selection	Site selection	30
negotiation	Negotiation	40
closing	Closing	70
closed	Closed	80
legal	Legal	50
due_diligence	Due Diligence	60
\.


--
-- Data for Name: deal_type_ref; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.deal_type_ref (slug, label, sort) FROM stdin;
lease	Lease	10
purchase	Purchase	20
sale_leaseback	Sale Leaseback	30
build_to_suit	Build to Suit	40
renewal	Renewal	50
relocation	Relocation	60
additional_office	Additional Office	70
startup	Start Up	80
expansion	Expansion	90
other	Other	100
\.


--
-- Data for Name: diagnostic_route; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.diagnostic_route (route_key, signal_kind, from_kind, relation, to_kind, test_verb, input_contract, minimum_effect, active, created_by, created_at) FROM stdin;
deal_stagnation.next_action_gap	deal_stagnation	deal_stagnation	may_be_explained_by	next_action_gap	get-deal-room	{"deal": "signal.subject_ref", "inspect": ["next_actions", "next_step", "next_date"]}	\N	t	c16ce9d1-0b9e-4306-baab-dabaedda9961	2026-08-13 07:19:27.907776+00
deal_stagnation.relationship_inactivity	deal_stagnation	deal_stagnation	may_be_explained_by	relationship_inactivity	get-deal-room	{"deal": "signal.subject_ref", "inspect": ["last_touch", "activity", "participants"]}	\N	t	c16ce9d1-0b9e-4306-baab-dabaedda9961	2026-08-13 07:19:27.907776+00
deal_stagnation.critical_date_pressure	deal_stagnation	deal_stagnation	may_be_explained_by	critical_date_pressure	get-deal-room	{"deal": "signal.subject_ref", "inspect": ["critical_dates", "documents", "phase"]}	\N	t	c16ce9d1-0b9e-4306-baab-dabaedda9961	2026-08-13 07:19:27.907776+00
\.


--
-- Data for Name: doctrine_edge_type; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.doctrine_edge_type (edge_type, acyclic, precedence_rank, description) FROM stdin;
OVERRIDES	t	10	source wins where both apply
SUPERSEDES	t	20	target is historical, source replaces it
EXCEPTION_TO	t	30	source carves a scoped exception out of target
DEPENDS_ON	t	\N	integrity only — target must stay live
APPLIES_TO	f	\N	scope binding, no precedence
CONFLICTS_WITH	f	\N	detected conflict — BLOCKS commit unless an OVERRIDES/SUPERSEDES edge resolves it
\.


--
-- Data for Name: doctrine_review_policy; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.doctrine_review_policy (id, name, max_age_days, revalidate_on_dep_change, content_classes) FROM stdin;
787d9649-467d-4887-b1a2-6659176e62e6	standing-doctrine	180	t	{playbook,sop,reference}
67de5b2b-d0b5-4f0a-8fe3-1a2586f7b618	routing	90	t	{index}
766982f1-b4ef-459f-85be-d03d17d4bd09	narrative	\N	t	{dossier_narrative,distillation}
\.


--
-- Data for Name: lead_lane; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.lead_lane (slug, label) FROM stdin;
renewal	Renewal radar
new_entity	New entity (corp filings)
relocation	Relocation
upstream	Upstream radar (PECOS/NPPES)
associate	Associate lane
\.


--
-- Data for Name: lead_stage; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.lead_stage (slug, label, sort) FROM stdin;
engaged	Engaged	100
outreach_active	Outreach Active	100
active_deal	Active Deal	100
qualified	Qualified	100
new	New	100
nurture_drip	Nurture (Drip)	100
closed_won	Closed-Won	100
opportunity	Opportunity	100
closed_lost	Closed-Lost	900
do_not_contact	Do Not Contact	910
\.


--
-- Data for Name: loop_domain; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.loop_domain (slug, label, sort) FROM stdin;
marketing	Marketing — social, newsletter, content, profile	40
business	Business — everything else business-side	50
system	System — record layer, repo, automation, hosting	60
deals	Deals — active transactions (incl. vendor intros on a live deal)	10
prospecting	Prospecting — lead generation and lead conversion	20
networking	Networking — vendor meetings, new vendors, vendor-to-vendor, prospect-to-vendor	30
\.


--
-- Data for Name: negotiation_claim_type; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.negotiation_claim_type (slug, label, falsifiable, derived, reversal_test, sort) FROM stdin;
finality	Best and final	t	f	REVERSED when the same side later files a round improved in our favour on any economic axis. Re-sending identical numbers is not a reversal.	10
authority	Authority limit — "the owner will not go below X"	t	f	REVERSED when the same side later proposes a rate better for us than the stated floor (negotiation_claim.stated_floor, defaulting to the claim round's own rate).	20
walk_away	Walk-away — "we are done"	t	f	REVERSED by the existence of ANY later round from the same side. No further test is needed: continuing to negotiate is the contradiction.	30
deadline	Deadline — "this dies on Friday"	t	t	REVERSED when the same side files a round dated after the deadline. DERIVED: the deadline is negotiation_round.expires_on and is NOT logged here. See the header.	40
competing_interest	Competing interest — "another tenant is looking"	f	f	NOT FALSIFIABLE. We can never observe whether the other tenant existed. Loggable so the tactic is visible in the history; permanently excluded from every score by falsifiable = false, which v_counterparty_bluff joins on rather than hardcoding.	50
\.


--
-- Data for Name: participant_role; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.participant_role (slug, label, side) FROM stdin;
support	Support	\N
referring_agent	Referring Agent	\N
investor	Investor	\N
capital_partner	Capital Partner	\N
lead	Lead	actor
client_contact	Client Contact	party
listing_side	Listing Side	party
\.


--
-- Data for Name: party_link_kind; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.party_link_kind (slug, label, sort) FROM stdin;
knows	Knows	10
intro	Intro	20
intro_received	Intro received	30
can_introduce	Can introduce	40
works_with	Works with	50
referral	Referral	60
intro_requested	Intro requested (we asked)	45
introduced	Introduced (completed)	50
referred	Referred (business sent)	60
\.


--
-- Data for Name: submarket_condition; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.submarket_condition (slug, label, tightness, note, sort) FROM stdin;
soft	Soft — oversupplied	-1	Vacancy is available and competing. A concession here costs the other side little; holding firm in a soft market is the version that means something.	10
balanced	Balanced	0	Neither side is carried by the market. This is the condition under which a negotiation score is closest to measuring the negotiator.	20
tight	Tight — landlord-favoured	1	Little competing space. A counterparty who concedes nothing here may simply not need to; that is leverage, and 0064 tags it rather than crediting it as skill.	30
\.


--
-- Data for Name: vendor_category; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.vendor_category (slug, label, sort) FROM stdin;
lender	Banker / Lender	10
cpa	CPA / Financial	20
attorney	Attorney	30
broker	Practice Broker / Consultant	40
gc	General Contractor	50
architect	Architect / Design	60
supply	Supply / Equipment Rep	70
insurance	Insurance	80
it	IT Services	90
marketing	Marketing / Demographics	100
developer	Developer / Investor	110
sbdc	SBDC Consultant	120
franchise	Franchise Development	130
doctor	Doctor (networking)	140
financial_advisor	Financial Advisor / Wealth	25
practice_consultant	Practice Management Consultant	45
practice_admin	Practice Administrator / Coordinator	145
healthcare_exec	Healthcare Industry Executive	150
\.


--
-- Data for Name: vendor_disposition; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.vendor_disposition (slug, label, workable, sort) FROM stdin;
active	Active — in the network	t	10
parked	Parked — deliberately dormant, drip only	f	20
avoid	Avoid — ruled out as a partner, with a reason	f	30
\.


--
-- Data for Name: vendor_relationship_level; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.vendor_relationship_level (level, label, note) FROM stdin;
0	Prospective	Identified as worth knowing. No two-way contact yet. TRIGGER OUT: they reply, or you meet.
1	Building	First TWO-WAY contact has happened — they replied or you met. An outbound attempt alone does not count. TRIGGER OUT: value moves in either direction (an intro, a referral, a deal).
2	Established	Value has moved in EITHER direction at least once. TRIGGER OUT: value moves BOTH ways, more than once.
3	Core	Reciprocal and repeated — value both ways, more than once. Protect these.
\.


--
-- PostgreSQL database dump complete
--


