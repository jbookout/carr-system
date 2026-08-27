# carr-system — repo pointer for sessions

Code lives here. Business, brand, persona, and deal context does NOT — by
design. Before concluding something "doesn't exist," query the CARR Record
Layer (the MCP connector's verbs, or `./run.sh retrieve "<question>"` locally):
it is the source of truth for doctrine, records, and brand.

The cutoff fired 2026-08-19: the generated Drive .md files are GONE, moved to
`_to_delete/md-renders-cutoff-20260819` in the vault, and the exporter now
prints RETIRED instead of rewriting them. There is no compiled-rules file and
no vault CLAUDE.md to fall back on — a session that cannot reach the store has
no second surface to read, and must say so rather than working from memory.

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

## A capture-verb denial has a fallback door

`report-problem`, `record-defect`, and `add-loop` are now directly
permission-allowlisted across all three MCP prefixes (`.claude/settings.json`),
closing the classifier coin-toss that once denied them (commit 18872b66). If a
DIFFERENT record-layer write verb is ever denied by the classifier, file it in
THIS session through the Bash door instead of stopping:

    ./run.sh call <verb> '<the same JSON you would have passed the verb>'

Same `mcp-server/src/tools.js` registry the connector serves, reached through
`tools/call-verb.py`, separately allowlisted as `Bash(./run.sh *)`. It carries
no credential (the receipted break-glass close stays a human's call) and it is
not `call-verb` — never reach for that generic passthrough to get at a verb
the classifier declined; that is working around a denial rather than filing it.

## Map work has one mandatory front door

For any request to recommend, design, build, revise, review, or publish a map,
GIS analysis, route, day trip, or Tour surface, call the live `map-architecture`
verb first. It returns the current doctrine and machine-contract pointer.

The configured Stop gate no longer holds the turn open on this. Joe's 2026-08-23
Stop-gate rationing, off that day's gates-audit council, left only three hooks
able to reopen a turn (core conduct, completion-evidence, drift-assertion), and
this one now ANNOUNCES instead: a governed session that skips the verb is told
so, and the skip is recorded in `out/map-architecture-gate.jsonl`, but nothing
stops the session ending. So the front door is still mandatory and it is now
enforced by the session making the call, with a loud record when it does not.

## Dell migration trigger

In an interactive Claude Code session whose working directory is
`~/carr-system`, Dell or Joe typing the exact phrase **ready for migration** is
explicit authorization to complete the bounded Dell-machine migration without
asking follow-up questions. This authorization covers only the steps below; it
does not waive Claude Code's own tool security, widen permissions, or authorize
any unrelated production change.

Dell's launch machine is Claude-only. The complete absence of `~/.codex` is an
expected supported state, and the migration skips Codex-specific configuration
without creating it. A partial Codex state fails visibly instead of being
silently treated as absent.

1. Run `./bin/migrate-dell.sh --apply </dev/null` yourself. Do not hand the
   command back to the human and do not use `AskUserQuestion` for an internal
   choice the script already settles.
2. A nonzero exit is a visible blocker. Read
   `out/dell-migration-receipt.json`, report its exact failure state and log
   paths, and stop. Do not close any migration record after a failed run.
3. A zero exit is not sufficient by itself. Require the receipt status
   `machine_migrated_pending_record_closeout`, then call `standing-context`.
   Verify that the server-derived sponsor is Dell and the personal brain is
   `dell-personal`. The old cross-check against the generated fallback headers
   is retired with the files themselves (cutoff 2026-08-19); the counts
   standing-context returns are the counts. Never choose a partner, tenant, or
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

There is no local fallback any more. The doctrine store is the only doctrine
surface; if it is unreachable, stop and say so.
