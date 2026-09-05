#!/usr/bin/env python3
"""config-as-code.py — the machine config belongs in the repo, same as the code.

WHY (Joe, 2026-08-03: "shouldnt all code be in the repo? .json is code").

He is right, and the exposure was worse than the one file he was pointing at.
Measured that night:

    ~/.claude/settings.json      1 on disk,  0 in the repo   <- fires all 5 hooks
    launchd plists               2 on disk,  0 in the repo   <- incl. the hourly
                                                                rule refresh
    scheduled tasks             15 on disk,  4 in the repo

So the five hook SCRIPTS were version-controlled and the thing that makes them
run was not. That is the two-homes disease the whole system is built to avoid:
the same night, hooks/SETTINGS-BLOCK.md was found to have silently drifted — it
documented two hooks and the live file had four. A document DESCRIBING config
drifts. Config in the repo does not.

WHAT THIS DOES NOT DO, and the reason is not squeamishness. It does not put
`~/.claude/settings.json` in the repo wholesale. That file also carries 77
permission entries and notification prefs that are Joe's, machine-shaped, and
churn weekly — committing them would create a second home for something Claude
Code only ever reads from ~/.claude/, and a baseline would go stale exactly the
way SETTINGS-BLOCK.md did. Only the CARR-OWNED `hooks` block is tracked, and
`install` merges it back leaving every other key untouched.

THE PATTERN IS THE ONE THE SYSTEM ALREADY USES: source in the repo, render on
the machine. settings.json becomes a render, the same way clients-active.md is.

PORTABILITY IS THE POINT, NOT A BONUS. Repo copies store {{HOME}}, {{REPO}} and
{{VAULT}} instead of /Users/booko. That is what lets the same source install on
Dell's machine — and 54 of the 70 active rules are SHARED scope, binding him
exactly as they bind Joe, with zero mechanical enforcement on his side today.

    ops/config-as-code.py check      # drift report; exit 1 if any. THE DEFAULT.
    ops/config-as-code.py pull       # machine -> repo (capture what is live)
    ops/config-as-code.py install    # repo -> machine (deploy; needs --apply)
    ops/config-as-code.py install-codex-continuity --apply
    ops/config-as-code.py verify-codex-continuity
    ops/config-as-code.py remove-codex-continuity --apply

`check` is what belongs in run.sh health: it answers "is the live config still
the config we think we have", which is the question nobody could answer tonight.
"""

import copy
import json
import os
import plistlib
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.machine_prerequisites import machine_prerequisites, prerequisite_failure_report

HOME = os.path.expanduser("~")
# THE CHECKOUT THIS FILE SITS IN — the source of the tracked copies to compare.
REPO_HERE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _canonical_repo(here):
    """The MAIN checkout, even when this runs from a linked worktree.

    WHY THIS IS NOT just `here` (fixed 2026-08-13). {{REPO}} tokenizes paths
    inside the MACHINE's installed config — launchd plists, settings.json — and
    the machine has exactly ONE installation, which always points at the main
    checkout. Deriving the token from this file's own location meant that running
    from a linked worktree tokenized to the WORKTREE path, so every live item
    referencing the real checkout compared unequal. Measured the same day: the
    identical command reported `OK — 36 items, repo matches machine` in the main
    checkout and `DRIFT — 18 of 36 items` from a worktree, on an unchanged
    machine. Nothing had drifted.

    That false positive was not cosmetic. It failed the config-as-code class in
    pre-push CI, which blocked pushing from a worktree — and working from a
    worktree is the standing remedy when the shared tree is busy, so the check
    broke the escape hatch. Worse, its stated remedy is `config-as-code.py pull`,
    which would have captured the machine's absolute paths INTO the repo,
    destroying the {{REPO}} templating that exists so Dell's clone works at a
    different path. Following the advice would have made the repo unportable.

    git's own answer is authoritative and cheap: --git-common-dir resolves to the
    MAIN repository's .git from inside any linked worktree (a worktree's own
    --git-dir points into .git/worktrees/<name>). Falls back to `here` when git
    is unavailable or this is not a checkout at all, which is the pre-existing
    behaviour and correct for the non-worktree case."""
    try:
        out = subprocess.run(
            ["git", "-C", here, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=15)
        if out.returncode == 0 and out.stdout.strip():
            common = out.stdout.strip()          # .../carr-system/.git
            root = os.path.dirname(common)
            if root and os.path.isdir(root):
                return os.path.abspath(root)
    except (OSError, subprocess.SubprocessError):
        pass
    return here


REPO = _canonical_repo(REPO_HERE)
PREREQUISITE_CHECK = machine_prerequisites

# `git -C <path>` IS NOT A GUARANTEE OF WHICH REPOSITORY YOU HIT. Every variable
# below outranks both -C and the working directory, and git exports several of
# them to every hook it runs — so anything reached from a hook inherits them.
# That is the 2026-08-14 incident: a selftest whose fixture used cwd=mkdtemp()
# ran its git commands against the live checkout instead, rewrote local main
# onto its own seed commits, and marked the repository core.bare true.
#
# THIS FILE WAS ASSESSED AS READ-ONLY AND THAT WAS WRONG. Line ~844 runs
# `git -C REPO config core.hooksPath ops/githooks`, which is a WRITE. Under an
# inherited GIT_DIR that write lands in whatever repository the variable names,
# not in REPO — so `config-as-code.py install` invoked from a hook could point a
# different repository's hooksPath at this repo's ops/githooks. The four reads
# are the milder half: misdirected, they yield a wrong drift verdict rather than
# a wrong write, which is a lie in a checker whose entire job is detecting drift.
#
# GIT_CONFIG_COUNT is the subtle one and is why this list is not just GIT_DIR:
# its KEY_<n>/VALUE_<n> pairs can set core.worktree and relocate a call with
# GIT_DIR never appearing anywhere.
#
# A shared ops/git_env.py with the same job is in flight in another session's
# PR #19. It is not merged and not on origin/main, so this stands alone rather
# than importing something that does not exist yet; consolidate onto that helper
# when it lands, and delete this.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import scrubbed_env as _git_env  # noqa: E402

# CONSOLIDATED into ops/git_env.py (loop #371). This file carried its own
# _GIT_LOCATION_VARS tuple and _git_env() because git_env.py was not yet on main
# when the scrub landed here in PR #20 — importing something unmerged would have
# traded a real hole for an ImportError, so the local copy was correct at the
# time and is redundant now. The bounded GIT_CONFIG_COUNT loop that lived here
# was the better implementation and went the other way: git_env.scrubbed_env()
# now uses it, because the original spun on a hostile count.
#
# Aliased to _git_env so the five call sites below are untouched by this change.


def _find_vault():
    """The vault sits under a Drive mount named for the ACCOUNT, so a hardcoded
    default is Joe-only by construction — the exact defect this file exists to
    fix, one level down. Glob for it instead, so Dell's machine resolves his own
    mount. CARR_VAULT overrides."""
    env = os.environ.get("CARR_VAULT")
    if env:
        return env
    import glob
    hits = sorted(glob.glob(os.path.join(
        HOME, "Library/CloudStorage/GoogleDrive-*/My Drive/CARR AI")))
    return hits[0] if hits else ""


VAULT = _find_vault()

SETTINGS = os.path.join(HOME, ".claude", "settings.json")
TASKS_SRC = os.path.join(HOME, ".claude", "scheduled-tasks")
TASKS_REPO = os.path.join(REPO, "ops", "scheduled-tasks")
# A quarantined definition is deliberately outside Claude's active discovery
# directory.  It is recoverable evidence of what this reconciler removed, not
# a second scheduler source of truth.
TASKS_QUARANTINE = os.path.join(
    HOME, ".claude", "scheduled-tasks-quarantine", "carr-primary-only"
)
LAUNCHD_SRC = os.path.join(HOME, "Library", "LaunchAgents")
LAUNCHD_REPO = os.path.join(REPO, "ops", "launchd")
LAUNCHD_ALT_REPO = {
    "com.carr.call-mode.plist": os.path.join(
        REPO, "tools", "dictation-rig", "launchd", "com.carr.call-mode.plist"
    ),
}
HOOKS_REPO = os.path.join(REPO, "ops", "config", "hooks.json")
CODEX_HOOKS_SRC = os.path.join(HOME, ".codex", "hooks.json")
CODEX_HOOKS_REPO = os.path.join(REPO, "ops", "config", "codex-hooks.json")
CODEX_CONFIG = os.path.join(HOME, ".codex", "config.toml")
CODEX_CONTINUITY_EVENTS = ("PreCompact", "PostCompact", "SessionStart", "UserPromptSubmit")
CODEX_CONTINUITY_APP_EVENTS = {
    "PreCompact": "preCompact",
    "PostCompact": "postCompact",
    "SessionStart": "sessionStart",
    "UserPromptSubmit": "userPromptSubmit",
}
CODEX_APP_SERVER_TIMEOUT_SECONDS = 15
CODEX_APP_SERVER_OUTPUT_LIMIT = 4 * 1024 * 1024
CODEX_PERMISSIONS_REPO = os.path.join(REPO, "ops", "config", "codex-permissions.toml")
CODEX_PERMISSIONS_BEGIN = "# >>> CARR managed permissions >>>"
CODEX_PERMISSIONS_END = "# <<< CARR managed permissions <<<"

# Longest first: REPO and VAULT both sit under HOME, so substituting HOME first
# would leave "{{HOME}}/carr-system" and the REPO token would never match.
# EMPTY VALUES ARE FILTERED OUT, and that is not defensive padding: str.replace
# with an empty needle inserts the token between every character of the file.
# _find_vault() returns "" when no Drive mount matches, so one unmounted Drive
# would otherwise shred every tracked config on the next pull.
TOKENS = [(tok, real) for tok, real in
          (("{{VAULT}}", VAULT), ("{{REPO}}", REPO), ("{{HOME}}", HOME)) if real]

# RUNS ON EXACTLY ONE MACHINE. Not a statement about Joe; a statement about what
# the job writes. Each of these mutates state that is SHARED between the two
# machines, so a second copy is either duplicated work or a two-writer conflict.
# Widened 2026-08-10 during the Dell migration audit, when the set held only the
# video pipeline and the other five would have been installed on his Mac:
#
#   videopipeline       — Joe's Movies folder; Dell has no video pipeline.
#   nightly-record-layer— pushes the corpus to the shared vault and mirrors
#                         doctrine to a path hardcoded to Joe's Google Drive
#                         (bin/nightly.sh:154), which cannot resolve on another
#                         machine. The cadence engine inside it IS idempotent,
#                         so the risk is the vault writes, not double-spawning.
#   rules-refresh       — writes the shared compiled-rules renders, and the cost
#                         ruling in its own plist is decisive: Neon free is
#                         100 CU-h/month at ~5 min per wake, so a second Mac
#                         waking it hourly doubles the burn and can SUSPEND the
#                         database for the rest of the month.
#   local-briefs        — maintains Joe's local review queue. Legacy brief files
#                         are explicit recovery only; a second scheduler would
#                         duplicate the same owner-specific maintenance.
#   partner-ping        — writes the shared record. One pinger is the point.
#   cutover-watch       — writes the shared record (a loop update on #532) and
#                         holds its own sentinel of what it last reported under
#                         out/cutover-watch/, which is per-machine and would
#                         make two Macs disagree about what is "new" — the
#                         same partner-ping shape (one watcher, one shared
#                         record) with the added risk of two update-loop calls
#                         racing on the same loop's base_version.
#
# What the second machine still needs from the nightly is the record-derived
# fetch allowlist, which is per-machine and gitignored. That is why
# com.carr.fetch-allowlist.plist exists as its own job rather than being
# inherited from the nightly chain.
PRIMARY_ONLY = {
    "com.carr.videopipeline.plist",
    # com.carr.preflight-watch.plist was listed here until 2026-08-22. It watched
    # DELL's migration packet from Joe's Mac and was built to remove itself once
    # his A15 closed. A15 is closed, the watcher unloaded and deleted its own
    # plist as designed, and bin/preflight-watch.sh is retired with this entry —
    # which had been naming a plist that exists in neither ops/launchd/ nor
    # ~/Library/LaunchAgents. A lifecycle that completes should leave nothing
    # behind pointing at it (rule def3e84e, artifact tombstones: nothing
    # silently rots).
    "com.carr.nightly-record-layer.plist",
    "com.carr.rules-refresh.plist",
    "com.carr.local-briefs.plist",
    "com.carr.partner-ping.plist",
    "com.carr.cutover-watch.plist",
}


# The mirror image: jobs only the SECOND machine needs, because the primary
# already gets the same effect from a chain the second machine must not run.
SECONDARY_ONLY = {"com.carr.fetch-allowlist.plist"}


# Versioned definitions that deliberately must not become live merely because
# config-as-code reconciles the rest of the machine.  These adapters have their
# own evidence/approval cutover gates; installing one early would turn a source
# artifact into an active schedule before those gates pass.
DEFINITION_ONLY: dict[str, str] = {
    # com.carr.control-plane-tick.plist held here until 2026-08-26: its gate was
    # "accepted shadow/canary evidence and cutover approval". Joe approved the
    # cutover that evening (decision f4af0c87, "Yes I approve cutover") with the
    # first accepted shadow receipt on record; the wrapper pins --mode shadow,
    # so installing activates evidence production only — legacy schedules keep
    # running until each workflow's replacement is accepted at its own tier.
}

# A LaunchAgent that invokes this installer cannot unload its own label and
# still return to bin/run-scheduled.sh: launchd terminates the wrapper process
# tree, so the run never reaches its durable receipt.  The caller may identify
# exactly that one active label.  Every other plist retains the ordinary
# unload/load convergence below.  If the active plist changed, install fails
# closed without writing a misleading new body over the still-old loaded job;
# an external installer is the named remedy.  If unchanged, it remains loaded.
ACTIVE_LAUNCHD_LABEL_ENV = "CARR_CONFIG_AS_CODE_ACTIVE_LAUNCHD_LABEL"


# Claude scheduled-task definitions are not merely configuration files: their
# presence asks a local AI client to perform work later.  Every tracked task is
# therefore primary-only until its *own* machine scope has been reviewed and
# deliberately listed here.  The empty allow-list is intentional.  It keeps a
# Dell migration from turning Joe's existing task catalogue into Dell's queue,
# while still leaving a narrow, auditable path for a future Dell-specific task.
#
# This policy is fail-closed in both directions:
#   * a tracked primary task missing from a secondary machine is not drift;
#   * a CARR-managed task found on a secondary machine is visible drift and is
#     never pulled into the shared baseline; unrelated personal tasks are not
#     CARR configuration and this tool never claims, moves, or counts them; and
#   * secondary install never creates ~/.claude/scheduled-tasks; primary install
#     renders the tracked definitions it owns.
# The actual scheduler registration is outside this config reconciler, so
# copying a SKILL.md would be both insufficient and unsafe.
#
# STILL EMPTY, deliberately, and here is the near-miss that tried to change it.
# On 2026-08-18 source-of-truth copies of Dell's three personal tasks were added
# to ops/scheduled-tasks/, which made them CARR-owned by this file's own rule —
# "the repository is the ownership registry" — and dell-social-batch-weekly
# immediately reported as a secondary scope violation. The first fix was to list
# the three here. That was the wrong lever: it widens the machine-scope policy to
# paper over a filing mistake, and it also left ops/control-plane-selftest.py
# failing, because that selftest requires every *.SKILL.md directly under
# ops/scheduled-tasks/ to be a registered control-plane workflow, which personal
# tasks are not and should not be.
#
# The copies now live in ops/scheduled-tasks/dell/. Both this reconciler and the
# control-plane selftest read only the top level, so the subdirectory keeps the
# repo copies Dell asked for while leaving his tasks classified as what they are:
# personal configuration this tool does not claim, move, or count. Anything added
# to the TOP level is a CARR-managed primary task and must be registered as a
# control-plane workflow.
SECONDARY_SCHEDULED_TASKS: set[str] = set()


# EPHEMERAL SCAFFOLDING IS NOT CONFIGURATION, and treating it as such made this
# check chronically red — the exact failure the comment in cmd_check() warns
# about, arriving from a direction nobody anticipated.
#
# Sessions legitimately create throwaway scheduled tasks: handoff continuations,
# one-time catch-up runs, drills. On 2026-08-21 two of them blocked unrelated
# pushes within one hour. The second was created BY a session whose own
# description read "clear the config drift blocking the push" — it was stuck
# behind this gate and its attempt to hand the problem on became the next
# instance of the problem. No session could resolve either one correctly:
# capturing a throwaway into the repository pollutes it permanently and then
# inverts into MISSING drift the moment the task is removed, while moving or
# deleting it takes another live session's work.
#
# tracked_scheduled_task_paths() already states the governing rule in its own
# docstring: a task in Claude's user-owned directory that CARR does not own is
# personal, and must not "make CARR health falsely red for it". This is that
# rule applied to the drift comparison, which had never honoured it.
#
# A MARKER, NOT A HEURISTIC. Guessing from the name would be silently wrong in
# both directions. The task itself declares what it is, so a genuine CARR task
# that someone forgot to commit still shows up as drift — which is the part of
# this check worth keeping.
EPHEMERAL_MARKER = "ephemeral:true"


def is_ephemeral_scheduled_task(text):
    """True when a task's own frontmatter declares it session scaffolding.

    Only the frontmatter block is read, so the phrase appearing in prose lower
    down the file cannot exempt a real task by accident.
    """
    if not text:
        return False
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    for line in lines[1:]:
        if line.strip() == "---":
            return False
        if line.strip().lower().replace(" ", "") == EPHEMERAL_MARKER:
            return True
    return False


def scheduled_task_allowed(name):
    """Whether this machine may host the named CARR scheduled task.

    Unknown task names deliberately resolve to false on a non-primary machine.
    A new secondary task must be added to the explicit allow-list with its
    safety case, rather than becoming installable because it happened to appear
    in the repository.
    """
    return IS_PRIMARY or name in SECONDARY_SCHEDULED_TASKS


def tracked_scheduled_task_paths():
    """Tracked CARR task definitions, keyed by their scheduler directory name.

    The repository is the ownership registry.  A random task in Claude's
    user-owned directory is personal configuration, not CARR state: do not
    pull it, delete it, quarantine it, or make CARR health falsely red for it.
    """
    if not os.path.isdir(TASKS_REPO):
        return {}
    return {
        filename[:-9]: os.path.join(TASKS_REPO, filename)
        for filename in os.listdir(TASKS_REPO)
        if filename.endswith(".SKILL.md")
        and os.path.isfile(os.path.join(TASKS_REPO, filename))
    }


def secondary_scheduled_task_state():
    """CARR-owned task definitions active on a secondary, by safe disposition.

    Exact tracked renders are safe to quarantine.  A tracked name whose body
    differs is intentionally a hard stop: it might be a local change and this
    reconciler must not silently discard it.  Names outside the tracked CARR
    registry are personal tasks and are intentionally absent from this result.
    """
    state = {"exact": [], "modified": []}
    if IS_PRIMARY or not os.path.isdir(TASKS_SRC):
        return state
    tracked = tracked_scheduled_task_paths()
    for name in sorted(os.listdir(TASKS_SRC)):
        local = os.path.join(TASKS_SRC, name, "SKILL.md")
        source = tracked.get(name)
        if not source or not os.path.isfile(local) or scheduled_task_allowed(name):
            continue
        if portable(read(local)) == read(source):
            state["exact"].append(name)
        else:
            state["modified"].append(name)
    return state


def secondary_scheduled_task_violations():
    """Installed, disallowed CARR tasks for the drift report only."""
    state = secondary_scheduled_task_state()
    return state["exact"] + state["modified"]


def scheduled_task_install_plan():
    """Return a fail-closed repo-to-machine scheduled-task reconciliation plan."""
    if IS_PRIMARY:
        return {"install": sorted(tracked_scheduled_task_paths()), "exact": [], "modified": []}
    return {"install": [], **secondary_scheduled_task_state()}


def _owner_email():
    """The repo owner's git identity, read from the ONE place it is written.

    ops/githooks/pre-push has decided since 2026-08-03 who may push to main, and
    duplicating its constant here would create the two-copies problem this file
    exists to prevent. Parsed rather than re-declared; the shell hook is left
    untouched so the push path cannot regress. Missing or unreadable returns ""
    which makes IS_PRIMARY false, and false is the safe direction: a machine
    that cannot prove it is primary installs only the per-machine jobs.
    """
    try:
        with open(os.path.join(REPO, "ops", "githooks", "pre-push"),
                  encoding="utf-8") as fh:
            m = re.search(r'^OWNER_EMAIL="([^"]+)"', fh.read(), re.M)
        return m.group(1) if m else ""
    except OSError:
        return ""


def _is_primary():
    owner = _owner_email()
    if not owner:
        return False
    me = subprocess.run(["git", "-C", REPO, "config", "user.email"],
                        capture_output=True, text=True, env=_git_env()).stdout.strip()
    return me == owner


IS_PRIMARY = _is_primary()


def missing_targets(body):
    """Absolute paths a plist needs that do not exist on THIS machine.

    A launchd job whose program was never built loads fine and then fails on
    every fire, throttling and filling the log — the failure mode the dictation
    and doc-engine jobs would have hit on a fresh clone, where the Swift binary
    and the doc-convo tree are not built. Skipping is the safe direction: the
    job installs later, on the next run, once the thing it runs exists.
    """
    try:
        d = plistlib.loads(body.encode())
    except Exception:
        return []          # unparseable is the drift check's problem, not ours
    args = d.get("ProgramArguments") or ([d["Program"]] if d.get("Program") else [])
    return [a for a in args
            if isinstance(a, str) and a.startswith("/") and not os.path.exists(a)]


def portable(text):
    for tok, real in TOKENS:
        text = text.replace(real, tok)
    return text


def concrete(text):
    for tok, real in TOKENS:
        text = text.replace(tok, real)
    return text


def read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def launchd_texts_match(live_text, repo_text):
    """Compare launchd plist bodies with token portability normalized on both sides."""
    return portable(live_text or "") == portable(repo_text or "")


def tracked_text_match(label, live, repo_text):
    """Config equality that keeps launchd normalization symmetrical."""
    if label.startswith("launchd "):
        return launchd_texts_match(live, repo_text)
    return live == repo_text


def hook_block_script_paths(block):
    """Every absolute .py path a hooks block's commands invoke, real and sorted.

    The extraction half shared by hook_scripts_untracked (which asks git about
    the LIVE block) and cmd_install's missing-script refusal (which asks the
    filesystem about the PLANNED block before writing it)."""
    paths = set()
    for groups in (block or {}).values():
        if not isinstance(groups, list):
            continue
        for grp in groups:
            for hook in (grp or {}).get("hooks", []) or []:
                for tok in re.findall(r"(/[^\s'\"]+\.py)", hook.get("command", "") or ""):
                    paths.add(os.path.realpath(concrete(tok)))
    return sorted(paths)


def hook_scripts_untracked():
    """Hook scripts that settings.json points at but git does not track.

    THE GAP THIS CLOSES, found 2026-08-09. This tool verifies that the hooks
    BLOCK inside settings.json matches the repo. It never checked whether the
    SCRIPTS that block points at are committed. Those are different failures and
    only one of them was covered: on this machine the live settings referenced
    hooks/conduct-stop-gate.py, hooks/conduct_patterns.py and
    hooks/escalation-gate.py, all created the same afternoon and none of them in
    git. The check reported "OK — repo matches machine" the whole time, and it
    was telling the truth about the only thing it was looking at.

    WHY IT MATTERS RATHER THAN BEING TIDINESS. A hook that exists only in one
    working tree is one `git clean`, one disk failure or one fresh clone from
    being gone, and the settings block that survives will then point at files
    that are not there. It also cannot reach Dell: he pulls the repo, so an
    untracked gate binds Joe's sessions and silently binds nothing of his, which
    is the twin-parity failure rule 61c64d91 exists to prevent. The 2026-08-08
    wipe proved the settings block is worth versioning; the scripts it invokes
    are the other half of the same control.

    Returns a list of (path, why) — repo-relative where possible.
    """
    block = live_hooks_block()
    if not block:
        return []
    out = []
    for p in hook_block_script_paths(block):
        if not os.path.exists(p):
            out.append((p, "settings.json invokes it and IT DOES NOT EXIST"))
            continue
        # Ask git about the file IN ITS OWN checkout, not in this script's.
        # The live settings point at the primary checkout (~/carr-system), while
        # this script may be running from a worktree — comparing against the
        # script's own REPO made every hook look "outside the repo" and the
        # check silently found nothing, which is worse than not having it.
        d = os.path.dirname(p)
        inside = subprocess.run(["git", "-C", d, "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True, env=_git_env())
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            continue                       # not in any git checkout: not ours to version
        # `git ls-files --error-unmatch` is the exact question: is this path in
        # the index? A file that is merely present is not a file that survives.
        rc = subprocess.run(["git", "-C", d, "ls-files", "--error-unmatch", p],
                            capture_output=True, env=_git_env()).returncode
        if rc != 0:
            top = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"],
                                 capture_output=True, text=True, env=_git_env()).stdout.strip()
            rel = os.path.relpath(p, top) if top else p
            out.append((rel, "settings.json invokes it but git DOES NOT TRACK it"))
    return out


def carr_plists():
    try:
        return sorted(f for f in os.listdir(LAUNCHD_SRC)
                      if f.startswith("com.carr.") and f.endswith(".plist"))
    except FileNotFoundError:
        return []


def launchd_repo_path(name):
    """Canonical tracked source for one installed CARR LaunchAgent."""
    return LAUNCHD_ALT_REPO.get(name, os.path.join(LAUNCHD_REPO, name))


def live_hooks_block():
    raw = read(SETTINGS)
    if raw is None:
        return None
    return json.loads(raw).get("hooks")


def live_codex_hooks():
    """Only the CARR-owned Codex tuples are config-as-code state.

    Codex's global hooks document is shared with unrelated projects.  Comparing
    or installing it wholesale would turn another project's hook into CARR
    drift, then overwrite it on the next install.
    """
    raw = read(CODEX_HOOKS_SRC)
    if raw is None:
        return None
    try:
        live = json.loads(raw)
        desired = json.loads(concrete(read(CODEX_HOOKS_REPO)))
    except Exception:
        return None
    return portable(json.dumps(carr_owned_hooks_document(
        live, (desired.get("hooks") or {}).keys()), indent=2) + "\n")


def is_carr_hook_command(command):
    if not isinstance(command, str):
        return False
    candidate = command.replace("\\", "/").lower()
    return ("/carr-system/hooks/" in candidate or
            "/my drive/carr ai/hooks/" in candidate or
            "/carr-system/ops/codex-continuity-hook.py" in candidate or
            "/my drive/carr ai/ops/codex-continuity-hook.py" in candidate or
            "{{repo}}/hooks/" in candidate or
            "{{repo}}/ops/codex-continuity-hook.py" in candidate)


def carr_owned_hooks_document(document, include_events=()):
    """Extract CARR commands, retaining their event/matcher grouping exactly."""
    hooks = document.get("hooks") if isinstance(document, dict) else {}
    # A first-run Codex document may be absent or may not have a hooks key yet.
    # Both are empty hook collections, not iterables named None.
    if not isinstance(hooks, dict):
        hooks = {}
    out = {}
    for event in list(include_events) + [e for e in hooks if e not in include_events]:
        groups = hooks.get(event, []) if isinstance(hooks, dict) else []
        kept = []
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict):
                continue
            commands = [h for h in group.get("hooks", [])
                        if isinstance(h, dict) and is_carr_hook_command(h.get("command"))]
            if commands:
                clone = dict(group)
                clone["hooks"] = commands
                kept.append(clone)
        if kept or event in include_events:
            out[event] = kept
    return {"hooks": out}


def merge_codex_carr_hooks(live, desired):
    """Replace only CARR-owned tuples, preserving every unrelated Codex hook."""
    result = json.loads(json.dumps(live if isinstance(live, dict) else {}))
    live_hooks = result.get("hooks")
    if not isinstance(live_hooks, dict):
        live_hooks = {}
    desired_hooks = desired.get("hooks") if isinstance(desired, dict) else {}
    for event in set(live_hooks) | set(desired_hooks or {}):
        retained = []
        for group in live_hooks.get(event, []) if isinstance(live_hooks.get(event, []), list) else []:
            if not isinstance(group, dict):
                retained.append(group)
                continue
            non_carr = [h for h in group.get("hooks", [])
                        if not (isinstance(h, dict) and is_carr_hook_command(h.get("command")))]
            if non_carr:
                clone = dict(group)
                clone["hooks"] = non_carr
                retained.append(clone)
        desired_groups = (desired_hooks or {}).get(event, [])
        retained.extend(json.loads(json.dumps(desired_groups)) if isinstance(desired_groups, list) else [])
        live_hooks[event] = retained
    result["hooks"] = live_hooks
    return result


def is_codex_continuity_hook_command(command):
    """Recognize only the continuity wrapper owned by the narrow installer."""
    if not isinstance(command, str):
        return False
    candidate = command.replace("\\", "/").lower()
    return ("/carr-system/ops/codex-continuity-hook.py" in candidate or
            "/my drive/carr ai/ops/codex-continuity-hook.py" in candidate or
            "{{repo}}/ops/codex-continuity-hook.py" in candidate)


def merge_codex_continuity_hooks(live, desired):
    """Merge continuity groups while preserving all other Codex configuration."""
    result = json.loads(json.dumps(live if isinstance(live, dict) else {}))
    live_hooks = result.get("hooks")
    if not isinstance(live_hooks, dict):
        live_hooks = {}
    desired_hooks = desired.get("hooks") if isinstance(desired, dict) else {}
    if not isinstance(desired_hooks, dict):
        desired_hooks = {}
    for event in CODEX_CONTINUITY_EVENTS:
        retained = []
        groups = live_hooks.get(event, [])
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict):
                retained.append(group)
                continue
            non_continuity = [hook for hook in group.get("hooks", [])
                              if not (isinstance(hook, dict) and
                                      is_codex_continuity_hook_command(hook.get("command")))]
            if non_continuity:
                clone = dict(group)
                clone["hooks"] = non_continuity
                retained.append(clone)
        desired_groups = desired_hooks.get(event, [])
        if isinstance(desired_groups, list):
            retained.extend(json.loads(json.dumps(desired_groups)))
        live_hooks[event] = retained
    result["hooks"] = live_hooks
    return result


def codex_permissions_source():
    """Return the canonical default line and managed TOML body, or None."""
    raw = read(CODEX_PERMISSIONS_REPO)
    if raw is None:
        return None
    lines = raw.splitlines()
    if not lines or not re.fullmatch(r'default_permissions\s*=\s*"[^"]+"', lines[0]):
        raise ValueError("Codex permissions source must begin with default_permissions")
    return lines[0], "\n".join(lines[1:]).strip() + "\n"


def canonical_codex_permissions(raw):
    """Render a live CARR-owned Codex permission slice in portable source form."""
    default = re.search(r'^default_permissions\s*=\s*"[^"]+"\s*$', raw, re.M)
    marker = re.search(re.escape(CODEX_PERMISSIONS_BEGIN) + r'\n(.*?)'
                       + re.escape(CODEX_PERMISSIONS_END), raw, re.S)
    if not default or not marker:
        return None
    rendered = default.group(0).strip() + "\n\n" + marker.group(1).strip() + "\n"
    return portable(rendered)


def live_codex_permissions():
    """Render the CARR-owned portion of config.toml in canonical source form."""
    raw = read(CODEX_CONFIG)
    return None if raw is None else canonical_codex_permissions(raw)


def install_codex_permissions(raw, default_line, body):
    """Replace only the managed CARR slice of Codex's user-owned config.toml."""
    default_re = re.compile(r'^default_permissions\s*=\s*"[^"]+"\s*\n?', re.M)
    if default_re.search(raw):
        planned = default_re.sub(default_line + "\n", raw, count=1)
    else:
        first_table = re.search(r'^\[', raw, re.M)
        at = first_table.start() if first_table else len(raw)
        planned = raw[:at] + default_line + "\n" + raw[at:]

    managed = CODEX_PERMISSIONS_BEGIN + "\n" + body.rstrip() + "\n" + CODEX_PERMISSIONS_END
    marker_re = re.compile(re.escape(CODEX_PERMISSIONS_BEGIN) + r'\n.*?'
                           + re.escape(CODEX_PERMISSIONS_END), re.S)
    if marker_re.search(planned):
        return marker_re.sub(managed, planned, count=1)
    return planned.rstrip() + "\n\n" + managed + "\n"


def codex_configuration_state():
    """Return configured, absent, or partial for this machine's Codex client.

    Codex is optional on secondary machines.  The absence of both user-owned
    files means there is no Codex surface to manage.  A hooks file without the
    config file is different: silently skipping that partial surface could
    leave a real client ungoverned, so callers must fail visibly.
    """
    has_hooks = os.path.exists(CODEX_HOOKS_SRC)
    has_config = os.path.exists(CODEX_CONFIG)
    if has_config:
        return "configured"
    if has_hooks:
        return "partial"
    return "absent"


def codex_app_server_request(method, params):
    """Call one bounded experimental Codex app-server method over JSONL stdio."""
    command = [os.environ.get("CARR_CODEX_CLI", "codex"), "app-server", "--stdio"]
    messages = [
        {"method": "initialize", "id": 1, "params": {
            "clientInfo": {"name": "carr-continuity-installer", "version": "1.0.0"},
            "capabilities": {"experimentalApi": True},
        }},
        {"method": "initialized", "params": {}},
        {"method": method, "id": 2, "params": params},
    ]
    process = None
    try:
        process = subprocess.Popen(
            command, cwd=REPO, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise RuntimeError("Codex app-server stdio pipes were not created")
        payload = b"".join((json.dumps(message) + "\n").encode() for message in messages)
        process.stdin.write(payload)
        process.stdin.flush()
        deadline = time.monotonic() + CODEX_APP_SERVER_TIMEOUT_SECONDS
        buffer = b""
        stderr_buffer = b""
        while time.monotonic() < deadline:
            ready, _, _ = select.select(
                [process.stdout, process.stderr], [], [],
                min(0.25, max(0, deadline - time.monotonic())))
            if not ready:
                if process.poll() is not None:
                    break
                continue
            for stream in ready:
                chunk = os.read(stream.fileno(), 65536)
                if stream is process.stderr:
                    stderr_buffer += chunk
                    if len(stderr_buffer) > CODEX_APP_SERVER_OUTPUT_LIMIT:
                        raise RuntimeError("Codex app-server stderr exceeded the 4 MiB limit")
                    continue
                if not chunk:
                    continue
                buffer += chunk
                if len(buffer) > CODEX_APP_SERVER_OUTPUT_LIMIT:
                    raise RuntimeError("Codex app-server response exceeded the 4 MiB limit")
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    response = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(f"Codex app-server returned invalid JSON ({exc})") from exc
                if response.get("id") != 2:
                    continue
                if "error" in response:
                    error = response.get("error") or {}
                    message = str(error.get("message") or "request refused")[:500]
                    raise RuntimeError(f"Codex app-server {method} failed: {message}")
                result = response.get("result")
                if not isinstance(result, dict):
                    raise RuntimeError(f"Codex app-server {method} returned no object result")
                return result
        raise RuntimeError(f"Codex app-server {method} did not answer within "
                           f"{CODEX_APP_SERVER_TIMEOUT_SECONDS}s")
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Codex app-server {method} unavailable ({exc})") from exc
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


def canonical_codex_continuity_hooks():
    """Load and validate the four exact rendered hook contracts from the repo."""
    source = read(CODEX_HOOKS_REPO)
    if source is None:
        raise RuntimeError(f"no tracked Codex hooks at {CODEX_HOOKS_REPO}")
    try:
        document = json.loads(concrete(source))
    except Exception as exc:
        raise RuntimeError(f"{CODEX_HOOKS_REPO} is not valid JSON ({exc})") from exc
    hooks = document.get("hooks") if isinstance(document, dict) else None
    if not isinstance(hooks, dict):
        raise RuntimeError(f"{CODEX_HOOKS_REPO} must contain a hooks object")
    desired = {"hooks": {event: hooks.get(event, [])
                          for event in CODEX_CONTINUITY_EVENTS}}
    contracts = []
    for event in CODEX_CONTINUITY_EVENTS:
        groups = desired["hooks"][event]
        if (not isinstance(groups, list) or len(groups) != 1 or
                not isinstance(groups[0], dict)):
            raise RuntimeError(f"{event} must contain exactly one continuity group")
        group = groups[0]
        handlers = group.get("hooks")
        if (not isinstance(handlers, list) or len(handlers) != 1 or
                not isinstance(handlers[0], dict)):
            raise RuntimeError(f"{event} must contain exactly one continuity handler")
        handler = handlers[0]
        if (handler.get("type") != "command" or
                not isinstance(handler.get("command"), str) or
                not isinstance(handler.get("timeout"), int) or
                isinstance(handler.get("timeout"), bool)):
            raise RuntimeError(f"{event} continuity handler shape is invalid")
        contracts.append({
            "eventName": CODEX_CONTINUITY_APP_EVENTS[event],
            "command": handler["command"],
            "matcher": group.get("matcher"),
            "handlerType": handler["type"],
            "timeoutSec": handler["timeout"],
        })
    return desired, contracts


def codex_continuity_hook_entries(contracts, require_trusted=False):
    """Return the exact four user hook instances observed by Codex itself."""
    response = codex_app_server_request("hooks/list", {"cwds": [REPO]})
    data = response.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise RuntimeError("hooks/list did not return exactly one requested working directory")
    listing = data[0]
    if listing.get("errors"):
        raise RuntimeError("hooks/list reported hook configuration errors")
    hooks = listing.get("hooks")
    if not isinstance(hooks, list):
        raise RuntimeError("hooks/list returned no hook array")
    source_path = os.path.realpath(CODEX_HOOKS_SRC)
    candidates = []
    for contract in contracts:
        matches = [hook for hook in hooks if isinstance(hook, dict) and
                   os.path.realpath(str(hook.get("sourcePath") or "")) == source_path and
                   all(hook.get(field) == value for field, value in contract.items())]
        if len(matches) != 1:
            raise RuntimeError("hooks/list did not uniquely match the canonical "
                               f"{contract['eventName']} continuity hook")
        candidates.append(matches[0])
    keys = set()
    for hook in candidates:
        key = hook.get("key")
        current_hash = hook.get("currentHash")
        if (hook.get("source") != "user" or hook.get("handlerType") != "command" or
                hook.get("enabled") is not True or not isinstance(key, str) or not key or
                not isinstance(current_hash, str) or
                not re.fullmatch(r"sha256:[0-9a-f]{64}", current_hash)):
            raise RuntimeError("hooks/list returned malformed continuity hook metadata")
        if key in keys:
            raise RuntimeError("hooks/list returned a duplicate continuity hook key")
        keys.add(key)
        if require_trusted and hook.get("trustStatus") != "trusted":
            raise RuntimeError(f"{hook.get('eventName')} continuity hook is "
                               f"{hook.get('trustStatus') or 'not trusted'}")
    return candidates


def _codex_user_config_layer():
    response = codex_app_server_request(
        "config/read", {"cwd": REPO, "includeLayers": True})
    layers = response.get("layers")
    if not isinstance(layers, list):
        raise RuntimeError("config/read returned no configuration layers")
    expected_path = os.path.realpath(CODEX_CONFIG)
    matches = []
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        name = layer.get("name")
        if (isinstance(name, dict) and name.get("type") == "user" and
                os.path.realpath(str(name.get("file") or "")) == expected_path):
            matches.append(layer)
    if len(matches) != 1:
        raise RuntimeError("config/read did not uniquely identify the user config layer")
    layer = matches[0]
    if (not isinstance(layer.get("config"), dict) or
            not re.fullmatch(r"sha256:[0-9a-f]{64}", str(layer.get("version") or ""))):
        raise RuntimeError("config/read returned malformed user config metadata")
    return layer


def _without_continuity_trust(config, keys):
    """Mask only selected trust tables so every unrelated value can be compared."""
    masked = copy.deepcopy(config)
    hooks = masked.get("hooks")
    if not isinstance(hooks, dict):
        return masked
    state = hooks.get("state")
    if isinstance(state, dict):
        for key in keys:
            state.pop(key, None)
        if not state:
            hooks.pop("state", None)
    if not hooks:
        masked.pop("hooks", None)
    return masked


def _hook_trust_state(config):
    hooks = config.get("hooks") if isinstance(config, dict) else None
    state = hooks.get("state") if isinstance(hooks, dict) else None
    return state if isinstance(state, dict) else {}


def _write_codex_config_edits(edits, expected_version):
    result = codex_app_server_request("config/batchWrite", {
        "edits": edits,
        "expectedVersion": expected_version,
        "filePath": CODEX_CONFIG,
        "reloadUserConfig": True,
    })
    if (result.get("status") != "ok" or
            os.path.realpath(str(result.get("filePath") or "")) !=
            os.path.realpath(CODEX_CONFIG)):
        raise RuntimeError("config/batchWrite did not confirm an effective user-config write")
    return _codex_user_config_layer()


def _restore_codex_continuity_trust(before_config, keys):
    """Restore selected trust tables without reverting unrelated concurrent config."""
    current = _codex_user_config_layer()
    current_config = current["config"]
    before_state = _hook_trust_state(before_config)
    current_state = _hook_trust_state(current_config)
    edits = []
    for key in keys:
        prior = before_state.get(key)
        if key in before_state and current_state.get(key) != prior:
            edits.append({"keyPath": f"hooks.state.{json.dumps(key)}",
                          "value": copy.deepcopy(prior), "mergeStrategy": "replace"})
        elif key not in before_state and key in current_state:
            edits.append({"keyPath": f"hooks.state.{json.dumps(key)}",
                          "value": None, "mergeStrategy": "upsert"})
    if not edits:
        return
    restored = _write_codex_config_edits(edits, current["version"])
    if (_without_continuity_trust(current_config, keys) !=
            _without_continuity_trust(restored["config"], keys)):
        raise RuntimeError("trust rollback changed unrelated Codex configuration")
    restored_state = _hook_trust_state(restored["config"])
    if any((key in before_state) != (key in restored_state) or
           (key in before_state and restored_state.get(key) != before_state.get(key))
           for key in keys):
        raise RuntimeError("trust rollback did not restore prior continuity entries")


def persist_codex_continuity_trust(entries, contracts, remove=False):
    """Atomically upsert or delete only four app-server-derived trust tables."""
    before = _codex_user_config_layer()
    config = before["config"]
    state = _hook_trust_state(config)
    expected = {entry["key"]: entry["currentHash"] for entry in entries}
    if remove:
        if all(key not in state for key in expected):
            print("  Codex continuity hook trust already absent")
            return 0
    elif (all(state.get(key) == {"trusted_hash": current_hash}
              for key, current_hash in expected.items()) and
          all(entry.get("trustStatus") == "trusted" for entry in entries)):
        print("  Codex continuity hooks already trusted")
        return 0

    edits = []
    for key, current_hash in expected.items():
        quoted = json.dumps(key)
        edits.append({
            "keyPath": (f"hooks.state.{quoted}" if remove else
                        f"hooks.state.{quoted}.trusted_hash"),
            "value": None if remove else current_hash,
            "mergeStrategy": "upsert",
        })
    try:
        after = _write_codex_config_edits(edits, before["version"])
        after_state = _hook_trust_state(after["config"])
        if _without_continuity_trust(config, expected) != _without_continuity_trust(
                after["config"], expected):
            raise RuntimeError("config/batchWrite changed unrelated Codex configuration")
        if remove:
            if any(key in after_state for key in expected):
                raise RuntimeError("config/batchWrite left continuity trust entries behind")
            print("  REMOVED   four Codex continuity hook trust entries")
            return 0
        if any(after_state.get(key) != {"trusted_hash": current_hash}
               for key, current_hash in expected.items()):
            raise RuntimeError("config/batchWrite did not persist exact continuity hook hashes")
        verified = codex_continuity_hook_entries(contracts, require_trusted=True)
        observed = {entry["key"]: entry["currentHash"] for entry in verified}
        if observed != expected:
            raise RuntimeError("hooks/list changed continuity identity during trust installation")
        print("  TRUSTED   four Codex continuity hooks using authoritative current hashes")
        return 0
    except RuntimeError as exc:
        try:
            _restore_codex_continuity_trust(config, expected)
        except RuntimeError as rollback_exc:
            raise RuntimeError(f"{exc}; trust rollback failed ({rollback_exc})") from exc
        raise


def cmd_verify_codex_continuity():
    """Read-only proof that Codex will automatically execute all four hooks."""
    try:
        _, contracts = canonical_codex_continuity_hooks()
        entries = codex_continuity_hook_entries(contracts, require_trusted=True)
    except RuntimeError as exc:
        print(f"ERROR: Codex continuity trust verification failed ({exc}).")
        return 1
    for entry in entries:
        print(f"  TRUSTED   {entry['eventName']}: {entry['currentHash']}")
    print("  Codex hooks/list confirms all four continuity hooks are trusted")
    return 0


def _write_codex_hooks_text(raw):
    """Atomically write or restore hooks.json after validating its object shape."""
    if raw is None:
        if os.path.exists(CODEX_HOOKS_SRC):
            os.unlink(CODEX_HOOKS_SRC)
        return
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("Codex hooks restore content is not a JSON object")
    parent = os.path.dirname(CODEX_HOOKS_SRC)
    os.makedirs(parent, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=parent,
                                         prefix=".codex-continuity-", delete=False) as fh:
            temp_path = fh.name
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp_path, CODEX_HOOKS_SRC)
        check = json.loads(read(CODEX_HOOKS_SRC))
        if not isinstance(check, dict):
            raise RuntimeError("written Codex hooks are not a JSON object")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def cmd_install_codex_continuity(apply, remove=False):
    """Install only the four CARR continuity hook groups owned by Codex.

    This intentionally has no relationship to the broad machine reconciler:
    it never reads or writes Claude settings, Codex permissions, LaunchAgents,
    scheduled tasks, git configuration, or any other global client state.
    """
    try:
        canonical_desired, contracts = canonical_codex_continuity_hooks()
    except RuntimeError as exc:
        print(f"ERROR: Codex continuity hook source is invalid ({exc}).")
        return 1
    desired = ({"hooks": {event: [] for event in CODEX_CONTINUITY_EVENTS}}
               if remove else canonical_desired)

    raw_live = read(CODEX_HOOKS_SRC)
    if raw_live is None:
        if remove:
            print("  No Codex continuity hook file exists; nothing to remove")
            return 0
        live = {}
        print(f"  Codex continuity hooks: {'WILL CREATE' if apply else 'would create'} {CODEX_HOOKS_SRC}")
    else:
        try:
            live = json.loads(raw_live)
        except Exception as exc:
            print(f"ERROR: {CODEX_HOOKS_SRC} is not valid JSON ({exc}) — refusing to touch it.")
            return 1
        if not isinstance(live, dict):
            print(f"ERROR: {CODEX_HOOKS_SRC} must contain a JSON object — refusing to touch it.")
            return 1

    merged = merge_codex_continuity_hooks(live, desired)
    rendered = json.dumps(merged, indent=2) + "\n"
    unchanged = raw_live is not None and raw_live == rendered
    if unchanged and (remove or not apply):
        print("  Codex continuity hooks already match the repo (unrelated configuration preserved)")
        return 0
    if not apply:
        print(f"  Codex continuity hooks: would write {CODEX_HOOKS_SRC}")
        action = "remove-codex-continuity" if remove else "install-codex-continuity"
        print(f"\nDRY RUN — nothing written. Re-run with `{action} --apply`.")
        return 0

    # Removal must capture Codex's exact keys and prior trust state before
    # hooks.json stops exposing them. Its trust deletion is phase one; if the
    # hook rewrite fails, only those four trust tables are restored. Install is
    # the inverse: hooks.json is phase one and is restored if trust phase two
    # refuses.
    entries = None
    removal_config = None
    if remove:
        try:
            entries = codex_continuity_hook_entries(contracts)
            removal_config = _codex_user_config_layer()["config"]
            persist_codex_continuity_trust(entries, contracts, remove=True)
        except RuntimeError as exc:
            print(f"ERROR: Codex continuity trust removal failed ({exc}).")
            return 1

    if not unchanged:
        parent = os.path.dirname(CODEX_HOOKS_SRC)
        os.makedirs(parent, exist_ok=True)
        backup = CODEX_HOOKS_SRC + ".bak-codex-continuity"
        had_live = raw_live is not None
        if had_live:
            shutil.copy2(CODEX_HOOKS_SRC, backup)
        try:
            _write_codex_hooks_text(rendered)
        except Exception as exc:
            rollback_errors = []
            try:
                _write_codex_hooks_text(raw_live)
            except Exception as rollback_exc:
                rollback_errors.append(f"hooks rollback failed ({rollback_exc})")
            if remove and removal_config is not None and entries is not None:
                try:
                    _restore_codex_continuity_trust(
                        removal_config, {entry["key"] for entry in entries})
                except RuntimeError as rollback_exc:
                    rollback_errors.append(f"trust rollback failed ({rollback_exc})")
            suffix = ("; " + "; ".join(rollback_errors)) if rollback_errors else ""
            print(f"ERROR: Codex continuity hook write failed ({exc}){suffix}.")
            return 1
        print(f"  WROTE OK  {CODEX_HOOKS_SRC} "
              f"(backup: {backup if had_live else 'none; new file'})")
    else:
        print("  Codex continuity hooks already match the repo "
              "(unrelated configuration preserved)")

    if remove:
        return 0
    try:
        entries = codex_continuity_hook_entries(contracts)
        persist_codex_continuity_trust(entries, contracts)
    except RuntimeError as exc:
        try:
            _write_codex_hooks_text(raw_live)
        except Exception as rollback_exc:
            print("ERROR: Codex continuity trust update failed "
                  f"({exc}); hooks rollback failed ({rollback_exc}).")
            return 1
        action = "removal" if remove else "installation"
        print(f"ERROR: Codex continuity trust {action} failed ({exc}); "
              "prior hooks restored.")
        return 1
    return 0


# A DEFINITION-ONLY TASK IS NOT A MISSING JOB. Four calendar-prebrief contracts
# live in the repository and say, in their own bodies, "This definition is
# disabled. Do not create, enable, or invoke any scheduler." Their activation
# needs Joe's allowlist approval, EventKit permission and device evidence that
# do not exist yet. The repository holds them as CONTRACTS, deliberately ahead
# of the machine — config-as-code-selftest even asserts a definition-only tick
# is not installed before cutover.
#
# Reporting them as MISSING FROM MACHINE told every session to install four
# jobs whose own text forbids installing them, and blocked the pre-push gate
# until somebody did. That is the ephemeral-task problem from the other
# direction: the repository and the machine legitimately differ, and the check
# could not express it.
def is_definition_only_task(text):
    """True when a repo-side task declares itself disabled rather than deployed."""
    return bool(text) and "This definition is disabled" in text


def pairs():
    """(label, live_text, repo_path) for every tracked item. live_text is
    already portable; repo contents are compared verbatim against it."""
    out = []

    hooks = live_hooks_block()
    out.append(("hooks block (settings.json)",
                None if hooks is None else portable(json.dumps(hooks, indent=2) + "\n"),
                HOOKS_REPO))
    if codex_configuration_state() == "configured":
        out.append(("Codex CARR hooks (hooks.json)", live_codex_hooks(), CODEX_HOOKS_REPO))
        out.append(("Codex CARR permissions (config.toml)", live_codex_permissions(),
                    CODEX_PERMISSIONS_REPO))

    seen = set()
    for name in sorted(os.listdir(TASKS_SRC)) if os.path.isdir(TASKS_SRC) else []:
        skill = os.path.join(TASKS_SRC, name, "SKILL.md")
        if os.path.isfile(skill) and scheduled_task_allowed(name):
            if is_ephemeral_scheduled_task(read(skill)):
                continue
            seen.add(f"{name}.SKILL.md")
            out.append((f"scheduled-task {name}", portable(read(skill)),
                        os.path.join(TASKS_REPO, f"{name}.SKILL.md")))
    # A task deleted from the machine but still in the repo is drift too — the
    # repo would otherwise quietly claim a job that no longer runs anywhere.
    for name, source in sorted(tracked_scheduled_task_paths().items()):
        filename = f"{name}.SKILL.md"
        if scheduled_task_allowed(name) and filename not in seen:
            if is_definition_only_task(read(source)):
                continue
            out.append((f"scheduled-task {name} (IN REPO, NOT ON MACHINE)",
                        None, source))

    for f in carr_plists():
        out.append((f"launchd {f}", portable(read(os.path.join(LAUNCHD_SRC, f))),
                    launchd_repo_path(f)))
    return out


def cmd_check():
    # SEVERITY IS NOT COSMETIC HERE, and the 2026-08-08 incident is why.
    # A tracked item MISSING from the machine means a protection that was
    # supposed to be running is not running. A tracked item merely DIFFERENT
    # usually means the repo baseline lagged a deliberate live change. Those are
    # not the same event and must not print the same line.
    #
    # WHAT ACTUALLY FAILED. On 2026-08-06 the guard's matcher was widened to
    # "Bash|WebFetch" on the machine and nobody pulled, so this check went red
    # for a benign reason and STAYED red. On 2026-08-08 15:46 a plugin install
    # rewrote ~/.claude/settings.json and deleted the entire hooks block — all
    # five gates off. The headline both days: "DRIFT — 2 of 28 items". Identical
    # string, and the health row prints only that first line, so the difference
    # between a matcher tweak and total gate annihilation was invisible. It went
    # unnoticed for a day and was found by accident.
    #
    # The lesson is rule 590b11e1's, arriving the other way round: a check that
    # is chronically red detects nothing, because a reader who has learned to
    # skip a red row will skip the one that matters. Keeping this row green when
    # nothing is wrong is therefore part of the control, not tidiness.
    if codex_configuration_state() == "partial":
        print(f"config-as-code: CODEX PARTIAL — {CODEX_HOOKS_SRC} exists but "
              f"{CODEX_CONFIG} does not; refusing to treat this client as absent")
        return 1

    missing, untracked, different = [], [], []
    for label, live, repo_path in pairs():
        have = read(repo_path)
        if live is None:
            missing.append((label, "on disk: MISSING; in repo: present"))
        elif have is None:
            untracked.append((label, "on disk: present; in repo: NOT TRACKED"))
        elif not tracked_text_match(label, live, have):
            different.append((label, "TRACKED BUT DIFFERENT from the live copy"))
    # A hook script the settings block invokes but git does not track is a
    # separate failure from a settings mismatch, and it used to be invisible
    # because this tool only ever compared the block itself. It is reported as
    # UNVERSIONED rather than folded into `untracked`, because the remedy is a
    # commit rather than a `pull`.
    unversioned = hook_scripts_untracked()
    secondary_task_violations = secondary_scheduled_task_violations()
    disallowed = [
        (f"scheduled-task {name} (NOT ALLOWED ON SECONDARY)",
         "present on disk; this machine has no approved scope for it")
        for name in secondary_task_violations
    ]
    drift = missing + untracked + different + disallowed
    if not drift and not unversioned:
        prerequisite_report = prerequisite_failure_report(PREREQUISITE_CHECK(REPO))
        if prerequisite_report:
            print(prerequisite_report)
            return 1
        print(f"config-as-code: OK — {len(pairs())} items, repo matches machine")
        return 0
    if not drift and unversioned:
        print(f"config-as-code: UNVERSIONED HOOKS — {len(unversioned)} script(s) the live "
              f"settings invoke are not in git: " + ", ".join(p for p, _ in unversioned))
        for p, why in unversioned:
            print(f"  {p}\n      {why}")
        print("\n  A gate that exists in one working tree only is one `git clean` or one\n"
              "  fresh clone from gone, and it can never reach Dell. Commit them:\n"
              "      git -C ~/carr-system add " + " ".join(p for p, _ in unversioned))
        return 1
    # The headline carries the severity, because callers that summarise this
    # tool (tools/health-check.py) read the FIRST LINE ONLY.
    # The denominator includes a CARR-owned disallowed task even though it is
    # intentionally omitted from normal pairs() on a secondary.  Otherwise
    # "16 of 4" could claim to have checked only four items while reporting
    # sixteen violations, which is operationally misleading.
    checked_items = len(pairs()) + len(disallowed)
    headline = f"config-as-code: DRIFT — {len(drift)} of {checked_items} items"
    if missing:
        headline += f" — {len(missing)} MISSING FROM MACHINE: " + ", ".join(
            label for label, _ in missing)
    print(headline)
    for label, why in drift:
        print(f"  {label}\n      {why}")
    if missing:
        print("\n  MISSING means a tracked config is NOT ON THE MACHINE. If it is the\n"
              "  hooks block, every gate is currently off — restore it FIRST:\n"
              "      python3 ops/config-as-code.py install --apply\n"
              "  then prove it with a denial that should fail, e.g. a WebFetch to a\n"
              "  host outside KNOWN_HOSTS.")
    if untracked or different:
        print("\n  `ops/config-as-code.py pull` to capture the machine into the repo.")
    if disallowed:
        print("\n  A secondary machine must not run CARR's primary-only scheduled-task "
              "catalogue. `install --apply` can quarantine an exact tracked render; "
              "a modified tracked task needs review and is never overwritten.")
    # Reported even when settings drift is also present: the two have different
    # remedies (a pull versus a commit), so folding them together would hide one.
    if unversioned:
        print(f"\n  ALSO — {len(unversioned)} hook script(s) the live settings invoke are not in git:")
        for p, why in unversioned:
            print(f"  {p}\n      {why}")
        print("      git -C ~/carr-system add " + " ".join(p for p, _ in unversioned))
    return 1


def cmd_pull(apply):
    if codex_configuration_state() == "partial":
        print(f"ERROR: partial Codex configuration — {CODEX_HOOKS_SRC} exists but "
              f"{CODEX_CONFIG} does not; refusing to omit it from the captured baseline.")
        return 1
    disallowed = secondary_scheduled_task_violations()
    if disallowed:
        print("ERROR: unapproved scheduled task(s) on this secondary machine: "
              + ", ".join(disallowed) + "; refusing to capture them into the repo.")
        return 1
    wrote = 0
    for label, live, repo_path in pairs():
        if live is None:
            print(f"  SKIP  {label} (not on this machine; left in the repo)")
            continue
        if read(repo_path) == live:
            continue
        print(f"  {'WRITE' if apply else 'would write'}  {os.path.relpath(repo_path, REPO)}")
        if apply:
            os.makedirs(os.path.dirname(repo_path), exist_ok=True)
            with open(repo_path, "w", encoding="utf-8") as fh:
                fh.write(live)
        wrote += 1
    print(f"\n{wrote} item(s) {'written' if apply else 'would be written'}."
          + ("" if apply else " Re-run with --apply."))
    return 0


def install_launchd_plist(filename, dest, body, body_matches):
    """Render and load one plist without letting an active job unload itself.

    Returns ``loaded``, ``kept``, or ``failed``.  A changed active plist cannot
    be rendered honestly without also reloading it, and reloading it here kills
    the receipt wrapper.  That case therefore leaves the destination untouched
    and fails with the exact external-install remedy.
    """
    try:
        label = plistlib.loads(body.encode("utf-8")).get("Label", "")
    except (AttributeError, plistlib.InvalidFileException, ValueError):
        label = ""
    active_label = os.environ.get(ACTIVE_LAUNCHD_LABEL_ENV, "").strip()
    is_active_self = bool(label and active_label == label)

    if is_active_self:
        if body_matches:
            print(f"      kept loaded (active installer job {label}; body unchanged)")
            return "kept"
        print(f"      SELF-RELOAD REFUSED ({filename}: active installer job {label}; "
              "destination left unchanged so loaded and installed state cannot diverge)")
        print("      remedy: run `python3 ops/config-as-code.py install --apply` "
              "from an external process")
        return "failed"

    if not body_matches:
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(body)

    subprocess.run(["launchctl", "unload", "-w", dest],
                   capture_output=True, check=False)
    r = subprocess.run(["launchctl", "load", "-w", dest],
                       capture_output=True, text=True, check=False)
    if r.returncode == 0:
        print("      loaded")
        return "loaded"
    print(f"      LOAD FAILED ({(r.stderr or r.stdout).strip()[:80]}) "
          f"— migration will remain incomplete")
    return "failed"


def cmd_install(apply):
    """repo -> machine. The half that makes a second machine possible."""
    settings_existed = os.path.exists(SETTINGS)
    raw = read(SETTINGS) if settings_existed else "{}"
    if not settings_existed:
        print(f"  Claude settings: WILL BE CREATED at {SETTINGS}")
    try:
        cfg = json.loads(raw)
    except Exception as exc:
        print(f"ERROR: {SETTINGS} is not valid JSON ({exc}) — refusing to touch it.")
        return 1

    src = read(HOOKS_REPO)
    if src is None:
        print(f"ERROR: no tracked hooks block at {HOOKS_REPO}. Run `pull` first.")
        return 1

    planned = json.loads(concrete(src))

    # REFUSE A BLOCK WHOSE SCRIPTS ARE NOT THERE (added after 2026-08-24).
    # Settings apply on the very next prompt of every session, so a hooks block
    # invoking a script this machine does not have blocks EVERY session the
    # moment it lands. That is not hypothetical: on 2026-08-24 install ran from
    # a worktree that had the freshly merged hook wrapper while {{REPO}} still
    # expanded to a canonical checkout one commit behind, and every session on
    # this Mac was dead overnight. cmd_check's hook_scripts_untracked() sees
    # this class only AFTER the write; install is the moment it is preventable.
    absent = [p for p in hook_block_script_paths(planned) if not os.path.exists(p)]
    if absent:
        print("ERROR: the tracked hooks block invokes script(s) this machine does not have:")
        for p in absent:
            print(f"    {p}")
        print("  Installing anyway would block every session at its next prompt.")
        print("  Most likely the checkout those paths point into is behind the branch")
        print("  that shipped them — fast-forward it, then re-run install.")
        return 1

    if cfg.get("hooks") == planned:
        print("  hooks block already matches the repo")
    else:
        print("  hooks block: WILL BE REPLACED with the repo's version")
        print(f"    preserved top-level keys: {sorted(k for k in cfg if k != 'hooks')}")
        p = cfg.get("permissions", {})
        print(f"    preserved permission entries: "
              f"{sum(len(v) for v in p.values() if isinstance(v, list))}")
        cfg["hooks"] = planned

    codex_state = codex_configuration_state()
    merged_codex = permission_config = None
    if codex_state == "partial":
        print(f"ERROR: partial Codex configuration — {CODEX_HOOKS_SRC} exists but "
              f"{CODEX_CONFIG} does not; refusing to skip or overwrite it.")
        return 1
    if codex_state == "absent":
        print("  SKIP  Codex configuration (Codex is not configured on this machine)")
    else:
        codex_src = read(CODEX_HOOKS_REPO)
        if codex_src is None:
            print(f"ERROR: no tracked Codex hooks at {CODEX_HOOKS_REPO}. Run `pull` first.")
            return 1
        codex_body = concrete(codex_src)
        try:
            desired_codex = json.loads(codex_body)
        except Exception as exc:
            print(f"ERROR: {CODEX_HOOKS_REPO} is not valid JSON ({exc}) — refusing to deploy it.")
            return 1
        live_codex_raw = read(CODEX_HOOKS_SRC)
        try:
            live_codex = json.loads(live_codex_raw) if live_codex_raw is not None else {}
        except Exception as exc:
            print(f"ERROR: {CODEX_HOOKS_SRC} is not valid JSON ({exc}) — refusing to touch it.")
            return 1
        merged_codex = merge_codex_carr_hooks(live_codex, desired_codex)
        desired_events = (desired_codex.get("hooks") or {}).keys()
        if carr_owned_hooks_document(live_codex, desired_events) == desired_codex:
            print("  Codex CARR hooks already match the repo (unrelated hooks preserved)")
        else:
            print("  Codex CARR hooks: WILL BE RECONCILED with the repo; unrelated hooks preserved")

        try:
            permission_source = codex_permissions_source()
        except Exception as exc:
            print(f"ERROR: {CODEX_PERMISSIONS_REPO} is invalid ({exc}) — refusing to deploy it.")
            return 1
        if permission_source is None:
            print(f"ERROR: no tracked Codex permissions at {CODEX_PERMISSIONS_REPO}. Run `pull` first.")
            return 1
        code_config = read(CODEX_CONFIG)
        permission_default, permission_body = permission_source
        permission_config = install_codex_permissions(
            code_config, concrete(permission_default), concrete(permission_body)
        )
        if permission_config == code_config:
            print("  Codex CARR permissions already match the repo")
        else:
            print("  Codex CARR permissions: WILL MAKE THE CURRENT WORKSPACE READ-ONLY")

    task_plan = scheduled_task_install_plan()
    if task_plan["modified"]:
        print("ERROR: modified CARR scheduled task(s) active on this secondary: "
              + ", ".join(task_plan["modified"])
              + "; refusing to move or overwrite them.")
        return 1
    if IS_PRIMARY:
        for name in task_plan["install"]:
            source = tracked_scheduled_task_paths()[name]
            destination = os.path.join(TASKS_SRC, name, "SKILL.md")
            # Same rule the launchd loop below already applies to
            # DEFINITION_ONLY plists, which this loop had never honoured. A task
            # whose body says "do not create, enable, or invoke any scheduler"
            # must not be written INTO the scheduler directory by the installer
            # that claims to be converging the machine to the repository.
            if is_definition_only_task(read(source)):
                print(f"  SKIP  scheduled task {name} (definition only: "
                      f"awaits its own stated activation approval)")
                continue
            if read(destination) == concrete(read(source)):
                print(f"  scheduled task already matches: {name}")
            else:
                print(f"  {'WRITE' if apply else 'would write'}  scheduled task {name}")
    elif task_plan["exact"]:
        for name in task_plan["exact"]:
            destination = os.path.join(TASKS_QUARANTINE, name)
            if os.path.exists(destination):
                print(f"ERROR: recovery quarantine already has {destination}; refusing to overwrite it.")
                return 1
            print(f"  {'QUARANTINE' if apply else 'would quarantine'}  scheduled task {name} "
                  f"-> {destination}")
    else:
        print("  SKIP  scheduled tasks (secondary machines install none; no approved "
              "secondary task scope is configured)")

    launchd_activation_failures = []
    if apply:
        os.makedirs(LAUNCHD_SRC, exist_ok=True)
    for f in sorted(os.listdir(LAUNCHD_REPO)) if os.path.isdir(LAUNCHD_REPO) else []:
        if f in DEFINITION_ONLY:
            print(f"  SKIP  {f} (definition only: {DEFINITION_ONLY[f]})")
            continue
        if f in PRIMARY_ONLY and not IS_PRIMARY:
            print(f"  SKIP  {f} (writes shared state; runs on the primary machine only)")
            continue
        if f in SECONDARY_ONLY and IS_PRIMARY:
            print(f"  SKIP  {f} (the nightly chain already does this here)")
            continue
        dest = os.path.join(LAUNCHD_SRC, f)
        source = read(os.path.join(LAUNCHD_REPO, f))
        if source is None:
            print(f"  ERROR  cannot render {f} because its tracked source is missing")
            return 1
        body = concrete(source)
        body_matches = launchd_texts_match(read(dest), source)
        if body_matches and not apply:
            continue
        gone = missing_targets(body)
        if gone:
            print(f"  SKIP  {f} (not built on this machine: {gone[0]})")
            continue
        if body_matches:
            print(f"  VERIFY LOADED  {dest}")
        else:
            print(f"  {'WRITE' if apply else 'would write'}  {dest}")
        if apply:
            # Load it, do not print a command for a human to paste (rule
            # e313a3ca). Writing the plist and stopping leaves the job on disk
            # and dead: on a fresh machine that means the nightly never runs,
            # so the record-derived fetch allowlist is generated once by the
            # migration and then never refreshed as clients are added. unload
            # is expected to fail when the job was never loaded; that is not
            # an error, which is why only the load result is reported.
            outcome = install_launchd_plist(f, dest, body, body_matches)
            if outcome == "failed":
                launchd_activation_failures.append(f)

    # Git hooks. Added 2026-08-03, when Dell was granted WRITE and it turned out
    # branch protection is unavailable on a private free-plan repo — so the pull
    # request review team-loops T39 relied on has no server-side replacement.
    # ops/githooks/pre-push refuses a direct push to main from any identity but
    # the owner's. It installs HERE rather than being a step in the runbook,
    # because a guard that depends on someone remembering a config command is
    # not a guard. Machine config ships with the code; that is what this file is.
    hooks_dir = os.path.join(REPO, "ops", "githooks")
    if os.path.isdir(hooks_dir):
        current = subprocess.run(
            ["git", "-C", REPO, "config", "--get", "core.hooksPath"],
            capture_output=True, text=True).stdout.strip()
        if current == "ops/githooks":
            print("  git hooksPath already points at ops/githooks")
        else:
            print(f"  git hooksPath: {current or '(unset)'} -> ops/githooks"
                  + ("" if apply else "   [would set]"))
        if apply:
            subprocess.run(["git", "-C", REPO, "config", "core.hooksPath", "ops/githooks"],
                           check=False, env=_git_env())
            for h in sorted(os.listdir(hooks_dir)):
                p = os.path.join(hooks_dir, h)
                if os.path.isfile(p):
                    os.chmod(p, os.stat(p).st_mode | 0o111)
            print("  git hooks installed (pre-push guards main)")

    if not apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0

    # Scheduled task definitions are reconciled as definitions only.  Scheduler
    # registration stays outside this tool; an on-disk SKILL.md must never be
    # mistaken for proof that a task is enabled.
    if IS_PRIMARY:
        for name, source in sorted(tracked_scheduled_task_paths().items()):
            # THE SKIP PRINTED ABOVE IS AN ANNOUNCEMENT; THIS IS THE WRITE IT
            # DESCRIBES. Guarding only the announcing loop left `install
            # --apply` reporting "SKIP scheduled task X (definition only)" and
            # then creating X anyway, which is precisely the outcome that loop's
            # comment says must not happen. The plan loop and the write loop
            # both walk the same registry, so a rule applied to one and not the
            # other is not a partial fix — it is a fix that prints.
            if is_definition_only_task(read(source)):
                continue
            destination = os.path.join(TASKS_SRC, name, "SKILL.md")
            desired = concrete(read(source))
            if read(destination) != desired:
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                with open(destination, "w", encoding="utf-8") as fh:
                    fh.write(desired)
    elif task_plan["exact"]:
        os.makedirs(TASKS_QUARANTINE, exist_ok=True)
        for name in task_plan["exact"]:
            source_dir = os.path.join(TASKS_SRC, name)
            destination = os.path.join(TASKS_QUARANTINE, name)
            # The full directory moves so task-local state stays together and
            # the active scheduler directory contains no CARR definition.
            shutil.move(source_dir, destination)

    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    backup = SETTINGS + ".bak-config-as-code"
    if settings_existed:
        shutil.copy2(SETTINGS, backup)
    with open(SETTINGS, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    try:
        json.loads(read(SETTINGS))
    except Exception as exc:
        if settings_existed:
            shutil.copy2(backup, SETTINGS)
            remedy = f"restored {backup}"
        else:
            os.unlink(SETTINGS)
            remedy = "removed the invalid new file"
        print(f"ERROR: write produced unparseable JSON ({exc}) — {remedy}")
        return 1
    written_backups = [backup] if settings_existed else []
    if codex_state == "configured":
        os.makedirs(os.path.dirname(CODEX_HOOKS_SRC), exist_ok=True)
        codex_backup = CODEX_HOOKS_SRC + ".bak-config-as-code"
        if os.path.exists(CODEX_HOOKS_SRC):
            shutil.copy2(CODEX_HOOKS_SRC, codex_backup)
            written_backups.append(codex_backup)
        with open(CODEX_HOOKS_SRC, "w", encoding="utf-8") as fh:
            json.dump(merged_codex, fh, indent=2)
            fh.write("\n")
        try:
            json.loads(read(CODEX_HOOKS_SRC))
        except Exception as exc:
            if os.path.exists(codex_backup):
                shutil.copy2(codex_backup, CODEX_HOOKS_SRC)
            print(f"ERROR: Codex hook write produced unparseable JSON ({exc}) — restored backup")
            return 1
        code_config_backup = CODEX_CONFIG + ".bak-config-as-code"
        shutil.copy2(CODEX_CONFIG, code_config_backup)
        written_backups.append(code_config_backup)
        with open(CODEX_CONFIG, "w", encoding="utf-8") as fh:
            fh.write(permission_config)
        try:
            import tomllib
            tomllib.loads(read(CODEX_CONFIG))
        except Exception as exc:
            shutil.copy2(code_config_backup, CODEX_CONFIG)
            print(f"ERROR: Codex config write produced invalid TOML ({exc}) — restored backup")
            return 1
    # NO RESTART NEEDED, and the old message here said otherwise for months.
    # Live-tested 2026-08-09 with two independent confirmations: git-writer-gate
    # and gate-edit-gate were both installed MID-SESSION and both fired in a
    # session that started before either existed. Claude Code reads the hooks
    # block per tool call, not once at session start. This matters — the old
    # wording implied every other running session stayed unguarded until it was
    # restarted, which would have made a gate install nearly useless on a machine
    # running five sessions. The opposite is true: an install takes effect
    # everywhere immediately. Rule 97326357 — a claim about a surface becomes
    # doctrine only after a live test from that surface.
    if launchd_activation_failures:
        print("ERROR: LaunchAgent install/reload failed for: "
              + ", ".join(launchd_activation_failures))
        return 1
    backup_note = ", ".join(written_backups) if written_backups else "none; new files"
    if codex_state == "absent":
        client_note = "Codex was not configured and was left absent."
    else:
        client_note = ("Codex must trust a changed non-managed hook definition before it "
                       "runs. Both configured clients are protected.")
    print(f"\nWROTE OK (backups: {backup_note}). Claude Code reads its hooks block "
          f"per tool call; {client_note}")
    return 0


def config_selftest():
    """Regression proof that Codex install touches only CARR-owned tuples."""
    desired = {"hooks": {"Stop": [{"hooks": [{
        "type": "command", "command": "/Users/booko/carr-system/hooks/completion-evidence-gate.py", "timeout": 15,
    }]}]}}
    live = {"hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": "/Users/booko/other/hooks/keep.py", "timeout": 5}]},
        {"hooks": [{"type": "command", "command": "/Users/booko/carr-system/hooks/old.py", "timeout": 10}]},
        {"hooks": [
            {"type": "command", "command": "/Users/booko/other/hooks/mixed.py", "timeout": 5},
            {"type": "command", "command": "/Users/booko/carr-system/hooks/old2.py", "timeout": 10},
        ]},
    ]}, "user_setting": {"keep": True}}
    merged = merge_codex_carr_hooks(live, desired)
    commands = [hook.get("command") for group in merged["hooks"]["Stop"]
                for hook in group.get("hooks", []) if isinstance(hook, dict)]
    cases = [
        ("unrelated Codex hook preserved", "/Users/booko/other/hooks/keep.py" in commands),
        ("mixed unrelated Codex hook preserved", "/Users/booko/other/hooks/mixed.py" in commands),
        ("stale CARR hook removed", "/Users/booko/carr-system/hooks/old.py" not in commands),
        ("desired CARR hook installed once", commands.count(
            "/Users/booko/carr-system/hooks/completion-evidence-gate.py") == 1),
        ("unrelated top-level key preserved", merged.get("user_setting") == {"keep": True}),
        ("CARR snapshot exact", carr_owned_hooks_document(merged, ["Stop"]) == desired),
    ]
    for label, passed in cases:
        print(f"{'PASS' if passed else 'FAIL'}  {label}")
    print(f"config-as-code-selftest: {sum(ok for _, ok in cases)}/{len(cases)} passed")
    return 0 if all(ok for _, ok in cases) else 1


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    apply = "--apply" in sys.argv
    if mode == "--selftest":
        return config_selftest()
    if mode == "check":
        return cmd_check()
    if mode == "pull":
        return cmd_pull(apply)
    if mode == "install-codex-continuity":
        return cmd_install_codex_continuity(apply)
    if mode == "remove-codex-continuity":
        return cmd_install_codex_continuity(apply, remove=True)
    if mode == "verify-codex-continuity":
        return cmd_verify_codex_continuity()
    if mode == "install":
        return cmd_install(apply)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
