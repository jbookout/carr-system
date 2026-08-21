-- 0188_applicable_rules_security_definer.sql
-- The rules compiler must be readable by the role that actually calls it.
--
-- WHAT IS BROKEN, measured live 2026-08-19 against production: the deployed
-- verb `applicable-rules` calls ops.applicable_rules(...), the function that
-- compiles the active admitted rule set for a workflow, surface and tier. The
-- call fails with `permission denied for table rule`, and has been failing
-- since at least 2026-08-18 (incident INC-20260818-01, plus the same shape on
-- 2026-08-15 and 2026-08-17 for other verbs).
--
-- WHY. The worker's read path authenticates as a login role granted carr_reader,
-- and carr_reader is deliberately VIEWS ONLY — mcp-server/wrangler.toml states
-- that as the design ("DATABASE_URL_READER — Neon login role granted carr_reader
-- (views only)"). ops.applicable_rules was created SECURITY INVOKER, so its body
-- executes with the CALLER's privileges and reads the base tables `rule` and
-- ops.rule_admission directly. There is no grant for carr_reader on either, and
-- there should not be: that boundary is the point.
--
-- WHY SECURITY DEFINER RATHER THAN A GRANT. Granting carr_reader select on
-- public.rule would fix the symptom by dismantling the property the boundary
-- exists to hold — the reader would gain a base table, and the next function
-- with the same shape would silently work for the wrong reason. Marking this one
-- function SECURITY DEFINER keeps the boundary intact and widens exactly one
-- door, the one the verb already goes through. The function is owned by
-- neondb_owner, is STABLE, returns rows and writes nothing, takes three scalar
-- text parameters used only in JSONB containment tests, and builds no dynamic
-- SQL, so there is no statement for a caller to influence.
--
-- TWO HARDENING DETAILS, both required for a SECURITY DEFINER function and both
-- absent from the original:
--   * search_path is PINNED. Without it a caller could point `rule` at a table
--     of their own making and have the definer read it. This is the standard
--     SECURITY DEFINER failure and the reason many shops ban the feature
--     outright; pinning removes it.
--   * `rule` is SCHEMA-QUALIFIED as public.rule. It resolved through the
--     search_path before, which is exactly what the pin now forbids.
--
-- The body is otherwise IDENTICAL to what production runs today, copied from
-- pg_get_functiondef rather than retyped, so this migration changes who may
-- read and nothing about what is returned.
--
-- REHEARSED: ops/p1-rebuild-gate.py applies the full migration set to a fresh
-- ephemeral Neon branch on every run; this file is picked up by that path like
-- any other.

begin;

create or replace function ops.applicable_rules(
    p_workflow text default null::text,
    p_surface  text default null::text,
    p_tier     text default null::text)
returns table(rule_id uuid, statement text, enforcement_class text,
              binding_moment text, applicability jsonb)
language sql
stable
security definer
set search_path = pg_catalog, public, ops
as $function$
  select r.id,r.statement,a.enforcement_class,a.binding_moment,a.applicability
    from public.rule r join ops.rule_admission a on a.rule_id=r.id
   where r.status='active' and a.state='admitted'
     and (p_workflow is null or not(a.applicability?'workflows')
          or a.applicability->'workflows'?'*' or a.applicability->'workflows'?p_workflow)
     and (p_surface is null or not(a.applicability?'surfaces')
          or a.applicability->'surfaces'?'*' or a.applicability->'surfaces'?p_surface)
     and (p_tier is null or not(a.applicability?'tiers')
          or a.applicability->'tiers'?'*' or a.applicability->'tiers'?p_tier)
   order by r.created_at,r.id
$function$;

comment on function ops.applicable_rules(text, text, text) is
  'Compiles the active admitted rule set for a workflow/surface/tier. '
  'SECURITY DEFINER with a pinned search_path (0188): the caller is the '
  'worker''s carr_reader role, which is views-only by design and cannot read '
  'public.rule or ops.rule_admission directly. Read-only, no dynamic SQL.';

commit;
