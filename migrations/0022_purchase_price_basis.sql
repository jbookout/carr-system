-- 0022: a purchase price gets a basis it can legally carry (ORDER 24(b)).
--
-- THE FINDING, from the document factory rather than from a design review. The
-- loi-purchase field map's `purchase_price` slot is owed on every purchase deal,
-- and its owed_note names the reason exactly: `negotiation_round.rate_basis` is
-- a RENT vocabulary — usd_sf_yr / usd_sf_mo / usd_mo_gross / usd_yr_gross. A
-- total purchase price has no basis in that list, so `record-counter` cannot
-- store one, so the slot cannot fill. The gap was reported, not improvised
-- around, which is why the fix is a migration instead of a text field.
--
-- WHAT THIS ADDS, and only this:
--   usd_total     — a total dollar figure (a purchase price: $1,250,000).
--   usd_sf_total  — dollars per SF of a total price (a purchase price expressed
--                   per square foot: $185.00/SF). NOT a rent per SF per year;
--                   the two are different questions and the slug says which.
--
-- HOW IT IS CONSTRAINED, checked before writing a line of DDL: `rate_basis` is a
-- plain CHECK constraint (`negotiation_round_rate_basis_check`), NOT a reference
-- table. ORDER 3's ref-table pattern (0017) applies to vocabularies a human
-- widens — deal types, activity kinds, participant roles. A rate basis is not
-- that: every basis carries arithmetic somewhere (the generated norm column, the
-- tool's normRate, the confirm band), so adding one is a code change by nature
-- and the CHECK states that honestly. Extending the CHECK is therefore "the same
-- way", per the order's own words.
--
-- THE NORM COLUMN IS DELIBERATELY LEFT ALONE. `rate_norm_sf_yr` is generated as
-- CASE rate_basis WHEN 'usd_sf_yr' ... WHEN 'usd_sf_mo' ... ELSE NULL. A total
-- price and a purchase-price-per-SF have no rent-per-SF-per-year equivalent, so
-- NULL is the correct answer and the existing ELSE already gives it. Inventing a
-- number there would put a purchase price into a rent comparison.
--
-- SCOPE: negotiation_round ONLY. The identical CHECK exists on `availability`,
-- `lease` and `comp` and is NOT touched — an availability listing and an
-- executed lease are rent records, and widening their vocabulary is a separate
-- ruling nobody has made. Every other site where this vocabulary is written down
-- is listed in ORDER 24's execution log for Fable, including the one that still
-- blocks the round from being written: `record-counter`'s rate_basis enum in
-- mcp-server/src/tools.js, which this order does not authorise touching.

alter table negotiation_round drop constraint negotiation_round_rate_basis_check;

alter table negotiation_round add constraint negotiation_round_rate_basis_check
  check (rate_basis in ('usd_sf_yr','usd_sf_mo','usd_mo_gross','usd_yr_gross',
                        'usd_total','usd_sf_total'));

comment on column negotiation_round.rate_basis is
  'What the rate_amount MEANS. Rent bases: usd_sf_yr, usd_sf_mo, usd_mo_gross, '
  'usd_yr_gross. Purchase bases (0022): usd_total = a total price; usd_sf_total '
  '= a total price stated per SF. Purchase bases carry no rate_norm_sf_yr by '
  'design — a price is not a rent and must never land in a rent comparison.';

-- ── guards: assert the end state rather than trusting the statements above ────
do $$
declare
  def   text;
  other text;
begin
  select pg_get_constraintdef(oid) into def
    from pg_constraint where conname = 'negotiation_round_rate_basis_check';
  if def is null then
    raise exception 'negotiation_round_rate_basis_check is gone — the table is now unconstrained';
  end if;
  if def not like '%usd_total%' or def not like '%usd_sf_total%' then
    raise exception 'the new purchase bases are not in the CHECK: %', def;
  end if;
  -- the four rent bases survive: this is a widening, never a replacement.
  if def not like '%usd_sf_yr%' or def not like '%usd_sf_mo%'
     or def not like '%usd_mo_gross%' or def not like '%usd_yr_gross%' then
    raise exception 'a rent basis was dropped by the rewrite: %', def;
  end if;

  -- the sibling tables are untouched, and staying untouched is the point.
  select string_agg(conrelid::regclass::text, ', ') into other
    from pg_constraint
   where conname in ('availability_rate_basis_check','lease_rate_basis_check','comp_rate_basis_check')
     and pg_get_constraintdef(oid) like '%usd_total%';
  if other is not null then
    raise exception 'a sibling rate_basis CHECK gained the purchase vocabulary: %', other;
  end if;

  -- the generated norm column is byte-identical to what 0001 wrote.
  if not exists (
    select 1 from information_schema.columns
     where table_schema='public' and table_name='negotiation_round'
       and column_name='rate_norm_sf_yr'
       and generation_expression like '%usd_sf_mo%' and generation_expression not like '%usd_total%') then
    raise exception 'rate_norm_sf_yr generation expression is not where 0001 left it';
  end if;

  raise notice 'ORDER 24 guards: negotiation_round.rate_basis now accepts usd_total '
               'and usd_sf_total; four rent bases intact; availability/lease/comp '
               'unchanged; rate_norm_sf_yr untouched';
end $$;
