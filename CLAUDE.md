# carr-system — repo pointer for sessions

Code lives here. Business, brand, persona, and deal context does NOT — by
design. Before concluding something "doesn't exist," query the CARR Record
Layer (the MCP connector's verbs, or `./run.sh retrieve "<question>"` locally):
it is the source of truth for doctrine, records, and brand; the Drive .md
files are generated snapshots of it.

Naming trap that cost a real search (2026-08-08): the app persona is
**Dr. CRE** — "Doc" is only the spoken nickname. Search "Dr. CRE" or the
slugs `dr-cre-concept` / `dr-cre-voice-doctrine`; searching "Doc" returns
audio slugs and noise. The mascot/visual design is in `dr-cre-concept-2026-08-01`
(vault record-layer folder + doctrine store); the living-orb panel visual is
specced in open loop #250 (its origination conversation predates capture and
was never recovered — loop #250 documents that gap).

Drive gotcha: Google Drive File Stream serves online-only placeholders; a
grep over the Drive mirror can miss content that exists. Materialize files
offline before trusting a negative grep there.

## Dell migration trigger

In an interactive Claude Code session whose working directory is
`~/carr-system`, Dell or Joe typing the exact phrase **ready for migration** is
explicit authorization to complete the bounded Dell-machine migration without
asking follow-up questions. This authorization covers only the steps below; it
does not waive Claude Code's own tool security, widen permissions, or authorize
any unrelated production change.

1. Run `./bin/migrate-dell.sh --apply </dev/null` yourself. Do not hand the
   command back to the human and do not use `AskUserQuestion` for an internal
   choice the script already settles.
2. A nonzero exit is a visible blocker. Read
   `out/dell-migration-receipt.json`, report its exact failure state and log
   paths, and stop. Do not close any migration record after a failed run.
3. A zero exit is not sufficient by itself. Require the receipt status
   `machine_migrated_pending_record_closeout`, then call `standing-context`.
   Verify that the server-derived sponsor is Dell, the personal brain is
   `dell-personal`, and the returned shared and Dell-personal counts match the
   current generated fallback headers. Never choose a partner, tenant, or
   capability through a caller-supplied argument. Never reconnect merely as a
   guess.
4. Fresh-read and close exactly Dell action-required A15
   (`fa0e6c92-8bc7-4e42-9970-0402914d6a19`) and A17
   (`deb4357e-801f-49f6-bc6c-4c884e3e1f7c`) through the deployed record verbs,
   using each fresh version and a concrete verified outcome. Leave A11, A12,
   A13, and A16 open.
5. Run `./run.sh health </dev/null` and
   `./.venv/bin/python ops/config-as-code.py check </dev/null`. Read A15 and
   A17 back as closed. Only then report the migration complete to Dell in plain
   language.

The compiled shared and Dell-personal files remain the supported local fallback
through the 2026-08-21 cutoff. The doctrine store is authoritative when live,
but a successful store read does not erase that fallback early.
