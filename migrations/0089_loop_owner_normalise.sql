-- 0089_loop_owner_normalise.sql — one spelling per owner, enforced, so an owner
-- filter stops silently missing a third of the list.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- THE BUG, measured 2026-08-10. Open loops by owner:
--     claude 92 · Joe 31 · joe 22 · dell 4 · Claude 4
--
-- 'Joe' and 'joe' are the same person and 'Claude' and 'Claude' the same actor,
-- but nothing in the schema says so, so every query that filters on owner is
-- wrong. `loop-board owner:'joe'` returns 22 of Joe's 53 rows and reports the
-- other 31 to nobody. The verb's own description offers owner as the way to see
-- "a person's pile", and it has been showing 42% of it.
--
-- This is worse than a cosmetic inconsistency because of WHERE it lands: the
-- autonomous drain queue is selected by owner='claude', so four rows the system
-- was allowed to finish on its own evidence have been invisible to it since they
-- were written. A queue that silently omits members is the same defect class as
-- a check that cannot fail.
--
-- WHY A CONSTRAINT AND NOT JUST AN UPDATE. An UPDATE fixes today's 35 rows and
-- nothing stops the 36th. The values are a closed set — the two partners, the
-- system actor, and 'joint' for the legacy rows owned by two people at once
-- which loop-board already documents as selectable by nobody. A CHECK makes the
-- next mis-spelling fail at the write instead of at some future query that
-- quietly returns less than it should.
--
-- 'joint' IS PRESERVED DELIBERATELY, not normalised away. Those rows are a real
-- and separate defect (a loop owned by two people is owned by neither, and
-- close-loop refuses them), tracked in its own row. Folding them into a person
-- here would erase the evidence of a problem that has not been fixed.

begin;

update loop_item
   set owner = lower(btrim(owner))
 where owner is not null
   and owner <> lower(btrim(owner));

-- Anything that is not one of the known actors after lowercasing is left alone
-- and surfaced by the guard below rather than guessed at. Silently rewriting an
-- owner nobody recognised would be inventing an assignment.
alter table loop_item
  drop constraint if exists loop_item_owner_known;

alter table loop_item
  add constraint loop_item_owner_known
  check (owner is null or owner in ('joe','dell','claude','joint'))
  not valid;

comment on constraint loop_item_owner_known on loop_item is
  'One spelling per owner. Added 0089 after Joe 31 / joe 22 and Claude 4 / '
  'claude 92 meant every owner filter returned a fraction of the pile — '
  'including the autonomous drain queue, which selects on owner=claude and had '
  'been silently omitting four rows it was entitled to work. NOT VALID on '
  'purpose: it binds every future write without failing the migration on any '
  'historical row a human still needs to look at.';

-- guards, before commit
do $$
declare mixed int; unknown_owner int; rec record;
begin
  select count(*) into mixed from loop_item
   where owner is not null and owner <> lower(btrim(owner));
  if mixed > 0 then
    raise exception '0089: % row(s) still carry a non-normalised owner', mixed;
  end if;

  select count(*) into unknown_owner from loop_item
   where owner is not null and owner not in ('joe','dell','claude','joint');
  if unknown_owner > 0 then
    for rec in select distinct owner from loop_item
                where owner is not null
                  and owner not in ('joe','dell','claude','joint') loop
      raise notice '0089: UNRECOGNISED owner left in place: %', rec.owner;
    end loop;
    raise notice '0089: % row(s) carry an owner outside the known set. The '
                 'constraint is NOT VALID so they persist; they need a human '
                 'to say who owns them.', unknown_owner;
  end if;

  raise notice '0089: owners normalised; per-owner open-loop counts follow';
  for rec in select owner, count(*) n from loop_item
              where status='open' and kind='open_loop'
              group by owner order by n desc loop
    raise notice '  %: %', coalesce(rec.owner,'(none)'), rec.n;
  end loop;
end $$;

commit;
