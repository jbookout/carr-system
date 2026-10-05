-- Bound Jev admission scans to today's paid reservations under the spend lock.
create index tool_call_jev_attempt_created_idx on public.tool_call (created_at)
  where verb = 'ask-jev-attempt';
