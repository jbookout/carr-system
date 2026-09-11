"""canonical_freshness — WR-000040 AC-FRESH, as a library with no door of its own.

AC-FRESH: "A scheduled fast-forward keeps canonical within one day of
origin/main and pages visibly when the tree is dirty or the fast-forward
fails."

WHY THIS EXISTS, measured 2026-09-11. The canonical checkout at ~/carr-system
sat at d75a4ab6 — 111 commits behind origin/main, 0 ahead — and had done for
nine days. Twenty of its twenty-four "modified" paths held bytes that are
already on origin/main, in commits canonical has never pulled: the tree was not
dirty with anyone's work, it was dirty with the past. Every derived thing —
dependency caches, repo-vs-production checks, the Dell migration precondition —
inherited that.

WHY IT IS A LIBRARY AND NOT TWO SCRIPTS, which is a fact about this repository
rather than a style preference. ops/scac-mutation-inventory.mjs discovers a SCAC
ingress from the git index: `isScriptEntrypoint` counts any tracked file with a
shebang, and any .py whose source carries a `__main__` guard. Two new
executable scripts would have been two new rows in a SEALED inventory whose
review overlay (`current_source_review`) can only re-digest ingresses it already
knows — `assertCurrentSourceInventoryMatchesFixture` throws "unknown ingress" on
an added key. A new row therefore costs a whole registry successor. This module
carries NO shebang and NO such guard on purpose: it is reached only through
tools/repo-hygiene-janitor.py, which is already an inventoried ingress, so the
machinery lands against one already-registered row. That is the same shape
tools/room-bridge/worktree_runtime_isolation.py already uses, and the janitor
already knows how to load such a module by path.

── THE PROPERTY fast_forward() MUST HOLD ────────────────────────────────────

IT NEVER DESTROYS ANYTHING. The only git command that moves the branch is
`git merge --ff-only`, which fails rather than rewriting history, and it is not
reached at all unless the tree is clean of TRACKED modifications. No reset, no
checkout of a path, no clean, no stash — a fast-forward that had to discard
something to succeed would be the exact failure this job is supposed to page
about.

TRACKED DIRT ONLY, which is the accepted plan's wording and not a softening.
Untracked paths are a separate settlement question: WR-000040's AC-CLEAN routes
each one to Joe for a discard-or-land ruling, and a freshness job that refused
to run until that ruling arrived would simply never run. So untracked paths are
REPORTED and do not block the fast-forward; tracked modifications block it
absolutely, because those are somebody's edit.

── THE PROPERTY watchdog() MUST HOLD ────────────────────────────────────────

IT OBSERVES THE REPOSITORY, NEVER THE JOB. A freshness machine whose only alarm
lives inside the freshness job is silent in the one failure that matters most —
the job never fired at all. launchd drops an agent whose plist is unloaded,
whose program is missing, or whose machine slept through every window, and it
says nothing. So `watchdog()` reaches its verdict from the repository alone and
never calls `fast_forward()` or reads anything it wrote. Sharing a module with
the job it watches does not weaken that; calling it would, and a test asserts
the call is absent.

WHAT IT PAGES ON: tracked dirt; staleness past the AC-FRESH bar; canonical
holding commits origin/main does not have; and an unverifiable fetch.
WHAT IT DOES NOT PAGE ON, deliberately: untracked paths. AC-CLEAN routes those
to Joe for a per-path ruling, and a watchdog that paged daily about four paths
awaiting a human decision would train its reader to ignore it. They are COUNTED
and NAMED in every report — visible, never alarming.

HOW IT PAGES, by two independent routes so one channel failing is not silence:

  1. A durable record-layer problem report through the Bash door,
     `./run.sh call report-problem`, with the COMPLETE registered payload —
     idempotency_key, situation, title, desired_outcome, acceptance_criteria.
     The verb's validator (`validate` in mcp-server/src/work-request-intake.js)
     takes an EXACT key set and refuses an incomplete call outright, so a
     situation-only page is not a page at all — it is a rejected call.
  2. A nonzero exit, so bin/run-scheduled.sh records a failed run and the
     service shows red in `ops-record health`.

Route 1 failing never suppresses route 2. A watchdog that could be silenced by
an unreachable store would be worse than none.
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TextIO

# Exit codes. Each is a distinct thing a reader needs to tell apart, and they
# are named here so a changed code fails a test loudly rather than turning an
# assertion into a tautology.
OK = 0
REFUSED_TRACKED_DIRT = 3
REFUSED_AHEAD = 4
FAILED = 5
ALARM = 6
BAD_REPOSITORY = 64

DEFAULT_MAX_AGE_HOURS = 24

# The verb's own caps, copied from mcp-server/src/work-request-intake.js so a
# page is never rejected for length. situation is capped at 1000 there; 900
# leaves room for the framing the server adds and matches the ~926 ceiling
# measured against the live verb.
SITUATION_CAP = 900
TITLE_CAP = 200
DESIRED_OUTCOME_CAP = 2000
CRITERION_TEXT_CAP = 500


class _Git:
    """Every git call this module makes, in one place, so a reader can confirm
    the list contains nothing that writes to the worktree or the index."""

    READ_ONLY = ("status", "rev-list", "rev-parse", "log", "fetch")

    def __init__(self, repository: Path):
        self.repository = Path(repository)

    def __call__(self, *args: str) -> subprocess.CompletedProcess:
        if args and args[0] not in self.READ_ONLY and args[0] != "merge":
            raise AssertionError(f"canonical_freshness does not run `git {args[0]}`")
        return subprocess.run(["git", "-C", str(self.repository), *args],
                              capture_output=True, text=True)

    def out(self, *args: str, default: str = "") -> str:
        done = self(*args)
        return done.stdout.strip() if done.returncode == 0 else default


def _is_checkout(repository: Path) -> bool:
    # A worktree's .git is a FILE, not a directory, so this tests for either.
    return (repository / ".git").exists()


def _counts(git: _Git) -> dict[str, Any]:
    tracked = [line for line in git.out("status", "--porcelain",
                                        "--untracked-files=no").splitlines() if line]
    untracked = [line for line in git.out("status", "--porcelain",
                                          "--untracked-files=normal").splitlines()
                 if line.startswith("??")]
    return {"tracked": tracked, "untracked": untracked}


# ─────────────────────────────────────────────────────────────────────────────
# The fast-forward.
# ─────────────────────────────────────────────────────────────────────────────


def fast_forward(repository: str | Path, *, dry_run: bool = False,
                 out: TextIO, err: TextIO) -> int:
    repository = Path(repository)
    if not _is_checkout(repository):
        print(f"canonical-fast-forward: {repository} is not a git checkout", file=err)
        return BAD_REPOSITORY
    git = _Git(repository)

    branch = git.out("rev-parse", "--abbrev-ref", "HEAD", default="unknown")
    print(f"canonical-fast-forward: repository={repository} branch={branch}", file=out)

    # Tracked dirt first, BEFORE the fetch: a job that refuses should refuse
    # cheaply, and a fetch on a dirty tree invites the temptation to "just tidy".
    state = _counts(git)
    print(f"canonical-fast-forward: tracked_modified={len(state['tracked'])} "
          f"untracked={len(state['untracked'])}", file=out)

    if state["tracked"]:
        print(f"canonical-fast-forward: REFUSED — {len(state['tracked'])} tracked path(s) "
              f"modified in canonical.", file=err)
        print("canonical-fast-forward: those are somebody's edit. Land or discard them; "
              "this job will not.", file=err)
        for line in state["tracked"]:
            print(line, file=err)
        return REFUSED_TRACKED_DIRT

    if git("fetch", "--quiet", "origin", "main").returncode != 0:
        print("canonical-fast-forward: FAILED — could not fetch origin/main", file=err)
        return FAILED

    behind = git.out("rev-list", "--count", "HEAD..origin/main", default="unknown")
    ahead = git.out("rev-list", "--count", "origin/main..HEAD", default="unknown")
    print(f"canonical-fast-forward: behind={behind} ahead={ahead}", file=out)
    if behind == "unknown" or ahead == "unknown":
        print("canonical-fast-forward: FAILED — could not compare against origin/main", file=err)
        return FAILED

    if int(ahead) != 0:
        print(f"canonical-fast-forward: REFUSED — canonical is {ahead} commit(s) AHEAD of "
              f"origin/main.", file=err)
        print("canonical-fast-forward: a fast-forward cannot represent that, and this job "
              "never rewrites.", file=err)
        return REFUSED_AHEAD

    if int(behind) == 0:
        print("canonical-fast-forward: already current with origin/main", file=out)
        return OK

    if dry_run:
        print(f"canonical-fast-forward: --dry-run, would fast-forward {behind} commit(s)",
              file=out)
        return OK

    merged = git("merge", "--ff-only", "origin/main")
    if merged.returncode != 0:
        print("canonical-fast-forward: FAILED — ff-only merge refused", file=err)
        print(merged.stderr.strip(), file=err)
        return FAILED

    head = git.out("rev-parse", "--short", "HEAD", default="unknown")
    print(f"canonical-fast-forward: fast-forwarded {behind} commit(s) to {head}", file=out)
    return OK


# ─────────────────────────────────────────────────────────────────────────────
# The page. Its whole job is to be a COMPLETE registered call.
# ─────────────────────────────────────────────────────────────────────────────


def page_payload(facts: Mapping[str, Any], reasons: Sequence[str], *,
                 idempotency_key: str | None = None) -> dict[str, Any]:
    """The exact argument object `report-problem` accepts — no more, no less.

    The verb's validator takes an EXACT key set and refuses an unknown field as
    hard as a missing one, so this function is the single place that knows the
    shape. A fresh idempotency_key is minted PER RUN rather than derived from
    the facts: two runs that observe the same dirty tree are two observations,
    and collapsing them onto one key would silently drop the second.
    """
    codes = ", ".join(sorted(facts["codes"])) or "unclassified"
    title = f"Canonical checkout freshness alarm: {codes}"[:TITLE_CAP]
    measurements = (f"tracked_modified={facts['tracked_modified']} "
                    f"untracked={facts['untracked']} behind={facts['behind']} "
                    f"ahead={facts['ahead']} head_age_hours={facts['head_age_hours']} "
                    f"bar={facts['max_age_hours']}h repository={facts['repository']}")
    situation = (f"WR-000040 AC-FRESH. {' '.join(reasons)} Measured: {measurements}."
                 )[:SITUATION_CAP]
    desired = (
        "Canonical returns to a state the freshness job can keep: no tracked modification "
        "in the shared checkout, and HEAD within the AC-FRESH bar of origin/main. Whoever "
        "owns the tracked edits lands or discards them — this job never will, because a "
        "fast-forward bought with somebody's edit is the failure it exists to report. "
        "Untracked paths are listed for context only and stay for Joe's AC-CLEAN ruling."
    )[:DESIRED_OUTCOME_CAP]
    criteria = [
        {"id": "CANONICAL-CLEAN",
         "text": ("`git status --porcelain --untracked-files=no` in the canonical checkout "
                  "prints nothing.")[:CRITERION_TEXT_CAP]},
        {"id": "CANONICAL-CURRENT",
         "text": (f"`git rev-list --count HEAD..origin/main` is 0, or HEAD is younger than "
                  f"{facts['max_age_hours']}h.")[:CRITERION_TEXT_CAP]},
        {"id": "CANONICAL-NOT-AHEAD",
         "text": ("`git rev-list --count origin/main..HEAD` is 0, so no work exists only in "
                  "the shared checkout.")[:CRITERION_TEXT_CAP]},
    ]
    return {
        "idempotency_key": idempotency_key or str(uuid.uuid4()),
        "situation": situation,
        "title": title,
        "desired_outcome": desired,
        "acceptance_criteria": criteria,
    }


def run_sh_pager(repository: str | Path) -> Callable[[Mapping[str, Any]], tuple[bool, str]]:
    """The real alarm channel: the Bash door named in WR-000040's plan.

    Returns (landed, detail). A nonzero exit is a rejection, and so is a
    zero-exit body carrying an `error` key — the verb reports a refused call in
    its payload, so reading only the exit status would call a rejection a page.
    """
    repository = Path(repository)

    def page(payload: Mapping[str, Any]) -> tuple[bool, str]:
        done = subprocess.run([str(repository / "run.sh"), "call", "report-problem",
                               json.dumps(payload)],
                              cwd=str(repository), capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)
        body = (done.stdout or "").strip()
        if done.returncode != 0:
            return False, f"exit={done.returncode} {(done.stderr or body).strip()[:400]}"
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError):
            return True, body[:400]
        if isinstance(parsed, dict) and parsed.get("error"):
            return False, f"verb refused: {parsed['error']}"
        return True, body[:400]

    return page


# ─────────────────────────────────────────────────────────────────────────────
# The watchdog.
# ─────────────────────────────────────────────────────────────────────────────


def watchdog(repository: str | Path, *, max_age_hours: int = DEFAULT_MAX_AGE_HOURS,
             page: bool = True,
             pager: Callable[[Mapping[str, Any]], tuple[bool, str]] | None = None,
             now: Callable[[], float] = time.time,
             out: TextIO, err: TextIO) -> int:
    repository = Path(repository)
    if not _is_checkout(repository):
        print(f"canonical-dirty-watchdog: {repository} is not a git checkout", file=err)
        return BAD_REPOSITORY
    git = _Git(repository)

    fetch_ok = git("fetch", "--quiet", "origin", "main").returncode == 0
    state = _counts(git)
    behind = git.out("rev-list", "--count", "HEAD..origin/main", default="unknown")
    ahead = git.out("rev-list", "--count", "origin/main..HEAD", default="unknown")

    # Age of the tip canonical is sitting on, which is what "within one day"
    # means: not when the job last ran, but how old the code in the tree is.
    head_epoch = int(git.out("log", "-1", "--format=%ct", "HEAD", default="0") or 0)
    age_hours = int((now() - head_epoch) // 3600) if head_epoch else 0

    codes: list[str] = []
    facts: dict[str, Any] = {
        "repository": str(repository),
        "tracked_modified": len(state["tracked"]),
        "untracked": len(state["untracked"]),
        "behind": behind,
        "ahead": ahead,
        "head_age_hours": age_hours,
        "max_age_hours": max_age_hours,
        "codes": codes,
    }

    print(f"canonical-dirty-watchdog: repository={repository}", file=out)
    print(f"canonical-dirty-watchdog: tracked_modified={facts['tracked_modified']} "
          f"untracked={facts['untracked']} behind={behind} ahead={ahead} "
          f"head_age_hours={age_hours}", file=out)
    if state["untracked"]:
        print("canonical-dirty-watchdog: untracked paths (reported, not alarmed — "
              "AC-CLEAN is Joe's ruling):", file=out)
        for line in state["untracked"]:
            print(f"  {line}", file=out)

    reasons: list[str] = []
    if facts["tracked_modified"]:
        codes.append("tracked-dirt")
        reasons.append(f"{facts['tracked_modified']} tracked path(s) modified in a tree no "
                       f"session may edit.")
    if ahead != "unknown" and int(ahead) != 0:
        codes.append("ahead-of-origin")
        reasons.append(f"Canonical is {ahead} commit(s) ahead of origin/main.")
    if age_hours > max_age_hours and behind != "unknown" and int(behind) != 0:
        codes.append("stale")
        reasons.append(f"Canonical HEAD is {age_hours}h old and {behind} commit(s) behind "
                       f"origin/main, past the {max_age_hours}h bar.")
    if not fetch_ok:
        codes.append("fetch-failed")
        reasons.append("Could not fetch origin/main, so freshness is unverifiable.")

    if not reasons:
        print(f"canonical-dirty-watchdog: OK — no tracked dirt, within {max_age_hours}h of "
              f"origin/main", file=out)
        return OK

    print(f"canonical-dirty-watchdog: ALARM — {' '.join(reasons)}", file=err)
    for line in state["tracked"]:
        print(line, file=err)

    if page:
        payload = page_payload(facts, reasons)
        # The store is a best-effort SECOND channel. Its failure is printed and
        # then ignored: the exit code below is the channel that cannot be
        # silenced. This is also why the pager is injected — the paging path is
        # driven end to end in the tests against a stand-in run.sh.
        landed, detail = (pager or run_sh_pager(repository))(payload)
        if landed:
            print(f"canonical-dirty-watchdog: paged report-problem "
                  f"idempotency_key={payload['idempotency_key']}", file=out)
        else:
            print(f"canonical-dirty-watchdog: the record-layer page did not land ({detail}); "
                  f"the nonzero exit below still stands", file=err)

    return ALARM


def run(mode: str, *, repository: str | Path, max_age_hours: int = DEFAULT_MAX_AGE_HOURS,
        page: bool = True, dry_run: bool = False, out: TextIO, err: TextIO) -> int:
    """The one door tools/repo-hygiene-janitor.py calls."""
    if mode == "fast-forward":
        return fast_forward(repository, dry_run=dry_run, out=out, err=err)
    if mode == "watchdog":
        return watchdog(repository, max_age_hours=max_age_hours, page=page, out=out, err=err)
    raise ValueError(f"unknown canonical freshness mode {mode!r}")
