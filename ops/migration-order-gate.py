#!/usr/bin/env python3
"""migration-order-gate.py — a change may only add migrations ABOVE the base.

THE RULE. Every migration file this change adds must carry a number strictly
greater than the highest migration number already on the base branch at the
moment the check runs. A branch that minted 0783 when main stood at 0782, and
then fell behind a main that merged 0785..0787, fails here until it is
renumbered above main's new maximum.

WHY, measured. The release pipeline applies migrations to the staging candidate
and to production in filename order, and both then require the source ledger to
EXTEND what they applied: tools/staging-project-replacement.py refuses with
"partial candidate ledger is not an exact source prefix", and tools/migrate.py
refuses a reordered ledger in production. A number below an applied one can
never apply cleanly again. It happened three times in two days — 0757 (fixed by
#1464), the 0769/0770 pair (#1500) and 0783 (#1498, renumbered to 0800) — and
each time every worker release stopped at staging-prepare until a human noticed
and renumbered by hand. The reservation ledger (tools/reserve-migration.py)
prevents two branches minting the SAME number; nothing stopped a branch merging
a number main had already moved past. This is that check.

WHY THE BASE BRANCH AND NOT THE APPLIED LEDGER. CI has no production credential
by construction, and db/schema.sql's committed ledger lags production by
hundreds of migrations, so neither can answer "what is applied". Main's tree is
an upper bound on what any release can have applied — the pipeline only ever
releases main — so "above main's maximum" implies "above anything applied".

WHERE IT FIRES. ops/ci.sh's gates class, which runs in the required
`ops/ci.sh --strict` context on every pull_request event. Strict status checks
are on (ruleset 20824501), so when main moves, a PR must be brought up to date
before it can merge; the merge queue's update-branch call pushes a merge commit
to the PR branch, which raises a `synchronize` event, which re-runs this gate
against the NEW main. A PR that fell behind therefore turns red at exactly the
moment it would otherwise have merged a stale number.

WHAT COUNTS AS ADDED: migration filenames present in HEAD's tree and absent
from the base tree. That is a set difference of trees, not a diff range, so it
is exact on a merge ref, on an updated branch, and on a branch that is merely
behind (main's own files are in both trees and never count). A rename counts
as the new name, which is how a fix forward passes.

Exit 0 clean · 1 an added migration is not above the base maximum · 2 the base
could not be read, which is not a pass.

  ops/migration-order-gate.py                       # base = origin/$GITHUB_BASE_REF or origin/main
  ops/migration-order-gate.py --base <ref> [--head <ref>]
"""
# doctrine: scripted-release-pipeline
from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys
from typing import Iterable, Mapping

REPO = pathlib.Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = "migrations"
MIGRATION_RE = re.compile(r"^migrations/(\d{4})_[a-z0-9_]+\.sql$")


def number(path: str) -> int | None:
    m = MIGRATION_RE.match(path)
    return int(m.group(1)) if m else None


def highest_number(paths: Iterable[str]) -> int | None:
    nums = [n for n in (number(p) for p in paths) if n is not None]
    return max(nums) if nums else None


def violations(base_paths: set[str], head_paths: set[str]) -> list[tuple[str, int]]:
    """Added migrations whose number is not strictly above the base maximum."""
    ceiling = highest_number(base_paths)
    if ceiling is None:
        return []
    added = sorted(p for p in head_paths - base_paths if number(p) is not None)
    return [(p, ceiling) for p in added if (number(p) or 0) <= ceiling]


def default_base(environ: Mapping[str, str]) -> str:
    ref = (environ.get("GITHUB_BASE_REF") or "").strip()
    return f"origin/{ref}" if ref else "origin/main"


def tree_paths(repo: pathlib.Path, ref: str) -> set[str]:
    out = subprocess.run(
        ["git", "ls-tree", "--name-only", f"{ref}:{MIGRATIONS_DIR}"],
        cwd=repo, capture_output=True, text=True, check=True).stdout
    return {f"{MIGRATIONS_DIR}/{line}" for line in out.splitlines() if line}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default=None)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--repo", default=str(REPO))
    args = parser.parse_args(argv)
    repo = pathlib.Path(args.repo)
    base = args.base or default_base(os.environ)

    try:
        base_paths = tree_paths(repo, base)
    except subprocess.CalledProcessError as exc:
        print(f"migration-order-gate: cannot read migrations/ at base {base!r}: "
              f"{(exc.stderr or '').strip()}", file=sys.stderr)
        print("  This is not a pass. Fetch the base (git fetch origin) and re-run.",
              file=sys.stderr)
        return 2
    try:
        head_paths = tree_paths(repo, args.head)
    except subprocess.CalledProcessError as exc:
        print(f"migration-order-gate: cannot read migrations/ at {args.head!r}: "
              f"{(exc.stderr or '').strip()}", file=sys.stderr)
        return 2

    bad = violations(base_paths, head_paths)
    ceiling = highest_number(base_paths)
    if not bad:
        added = len([p for p in head_paths - base_paths if number(p) is not None])
        print(f"migration-order-gate: OK — {added} added migration(s), all above "
              f"{base}'s highest ({ceiling:04d})" if ceiling is not None
              else "migration-order-gate: OK — base has no migrations")
        return 0

    print(f"migration-order-gate: REFUSED — {len(bad)} added migration(s) not above "
          f"{base}'s highest number {ceiling:04d}:", file=sys.stderr)
    for path, _ in bad:
        print(f"  {path}", file=sys.stderr)
    print(
        "  The release pipeline applies migrations in filename order and requires the\n"
        "  source ledger to extend what staging and production already applied; a\n"
        "  number at or below main's maximum can never apply in order.\n"
        "  Fix: ./run.sh reserve-migration --name <slug>, then git mv each file to the\n"
        "  reserved number, update every reference to the old filename (tests, SCAC\n"
        "  seals, tools/migrate.py atomic groups), and push. Renumbering is safe only\n"
        "  while the file has applied nowhere — this gate exists so that stays true.",
        file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
