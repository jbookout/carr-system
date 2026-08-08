-- 0077_reader_actor_columns.sql — let the reader path resolve WHO is asking,
-- and nothing else. Caught by rehearsing doctrine-index as app_reader (the
-- 0076 lesson applied: rehearse as the role that will run it): carr_reader is
-- views-only by design, so the doctrine read verbs' personal-visibility filter
-- could not resolve the caller's actor id and every read verb died on
-- "permission denied for table actor".
--
-- The grant is COLUMN-SCOPED on purpose: id and slug only. The actor table
-- also carries email and phone; the reader role has no business with either,
-- and a views-only posture stays intact everywhere else.

begin;

grant select (id, slug) on actor to carr_reader;

do $$
declare n int;
begin
  select count(*) into n from information_schema.column_privileges
   where grantee = 'carr_reader' and table_name = 'actor'
     and column_name in ('id','slug') and privilege_type = 'SELECT';
  if n < 2 then
    raise exception 'carr_reader actor column grants incomplete (% of 2)', n;
  end if;
end $$;

commit;
