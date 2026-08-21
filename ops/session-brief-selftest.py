#!/usr/bin/env python3
"""Selftest for hooks/session-brief.py's nightly_verdict().

WHY THIS EXISTS. On 2026-08-14 the brief opened the session with five named
nightly failures — two golden-workflow assertions, the golden workflow suite,
schema snapshot drift, and the vault drift watch. Only the LAST of those was
still failing; the other four had been fixed hours earlier and their runs had
already gone green on the parts that mattered. The accumulator was deduped at
each run boundary but never reset, so the line reported the union of every
failure since the last fully green chain and read as one catastrophic run.

That is the same disease this function was written to cure. Its own docstring
says a line that prints every session is a line nobody reads; a line that names
four already-fixed failures is worse, because the one real failure hides among
them, and the session pays to rediscover which.

SECOND INCIDENT, 2026-08-21, same disease in loose_work(). The brief opened a
session with "1 tracked file(s) in ~/carr-system have sat uncommitted for 324h".
The only dirty tracked path was the git submodule tools/dictation-rig/vendor/quill,
whose actual edit was 29 hours old. getmtime() had been called straight on the
path git status printed, and for a gitlink that path is a DIRECTORY: a directory's
mtime does not move when a file nested inside it is edited, so the number was
frozen at the submodule's checkout date and could only ever climb. Worse, the
submodule was dirty by DESIGN — its diff is byte-identical to the committed patch
tools/dictation-rig/patches/0001-menubar-visible-recording.patch, which the build
applies — so the line was announcing a permanent steady state as if it were
someone's stranded work, which is precisely the "line nobody reads" failure.

Run: python3 ops/session-brief-selftest.py
"""
import importlib.util
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BRIEF = os.path.join(os.path.dirname(HERE), "hooks", "session-brief.py")

spec = importlib.util.spec_from_file_location("session_brief", BRIEF)
assert spec is not None and spec.loader is not None, f"cannot load {BRIEF}"
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)

CASES: list[tuple] = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def write_log(text):
    fd, path = tempfile.mkstemp(suffix=".log")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    return path


# One failing run, then a second run that failed on ONE different step. This is
# the real 2026-08-14 shape, reduced.
TWO_RUNS = """\
2026-08-14T13:08:00Z  START golden workflow suite
2026-08-14T13:08:17Z  FAIL  golden workflow suite (read verbs) (exit 1)
2026-08-14T13:08:23Z  ===== nightly chain FINISHED WITH FAILURES — see above =====
2026-08-14T13:36:34Z  FAIL  vault drift watch (check, first) (exit 2)
2026-08-14T13:39:27Z  START golden workflow suite
2026-08-14T13:43:27Z  OK    golden workflow suite (read verbs)
2026-08-14T13:43:34Z  ===== nightly chain FINISHED WITH FAILURES — see above =====
"""


@case("the verdict names only the LAST run's failures, not every failure since green")
def _(assert_):
    path = write_log(TWO_RUNS)
    out = sb.nightly_verdict(log=path)
    assert_("vault drift watch" in out,
            f"the last run's real failure must be named: {out!r}")
    assert_("golden workflow suite" not in out,
            f"a step that failed in an EARLIER run and passed in the last one must "
            f"not be reported as current: {out!r}")


@case("a clean final run says nothing at all")
def _(assert_):
    path = write_log(TWO_RUNS + "2026-08-14T14:10:00Z  ===== nightly chain OK =====\n")
    out = sb.nightly_verdict(log=path)
    assert_(out == "", f"a green chain must print nothing, got {out!r}")


@case("repeats within one run are still deduped")
def _(assert_):
    path = write_log(
        "2026-08-14T01:00:00Z  FAIL  vault drift watch (check, first) (exit 2)\n"
        "2026-08-14T01:05:00Z  FAIL  vault drift watch (check, first) (exit 2)\n"
        "2026-08-14T01:09:00Z  ===== nightly chain FINISHED WITH FAILURES =====\n")
    out = sb.nightly_verdict(log=path)
    assert_(out.count("vault drift watch") == 1,
            f"one step failing twice in a run should be named once: {out!r}")


@case("failures from a run still in flight are not attributed to the last finished one")
def _(assert_):
    # A chain that is running RIGHT NOW has emitted FAIL lines past the last
    # boundary marker. Those belong to a run that has not finished, so the
    # verdict still describes the last COMPLETED run.
    path = write_log(TWO_RUNS + "2026-08-14T14:20:00Z  FAIL  exports (7 targets) (exit 1)\n")
    out = sb.nightly_verdict(log=path)
    assert_("exports" not in out,
            f"an unfinished run's failure must not join the last verdict: {out!r}")


@case("a missing log is silent rather than fatal — the brief must never fail on this")
def _(assert_):
    out = sb.nightly_verdict(log="/nonexistent/nightly.log")
    assert_(out == "", f"a missing log should produce no line, got {out!r}")


# ── loose_work(): a tracked path can be a DIRECTORY (2026-08-21) ─────────────

def _git(cwd, *args):
    return subprocess.run(("git",) + args, cwd=cwd, capture_output=True,
                          text=True, timeout=30)


def _repo(path):
    """A throwaway git repo with an identity, so commits work on any machine."""
    os.makedirs(path, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "selftest@carr.local")
    _git(path, "config", "user.name", "selftest")
    _git(path, "config", "commit.gpgsign", "false")
    _git(path, "config", "protocol.file.allow", "always")
    return path


@case("newest mtime of a DIRECTORY comes from the files inside, not the directory itself")
def _(assert_):
    root = tempfile.mkdtemp()
    inner = os.path.join(root, "sub")
    os.makedirs(inner)
    deep = os.path.join(inner, "nested")
    os.makedirs(deep)
    f = os.path.join(deep, "edited.txt")
    with open(f, "w") as fh:
        fh.write("x")
    # The 324h shape: the directory looks ancient, the file inside is recent.
    old = 1_000_000.0
    new = 2_000_000.0
    os.utime(f, (new, new))
    os.utime(deep, (old, old))
    os.utime(inner, (old, old))
    got = sb._newest_mtime(inner)
    assert_(got == new,
            f"a directory's age must come from its newest file ({new}), got {got}")
    assert_(got != os.path.getmtime(inner),
            "the directory's own mtime must not be what is reported")


@case("newest mtime of a plain FILE is still just that file's mtime")
def _(assert_):
    fd, f = tempfile.mkstemp()
    os.close(fd)
    os.utime(f, (1_500_000.0, 1_500_000.0))
    got = sb._newest_mtime(f)
    assert_(got == 1_500_000.0, f"expected the file's own mtime, got {got}")


@case("a submodule that is only DIRTY INSIDE is not reported as stranded work")
def _(assert_):
    base = tempfile.mkdtemp()
    up = _repo(os.path.join(base, "upstream"))
    with open(os.path.join(up, "vendored.txt"), "w") as fh:
        fh.write("original\n")
    _git(up, "add", "-A")
    _git(up, "commit", "-qm", "seed")

    parent = _repo(os.path.join(base, "parent"))
    with open(os.path.join(parent, "README"), "w") as fh:
        fh.write("parent\n")
    _git(parent, "add", "-A")
    _git(parent, "commit", "-qm", "seed")
    add = _git(parent, "-c", "protocol.file.allow=always", "submodule", "add",
               "-q", up, "vendor/dep")
    if add.returncode != 0:
        assert_(True, "")            # submodules unavailable here; nothing to prove
        return
    _git(parent, "commit", "-qm", "vendor dep")

    # Dirty the submodule's WORK TREE only — the recorded commit does not move.
    # This is the applied-patch steady state the dictation rig lives in.
    edited = os.path.join(parent, "vendor", "dep", "vendored.txt")
    with open(edited, "w") as fh:
        fh.write("patched by the build\n")
    ancient = 1_000_000.0             # make the gitlink DIRECTORY look 300h+ old
    os.utime(os.path.join(parent, "vendor", "dep"), (ancient, ancient))

    out = sb.loose_work(repo=parent)
    assert_(out == "",
            f"a submodule dirty only in its work tree is the designed state and "
            f"must produce no line, got {out!r}")


@case("a submodule whose RECORDED COMMIT moved is still reported")
def _(assert_):
    base = tempfile.mkdtemp()
    up = _repo(os.path.join(base, "upstream"))
    with open(os.path.join(up, "vendored.txt"), "w") as fh:
        fh.write("original\n")
    _git(up, "add", "-A")
    _git(up, "commit", "-qm", "seed")

    parent = _repo(os.path.join(base, "parent"))
    with open(os.path.join(parent, "README"), "w") as fh:
        fh.write("parent\n")
    _git(parent, "add", "-A")
    _git(parent, "commit", "-qm", "seed")
    add = _git(parent, "-c", "protocol.file.allow=always", "submodule", "add",
               "-q", up, "vendor/dep")
    if add.returncode != 0:
        assert_(True, "")
        return
    _git(parent, "commit", "-qm", "vendor dep")

    # A real second commit inside the submodule, then move the pointer to it.
    sub = os.path.join(parent, "vendor", "dep")
    with open(os.path.join(sub, "vendored.txt"), "w") as fh:
        fh.write("v2\n")
    _git(sub, "add", "-A")
    _git(sub, "commit", "-qm", "v2")

    stale = 1_000_000.0               # 12h clock must still see this as old
    for root, dirs, files in os.walk(sub):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            try:
                os.utime(os.path.join(root, name), (stale, stale))
            except OSError:
                pass
    os.utime(sub, (stale, stale))

    out = sb.loose_work(repo=parent)
    assert_("uncommitted" in out,
            f"a moved submodule pointer IS uncommitted work and must be "
            f"reported, got {out!r}")


def main():
    failures = []
    for name, fn in CASES:
        errors = []

        def assert_(cond, msg):
            if not cond:
                errors.append(msg)

        try:
            fn(assert_)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"raised {type(exc).__name__}: {exc}")
        if errors:
            print(f"[FAIL] {name}")
            for e in errors:
                print(f"    - {e}")
            failures.append(name)
        else:
            print(f"[PASS] {name}")

    print(f"\n{len(CASES) - len(failures)}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
