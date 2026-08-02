-- 0032_audit_repairs.sql — ORDER 43 repair + the fresh-session audit's structural findings.
-- Joe: "Honestly I want you to fix everything." (2026-08-01, after the cold-session audit)
--
-- Four repairs, each independently reversible, no row deleted anywhere:
--   A. v_last_touch: narrow the over-broad import carve-out (the real ORDER 43 fault)
--   B. deal_phase.sort: resolve the legal/due_diligence collision + wrong closing order
--   C. next_action: undated != due today, plus a real hold mechanism
--   D. deal.notes_path: link deals to their client's dossier where one exists
--
-- WHY A IS THE FIX AND "convert the 40 rows to events" WAS NOT: the 40 deal rows are
-- legitimate Wave-1 import rows. The frozen Salesforce source says "Last Activity empty
-- in SF for all deals" — there was never a touch date to carry, so the importer stamped
-- the import moment and v_last_touch's third clause (kind='note' AND source='import')
-- counted it as contact. That clause was meant to preserve genuine legacy last-touch
-- carriers; it is simply too wide. Narrowed to match the carriers by their summary, it
-- keeps all 10 client rows and both stamp rows and drops the 40 dateless deal blobs and
-- the hunt-ledger verdicts (a hunt verdict is internal annotation, not contact).
-- Consequence, intended and honest: those 40 deals now show NO last_touch, and
-- v_stale_records surfaces them — which is the true state, since we have no contact
-- record for them at all.

begin;

-- ---------- A. v_last_touch ----------
create or replace view v_last_touch as
with contact as (
  select a.*
    from activity a
    join activity_kind k on k.slug = a.kind
   where k.is_contact
      -- genuine legacy last-touch carriers, matched by what they ARE, not by being imported:
      or (a.kind = 'note' and a.summary = 'last-touch stamp (imported)')
      or (a.kind = 'note' and a.summary like 'Last touch carried from%')
)
select 'deal'::text as subject_type, deal_id as subject_id, max(occurred_at)::date as last_touch
  from contact where deal_id is not null group by deal_id
union all
select 'client'::text, client_id, max(occurred_at)::date
  from contact where client_id is not null group by client_id
union all
select 'lead'::text, lead_id, max(occurred_at)::date
  from contact where lead_id is not null group by lead_id
union all
select 'vendor'::text, vendor_id, max(occurred_at)::date
  from contact where vendor_id is not null group by vendor_id;

-- ---------- B. deal_phase.sort ----------
-- Was: pending 10, research 20, site_selection 30, negotiation 40, closing 50, closed 60,
--      due_diligence 100, legal 100  → DD/legal collided AND sorted after closing/closed.
update deal_phase set sort = 50 where slug = 'due_diligence';
update deal_phase set sort = 60 where slug = 'legal';
update deal_phase set sort = 70 where slug = 'closing';
update deal_phase set sort = 80 where slug = 'closed';

-- ---------- C. next_action ----------
alter table next_action add column if not exists hold_until date;
comment on column next_action.hold_until is
  'Explicit do-not-surface date. A row with hold_until in the future never appears in v_today_triage, however it is dated. Added 0032 because two rows carried "do not surface" as PROSE and the queue surfaced them anyway — an instruction living only in text is not an instruction the system can obey.';

-- The two rows that said so in words now say it in a field.
update next_action set hold_until = date '2026-12-31'
 where id = 'cad06096-12f4-4aeb-9027-0739059c5636';  -- Weiler: "NO email, NO text before then"
update next_action set hold_until = date '2027-12-31'
 where id = '68aa1185-5d71-4fc9-8f88-e50e6071259b';  -- "inbound only", Joe's Jul 11 call

-- ---------- D. deal.notes_path ----------
update deal d set notes_path = c.notes_path, updated_at = now()
  from client c
 where c.id = d.client_id and c.notes_path is not null and d.notes_path is null;

commit;

-- ---------- C (cont). v_today_triage: undated is BACKLOG, not due-today ----------
-- The flood was structural: `due_on IS NULL OR due_on <= CURRENT_DATE` treated every
-- undated task as due now. 145 auto-generated "Enrich: confirm relationship history w/
-- Dell" rows, 4 Chris Kelly re-asks and both do-not-surface rows are all undated, so they
-- drowned the six genuinely overdue dated items. Undated now means unscheduled.
create or replace view v_today_triage as
  select 'next_action'::text as item_kind, na.id, na.subject_type, na.subject_id,
         owner.slug as owner, na.description as what, na.due_on
    from next_action na
    join actor owner on owner.id = na.owner_id
   where na.status = 'open'
     and na.due_on is not null and na.due_on <= current_date
     and (na.hold_until is null or na.hold_until <= current_date)
union all
  select 'critical_date'::text, cd.id, 'deal'::text, cd.deal_id, null::text,
         cd.kind || coalesce(': ' || cd.note, ''), cd.due_on
    from critical_date cd
   where cd.status = 'open' and cd.due_on <= (current_date + 14)
union all
  select 'ingest'::text, i.id, i.subject_type, i.subject_id, null::text,
         coalesce(i.summary, i.kind), null::date
    from ingest_item i
   where i.status = 'untriaged';

-- ---------- guards: every claim above, asserted ----------
do $$
declare n int; m int;
begin
  select count(*) into n from deal_phase group by sort having count(*) > 1 limit 1;
  if found then raise exception 'phase sort collision remains'; end if;

  select count(*) into n from v_last_touch where subject_type='client';
  if n <> 10 then raise exception 'client last_touch changed: expected 10, got %', n; end if;

  select count(*) into n from v_last_touch where subject_type='deal';
  if n <> 0 then raise exception 'deal last_touch: expected 0 after narrowing, got %', n; end if;

  select count(*) into n from next_action where hold_until is not null;
  if n <> 2 then raise exception 'hold_until: expected 2 rows, got %', n; end if;

  select count(*) into m from v_today_triage where what like 'Enrich: confirm relationship%';
  if m <> 0 then raise exception 'boilerplate still in triage: % rows', m; end if;
end $$;
