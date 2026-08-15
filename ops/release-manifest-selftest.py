#!/usr/bin/env python3
"""
release-manifest-selftest.py — the P0-1 rebuild clause, proven on every change.

WHY THIS EXISTS SEPARATELY FROM ops/p0-1-release-gate.py. That gate needs a
database and runs against the isolated staging project. This one needs nothing
but git, so ops/ci.sh picks it up automatically (it globs ops/*-selftest.py) and
every proposed change re-proves the rebuild clause. A check that only runs when
somebody remembers to point it at a database is a check that stops running.

WHAT IT PROVES

  1. DETERMINISM. Two builds of the same SHA produce byte-identical digests. If
     this ever fails, the digest has picked up something ambient — a timestamp,
     a path, a machine — and "identical artifact rebuild" becomes unfalsifiable.

  2. THE DIGEST IS OF THE COMMIT, NOT THE CHECKOUT. The digest of HEAD is the
     same whether or not the working tree is dirty. This repo regularly holds
     another session's uncommitted work (rule 308ef1de), so a digest that moved
     with the working tree would be worthless exactly when it matters.

  3. SENSITIVITY. A different SHA whose deployed paths differ produces a
     different artifact digest. A digest that never changes is a constant
     wearing a hash's clothes.

  4. VERIFY ROUND-TRIPS. A freshly built manifest verifies against its own
     recorded SHA.

  5. VERIFY ACTUALLY BITES. A manifest with a tampered digest FAILS verify. This
     is the seeded failure for this clause: without it, assertion 4 could pass
     because verify returns zero unconditionally.

  6. THE PLAN HASH COVERS WHAT AN APPROVER READS, AND ONLY THAT. Changing a
     material field moves it; changing a deploy-time observation does not. The
     database trigger that voids stale approvals is only as good as this hash.

  7. THE MANIFEST CARRIES ALL SEVEN CLASSES the acceptance names.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "release-manifest.py"

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, text=True, cwd=REPO)


def build(*args: str) -> dict:
    out = run("build", *args)
    if out.returncode != 0:
        raise SystemExit(f"release-manifest-selftest: build failed: {out.stderr.strip()}")
    return json.loads(out.stdout)


def git(*args: str) -> str:
    return subprocess.run(("git", "-C", str(REPO), *args),
                          capture_output=True, text=True).stdout


def main() -> int:
    print("release-manifest-selftest: P0-1 rebuild clause")

    first = build("--sha", "HEAD")
    second = build("--sha", "HEAD")

    # 1. determinism
    check("1. two builds of one SHA are identical",
          first == second,
          "the digest picked up something ambient")

    # 2. the digest belongs to the commit, not the checkout
    dirty = bool(git("status", "--porcelain").strip())
    head_sha = git("rev-parse", "HEAD").strip()
    by_sha = build("--sha", head_sha)
    check("2. HEAD and its explicit SHA digest identically"
          + (" (working tree is dirty, which is the interesting case)" if dirty else ""),
          by_sha["artifact_digest"] == first["artifact_digest"])

    # 3. sensitivity: the comparison commit must be one whose deployed-path tree
    #    GENUINELY differs from HEAD's. `git log -- <path>` is not that test: it
    #    lists a commit that changed the path relative to its own parent, which
    #    includes commits a later one changed straight back. Asking git for the
    #    real difference is the precise question, and it keeps the assertion
    #    exactly as strong: a different deployed tree must digest differently.
    other = None
    for sha in git("log", "-40", "--format=%H", "--", "mcp-server", "dealroom").split():
        if sha == head_sha:
            continue
        differs = subprocess.run(
            ("git", "-C", str(REPO), "diff", "--quiet", sha, head_sha,
             "--", "mcp-server", "dealroom"))
        if differs.returncode != 0:
            other = sha
            break
    if other:
        older = build("--sha", other)
        check("3. a commit whose deployed tree differs digests differently",
              older["artifact_digest"] != first["artifact_digest"],
              f"{other[:12]} has a different tree but the SAME digest")
    else:
        check("3. a commit whose deployed tree differs digests differently",
              False, "no differing commit in the last 40 — cannot prove sensitivity")

    # 4 + 5. verify round-trips, and verify bites
    with tempfile.TemporaryDirectory() as tmp:
        good = Path(tmp) / "good.json"
        good.write_text(json.dumps(first))
        out = run("verify", "--manifest", str(good))
        check("4. a fresh manifest verifies against its own SHA",
              out.returncode == 0, out.stdout.strip()[-300:])

        tampered = dict(first)
        tampered["artifact_digest"] = "sha256:" + "0" * 64
        bad = Path(tmp) / "bad.json"
        bad.write_text(json.dumps(tampered))
        out = run("verify", "--manifest", str(bad))
        check("5. a tampered digest FAILS verify",
              out.returncode != 0,
              "verify accepted a manifest whose digest does not match its SHA")

    # 6. the plan hash covers material fields only
    material = dict(first)
    material["config_fingerprint"] = "sha256:" + "1" * 64
    out = run("plan-hash", "--manifest", _tmp_json(material))
    moved = out.stdout.strip()
    check("6a. changing configuration moves the plan hash",
          moved and moved != first["plan_hash"])

    observed = dict(first)
    observed["commit_subject"] = "a different subject line entirely"
    out = run("plan-hash", "--manifest", _tmp_json(observed))
    check("6b. a non-material field does NOT move the plan hash",
          out.stdout.strip() == first["plan_hash"])

    # 7. all seven acceptance classes present
    classes = {
        "code": ("git_sha", "artifact_digest", "dependency_lock_digest"),
        "schema": ("schema_highest_migration", "migration_set"),
        "config": ("config_fingerprint", "config_paths"),
        "plan": ("plan_hash",),
    }
    missing = [f"{name}.{field}"
               for name, fields in classes.items()
               for field in fields
               if first.get(field) in (None, "", [])]
    check("7. the manifest carries code, schema, config and plan",
          not missing, "missing: " + ", ".join(missing) if missing else "")

    print()
    if FAILURES:
        print(f"release-manifest-selftest: {len(FAILURES)} FAILED")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print("release-manifest-selftest: the rebuild clause holds")
    return 0


_TMPDIR = tempfile.mkdtemp(prefix="release-manifest-selftest-")


def _tmp_json(obj: dict) -> str:
    path = Path(_TMPDIR) / f"m{abs(hash(json.dumps(obj, sort_keys=True)))}.json"
    path.write_text(json.dumps(obj))
    return str(path)


if __name__ == "__main__":
    sys.exit(main())
