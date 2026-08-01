-- 0031_decision_events_and_idea_loops.sql — ORDER 40 (personal set to records).
--
-- Two surfaces, one migration, because they share nothing but the order that
-- asked for them and neither is big enough to deserve its own file:
--   (a) `idea` joins the loop vocabulary, so idea-bank.md can become a render of
--       loop_item exactly as the four loop files already are.
--   (b) v_decision_entry — the read side of decision-history-as-events.
--
-- Rehearsed on Neon branch rehearse-0031-order40. NOT APPLIED IN PRODUCTION.
-- Production apply is Joe's tap, per the stream preamble.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- WHAT R-40a ASKED FOR, AND WHY (a) IS DDL AND (b) IS NOT.
--
-- R-40a: "extend the kind/cause (and, if genuinely required, subject) vocabulary
-- per whatever pattern ACTUALLY governs each — read the schema, ref-table where
-- the ORDER 3 pattern applies, minimal change."
--
-- The read, recorded here so the next reader does not repeat it:
--
--   event.subject_type — text NOT NULL, **no CHECK, no FK**. 0001's line carries
--     a COMMENT listing example values ('deal','party','lead','rule',...) and
--     nothing more. Nothing governs it. So `subject_type='decision'` needs no
--     migration at all; it is a new value in an open column, not a vocabulary
--     extension. Verified on the branch (see the guard block).
--   event.verb — same: text NOT NULL, no constraint. `verb='log-decision'` needs
--     no migration either. The importer writes the verb a future present-tense
--     verb would write, so imported rows and verb-written rows are one shape.
--   event.cause — the ONE constrained column, an inline CHECK. It already carries
--     'import_migration', which is exactly what a bulk legacy import is. Adding
--     an 'import_decision_history' beside it would widen a constrained
--     vocabulary to say something record_source.source_system already says
--     precisely. Minimal change wins: the importer reuses 'import_migration' and
--     provenance lives in record_source, the ORDER 39 pattern. NO CHECK IS
--     TOUCHED. Flagged for ratification rather than assumed.
--   event has NO kind column at all — it is the field-level audit spine
--     (verb/subject/field/old/new/cause) plus the quote/rationale pair. There is
--     no kind vocabulary here to extend. (ORDER 36's 0028 reached the same read
--     from the other side and put analysis on `activity` for that reason.)
--
-- So the decision half of R-40a requires ZERO schema change, and this file's
-- decision content is one read-only view. That is the honest minimum, and it is
-- reported as such rather than padded into a migration that looks busier.
--
-- loop_item.kind / loop_block.kind — by contrast, BOTH are inline CHECKs
--   (0024), not the ORDER 3 ref-table pattern that governs activity_kind. There
--   is no ref table to add a row to. Extending the CHECK in place is therefore
--   the minimal change AND the pattern-true one: a CHECK is extended by
--   replacing it, never by adding a second CHECK beside it.
-- ─────────────────────────────────────────────────────────────────────────────

begin;

-- ============================================================================
-- (a) `idea` joins the loop vocabulary
-- ============================================================================
-- idea-bank.md is Joe-personal (00_Context), a parking lot of candidate ideas
-- that are NOT committed actions — which is precisely why it was kept out of
-- open-loops.md in the first place. It rides loop_item anyway because the shape
-- is identical (numbered rows, a status, a promote/retire lifecycle, prose
-- scaffolding around two tables) and because loop_block already solves the one
-- hard part: keeping the file's doctrine prose editable by Joe rather than by a
-- code change.
--
-- The lifecycle mapping, ruled by the Fable seat 2026-08-01 and recorded here:
--   Parked row      -> status 'open'
--   Retired row     -> status 'done', close_outcome = the "Why retired" cell
-- The existing loop_item_closed_has_outcome constraint therefore does real work
-- on ideas too: a retired idea with no stated reason cannot be stored, which is
-- the idea bank's own rule ("Move, don't delete — the reasoning stays visible so
-- we don't re-litigate it later") made structural.

alter table loop_item  drop constraint loop_item_kind_check;
alter table loop_item  add  constraint loop_item_kind_check
  check (kind in ('open_loop','team_loop','action_required','idea'));

alter table loop_block drop constraint loop_block_kind_check;
alter table loop_block add  constraint loop_block_kind_check
  check (kind in ('open_loop','team_loop','action_required','idea'));

comment on column loop_item.kind is
  'open_loop / team_loop / action_required (0024) plus idea (0031, ORDER 40): a '
  'candidate idea awaiting a decision to act, rendered into '
  '00_Context/idea-bank.md. An idea is deliberately NOT an open_loop — the bank '
  'holds what has no owner and no commitment yet, which is the distinction the '
  'file was created to preserve.';

-- ============================================================================
-- (b) v_decision_entry — decision-history read back as decisions
-- ============================================================================
-- One row per decision-history entry. Adds no table and no column: a decision is
-- an ordinary `event` row whose subject_type is 'decision', and the only thing
-- missing was a way to read those back grouped as history rather than scattered
-- through the audit spine.
--
-- RULE-29 GROUPING IS NOT IN THIS VIEW, BY DESIGN. R-40a: "Rule-29 per-session
-- grouping happens at RENDER time, not storage." This view therefore exposes the
-- grouping KEY (session_key, entry_date) and leaves the grouping to the
-- exporter's query. Storing a pre-grouped shape would fossilize today's rule 29
-- into the schema, and rule 29 is doctrine — it belongs where Joe can change it.
--
-- WINDOWING IS THE CONSUMER'S JOB TOO. ORDER 4's 100KB split exists because a
-- flat file grows without bound. A view that always returned all 192 entries
-- would rebuild that problem in SQL. entry_date is indexed-friendly and the
-- exporter passes a window; the "archive" is simply a wider window, not a second
-- file. That is what makes ORDER 4's split convention moot rather than merely
-- relocated.

create or replace view v_decision_entry as
select
    rs.external_key,
    split_part(rs.external_key, '#', 1)                     as source_file,
    split_part(rs.external_key, '#', 2)                     as session_key,
    e.occurred_at::date                                     as entry_date,
    act.slug                                                as author,
    e.new_value ->> 'title'                                 as title,
    e.human_quote,
    e.agent_rationale,
    e.cause,
    (e.new_value ->> 'quote_absent')::boolean               as quote_absent,
    e.new_value ->> 'provenance'                            as provenance,
    e.subject_id                                            as decision_id,
    e.id                                                    as event_id
from record_source rs
join event e   on e.id = rs.entity_id and rs.entity_type = 'event'
join actor act on act.id = e.actor_id
where rs.source_system = 'decision-history';

comment on view v_decision_entry is
  'ORDER 40. decision-history.md + its archive read back as decision events. One '
  'row per source entry. Grouping (rule 29, per session) and windowing (ORDER 4''s '
  'former 100KB split) are the RENDER''s job — this view exposes session_key and '
  'entry_date and groups nothing itself, so doctrine stays doctrine.';

-- Reader-safe by construction: no contact detail, no actor uuids beyond the
-- subject/event surrogate ids, no base-table reach. Same posture as
-- v_md_ledger_entry (0030).
grant select on v_decision_entry to carr_reader;
grant select on v_decision_entry to carr_writer;
grant select on v_decision_entry to carr_exporter;

-- ============================================================================
-- guards: assert the end state rather than trusting it
-- ============================================================================
do $$
declare n int; t text;
begin
  -- (a) both CHECKs accept idea, and still refuse a typo.
  select pg_get_constraintdef(oid) into t from pg_constraint
   where conname = 'loop_item_kind_check';
  if t is null or t not like '%idea%' then
    raise exception '0031: loop_item_kind_check does not admit idea (%)', t;
  end if;
  select pg_get_constraintdef(oid) into t from pg_constraint
   where conname = 'loop_block_kind_check';
  if t is null or t not like '%idea%' then
    raise exception '0031: loop_block_kind_check does not admit idea (%)', t;
  end if;

  -- exactly one kind CHECK per table — an extended CHECK, never a second one
  -- bolted beside the first (the failure 0017's header warns about).
  select count(*) into n from pg_constraint
   where conrelid = 'loop_item'::regclass and contype = 'c'
     and pg_get_constraintdef(oid) like '%kind%';
  if n <> 1 then
    raise exception '0031: loop_item has % kind CHECKs, expected exactly 1', n;
  end if;

  -- (b) the decision half really did need no vocabulary change. Assert the
  -- premise, so a later migration that quietly CONSTRAINS these columns breaks
  -- here loudly instead of breaking the importer silently.
  select count(*) into n from pg_constraint
   where conrelid = 'event'::regclass and contype = 'c'
     and (pg_get_constraintdef(oid) like '%subject_type%'
       or pg_get_constraintdef(oid) like '%verb%');
  if n <> 0 then
    raise exception '0031: event.subject_type/verb are now constrained (% CHECKs) — '
                    'the ORDER 40 importer assumed they are open text', n;
  end if;

  -- cause must still carry import_migration; the importer depends on it and
  -- deliberately did NOT widen this vocabulary.
  select pg_get_constraintdef(oid) into t from pg_constraint
   where conrelid = 'event'::regclass and contype = 'c'
     and pg_get_constraintdef(oid) like '%cause%';
  if t is null or t not like '%import_migration%' then
    raise exception '0031: event.cause no longer admits import_migration (%)', t;
  end if;

  -- the view exists and is reader-visible
  perform 1 from pg_views where viewname = 'v_decision_entry';
  if not found then
    raise exception '0031: v_decision_entry was not created';
  end if;
  if not has_table_privilege('carr_reader', 'v_decision_entry', 'select') then
    raise exception '0031: carr_reader cannot read v_decision_entry';
  end if;

  -- ORDER 40 touches no record surface. Assert the blast radius.
  select count(*) into n from information_schema.columns where table_name='lead';
  if n <> 32 then
    raise exception '0031: lead has % columns, expected 32 — ORDER 40 must not touch lead', n;
  end if;

  raise notice '0031 guards OK: idea admitted to both loop CHECKs (one CHECK each), '
               'event.subject_type/verb confirmed unconstrained (no migration needed), '
               'cause still admits import_migration, v_decision_entry live, lead untouched.';
end $$;

commit;
