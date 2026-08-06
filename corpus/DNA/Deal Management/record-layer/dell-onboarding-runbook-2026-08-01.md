# Dell onboarding runbook — the Monday sitting (in person at Dell's office)

*Prepared 2026-08-01 by the Fable supervisor seat so ANY session can run this tap-and-go. Full-context sibling: `DNA/Team/dell-adoption-runbook.md` (the standing adoption doc); THIS page is the one-sitting script. Readiness verified 2026-08-01: connector identity mapping READY (dell.mccraney.carr.us@gmail.com → dell, bearer fallback in place) · compiled-rules-dell RENDERS LIVE (DNA/compiled-rules-dell.md, empty-with-header = correct day one) · find/catch-me-up VERIFIED WORKING live (the 7/31 "known broken" note was fixed by ORDER 7 the same night).*

**Minimum in-person set if time is tight (10 min): items 1 + 2. Get item 4's two rulings in the hallway regardless. Item 3 survives distance.**

## 0. PREREQUISITES — check these in the first 60 seconds (added 2026-08-03)
**No other step in this runbook verified these, and every one of items 2-4 stalls without them.** Ask before opening anything:
1. **Does Dell have Claude Code on his Mac** — the desktop app, the CLI, or both? Items 2, 3 and the hook install all need a terminal and a local session on HIS machine. If it is missing, install first: the desktop app from claude.ai/download, or `npm install -g @anthropic-ai/claude-code` if he already has node. (Joe's own CLI turned out to be missing as late as 2026-08-03, so do not assume.)
2. **Does he have his own claude.ai subscription** (Pro or Max)? The connector and Remote Control both require one; API keys are not supported and a shared login is not a plan.
3. **Is he signed in as `dell.mccraney.carr.us@gmail.com`?** The identity map is frozen to that address — any other Google account is refused by design, not by accident.

**WHICH SURFACE DOES WHAT, so nobody hunts for the wrong window.** CLAUDE CODE ON DELL'S MAC is the main body of the sitting: the repo clone, the hook install, his `.claude/skills/`, his scheduled tasks (local by the Jul 10 placement law), and the joint test that proves a hook refuses him. HIS CLAUDE DESKTOP APP is where the connector gets INSTALLED (item 1) — connectors are managed there, not on the phone. HIS PHONE is where the connector is then VERIFIED, because done-test 7 is specifically a phone answer carrying `actor = dell`, and the phone is the field-capture surface that makes the whole thing worth having. COWORK NEEDS NO INSTALL — it reads Drive; nothing to do for it today.

**ORDER MATTERS:** clone the repo (item 2) BEFORE the hook install, because `ops/config-as-code.py` lives inside the repo. Restart his session after installing hooks — they load at session start.

## 1. Phone connector connect (~5 min) — closes OAuth done-test 7
**Domain ruling (Joe, 2026-08-01): api.doctorcre.com is the PRIMARY name as of Dell's onboarding — his devices only ever learn the new name.** practicecre.com stays live as an alias (Joe's phone reconnects whenever convenient, or never); its retirement is a later, unhurried decision. Both names answer on the same Worker.
1. **INSTALL ON HIS DESKTOP APP, NOT ON THE PHONE** — Claude desktop → Settings → Connectors → add custom connector → `https://api.doctorcre.com/mcp`. Corrected 2026-08-03 from Joe's firsthand account of his own 7/31 connect: *"It was live on my phone, but I had to install the connector on desktop."* Connectors are account-level, so adding once on desktop makes it available on the phone; the original wording here said "Dell's phone → Settings → Connectors" and would have sent them hunting a menu on the wrong device. The ORDER 9 done-test text reading "Joe adds the connector on his phone" described where it was VERIFIED, not where it was installed.
2. Sign-in with Google → **dell.mccraney.carr.us@gmail.com** (must be exactly this account — the identity map is frozen to it).
3. Test from his phone chat: "what's my deal board?" — a live answer closes done-test 7.
4. If the grant screen loops or 401s: one-tap reconnect is the designed recovery (proven on Joe's phone 7/31). If it still fails, capture the error text and park — do NOT debug live in the sitting.
5. After success, note it on the team board (T-row) so ORDER 9's log can close test 7, and queue the PARTNER_TOKENS retirement + allowPlainPKCE flip (the two follow-ons waiting on this test).

## 2. Re-clone carr-system on Dell's Mac (~5 min) — T60
History was rewritten 8/1 (PII purge). His clone will not fast-forward.
1. On his Mac: rename the old clone aside (never delete — his call later): `mv ~/carr-system ~/carr-system-pre-rewrite-keep`
2. Fresh clone: `git clone https://github.com/jbookout/carr-system.git ~/carr-system`
3. Confirm HEAD is at/after `871b8ed` (`git log --oneline -1`).
4. Close T60 via `update-loop`/`close-loop` (outcome: re-cloned, date).
Note: his repo access is read + fork-PR; nothing else about his workflow changed. The purged files live on as Drive zips in `CARR AI/Archive/snapshots/`.

## 3. The "say it and it's a record" walkthrough (~15 min, can move to Wednesday remote)
The point: Dell watches ONE capture land end-to-end, then does one himself. Script:
1. Joe (or the session) asks Dell for one real thing from his week — a vendor coffee, a client call, an intro.
2. Say it in the session in plain words. The session runs the debrief routing: `log-activity` on the record (+ `links[]` if an intro) — and shows Dell the verb call and the `ok`.
3. Open the rendered surface he already knows (vendors.xlsx row / the dossier / hunt-ledger) and show the same fact THERE, arrived by render, no file edited.
4. The teach half: Dell states one standing preference in his own words ("always X for my clients"). Capture via `teach` (scope personal, HIS verbatim words), activate on his yes, `run.sh export --only compiled-rules` — then show him `DNA/compiled-rules-dell.md` carrying his rule. **His file goes from empty to his first rule while he watches. That is the whole lesson.**
5. Close: "anything you say in any session is a record; the files are just views. You never edit a generated file — and you never need to."

## 4. Dell's two pending rulings (get these in person even if 3 slips)
- **R-D1 — spreadsheet renders keep/retire:** the 20+ dossier renders and fallback workbook set are classified fallback-tier/Dell-hold readable forms (fable-orders ruling 6). Dell rules: does HIS practice want the per-dossier files kept rendering, or does he work from verbs/boards and let them thin out at Wave 5? (No wrong answer; his side, his call.)
- **R-D2 — reader DSN for his board scripts:** his board scripts can get a views-scoped reader DSN (like Joe's exporter credential) so his Mac renders boards locally without the Worker. Dell rules: wants the DSN provisioned now, or stays on rendered files until the app (Wave 5)?
Record both via the team board + decision-history; if either reads as a standing preference, capture via `teach` (his verbatim words).

## Abort paths
Anything failing live: park it, keep the sitting warm — the retreat mode in `DNA/Team/twin-system-playbook.md` (interim mode) still works and nothing here is one-way. First-week trouble triggers are in Joe-Start-Here.
