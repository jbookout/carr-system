-- 0095_sponsor_runtime_audit.sql — preserve the verified sponsor separately
-- from the runtime agent on every new tool call and event.
--
-- A runtime actor answers "which executable principal acted". A sponsor
-- answers "which verified CARR partner's personal brain was available". They
-- are independent facts. Existing actor_id continues to be the runtime
-- attribution; no historical row is backfilled because its sponsor was not
-- recorded at the time.

begin;

-- Claude Code is a registered OAuth runtime, separate from its verified
-- sponsor. Like the existing codex/grok automation rows (0074), it has no
-- humanOnly authority merely because a partner sponsored the session.
insert into actor (slug, kind, display_name, active)
values ('claude', 'automation', 'Claude Code (sponsored runtime agent, 0095)', true)
on conflict (slug) do nothing;

alter table event add column if not exists sponsoring_human_slug text;
alter table event add column if not exists personal_scope text;
alter table event add column if not exists authorization_class text;
alter table event add column if not exists organization_tenant_id text;
alter table tool_call add column if not exists sponsoring_human_slug text;
alter table tool_call add column if not exists personal_scope text;
alter table tool_call add column if not exists authorization_class text;
alter table tool_call add column if not exists organization_tenant_id text;

alter table event add constraint event_sponsoring_human_slug_check
  check (sponsoring_human_slug is null or sponsoring_human_slug in ('joe', 'dell'));
alter table event add constraint event_personal_scope_check
  check (personal_scope is null or personal_scope in ('joe-personal', 'dell-personal', 'none'));
alter table tool_call add constraint tool_call_sponsoring_human_slug_check
  check (sponsoring_human_slug is null or sponsoring_human_slug in ('joe', 'dell'));
alter table tool_call add constraint tool_call_personal_scope_check
  check (personal_scope is null or personal_scope in ('joe-personal', 'dell-personal', 'none'));

comment on column event.sponsoring_human_slug is
  'Verified partner sponsoring the runtime agent. Null means unsponsored or pre-0095 unknown.';
comment on column event.personal_scope is
  'Rule brain loaded for this event: joe-personal, dell-personal, none, or null before 0095.';
comment on column event.authorization_class is
  'Server-derived authority class, distinct from request-side operational profile.';
comment on column event.organization_tenant_id is
  'Server-derived CARR organization tenant. Null before 0095 is historically unknown.';
comment on column tool_call.sponsoring_human_slug is
  'Verified partner sponsoring the runtime agent. Null means unsponsored or pre-0095 unknown.';
comment on column tool_call.personal_scope is
  'Rule brain loaded for this tool call: joe-personal, dell-personal, none, or null before 0095.';
comment on column tool_call.authorization_class is
  'Server-derived authority class, distinct from request-side operational profile.';
comment on column tool_call.organization_tenant_id is
  'Server-derived CARR organization tenant. Null before 0095 is historically unknown.';

do $$
declare cols int; claude_rows int;
begin
  select count(*) into cols
    from information_schema.columns
   where table_name in ('event', 'tool_call')
     and column_name in ('sponsoring_human_slug', 'personal_scope', 'authorization_class', 'organization_tenant_id');
  if cols <> 8 then
    raise exception '0095: expected eight sponsor/runtime audit columns, found %', cols;
  end if;
  select count(*) into claude_rows from actor where slug = 'claude' and active = true;
  if claude_rows <> 1 then
    raise exception '0095: expected one active Claude runtime actor, found %', claude_rows;
  end if;
  raise notice '0095: sponsor/runtime audit provenance is ready; prior rows remain unknown';
end $$;

commit;
