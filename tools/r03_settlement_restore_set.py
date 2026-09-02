#!/usr/bin/env python3
"""Derive the R03 settlement's RESTORE set, fail-closed.

WHY THIS IS ITS OWN MODULE AND NOT A LOOP INSIDE THE AUTHORING SCRIPT.
On 2026-09-02 the authoring script built the restore set by taking every path
`git status --porcelain` reported as dirty in the canonical checkout. The
settlement then restores each of those paths to its pinned blob. On a checkout
that a dozen sessions write continuously, "dirty" does not mean "debris" — it
means SOMEBODY IS MID-EDIT. The manifest authored that day would have reverted
two files belonging to another session (its in-flight fix to the worktree
reaper), which is the precise thing decision bf48e5aa had ruled out hours
earlier: a partner authorisation to fire the sweep does not extend to
discarding another session's uncommitted work.

That rule existed only as prose in a decision record. Prose does not run. This
module is that decision expressed as a check, in the one place the set is built.

THE PROCEDURE, ordered, so a second reader reaches the same answer:

  1. Read `git status --porcelain -z` from RAW stdout.
  2. Every reported path is a CANDIDATE.
  3. A candidate enters the restore set ONLY if it is on the operator's
     explicitly approved allow-list.
  4. A candidate that is NOT on the allow-list REFUSES the whole authoring run.
     It is never silently enrolled and never silently skipped.
  5. A clean tree with an empty allow-list yields an empty restore set. That is
     the normal, healthy case, and it is what the branch-only settlement uses.

WHY REFUSE RATHER THAN SKIP. Skipping would author a manifest that quietly did
less than the operator believed, and the operator would find out by reading a
diff that never came. Refusing costs one re-run on a quiet tree and cannot
destroy anything.

WHY NOT DECIDE OWNERSHIP WITH ops/worktree-attribution.py. That tool answers
"which WORKTREE has this path dirty", and it was measured against the very case
that motivated this module: for hooks/worktree-self-plumb.py, dirty in CANONICAL
itself rather than in any worktree, it reports `owner UNKNOWN`. An
attribution-based predicate would therefore have MISSED the exact file it was
written to protect. Attribution is still valuable — it names a human-readable
owner in the refusal message — but it is reporting, not the gate. The gate is
the allow-list, because "no worktree claims it" is not evidence that nobody is
editing it.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable, Sequence


class RestoreSetRefusal(RuntimeError):
    """Raised when the live tree carries a dirty path the operator did not approve."""


def _git_stdout(repository: Path, *args: str) -> str:
    """Return git's stdout VERBATIM.

    Deliberately does not .strip(). `git status --porcelain` encodes the
    worktree status in column 2, so an unstaged modification begins with a
    SPACE: " M hooks/x.py". A helper that strips the whole captured blob eats
    that leading space on the FIRST line only; a parser slicing line[3:] then
    starts one character late and records "ooks/x.py", a path that does not
    exist. That shipped in a real manifest on 2026-09-02, and it is invisible
    on every line but the first, which is why review passed it.
    """
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def dirty_paths(repository: Path) -> list[str]:
    """Every path git reports as modified, added, deleted or conflicted.

    Uses -z so a path containing a space, a quote or a newline cannot be
    mis-split. Untracked entries are excluded: the settlement's restore stage
    rewrites tracked content to a pinned blob, and an untracked file has no
    pinned blob to be restored to.
    """
    raw = _git_stdout(repository, "status", "--porcelain", "-z")
    out: list[str] = []
    fields = raw.split("\0")
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if not entry:
            continue
        # Porcelain v1 -z: two status characters, one space, then the path.
        status, path = entry[:2], entry[3:]
        if status == "??":
            continue
        if "R" in status or "C" in status:
            # A rename/copy is followed by its ORIGIN path as the next field.
            i += 1
        if path:
            out.append(path)
    return sorted(set(out))


def attribute(repository: Path, paths: Sequence[str]) -> dict[str, str]:
    """Best-effort human-readable owner per path, for the refusal message only.

    Never decides anything. If the attribution tool is missing or fails, every
    path maps to a plain 'unattributed' and the refusal still fires — a
    reporting aid that breaks must not be able to open the gate.
    """
    tool = repository / "ops" / "worktree-attribution.py"
    if not paths or not tool.exists():
        return {p: "unattributed" for p in paths}
    try:
        result = subprocess.run(
            ["python3", str(tool), *paths],
            capture_output=True, text=True, cwd=str(repository), timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return {p: "unattributed" for p in paths}
    owners: dict[str, str] = {p: "unattributed" for p in paths}
    current: str | None = None
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped in owners:
            current = stripped
        elif current and stripped:
            owners[current] = stripped
            current = None
    return owners


def build_restore_set(repository: Path, pin: str,
                      allowed: Iterable[str]) -> list[dict[str, str]]:
    """The restore set, or a refusal naming every unapproved dirty path.

    `allowed` is the operator's explicit list of paths this settlement is
    entitled to restore. An allowed path that is not currently dirty is simply
    absent from the result — there is nothing to restore — which keeps a stale
    allow-list from inventing work.
    """
    allow = set(allowed)
    candidates = dirty_paths(repository)
    unapproved = [p for p in candidates if p not in allow]
    if unapproved:
        owners = attribute(repository, unapproved)
        lines = "\n".join(f"    {p}\n        {owners.get(p, 'unattributed')}"
                          for p in unapproved)
        raise RestoreSetRefusal(
            f"{len(unapproved)} dirty path(s) are not on the approved restore "
            f"allow-list, so this settlement will not be authored:\n{lines}\n"
            "  A dirty path in the shared checkout means someone is mid-edit. "
            "Restoring it to the pinned blob would destroy that work. Either "
            "wait for a quiet tree, or approve each path deliberately."
        )
    restored: list[dict[str, str]] = []
    for path in candidates:
        blob = _git_stdout(repository, "rev-parse", f"{pin}:{path}").strip()
        if blob:
            restored.append({"path": path, "blob_oid": blob})
    return restored
