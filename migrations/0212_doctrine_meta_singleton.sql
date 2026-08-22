-- 0212_doctrine_meta_singleton.sql
-- Restore the generation singleton when an already-ledgered 0075 bootstrap
-- was absent from an older schema snapshot.  Never reset a live generation.

begin;

insert into public.doctrine_meta (id, generation)
values (1, 0)
on conflict (id) do nothing;

commit;
