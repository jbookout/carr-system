-- 0158_job_timeout_receipts.sql
-- A timeout is neither a generic failure nor a lease-expiry dead letter. Keep
-- that evidence explicit in the immutable receipt vocabulary.

begin;

alter table ops.job_receipt drop constraint job_receipt_kind_check;
alter table ops.job_receipt add constraint job_receipt_kind_check
  check (kind in ('completion','failure','timeout','dead_letter','approval','override'));

commit;

do $$
begin
  if not exists (
    select 1 from pg_constraint
     where conrelid='ops.job_receipt'::regclass
       and conname='job_receipt_kind_check'
       and pg_get_constraintdef(oid) like '%timeout%'
  ) then
    raise exception '0158 FAILED: timeout receipt kind missing';
  end if;
end $$;
