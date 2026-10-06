-- Bound Jev admission scans to today's paid reservations under the spend lock.
-- rollback: forward migration dropping public.tool_call_jev_attempt_created_idx; no data or admission semantics change.
-- lock-review: app_reader catalog read 2026-10-06 found ~371k rows and 183 MiB heap in tool_call. The transactional runner cannot use CONCURRENTLY; reads remain available while writes pause. Abort lock acquisition after 2s and the build after 30s, rolling back for a later retry if either bound is exceeded.
set local lock_timeout = '2s';
set local statement_timeout = '30s';
create index tool_call_jev_attempt_created_idx on public.tool_call (created_at)
  where verb = 'ask-jev-attempt';
