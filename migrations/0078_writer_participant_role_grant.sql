-- 0078_writer_participant_role_grant.sql — un-break set-lead: let the writer's
-- side-validation trigger read the vocabulary it enforces.
--
-- THE FAILURE (found 2026-08-08, loop #249): every set-lead call returned
-- "internal error". The verb's insert into deal_participant fires 0060's
-- trg_deal_participant_side, whose first statement is
--   select side into s from participant_role where slug = new.role;
-- The function is invoker-rights plpgsql (prosecdef=false), so that SELECT runs
-- as carr_writer — and carr_writer holds no grant on participant_role: 0017
-- created it after 0004's blanket grant and added none, on the reasoning that
-- "the FK checks the writer role triggers run as the referenced table's owner."
-- True for plain RI checks (which is why new-deal's FKs to the equally
-- ungranted deal_type_ref and deal_lane pass), false for an invoker-rights
-- trigger reading the table directly. set-lead therefore broke the moment 0060
-- was applied (2026-08-03) and nobody noticed until the five 2026-08-07
-- Salesforce deals needed lead owners: the 40 existing lead rows are
-- import-written, and rehearsals ran as owner (the 0076 blind spot), where
-- grants never fire.
--
-- THE FIX, column-scoped per the 0077 pattern: slug and side are the two
-- columns the trigger reads. label stays owner-only; the views-only posture of
-- everything else is untouched. The other 0066 vocabulary trigger
-- (campaign_channels_valid -> marketing_subject) already carries its grant;
-- sweep 2026-08-08 found no third instance of the class.

begin;

grant select (slug, side) on participant_role to carr_writer;

do $$
declare n int;
begin
  select count(*) into n from information_schema.column_privileges
   where grantee = 'carr_writer' and table_name = 'participant_role'
     and column_name in ('slug','side') and privilege_type = 'SELECT';
  if n < 2 then
    raise exception 'carr_writer participant_role column grants incomplete (% of 2)', n;
  end if;
end $$;

commit;
