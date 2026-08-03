-- 0068_rule_version.sql — give `rule` the version/updated_at pair every other
-- mutable table has had since 0001, so a rule can be CORRECTED IN PLACE.
--
-- WHY (Joe, 2026-08-02: "why dont you create a verb to edit a rule first so you
-- can just do it"):
--
-- The rule store shipped with a one-way lifecycle. `teach` writes proposed,
-- `activate-rule` flips it active, and `retire-rule` (2026-08-02) withdraws it.
-- There has never been a way to fix the WORDS of a rule that is otherwise
-- correct. The only path was retire + re-teach, which:
--   * mints a NEW id, so every conversation, spec and handoff that cites the old
--     one now points at a tombstone;
--   * loses created_at, taught_by and the activation event;
--   * requires a fresh `human_quote`, because teach demands one — so the partner
--     has to re-say something he already said, for a change to prose he never
--     wrote.
--
-- That last point is the sharp one. Verified 2026-08-02:
--   select count(*) from rule where status='proposed'
--     and (human_quote is null or btrim(human_quote)='');   -> 53 of 54
-- Fifty-three proposed rules carry NO quote at all. They were imported from
-- 00_Context/ai-operating-notes.md by pipelines/import_operating_notes.py, which
-- correctly refused to fabricate quotes. Their `statement` is compiled doctrine
-- prose, not anyone's spoken words. Editing it is editing OUR articulation, not
-- the partner's testimony — there is nothing to protect by forcing a re-teach.
--
-- The precedent already exists one table over: `update-decision` (verb, 2026-08-02)
-- exists precisely so a defective decision entry can be corrected rather than
-- re-litigated. Decisions got that affordance. Rules never did.
--
-- THE LINE THIS ENFORCES: amend = same rule, better words. teach + retire =
-- different rule. `human_quote` stays immutable once set — the tool layer allows
-- filling a NULL quote (backfill) and refuses to overwrite a real one, so the
-- partner's testimony can never be edited by a session.
--
-- Version + updated_at are added together because trg_touch_row() sets both;
-- attaching it to a table missing updated_at would fail at the first update.
-- With the trigger attached, activate-rule and retire-rule also bump version,
-- which is correct: a version read before an activation SHOULD go stale.

alter table rule
  add column version    int         not null default 1,
  add column updated_at timestamptz not null default now();

create trigger rule_touch before update on rule
  for each row execute function trg_touch_row();

comment on column rule.version is
  '[A2] optimistic-concurrency token for amend-rule; bumped by trg_touch_row on every update';
comment on column rule.human_quote is
  'verbatim words of the teacher. IMMUTABLE once non-empty — amend-rule may fill a NULL, never overwrite. NULL means imported doctrine, not a paraphrase passed off as a quote.';
