# carr-system

Version-controlled code spine of the CARR AI system (Joe Bookout + Dell McCraney, healthcare CRE). Phase 1 of the orchestrator migration, started 2026-07-24.

## The two-layer rule

Knowledge lives as markdown in the Google Drive vault (`My Drive/CARR AI/`) forever — it is Claude's cross-session memory. Only the deterministic spine of procedures lives here as code. A markdown SOP is never converted-and-deleted; it remains the plain-language spec beside its code. Full plan: `DNA/Deal Management/system-evolution-plan.md` in the vault.

## Layout

- `manifest.tsv` — the repo↔vault map, one row per synced code file; `tools/check.sh` reads it. Adding a file to the repo means adding its row here.
- `generators/` — the three board/feed generators (phase 1; repo is their runtime on Joe's Mac since phase 2)
  - `build-deal-room.py` — JSON→HTML Deal Room renderer. Reads `DNA/Team/live-boards/panhandle-team-deals.json`, writes `deal-room-panhandle.html`. NOT the Salesforce-export importer (that is a separate, still-open build — vault open-loops #84).
  - `build-lead-board.py` — the Lead Board builder. Writes `Automation/lead-board.html`.
  - `build-renewal-feed.py` — the renewal-radar feed builder. Writes `Automation/renewal-radar.json`.
- `pipelines/` (+ `pipelines/radar/`) — the rest of the durable pipeline code, imported phase 3 (2026-07-24). REPOINTED to repo runtime (verified identical first): `corroborate.py`, `build-space-search.py` (+ its template asset). Still vault-runtime pending a verified run: `build-front-door.py` (writes beside the script — needs an output-path patch first), `build-search-map.py`, `dso-match.py`, and the input-gated radar scripts (PECOS/license pools/tax rolls/date-founders — verify on their next real run, PECOS gate is October). Deliberately left out of the repo: the demoted Firefly receiver, deal-specific research one-offs, launchd plists.
- `shared/` — code on the shared DNA tier (Dell's lead-board template, the vendor intro-path tool, the fill-engine pair). Vault copies canonical for Dell's use; repo is version history + review.
- `video/` — the video lane's code, imported 2026-07-25 (it had been living untracked at `~/Movies/CARR Video Pipeline/Scripts` since it was built on Jul 22). AE ExtendScript comp builders + their shell/python drivers: lower thirds, stock b-roll clips, the animated-static builder, the watch-folder encoder, the Premiere XML cutter, and the parked CEP panel. Mapped by `manifest-video.tsv`, whose runtime root is `~/Movies/CARR Video Pipeline` rather than the vault — the pipeline lives outside Drive on purpose because media files are large and Drive sync would churn on them, but the CODE still belongs under version control. Repo is the source of truth, `~/Movies` is the runtime, and `run.sh check` now reports drift between them. No output baselines for this lane (video outputs are large binaries and do not belong in git); the review mechanism is the visual pass logged in the pipeline's own `CRITIQUE-LOG.md`. Reasoning and the exclusion list are recorded in the manifest header.
  - Animated statics: `make-animated-static.sh <layers> <name> [--concept K] [--sfx N] [--email] [--dry-run]`, planned by `plan-animated-static.py` against `choreography-log.tsv` so a recent shape is never repeated (7 concepts, 5 landing sounds; one sound per piece, varying between pieces). `--email` adds an Outlook-safe cut: legacy Outlook on Windows renders only a GIF's first frame, so that cut leads with the FINISHED card for one frame and does not loop. Social dosing doctrine lives in the vault at `Marketing/Social Media/social-media-workflow.md` (two per month, never consecutive weeks, IG/FB only, never X).
- `bin/` — shell utilities (automation-Chrome launcher, calendar fetcher).
- `baselines/` — committed snapshots of each generator's current output. Any code change must show its output diff against these before being accepted (the checkability guardrail: nobody reads this code line-by-line, so the outputs are what get reviewed). The two PII-bearing outputs (`lead-board.html`, `deal-room-panhandle.html`) are hash-only tracked since ORDER 42b (2026-08-06) — see the Boundaries section.
- `tools/check.sh` — code drift (every manifest row) + output drift vs baselines. `run.sh check`.
- `tools/health-check.py` — the rule-28 façade check as code (phase 3 pilot): every watched pipeline output tested for STALE-past-cadence and BEHIND-its-inputs. `run.sh health`; the daily heartbeat's JOB 4a calls it.
- `tools/writing-lint.py` — `DNA/writing-rules.md` as a deterministic gate (2026-07-25). `run.sh lint <file> [--surface email|social|proposal|web]`. HARD findings are hard bans with a per-instance signature (any hit fails, exit 1); REVIEW findings are hard-ban rules whose pattern has legitimate CRE uses (land, leverage-the-noun, landscape, shift) and always need a human; RATION covers the one-per-piece filler adverbs. It does NOT replace the `writing-audit` skill's judgment pass — it precedes it, the same way the ban list precedes the judgment checks inside writing-rules.md. Fixtures in `tools/fixtures/` are the contract: `clean-email.txt` must stay 0 HARD (false-positive guard), `dirty-social.txt` must keep catching every planted tell (detection guard); both are baselined and checked by `run.sh check`. When writing-rules.md gains a rule, add it here in the same edit and re-run `tools/writing-lint-baseline.sh`.

## Execution model (phase 2 live, 2026-07-24)

**On Joe's Mac this repo IS the runtime.** SOPs call `./run.sh deal-room|lead-board|renewal-feed|all`, which runs the repo generators against the vault (override the vault path with `CARR_VAULT`). Repointed 2026-07-24 after each repo run was verified output-identical to its vault-copy run (deal-room byte-identical; renewal-feed and lead-board identical back-to-back).

**The vault copies remain as the fallback + Dell's runtime** (cloud-only sessions, and Dell's side until his brain joins the repo). They must stay in sync with this repo. Change flow:

1. Edit the script here (fork + PR if you're Dell's side — see access model).
2. Run `tools/check.sh` — it reports code drift (repo vs vault) and output drift (vault output vs baseline).
3. When a change is accepted: run the pipeline via `run.sh`, verify the output diff is exactly the intended change, update the baseline, copy the script to its vault path (keeping the fallback in sync), commit baseline + code together.

## Access model (Joe's decision, 2026-07-24)

- Joe: owner, merges everything. Joe is the code-savvy partner; every fix lands through him.
- Dell's side: READ access via collaborator invite (when granted). Proposed changes come as pull requests from a fork — Dell's brain forks the repo, pushes the fix to its fork, opens a PR. GitHub notifies Joe automatically; Joe reviews the output diff and merges. Dell's side never pushes to this repo directly.
- Why fork-PRs and not branch protection: platform-enforced branch protection on private repos requires a paid GitHub plan; the fork model gives hard enforcement (Dell physically cannot write here) at $0, and upgrading to GitHub Pro later just relaxes this to same-repo branches + protected main.

## Boundaries

- This repo is PRIVATE and stays private. It is not, however, where client PII is meant to live: prospect/client PII never lives in GitHub (Joe's ruling (a), 2026-08-01; ORDER 42). The two output baselines that used to carry it in full — `baselines/lead-board.html` (~60,687 emails / 218 phones) and `baselines/deal-room-panhandle.html` (91/49) — moved to hash-only tracking under ORDER 42b (2026-08-06): the full HTML stays LOCAL and gitignored, only its sha256 is committed (`baselines/SHA256SUMS`); see `tools/check.sh`. `backups/*.sql.age` (encrypted full DB dumps) likewise moved out of git to the R2 archive under ORDER 42b; see `bin/backup-dump.sh` and `lib/r2_archive.py`. History predating ORDER 42b's purge may still carry these — see `ops/order42b-history-purge.md` for the purge status. The partner boundary is open (Joe and Dell share all client details); the external publication firewall is absolute.
- Nothing in this repo sends anything external. Generators write local files/artifacts only. Claude drafts, Joe sends — the permanent gate.
