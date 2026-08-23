-- 0288_rule_delivery_layers.sql
-- WHERE A RULE IS DELIVERED, WHICH IS A DIFFERENT QUESTION FROM HOW IT IS ENFORCED.
--
-- WHY THIS EXISTS (2026-08-23, the rules council's delivery half). The
-- enforcement recategorization shipped into Production earlier the same day
-- through bin/sync-rule-admission-prod.sh: 218 active rules, 218 admission
-- contracts, zero needs_revision. What it did NOT change is what a session is
-- handed at boot. standing-context still recites all 204 recited rules into
-- every session whatever that session turns out to be doing, because nothing in
-- the database has ever said which rules a given piece of work needs.
--
-- Both council chairs ruled the same shape and one of them named the failure to
-- avoid: "Do not flip applicable-rules on with wildcard tags and call it
-- scoping. That would silently drop rules on undeclared sessions." So the tags
-- land as their own typed thing, with the wildcard refused by a constraint
-- rather than by a reviewer's attention.
--
-- WHY NOT INSIDE ops.rule_admission.applicability, WHERE APPLICABILITY LIVES.
-- Two reasons, both structural rather than stylistic.
--
--   1. AN APPROVED RULE'S CONTRACT IS FROZEN. Migration 0228's
--      approved_rule_enforcement_point_immutable trigger raises on any touch of
--      a contract an approval receipt covers, and it is right to: the receipt
--      records Joe's approval of exact contract text. A delivery tag added to
--      that jsonb later would put words inside an approved contract that Joe
--      never approved.
--   2. APPLICABILITY AND DELIVERY ARE NOT THE SAME AXIS, and conflating them is
--      what produced the current state. Every backfilled contract carries
--      applicability {"workflows":["*"],"surfaces":["*"],"tiers":["*"]} — the
--      wildcards the chair warned about, already in Production, on 214 rules.
--      Overwriting those in place would silently change what
--      ops.applicable_rules answers, which is a live enforcement compiler.
--
-- AND ops.applicable_rules IS NOT THE PACK COMPILER, WHICH IS THE FINDING THAT
-- SHAPED THIS FILE. Measured against Production 2026-08-23: with no filter at
-- all it returns ONE row out of 218 admitted rules. Migration 0228 replaced the
-- 0148 selector with a receipt-bound form — a rule is returned only while its
-- statement hash, normalized contract, authority receipt, control bindings and
-- every requested installed control still match an immutable approval. That is
-- the correct behaviour for an enforcement compiler and it is catastrophic as a
-- delivery selector: a session that compiled its rule pack from it would be
-- handed one rule and told that was the applicable set. This migration
-- therefore adds a SEPARATE selector over the delivery tags, and leaves
-- ops.applicable_rules exactly as it is.
--
-- SHADOW MODE IS THE DEFAULT AND IS RECORDED HERE, not in a deploy flag. Both
-- chairs required a week of running the selector alongside full recitation
-- before anything is cut. ops.rule_delivery_policy starts 'shadow', which means
-- the verb computes the scoped set, reports it, and still recites everything.
-- Flipping to 'enforced' is a Joe-authority row change, not a code change, so
-- the flip and the rollback are both one statement.

begin;

create table if not exists ops.rule_pack (
  pack          text primary key,
  title         text not null,
  description   text not null,
  triggers      text[] not null,
  source        text not null,
  updated_at    timestamptz not null default now(),
  -- A pack nothing can fire is a pack nothing ever loads, which is how a
  -- "scoped" rule becomes an unrecited one.
  constraint rule_pack_has_triggers check (cardinality(triggers) >= 1),
  constraint rule_pack_no_wildcard_trigger check (not ('*' = any(triggers))),
  constraint rule_pack_named check (btrim(pack) <> '' and pack <> '*'
                                    and btrim(title) <> '' and btrim(description) <> '')
);

comment on table ops.rule_pack is
  'The task packs a session can load. Triggers are OBSERVED-WORK nouns and verbs, '
  'never session names: rule 347a9ca6 forbids predicting the work from the title.';

create table if not exists ops.rule_load_layer (
  rule_id       uuid primary key references rule(id),
  short_id      text not null,
  load_layer    text not null,
  packs         text[] not null default '{}',
  scope         text not null,
  why           text,
  source        text not null,
  map_digest    text not null,
  updated_at    timestamptz not null default now(),
  constraint rule_load_layer_known check (load_layer in ('layer0','control','pack')),
  -- Layer 0 is unconditional. A pack on it reads as a narrowing of something
  -- that must survive an undeclared boot.
  constraint rule_load_layer_layer0_unconditional
    check (load_layer <> 'layer0' or cardinality(packs) = 0),
  -- A pack rule with no pack is delivered by nothing at all.
  constraint rule_load_layer_pack_has_pack
    check (load_layer <> 'pack' or cardinality(packs) >= 1),
  -- The wildcard refusal, in the database rather than in a reviewer's attention.
  constraint rule_load_layer_no_wildcard check (not ('*' = any(packs))),
  -- Layer 0 costs every session tokens forever; it states why it earns them.
  constraint rule_load_layer_layer0_reasoned
    check (load_layer <> 'layer0' or coalesce(btrim(why),'') <> ''),
  constraint rule_load_layer_scope check (scope in ('shared','joe','dell'))
);

comment on table ops.rule_load_layer is
  'WHEN each active rule''s text reaches a session. layer0 = every boot. '
  'control = an installed deny/stop/schema control prints it at the moment it '
  'binds. pack = when the observed work matches a pack trigger. '
  'ops/rule-load-layer-check.py holds the same contract statically, including '
  'the one this table cannot see: that `control` is only honest for a rule whose '
  'enforcement_class is actually built.';

-- Every named pack must exist. This is a trigger rather than a foreign key
-- because the packs are an array on the row, and it is safe as an
-- invoker-rights trigger for the reason rule 5409731b makes us state out loud:
-- the ONLY writer of this table is the reviewed-map backfill, which runs as the
-- owning role through bin/sync-rule-admission-prod.sh. No application role holds
-- insert or update here, so no caller can hit an ungranted read inside the body.
create or replace function ops.validate_rule_load_layer_packs()
returns trigger language plpgsql as $$
declare missing text;
begin
  select string_agg(p, ', ') into missing
    from unnest(new.packs) p
   where not exists (select 1 from ops.rule_pack rp where rp.pack = p);
  if missing is not null then
    raise exception 'rule % names undefined pack(s): %', new.short_id, missing;
  end if;
  return new;
end $$;

drop trigger if exists rule_load_layer_packs_exist on ops.rule_load_layer;
create trigger rule_load_layer_packs_exist
  before insert or update on ops.rule_load_layer
  for each row execute function ops.validate_rule_load_layer_packs();

create table if not exists ops.rule_delivery_policy (
  singleton     boolean primary key default true,
  mode          text not null default 'shadow',
  changed_by    text,
  reason        text,
  changed_at    timestamptz not null default now(),
  constraint rule_delivery_policy_singleton check (singleton),
  constraint rule_delivery_policy_mode check (mode in ('shadow','enforced'))
);

comment on table ops.rule_delivery_policy is
  'shadow = compute the scoped set, report it, recite everything anyway. '
  'enforced = deliver the scoped set. Both council chairs required a week of '
  'shadow at zero unexplained misses before this row may say enforced.';

insert into ops.rule_delivery_policy (singleton, mode, changed_by, reason)
values (true, 'shadow', 'migration:0288',
        'Shadow first: the selector runs beside full recitation until a week of '
        'observations shows no consequential rule was omitted.')
on conflict (singleton) do nothing;

-- THE SELECTOR. SECURITY DEFINER with a pinned search_path for the reason
-- migration 0188 spelled out for ops.applicable_rules: the worker's read path
-- authenticates as carr_reader, which is views-only by design and cannot read
-- public.rule. Pinning the path is what makes definer rights safe here — without
-- it a caller could point `rule` at a table of their own making.
--
-- IT RETURNS EVERY ACTIVE RULE IN SCOPE, with a `selected` flag, rather than
-- filtering. That is deliberate and it is what makes shadow mode possible at
-- all: the caller can see exactly which rules a scoped delivery WOULD have
-- omitted, which is the comparison the council asked to run for a week.
create or replace function ops.rule_delivery_plan(
    p_actor text default null,
    p_packs text[] default '{}')
returns table(rule_id uuid, short_id text, load_layer text, packs text[],
              scope text, selected boolean)
language sql stable security definer
set search_path = pg_catalog, public, ops
as $function$
  select r.id, l.short_id, l.load_layer, l.packs, l.scope,
         (l.load_layer = 'layer0'
          or l.packs && coalesce(p_packs, '{}'::text[])) as selected
    from public.rule r
    join ops.rule_load_layer l on l.rule_id = r.id
   where r.status = 'active'
     and (l.scope = 'shared' or (p_actor is not null and l.scope = p_actor))
   order by l.load_layer, l.short_id
$function$;

comment on function ops.rule_delivery_plan(text, text[]) is
  'The DELIVERY selector: which active rules a scoped boot would hand this '
  'partner for these packs. Not ops.applicable_rules, which is the receipt-bound '
  'ENFORCEMENT compiler and returns only rules carrying a live approval receipt.';

create or replace function ops.rule_pack_index()
returns table(pack text, title text, description text, triggers text[], rule_count bigint)
language sql stable security definer
set search_path = pg_catalog, public, ops
as $function$
  select p.pack, p.title, p.description, p.triggers,
         count(l.rule_id) filter (where r.status = 'active') as rule_count
    from ops.rule_pack p
    left join ops.rule_load_layer l on p.pack = any(l.packs)
    left join public.rule r on r.id = l.rule_id
   group by p.pack, p.title, p.description, p.triggers
   order by p.pack
$function$;

comment on function ops.rule_pack_index() is
  'The PACK INDEX a Layer 0 boot returns instead of the packs themselves: names '
  'and triggers, so an undeclared session knows what exists and what would load it.';

grant select on ops.rule_pack, ops.rule_load_layer, ops.rule_delivery_policy
  to carr_reader, carr_writer;
revoke all on function ops.validate_rule_load_layer_packs() from public;
grant execute on function ops.rule_delivery_plan(text, text[])
  to carr_reader, carr_writer;
grant execute on function ops.rule_pack_index() to carr_reader, carr_writer;

do $$
begin
  if exists (select 1 from pg_roles where rolname='carr_jobs') then
    -- The nightly watch counts tagged rules and reads the policy row. It never
    -- needs a statement, and migration 0285 already refused it one.
    execute 'grant select on ops.rule_pack, ops.rule_load_layer, '
            'ops.rule_delivery_policy to carr_jobs';
    execute 'grant execute on function ops.rule_pack_index() to carr_jobs';
  else
    raise notice '0288: carr_jobs is absent (a disposable or sanitized database); '
                 'skipping the routine grants';
  end if;
end $$;

commit;

do $$
declare bad int;
begin
  if to_regclass('ops.rule_load_layer') is null or to_regclass('ops.rule_pack') is null
     or to_regclass('ops.rule_delivery_policy') is null then
    raise exception '0288 FAILED: the delivery tables are missing';
  end if;
  if to_regprocedure('ops.rule_delivery_plan(text,text[])') is null
     or to_regprocedure('ops.rule_pack_index()') is null then
    raise exception '0288 FAILED: the delivery selector is missing';
  end if;
  if (select mode from ops.rule_delivery_policy) <> 'shadow' then
    raise exception '0288 FAILED: delivery must start in shadow mode';
  end if;
  -- Prove the wildcard refusal is real rather than documented, the way rule
  -- e65efc68 asks: the constraint fires before anything depends on it.
  begin
    insert into ops.rule_pack (pack, title, description, triggers, source)
    values ('0288-probe', 't', 'd', array['*'], 'migration:0288');
    raise exception '0288 FAILED: a wildcard trigger was accepted';
  exception when check_violation then null;
  end;
  select count(*) into bad from ops.rule_pack where pack = '0288-probe';
  if bad <> 0 then raise exception '0288 FAILED: the probe row survived'; end if;
end $$;
