#!/usr/bin/env python3
"""worktree-self-plumb.py — SessionStart self-plumb for a carr-system
worktree Claude Code (or Codex, or a bare `git worktree add`) created without
ever calling bin/worktree.sh — plus the orphan-worktree reaper (see below).

THE LIVE FAILURE THIS CLOSES. A session landed in a worktree created by
Claude Code's own native isolation (a bare `git worktree add` under
.claude/worktrees/, outside this repo's `run.sh worktree` command). It found
no mcp-server/node_modules there, could not run its own tooling, and fell
back to running from the shared CANONICAL checkout instead — the exact
multi-writer collision `run.sh worktree` exists to prevent (see that file's
header). bin/worktree.sh's create path already solves this for worktrees IT
creates, by symlinking .venv, out, and mcp-server/node_modules back to the
canonical tree. A worktree created by any other door gets none of that,
because nothing calls the linking step for it.

`bin/worktree.sh --plumb [path]` (added alongside this file) is the same
three links, made callable against a worktree bin/worktree.sh did not
create. THIS hook is what makes that automatic: at SessionStart, if the
session's cwd is inside a non-canonical carr-system worktree that is
missing any of the three links, it calls `--plumb` for that worktree and
says so in the brief.

WHY THIS IS A SEPARATE HOOK, NOT AN EXTENSION OF hooks/session-brief.py.
The task that produced this file named session-brief.py as the natural
extension point, on the reasonable assumption that it is the general
SessionStart entry point every local session runs, and that "cwd is not a
carr-system worktree" would just be the common case it skips. That assumption
does not hold: session-brief.py is wired ONLY into the two VAULT project
settings files (claude-tree/settings/my-drive-root.settings.json and
carr-ai-project.settings.json, deployed by bin/sync-settings.sh to the
"My Drive" and "My Drive/CARR AI" trees). It is never wired into any
carr-system checkout's own settings — this repo's tracked .claude/settings.json
carries no SessionStart hook at all today, and neither ~/.claude/settings.json
(user-level; SessionStart there is gate-integrity.py only) reaches it for a
session rooted in a carr-system worktree specifically. Concretely: 100% of
session-brief.py's actual invocations are vault-rooted sessions, 0% are
carr-system-worktree sessions — extending it would be correct code that
never runs for the failure this file exists to fix.

The fix that actually reaches the failure has to live somewhere every
carr-system worktree carries BY CONSTRUCTION, regardless of which door
created it — and that is exactly what a file tracked in the repo gives for
free: `git worktree add` (by us, by Claude Code's native isolation, by Codex,
by hand) always checks out HEAD, so this repo's own .claude/settings.json —
where this hook is registered as SessionStart, added in the same commit —
ships into every worktree automatically. No per-worktree deployment step, no
"most doors don't know to run it" gap. That is the same reasoning
bin/worktree.sh's own header gives for CREATE PATH FRESHNESS and --sweep:
the fix has to live on a path every case actually walks, not a path only the
already-correct case walks.

THE ORPHAN REAPER (2026-08-18). Second job, same front door, same reasoning
about where the fix has to live. A session that dies mid-flight — usage
exhaustion, a crash, a closed laptop — never removes its worktree, and no
code path ever visited them again: on 2026-08-18 a manual sweep removed 73
such orphans (4.4GB) from .claude/worktrees and .codex-worktrees. SessionStart
is the one event every future session actually walks, so the reaper runs here.

It is NOT bin/worktree.sh --sweep, and the difference is deliberate. --sweep
answers "is this branch's WORK finished?" — it requires merged-into-origin/main
and 48h of file idleness, because reaping an unmerged work tree early would
hide unfinished work. The orphan reaper answers the cheaper question "is any
SESSION still using this tree?", and for that the merge state is irrelevant:
removing a clean worktree on a NAMED branch loses nothing (the branch ref
lives in the main repo; `git worktree remove` never deletes it), so an
abandoned-but-unmerged branch worktree is exactly the case --sweep can never
reap and this can. The rules below are the exact rules the 2026-08-18 manual
sweep proved safe, for each REGISTERED worktree (git worktree list --porcelain):

  skip  the canonical checkout, and this session's own worktree
  skip  locked worktrees (someone said keep, in git's own vocabulary)
  skip  a .git index touched under 6h ago, OR any file inside the working
        tree written under 6h ago — either is a possibly-live session, and
        a build seat that writes for hours without running git only moves
        the second one (defect a4abb972); this
        hook also TOUCHES its own worktree's index at every boot, so a
        resumed session re-marks itself live the moment it starts
  skip  any tree where `git status --porcelain` shows real work or errors —
        uncommitted work is never judged, same refusal --remove enforces;
        "real" excludes this system's own untracked plumbing symlinks,
        because --remove drops those before ITS dirty test (see classify)
  reap  a named branch — via bin/worktree.sh --remove, NEVER raw
        `git worktree remove` (rule a8c55a47: the automated path and the
        manual path must be the same code — that path carries the
        dirty-refusal and plumbing restore-on-refusal)
  reap  a detached HEAD only when its commit is an ancestor of origin/main
        (nothing unpublished at stake); no fetch first, on purpose — an
        ancestor of a STALE origin/main is still an ancestor of the fresh
        one, so staleness only ever keeps more, never reaps more
  then  `git worktree prune` for entries whose directories are already gone

The reaper runs DETACHED in the background (out/worktree-reap.log), because a
boot hook must never make a session wait on 73 directory removals, and a
hook-timeout kill halfway through a removal is worse than slow. A lock file
(out/worktree-reap.lock) keeps two booting sessions from sweeping at once.

hooks/*.py are covered by the gate-integrity.py baseline (rule: a session
that adds or edits a hook re-blesses the baseline in the same commit) — this
file was blessed alongside its addition; see ops/config/gate-baseline.json.

FAIL-SOFT, ALWAYS. A boot-time convenience must never fail or block a
session: any error anywhere in here is swallowed and the hook prints
nothing, same discipline as hooks/session-brief.py's own nightly/loose-work
lines.

Fixtures: ops/worktree-self-plumb-selftest.py (reaper classification and
removal, against real scratch repos).
"""
import json
import os
import subprocess
import sys
import time

# __file__ resolves through the absolute canonical path this hook is always
# invoked by ("${HOME}/carr-system/hooks/worktree-self-plumb.py" in
# .claude/settings.json — the same "always call the canonical copy"
# convention hooks/delegation-gate.py already uses), so REPO is the
# canonical tree regardless of which worktree's cwd triggered this hook.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Must match the three names bin/worktree.sh links at create time and
# re-links under --plumb. Kept here only to decide WHETHER to bother calling
# --plumb (and what to say if we do) — the actual linking decision (what
# counts as already-plumbed, the tracked-real-dir guard) stays solely in
# bin/worktree.sh's link(), never duplicated here (rule a8c55a47).
PLUMB_LINKS = (".venv", "out", os.path.join("mcp-server", "node_modules"))

# ── orphan reaper thresholds — the 2026-08-18 sweep's proven rules ─────────
REAP_MIN_IDLE_S = 6 * 3600     # index younger than this = possibly-live session
REAP_LOCK_STALE_S = 2 * 3600   # a lock older than this belongs to a dead reaper
REAP_REMOVE_TIMEOUT = 600      # one removal; big node_modules dirs are slow


def resolve_cwd(payload):
    return (payload.get("cwd") or payload.get("working_directory")
            or payload.get("workingDirectory") or os.getcwd())


def run_git(args, cwd, timeout=10):
    try:
        p = subprocess.run(["git", *args], cwd=cwd,
                            capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def canonical_root(repo):
    """The canonical checkout, resolved through git rather than assumed.

    REPO is already canonical when this file is invoked by its wired absolute
    path, but a copy run from inside a worktree (a selftest, a hand test)
    would otherwise mistake that worktree for canonical — and for a REAPER
    that confusion must be impossible: the skip-canonical guard has to anchor
    on the tree git itself calls home. Same resolution bin/worktree.sh uses.
    """
    out = run_git(["rev-parse", "--path-format=absolute", "--git-common-dir"], repo)
    if out:
        return os.path.realpath(os.path.dirname(out))
    return os.path.realpath(repo)


# ── orphan reaper ──────────────────────────────────────────────────────────

def worktree_entries(repo):
    """`git worktree list --porcelain` as dicts; [] on any failure."""
    out = run_git(["worktree", "list", "--porcelain"], repo)
    if out is None:
        return []
    entries, cur = [], None
    for ln in out.splitlines():
        if ln.startswith("worktree "):
            if cur:
                entries.append(cur)
            cur = {"path": ln[len("worktree "):].strip()}
        elif cur is None:
            continue
        elif ln.startswith("HEAD "):
            cur["head"] = ln[len("HEAD "):].strip()
        elif ln.startswith("branch refs/heads/"):
            cur["branch"] = ln[len("branch refs/heads/"):].strip()
        elif ln == "bare":
            cur["bare"] = True
        elif ln == "detached":
            cur["detached"] = True
        elif ln == "locked" or ln.startswith("locked "):
            cur["locked"] = True
        elif ln == "prunable" or ln.startswith("prunable "):
            cur["prunable"] = True
    if cur:
        entries.append(cur)
    return entries


def index_gitdir(wt):
    """The worktree's private gitdir (…/.git/worktrees/<id>), or None."""
    dotgit = os.path.join(wt, ".git")
    try:
        if os.path.isdir(dotgit):
            return dotgit                      # a main checkout, not a worktree
        with open(dotgit) as fh:
            first = fh.read().strip()
        if not first.startswith("gitdir:"):
            return None
        gitdir = first.split(":", 1)[1].strip()
        if not os.path.isabs(gitdir):
            gitdir = os.path.normpath(os.path.join(wt, gitdir))
        return gitdir
    except Exception:
        return None


def index_age_s(wt):
    """Seconds since the worktree's .git index moved; None when unknowable.

    The index is the one file every git operation a live session performs
    keeps warm, and it lives OUTSIDE the working tree — a session cannot
    fake it old, and reaping cannot be dodged by it. This is the 6h
    liveness signal the 2026-08-18 sweep used.
    """
    gitdir = index_gitdir(wt)
    if not gitdir:
        return None
    try:
        idx = os.path.join(gitdir, "index")
        st = os.stat(idx) if os.path.exists(idx) else os.stat(gitdir)
        return time.time() - st.st_mtime
    except Exception:
        return None


# A build seat can write files for hours without running a single git
# command, which leaves .git/index cold while the worktree is very much
# alive. That is exactly how defect a4abb972 happened: an automated sweep
# removed a paused build's in-flight evidence because the only liveness
# signal it had was a file the build never touched. So idleness is judged
# on BOTH signals and the youngest one wins.
TREE_SCAN_MAX_ENTRIES = 20000  # past this the tree is too big to judge cheaply


def tree_age_s(wt):
    """Seconds since ANY file inside the worktree moved; None when unknowable.

    Complements index_age_s, which only sees git operations. This sees the
    writes themselves — the signal a build seat actually produces.

    Symlinks are never followed: .venv, out and mcp-server/node_modules are
    plumbing links into the canonical repo, and walking them would read
    canonical's activity as this worktree's and keep every worktree forever.
    A tree too large to scan, or any error, returns None, which classify()
    reads as "do not judge it" — the keep direction, same as every other
    uncertain answer here.
    """
    newest = 0.0
    seen = 0
    skip = {".git", "node_modules", ".venv", "__pycache__"}
    try:
        for root, dirs, files in os.walk(wt, followlinks=False):
            dirs[:] = [d for d in dirs if d not in skip]
            for name in files + dirs:
                seen += 1
                if seen > TREE_SCAN_MAX_ENTRIES:
                    return None
                fp = os.path.join(root, name)
                try:
                    st = os.lstat(fp)
                except OSError:
                    continue
                if st.st_mtime > newest:
                    newest = st.st_mtime
    except Exception:
        return None
    if not newest:
        return None
    return time.time() - newest


def mark_alive(wt):
    """Touch this session's own index so the 6h rule reads it as live.

    A RESUMED session can land in a worktree whose index is days old, and
    nothing guarantees its first git operation beats another boot's reaper.
    Touching at SessionStart makes the liveness rule true by construction
    for every session this hook boots.
    """
    try:
        gitdir = index_gitdir(wt)
        if gitdir:
            idx = os.path.join(gitdir, "index")
            os.utime(idx if os.path.exists(idx) else gitdir, None)
    except Exception:
        pass


def classify(canon, entry, skip_paths):
    """One worktree entry -> ("reap"|"keep"|"prune", reason), or None to ignore.

    Encodes exactly the 2026-08-18 sweep rules — see the module docstring.
    Every uncertain answer (unreadable index, status error, no origin/main)
    lands on "keep": the failure direction is always kept-too-long, never
    reaped-too-eagerly, same as --sweep.
    """
    wt = entry.get("path") or ""
    if not wt or os.path.realpath(wt) in skip_paths:
        return None
    if entry.get("bare"):
        return ("keep", "bare — no working tree to judge")
    if entry.get("locked"):
        return ("keep", "locked")
    if entry.get("prunable") or not os.path.isdir(wt):
        return ("prune", "directory already gone")
    age = index_age_s(wt)
    if age is None:
        return ("keep", "cannot read .git index — not judging it")
    if age < REAP_MIN_IDLE_S:
        return ("keep", f"index touched {age / 3600:.1f}h ago (<6h, possibly live)")
    # Second liveness signal: the writes themselves. A build that never runs
    # git leaves the index cold while filling the tree — defect a4abb972.
    twork = tree_age_s(wt)
    if twork is None:
        return ("keep", "cannot judge working-tree mtimes — not judging it")
    if twork < REAP_MIN_IDLE_S:
        return ("keep", f"working tree written {twork / 3600:.1f}h ago "
                        "(<6h, possibly a live build)")
    age = min(age, twork)
    status = run_git(["status", "--porcelain"], wt, timeout=30)
    if status is None:
        return ("keep", "git status failed — not judging it")
    # Judge dirtiness the way bin/worktree.sh --remove will: its drop_plumbing
    # deletes this script's own untracked .venv/out/node_modules symlinks
    # BEFORE the dirty test, so an untracked plumbing symlink is not work.
    # Older checkouts whose .gitignore predates mcp-server/node_modules show
    # exactly that as `??` — found live 2026-08-18, ~20 worktrees kept for a
    # symlink the removal door would have dropped. Anything else stays a keep,
    # and remove_one re-judges after actually dropping, so a mismatch here
    # can only under-reap, never over-reap.
    def is_plumbing(line):
        if not line.startswith("?? "):
            return False
        rel = line[3:].strip().strip('"').rstrip("/")
        return rel in PLUMB_LINKS and os.path.islink(os.path.join(wt, rel))
    work = [ln for ln in status.splitlines() if ln.strip() and not is_plumbing(ln)]
    if work:
        return ("keep", f"uncommitted work ({len(work)} paths)")
    if entry.get("branch"):
        return ("reap", f"clean, idle {age / 3600:.0f}h, branch "
                        f"{entry['branch']} survives in the main repo")
    head = entry.get("head") or ""
    if not head:
        return ("keep", "detached with no readable HEAD")
    if run_git(["show-ref", "--verify", "--quiet",
                "refs/remotes/origin/main"], canon) is None:
        return ("keep", "detached and no origin/main to test ancestry against")
    if run_git(["merge-base", "--is-ancestor", head,
                "refs/remotes/origin/main"], canon) is None:
        return ("keep", f"detached at {head[:8]}, NOT an ancestor of origin/main")
    return ("reap", f"clean, idle {age / 3600:.0f}h, detached at {head[:8]} "
                    "already on origin/main")


def reap_main(argv):
    """The background sweep. `--dry-run` reports without removing; `--skip
    <path>` protects the invoking session's worktree; `--repo <path>` retargets
    (selftests and hand runs only — the wired hook never passes it)."""
    def arg_after(flag):
        if flag in argv:
            i = argv.index(flag)
            if i + 1 < len(argv):
                return argv[i + 1]
        return None

    dry = "--dry-run" in argv
    canon = canonical_root(arg_after("--repo") or REPO)
    skip_paths = {canon}
    skip = arg_after("--skip")
    if skip:
        skip_paths.add(os.path.realpath(skip))

    def say(msg):
        print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')}  {msg}", flush=True)

    lock = os.path.join(canon, "out", "worktree-reap.lock")
    try:
        os.makedirs(os.path.dirname(lock), exist_ok=True)
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            if time.time() - os.path.getmtime(lock) < REAP_LOCK_STALE_S:
                return 0                     # another reaper is live — quiet
            os.unlink(lock)                  # dead reaper's leftovers
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except Exception:
            return 0
    except Exception:
        return 0
    with os.fdopen(fd, "w") as fh:
        fh.write(str(os.getpid()))

    try:
        say(f"reap start ({'dry-run' if dry else 'live'}) repo={canon}")
        reaped = kept = 0
        for entry in worktree_entries(canon):
            verdict = classify(canon, entry, skip_paths)
            if verdict is None:
                continue
            action, reason = verdict
            name = os.path.basename(entry.get("path") or "")
            if action == "prune":
                continue                     # `git worktree prune` below owns it
            if action == "keep":
                kept += 1
                if dry:
                    say(f"KEEP  {name} — {reason}")
                continue
            if dry:
                say(f"would remove {name} — {reason}")
                reaped += 1
                continue
            # rule a8c55a47: removal goes through the SAME door a human uses,
            # which re-checks dirty and restores plumbing on any refusal.
            try:
                p = subprocess.run(
                    ["zsh", os.path.join(canon, "bin", "worktree.sh"),
                     "--remove", entry["path"]],
                    cwd=canon, capture_output=True, text=True,
                    timeout=REAP_REMOVE_TIMEOUT)
            except Exception as exc:
                say(f"KEEP  {name} — removal errored: {exc}")
                kept += 1
                continue
            if p.returncode == 0:
                say(f"REAPED {name} — {reason}")
                reaped += 1
            else:
                detail = " ".join((p.stdout + " " + p.stderr).split())[:200]
                say(f"KEEP  {name} — --remove refused: {detail}")
                kept += 1
        run_git(["worktree", "prune"], canon)
        say(f"reap done: {reaped} {'would be ' if dry else ''}reaped, {kept} kept")
    finally:
        try:
            os.unlink(lock)
        except Exception:
            pass
    return 0


def maybe_spawn_reaper(canon, current_wt):
    """Detach a reaper when the cheap signals say there may be orphans.

    Cheap means list + stat only — no `git status` here, because this runs
    inside the boot hook's 20s budget. The count returned is CANDIDATES
    (registered, unlocked, idle 6h+); the background pass applies the
    dirty/ancestry rules and may well keep them all.
    """
    skip_paths = {canon, os.path.realpath(current_wt)}
    cands = 0
    for entry in worktree_entries(canon):
        wt = entry.get("path") or ""
        if not wt or os.path.realpath(wt) in skip_paths:
            continue
        if entry.get("bare") or entry.get("locked") or entry.get("prunable"):
            continue
        if not os.path.isdir(wt):
            cands += 1                       # prune fodder — worth a pass too
            continue
        age = index_age_s(wt)
        if age is not None and age >= REAP_MIN_IDLE_S:
            cands += 1
    if not cands:
        return 0
    lock = os.path.join(canon, "out", "worktree-reap.lock")
    try:
        if time.time() - os.path.getmtime(lock) < REAP_LOCK_STALE_S:
            return 0                         # a reaper is already on it
    except OSError:
        pass
    log = os.path.join(canon, "out", "worktree-reap.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    try:
        if os.path.getsize(log) > 1_000_000:
            os.replace(log, log + ".old")
    except OSError:
        pass
    with open(log, "a") as fh:
        subprocess.Popen(
            [sys.executable or "python3", os.path.abspath(__file__),
             "--reap", "--skip", current_wt],
            cwd=canon, stdout=fh, stderr=subprocess.STDOUT,
            start_new_session=True)
    return cands


def main():
    if "--reap" in sys.argv[1:]:
        # Detached child (or a hand/selftest run) — no SessionStart payload.
        try:
            return reap_main(sys.argv[1:])
        except Exception:
            return 0                         # fail-soft, like the hook itself

    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    try:
        cwd = resolve_cwd(payload)
        if not cwd or not os.path.isdir(cwd):
            return 0

        toplevel = run_git(["rev-parse", "--show-toplevel"], cwd)
        if not toplevel:
            return 0  # not inside any git working tree — most sessions (the vault) end here

        toplevel = os.path.realpath(toplevel)
        canon = canonical_root(REPO)

        if toplevel != canon:
            # If this hook fired at all, cwd is under a worktree that carries
            # this repo's tracked .claude/settings.json (that is the only way
            # Claude Code would have run it) — so toplevel is necessarily a
            # carr-system checkout. Whether it is a REGISTERED worktree of THIS
            # canonical tree is still --plumb's own guard to enforce; if it
            # refuses, this hook just prints nothing (see except below).
            mark_alive(toplevel)
            missing = [name for name in PLUMB_LINKS
                       if not os.path.islink(os.path.join(toplevel, name))
                       and os.path.isdir(os.path.join(canon, name))]
            if missing:
                plumb = subprocess.run(
                    ["zsh", os.path.join(canon, "bin", "worktree.sh"), "--plumb", toplevel],
                    cwd=canon, capture_output=True, text=True, timeout=20)
                if plumb.returncode == 0:
                    applied = [ln.strip() for ln in plumb.stdout.splitlines() if ln.strip()]
                    print(
                        "worktree self-plumb: this session's worktree "
                        f"({os.path.basename(toplevel)}) was missing {', '.join(missing)} "
                        "— a door other than run.sh worktree created it. Applied: "
                        + "; ".join(applied)
                    )
                # A non-zero exit (not a registered worktree, or some other
                # refusal) is deliberately silent here — the boot hook proposes
                # the fix, it does not surface --plumb's own refusal reasoning
                # at every session start; run `./run.sh worktree --plumb` by
                # hand to see it.

        # The orphan reaper runs for canonical AND worktree sessions — the
        # canonical tree is where a human most often sits, and its sessions
        # are exactly the ones that notice 4.4GB of dead checkouts.
        n = maybe_spawn_reaper(canon, toplevel)
        if n:
            print(
                f"worktree reaper: {n} idle worktree candidate(s) — sweeping in "
                "the background (clean + idle 6h + branch-safe rules; "
                "log: out/worktree-reap.log)"
            )
    except Exception:
        pass  # fail-soft: this must never block or fail a session
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
