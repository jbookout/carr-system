-- 0048_candidate_pool.sql — prospect_pool becomes candidate_pool.
--
-- Joe: "the prospect pool should be candidate pool now remember. that solidifies the whole
-- thought now too bc we dont have to worry about merging candidats into the contact list.
-- but they still have context since you've done research to find the candidate."
--
-- THE VOCABULARY THIS FREES. The settled funnel is candidate -> lead -> prospect -> client.
-- While the reservoir was called prospect_pool, "prospect" meant two different things: the
-- 9,860 unjudged rows from a licence sweep, AND the stage after lead where a judgment has
-- been made that someone is worth working. Same word, opposite ends of the funnel. Renaming
-- the reservoir gives "prospect" back to the stage.
--
-- ── LOOP #125's GATE IS WRONG ON ITS PREMISE, and this migration is the evidence ──
-- #125 requires the rename to ship in the SAME migration as the party dedup, reasoning that
-- "all four operate on the party layer". prospect_pool has NO party_id — candidates are
-- deliberately identity-free until promote-pool mints one, which is the whole reason 9,860
-- rows cost nothing. The rename therefore never touches the party layer and is independent
-- of the merge. The gate's other three clauses (stage, contact_state, dedup) do belong
-- together; this one was mis-grouped. Recorded rather than silently ignored.
--
-- ── RENAME WITH A COMPATIBILITY VIEW, NOT A FLAG DAY ──
-- Four views and six code files reference prospect_pool, including mcp-server/src/tools.js
-- (promote-pool) and run.sh. A hard rename breaks every one of them between the migration
-- and the Worker deploy. So the old name survives as a view over the new table: a
-- single-table view is auto-updatable in Postgres, so reads AND writes keep working while
-- code moves across at its own pace. The view is dropped in a later migration once nothing
-- references it — and that drop is the thing that proves the migration finished, rather
-- than a comment claiming it did.
--
-- The 'pool' status value and the promote-pool verb name are NOT touched here. They are
-- code-side and belong with the Worker change, not stranded half-done in SQL.

begin;

alter table prospect_pool rename to candidate_pool;
alter sequence if exists prospect_pool_id_seq rename to candidate_pool_id_seq;

-- Backward compatibility. Auto-updatable (single table, no aggregates), so existing INSERTs
-- and UPDATEs through the old name keep working until every caller is moved.
create view prospect_pool as select * from candidate_pool;

comment on table candidate_pool is
  'The candidate reservoir: raw, unjudged rows from licence sweeps and radar lanes. 9,860 '
  'at rename. Deliberately carries NO party_id — a candidate has no identity until '
  'promote-pool mints one, which is why 9,860 rows cost nothing and why merging never has '
  'to consider them. Qualifying context lives here as STRUCTURED columns (source_row 100%, '
  'score_basis 100%, segment_play 87%) rather than as a dossier: candidate context is '
  'uniform because it came from a list, and narrative context begins at promotion, when a '
  'human starts working an individual. Renamed from prospect_pool in 0048 to free the word '
  '"prospect" for the funnel stage after lead.';

comment on view prospect_pool is
  'DEPRECATED compatibility shim for the 0048 rename. Auto-updatable, so old callers keep '
  'working. Drop it once nothing references the old name — that drop is what proves the '
  'rename finished.';

-- guards, before commit
do $$
declare rows_before int; rows_after int; shim int; promoted int;
begin
  select count(*) into rows_after from candidate_pool;
  select count(*) into shim from prospect_pool;

  if rows_after <> 9860 then
    raise exception 'candidate_pool holds % rows, expected 9860 — the rename moved data', rows_after;
  end if;
  if shim <> rows_after then
    raise exception 'compatibility view shows % rows against the table''s % — the shim does '
                    'not mirror the table', shim, rows_after;
  end if;

  -- the qualifying context is the reason this table exists; it must have survived intact
  if (select count(source_row) from candidate_pool) <> 9860 then
    raise exception 'source_row coverage dropped below 9860 — qualifying context lost';
  end if;
  if (select count(score_basis) from candidate_pool) <> 9860 then
    raise exception 'score_basis coverage dropped below 9860 — the reason each candidate '
                    'qualified is the one thing that must carry over to their lead';
  end if;

  -- promoted_lead_id is the carry-over link; it must still be there even though unused
  select count(*) into promoted from candidate_pool where promoted_lead_id is not null;
  raise notice 'candidate_pool live: 9860 rows, context intact, % promoted so far. '
               'prospect_pool remains as a deprecated shim.', promoted;
end $$;

commit;
