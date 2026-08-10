-- 0093_deal_parking_shape_hardening.sql — make the parking reason database-mandatory.
--
-- 0092's application layer rejects a parked value without a reason, but the
-- SQL CHECK used `parking_reason in (...)` without an explicit IS NOT NULL.
-- PostgreSQL CHECK constraints accept UNKNOWN, so a direct writer could still
-- produce an unclassified parked row. Rehearsal caught this before production.

begin;

alter table deal drop constraint if exists deal_parking_shape_check;
alter table deal add constraint deal_parking_shape_check check (
  (operating_state = 'active'
    and parking_reason is null and parking_note is null
    and parked_at is null and parked_by is null)
  or
  (operating_state = 'parked'
    and parking_reason is not null
    and parking_reason in ('prospect_never_active','client_paused','other')
    and parked_at is not null and parked_by is not null)
);

commit;
