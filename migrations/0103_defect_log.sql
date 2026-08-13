-- 0103_defect_log.sql
-- THE DEFECT LOG (loop #185) — the system's memory of its own failures.
--
-- THE GAP. The record layer holds decisions, rules, loops, findings, deprecations and
-- health. It holds NOTHING about the times it was wrong. Every safeguard built so far is
-- PROSPECTIVE — a hook, a gate, a registry, each one guessing in advance at what will go
-- wrong. A defect log is the only mechanism that gets better as failures accumulate
-- instead of requiring someone to predict them.
--
-- THE EVIDENCE, from the session that filed the loop. On 2026-08-04 one session made six
-- errors of ONE identifiable class — reading a dated artifact as present state — and Joe
-- caught every one himself, at 99% of his token budget. Nothing in the system knew any of
-- it the next day. The next session booted believing it was reliable and was free to
-- repeat the night.
--
-- WHY ITS OWN TABLE, AND NOT A record_flag SUBJECT KIND. The loop left this open as (a) a
-- new subject kind on record_flag, the 0066 pattern, (b) its own table, or (c) something
-- the calibration ledger becomes. It is (b), and the reason is the loop's own acceptance
-- criterion: "the entry must be STRUCTURED — what was claimed, what was true, which source
-- went unread, which rule it violated — so a session can be told at start 'this class has
-- failed N times'." record_flag.value is jsonb, so every one of those fields would be
-- optional by construction and a defect filed without `actual` would look exactly like one
-- filed with it. The four fields ARE the mechanism; NOT NULL on the two that carry the
-- contradiction is the whole difference between a defect record and a note. 0066 and 0101
-- remain the right pattern for a FINDING about a thing; a defect is not a finding about a
-- thing, it is a claim the system made and the truth it collided with.
--
-- DECIDED HERE RATHER THAN PARKED. Rule e065aa82: internal decisions are the system's to
-- make and record, not to park on Joe. This is an architecture choice inside the record
-- layer with no business consequence, and the loop had already sat open since 2026-08-05
-- waiting for someone to pick one of three.

begin;

create table defect (
  id             uuid primary key default gen_random_uuid(),
  occurred_on    date not null default current_date,
  -- The CLASS is what makes this accumulate into something usable. Free text, because
  -- the classes are not known in advance and a fixed vocabulary would force every new
  -- failure into an old bucket — but normalised, so 'Stale Artifact' and
  -- 'stale-artifact' cannot become two classes that each look rare.
  defect_class   text not null,
  claimed        text not null,   -- what the session asserted
  actual         text not null,   -- what was true
  source_unread  text,            -- the artifact that would have shown it, unopened
  rule_violated  text,            -- rule id (short or full) or a short name
  detected_by    text not null,   -- who caught it
  session_key    text,
  cost_note      text,            -- what it cost: tokens, a wrong deliverable, a partner's evening
  created_at     timestamptz not null default now(),
  created_by     uuid references actor(id),
  constraint defect_class_shape
    check (defect_class = lower(btrim(defect_class))
           and defect_class <> '' and defect_class !~ '\s\s'),
  -- WHO CAUGHT IT is the single most diagnostic field in the table, which is why it is
  -- NOT NULL and a closed vocabulary. A system whose defects are all detected_by='human'
  -- has no working self-check, and that fact is invisible unless it is counted.
  constraint defect_detected_by_check
    check (detected_by in ('human','self','gate','check','peer_review','downstream')),
  -- A defect must actually state a contradiction. Two fields saying the same thing is a
  -- note; the pair is what makes it reviewable later.
  constraint defect_states_a_contradiction
    check (btrim(claimed) <> '' and btrim(actual) <> ''
           and lower(btrim(claimed)) <> lower(btrim(actual)))
);

create index defect_class_idx on defect (defect_class, occurred_on desc);

comment on table defect is
  'The system''s memory of its own failures (0103, loop #185). One row = one claim the '
  'system made that was not true, with what was true beside it. Every prospective '
  'safeguard in this repo guesses at future failure; this is the only retrospective one. '
  'detected_by is NOT NULL and closed-vocabulary on purpose: a log where every row reads '
  'human is a log saying the self-checks do not work, and that is only visible if counted.';

grant select on defect to carr_reader, carr_writer, carr_exporter;
grant insert on defect to carr_writer;

-- ── THE READ SIDE ────────────────────────────────────────────────────────────────────────
create or replace view v_defect as
select d.id, d.occurred_on, d.defect_class, d.claimed, d.actual,
       d.source_unread, d.rule_violated, d.detected_by, d.session_key, d.cost_note,
       r.statement as rule_statement,
       a.slug      as recorded_by,
       d.created_at
  from defect d
  left join actor a on a.id = d.created_by
  -- The rule pointer is stored as text because a short id is what a session can quote
  -- (rule 4e104d4c's lesson about the 8-character form). Resolve it here rather than
  -- forcing every reader to.
  left join rule r on r.id::text = d.rule_violated
                   or left(r.id::text, 8) = lower(btrim(coalesce(d.rule_violated, '')));

comment on view v_defect is
  'Every defect with its rule statement and author resolved (0103). carr_reader holds no '
  'grant on any base table, so this is the only way a read session sees the log at all.';

grant select on v_defect to carr_reader, carr_writer, carr_exporter;

-- THE SURFACE THE LOOP ACTUALLY ASKED FOR: "a session can be told at start 'this class has
-- failed N times, here are the artifacts that were stale'". That sentence is this view.
create or replace view v_defect_class as
select defect_class,
       count(*)::int                                              as occurrences,
       min(occurred_on)                                           as first_seen,
       max(occurred_on)                                           as last_seen,
       count(*) filter (where detected_by = 'human')::int          as caught_by_human,
       -- Deduplicated and capped: a class with forty stale sources is not answered by
       -- listing forty, it is answered by "these are the ones that keep being missed".
       (array_agg(distinct source_unread) filter (where source_unread is not null))[1:5]
                                                                   as sources_unread,
       (array_agg(distinct rule_violated) filter (where rule_violated is not null))[1:5]
                                                                   as rules_violated
  from defect
 group by defect_class;

comment on view v_defect_class is
  'One row per defect class: how many times, when first and last, how many the HUMAN had '
  'to catch, and the artifacts that keep going unread (0103, loop #185). This is what a '
  'session is handed at start instead of being handed the prose rules and trusted.';

grant select on v_defect_class to carr_reader, carr_writer, carr_exporter;

-- ── SEED: THE SIX ERRORS THAT CAUSED THIS LOOP ───────────────────────────────────────────
-- A log that starts empty proves nothing and teaches nothing. Rule bbffc139 already records
-- what an empty mechanism is worth: "This rule produced ZERO entries across the weeks it was
-- active." These six are documented verbatim in loop #185's body, they are the reason the
-- table exists, and every one of them was caught by Joe rather than by the system. Seeding
-- them makes the very first read of v_defect_class true instead of hypothetical.
insert into defect (occurred_on, defect_class, claimed, actual, source_unread,
                    rule_violated, detected_by, session_key, cost_note, created_by)
select '2026-08-04'::date, 'dated-artifact-read-as-present-state',
       v.claimed, v.actual, v.source_unread, v.rule_violated, 'human',
       '2026-08-04-claude', v.cost_note, a.id
  from actor a
  cross join (values
    ('ORDER 28 was never run',
     'its execution log sat inline in the same file that was grepped',
     'the ORDER 28 file below the section headings that were grepped',
     'fa217e48',
     'Joe caught it himself at 99% of his token budget'),
    ('dna-protocol and lead-system are a LIVE HAZARD',
     'both had been corrected on 2026-08-03; neither file was opened before the claim',
     'dna-protocol.md and lead-system.md, unopened',
     'fa217e48',
     'a false hazard reported to the partner'),
    ('the lead board is blocked on ORDER 26 and parked',
     'migration 0025 had shipped and applied the fix on 2026-07-31',
     'migration 0025, and the migration log; a Python error string was quoted instead',
     'a9ecd5b4',
     'a shipped fix reported as a blocker'),
    ('records mode is live, per a test of _records_available()',
     'the board gates on pool_reach(), which was never tested',
     'the board source, where the actual gate is',
     'a9ecd5b4',
     'a success signal derived from the wrong function'),
    ('context must stay on Drive, per ORDER 28 ratification',
     'that ratification was scoped to RECORD exports and settled nothing about the doctrine tier',
     'ORDER 28, read past the quoted line',
     'fa217e48',
     'a scope error presented to the partner as settled doctrine'),
    ('the router repoint is three rows',
     '36 lines of an 87-line INDEX.md had been read',
     'INDEX.md, lines 37-87',
     'fa217e48',
     'a count asserted from a partial read')
  ) as v(claimed, actual, source_unread, rule_violated, cost_note)
 where a.slug = 'system';

-- ── DONE-TEST ────────────────────────────────────────────────────────────────────────────
do $$
declare n int; human int; cls int;
begin
  select occurrences, caught_by_human into n, human
    from v_defect_class where defect_class = 'dated-artifact-read-as-present-state';
  if n is distinct from 6 then
    raise exception '0103 done-test: seeded class shows % occurrences, expected 6', n;
  end if;
  if human is distinct from 6 then
    raise exception '0103 done-test: seeded class shows % caught by human, expected 6', human;
  end if;
  select count(*) into cls from v_defect_class;
  if cls <> 1 then
    raise exception '0103 done-test: expected exactly one class, found %', cls;
  end if;
  -- The rule join must actually resolve a short id, or rule_statement is dead weight.
  if not exists (select 1 from v_defect where rule_violated = 'fa217e48' and rule_statement is not null) then
    raise exception '0103 done-test: the short-form rule id did not resolve to a rule statement';
  end if;
  -- And the contradiction constraint must actually bite.
  begin
    insert into defect (defect_class, claimed, actual, detected_by)
      values ('probe', 'same', 'SAME', 'self');
    raise exception '0103 done-test: a defect whose claim equals its actual was accepted';
  exception when check_violation then null;
  end;
  raise notice '0103 done-test ok — 6 seeded defects in 1 class, all human-caught, rule join live';
end $$;

commit;
