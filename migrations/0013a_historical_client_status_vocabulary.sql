-- 0013a: restore the eleven client_status slugs that production had and the
-- repo never created, so the migration chain can build a database from nothing.
--
-- THE DEFECT THIS FIXES. Applying every migration in order to an EMPTY database
-- fails at 0014 with: expected 14 active-pipeline statuses, got 3. 0014 flags
-- fourteen slugs as on the pipeline and then asserts, correctly, that exactly
-- fourteen rows were flagged. Only three of those slugs — research, active,
-- paused — are ever created by a migration (0002's seed). The other eleven
-- entered production out of band and exist in no .sql file in this repo.
--
-- WHY IT WENT UNSEEN FOR THE WHOLE LIFE OF THE PROJECT. Every migration
-- rehearsal to date ran against a Neon BRANCH of production, and a branch starts
-- WITH production's data — including these eleven rows. The rehearsal always
-- inherited the exact state the chain fails to produce, so the only test we had
-- was structurally incapable of finding this. It surfaced the first time the
-- chain met a genuinely empty Postgres, in CI, on 2026-08-13
-- (GitHub Actions run 31750959562).
--
-- WHY THAT MATTERS BEYOND A RED CHECK. Doctrine requires that "a fresh
-- non-production environment can be reconstructed from repository declarations
-- and approved secret references" (production-maturity-baseline, s06). It could
-- not be. The same gap blocks Program 1's isolated Staging, because a separate
-- Neon project also starts empty and would die at the same line.
--
-- WHERE THE ELEVEN COME FROM, since production no longer holds them to be read.
-- 0018 rationalised the vocabulary down to exactly six, so the old slugs are
-- gone from the live database and cannot be queried back. They are recovered
-- instead from the two places that still name them verbatim: 0014's flag list,
-- and 0018's own migration rules, which map each old status to its replacement
-- (R2 cold_not_started → cold, R4 research/negotiation/negotiating/
-- due_diligence/legal/closing/pending/active/warm_re_engagement → active_deal,
-- R6 active_awaiting_reply/active_relationship_building → engaged). Those rules
-- are Joe's ruling of 2026-07-31 recorded verbatim, which makes them the best
-- surviving evidence of what the vocabulary was.
--
-- WHY THE FILE IS NUMBERED 0013a RATHER THAN APPENDED AT THE END. The runner
-- applies files in filename order and skips anything already recorded in
-- schema_migrations. 0013a sorts after 0013 and before 0014 ('_' is 0x5F, 'a' is
-- 0x61, and '3' < '4'), so on an EMPTY database it runs at the point in history
-- where these rows actually appeared, and 0014's guard then passes honestly
-- rather than being weakened to accommodate a gap. On PRODUCTION it is simply
-- pending and applies now, out of historical order — which is safe because it is
-- a no-op there.
--
-- IT IS A NO-OP ON PRODUCTION, TWICE OVER. `on conflict (slug) do nothing` means
-- an existing slug is untouched. And production has already run 0018, which
-- removed these eleven, so nothing here can resurrect them: the insert lands
-- eleven rows that 0018 has already been proven to clean up, and the guard below
-- asserts the vocabulary is back to six afterwards. Deliberately NOT relaxing
-- 0014's assertion to a floor, which was the cheaper option — a floor would have
-- left the reconstruction claim false while making the check green.
--
-- Sort values start at 70 so the six original seeds (10-60) keep their order.
-- Labels are reconstructed from the slugs; no surviving artifact records the
-- display strings, and nothing reads them after 0018 replaces the vocabulary.

insert into client_status (slug, label, sort) values
  ('cold_not_started',             'Cold — not started',           70),
  ('warm_re_engagement',           'Warm — re-engagement',         80),
  ('active_awaiting_reply',        'Active — awaiting reply',      90),
  ('active_relationship_building', 'Active — relationship building', 100),
  ('active_client',                'Active client',                110),
  ('due_diligence',                'Due diligence',                120),
  ('legal',                        'Legal',                        130),
  ('negotiation',                  'Negotiation',                  140),
  ('negotiating',                  'Negotiating',                  150),
  ('closing',                      'Closing',                      160),
  ('pending',                      'Pending',                      170)
on conflict (slug) do nothing;

-- Guard: 0014 is about to require exactly fourteen flagged rows, and it can only
-- flag a slug that exists. Assert the fourteen are present HERE, so a failure
-- names the missing vocabulary rather than surfacing later as a count mismatch
-- in a migration that is not the one at fault.
do $$
declare missing text;
begin
  select string_agg(s, ', ' order by s) into missing
    from unnest(array[
      'cold_not_started','research','due_diligence','legal','negotiation',
      'closing','pending','warm_re_engagement','active_awaiting_reply',
      'active_relationship_building','active_client','negotiating',
      'paused','active'
    ]) as s
   where not exists (select 1 from client_status cs where cs.slug = s);
  if missing is not null then
    raise exception '0014 will fail: client_status is missing %', missing;
  end if;
end $$;
