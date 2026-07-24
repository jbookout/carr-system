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
- `pipelines/` (+ `pipelines/radar/`) — the rest of the durable pipeline code, imported phase 3 (2026-07-24) for version control; their VAULT copies remain the SOP-invoked runtime until each is repointed after a verified identical run. Deliberately left out: the demoted Firefly receiver, deal-specific research one-offs, launchd plists.
- `shared/` — code on the shared DNA tier (Dell's lead-board template, the vendor intro-path tool, the fill-engine pair). Vault copies canonical for Dell's use; repo is version history + review.
- `bin/` — shell utilities (automation-Chrome launcher, calendar fetcher).
- `baselines/` — committed snapshots of each generator's current output. Any code change must show its output diff against these before being accepted (the checkability guardrail: nobody reads this code line-by-line, so the outputs are what get reviewed).
- `tools/check.sh` — code drift (every manifest row) + output drift vs baselines. `run.sh check`.
- `tools/health-check.py` — the rule-28 façade check as code (phase 3 pilot): every watched pipeline output tested for STALE-past-cadence and BEHIND-its-inputs. `run.sh health`; the daily heartbeat's JOB 4a calls it.

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

- This repo is PRIVATE and stays private: baselines contain client deal data. The partner boundary is open (Joe and Dell share all client details); the external publication firewall is absolute.
- Nothing in this repo sends anything external. Generators write local files/artifacts only. Claude drafts, Joe sends — the permanent gate.
