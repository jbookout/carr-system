-- 0096_loop_owner_legacy_repair.sql
--
-- T70: 0089 deliberately left historical free-text loop owners in place while
-- adding loop_item_owner_known NOT VALID.  PostgreSQL checks even a NOT VALID
-- CHECK whenever an old row is updated, so closing a row such as `joe→dell`
-- failed although close-loop does not change owner.  This is a one-time,
-- reviewed repair: map the spellings actually present in the historical import
-- and validate the existing closed-set constraint.  Do not widen that set.
--
-- Reviewed sources:
--   * 0024_loop_item.sql's import vocabulary: Joe/Claude, Joe→Dell, and
--     Dell's brain→Joe;
--   * tools.js's documented legacy joint forms (Joe + Dell); and
--   * 2026-08-11 read-only loop-board inventory: claude/joe, joe/claude,
--     joe + dell, joe→dell, dell→joe, dell's brain→joe, and the malformed
--     completed-label below.
--
-- Directional labels become the named recipient.  Slash/plus labels denote a
-- multi-owner historic row and become `joint`, preserving the refusal rather
-- than inventing a single accountable owner.  The malformed label was an old
-- completion annotation followed by Dell's brain handing work to Joe, so it
-- maps to Joe.  Any other value aborts before an update; it needs review, not
-- a heuristic assignment.

begin;

do $$
declare
  unmapped text[];
  changed integer;
begin
  select array_agg(owner order by owner)
    into unmapped
    from (
      select distinct owner
        from loop_item
       where owner is not null
         and owner not in ('joe', 'dell', 'claude', 'joint')
         and owner not in (
           'claude/joe',
           'dell''s brain→joe',
           'dell→joe',
           'joe + dell',
           'joe/claude',
           'joe→dell',
           '✅ done 2026-07-29 (joe''s go) — dell''s brain→joe'
         )
    ) unknown;

  if unmapped is not null then
    raise exception '0096: unmapped noncanonical loop owner(s): %',
      array_to_string(unmapped, ', ')
      using errcode = 'check_violation',
            hint = 'Add an explicitly reviewed mapping; do not bypass loop_item_owner_known.';
  end if;

  with owner_map(old_owner, new_owner) as (
    values
      ('claude/joe', 'joint'),
      ('dell''s brain→joe', 'joe'),
      ('dell→joe', 'joe'),
      ('joe + dell', 'joint'),
      ('joe/claude', 'joint'),
      ('joe→dell', 'dell'),
      ('✅ done 2026-07-29 (joe''s go) — dell''s brain→joe', 'joe')
  )
  update loop_item li
     set owner = owner_map.new_owner
    from owner_map
   where li.owner = owner_map.old_owner;

  get diagnostics changed = row_count;
  raise notice '0096: repaired % historical loop owner row(s)', changed;
end $$;

alter table loop_item
  validate constraint loop_item_owner_known;

commit;
