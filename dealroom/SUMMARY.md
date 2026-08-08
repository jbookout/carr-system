# Deal Room front-end (WO-2)

Static app under `dealroom/`. Design authority: `design/mockups/dealroom-v2.html`.
Fixture mode is the default; zero backend required.

## Run

```bash
cd dealroom
python3 -m http.server 8787
# open http://127.0.0.1:8787/
```

Optional: `?mode=live&api=https://…` selects the live client (throws without a base URL; no network is dialed in this work order).

## Layout

| Path | Role |
|------|------|
| `index.html` | Shell: top bar, board, deal page mount, whisper, confirm strip |
| `css/app.css` | CARR tokens + all surfaces from the v2 mockup |
| `data/board-seed.json` | Mockup board-data as fixture seed (46 deals, threads, history) |
| `js/client.js` | Shared client interface + phase icon map |
| `js/fixture-client.js` | In-memory WO-1 contract (event log, presence, writes, confirms) |
| `js/live-client.js` | Same interface; HTTP verb paths for a later deploy |
| `js/app.js` | Board, deal page, composer, poll, presence, Δ, demo |
| `js/uuid.js` | UUID v4 for write idempotency keys |
| `manifest.webmanifest` | Installable PWA shell (standalone) |

No build step. Cloudflare Worker can serve the directory as static assets.

## Mockup map

| Mockup element | Implementation |
|----------------|----------------|
| Ledger / Dense layout buttons | `.vibe.lay` toggles `body.dense`; separate axis from color |
| ☀️ / 🌙 / ◐ color schemes | `.vibe.col` light/night; `.vibe.cvd` color assist (capability-named) |
| Parchment paper / warm cards | `--paper #F2E9D8`, `--card #F7F0E1`, `--field #F4EBDA` (no pure-white fields on light) |
| Stat tiles | `#stats` from live deal set |
| Filter chips + Δ | Chips filter board; Δ = deal ids with events after `last_call_at` from the event log |
| Phase pills + navy ladder | `.ph[data-p=…]` tokens; click cycles phase via `patch-deal-field` |
| Icon language 🔥🔍🤝⚖️📋🔑✅ | `PHICON`; ⚠️ overrides for attention or overdue |
| Owner avatars J/D | `.own`; click cycles joe/dell/unassigned |
| Next-step cell + 📝 | Display + quick-note button inside cell; cell click opens composer (not contenteditable overwrite) |
| Presence `dellcell` | `.flagged` + `::before` name flag from poll `presence[]`; partner avatar `.here` |
| Whisper toasts | `#whisper` |
| Confirm strip | `#confirms`; Yes/Skip only; nothing auto-writes |
| Deal page (brief / facts / notes / history) | `#dealview` via `getDeal`; next-step thread newest-first |
| Jot row | Bottom row creates On Deck deal via `createDeal` |
| Simulate the call | Fixture `simulatePartnerCall`: partner presence, partner write, distill proposals |
| 760px breakpoint | Same column hide + 2-col stats as mockup |
| prefers-reduced-motion / focus-visible | CSS from mockup; demo scroll respects reduced motion |

## Client interface

Both clients implement:

```
getBoard() -> { deals, as_of, last_call_at }
getDeal(id) -> { deal, thread[], critical_dates[], history[] }
getChanges(cursor) -> { events[], presence[], cursor }   // poll ~1s
presenceLease({ deal, field, idempotency_key })
patchDealField({ deal, field, value, base_event_id, idempotency_key })
resolveConflict({ conflict_id, winner: "a"|"b", idempotency_key })
addDealNote({ deal, text, idempotency_key })
setNextStep({ deal, text, next_date?, idempotency_key })
createDeal({ name, idempotency_key })
```

Fixture extras (demo / confirm strip):

```
getPendingConfirms() / resolveConfirm({ proposal_id, accept, idempotency_key })
simulatePartnerCall()
```

Writes always carry a UUID v4 `idempotency_key`. Presence lease TTL ~3s; UI renews while the composer is open. Conflict path returns both values; UI is two-tap Keep A / Keep B.

## Next-step thread (ruling not in the mockup)

- The cell never overwrite-edits.
- Click opens a composer: current step (read-only) + one input + **Add note** / **Set as next step**.
- Add note → `add-deal-note` (context stays; next step unchanged).
- Set as next step → `set-next-step`; prior step auto-archives into the deal thread with attribution.
- Quick note 📝 is a short path to `add-deal-note` without opening the full composer.
- Deal page shows the full append-only thread (notes + archived steps).

## Deviations from the mockup (and why)

1. **Next-step is not contenteditable.** Mockup used inline overwrite; final ruling requires append-only composer + thread.
2. **No Google Fonts request.** Tokens keep Oswald/Montserrat stacks with system fallbacks so fixture mode needs zero network.
3. **Demo is fixture-client driven**, not a closed script mutating local arrays. Presence, writes, and confirms flow through the same poll/event paths as a real partner.
4. **Phase / owner are click-to-cycle** for phone-legible editing against `patch-deal-field` (mockup phase was display-only outside the demo).
5. **Mode pill** (`Fixture` / `Live`) is chrome so operators know which client is active; not in the mockup.
6. **Empty due shows `-`** instead of an em-dash (user-visible em-dash ban).
7. **PWA manifest** added so the 760px floor can ship as installable; no service worker yet.

## Hard boundaries observed

- Only `dealroom/` touched.
- No websockets; 1s poll of `getChanges`.
- No secrets, no live endpoints dialed in fixture mode.
- Orange is chrome/action only; deal data uses ink/phase ladder.
- Color assist is capability-named (◐), never person- or condition-named.
