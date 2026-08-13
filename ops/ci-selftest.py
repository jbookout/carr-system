#!/usr/bin/env python3
"""ci-selftest.py — proves ops/ci.sh actually runs, refuses, and reports honestly.

WHY THIS EXISTS, and why it is embarrassing that it did not exist first. Every
other gate in this repo has a selftest. ops/ci.sh — the script that gates all the
others — shipped with none, and inside one afternoon it accumulated four defects
that were each found by RUNNING it rather than by testing it:

  1. ${STRICT:+--strict} expanded on STRICT=0, because :+ fires on any non-empty
     value and "0" is non-empty. --strict leaked into every local run.
  2. `declare -A` is bash 4; macOS ships bash 3.2.57, so the script died on line
     64 before running a single check.
  3. Under zsh, `for c in $CLASS_ORDER` does not word-split, so the loop ran ONCE
     with c set to the whole list, every check_$c was an unknown command, and the
     script still reached its success line and printed "CI passed — every class
     green". EIGHT CLASSES REPORTED GREEN, ZERO EXECUTED. Caught by a peer
     session, not by me.
  4. core.fileMode is false in this repo, so chmod +x never reached the index.
     ci.sh was 100644 in git, and CI died with exit 126 — found but not
     executable.

Case 3 is the one that matters most and the reason this file leads with it. A
promotion gate that reports green having run nothing is worse than no gate: it
converts "unverified" into "verified" silently, and every downstream claim
inherits the lie. Defects 1, 2 and 4 make the script fail loudly, which is
survivable. Defect 3 makes it succeed falsely.

The rule this file is the answer to (Joe, 2026-08-13, from Addy Osmani): write
the tests first, then the code that makes them pass. Applied late here, on
purpose, to the exact artifact that proved why.

    .venv/bin/python ops/ci-selftest.py     # exit 0 = all pass
"""

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
CI = REPO / "ops" / "ci.sh"

# Assembled at runtime so this source file does not itself contain a
# credential-shaped literal. The scanner reads tracked files, and this IS one —
# a fixture that trips the gate it tests is a false positive forever.
SEED_DSN = "DATABASE_URL=" + "postgres://prod:" + "hunter2hunter2" + "@ep-x.neon.tech/carr"

RESULTS = []


def check(label, ok, detail=""):
    RESULTS.append((label, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail and not ok else ""))


ANSI = re.compile(r"\x1b\[[0-9;]*m")


def run(args, env=None, shell_cmd=None, timeout=600):
    e = dict(os.environ)
    e.pop("CARR_CI_DATABASE_URL", None)
    if env:
        e.update(env)
    cmd = shell_cmd if shell_cmd else [str(CI), *args]
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO, env=e, timeout=timeout)
    # Strip ANSI. ci.sh colours its verdicts, and an un-stripped regex looking
    # for "OK  artifact" never matches "\x1b[32mOK\x1b[0m    artifact" — which
    # made this file report the catastrophe case as failing when it was fine.
    return p.returncode, ANSI.sub("", (p.stdout or "") + (p.stderr or ""))


# ---------------------------------------------------------------- 1. the big one
def test_no_green_without_running():
    """The catastrophe case. Invoked through OTHER shells, ci.sh must never claim
    success while executing nothing. It re-execs itself under bash for exactly
    this reason; if that guard is ever removed, this fails."""
    for sh in ("zsh", "sh", "bash"):
        exe = shutil.which(sh)
        if not exe:
            continue
        rc, out = run([], shell_cmd=[exe, str(CI), "--only", "artifact"])
        claims_green = "CI passed" in out
        actually_ran = re.search(r"(OK|FAIL|SKIP)\s+artifact", out) is not None
        check(f"{sh}: never reports green without running a class",
              not (claims_green and not actually_ran),
              f"claimed green={claims_green} ran={actually_ran}")


def test_class_table_is_complete():
    """Every class in CLASS_ORDER needs a check_ function AND a description. A
    class listed but not implemented would silently never run — the same failure
    as case 3, arriving by a different door."""
    src = CI.read_text()
    order = re.search(r'^CLASS_ORDER="([^"]+)"', src, re.M)
    check("CLASS_ORDER is declared", order is not None)
    if not order:
        return
    classes = order.group(1).split()
    listed = set(re.findall(r"^\s+(\w+)\)\s+echo\s+\"", src, re.M))
    for c in classes:
        check(f"class '{c}' has a check_ function",
              re.search(rf"^check_{c}\(\)", src, re.M) is not None)
        check(f"class '{c}' has a description", c in listed)


# ---------------------------------------------------------------- 2. skip vs pass
def test_strict_turns_skip_into_failure():
    rc_loose, out_loose = run(["--only", "migration"])
    rc_strict, out_strict = run(["--strict", "--only", "migration"])
    check("a SKIP alone passes without --strict", rc_loose == 0 and "SKIP" in out_loose,
          f"rc={rc_loose}")
    check("the same SKIP fails under --strict", rc_strict == 1 and "SKIP" in out_strict,
          f"rc={rc_strict}")
    check("--strict failure explains why a skip counts",
          "stopped running" in out_strict.lower() or "skipped" in out_strict.lower())


def test_unknown_class_refuses():
    rc, out = run(["--only", "definitely-not-a-class"])
    check("an unknown class name exits 64, not 0", rc == 64, f"rc={rc}")


# ---------------------------------------------------------------- 3. safety
def test_migration_refuses_non_loopback():
    """The hard safety property. This check applies 130 forward migrations; if it
    ever accepts a remote DSN it would apply them to whatever it was pointed at.
    There is deliberately no override flag, so there is nothing to test around."""
    for dsn, label in [
        ("postgres://u:p@ep-steep-field.us-east-2.aws.neon.tech/carr", "a Neon host"),
        ("postgres://u:p@10.0.0.5:5432/carr", "a private IP"),
        ("postgres://u:p@db.internal:5432/carr", "an internal hostname"),
    ]:
        rc, out = run(["--only", "migration"], env={"CARR_CI_DATABASE_URL": dsn})
        check(f"migration refuses {label}", rc != 0 and "REFUSED" in out, f"rc={rc}")

    rc, out = run(["--only", "migration"],
                  env={"CARR_CI_DATABASE_URL": "postgres://u:p@localhost:5432/x"})
    check("migration ACCEPTS loopback (fails on no server, not on the guard)",
          "REFUSED" not in out)


# ---------------------------------------------------------------- 4. the exec bit
def test_tracked_scripts_are_executable_in_git():
    """core.fileMode is false in this repo, so chmod on disk never reaches the
    index. A script can be executable locally and 100644 in git, which is exactly
    how CI died with exit 126. Check the INDEX, not the filesystem — the
    filesystem is the thing that lies here."""
    out = subprocess.run(["git", "ls-files", "-s"], cwd=REPO,
                         capture_output=True, text=True, check=True).stdout
    modes = {}
    for line in out.splitlines():
        mode, _, rest = line.partition(" ")
        modes[rest.split("\t", 1)[-1]] = mode

    # FAIL only on what is INVOKED AS A COMMAND. A shebang is not by itself a
    # requirement to be executable: bin/council-lib.sh is sourced and
    # fill-engine/fill_document.py is imported, and neither needs the bit. The
    # defect that killed CI was narrower than "has a shebang" — it was "something
    # runs this by path and the bit is missing".
    entry_points = [
        "ops/ci.sh", "ops/ci-selftest.py", "ops/ci-secret-scan.py",
        "ops/ci-dep-check.py", "ops/verb-count.sh", "ops/githooks/pre-push",
        "bin/deploy-worker.sh", "run.sh",
    ]
    missing = [p for p in entry_points
               if p in modes and modes[p] != "100755"]
    check("every CI entry point is executable in the index",
          not missing, f"non-executable: {', '.join(missing)}")

    # REPORT the wider pattern without failing on it. Silence here would hide a
    # real question (which of these are meant to be run directly?); failing would
    # assert an answer this test cannot actually determine.
    others = []
    for path, mode in modes.items():
        if mode != "100644" or path in entry_points:
            continue
        if any(p in path for p in ("node_modules", "vendor", ".venv", ".claude/worktrees")):
            continue
        try:
            with open(REPO / path, "rb") as fh:
                if fh.read(2) == b"#!":
                    others.append(path)
        except OSError:
            continue
    if others:
        print(f"        note: {len(others)} other tracked file(s) carry a shebang but are "
              f"not executable in the index. Not a failure — most are sourced or imported. "
              f"Worth a decision if any is meant to be run directly: {', '.join(sorted(others)[:5])}"
              f"{' …' if len(others) > 5 else ''}")


# ---------------------------------------------------------------- 5. the scanners
def test_secret_scanner_catches_and_respects_allow():
    scan = [sys.executable, str(REPO / "ops" / "ci-secret-scan.py")]
    rc, _ = subprocess.run(scan, cwd=REPO, capture_output=True, text=True).returncode, None
    check("the tree is currently clean of shaped credentials", rc == 0, f"rc={rc}")

    seeded = REPO / "_ci_selftest_seed.env"
    try:
        seeded.write_text(SEED_DSN + "\n")
        subprocess.run(["git", "add", "-N", str(seeded)], cwd=REPO, capture_output=True)
        p = subprocess.run(scan, cwd=REPO, capture_output=True, text=True)
        check("a seeded credential is caught", p.returncode == 1)
        check("the finding never prints the credential value",
              "hunter2" + "hunter2" not in (p.stdout + p.stderr))

        seeded.write_text(SEED_DSN + "  # ci-secret-scan" + ": allow — selftest fixture\n")
        p = subprocess.run(scan, cwd=REPO, capture_output=True, text=True)
        check("an inline allow marker on the same line suppresses it", p.returncode == 0)
    finally:
        subprocess.run(["git", "rm", "--cached", "-q", "--force", str(seeded)],
                       cwd=REPO, capture_output=True)
        seeded.unlink(missing_ok=True)


def test_dep_check_detects_a_stale_lock():
    req = REPO / "requirements.txt"
    original = req.read_text()
    dep = [sys.executable, str(REPO / "ops" / "ci-dep-check.py")]
    try:
        p = subprocess.run(dep, cwd=REPO, capture_output=True, text=True)
        check("dependency check passes on the committed tree", p.returncode == 0)

        req.write_text(original + "\nsome-package-that-is-not-locked>=1.0\n")
        p = subprocess.run(dep, cwd=REPO, capture_output=True, text=True)
        check("a requirements.txt edit makes the lock STALE",
              p.returncode == 1 and "STALE" in (p.stdout + p.stderr))

        req.write_text(original + "\n# a comment-only edit\n")
        p = subprocess.run(dep, cwd=REPO, capture_output=True, text=True)
        check("a comment-only edit does NOT invalidate the lock", p.returncode == 0)
    finally:
        req.write_text(original)


def test_lock_is_not_platform_specific():
    """pip freeze drops environment markers, which made the lock Mac-only and
    killed the first CI run on pyobjc. Anything darwin-only must carry its
    marker."""
    lock = (REPO / "requirements.lock").read_text()
    bad = [l for l in lock.splitlines()
           if l.strip() and not l.startswith("#")
           and l.split("==")[0].lower().startswith("pyobjc")
           and "sys_platform" not in l]
    check("darwin-only packages in the lock carry a sys_platform marker",
          not bad, f"bare: {bad[:3]}")


def test_migration_filenames_match_the_runner():
    """Every migration must satisfy tools/migrate.py's own NAME_RE, and the
    ordered-insert file must sort where it claims to. CI rejected 0013a with
    'bad migration filename' AFTER it was written and pushed — a filename
    contract that is only enforced on a live database is one you find out about
    from a red runner rather than from a check."""
    src = (REPO / "tools" / "migrate.py").read_text()
    m = re.search(r'NAME_RE = re\.compile\(r"([^"]+)"\)', src)
    check("migrate.py's NAME_RE is readable from source", m is not None)
    if not m:
        return
    rx = re.compile(m.group(1))
    names = sorted(p.name for p in (REPO / "migrations").iterdir()
                   if p.suffix == ".sql")
    bad = [n for n in names if not rx.match(n)]
    check("every migration filename matches the runner's contract",
          not bad, f"rejected: {', '.join(bad[:4])}")

    # Ordering is the whole point of an inserted migration; assert it rather
    # than trusting the ASCII reasoning in the file's own header comment.
    inserted = [n for n in names if re.match(r"^\d{4}[a-z]_", n)]
    for n in inserted:
        stem = n[:4]
        nxt = f"{int(stem) + 1:04d}"
        before = [x for x in names if x.startswith(stem + "_")]
        after = [x for x in names if x.startswith(nxt + "_")]
        if before and after:
            i, b, a = names.index(n), names.index(before[0]), names.index(after[0])
            check(f"{n} sorts between {before[0]} and {after[0]}", b < i < a)


def main():
    for fn in (test_no_green_without_running,
               test_class_table_is_complete,
               test_strict_turns_skip_into_failure,
               test_unknown_class_refuses,
               test_migration_refuses_non_loopback,
               test_tracked_scripts_are_executable_in_git,
               test_secret_scanner_catches_and_respects_allow,
               test_dep_check_detects_a_stale_lock,
               test_lock_is_not_platform_specific,
               test_migration_filenames_match_the_runner):
        try:
            fn()
        except Exception as exc:  # a crashing case is a failing case, never a silent skip
            check(f"{fn.__name__} raised", False, repr(exc))

    failed = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\nci-selftest: {len(RESULTS) - failed}/{len(RESULTS)} passed")
    if failed:
        print("FAILED:")
        for label, ok, detail in RESULTS:
            if not ok:
                print(f"  - {label}  {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
