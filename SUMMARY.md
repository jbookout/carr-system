# WO-1 Deal Room API

## Contract built

- `GET /pipeline/changes?cursor=<opaque>` is OAuth-protected and returns `{ events, presence, cursor }`. Events are deal-only, ordered by `(recorded_at,id)`, paged by an opaque keyset cursor, and recursively stripped of both Salesforce placeholder fields. Presence includes only unexpired field leases.
- `presence-lease` accepts `{ idempotency_key, deal, field }` and upserts an approximately three-second lease without writing an event.
- `patch-deal-field` accepts `{ idempotency_key, deal, field, value, base_event_id }` for `phase`, `owner`, `attention`, and `next_date`. Same-field writes serialize and compare against the field event base; different fields do not conflict. Conflicts retain both values and actors.
- `resolve-conflict` accepts `{ idempotency_key, conflict_id, winner }`, applies the winner through the normal field update/event path, and records the resolver.
- `add-deal-note` and `set-next-step` append attributed thread rows. Next-step rows are never overwritten; the newest row is current and prior rows remain the archive.
- `get-deal-room` is the single-deal read surface, returning board fields, newest-first attributed thread, critical dates, and newest-first attributed history. It is a verb so it reuses the existing authenticated MCP read path and reader connection.

## Migration

`migrations/0079_deal_room_api.sql` adds Deal Room fields, presence leases, append-only notes, conflict records, reader-safe views, writer grants, and the partial `(recorded_at,id)` deal-event cursor index.

## Verification

`cd mcp-server && npm test` runs five self-contained `node:test` cases using an in-memory query client and fake clock. No database or network is used.

## Deviations

None from the API behavior brief. Commit creation is environment-blocked because the shared worktree Git index is read-only to this sandbox; source and test changes remain unstaged in the worktree.
