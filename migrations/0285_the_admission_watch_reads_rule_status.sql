-- 0285_the_admission_watch_reads_rule_status.sql
-- THE ADMISSION AUDIT CAN NOW RUN UNATTENDED, AND STILL CANNOT READ A RULE.
--
-- WHY THIS EXISTS (2026-08-23). ops/rule-admission-audit.py is the whole of the
-- control-plane roadmap's Phase 1 exit condition. It had never run against
-- Production by anything until today, when it read 218 active rules with only 4
-- admitted and 214 carrying no admission contract at all. Two production doors
-- and a nightly watch closed the equivalent gap for the control-plane registry
-- the same day; the admission half could not follow, for one reason: the audit
-- joins the `rule` table, and `rule` is authority-scoped. carr_authority,
-- carr_backup and carr_writer may select it. No role a routine may become can.
--
-- WHAT THIS GRANTS, and why it is this narrow. The audit needs two facts about
-- each rule and nothing else: which row it is, and whether it is active. It
-- never reads a statement, a teacher, an activation actor or a scope. So this
-- is a COLUMN-LEVEL grant of exactly (id, status) to carr_jobs. Rule text stays
-- unreadable by every routine, which is not a confidentiality argument — the
-- statements are recited to both partners at every session boot — but a scoping
-- one: an unattended job that can only count rows cannot quietly become a
-- second reader of the doctrine, and a credential that never needed the text is
-- the credential that should not carry it.
--
-- WHAT IT DOES NOT GRANT, checked below rather than asserted: no insert, update
-- or delete on `rule` anywhere, and no select on any column but the two named.
-- The admission contract itself was already readable by carr_jobs
-- (ops.rule_admission), which is what makes the pair enough.
--
-- Rule 5409731b binds here — a change to who may read a table changes the
-- permission surface of everything that touches it — so the verification block
-- asserts the exact intended shape and refuses the migration otherwise.

begin;

do $$
begin
  if not exists (select 1 from pg_roles where rolname='carr_jobs') then
    raise notice '0285: carr_jobs is absent (a disposable or sanitized database); skipping the grant';
    return;
  end if;
  execute 'grant select (id, status) on rule to carr_jobs';
end $$;

do $$
declare
  readable text[];
begin
  if not exists (select 1 from pg_roles where rolname='carr_jobs') then
    return;
  end if;

  -- Exactly the two columns, and no more.
  select coalesce(array_agg(a.attname order by a.attname), '{}')
    into readable
    from pg_attribute a
   where a.attrelid = 'rule'::regclass
     and a.attnum > 0
     and not a.attisdropped
     and has_column_privilege('carr_jobs', a.attrelid, a.attname, 'select');
  if readable <> array['id','status'] then
    raise exception '0285 FAILED: carr_jobs reads % on rule; expected exactly {id,status}', readable;
  end if;

  -- No table-wide select, and no write of any kind.
  if has_table_privilege('carr_jobs','rule','select') then
    raise exception '0285 FAILED: carr_jobs holds a table-wide select on rule';
  end if;
  if has_table_privilege('carr_jobs','rule','insert')
     or has_table_privilege('carr_jobs','rule','update')
     or has_table_privilege('carr_jobs','rule','delete') then
    raise exception '0285 FAILED: carr_jobs holds a write privilege on rule';
  end if;

  -- The pair has to be enough on its own, or the grant bought nothing.
  if not has_table_privilege('carr_jobs','ops.rule_admission','select') then
    raise exception '0285 FAILED: carr_jobs cannot read ops.rule_admission, so the audit still cannot run';
  end if;
end $$;

commit;
