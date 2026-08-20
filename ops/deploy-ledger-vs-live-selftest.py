#!/usr/bin/env python3
"""deploy-ledger-vs-live-selftest.py — prove the drift check actually detects drift.

A check that has only ever been run against a system in agreement is a check
nobody has tested. Every case below is one of the three real disagreements that
happened on 2026-08-19 and 2026-08-20 and were each caught by a human curling
/release by hand, plus the two directions the comparison must tell apart.

IN-PROCESS BY DESIGN, and here is exactly what that does and does not buy. The
value under test is the COMPARISON — which pairs count as drift, which direction
the message reports, and what exit code each produces. That logic needs no
network and no Postgres, and giving it neither means the suite is hermetic and
cannot fail because Neon was asleep or Cloudflare was slow. What it therefore
does NOT cover is the two readers themselves: read_live's HTTP handling and
baseline_row's SQL. The final case runs the real script end to end against the
real endpoint and ledger to cover those, and accepts SKIP as a pass — because on
a machine with no credential, SKIP is the correct answer and demanding otherwise
would make this suite fail for the wrong reason.

Exit 0 if every case passes, 1 otherwise.

    ops/deploy-ledger-vs-live-selftest.py
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CHECK = HERE / "deploy-ledger-vs-live.py"

FAILED: list[str] = []
RAN: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    # RAN is counted, never hardcoded. The first draft of this file ended with a
    # literal "11" while twelve cases ran, which is the same species of bug this
    # whole lane is about: a number asserted instead of measured.
    RAN.append(label)
    print(("  ok    " if ok else "  FAIL  ") + label + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(label)


def load_check_module():
    spec = importlib.util.spec_from_file_location("deploy_ledger_vs_live", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def live(verbs, sha="a" * 40, version="ver-1", env="production"):
    """A /release payload in the shape the Worker really returns, wrapped fields
    and all — env, git_sha and worker_version each arrive as {"value": ...}."""
    return {"ok": True, "env": {"value": env}, "verb_count": verbs,
            "git_sha": {"value": sha},
            "worker_version": {"id": version, "value": version}}


def run_case(mod, live_payload, ledger_row):
    """Call main() with both readers stubbed; return (exit_code, printed_line)."""
    mod.read_live = lambda url, timeout=30: live_payload

    class FakeCursor:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class FakeConn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return FakeCursor()

    class FakeOps:
        @staticmethod
        def connect(_mode): return FakeConn()

    class FakeBaselineModule:
        @staticmethod
        def load_ops_record(): return FakeOps()
        @staticmethod
        def baseline_row(_cur, _service, _env): return ledger_row

    mod.load_baseline_module = lambda: FakeBaselineModule()
    argv = sys.argv
    sys.argv = ["deploy-ledger-vs-live.py"]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = mod.main()
    finally:
        sys.argv = argv
    return rc, buf.getvalue().strip()


def main() -> int:
    mod = load_check_module()
    row = lambda verbs, sha="a" * 40, ver="ver-1": (  # noqa: E731
        verbs, sha, ver, "complete", "2026-08-20 12:17:16+00")

    rc, out = run_case(mod, live(143), row(143))
    check("agreement passes", rc == 0 and out.startswith("OK"), out[:90])

    # THE 2026-08-20 CASE: ledger 141, production 143, because a complete row
    # carried verb_count NULL and the baseline query skipped it.
    rc, out = run_case(mod, live(143), row(141))
    check("ledger BEHIND live is caught", rc == 1 and "BEHIND" in out, out[:110])
    check("...and it says a loss would pass the guard",
          "drop live verbs" in out, out[:110])

    # THE 2026-08-09 CASE this whole lane exists for: production lost verbs.
    rc, out = run_case(mod, live(66), row(75))
    check("ledger AHEAD of live is caught", rc == 1 and "AHEAD" in out, out[:110])
    check("...and the two directions do not share wording",
          "verb loss the guard exists to catch" in out, out[:110])

    rc, out = run_case(mod, live(143, sha="b" * 40), row(143, sha="c" * 40))
    check("a different commit serving is caught",
          rc == 1 and "git_sha" in out, out[:110])

    rc, out = run_case(mod, live(143, version="ver-live"), row(143, ver="ver-ledger"))
    check("a different provider version is caught",
          rc == 1 and "provider_version" in out, out[:110])

    # Every field wrong at once must report ALL of them, not stop at the first:
    # a partial report invites fixing one and believing the drift is closed.
    rc, out = run_case(mod, live(143, sha="b" * 40, version="ver-live"),
                       row(141, sha="c" * 40, ver="ver-ledger"))
    check("all three disagreements are reported together",
          rc == 1 and all(k in out for k in ("verb_count", "git_sha", "provider_version")),
          out[:140])

    rc, out = run_case(mod, live(143), None)
    check("no baseline row at all is caught, not treated as agreement",
          rc == 1 and "NO baseline row" in out, out[:110])

    # env is load-bearing: git_sha and schema are identical across environments
    # by design, so a staging endpoint would otherwise look like a clean match.
    rc, out = run_case(mod, live(143, env="staging"), row(143))
    check("a staging endpoint is refused, not silently compared",
          rc == 1 and "meaningless" in out, out[:110])

    # An unreadable endpoint is not evidence of drift.
    mod.read_live = lambda url, timeout=30: (_ for _ in ()).throw(OSError("boom"))
    argv = sys.argv
    sys.argv = ["deploy-ledger-vs-live.py"]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = mod.main()
    finally:
        sys.argv = argv
    check("an unreachable endpoint SKIPs rather than reporting drift",
          rc == 2 and buf.getvalue().startswith("SKIP"), buf.getvalue().strip()[:90])

    # END TO END, covering the two readers the stubs above deliberately replace.
    # SKIP counts as a pass: on a machine with no ledger credential that is the
    # right answer, and failing here would be failing for the wrong reason.
    py = REPO / ".venv" / "bin" / "python"
    proc = subprocess.run([str(py if py.exists() else sys.executable), str(CHECK)],
                          capture_output=True, text=True, timeout=120,
                          cwd=str(REPO), env=dict(os.environ))
    first = (proc.stdout or proc.stderr).strip().splitlines()
    first = first[0] if first else ""
    check("the real script runs against the real endpoint and ledger",
          proc.returncode in (0, 1, 2) and first[:4] in ("OK d", "WARN", "SKIP"),
          f"rc={proc.returncode} {first[:100]}")

    print(f"\ndeploy-ledger-vs-live-selftest: {len(RAN) - len(FAILED)}/{len(RAN)} passed")
    if FAILED:
        print("FAILED: " + ", ".join(FAILED))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
