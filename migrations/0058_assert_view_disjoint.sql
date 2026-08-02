-- 0058_assert_view_disjoint.sql — the argument that a view cannot double-count becomes a test.
--
-- THE SHAPE OF THE BUG THIS EXISTS TO CATCH. There are 52 `union all` branches across 14
-- migrations (0004, 0016, 0017, 0019, 0020, 0026, 0027, 0028, 0032, 0033, 0034, 0054, 0055,
-- 0056). Every one of them is `union all` rather than `union`, and that is the RIGHT choice:
-- a bare `union` dedups silently, so two branches that genuinely both matter get collapsed
-- into one row and a real record disappears with no error. But choosing `union all` buys the
-- mirror bug, and it is the nastier of the two. Two branches that overlap emit the same
-- subject twice, nothing complains, and the number just gets bigger.
--
-- WHY BIGGER IS WORSE THAN SMALLER HERE. These views are denominators.
-- v_capture_coverage divides touched records by total records — it currently reads 0.7% for
-- vendors and 5.3% for leads, numbers small enough that a few phantom rows in the
-- denominator move the percentage visibly while still looking plausible. v_integrity_digest
-- is the system's self-report. v_ref_index feeds `find` and resolveSubject, where a
-- duplicated subject makes a name permanently ambiguous to a resolver that refuses to guess
-- past one match. A missing row eventually gets noticed by the human who knows it should be
-- there. A 4% inflated denominator gets noticed by nobody, ever.
--
-- WHAT 0056 DID, AND WHY IT IS NOT ENOUGH. 0056 added a fifth branch to v_ref_index and
-- spent a full paragraph arguing it cannot double-count ("NOTHING DOUBLE-COUNTS, BY
-- CONSTRUCTION"). The argument is correct, and it was backed by two hand-written guard
-- queries at the bottom of that file. But those queries are specific to that view and that
-- day, and the next migration to add a branch will either re-derive them, write a weaker
-- version, or write a paragraph and no query at all. Prose in a COMMENT is a claim. This
-- makes it a call.
--
-- HOW IT DIFFERS FROM A CHECK CONSTRAINT. It does not run in the hot path and it is not
-- enforced continuously — a view has no constraints to attach. It is a MIGRATION-TIME
-- assertion: the branch author calls it once against real data in the same transaction that
-- creates the view, and if the branches overlap the migration rolls back and never applies.
-- That is the right moment to catch it, because overlap is introduced by a schema change and
-- essentially never by a data change.
--
-- IT ACCEPTS A SUBQUERY, NOT ONLY A VIEW NAME, because the interesting overlaps are usually
-- between a PROJECTION of the branches rather than the whole rows. v_ref_index is the case
-- in point: it is disjoint on (subject_type, subject_id) by construction, and the question
-- 0056 actually had to answer was narrower — can one party surface under BOTH the party
-- branch and a role branch? That is a disjointness question about a two-branch projection,
-- and without subquery support the caller is back to hand-writing it.
--
-- THE SOURCE AND KEY ARE PASSED AS TEXT AND EXECUTED, so this is dynamic SQL and the
-- injection question deserves a straight answer rather than a shrug: the only callers are
-- migration files, which already run arbitrary DDL as the owner, so there is no privilege
-- here to escalate. What the validation below buys is a clear error on a typo — `to_regclass`
-- turns a misspelled view name into "no such relation" instead of a syntax error forty
-- characters into a generated string.
--
-- NULL KEYS ARE REPORTED, NOT SKIPPED, and that is deliberate. SQL `group by` treats nulls
-- as equal, so two rows whose key is null land in one group and get flagged. A caller keying
-- on a column that is legitimately null across a whole branch (v_ref_index.ref is null for
-- every deal row, by construction) should key on something else — which is exactly why the
-- call below keys on (subject_type, subject_id) and not on ref. Silently dropping null keys
-- would make the assertion pass on precisely the view where it should fail loudest.

begin;

create or replace function assert_view_disjoint(source text, key_expr text)
returns void
language plpgsql
as $fn$
declare
  dup_keys bigint;
  surplus  bigint;
  worst    text;
begin
  -- A bare identifier must resolve to a real relation. A parenthesised expression is taken
  -- as a caller-supplied subquery and must carry its own alias, which is a Postgres rule
  -- rather than one invented here.
  if source !~ '^\s*\(' and to_regclass(source) is null then
    raise exception 'assert_view_disjoint: no such relation %', source;
  end if;

  -- One pass: how many distinct key values repeat, how many surplus rows that is (the actual
  -- size of the inflation), and one sample to name in the error.
  execute format(
    $q$ select count(*), coalesce(sum(d.n - 1), 0), coalesce(min(d.k), '(null key)')
          from (select (%s)::text as k, count(*) as n
                  from %s
                 group by 1
                having count(*) > 1) d $q$,
    key_expr, source)
    into dup_keys, surplus, worst;

  if dup_keys > 0 then
    raise exception '% is NOT disjoint on %: % key(s) appear more than once, % surplus '
                    'row(s) (e.g. %). A union all branch overlaps another, so every consumer '
                    'of this view is counting something twice.',
                    source, key_expr, dup_keys, surplus, worst;
  end if;
end
$fn$;

comment on function assert_view_disjoint(text, text) is
  'Raises unless every key value in `source` (a relation name, or a parenthesised subquery '
  'with an alias) appears exactly once — 0058. Call it from a migration''s guard block '
  'whenever you add or change a `union all` branch, INSTEAD of writing a paragraph arguing '
  'the branches cannot overlap. 0056 wrote the paragraph and the paragraph was right; the '
  'next one may not be, and an inflated denominator is invisible. Migration-time only: it '
  'does a full scan and belongs nowhere near a hot path. Null key values are grouped and '
  'reported like any other, never skipped, so key on something non-null in every branch.';

-- ── the helper is itself tested, on both answers ────────────────────────────────────────
-- An assertion nobody has watched fail is indistinguishable from an assertion that cannot
-- fire. This proves it raises on a known duplicate, stays quiet on a known-clean source, and
-- rejects a name that does not exist — inside the same transaction that defines it, so a
-- helper that does not work never reaches the database.
do $$
declare fired boolean := false; msg text;
begin
  create temp view _disjoint_selftest_dup   as select 1 as k union all select 1;
  create temp view _disjoint_selftest_clean as select 1 as k union all select 2;

  begin
    perform assert_view_disjoint('_disjoint_selftest_dup', 'k');
  exception when others then
    fired := true; msg := sqlerrm;
  end;
  if not fired then
    raise exception 'assert_view_disjoint stayed silent on a view with a known duplicate — '
                    'the helper does not work and every call to it would be theatre';
  end if;

  perform assert_view_disjoint('_disjoint_selftest_clean', 'k');            -- must NOT raise
  perform assert_view_disjoint('(select 7 as k union all select 8) z', 'k'); -- subquery form

  fired := false;
  begin
    perform assert_view_disjoint('v_no_such_view_anywhere', 'k');
  exception when others then fired := true;
  end;
  if not fired then
    raise exception 'assert_view_disjoint accepted a relation name that does not exist';
  end if;

  drop view _disjoint_selftest_dup;
  drop view _disjoint_selftest_clean;
  raise notice 'assert_view_disjoint self-test passed. On a duplicate it says: %', msg;
end $$;

-- ── first real use: v_ref_index, the view 0056 argued about ─────────────────────────────
do $$
declare rows_now bigint; parties bigint;
begin
  -- (a) No subject twice. (subject_type, subject_id) is the pair every consumer resolves on
  -- and it is non-null in all five branches; `ref` would be the wrong key, since the deal
  -- branch emits it as null by construction.
  perform assert_view_disjoint('v_ref_index', '(subject_type, subject_id)');

  -- (b) 0056's actual claim, which the composite key above cannot see: a party must not
  -- surface under BOTH the party branch and a role branch, or its name becomes permanently
  -- ambiguous to a resolver that refuses to guess past one match. The party branch emits one
  -- row per party, so `distinct` on the role side makes any overlap show up as a repeated
  -- key. Note this deliberately does NOT assert party_id is unique across the whole view —
  -- it legitimately is not. Since 0055 repaired the orphaned roles, Dr. Erik Petersen holds
  -- L-201 AND C-126 on one party, which is the correct answer and the entire point of 0046.
  perform assert_view_disjoint(
    $src$ (select party_id from v_ref_index where subject_type = 'party'
           union all
           select distinct party_id from v_ref_index
            where subject_type <> 'party' and party_id is not null) z $src$,
    'party_id');

  select count(*) into rows_now from v_ref_index;
  select count(*) into parties  from v_ref_index where subject_type = 'party';
  raise notice 'v_ref_index disjoint on (subject_type, subject_id), and no party surfaces '
               'under both a role branch and the party branch. % rows, % of them the 0056 '
               'party branch. That migration''s argument now has a test behind it.',
               rows_now, parties;
end $$;

commit;
