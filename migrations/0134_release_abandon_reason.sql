-- 0134_release_abandon_reason.sql
-- A RELEASE THAT ENDS WITHOUT SHIPPING HAS TO SAY WHY.
--
-- WHAT PROMPTED IT. On 2026-08-16 the first real releases went through
-- ops.release and left two candidates behind: carr-mcp-staging-b73ed84a, built
-- and then overtaken when main moved two commits before Joe signed, and
-- carr-mcp-staging-28dbef81, replaced by a -v2 that carried the security
-- evidence the approval constraint requires. Both are unapproved and inert —
-- they cannot ship, because a deploy needs a live approval matching a freshly
-- recomputed plan hash. But nothing could move them out of `candidate`, because
-- ops-record.py had no action for it, and the honest reason lived only in a
-- decision entry nobody reading the table would find.
--
-- THE TABLE ALREADY HAS BOTH TERMINAL STATES and 0131 was careful about them:
-- `abandoned` for a release that will never ship and has no successor, and
-- `superseded` for one replaced by a named later release, which
-- a_superseded_release_names_its_successor already forces to point at that
-- successor. Neither could be reached from the wrapper. This migration adds the
-- one field `abandoned` was missing.
--
-- WHY A COLUMN RATHER THAN REUSING failure_class. A failed release is one that
-- ran and went wrong; failure_class describes that fault and feeds the incident
-- path. An abandoned release never ran at all. Recording "superseded before
-- approval" as a failure class would put a non-event into every count built on
-- failures, which is the same shape as recording an abandonment as done —
-- exactly what close-loop's `resolution` split refuses to do for loops.
--
-- THE CONSTRAINT IS THE POINT, not the column. Without it the field is optional
-- and the first hurried session leaves it null, which is how the system ends up
-- with terminal rows nobody can explain. 0131's own comment says an approval is
-- four columns that live and die together; this is the same discipline one state
-- down: `abandoned` and its reason live and die together.
--
-- NOT RETROACTIVE, deliberately. Existing rows are `candidate`, `approved` or
-- `complete`, so none can violate this — the constraint binds only the state it
-- names. The two orphan candidates are moved by the wrapper afterwards, with
-- their reasons written at that moment rather than backfilled by a migration
-- guessing at intent.

begin;

alter table ops.release
  add column if not exists abandoned_reason text;

comment on column ops.release.abandoned_reason is
  'Why a release ended without ever shipping. Required when state = abandoned; '
  'a superseded release names its successor in superseded_by instead, so this '
  'stays null there. Never reuse failure_class for this: that describes a '
  'release that RAN and went wrong, and an abandoned one never ran.';

alter table ops.release
  drop constraint if exists an_abandoned_release_says_why;

alter table ops.release
  add constraint an_abandoned_release_says_why check (
    state <> 'abandoned'
      or (abandoned_reason is not null and length(btrim(abandoned_reason)) >= 12)
  );

commit;
