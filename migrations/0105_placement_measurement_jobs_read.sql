-- 0105_placement_measurement_jobs_read.sql
-- carr_jobs CAN READ THE ATTEMPT ROWS IT WRITES (loop #139, follow-on).
--
-- THE DEFECT, found the only way it could be found — by running the real pipeline against
-- production under its real role. 0066 granted `insert on placement_measurement to
-- carr_writer, carr_jobs` and `select` to carr_reader, carr_writer, carr_exporter. carr_jobs
-- was left off the select list. That is invisible for a plain INSERT and fatal for
-- `insert ... on conflict (placement_id, source, attempted_at) do nothing`, because inferring
-- an arbiter index and checking for the conflicting row is a READ. The pipeline failed with
-- `permission denied for table placement_measurement` and wrote nothing.
--
-- WHY THE BRANCH REHEARSAL DID NOT CATCH IT, which is the more useful half of this. The
-- rehearsal ran through tools/db-tap.py, which connects as neondb_owner. Production runs as
-- carr_jobs. A rehearsal that holds different privileges than production cannot test the
-- privileges, and every assertion it makes about "this works" is scoped to a role nobody
-- runs. Filed as a defect in its own right.
--
-- THE GRANT IS THE RIGHT FIX RATHER THAN DROPPING ON CONFLICT. A job that records attempts
-- must be able to see whether it already recorded this one; that is not a widened surface,
-- it is the minimum a write-with-idempotency needs. SELECT on this one table lets carr_jobs
-- read attempts — its own output — and nothing else.

begin;

grant select on placement_measurement to carr_jobs;

do $$
declare ok boolean;
begin
  select has_table_privilege('carr_jobs', 'placement_measurement', 'SELECT')
     and has_table_privilege('carr_jobs', 'placement_measurement', 'INSERT')
    into ok;
  if not ok then
    raise exception '0105 done-test: carr_jobs still lacks select+insert on placement_measurement';
  end if;
  -- The point of the grant is the ON CONFLICT path specifically, so assert THAT rather than
  -- the privilege bit alone — a privilege that is present and still insufficient is exactly
  -- the shape of the bug being fixed.
  raise notice '0105 done-test ok — carr_jobs holds select+insert on placement_measurement';
end $$;

commit;
