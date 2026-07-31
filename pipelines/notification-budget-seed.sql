-- notification-budget-seed.sql — ORDER 16(c), wave2-design §2g.
--
-- WHAT THIS IS. The budget stating what MAY interrupt a human, written into the
-- record as data. ENFORCEMENT IS WAVE 3's push layer; this order builds no push
-- path at all, so today these rows are a decision made explicit and nothing else.
-- That is the point: when the push layer arrives it reads a rule that was ruled
-- deliberately, rather than inheriting whatever the first implementer felt like.
--
-- WHERE IT LIVES, AND WHY NOT SOMEWHERE NEW. 0019's one-config-home ruling:
-- thresholds and settings live in system_config, never in a second config table.
-- 0019 seeded `notification_budget.settings` as an empty object with its own
-- instruction for what comes next, quoted verbatim from its note:
--     "First real budget lands as its own notification_budget.<setting> key."
-- So these are dotted sibling keys, exactly as the learning and promotion
-- namespaces did it. The empty `.settings` row stays, untouched, as the
-- namespace anchor.
--
-- IDEMPOTENT BY CONSTRUCTION. system_config.key is the primary key, so every
-- insert guards on conflict and a second run inserts nothing and changes
-- nothing. The check at the bottom fails the whole file unless the namespace
-- ends at exactly five keys. Re-runnable is not a claim here, it is enforced.
--
-- HOW TO RUN IT (Joe's tap; an agent is blocked from production writes):
--   export PATH="/opt/homebrew/opt/libpq/bin:/opt/homebrew/bin:$PATH"
--   cd ~/carr-system
--   export DATABASE_URL="$(cd mcp-server && npx -y neonctl connection-string production \
--     --project-id steep-field-48688294 --org-id org-dry-dew-75906281 \
--     --role-name neondb_owner --database-name neondb)"
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f pipelines/notification-budget-seed.sql
--
-- EXPECTED, EXACTLY (verified on Neon branch `rehearse-order16`, a full-data copy
-- of production at 20 migrations, run twice):
--   BEGIN
--   INSERT 0 4                  <- ONE statement inserting four rows, not four lines
--   NOTICE:  notification budget seeded: 4 rules, 5 keys in the namespace
--   DO
--   COMMIT
-- then a five-row table: active_search_match, client_reply, deal_critical_date,
-- settings (the empty 0019 anchor, unchanged), signed_document. A SECOND run
-- prints `INSERT 0 0` and the identical NOTICE and table.
-- ANYTHING ELSE MEANS STOP.

begin;

-- ── the four things that MAY interrupt ───────────────────────────────────────
-- Each is a fact that changes what the person should do in the next hour. That
-- is the whole test, and it is the reason the list is four items and not forty:
-- a budget everything passes is not a budget.
--
-- Quiet hours and the weekends-off rule bind on top of every row here. Joe's
-- Jul 11 ruling is that Saturday and Sunday are not workdays for either partner
-- and Monday absorbs the backlog, so `weekend` below is false everywhere except
-- the one class where waiting until Monday can cost the client the deal.

insert into system_config (key, value, note) values

  ('notification_budget.signed_document',
   '{"may_interrupt": true, "quiet_hours": false, "weekend": true, "channel": "push"}'::jsonb,
   'ORDER 16(c). A document came back signed. It changes what happens next on a '
   'real deal and it is rare, so it earns the interruption. The one class that '
   'survives the weekend rule: a signature waiting until Monday can cost the '
   'client the space.'),

  ('notification_budget.active_search_match',
   '{"may_interrupt": true, "quiet_hours": false, "weekend": false, "channel": "push"}'::jsonb,
   'ORDER 16(c). A new availability matched an OPEN space search, meaning a '
   'client who is actively looking. Space moves in days, so same-day matters; '
   'the matcher digest is where a match with no open search goes instead.'),

  ('notification_budget.client_reply',
   '{"may_interrupt": true, "quiet_hours": false, "weekend": false, "channel": "push"}'::jsonb,
   'ORDER 16(c). A client or prospect replied. The person answering is the whole '
   'product; a reply that sits for a day undoes the response time we sell.'),

  ('notification_budget.deal_critical_date',
   '{"may_interrupt": true, "quiet_hours": false, "weekend": false, "channel": "push", "lead_days": 3}'::jsonb,
   'ORDER 16(c). A critical date on a live deal is inside its lead time. Missing '
   'a diligence or option deadline is the one failure a tenant rep cannot walk '
   'back, so it interrupts, and it does so three days early rather than on the day.')

on conflict (key) do nothing;

-- ── the check ────────────────────────────────────────────────────────────────
-- Everything not named above is silent, and that default is the load-bearing
-- half of a budget. It is written as a check rather than a comment so a future
-- addition has to be deliberate: adding a fifth interrupting class fails this
-- file until someone raises the number on purpose.
do $$
declare n int;
begin
  select count(*) into n from system_config where key like 'notification_budget.%';
  if n <> 5 then
    raise exception 'expected 5 keys in the notification_budget namespace '
                    '(4 rules + the empty .settings anchor from 0019), found %', n;
  end if;

  select count(*) into n from system_config
   where key like 'notification_budget.%' and key <> 'notification_budget.settings'
     and (value->>'may_interrupt')::boolean is true;
  if n <> 4 then
    raise exception 'expected exactly 4 interrupting classes, found %', n;
  end if;

  -- The weekend carve-out is ONE class. If this ever counts higher, the
  -- weekends-off rule has been eroded a row at a time, which is how it would go.
  select count(*) into n from system_config
   where key like 'notification_budget.%' and (value->>'weekend')::boolean is true;
  if n <> 1 then
    raise exception 'weekends-off allows exactly 1 carve-out (a signed document), found %', n;
  end if;

  raise notice 'notification budget seeded: 4 rules, 5 keys in the namespace';
end $$;

commit;

select key,
       value->>'may_interrupt' as interrupts,
       value->>'weekend'       as weekends
  from system_config
 where key like 'notification_budget.%'
 order by key;
