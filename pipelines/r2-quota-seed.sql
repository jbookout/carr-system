-- r2-quota-seed.sql — ORDER 20(b), Joe's requirement of 2026-07-31 evening.
--
-- WHAT THIS IS. The cap on the R2 document archive, written into the record as
-- data. Joe's words: a HARD self-enforced cap, not a billing alert. A billing
-- alert tells you after the money starts. This refuses before it does.
--
-- WHY 8 AND NOT 10. Cloudflare's R2 free tier is 10 GB of stored objects. The
-- cap sits under it so that the system stops on its own terms with room to
-- spare, rather than at the exact edge where a rounding difference between our
-- byte count and Cloudflare's is the difference between free and billed.
--
-- WHAT ENFORCES IT. `lib/r2_archive.py`, read by `pipelines/prepare_document.py`
-- and `pipelines/backfill_document_attachments.py`. The pipeline is the bucket's
-- SOLE WRITER: no Worker route uploads, no public access exists, and nothing
-- else puts objects there. That is what makes the local usage ledger
-- (`out/r2-usage.json`) trustworthy enough to gate on, and it is a property to
-- preserve rather than a coincidence to rely on.
--
-- THE CODE DEFAULTS TO 8 GB WHEN THIS ROW IS ABSENT, on purpose: a missing
-- config row degrades to more careful than configured, never to unbounded. So
-- this seed does not switch the guard on. The guard is already on. This row is
-- what lets Joe change the number without a deploy, which is the whole reason
-- system_config exists (0019's one-config-home ruling: thresholds live here,
-- never in a second config table).
--
-- IDEMPOTENT BY CONSTRUCTION. system_config.key is the primary key, so the
-- insert guards on conflict; a second run inserts nothing and changes nothing.
--
-- HOW TO RUN IT (Joe's tap; an agent has no writer credential and `carr_jobs`
-- deliberately holds no write on system_config):
--   export PATH="/opt/homebrew/opt/libpq/bin:/opt/homebrew/bin:$PATH"
--   cd ~/carr-system
--   export DATABASE_URL="$(cd mcp-server && npx -y neonctl connection-string production \
--     --project-id steep-field-48688294 --org-id org-dry-dew-75906281 \
--     --role-name neondb_owner --database-name neondb)"
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f pipelines/r2-quota-seed.sql
--
-- EXPECTED, EXACTLY (verified on Neon branch `rehearse-order20`, a full-data
-- copy of production at 21 migrations, run twice):
--   BEGIN
--   INSERT 0 1
--   NOTICE:  r2 quota seeded: 8 GB cap, 1 key in the namespace
--   DO
--   COMMIT
-- then a one-row table reading r2.quota_gb = 8. A SECOND run prints INSERT 0 0
-- and the identical NOTICE and table. ANYTHING ELSE MEANS STOP.
--
-- TO CHANGE THE CAP LATER, it is one sentence and no deploy:
--   update system_config set value = '12'::jsonb, updated_at = now()
--    where key = 'r2.quota_gb';
-- Raising it past 10 leaves the free tier. That is a decision, not a typo, so it
-- is left to a human hand rather than wired to anything automatic.

begin;

insert into system_config (key, value, note) values
  ('r2.quota_gb', '8'::jsonb,
   'ORDER 20. HARD self-enforced cap on the carr-documents R2 archive, in '
   'gigabytes. The pipeline is the bucket''s sole writer and checks its usage '
   'ledger before every upload; an upload that would cross this number is '
   'REFUSED, nothing is deleted to make room, the OneDrive copy stands, and the '
   'archive copy is recorded as owed on the document row. 8 sits under '
   'Cloudflare''s 10 GB free tier deliberately. lib/r2_archive.py defaults to 8 '
   'when this row is absent, so the guard holds even with no configuration.')
on conflict (key) do nothing;

do $$
declare n int; v numeric;
begin
  select count(*) into n from system_config where key like 'r2.%';
  select (value #>> '{}')::numeric into v from system_config where key = 'r2.quota_gb';
  if v is null then
    raise exception 'r2.quota_gb did not land';
  end if;
  -- A cap above the free tier is a billing decision. It is allowed, because it
  -- is Joe's to make, but it is never allowed to happen quietly.
  if v > 10 then
    raise warning 'r2.quota_gb is % GB, which is ABOVE Cloudflare''s 10 GB free tier. '
                  'Storage past 10 GB is billed. Set deliberately or not at all.', v;
  end if;
  raise notice 'r2 quota seeded: % GB cap, % key in the namespace', v, n;
end $$;

commit;

select key, value, left(note, 60) || '...' as note from system_config where key like 'r2.%';
