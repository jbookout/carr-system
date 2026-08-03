-- 0064_counterparty_scorecard.sql — a threat rating for the people on the other side of the
-- table, computed entirely at read time, that refuses to produce a number it has not earned.
--
-- FULL REASONING, THE MEASUREMENTS, AND THE FORMULA ARGUMENT: 0064_counterparty_scorecard_spec.md,
-- beside this file. This header carries the part a reader needs in order to judge the SQL.
--
-- WHAT THIS IS FOR. Joe walks into a negotiation against a listing agent and wants one
-- question answered: how hard is this person to move, and WHERE. 10 is hardest. It is a
-- THREAT RATING, not a performance review — a 9 is not a bad person, they are an expensive
-- afternoon. And the rating alone is nearly useless without the second half: "an 8 who holds
-- rate and concedes TI" is the actual scouting report, so the category profile is a
-- first-class output of this view and not a footnote on it.
--
-- ── WHAT IT RETURNS TODAY, STATED FIRST SO NOBODY IS SURPRISED ──
-- Measured read-only against production 2026-08-02, before and after writing every line here:
--   negotiation_round                                    2 rows, on 1 deal
--   deals with any negotiation recorded at all           1 of 40
--   live listing_side participants carrying a party      1 (Mehdi, on Gulf Coast Pelvic Health)
--   negotiation_claim                                    0 rows (0063 creates it empty)
--   observed counters by OUR side, anywhere in the book  ZERO
-- v_counterparty_scorecard therefore returns exactly ONE row today, and that row carries
-- n = 0, n_band = 'unrated', a NULL threat_rating, a NULL hardness_absolute, an empty category
-- profile, and the sentence "our side never countered on any recorded deal". That is not
-- degradation. That is the view working.
--
-- ── THE EXCLUSION RULE, WHICH IS THE MOST IMPORTANT LINE IN THIS FILE ──
-- The one negotiation in the record is a ROUND-1 ACCEPTANCE: the landlord proposed $17.00/SF
-- and the tenant took it, same day, same numbers on every economic axis. Scored naively, that
-- listing agent never conceded a dollar and rates a perfect 10 for it — when in fact he was
-- never tested. He is not hard to move. Nobody pushed.
-- So: A DEAL WHERE OUR SIDE NEVER COUNTERED CONTRIBUTES NOTHING. Not a weaker signal, not a
-- discounted one: nothing. It does not enter n, it does not enter any average, it does not
-- enter the field the curve is drawn over. It is reported, with its reason, so the exclusion
-- is visible rather than silent.
-- "Countered" is defined structurally rather than by a flag nobody will set: our side filed a
-- round, there was already a position on the table from their side, and ours DIFFERS from it
-- on at least one of rate / TI / free rent / term. Free-text escalator and opex notes are
-- deliberately not part of that test — the two rows in production differ in escalator TEXT
-- ("3% annual" vs "3% annual (to ~$19.13/SF by year 5)") while agreeing on every number, and
-- a test that read prose would have scored an acceptance as a counter. Verified: with the
-- rule as written, the Gulf Coast deal is excluded; with escalator included, it is not.
--
-- ── STORE ABSOLUTES, RENDER RELATIVES (Joe's ruling, and the reason nothing here is a table) ──
-- Every number in these four views is computed on read from negotiation_round and
-- negotiation_claim. Nothing is materialised, and in particular NO CURVED GRADE IS EVER
-- STORED. A stored curve re-rates everyone the day one agent leaves the market, and you could
-- never afterwards tell whether an agent got tougher or the field got softer.
-- hardness_absolute is the durable measurement: a 0-100 composite whose formula does not
-- depend on who else is in the table. threat_rating is percent_rank over the CURRENT field,
-- evaluated at read time. A new toughest agent simply becomes the new 10.0 and no stored
-- value anywhere changes.
--
-- ── STORE OBSERVATIONS, COMPUTE PATTERNS (the same ruling, one level down) ──
-- "This one bluffs" appears in no column. v_counterparty_bluff counts claims made against
-- claims later contradicted by that same side's own subsequent rounds, and reports them as
-- "reversed 4 of 5" rather than 80% — because with an n this small a percentage is a lie
-- about precision. Unfalsifiable claims are excluded by joining negotiation_claim_type on
-- falsifiable = true, never by a hardcoded list: 0052 hardcoded a kind list into a view and
-- 0053 had to rewrite the view to correct it.
--
-- ── n IS ALWAYS VISIBLE, AND IT BANDS THE ANSWER ──
--   n = 0-2   unrated      no threat_rating at all. Raw round counts are shown instead.
--   n = 3-5   provisional  a rating, plus rating_low / rating_high, plus the word provisional.
--   n >= 6    rated
-- The band half-width is 6.0/sqrt(n), clamped to [0.5, 4.5]. It is a SPREAD HEURISTIC and it
-- is labelled as one in the column comment — it is not a confidence interval, because there
-- is no distribution to build one from and pretending otherwise on n=4 would be the same
-- false precision the bands exist to prevent.
--
-- ── CONFOUNDERS ARE TAGGED, NOT CORRECTED FOR ──
-- submarket tightness (0063) and the mix of conditions across a counterparty's deals are
-- reported beside the score, never subtracted from it. With n in single digits any correction
-- would be invented. The honest form is "8.4, and every one of those four deals was in a tight
-- submarket", which is what submarket_mix and deals_condition_unrecorded say.
--
-- ── WHAT IS DELIBERATELY EXCLUDED FROM THE COMPOSITE, THOUGH IT IS REPORTED ──
-- rounds_to_settle is directionally ambiguous — many rounds can mean they were immovable, or
-- that we ground them down, and nothing in the record distinguishes those. It is exposed
-- because Joe asked for it and it is genuinely informative to a human; it carries no weight.
-- term and escalator are in the category profile but not in the composite for the same
-- reason: a longer term is not obviously worse for a tenant, and "moved" is the only honest
-- verdict available on them. Their profile vocabulary is fixed/flexible rather than
-- holds/concedes so the difference is visible in the output itself.
--
-- ── OUR SIDE IS ALWAYS TENANT OR BUYER, AND THAT IS A STANDING FACT, NOT AN ASSUMPTION ──
-- CARR represents tenants and buyers exclusively and never landlords or sellers. That makes
-- the orientation of every metric unambiguous: their side always wants the rate HIGHER, ours
-- always wants it LOWER, on every deal, forever. If that ever changes, these views are wrong
-- in a way no guard here can catch, and the reopen condition is in the spec.
--
-- REVERSAL: `drop view if exists v_counterparty_scorecard, v_counterparty_bluff,
-- v_negotiation_deal, v_negotiation_position;`. This file creates four views, reads three
-- tables, and writes nothing.

begin;

-- ── 0. the premise, asserted rather than assumed (0022's pattern) ────────────────────────
-- Every view below reads objects 0063 creates. Filename order applies them first, but a
-- replay onto a database where 0063 was skipped or reverted should say WHY it failed rather
-- than emit four "relation does not exist" errors from inside a view definition.
do $$
declare missing text;
begin
  select string_agg(t, ', ' order by t) into missing
    from unnest(array['negotiation_claim','negotiation_claim_type','submarket_condition']) as t
   where to_regclass('public.' || t) is null;
  if missing is not null then
    raise exception '0064 needs 0063''s objects and these are absent: %. Apply '
                    '0063_counterparty_observation.sql first.', missing;
  end if;
  if not exists (select 1 from information_schema.columns
                  where table_schema='public' and table_name='negotiation_round'
                    and column_name='submarket_condition') then
    raise exception 'negotiation_round.submarket_condition is missing — 0063 did not complete';
  end if;
end $$;

-- ── 1. v_negotiation_position — one row per round, made comparable ───────────────────────
create or replace view v_negotiation_position as
with basis as (
  -- Comparability is decided PER DEAL, once, before anything is subtracted. 0022's rule
  -- stands: a purchase price is not a rent and must never land in a rent comparison, so a
  -- deal that mixes bases gets a NULL rate axis rather than a wrong one.
  select deal_id,
         count(*) filter (where rate_basis is not null and rate_norm_sf_yr is null) as unnormed,
         count(distinct rate_basis) filter (where rate_basis is not null)           as rate_bases,
         count(distinct ti_basis)   filter (where ti_basis   is not null)           as ti_bases
    from negotiation_round
   group by deal_id
),
pos as (
  select nr.id, nr.deal_id, nr.round_no, nr.side, nr.proposed_on, nr.expires_on,
         nr.free_rent_months, nr.term_months, nr.escalator, nr.submarket_condition,
         -- CARR is tenant/buyer rep only. See the header: this is a standing fact.
         case when nr.side in ('tenant','buyer') then 'ours' else 'theirs' end as camp,
         -- proposed_on is a DATE and both production rows share one, so created_at then id
         -- break the tie deterministically. Without a stable order the exclusion rule below
         -- would flip between runs.
         row_number() over (partition by nr.deal_id
                            order by nr.proposed_on, nr.created_at, nr.id)          as seq,
         case when b.unnormed = 0   then nr.rate_norm_sf_yr
              when b.rate_bases = 1 then nr.rate_amount end                         as rate_cmp,
         case when b.unnormed = 0   then 'usd_sf_yr_norm'
              when b.rate_bases = 1 then nr.rate_basis end                          as rate_cmp_basis,
         case when b.ti_bases <= 1  then nr.ti_amount end                           as ti_cmp,
         case when b.ti_bases <= 1  then nr.ti_basis   end                          as ti_cmp_basis
    from negotiation_round nr
    join basis b on b.deal_id = nr.deal_id
)
select p.id, p.deal_id, p.round_no, p.side, p.camp, p.seq, p.proposed_on, p.expires_on,
       p.rate_cmp, p.rate_cmp_basis, p.ti_cmp, p.ti_cmp_basis,
       p.free_rent_months, p.term_months, p.escalator, p.submarket_condition,
       prev.seq as prior_opposing_seq,
       -- THE EXCLUSION PRIMITIVE. True when this round moves off the position the other side
       -- already had on the table. Numbers only: escalator and opex are prose and a prose
       -- difference is not a concession.
       (prev.seq is not null
        and (p.rate_cmp          is distinct from prev.rate_cmp
          or p.ti_cmp            is distinct from prev.ti_cmp
          or p.free_rent_months  is distinct from prev.free_rent_months
          or p.term_months       is distinct from prev.term_months)) as moves_off_standing
  from pos p
  left join lateral (
    select p2.seq, p2.rate_cmp, p2.ti_cmp, p2.free_rent_months, p2.term_months
      from pos p2
     where p2.deal_id = p.deal_id and p2.camp <> p.camp and p2.seq < p.seq
     order by p2.seq desc
     limit 1) prev on true;

comment on view v_negotiation_position is
  'One row per negotiation round, normalised so two rounds can be subtracted (0064). camp is '
  'ours/theirs and is derived from side, because CARR represents tenants and buyers only and '
  'never landlords or sellers. rate_cmp is NULL when the deal mixes rate bases — a purchase '
  'price is not a rent (0022) and a wrong comparison is worse than no comparison. '
  'moves_off_standing is the primitive behind the exclusion rule: it is TRUE only when this '
  'round differs from the other side''s standing position on a NUMBER, so a same-terms '
  'acceptance never reads as a counter.';

-- ── 2. v_negotiation_deal — one row per deal per counterparty ────────────────────────────
create or replace view v_negotiation_deal as
with cp as (
  -- The counterparty is the LISTING SIDE, and only that. v_counterparty_history (0026) also
  -- surfaces referring_agent, correctly for its own purpose — but a referring agent is on our
  -- side of the table and has no business in a threat rating.
  select dp.deal_id, dp.party_id
    from deal_participant dp
   where dp.role = 'listing_side' and dp.to_at is null and dp.party_id is not null
),
cp_n as (select deal_id, count(distinct party_id) as n_cp from cp group by deal_id),
agg as (
  select p.deal_id,
         count(*) filter (where p.camp = 'ours')   as rounds_ours,
         count(*) filter (where p.camp = 'theirs') as rounds_theirs,
         count(*)                                  as rounds_total,
         bool_or(p.camp = 'ours' and p.moves_off_standing) as counter_tested,
         max(p.rate_cmp_basis)                     as rate_cmp_basis,
         min(p.proposed_on)                        as first_round_on,
         max(p.proposed_on)                        as last_round_on,
         (array_agg(p.rate_cmp order by p.seq)      filter (where p.camp='theirs' and p.rate_cmp is not null))[1] as their_open_rate,
         (array_agg(p.rate_cmp order by p.seq desc) filter (where p.camp='theirs' and p.rate_cmp is not null))[1] as their_last_rate,
         (array_agg(p.rate_cmp order by p.seq)      filter (where p.camp='ours'   and p.rate_cmp is not null))[1] as our_open_rate,
         (array_agg(p.rate_cmp order by p.seq desc) filter (where p.camp='ours'   and p.rate_cmp is not null))[1] as our_last_rate,
         min(p.rate_cmp) filter (where p.camp='theirs') as their_best_rate,
         (array_agg(p.ti_cmp order by p.seq) filter (where p.camp='theirs' and p.ti_cmp is not null))[1] as their_open_ti,
         max(p.ti_cmp) filter (where p.camp='theirs') as their_best_ti,
         (array_agg(p.free_rent_months order by p.seq) filter (where p.camp='theirs' and p.free_rent_months is not null))[1] as their_open_free,
         max(p.free_rent_months) filter (where p.camp='theirs') as their_best_free,
         count(*)          filter (where p.camp='theirs' and p.rate_cmp         is not null) as their_rate_rows,
         count(*)          filter (where p.camp='theirs' and p.ti_cmp           is not null) as their_ti_rows,
         count(*)          filter (where p.camp='theirs' and p.free_rent_months is not null) as their_free_rows,
         count(*)          filter (where p.camp='theirs' and p.term_months      is not null) as their_term_rows,
         count(*)          filter (where p.camp='theirs' and p.escalator        is not null) as their_esc_rows,
         count(distinct p.term_months) filter (where p.camp='theirs' and p.term_months is not null) as their_term_values,
         count(distinct p.escalator)   filter (where p.camp='theirs' and p.escalator   is not null) as their_esc_values,
         (array_agg(p.submarket_condition order by p.seq desc)
            filter (where p.submarket_condition is not null))[1] as submarket_condition
    from v_negotiation_position p
   group by p.deal_id
)
select a.deal_id,
       d.name                                        as deal_name,
       d.deal_type,
       d.phase,
       c.party_id                                    as counterparty_id,
       coalesce(n.n_cp, 0)                           as listing_side_parties,
       a.rounds_ours, a.rounds_theirs, a.rounds_total,
       a.first_round_on, a.last_round_on,
       a.counter_tested,
       -- QUALIFIES is the gate every number downstream passes through.
       (a.counter_tested and coalesce(n.n_cp,0) = 1) as qualifies,
       case when not a.counter_tested
              then 'our side never countered — the counterparty was not tested'
            when coalesce(n.n_cp,0) = 0
              then 'no listing_side party recorded on this deal'
            when n.n_cp > 1
              then 'more than one live listing_side party — movement cannot be attributed'
       end                                           as exclusion_reason,
       a.rate_cmp_basis,
       a.their_open_rate, a.their_last_rate, a.our_open_rate, a.our_last_rate,
       -- Positive = conceded. Negative is left NEGATIVE on purpose: a side that moved AWAY
       -- from the other is a real and interesting observation, and clamping it would hide it.
       a.their_open_rate - a.their_last_rate         as their_movement,
       a.our_last_rate   - a.our_open_rate           as our_movement,
       -- Joe's single best measure: what share of the total closing WE paid for. BOTH sides'
       -- openings and finals must be present — a share computed against a movement we cannot
       -- see is not a share, and coalescing the missing half to zero would read as "they never
       -- budged" when the truth is "we never recorded what they did".
       case when a.their_open_rate is not null and a.their_last_rate is not null
             and a.our_open_rate   is not null and a.our_last_rate   is not null
             and (a.their_open_rate - a.their_last_rate)
               + (a.our_last_rate   - a.our_open_rate) > 0
            then round( (a.our_last_rate - a.our_open_rate)
                      / ((a.their_open_rate - a.their_last_rate)
                       + (a.our_last_rate   - a.our_open_rate)), 4)
       end                                           as our_share_of_movement,
       case when a.their_rate_rows >= 2 and a.their_open_rate > 0
            then round(greatest(0, least(1, (a.their_open_rate - a.their_last_rate)
                                            / a.their_open_rate)), 4)
       end                                           as their_concession_frac,
       -- Converged = both sides' last stated rate agree. The only settle signal available
       -- from negotiation_round alone; deal.phase is not used because a deal can reach legal
       -- on terms that were never written as a round.
       coalesce(a.their_last_rate = a.our_last_rate, false) as converged,
       case when a.their_last_rate is not null and a.their_last_rate = a.our_last_rate
                 and a.their_last_rate > 0 and a.their_open_rate is not null
            then round((a.their_open_rate - a.their_last_rate) / a.their_last_rate, 4)
       end                                           as opening_spread_frac,
       -- Category evidence. `tested` needs at least two of THEIR rounds carrying the field,
       -- because one round can neither hold nor concede anything.
       (a.their_rate_rows >= 2)                                     as rate_tested,
       (a.their_rate_rows >= 2 and a.their_best_rate >= a.their_open_rate) as rate_held,
       (a.their_ti_rows   >= 2)                                     as ti_tested,
       (a.their_ti_rows   >= 2 and a.their_best_ti   <= a.their_open_ti)   as ti_held,
       (a.their_free_rows >= 2)                                     as free_rent_tested,
       (a.their_free_rows >= 2 and a.their_best_free <= a.their_open_free) as free_rent_held,
       -- Term and escalator: direction is ambiguous (a longer term is not obviously worse for
       -- a tenant), so the only honest verdict is "moved at all". Reported, never scored.
       (a.their_term_rows >= 2)                                     as term_tested,
       (a.their_term_rows >= 2 and a.their_term_values = 1)         as term_fixed,
       (a.their_esc_rows  >= 2)                                     as escalator_tested,
       (a.their_esc_rows  >= 2 and a.their_esc_values  = 1)         as escalator_fixed,
       a.submarket_condition
  from agg a
  join deal d on d.id = a.deal_id
  left join cp   c on c.deal_id = a.deal_id
  left join cp_n n on n.deal_id = a.deal_id;

comment on view v_negotiation_deal is
  'One row per deal per live listing_side counterparty (0064). `qualifies` is the exclusion '
  'gate and everything downstream passes through it: a deal where OUR side never countered '
  'contributes NOTHING to any score, because a counterparty who was never pushed has not '
  'been shown to be hard to move. The one negotiation in production is a round-1 acceptance '
  'and is excluded by exactly that rule. Deals with no listing_side party, or with more than '
  'one, are excluded too and say so in exclusion_reason rather than disappearing.';
comment on column v_negotiation_deal.our_share_of_movement is
  'Of all the ground that closed between the two opening positions, the fraction WE gave. '
  'Joe''s single best measure of how hard someone is to move. NULL when total movement is '
  'zero or negative, never 0 — no movement is not the same as no concession by us.';

-- ── 3. v_counterparty_bluff — claims made against claims contradicted ────────────────────
create or replace view v_counterparty_bluff as
with their_rounds as (
  select p.*, cp.party_id as counterparty_id
    from v_negotiation_position p
    join (select deal_id, party_id from deal_participant
           where role = 'listing_side' and to_at is null and party_id is not null) cp
      on cp.deal_id = p.deal_id
   where p.camp = 'theirs'
),
logged as (
  select tr.counterparty_id, tr.deal_id, tr.id as round_id, tr.seq, tr.expires_on,
         tr.rate_cmp, tr.ti_cmp, tr.free_rent_months,
         c.claim_type, t.falsifiable,
         -- The authority floor, converted onto the deal's comparison basis or refused. A
         -- floor we cannot compare is counted as MADE and left untestable, never guessed onto
         -- the wrong scale.
         case when c.stated_floor is null then tr.rate_cmp
              when tr.rate_cmp_basis = 'usd_sf_yr_norm' and c.stated_floor_basis = 'usd_sf_yr'
                   then c.stated_floor
              when tr.rate_cmp_basis = 'usd_sf_yr_norm' and c.stated_floor_basis = 'usd_sf_mo'
                   then c.stated_floor * 12
              when tr.rate_cmp_basis = c.stated_floor_basis then c.stated_floor
         end as floor_cmp
    from their_rounds tr
    join negotiation_claim      c on c.round_id = tr.id
    join negotiation_claim_type t on t.slug     = c.claim_type
),
derived_deadline as (
  -- A deadline is expires_on. 0063 makes it unloggable as a claim row precisely so this is
  -- the only place it comes from.
  select tr.counterparty_id, tr.deal_id, tr.id as round_id, tr.seq, tr.expires_on,
         tr.rate_cmp, tr.ti_cmp, tr.free_rent_months,
         'deadline'::text as claim_type, true as falsifiable, null::numeric as floor_cmp
    from their_rounds tr
   where tr.expires_on is not null
),
claims as (
  select * from logged
  union all
  select * from derived_deadline
),
tested as (
  select cl.*,
         case cl.claim_type
           -- Continuing to negotiate at all is the contradiction.
           when 'walk_away' then exists (
             select 1 from their_rounds t2
              where t2.deal_id = cl.deal_id and t2.counterparty_id = cl.counterparty_id
                and t2.seq > cl.seq)
           -- Filing the same numbers again is not a reversal; improving on them is.
           when 'finality' then exists (
             select 1 from their_rounds t2
              where t2.deal_id = cl.deal_id and t2.counterparty_id = cl.counterparty_id
                and t2.seq > cl.seq
                and (t2.rate_cmp         <  cl.rate_cmp
                  or t2.ti_cmp           >  cl.ti_cmp
                  or t2.free_rent_months >  cl.free_rent_months))
           when 'authority' then case when cl.floor_cmp is not null then exists (
             select 1 from their_rounds t2
              where t2.deal_id = cl.deal_id and t2.counterparty_id = cl.counterparty_id
                and t2.seq > cl.seq and t2.rate_cmp < cl.floor_cmp) end
           when 'deadline' then exists (
             select 1 from their_rounds t2
              where t2.deal_id = cl.deal_id and t2.counterparty_id = cl.counterparty_id
                and t2.seq > cl.seq and t2.proposed_on > cl.expires_on)
           -- competing_interest and anything else added later: untestable by construction.
           else null
         end as reversed
    from claims cl
)
select t.counterparty_id,
       p.name                                            as counterparty_name,
       t.claim_type,
       ct.falsifiable,
       count(*)                                          as claims_made,
       count(*) filter (where t.reversed is not null)    as claims_testable,
       count(*) filter (where t.reversed)                as claims_reversed,
       -- Joe: "4 of 5", not "80%". At this n a percentage claims a precision that is not there.
       case when count(*) filter (where t.reversed is not null) = 0
            then 'made ' || count(*) || ', none testable'
            else 'reversed ' || count(*) filter (where t.reversed)
                 || ' of '   || count(*) filter (where t.reversed is not null)
       end                                               as as_observed
  from tested t
  join negotiation_claim_type ct on ct.slug = t.claim_type
  join party p on p.id = t.counterparty_id
 group by t.counterparty_id, p.name, t.claim_type, ct.falsifiable;

comment on view v_counterparty_bluff is
  'Claims a counterparty made about their own position, against claims their own later rounds '
  'contradicted (0064). An OBSERVATION count — no row here says anyone bluffs, and no column '
  'stores a characterisation of a named person. Unfalsifiable claims are kept out of the rate '
  'by joining negotiation_claim_type on falsifiable, so marking a future claim type '
  'unfalsifiable is enough; the rule lives in the data, not in a list copied into this view. '
  'deadline claims come from negotiation_round.expires_on, which is why 0063 makes them '
  'unloggable as rows. Returns 0 rows until claims are captured.';

-- ── 4. v_counterparty_scorecard — the absolute, the curve, and the scouting report ───────
create or replace view v_counterparty_scorecard as
with bluff as (
  select counterparty_id,
         sum(claims_made)     as claims_made,
         sum(claims_testable) as claims_testable,
         sum(claims_reversed) as claims_reversed
    from v_counterparty_bluff
   where falsifiable                     -- read from the vocabulary, never hardcoded
   group by counterparty_id
),
rolled as (
  select d.counterparty_id,
         count(*)                                                    as deals_seen,
         count(*) filter (where d.qualifies)                         as n,
         count(*) filter (where not d.qualifies)                     as deals_excluded,
         count(*) filter (where not d.counter_tested)                as deals_untested,
         count(*) filter (where d.listing_side_parties > 1)          as deals_ambiguous_side,
         sum(d.rounds_ours)                                          as rounds_ours_total,
         sum(d.rounds_theirs)                                        as rounds_theirs_total,
         avg(d.our_share_of_movement)  filter (where d.qualifies)    as asym,
         avg(d.their_concession_frac)  filter (where d.qualifies)    as concession_frac,
         avg(d.opening_spread_frac)    filter (where d.qualifies)    as opening_spread,
         avg(d.rounds_total::numeric)  filter (where d.qualifies and d.converged) as rounds_to_settle,
         count(*) filter (where d.qualifies and d.rate_tested)       as rate_tested,
         count(*) filter (where d.qualifies and d.rate_held)         as rate_held,
         count(*) filter (where d.qualifies and d.ti_tested)         as ti_tested,
         count(*) filter (where d.qualifies and d.ti_held)           as ti_held,
         count(*) filter (where d.qualifies and d.free_rent_tested)  as free_tested,
         count(*) filter (where d.qualifies and d.free_rent_held)    as free_held,
         count(*) filter (where d.qualifies and d.term_tested)       as term_tested,
         count(*) filter (where d.qualifies and d.term_fixed)        as term_fixed,
         count(*) filter (where d.qualifies and d.escalator_tested)  as esc_tested,
         count(*) filter (where d.qualifies and d.escalator_fixed)   as esc_fixed,
         avg(sc.tightness::numeric) filter (where d.qualifies)       as avg_tightness,
         count(*) filter (where d.qualifies and d.submarket_condition is null) as deals_condition_unrecorded
    from v_negotiation_deal d
    left join submarket_condition sc on sc.slug = d.submarket_condition
   where d.counterparty_id is not null
   group by d.counterparty_id
),
comps as (
  select r.*,
         b.claims_made, b.claims_testable, b.claims_reversed,
         -- CLAMPED for the composite only. our_share_of_movement can legitimately exceed 1
         -- when the other side moved BACKWARDS (their_movement negative), and that is a real
         -- observation worth keeping — so the unclamped mean is still reported as
         -- avg_our_share_of_movement. It cannot be allowed to push a 0-100 composite past 100.
         -- The outer CASE is load-bearing and not decoration: GREATEST and LEAST IGNORE NULLs
         -- in Postgres, so a bare greatest(0, NULL) returns 0 — which would turn "we have no
         -- asymmetry data" into "they conceded everything", weight it at 45, and produce the
         -- exact silently-scored-zero this whole re-normalisation exists to prevent.
         case when r.asym is not null
              then least(1, greatest(0, r.asym)) end                 as c_asym,
         1 - r.concession_frac                                       as c_grip,
         case when coalesce(b.claims_testable,0) > 0
              then 1 - (b.claims_reversed::numeric / b.claims_testable) end as c_bluff,
         -- Only the three categories with an unambiguous direction feed the score.
         case when (r.rate_tested + r.ti_tested + r.free_tested) > 0
              then (r.rate_held + r.ti_held + r.free_held)::numeric
                 / (r.rate_tested + r.ti_tested + r.free_tested) end as c_hold
    from rolled r
    left join bluff b on b.counterparty_id = r.counterparty_id
),
weighted as (
  select c.*,
         -- Weights re-normalise over whatever components exist, so a missing component is
         -- ABSENT rather than silently scored zero. Fewer than two components = no composite.
         (45 * (c.c_asym  is not null)::int
        + 20 * (c.c_grip  is not null)::int
        + 15 * (c.c_bluff is not null)::int
        + 20 * (c.c_hold  is not null)::int)                         as weight_sum,
         (45 * coalesce(c.c_asym, 0) + 20 * coalesce(c.c_grip, 0)
        + 15 * coalesce(c.c_bluff,0) + 20 * coalesce(c.c_hold, 0))   as weight_num,
         ((c.c_asym is not null)::int + (c.c_grip is not null)::int
        + (c.c_bluff is not null)::int + (c.c_hold is not null)::int) as components
    from comps c
),
scored as (
  select w.*,
         case when w.components >= 2 and w.weight_sum > 0
              then round(100 * w.weight_num / w.weight_sum, 1) end   as hardness_absolute
    from weighted w
),
eligible as (
  select s.*, (s.n >= 3 and s.hardness_absolute is not null) as curve_eligible
    from scored s
),
curved as (
  select e.*,
         sum(e.curve_eligible::int) over ()                          as field_n,
         case when e.curve_eligible
              then percent_rank() over (partition by e.curve_eligible
                                        order by e.hardness_absolute) end as pr
    from eligible e
),
banded as (
  -- percent_rank returns double precision and round(double, int) does not exist in Postgres;
  -- everything below is numeric on purpose so the two-argument round is the numeric one.
  select c.*,
         case when c.curve_eligible and c.field_n >= 3
              then round((1 + 9 * c.pr)::numeric, 1) end             as threat_rating,
         least(4.5, greatest(0.5, round(6.0 / sqrt(greatest(c.n,1)::numeric), 2))) as half_width
    from curved c
)
select c.counterparty_id,
       p.name                                                        as counterparty_name,
       c.n,
       case when c.n >= 6 then 'rated'
            when c.n >= 3 then 'provisional'
            else 'unrated' end                                       as n_band,
       c.hardness_absolute,
       c.threat_rating,
       case when c.threat_rating is not null
            then greatest(1.0, round(c.threat_rating - c.half_width, 1)) end as rating_low,
       case when c.threat_rating is not null
            then least(10.0, round(c.threat_rating + c.half_width, 1)) end   as rating_high,
       c.field_n,
       -- THE SCOUTING REPORT. First-class, not a footnote: an 8 who holds rate and concedes
       -- TI is a different afternoon from an 8 who does the reverse.
       jsonb_strip_nulls(jsonb_build_object(
         'rate', case when c.rate_tested >= 2 then jsonb_build_object(
            'verdict', case when c.rate_held = c.rate_tested then 'holds'
                            when c.rate_held = 0             then 'concedes'
                            else 'mixed' end,
            'deals_tested', c.rate_tested, 'deals_held', c.rate_held) end,
         'ti', case when c.ti_tested >= 2 then jsonb_build_object(
            'verdict', case when c.ti_held = c.ti_tested then 'holds'
                            when c.ti_held = 0           then 'concedes'
                            else 'mixed' end,
            'deals_tested', c.ti_tested, 'deals_held', c.ti_held) end,
         'free_rent', case when c.free_tested >= 2 then jsonb_build_object(
            'verdict', case when c.free_held = c.free_tested then 'holds'
                            when c.free_held = 0             then 'concedes'
                            else 'mixed' end,
            'deals_tested', c.free_tested, 'deals_held', c.free_held) end,
         -- fixed/flexible, not holds/concedes: the direction of a term change is ambiguous
         -- and the vocabulary says so out loud.
         'term', case when c.term_tested >= 2 then jsonb_build_object(
            'verdict', case when c.term_fixed = c.term_tested then 'fixed'
                            when c.term_fixed = 0             then 'flexible'
                            else 'mixed' end,
            'deals_tested', c.term_tested, 'deals_fixed', c.term_fixed) end,
         'escalator', case when c.esc_tested >= 2 then jsonb_build_object(
            'verdict', case when c.esc_fixed = c.esc_tested then 'fixed'
                            when c.esc_fixed = 0            then 'flexible'
                            else 'mixed' end,
            'deals_tested', c.esc_tested, 'deals_fixed', c.esc_fixed) end
       ))                                                            as category_profile,
       nullif(concat_ws('; ',
         case when c.rate_tested >= 2 then
           (case when c.rate_held = c.rate_tested then 'holds' when c.rate_held = 0 then 'concedes' else 'mixed on' end) || ' rate' end,
         case when c.ti_tested >= 2 then
           (case when c.ti_held = c.ti_tested then 'holds' when c.ti_held = 0 then 'concedes' else 'mixed on' end) || ' TI' end,
         case when c.free_tested >= 2 then
           (case when c.free_held = c.free_tested then 'holds' when c.free_held = 0 then 'concedes' else 'mixed on' end) || ' free rent' end,
         case when c.term_tested >= 2 then
           (case when c.term_fixed = c.term_tested then 'fixed' when c.term_fixed = 0 then 'flexible' else 'mixed' end) || ' term' end,
         case when c.esc_tested >= 2 then
           (case when c.esc_fixed = c.esc_tested then 'fixed' when c.esc_fixed = 0 then 'flexible' else 'mixed' end) || ' escalator' end
       ), '')                                                        as profile_line,
       -- the absolutes, exposed so the composite can be argued with rather than trusted
       round(c.asym, 3)            as avg_our_share_of_movement,
       round(c.concession_frac, 3) as avg_their_concession_frac,
       round(c.opening_spread, 3)  as avg_opening_spread_frac,
       round(c.rounds_to_settle, 1) as avg_rounds_to_settle,   -- reported, NOT scored
       c.claims_made, c.claims_testable, c.claims_reversed,
       case when coalesce(c.claims_testable,0) > 0
            then 'reversed ' || c.claims_reversed || ' of ' || c.claims_testable
            when coalesce(c.claims_made,0) > 0
            then 'made ' || c.claims_made || ', none testable yet'
       end                         as bluff_as_observed,
       c.components                as composite_components,
       -- confounders, TAGGED rather than corrected for
       round(c.avg_tightness, 2)   as avg_submarket_tightness,
       c.deals_condition_unrecorded,
       -- what was thrown away, and why, so the exclusion is never silent
       c.deals_seen, c.deals_excluded, c.deals_untested, c.deals_ambiguous_side,
       c.rounds_ours_total, c.rounds_theirs_total,
       case when c.n = 0 and c.deals_untested = c.deals_seen
              then 'our side never countered on any recorded deal — this counterparty has '
                   'not been tested and no number is available'
            when c.n = 0
              then 'no deal qualifies: ' || c.deals_untested || ' untested, '
                   || c.deals_ambiguous_side || ' with an ambiguous listing side'
            when c.n < 3
              then 'n = ' || c.n || ' — below the floor for a curved rating; the absolute '
                   'and the raw rounds are all that is honest here'
            when c.field_n < 3
              then 'the field holds fewer than 3 rated counterparties, so there is nothing '
                   'to curve against'
            when c.hardness_absolute is null
              then 'fewer than two composite components have data'
       end                         as why_no_number
  from banded c
  join party p on p.id = c.counterparty_id;

comment on view v_counterparty_scorecard is
  'How hard a listing agent is to move: absolute metrics, a read-time 1-10 threat rating, and '
  'a category profile (0064). 10 = hardest. A THREAT RATING, not a performance review. '
  'Nothing here is stored — hardness_absolute is a fixed formula independent of who else is '
  'in the table, and threat_rating is percent_rank over the CURRENT field, so a new toughest '
  'agent becomes the new 10.0 without rescaling one stored value. Deals where OUR side never '
  'countered contribute NOTHING, which is why the single negotiation in production yields no '
  'number: a counterparty who was never pushed has not been shown to be hard to move.';
comment on column v_counterparty_scorecard.threat_rating is
  '1-10, computed at READ TIME as percent_rank over every counterparty with n >= 3. NULL '
  'below n = 3, and NULL whenever the field itself holds fewer than 3 rated counterparties — '
  'a curve over one person is a number about nobody. Always read it beside n and field_n.';
comment on column v_counterparty_scorecard.rating_low is
  'A SPREAD HEURISTIC, 6.0/sqrt(n) clamped to [0.5, 4.5] — deliberately NOT a confidence '
  'interval. There is no distribution here to build one from, and a statistical-looking band '
  'on n = 4 would be the same false precision the bands exist to prevent.';
comment on column v_counterparty_scorecard.category_profile is
  'Per-term-category verdict with the deal count it rests on. rate / TI / free rent use '
  'holds-concedes-mixed because the direction of a concession is unambiguous. term and '
  'escalator use fixed-flexible-mixed because it is NOT — a longer term is not obviously '
  'worse for a tenant — and those two are excluded from the composite for that reason. A '
  'category needs 2 tested deals before it says anything.';
comment on column v_counterparty_scorecard.deals_seen is
  'Deals against this counterparty on which ANY negotiation round is recorded — not every '
  'deal they appear on. A listing agent on a deal where nobody logged a round is invisible '
  'here, correctly: this view measures negotiations, and an unlogged negotiation is not one. '
  'deals_seen minus n is what was thrown away, and deals_untested / deals_ambiguous_side say '
  'why.';
comment on column v_counterparty_scorecard.avg_rounds_to_settle is
  'Reported, never scored. Many rounds can mean they were immovable or that we ground them '
  'down, and nothing in the record distinguishes those two. Feeding it into the composite '
  'would put an ambiguous signal behind a decisive-looking number.';

grant select on v_negotiation_position, v_negotiation_deal,
                v_counterparty_bluff,   v_counterparty_scorecard to carr_reader;
-- carr_writer too, on 0026's stated reasoning: write verbs resolve and validate through views,
-- and the 0020 incident (v_ref_index missing from writer) is a class we do not repeat.
grant select on v_negotiation_position, v_negotiation_deal,
                v_counterparty_bluff,   v_counterparty_scorecard to carr_writer;

-- ── guards BEFORE commit ─────────────────────────────────────────────────────────────────
-- These do not check that the arithmetic is RIGHT — no data exists to check it against, and a
-- guard that pretends otherwise is theatre. They check the two things that can be verified on
-- an empty table and that would be catastrophic if wrong: that a number is never emitted
-- without evidence, and that the exclusion rule actually excludes.
do $$
declare
  rows_sc int; rows_bluff int; bad int; untested int; total_rounds int; cp_rows int;
begin
  -- (1) NO NUMBER WITHOUT EVIDENCE. The single most important property of this file.
  select count(*) into bad from v_counterparty_scorecard
   where n < 3 and threat_rating is not null;
  if bad > 0 then
    raise exception '% counterparty row(s) carry a threat_rating at n < 3 — a rating from '
                    'two negotiations is a guess wearing a decimal point', bad;
  end if;
  select count(*) into bad from v_counterparty_scorecard
   where n = 0 and (hardness_absolute is not null or category_profile <> '{}'::jsonb);
  if bad > 0 then
    raise exception '% counterparty row(s) with ZERO qualifying deals emitted a composite or '
                    'a category profile — this is the exact failure the exclusion rule exists '
                    'to stop', bad;
  end if;
  select count(*) into bad from v_counterparty_scorecard
   where n = 0 and why_no_number is null;
  if bad > 0 then
    raise exception '% unrated row(s) give no reason — silence about an exclusion is how a '
                    'reader concludes there is nothing to know', bad;
  end if;

  -- (2) THE EXCLUSION RULE ACTUALLY EXCLUDES, asserted against the reported symptom by name.
  -- The Gulf Coast Pelvic Health negotiation is a round-1 acceptance and MUST NOT qualify.
  select count(*) into total_rounds from negotiation_round;
  select count(*) into untested from v_negotiation_deal where not counter_tested;
  if total_rounds = 2 and untested < 1 then
    raise exception 'the only negotiation in the record is a round-1 acceptance and it is '
                    'NOT being excluded — scoring it would rate an untested listing agent a '
                    'perfect 10 for never conceding';
  end if;
  select count(*) into bad from v_negotiation_deal where qualifies and not counter_tested;
  if bad > 0 then
    raise exception '% deal(s) qualify without our side having countered', bad;
  end if;

  -- (3) UNFALSIFIABLE CLAIMS CANNOT REACH A SCORE. Structural, not a promise: the composite
  -- reads v_counterparty_bluff through `where falsifiable`.
  if not exists (select 1 from negotiation_claim_type where falsifiable = false) then
    raise exception 'no unfalsifiable claim type exists — 0063 must have been altered, and '
                    'the filter that keeps competing_interest out of the score now filters '
                    'nothing';
  end if;

  select count(*) into rows_sc    from v_counterparty_scorecard;
  select count(*) into rows_bluff from v_counterparty_bluff;
  select count(*) into cp_rows    from deal_participant
   where role = 'listing_side' and to_at is null and party_id is not null;

  raise notice 'counterparty scorecard live, and it is nearly silent on purpose. % round(s) '
               'on % deal(s); % live listing_side counterpart(ies); % scorecard row(s), % '
               'bluff row(s). Every counterparty is unrated today because our side has '
               'countered ZERO times in the record. These views produce almost nothing until '
               'record-counter is used on both sides of a live negotiation and a verb exists '
               'to write negotiation_claim — that is the work, not this file.',
               total_rounds, (select count(distinct deal_id) from negotiation_round),
               cp_rows, rows_sc, rows_bluff;
end $$;

commit;
