-- 0063_counterparty_observation.sql — the record learns to hold what a counterparty CLAIMED,
-- without ever holding what we think of them.
--
-- THE DEFECT, measured read-only against production 2026-08-02 before a line of this was
-- written. negotiation_round holds 23 columns and every one of them records a NUMBER a side
-- proposed. Not one records a CLAIM a side made about that number. "This is our best and
-- final", "the owner won't go below eighteen", "we walk on Friday" — the whole texture of a
-- negotiation, and the only place any of it can go today is the free-text `note`, where it is
-- unqueryable. The table holds 2 rows on 1 deal (Gulf Coast Pelvic Health), and both of them
-- carry a paragraph of settled terms in `note` for exactly that reason.
--
-- WHY THIS MATTERS RIGHT NOW, WHICH IS TO SAY WHY IT IS WORTH BUILDING ON 2 ROWS. A bluff is
-- only detectable as the DIFFERENCE between a claim and what happened afterwards. If the
-- claim is never captured, the difference is uncomputable forever after — you cannot go back
-- and remember which of forty rounds was called final. The number is recoverable from an
-- email; the claim is not. Capture has to exist before the negotiation, not after it, which
-- is why the schema lands while the table is nearly empty.
--
-- WHAT THIS DELIBERATELY DOES NOT STORE, and it is the whole design constraint. There is no
-- column anywhere in this file for "aggressive", "bluffs", "reasonable", or any other
-- characterisation of a named human being. Joe's ruling, and the same rule his own taught
-- vendor-level doctrine already states: relationship facts are "defined by countable events,
-- never by impression". A claim is an event — it was made, on a date, on a round, and it was
-- either later contradicted by that same side or it was not. The PATTERN ("this one bluffs")
-- is computed at read time in 0064 and stored nowhere.
--
-- ── WHY A CHILD TABLE AND NOT A `claimed_firm` BOOLEAN, WHICH IS WHAT I WAS ASKED FOR ──
-- A boolean cannot carry the class of the claim, and the class is the whole point: a finality
-- claim is falsified by a later concession, an authority claim by a later price below the
-- stated floor, a walk-away claim by the mere existence of a later round. Three different
-- tests. One boolean answers none of them.
-- An enum COLUMN on negotiation_round fails for a different reason, and it is a data reason
-- rather than a taste one: a side routinely makes more than one claim in a single breath
-- ("this is our best and final, the owner won't go below eighteen, and we have another tenant
-- looking"). One column holds one of those three and silently discards the other two. That is
-- the 0045 fault — a slot doing several jobs — arriving pre-built.
-- So: a row per claim, and the vocabulary as ROWS in a ref table, which is this system's
-- standing pattern for anything a human widens (0017; activity_kind, party_link_kind,
-- participant_role, vendor_disposition all live this way). Critically, `falsifiable` is a
-- COLUMN on the vocabulary rather than a list hardcoded in a view — so when the score in 0064
-- excludes unfalsifiable claims it does so by reading the data, not by carrying a copy of the
-- rule. 0052 hardcoded its own kind list into a view and 0053 had to rewrite the view to fix
-- the list; that is the mistake being avoided here.
--
-- ── DEADLINE CLAIMS GET NO ROW, ON PURPOSE ──
-- negotiation_round.expires_on ALREADY records "this offer dies on Friday", and a later round
-- from the same side dated after it already falsifies the deadline. Adding a `deadline` claim
-- row would be a second home for a fact the table already holds — the exact fault 0019(g)
-- refused when it declined to create job_config beside system_config. `deadline` therefore
-- exists in the vocabulary (0064 reports it, and a reader needs to see the class named) but
-- is marked derived=true, and the composite foreign key below makes inserting one IMPOSSIBLE
-- rather than merely discouraged. The guard proves the refusal by attempting it.
--
-- ── THE SUBMARKET TAG SEPARATES LEVERAGE FROM SKILL, AND IT SITS ON THE ROUND ──
-- Joe's confounder, and it is a real one: a landlord in a tight submarket concedes nothing
-- because he does not have to. Scoring that as a hard negotiator credits the market to the
-- man. The tag hangs on the ROUND rather than the DEAL because a submarket demonstrably moves
-- during a negotiation — a competing tenant appears in week three and the same building is a
-- different building — and a deal-level column would freeze the first answer given. Recording
-- it once is enough: 0064 reads the LATEST non-null value on the deal, so nobody has to
-- restate it every round.
-- Three values, not five. A human applies soft / balanced / tight consistently from memory;
-- a five-point scale invites false precision on a judgment nobody actually measures.
--
-- ── WHAT IS NOT ADDED, AND WHY, BECAUSE IT WAS ASKED FOR ──
-- Joe named a second confounder: the counterparty's own client's motivation. There is no
-- column for it here and that is deliberate. "The landlord is motivated" is a characterisation
-- of a third party we never speak to — precisely the class of fact this migration exists to
-- keep out of the record. The falsifiable version of that question is TIME ON MARKET, which
-- the schema already answers through availability.available_on and availability.observed_at
-- (0 rows today, and that emptiness is the honest blocker). Adding a subjective "motivated"
-- flag would be cheaper and wrong.
--
-- ── WHAT THIS BREAKS: NOTHING, AND THAT IS CHECKED RATHER THAN HOPED ──
-- Every column added is nullable and every table added is new. `record-counter` inserts
-- negotiation_round with an explicit column list, so it keeps working untouched.
--
-- THE COMPANION CHANGE IS ALREADY WRITTEN, AND THE TWO HALVES MUST SHIP TOGETHER. The seat
-- that owns mcp-server/src/tools.js has extended `record-counter` with a `submarket_condition`
-- argument and a `claims[]` array, plus validateSubmarket / validateClaimType / require0063.
-- Read against that file, the shapes line up exactly: it inserts
-- `negotiation_claim (round_id, claim_type, stated_floor, stated_floor_basis, quote, note,
-- source, created_by)`, reads `negotiation_claim_type (slug, label, falsifiable, derived,
-- reversal_test)` and `submarket_condition (slug, label, tightness)`, refuses a derived class
-- in the verb so the FK violation arrives as a sentence, and enforces one claim per class per
-- round against the unique key below. Every slug matches: finality / authority / walk_away /
-- competing_interest / deadline, and soft / balanced / tight.
-- That verb is defensive about the order — `require0063()` returns a migration_not_applied
-- error rather than an undefined_column, and submarket_condition only joins the INSERT column
-- list when it was supplied — so either half can land first without breaking. They should
-- still go out together, because until both are live nothing can be logged and 0064's bluff
-- metrics read zero by construction.
--
-- REVERSAL. Three statements, and nothing outside this migration's own objects is touched:
--     drop table if exists negotiation_claim;
--     drop table if exists negotiation_claim_type;
--     alter table negotiation_round drop column if exists submarket_condition;
--     drop table if exists submarket_condition;
-- No existing row is read, updated or deleted anywhere in this file.

begin;

-- ── 1. submarket condition: the leverage confounder, as a vocabulary ─────────────────────
create table if not exists submarket_condition (
  slug      text    primary key,
  label     text    not null,
  tightness smallint not null,     -- -1 favours the tenant/buyer, 0 balanced, +1 favours them
  note      text    not null,
  sort      integer not null unique
);

insert into submarket_condition (slug, label, tightness, note, sort) values
  ('soft',     'Soft — oversupplied',   -1,
   'Vacancy is available and competing. A concession here costs the other side little; '
   'holding firm in a soft market is the version that means something.', 10),
  ('balanced', 'Balanced',               0,
   'Neither side is carried by the market. This is the condition under which a negotiation '
   'score is closest to measuring the negotiator.', 20),
  ('tight',    'Tight — landlord-favoured', 1,
   'Little competing space. A counterparty who concedes nothing here may simply not need to; '
   'that is leverage, and 0064 tags it rather than crediting it as skill.', 30)
on conflict (slug) do nothing;

comment on table submarket_condition is
  'How the submarket stood when a negotiation round was proposed (0063). Exists to keep '
  'LEVERAGE separable from SKILL in v_counterparty_scorecard: a landlord in a tight market '
  'concedes nothing because he need not, and scoring that as toughness credits the market to '
  'the man. Three values on purpose — a human applies soft/balanced/tight consistently from '
  'memory, and five invites false precision on a judgment nobody measures.';
comment on column submarket_condition.tightness is
  'Signed, so it sorts and averages: -1 favours our side (tenant/buyer), 0 balanced, +1 '
  'favours theirs. CARR represents tenants and buyers only, so the sign is unambiguous and '
  'never needs flipping per deal.';

alter table negotiation_round
  add column if not exists submarket_condition text references submarket_condition(slug);

comment on column negotiation_round.submarket_condition is
  'The submarket as it stood when THIS round was proposed (0063). On the round rather than '
  'the deal because a submarket moves mid-negotiation — a competing tenant appears in week '
  'three and the same building is a different building — and a deal-level column freezes the '
  'first answer anyone gave. It need only be recorded once: v_negotiation_deal reads the '
  'LATEST non-null value on the deal. NULL means not recorded, never "balanced".';

-- ── 2. the claim vocabulary, with falsifiability as DATA ─────────────────────────────────
create table if not exists negotiation_claim_type (
  slug          text    primary key,
  label         text    not null,
  falsifiable   boolean not null,
  derived       boolean not null default false,
  reversal_test text    not null,
  sort          integer not null unique,
  -- Redundant against the primary key, and required anyway: a composite foreign key can only
  -- point at columns carrying a unique constraint of exactly that shape. This is the target
  -- that lets negotiation_claim reference the derived=false half of this vocabulary and
  -- nothing else.
  unique (slug, derived)
);

insert into negotiation_claim_type (slug, label, falsifiable, derived, reversal_test, sort) values
  ('finality', 'Best and final', true, false,
   'REVERSED when the same side later files a round improved in our favour on any economic '
   'axis. Re-sending identical numbers is not a reversal.', 10),
  ('authority', 'Authority limit — "the owner will not go below X"', true, false,
   'REVERSED when the same side later proposes a rate better for us than the stated floor '
   '(negotiation_claim.stated_floor, defaulting to the claim round''s own rate).', 20),
  ('walk_away', 'Walk-away — "we are done"', true, false,
   'REVERSED by the existence of ANY later round from the same side. No further test is '
   'needed: continuing to negotiate is the contradiction.', 30),
  ('deadline', 'Deadline — "this dies on Friday"', true, true,
   'REVERSED when the same side files a round dated after the deadline. DERIVED: the deadline '
   'is negotiation_round.expires_on and is NOT logged here. See the header.', 40),
  ('competing_interest', 'Competing interest — "another tenant is looking"', false, false,
   'NOT FALSIFIABLE. We can never observe whether the other tenant existed. Loggable so the '
   'tactic is visible in the history; permanently excluded from every score by falsifiable = '
   'false, which v_counterparty_bluff joins on rather than hardcoding.', 50)
on conflict (slug) do nothing;

comment on table negotiation_claim_type is
  'What a side can CLAIM about its own position, as rows (0063; the 0017 vocabulary-as-rows '
  'pattern). Two columns carry rules that would otherwise be hardcoded in a view: '
  'falsifiable says whether the claim can ever be checked, and derived says the record '
  'already holds it elsewhere. 0052 hardcoded a kind list into a view and 0053 had to rewrite '
  'the view to correct the list — this is that mistake, not repeated.';
comment on column negotiation_claim_type.falsifiable is
  'FALSE means no observation could ever contradict this claim, so it must never move a '
  'score. Exactly one row is false today: competing_interest. v_counterparty_bluff filters on '
  'THIS COLUMN, so marking a future claim type unfalsifiable is enough to keep it out of the '
  'numbers — no view edit, no second copy of the rule.';
comment on column negotiation_claim_type.derived is
  'TRUE means the record already holds this claim on another column and logging it here would '
  'be a second home for one fact. Only deadline, which IS negotiation_round.expires_on. The '
  'composite FK from negotiation_claim makes inserting a derived type impossible; the 0063 '
  'guard proves the refusal by attempting it. Flipping a type from false to true while claim '
  'rows exist is refused by that same FK, which is correct — you cannot retroactively declare '
  'a logged class derived.';

-- ── 3. the claims themselves ─────────────────────────────────────────────────────────────
create table if not exists negotiation_claim (
  id                 uuid primary key default gen_random_uuid(),
  round_id           uuid    not null references negotiation_round(id),
  claim_type         text    not null,
  -- Pinned FALSE by its default and held there by the CHECK below. It exists for one reason:
  -- so the composite FK further down can point at the SUBSET of negotiation_claim_type whose
  -- derived flag is false. This is the declarative way to say "this column may only reference
  -- some rows of that table" — no trigger, and the failure is an ordinary
  -- foreign_key_violation at insert time. (A GENERATED ALWAYS column would express the intent
  -- more tightly; it is not used because Postgres' rules for generated columns inside a
  -- foreign key are narrow enough that a default-plus-CHECK is the version I can guarantee
  -- behaves, and the guard at the bottom of this file proves the behaviour either way.)
  loggable           boolean not null default false,
  stated_floor       numeric(12,2),
  stated_floor_basis text,
  quote              text,
  note               text,
  source             text    not null default 'stated',
  created_at         timestamptz not null default now(),
  created_by         uuid    not null references actor(id),
  unique (round_id, claim_type),
  check (loggable is false),   -- nothing may opt itself into the derived half of the vocabulary
  foreign key (claim_type, loggable) references negotiation_claim_type (slug, derived),
  -- 0001's "no bare numbers" rule, restated: a floor without a basis is not a floor.
  check (stated_floor is null or stated_floor_basis is not null),
  check (stated_floor is null or stated_floor > 0),
  check (stated_floor_basis is null or stated_floor_basis in
           ('usd_sf_yr','usd_sf_mo','usd_mo_gross','usd_yr_gross','usd_total','usd_sf_total'))
);

create index if not exists negotiation_claim_round_idx on negotiation_claim (round_id);
create index if not exists negotiation_claim_type_idx  on negotiation_claim (claim_type);

comment on table negotiation_claim is
  'One row per claim a side made ABOUT its own position on a given round (0063) — "best and '
  'final", "the owner will not go below X", "we walk Friday". An OBSERVATION, never a '
  'characterisation: nothing here says what anyone is like, only what they said and when. '
  'Whether a claim was later contradicted is computed in v_counterparty_bluff from the rounds '
  'that followed, and is stored nowhere. A round may carry several claims, which is why this '
  'is a table and not a column — a side routinely makes three at once and a single enum '
  'column would keep one and discard two.';
comment on column negotiation_claim.claim_type is
  'References negotiation_claim_type, but only its derived=false rows: the composite FK '
  '(claim_type, loggable) forbids claim_type = ''deadline'' because a deadline already lives '
  'on negotiation_round.expires_on and two homes for one fact is the 0045 fault.';
comment on column negotiation_claim.stated_floor is
  'The number named in an authority claim, when it differs from the round''s own rate — "the '
  'owner will not go below 18" while offering 19. NULL means the claim was about the round''s '
  'own position, and v_counterparty_bluff falls back to that. Never a guess.';
comment on column negotiation_claim.quote is
  'Their words, as close to verbatim as was heard. Evidence for a reader, never parsed: no '
  'view reads this column and no score may ever depend on its text.';

-- ── 4. grants, mirroring negotiation_round and party_link_kind ───────────────────────────
grant select on submarket_condition, negotiation_claim_type to carr_reader, carr_writer;
grant select, insert, update on negotiation_claim to carr_writer;
grant select on negotiation_claim to carr_reader;

-- ── guards BEFORE commit, so a failure rolls the whole thing back ────────────────────────
-- (0043's lesson, stated correctly and placed correctly: a guard after `commit;` is a report,
-- because migrate.py has already ended the transaction. 0047, 0052 and 0059 do it here.)
do $$
declare
  n int; r uuid; a uuid; fk text; rounds_before int; rounds_after int;
begin
  -- (1) the vocabularies landed whole.
  select count(*) into n from submarket_condition;
  if n <> 3 then raise exception 'expected 3 submarket_condition rows, got %', n; end if;
  if (select count(*) from submarket_condition where tightness not between -1 and 1) > 0 then
    raise exception 'a submarket_condition row carries a tightness outside -1..1';
  end if;

  select count(*) into n from negotiation_claim_type;
  if n <> 5 then raise exception 'expected 5 negotiation_claim_type rows, got %', n; end if;
  select count(*) into n from negotiation_claim_type where falsifiable;
  if n <> 4 then raise exception 'expected 4 falsifiable claim types, got %', n; end if;
  if not exists (select 1 from negotiation_claim_type
                  where slug = 'competing_interest' and falsifiable = false) then
    raise exception 'competing_interest must be falsifiable=false — it is the one claim no '
                    'observation can ever contradict, and a score that eats it is a score '
                    'that rewards saying it';
  end if;
  if (select count(*) from negotiation_claim_type where derived) <> 1
     or not exists (select 1 from negotiation_claim_type where slug='deadline' and derived) then
    raise exception 'deadline must be the one and only derived claim type';
  end if;

  -- (2) THE COMPOSITE FK EXISTS AND IS SHAPED RIGHT. Asserted from the catalog, because the
  -- behavioural test below cannot run on an empty negotiation_round.
  select pg_get_constraintdef(oid) into fk
    from pg_constraint
   where conrelid = 'negotiation_claim'::regclass and contype = 'f'
     and pg_get_constraintdef(oid) like '%negotiation_claim_type%';
  if fk is null or fk not like '%loggable%' or fk not like '%derived%' then
    raise exception 'the (claim_type, loggable) -> negotiation_claim_type(slug, derived) FK is '
                    'missing or reshaped: %. Without it a deadline can be logged twice',
                    coalesce(fk, '<none>');
  end if;

  -- (3) THE REFUSAL, PROVEN RATHER THAN ASSERTED. Both inserts below run inside their own
  -- subtransaction and are rolled back either way; nothing persists from this block.
  select id into r from negotiation_round order by created_at limit 1;
  select id into a from actor where slug = 'system';
  if r is not null and a is not null then
    begin
      insert into negotiation_claim (round_id, claim_type, created_by) values (r, 'deadline', a);
      raise exception 'GUARD FAILED: negotiation_claim accepted claim_type=''deadline''. A '
                      'deadline is already negotiation_round.expires_on; logging it here puts '
                      'one fact in two places and lets the two disagree';
    exception when foreign_key_violation then
      null;  -- correct: the composite FK refused it
    end;

    begin
      insert into negotiation_claim (round_id, claim_type, created_by) values (r, 'finality', a);
      raise exception using errcode = 'ZZ999', message = 'sentinel';
    exception
      when foreign_key_violation then
        raise exception 'GUARD FAILED: negotiation_claim refused claim_type=''finality'', '
                        'which is loggable. The composite FK is too narrow and NOTHING can be '
                        'recorded';
      when sqlstate 'ZZ999' then
        null;  -- correct: a loggable type was accepted, then rolled back
    end;
  end if;

  -- (4) NOT ONE EXISTING ROW MOVED. This migration is additive and that is the claim.
  select count(*) into rounds_after from negotiation_round;
  select count(*) into rounds_before from negotiation_round where submarket_condition is null;
  if rounds_before <> rounds_after then
    raise exception 'a negotiation_round took a submarket_condition during the migration — '
                    'this file records nothing and must invent nothing';
  end if;
  if (select count(*) from negotiation_claim) <> 0 then
    raise exception 'negotiation_claim is not empty after a migration that inserts no claims';
  end if;

  raise notice 'counterparty observation live. % negotiation_round row(s), all with a NULL '
               'submarket_condition (nothing backfilled, by design). 5 claim types: 4 '
               'falsifiable, 1 not (competing_interest), 1 derived and now UNLOGGABLE '
               '(deadline = expires_on). negotiation_claim holds 0 rows and WILL hold 0 rows '
               'until a verb can write it — 0064''s bluff metrics read zero until then, and '
               'that is the correct behaviour, not a fault.', rounds_after;
end $$;

commit;
