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

import atexit
import contextlib
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import scrubbed_env  # noqa: E402

# WHY scrubbed_env AND NOT fixture_env. The git calls below act on REPO ON
# PURPOSE — this file seeds a real defect into the real tree to prove CI catches
# it. What it must never do is act on somewhere ELSE: GIT_DIR outranks cwd and
# every git hook exports it, and ops/githooks/pre-push runs ops/ci.sh which runs
# this file. Without the scrub, `git add -N` and `git rm --cached` below would
# stage and unstage in whatever repository invoked the push. scrubbed_env keeps
# the caller's identity, which a real repo operation wants. See ops/git_env.py.
# Loop #371.
CI = REPO / "ops" / "ci.sh"

# Assembled at runtime so this source file does not itself contain a
# credential-shaped literal. The scanner reads tracked files, and this IS one —
# a fixture that trips the gate it tests is a false positive forever.
SEED_DSN = "DATABASE_URL=" + "postgres://prod:" + "hunter2hunter2" + "@ep-x.neon.tech/carr"

RESULTS: list[tuple] = []


def check(label, ok, detail=""):
    RESULTS.append((label, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail and not ok else ""))


ANSI = re.compile(r"\x1b\[[0-9;]*m")


# ------------------------------------------------------ accepted machine state
# THE ONE ACCEPTED STATE in which the types class cannot be exercised at all.
# requirements.txt pins `mypy>=2.3; python_version >= "3.10"`, so on an older
# runtime mypy is not merely missing — it is deliberately not installable.
# Dell's Mac ships the Command Line Tools' Python 3.9.6 and has no package
# manager, which is a standing machine state, not a transient breakage.
#
# Named as an exact constant, and printed on the passing line, because a
# permanently chosen machine state must never read as a permanent failure and
# must never be normalised by habitually passing CARR_SKIP_CI on every push.
# The acceptance is deliberately narrow: on 3.10 or newer a missing mypy is a
# real defect, the seeded-error assertions below run unchanged, and
# test_mypy_pin_acceptance_is_narrow proves that boundary still bites.
MYPY_PIN_MIN_PYTHON = (3, 10)


def type_check_interpreter_version():
    """The Python bin/type-check.sh would actually use, not the one running us.

    That script prefers $REPO/.venv/bin/mypy and falls back to mypy on PATH, so
    the venv's interpreter is what decides whether mypy can exist at all.
    Reading our own sys.version_info would be wrong the moment the selftest and
    the venv differ, which is exactly the case on a machine whose venv was built
    from a different python than the one invoking this file.
    """
    venv_python = REPO / ".venv" / "bin" / "python"
    if venv_python.exists():
        probe = subprocess.run(
            [str(venv_python), "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
            capture_output=True, text=True)
        parts = probe.stdout.split()
        if probe.returncode == 0 and len(parts) >= 2:
            return (int(parts[0]), int(parts[1]))
    return sys.version_info[:2]


def mypy_pin_excludes_this_machine():
    return type_check_interpreter_version() < MYPY_PIN_MIN_PYTHON


# ---------------------------------------------------------------- seed safety
# THIS FILE SEEDS REAL DAMAGE INTO THE LIVE WORKING TREE — that is the point of
# it, because a check is only proven by making it fail. The danger is what
# happens if the process does not reach its own cleanup line.
#
# It already did happen, on 2026-08-13 (loop #368). A seeded defect was left
# behind — the `state-as-of` verb missing from mcp-server/src/tools.js, 46
# deletions in one hunk — and the marker file that would have said so had
# already been removed. ops/ci.sh --only artifact then failed with "would REMOVE
# 1 verb(s): deployed 105, tree has 104", and because the pre-push hook runs the
# full ci.sh, that blocked EVERY session on this machine from pushing anything,
# for a reason unrelated to their own work. The failure names a verb deletion,
# so the natural first read is that a person deleted a verb on purpose.
#
# WHY try/finally IS NOT ENOUGH, which is what this file relied on before.
# `finally` runs on an exception and on a normal exit. It does NOT run on
# SIGKILL, on a machine losing power, or when a parent harness kills the process
# group on timeout — and a CI selftest is exactly the kind of long job something
# else kills. A harness that seeds real damage and relies on reaching its own
# cleanup line is one crash away from doing this again.
#
# WHAT REPLACES IT. The original bytes are written to a JOURNAL BEFORE anything
# is modified, and the journal is removed only after a successful restore. So
# the damage is never un-recorded: either the journal is absent (nothing is
# seeded) or it names every path and holds its original content. Recovery then
# needs no memory of what the run was doing.
#
#   * seeded_paths() restores on any ordinary exit path — normal, exception,
#     SIGINT, SIGTERM — via finally plus atexit plus signal handlers.
#   * On a kill that outruns all of those, the journal survives on disk, and the
#     NEXT run finds it, restores every path from it, and REFUSES to start.
#     Refusing matters: a run that silently repaired and continued would hide a
#     crash that has already cost this machine a full push outage once.
SEED_JOURNAL = REPO / "_ci_selftest_seed_journal.json"


def _restore_from_journal(journal_data):
    """Write every recorded path back to its original bytes. Returns the paths."""
    restored = []
    for rel, original in (journal_data.get("paths") or {}).items():
        target = REPO / rel
        if original is None:
            # Path did not exist before the seed: the seed created it, so the
            # restore is removal, not a write of the string "None".
            if target.exists():
                target.unlink()
                restored.append(rel)
            continue
        if not target.exists() or target.read_text() != original:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(original)
            restored.append(rel)
    return restored


def _recover_stale_journal():
    """Called at import. A journal on disk means a previous run was killed."""
    if not SEED_JOURNAL.exists():
        return
    try:
        data = json.loads(SEED_JOURNAL.read_text())
    except (OSError, ValueError) as exc:
        print(f"FATAL: {SEED_JOURNAL.name} exists but is unreadable ({exc}).\n"
              f"  A previous run seeded real damage into this tree and was killed before\n"
              f"  restoring it. The journal is the only record of what was changed, so it\n"
              f"  cannot be repaired automatically. Inspect the file and the tree by hand.",
              file=sys.stderr)
        sys.exit(1)
    restored = _restore_from_journal(data)
    SEED_JOURNAL.unlink(missing_ok=True)
    # THE SHAPE OF THIS OUTPUT IS LOAD-BEARING, not decoration. ops/ci.sh's
    # gates class runs each suite quietly and, on failure, prints only
    # `tail -12` of its log. So a recovery and a genuinely broken check reach
    # the terminal looking identical, and telling them apart is the difference
    # between a thirty-second re-run and another evening like 2026-08-13. The
    # banner is repeated at the END as well as the start, because the tail is
    # what gets shown, and the last line is the ACTION rather than the diagnosis.
    bar = "=" * 68
    for line in (bar, "NOT A TEST FAILURE — a stale seed was recovered.", bar):
        print(line, file=sys.stderr)
    print("A previous run was killed while a seeded defect was live in the working",
          file=sys.stderr)
    print("tree. Every path it recorded has been restored from the journal:",
          file=sys.stderr)
    for rel in restored:
        print(f"    restored  {rel}", file=sys.stderr)
    if not restored:
        print("    (every recorded path was already correct — nothing to undo)",
              file=sys.stderr)
    print("\nRefusing to start is deliberate: a silent repair would hide a crash that",
          file=sys.stderr)
    print("blocked every push on this machine for hours on 2026-08-13.", file=sys.stderr)
    for line in (bar, "NOTHING IS BROKEN. RE-RUN THIS SUITE TO PROCEED.", bar):
        print(line, file=sys.stderr)
    # 75 is EX_TEMPFAIL — a transient condition the caller should retry, and
    # distinct from 1. ci.sh treats any nonzero as a failed class, which stays
    # correct because the push must still be blocked; the code simply carries
    # the distinction for any caller that wants "retry me" rather than "a check
    # is broken".
    sys.exit(75)


@contextlib.contextmanager
def seeded_paths(*rels):
    """Seed real damage into the named repo-relative paths, safely.

    Records each path's original bytes to the journal BEFORE yielding, and
    restores on every exit path this process can still control. A path that does
    not exist yet is recorded as None so its restore is a deletion.
    """
    originals = {}
    for rel in rels:
        p = REPO / rel
        originals[rel] = p.read_text() if p.exists() else None
    SEED_JOURNAL.write_text(json.dumps(
        {"pid": os.getpid(), "paths": originals}, indent=2))

    done = {"restored": False}

    def restore(*_args):
        if done["restored"]:
            return
        done["restored"] = True
        _restore_from_journal({"paths": originals})
        SEED_JOURNAL.unlink(missing_ok=True)

    prev = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            prev[sig] = signal.getsignal(sig)
            signal.signal(sig, lambda s, f: (restore(), sys.exit(130)))
        except (ValueError, OSError):
            pass  # not on the main thread, or the platform refuses — finally still covers it
    atexit.register(restore)
    try:
        yield
    finally:
        restore()
        for sig, handler in prev.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass


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
    out = subprocess.run(["git", "ls-files", "-s"], cwd=REPO, env=scrubbed_env(),
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
    with seeded_paths("_ci_selftest_seed.env"):
        seeded.write_text(SEED_DSN + "\n")
        subprocess.run(["git", "add", "-N", str(seeded)], cwd=REPO, env=scrubbed_env(), capture_output=True)
        p = subprocess.run(scan, cwd=REPO, capture_output=True, text=True)
        check("a seeded credential is caught", p.returncode == 1)
        check("the finding never prints the credential value",
              "hunter2" + "hunter2" not in (p.stdout + p.stderr))

        seeded.write_text(SEED_DSN + "  # ci-secret-scan" + ": allow — selftest fixture\n")
        p = subprocess.run(scan, cwd=REPO, capture_output=True, text=True)
        check("an inline allow marker on the same line suppresses it", p.returncode == 0)
        # The index entry is this test's own doing and is not content, so it is
        # dropped here rather than by the journal, which restores file bytes.
        subprocess.run(["git", "rm", "--cached", "-q", "--force", str(seeded)],
                       cwd=REPO, env=scrubbed_env(), capture_output=True)


def test_dep_check_detects_a_stale_lock():
    req = REPO / "requirements.txt"
    original = req.read_text()
    dep = [sys.executable, str(REPO / "ops" / "ci-dep-check.py")]
    with seeded_paths("requirements.txt"):
        p = subprocess.run(dep, cwd=REPO, capture_output=True, text=True)
        check("dependency check passes on the committed tree", p.returncode == 0)

        req.write_text(original + "\nsome-package-that-is-not-locked>=1.0\n")
        p = subprocess.run(dep, cwd=REPO, capture_output=True, text=True)
        check("a requirements.txt edit makes the lock STALE",
              p.returncode == 1 and "STALE" in (p.stdout + p.stderr))

        req.write_text(original + "\n# a comment-only edit\n")
        p = subprocess.run(dep, cwd=REPO, capture_output=True, text=True)
        check("a comment-only edit does NOT invalidate the lock", p.returncode == 0)


def test_types_class_catches_a_seeded_type_error():
    """The `types` class must REFUSE a type error, not merely exist.

    The class landed in #60 and was proven to block BY HAND. No test was left
    behind, and this is the class that most needs one on its own evidence: #60's
    commit message records that its first attempt at that proof was a FALSE PASS.
    The seed was appended to tools/health-check.py, whose module body ends in
    sys.exit(rc); everything after a NoReturn call at module level is unreachable,
    mypy does not check unreachable code, and so the test measured dead code
    while reporting that the gate worked.

    That is exactly the failure this file leads with — a check reporting green
    having examined nothing — and a hand-run proof does not stop it returning on
    the next edit. Three PRs (#60, #65, #67) rewrote this class inside one hour,
    one of them silently dropping the exit-78 branch, which is the rate of change
    a permanent test is for.

    MODULE LEVEL IS LOAD-BEARING: mypy.ini sets check_untyped_defs = False, so an
    error inside an unannotated function body is not reported at all and this
    test would pass for the wrong reason a second time.
    """
    if mypy_pin_excludes_this_machine():
        found = type_check_interpreter_version()
        check(
            "types: ACCEPTED — Python %d.%d is below the mypy pin %d.%d, so mypy "
            "cannot be installed here and the seeded-error path cannot run"
            % (found[0], found[1], MYPY_PIN_MIN_PYTHON[0], MYPY_PIN_MIN_PYTHON[1]),
            True)
        rc, out = run(["--only", "types"])
        check("types still reports honestly on the accepted machine (skip, not pass)",
              rc == 0 and "SKIP" in out.upper(), f"rc={rc} out={out[-400:]}")
        return

    fixture = "tools/_ci_selftest_types_fixture.py"
    with seeded_paths(fixture):
        rc, out = run(["--only", "types"])
        check("types passes on the committed tree", rc == 0,
              f"rc={rc} out={out[-400:]}")

        (REPO / fixture).write_text(
            "# fixture written by ops/ci-selftest.py — removed on exit.\n"
            "# Module level on purpose: check_untyped_defs = False means an error\n"
            "# inside an unannotated function body would not be reported at all.\n"
            "x: int = 'not an int'\n")
        rc, out = run(["--only", "types"])
        check("a seeded type error makes the types class FAIL", rc == 1,
              f"rc={rc} out={out[-400:]}")
        check("the failing run names the types class",
              "types" in ANSI.sub("", out).lower(), f"out={out[-400:]}")

    rc, out = run(["--only", "types"])
    check("removing the seed turns the types class green again", rc == 0,
          f"rc={rc} out={out[-400:]}")


def test_type_check_script_resolves_mypy_in_both_homes():
    """bin/type-check.sh runs in two environments and must not fork.

    Joe's Mac has a .venv; the GitHub runner pip-installs requirements.lock into
    the system python and has none. The script prefers the venv and falls back to
    PATH.

    THE EXIT-78 BRANCH IS PINNED HERE BECAUSE IT WAS ALREADY LOST ONCE. Absent
    mypy exits 78 (EX_CONFIG), which ci.sh must read as SKIP — a skip that
    --strict then refuses in CI, so the check cannot go quietly missing while
    a machine without mypy is not told it has type errors. Two sessions built
    this class in parallel on 2026-08-14 and the one WITHOUT that branch merged
    second (#65 over #60), so a machine with no mypy read "mypy found shape
    mistakes": a false failure carrying a false explanation. #67 restored it.
    Nothing but a test stops the third rewrite dropping it again.
    """
    src = (REPO / "bin" / "type-check.sh").read_text()
    ci = CI.read_text()
    check("type-check.sh falls back to mypy on PATH", "command -v mypy" in src)
    check("absent mypy exits 78 (EX_CONFIG), not 0", "exit 78" in src)
    check("ci.sh still reads 78 as a SKIP rather than a type failure",
          re.search(r'-eq 78 \]', ci) is not None and
          re.search(r'skip types', ci) is not None)
    check("mypy is pinned in the lockfile so the runner has it",
          "mypy==" in (REPO / "requirements.lock").read_text())
    check("ci.sh's types class calls the script rather than mypy directly",
          "bin/type-check.sh" in ci)


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


def test_known_gaps_all_expire():
    """A known gap suppresses a red class's exit code while a ruling is pending.
    That is only safe because it expires. An entry with no expiry, or one dated
    so far out it never bites, is a permanent exemption wearing a temporary
    label — which is the thing this mechanism must not become."""
    import datetime, json as _json
    scope = REPO / "ops" / "config" / "ci-check-scope.json"
    if not scope.exists():
        return
    gaps = _json.loads(scope.read_text()).get("known_gaps", [])
    today = datetime.date.today()
    for g in gaps:
        name = g.get("class", "?")
        exp = g.get("expires")
        check(f"known gap '{name}' has an expiry date", bool(exp))
        if not exp:
            continue
        try:
            d = datetime.date.fromisoformat(exp)
        except ValueError:
            check(f"known gap '{name}' expiry parses as a date", False, exp)
            continue
        check(f"known gap '{name}' expires within 30 days",
              d <= today + datetime.timedelta(days=30),
              f"{exp} is {(d - today).days} days out")
        check(f"known gap '{name}' names the loop carrying the ruling",
              bool(g.get("loop")))
    if not gaps:
        check("no known gaps outstanding (nothing suppressed)", True)


def test_no_env_claims_a_production_hostname():
    """The 2026-08-13 incident, pinned. A staging deploy took over
    api.doctorcre.com, api.practicecre.com and dealroom.doctorcre.com because
    wrangler inherits `routes` and [env.staging] did not override it. Production
    answered from the empty staging database for about two minutes."""
    import subprocess as sp
    toml = REPO / "mcp-server" / "wrangler.toml"
    checker = REPO / "ops" / "deploy-attachment-check.py"
    if not toml.exists() or not checker.exists():
        return
    src = toml.read_text()
    envs = re.findall(r"^\[env\.([A-Za-z0-9_-]+)\]", src, re.M)
    for env in sorted(set(envs)):
        r = sp.run([sys.executable, str(checker), str(toml), env],
                   capture_output=True, text=True)
        check(f"env '{env}' claims no production hostname", r.returncode == 0,
              (r.stdout + r.stderr).strip()[:120])

    # And the guard must still REFUSE the exact config that caused the incident.
    import tempfile as tf
    broken = "\n".join(l for l in src.splitlines() if l.strip() != "routes = []")
    with tf.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
        fh.write(broken)
        path = fh.name
    try:
        r = sp.run([sys.executable, str(checker), path, "staging"],
                   capture_output=True, text=True)
        check("removing `routes = []` is REFUSED, not silently allowed",
              r.returncode == 1, f"rc={r.returncode}")
    finally:
        os.unlink(path)


def test_mypy_pin_acceptance_is_narrow():
    """The accepted state must not quietly widen into "mypy is optional".

    Rule bd4a6d22 requires that everything outside a named acceptance still
    fails, and that a test proves it. The danger here is drift: someone raises
    MYPY_PIN_MIN_PYTHON to silence a red types class on a NEWER machine, and the
    type gate stops binding everywhere at once with nothing to catch it.
    """
    check("the mypy pin boundary is exactly 3.10",
          MYPY_PIN_MIN_PYTHON == (3, 10), f"got {MYPY_PIN_MIN_PYTHON}")
    for ver in ((3, 7), (3, 8), (3, 9)):
        check(f"Python {ver[0]}.{ver[1]} is inside the acceptance",
              ver < MYPY_PIN_MIN_PYTHON, f"{ver}")
    for ver in ((3, 10), (3, 11), (3, 12), (3, 13), (4, 0)):
        check(f"Python {ver[0]}.{ver[1]} is OUTSIDE it, so a missing mypy still fails",
              not (ver < MYPY_PIN_MIN_PYTHON), f"{ver}")
    pin = (REPO / "requirements.txt").read_text(encoding="utf-8")
    check("requirements.txt still carries the pin this acceptance is derived from",
          'python_version >= "3.10"' in pin,
          "the constant and the pin must move together, or the acceptance is a guess")


def test_gates_treats_only_78_as_not_configured():
    """Exit 78 in the gates loop must mean "not configured", and nothing else.

    The loop used to count every nonzero alike, so a selftest that correctly
    declined for want of a local dependency read as a red gate, and the only way
    past it was CARR_SKIP_CI on every push. The risk in the fix is that it
    widens: if an ordinary crash ever
    skipped too, this class would go quiet exactly when it should shout.

    THIS TEST IS STRUCTURAL, AND THAT IS A DELIBERATE DOWNGRADE — say so rather
    than pretend otherwise. The behavioural version (seed a fixture that exits 78
    beside one that exits 1, run the gates class, assert only the second is
    named) cannot work here for two independent reasons, both measured
    2026-08-19: the gates loop globs ops/*-selftest.py, so it re-enters THIS file
    recursively and the nested run's crash-safety restores the outer run's seeded
    paths — the fixtures are deleted before the loop reaches them; and even if
    they survived, the nested re-entry runs the slowest class in ci.sh a second
    and third time, costing more on every push forever than the bug it guards.

    This structural check binds the narrowness of the exception without adding
    a recursively executing fixture to the slowest CI class.
    """
    ci = (REPO / "ops" / "ci.sh").read_text(encoding="utf-8")
    body = ci[ci.index("check_gates()"):]
    body = body[:body.index("\n}")]

    check("the gates loop skips on exactly 78, not a range",
          '[ "$grc" -eq 78 ]' in body,
          "an -ge/-ne form here would swallow real failures")
    check("every other nonzero still routes to the failure list",
          '[ "$grc" -ne 0 ]' in body and 'failures="$failures $base"' in body,
          "the else-branch must still record failures")
    check("the 78 skip announces itself rather than passing silently",
          "NOT CONFIGURED (exit 78)" in body,
          "a silent skip is how coverage disappears without anyone noticing")
    for bad in ('-ge 78', '-ne 0 ] && continue', '|| true'):
        check(f"the gates loop does not weaken with {bad!r}", bad not in body, bad)


def main():
    for fn in (test_no_green_without_running,
               test_class_table_is_complete,
               test_strict_turns_skip_into_failure,
               test_unknown_class_refuses,
               test_migration_refuses_non_loopback,
               test_tracked_scripts_are_executable_in_git,
               test_secret_scanner_catches_and_respects_allow,
               test_dep_check_detects_a_stale_lock,
               test_types_class_catches_a_seeded_type_error,
               test_type_check_script_resolves_mypy_in_both_homes,
               test_lock_is_not_platform_specific,
               test_migration_filenames_match_the_runner,
               test_known_gaps_all_expire,
               test_no_env_claims_a_production_hostname,
               test_mypy_pin_acceptance_is_narrow,
               test_gates_treats_only_78_as_not_configured):
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
    # BEFORE ANY TEST RUNS. A journal on disk means a previous run was killed
    # with seeded damage live in the tree; this restores it and refuses to
    # start. Deliberately not inside main(), so no future reordering of the
    # test list can end up running a test before the tree is known good.
    _recover_stale_journal()
    sys.exit(main())
