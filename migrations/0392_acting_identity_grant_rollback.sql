-- 0392_acting_identity_grant_rollback.sql
--
-- The second half of the 0391 repair: take back every grant 0389 and 0390 handed
-- out, now that ops.work_request_acting_identity serves the card instead.
--
-- SEPARATE FROM 0391 ON PURPOSE. 0391 is additive and safe whenever; this one is
-- only safe once the Worker calling that function is actually serving. Applied
-- too early it revokes the grants the deployed inline joins still depend on and
-- breaks work-request-card a third time. Order is: apply 0391, promote the
-- Worker, then apply this.
--
-- THE RECEIPT GRANTS GO TOO, not just the boundary-breaking ones. A definer
-- function runs as its owner and needs nothing from the caller, so 0389's table
-- reads are now unused. Leaving them is how a boundary erodes: not by a decision
-- anyone made, but by a grant nobody later remembers is unnecessary.

revoke select on table public.actor from carr_reader;
revoke select on table public.tool_call from carr_reader;
revoke select on table ops.work_request_triage_receipt from carr_reader, carr_writer;
revoke select on table ops.sourced_work_request_plan_acceptance_receipt from carr_reader, carr_writer;
revoke select on table ops.sourced_work_request_outcome_feedback_acceptance_receipt from carr_reader, carr_writer;

do $$
begin
  if has_table_privilege('carr_reader','public.actor','select') then
    raise exception '0392 FAILED: carr_reader still holds direct public.actor select';
  end if;
  if has_table_privilege('carr_reader','public.tool_call','select') then
    raise exception '0392 FAILED: carr_reader still holds direct public.tool_call select';
  end if;
  if has_table_privilege('carr_reader','ops.work_request_triage_receipt','select')
     or has_table_privilege('carr_writer','ops.work_request_triage_receipt','select') then
    raise exception '0392 FAILED: a receipt-table grant from 0389 survived';
  end if;
  if not has_function_privilege('carr_reader','ops.work_request_acting_identity(text)','execute') then
    raise exception '0392 FAILED: the reader lost its projection along with the grants';
  end if;
end $$;
