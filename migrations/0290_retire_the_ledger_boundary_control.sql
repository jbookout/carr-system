-- 0290_retire_the_ledger_boundary_control.sql
-- THE CONTROL WHOSE ONLY IMPLEMENTATION NEVER RAN IS REMOVED FROM THE CATALOG.
--
-- WHY THIS EXISTS (2026-08-23). The process-audit council's dead-weight sweep
-- retired hooks/ledger-boundary-sweep.py in the same change that carries this
-- migration. That file was WRITTEN BUT REGISTERED NOWHERE: the 2026-08-06 #214
-- audit found no settings file naming it and out/hook-guard.log showing zero
-- firings against 75 for ledger-sweep.py, and nothing changed in the seventeen
-- days after. hooks/SETTINGS-BLOCK.md was the only surface claiming it was a
-- hook, which is the drift that retired that document too.
--
-- WHY THE ROW CANNOT SIMPLY BE LEFT BEHIND. ops.enforcement_control_catalog is
-- what approve-rule consults to decide whether a claimed control is real
-- enforcement, and ops/control-catalog-parity-gate.py FAILS on any row the
-- repository's map no longer declares:
--
--     "An unreviewed row here is an unreviewed claim about what counts as
--      enforcement. Declare them in the map, or retire them deliberately —
--      this gate will not delete a row that may still be enforcing a live rule."
--
-- This migration is that deliberate retirement. The gate refuses to do it
-- itself, and ops/sync_control_catalog.py leaves stranded rows in place for the
-- same reason, so the decision has to be written down as SQL — which is the
-- point: a control leaving the catalog should be as visible as one joining it.
--
-- WHY IT IS SAFE, AND WHY THIS FILE DOES NOT ASK YOU TO TAKE THAT ON TRUST.
-- Three independent facts, two of them enforced by the database rather than by
-- this comment:
--
--   1. NO RULE BINDS IT. rule_controls in ops/config/rule-enforcement-map.json
--      names ledger_boundary zero times across all 218 active rules, and the
--      catalog row itself carries installed=false with a null verified_at — it
--      has never claimed to be in force.
--   2. THE FOREIGN KEY REFUSES. ops.rule_control_binding.control_key references
--      this table ON DELETE RESTRICT, so a binding anyone added since would
--      abort this migration instead of silently un-enforcing a rule.
--   3. THE TRIGGER REFUSES. active_approved_control_immutable fires BEFORE
--      DELETE and raises if the key appears in any rule_approval_receipt for an
--      active rule.
--
-- So if any part of premise 1 is wrong, this migration fails loudly rather than
-- removing enforcement. The explicit guard below exists only to say WHICH of
-- those it was, because a bare FK violation names a constraint and not a reason.
--
-- WHAT IS NOT TOUCHED: ledger_capture, the adjacent and very much live control
-- backed by hooks/ledger-sweep.py (75 recorded firings). The two differ by one
-- word and only one of them was ever wired, which is precisely why the dead one
-- survived this long.

begin;

do $$
declare
  bound   integer;
  claimed integer;
begin
  if not exists (select 1 from ops.enforcement_control_catalog
                  where control_key = 'ledger_boundary') then
    raise notice '0290: ledger_boundary is already absent from the catalog; nothing to retire';
    return;
  end if;

  -- Name the reason before the constraint does. Both of these are also enforced
  -- by the database independently of this block; a clear message is the only
  -- thing being added here.
  select count(*) into bound
    from ops.rule_control_binding
   where control_key = 'ledger_boundary';
  if bound > 0 then
    raise exception '0290 REFUSED: ledger_boundary still has % rule binding(s); '
                    'it is enforcing something and must not be retired', bound;
  end if;

  select count(*) into claimed
    from ops.rule_approval_receipt ar
    join rule r on r.id = ar.rule_id and r.status = 'active'
   where 'ledger_boundary' = any(ar.requested_control_keys);
  if claimed > 0 then
    raise exception '0290 REFUSED: ledger_boundary backs % active approved rule(s)', claimed;
  end if;

  delete from ops.enforcement_control_catalog where control_key = 'ledger_boundary';
end $$;

-- THE SECOND RETIRED HOOK LEAVES A CONTROL THAT STAYS. hooks/install-record-home-gate.py
-- was one of five implementation refs on record_home, and it is deleted in this
-- same change: a one-shot 2026-08-03 installer that merged record-home-gate.py
-- into ~/.claude/settings.json by hand, superseded by ops/config-as-code.py.
-- The control itself is very much live — hooks/record-home-gate.py is the .md
-- write deny — so this narrows its evidence rather than retiring it.
--
-- IT HAS TO MOVE HERE TOO, not just in the map. implementation_ref is compared
-- string-for-string by ops/control-catalog-parity-gate.py, so a map that no
-- longer names the file and a catalog row that still does is a DISAGREEMENT and
-- fails the gate exactly as an undeclared row does. Leaving it would also keep
-- a deleted file listed as enforcement evidence for rule 14181e60, the
-- database-first write law — which is the "recitation is not enforcement"
-- failure (rule ab814a26) written into the very table that adjudicates it.
--
-- The value below is what ops/sync_control_catalog.py compiles from the map:
-- the implementation list joined with '; ', in map order.
do $$
declare
  updated integer;
begin
  update ops.enforcement_control_catalog
     set implementation_ref = 'hooks/record-home-gate.py; hooks/bash-write-gate.py; '
                              'hooks/guard-unattended.py; hooks/write-effect-check.py',
         updated_at = now()
   where control_key = 'record_home'
     and implementation_ref <> 'hooks/record-home-gate.py; hooks/bash-write-gate.py; '
                               'hooks/guard-unattended.py; hooks/write-effect-check.py';
  get diagnostics updated = row_count;
  if updated = 0 then
    raise notice '0290: record_home already carries the narrowed implementation list';
  end if;
end $$;

do $$
begin
  if exists (select 1 from ops.enforcement_control_catalog
              where control_key = 'ledger_boundary') then
    raise exception '0290 FAILED: ledger_boundary is still in the catalog after the delete';
  end if;

  -- The neighbour it is one word away from must be untouched, or this migration
  -- retired the wrong control and the parity gate would have agreed with it.
  if not exists (select 1 from ops.enforcement_control_catalog
                  where control_key = 'ledger_capture') then
    raise exception '0290 FAILED: ledger_capture is gone — the live ledger control was removed';
  end if;

  if exists (select 1 from ops.enforcement_control_catalog
              where control_key = 'record_home'
                and implementation_ref like '%install-record-home-gate%') then
    raise exception '0290 FAILED: record_home still names the deleted installer as evidence';
  end if;

  -- The gate it actually denies with must still be listed, or this narrowed the
  -- evidence down to nothing and the parity gate would have agreed.
  if not exists (select 1 from ops.enforcement_control_catalog
                  where control_key = 'record_home'
                    and implementation_ref like '%hooks/record-home-gate.py%') then
    raise exception '0290 FAILED: record_home no longer names hooks/record-home-gate.py';
  end if;
end $$;

commit;
