-- 0025: two grant fixes found by OUTPUT on 2026-07-31 night (Fable seat session 3).
--
-- (a) ORDER 31's migration granted carr_exporter the export VIEWS, but
--     exporters/targets.py's loop builder reads the BASE tables loop_block +
--     loop_item. Production bootstrap of the four loop- targets failed with
--     permission denied (proven under the exporter DSN; the staged files that
--     looked rendered were the rehearsal branch's artifacts). The exporter is
--     a trusted render role reading everything it renders; grant the two bases.
--
-- (b) ORDER 26's parked flip: the board's records mode needs ALL pool sources,
--     but v_export_pool is deliberately scoped to source='lead-router' (it IS
--     export target #8 and must not grow the 540 lane rows). Per the agent's
--     preferred option, a separate all-source view for consumers, exporter-
--     granted, same columns plus source. v_export_pool itself is UNTOUCHED.

begin;

grant select on loop_block, loop_item to carr_exporter;

create or replace view v_export_pool_all as
select
  source,
  source_seq,
  source_key,
  source_row,
  segment       as "SEGMENT",
  segment_play  as "THE PLAY",
  name          as "Name",
  vertical      as "Profession",
  address       as "Practice Address",
  city          as "City",
  county        as "County",
  email         as "Email",
  phone         as "Phone",
  status        as _status,
  dup_tier      as _dup_tier,
  dup_ref       as _dup_ref,
  est_lease_event,
  est_basis,
  score,
  score_basis
from prospect_pool;

comment on view v_export_pool_all is
  '0025: ALL-source pool read for the lead board''s records mode (ORDER 26 flip). '
  'v_export_pool stays router-scoped because it is export target #8; this view is '
  'the consumer read path and is NOT an export target.';

grant select on v_export_pool_all to carr_exporter;

commit;
