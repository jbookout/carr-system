# carr-system

Version-controlled code spine of the CARR AI system (Joe Bookout + Dell McCraney, healthcare CRE). Phase 1 of the orchestrator migration, started 2026-07-24.

## The two-layer rule

Knowledge lives as markdown in the Google Drive vault (`My Drive/CARR AI/`) forever — it is Claude's cross-session memory. Only the deterministic spine of procedures lives here as code. A markdown SOP is never converted-and-deleted; it remains the plain-language spec beside its code. Full plan: `DNA/Deal Management/system-evolution-plan.md` in the vault.

## Layout

- `generators/` — the pipeline scripts (phase 1: the three already-code generators)
  - `build-deal-room.py` — JSON→HTML Deal Room renderer. Reads `DNA/Team/live-boards/panhandle-team-deals.json`, writes `deal-room-panhandle.html`. NOT the Salesforce-export importer (that is a separate, still-open build — vault open-loops #84).
  - `build-lead-board.py` — the Lead Board builder. Writes `Automation/lead-board.html`.
  - `build-renewal-feed.py` — the renewal-radar feed builder. Writes `Automation/renewal-radar.json`.
- `baselines/` — committed snapshots of each generator's current output. Any code change must show its output diff against these before being accepted (the checkability guardrail: nobody reads this code line-by-line, so the outputs are what get reviewed).
- `tools/check.sh` — the drift + diff check (see below).

## Phase-1 execution model (important)

The VAULT copies of these scripts are still the live executing copies — scheduled runs and SOPs point at the vault, not this repo. This repo is version control and review, not yet the runtime. Change flow until runs are repointed:

1. Edit the script here (branch if you're Dell's side — see access model).
2. Run `tools/check.sh` — it reports code drift (repo vs vault) and output drift (vault output vs baseline).
3. When a change is accepted: copy the script to its vault path, re-run the pipeline, verify the output diff is exactly the intended change, update the baseline here, commit both together.

## Access model (Joe's decision, 2026-07-24)

- Joe: owner, merges everything. Joe is the code-savvy partner; every fix lands through him.
- Dell's side: READ access via collaborator invite (when granted). Proposed changes come as pull requests from a fork — Dell's brain forks the repo, pushes the fix to its fork, opens a PR. GitHub notifies Joe automatically; Joe reviews the output diff and merges. Dell's side never pushes to this repo directly.
- Why fork-PRs and not branch protection: platform-enforced branch protection on private repos requires a paid GitHub plan; the fork model gives hard enforcement (Dell physically cannot write here) at $0, and upgrading to GitHub Pro later just relaxes this to same-repo branches + protected main.

## Boundaries

- This repo is PRIVATE and stays private: baselines contain client deal data. The partner boundary is open (Joe and Dell share all client details); the external publication firewall is absolute.
- Nothing in this repo sends anything external. Generators write local files/artifacts only. Claude drafts, Joe sends — the permanent gate.
