-- 0081_loop_blocker.sql — the deferral gate. A new open_loop must name why this
-- session cannot do the work now, chosen from a closed list of real blockers.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- WHY THIS IS SCHEMA AND NOT ANOTHER RULE. Joe taught rule 179be4b8 on
-- 2026-08-08 in these words: "go ahead and do the parked follow ups now. why
-- would you put them off? thats the exact reason we have a giant growing list of
-- loops. you constantly opt to not finish the job." That rule is ACTIVE. One day
-- later, 2026-08-09, the hot list held 27 rows and the backlog 162, and Joe
-- asked for the same thing again. A rule that has to be remembered at the right
-- moment is not a control; it is a hope. Rule 179be4b8 also binds at the wrong
-- moment — it fires at BUILD WRAP-UP, for follow-ups noticed during a build —
-- while most loops are opened mid-session by a session that simply decided not
-- to do the thing. Nothing was ever asked at the moment of deferral.
--
-- THIS MIGRATION MOVES THE QUESTION TO THAT MOMENT. `add-loop` becomes unable to
-- open an open_loop without naming a blocker from a closed list. The list is
-- closed on purpose: every entry is a state of the world outside the session, so
-- there is no cell to write "later" into. A session that cannot name one has
-- just proved it could do the work, which is the answer the gate is for.
--
-- WHAT IS DELIBERATELY NOT GATED.
--   team_loop and action_required are handoffs to the OTHER partner. The blocker
--     is the other human by construction, and forcing a redundant declaration
--     would train sessions to type a word to get past a door.
--   idea rows are parked by design — the idea bank exists to hold work that is
--     not being done now, and its own rule 4 ("move, don't delete") already
--     governs it.
--   The 189 open_loop rows that exist today keep a NULL blocker. Backfilling a
--     guess would put invented reasons in Joe's file, and the NULL is the honest
--     record: these were opened before anything asked. v_loop_no_blocker names
--     them so the backlog can be swept down rather than admired.
--
-- NOT NULL IS NOT USED, ON PURPOSE. The constraint is a check that fires only on
-- rows carrying a class, plus the verb-level requirement in
-- mcp-server/src/tools.js. A table-wide NOT NULL would have to be preceded by a
-- backfill of 189 fabricated reasons, and the importer that first loaded these
-- files would break on re-run. The gate belongs where the decision is made.
-- ─────────────────────────────────────────────────────────────────────────────

begin;

alter table loop_item add column if not exists blocker_class  text;
alter table loop_item add column if not exists blocker_detail text;

comment on column loop_item.blocker_class is
  'Why the session that opened this loop could not do the work itself, from a '
  'closed list of states of the world OUTSIDE the session: human_only, '
  'counterparty, ruling, external_event, other_lane, capability. NULL on rows '
  'opened before migration 0081 (2026-08-09) and on kinds the gate does not '
  'cover (team_loop, action_required, idea). There is deliberately no value '
  'meaning "later" — a session that cannot name one of these can do the work.';
comment on column loop_item.blocker_detail is
  'The specific thing named: which person, which ruling, which date, which '
  'credential. "the landlord" is not a counterparty; "Sanders, the listing '
  'broker on C-112" is. Required by add-loop whenever blocker_class is set.';

alter table loop_item drop constraint if exists loop_item_blocker_class_known;
alter table loop_item add constraint loop_item_blocker_class_known
  check (blocker_class is null or blocker_class in
    ('human_only','counterparty','ruling','external_event','other_lane','capability'));

-- A class without a specific thing is the vague deferral wearing a label. The
-- 12-character floor is not a quality bar, it is a floor under "n/a" and "tbd";
-- the verb carries the real vocabulary check.
alter table loop_item drop constraint if exists loop_item_blocker_detail_present;
alter table loop_item add constraint loop_item_blocker_detail_present
  check (blocker_class is null
         or (blocker_detail is not null and length(btrim(blocker_detail)) >= 12));

commit;

-- ── the audit surface, with the action it is bound to ────────────────────────
-- Rule 590b11e1: no metric without a bound action. The bound action for this
-- view is stated in its own comment and carried by the health row that reads it:
-- every row it returns is a loop nobody ever justified deferring, so the sweep
-- is to DO it or CLOSE it — never to re-file it.
begin;

create or replace view v_loop_no_blocker as
select li.id,
       li.number,
       li.kind,
       li.domain,
       li.owner,
       li.marker,
       li.since_text,
       lb.block_key                                   as section,
       left(coalesce(li.body, li.title, ''), 160)     as gist,
       (li.created_at < timestamptz '2026-08-09')     as predates_gate
  from loop_item li
  join loop_block lb on lb.id = li.block_id
 where li.kind = 'open_loop'
   and li.status = 'open'
   and li.blocker_class is null
 order by li.created_at;

comment on view v_loop_no_blocker is
  'Open open_loop rows carrying no named blocker. BOUND ACTION: each row is a '
  'candidate to DO or to CLOSE, never to re-file — nobody ever established that '
  'the work needed deferring. predates_gate separates the 2026-08-09 inheritance '
  'from anything opened after the gate landed; a false row means add-loop was '
  'bypassed and is a defect, not a backlog item.';

grant select on v_loop_no_blocker to carr_reader, carr_writer, carr_exporter;

commit;

-- ── proof, in the same run ───────────────────────────────────────────────────
-- Each probe runs inside its own PL/pgSQL sub-block, so a caught exception (and
-- the deliberate sentinel that undoes the ACCEPTED case) rolls the write back.
-- Nothing here survives the migration; the point is that the constraints are
-- exercised by output rather than trusted from their own DDL text.
do $$
declare n int; probe uuid; refused boolean;
begin
  if not exists (select 1 from information_schema.columns
                  where table_name='loop_item' and column_name='blocker_class') then
    raise exception '0081: blocker_class did not land';
  end if;
  select id into probe from loop_item where kind='open_loop' and status='open' limit 1;
  if probe is null then raise exception '0081: no open_loop row to probe against'; end if;

  -- (1) an unknown class must be refused, detail deliberately valid so only the
  --     class check can be the thing that fires
  refused := false;
  begin
    update loop_item set blocker_class='later',
                         blocker_detail='waiting until there is more time'
     where id = probe;
  exception when check_violation then refused := true;
  end;
  if not refused then raise exception '0081: the class check accepted "later"'; end if;

  -- (2) a real class with a throwaway detail must be refused
  refused := false;
  begin
    update loop_item set blocker_class='ruling', blocker_detail='tbd' where id = probe;
  exception when check_violation then refused := true;
  end;
  if not refused then raise exception '0081: the detail floor accepted "tbd"'; end if;

  -- (3) a real class with a real detail must be ACCEPTED — a gate that refuses
  --     everything proves nothing. Undone by the sentinel.
  begin
    update loop_item set blocker_class='ruling',
                         blocker_detail='Joe has not ruled on the C-112 escalation cap'
     where id = probe;
    raise exception 'undo-0081-probe';
  exception
    when check_violation then raise exception '0081: a valid blocker was refused';
    when raise_exception then
      if sqlerrm <> 'undo-0081-probe' then raise; end if;
  end;

  select count(*) into n from v_loop_no_blocker;
  raise notice '0081 ok — 3 probes behaved; v_loop_no_blocker sees % inherited open loops', n;
end $$;
