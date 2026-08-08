# WO-4 Capture Bridge Summary

## Built contracts

- `POST /capture/claim` authenticates a bearer against `CAPTURE_TOKENS` before reading the request body. The matching map key must equal `device_id` and resolve to an existing machine actor. The endpoint accepts `{nonce, device_id, mode: "meeting", started_at, consent: {announced_at}}`. Missing consent proof is refused. A nonce is globally one-time. Success returns a random opaque `session_token` with a 24-hour TTL, while only its SHA-256 hash is stored.
- `POST /capture/status` accepts `{session_token, state, at, detail?}`. Forward and same-state updates are accepted until a terminal state. Backward updates are refused with 409. `done` and `failed` refuse every later update with 409.
- `POST /capture/candidates` accepts `{session_token, idempotency_key, items}`. Each item has `{kind, payload, evidence_quote, confidence}`. The entire batch is validated before storage. A quote over 15 words refuses the whole request. Payloads with transcript, audio, raw-text, verbatim, or human-quote fields are refused, and payload size is bounded. Reusing the same batch key does not duplicate candidates. Every stored item starts as `pending`; storage writes neither a business record nor a deal event.
- `GET /capture/session` authenticates with `Authorization: Bearer <session_token>` and returns `{state, candidates: {pending, confirmed, skipped}, meeting_record}`. `meeting_record` remains null until a confirmed `meeting_record` candidate has produced a real activity id.
- Read verb `capture-queue` returns pending candidates from active, unexpired sessions ordered by confidence and creation order. It includes kind, untrusted payload, capped evidence quote, confidence, resolved deal name when available, and a string timestamp. Confidence is ordering data only.
- Write verb `resolve-candidate` is human-only and is not added to any narrow automation profile. Reject marks only `skipped`. Accept maps the candidate kind to the live registered verb path: `phase_move` to `patch-deal-field`, `next_step` to `set-next-step`, `new_deal` to `new-deal`, and `activity` or `meeting_record` to `log-activity`. A meeting record forces activity kind `meeting`. The inner write uses the confirming partner actor and a stable candidate-derived idempotency key. Confirmation is stored only after the inner verb succeeds and returns a real ref. Inner failure leaves the candidate pending through the enclosing transaction.

## Migration

`migrations/0081_capture_bridge.sql`

It creates `capture_session`, `capture_candidate`, `v_capture_session_status`, and `v_capture_candidate_queue`. The reader role receives only view grants. The writer role receives the base-table permissions required by claim, status, candidate storage, session-token polling, and resolution. The database also enforces the candidate kind/status vocabularies, confidence range, resolution invariants, uniqueness, and the 15-word evidence limit.

## Deal Room polling visibility

`/pipeline/changes` now includes a `capture_sessions` snapshot alongside its existing `events`, `presence`, and `cursor` fields. This is the smallest extension consistent with the polling contract: capture status is current session state, so it does not need a second event stream or cursor. Every poll sees the current active-session state, while the existing deal-event cursor and ordering remain unchanged. Both capture timestamps use `to_jsonb(column)#>>'{}'` and therefore serialize as strings.

## Human provisioning for `CAPTURE_TOKENS`

1. Choose the stable device slug that will also identify its machine actor, for example `mac-studio`.
2. Through the sanctioned database migration/provisioning lane, ensure an active machine actor exists with that exact slug. This worktree does not create or provision an actor row.
3. Generate a high-entropy token locally. Do not place it in git, shell history, logs, screenshots, or this file.
4. Read the currently provisioned `CAPTURE_TOKENS` map through the authorized secret-management process, add the new `"device-slug": "token"` entry without dropping existing entries, and replace the Worker secret with the complete JSON map. `wrangler secret put CAPTURE_TOKENS` is the expected Cloudflare operation when run by the authorized human or orchestrator.
5. Put that same token in the local rig's protected configuration and send it only as the bearer on `/capture/claim`. Never reuse it as a session token.
6. Verify a claim from that device, then verify an invalid bearer receives 401. Rotate by replacing the map value and the rig value together.

No secret was created, read, changed, or deployed by this work order.

## Deviations and clarifications

- The brief's candidate body example omitted the idempotency key while separately requiring idempotent reposts. The built contract adds required top-level `idempotency_key` to `/capture/candidates` so the requirement has an explicit wire representation.
- The requested commits could not be created in this managed worktree. `git add` was refused because the sandbox cannot create `/Users/booko/carr-system/.git/worktrees/wo4-capture-bridge/index.lock`. The patch is complete and unstaged; the orchestrating session must create the granular commits after regaining Git metadata write access.
- No behavioral deviation from the brief is known.

## Test evidence

Run from `mcp-server/`:

```text
npm test
17 tests passed
0 tests failed
```

The suite is self-contained with an in-memory query fake and no database, network, secret, migration, or deploy access.
