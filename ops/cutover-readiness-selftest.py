#!/usr/bin/env python3
"""cutover-readiness-selftest.py — proves the DECISION logic in
ops/cutover-readiness.py without a live database or a local-actor identity
file. It cannot fake either of those (a fake identity file is exactly the
caller-supplied-identity hole Phase 1 closed in mcp-server/local-verb.mjs,
and this suite must not reopen it even for testing purposes), so it tests
the two pure functions the real script's decisions actually reduce to:

  declared_count()  — regex parsing of a compiled-rules render's own header
                       or footer, both real forms that exist today (the
                       shared file uses the header; compiled-rules-dell.md
                       is empty and only has the footer).
  classify_partner() — the READY / NOT READY / PARTIAL-HERE decision, given
                       already-computed booleans. Every branch is exercised:
                       local+all-green, local+render-disagreement,
                       local+live-call-failed, local+identity-mismatch,
                       local+doctrine-unreachable, non-local+render-agrees,
                       non-local+render-disagrees.

Same "fixtures + assert" style as the other ops/*-selftest.py files; unlike
guard-selftest.py this needs no subprocess because there is no hook process
boundary to prove — the functions under test are already the artifact the
main script calls, imported directly (not reimplemented), so a fix to either
function is proven by the same file it changes.

  ./.venv/bin/python ops/cutover-readiness-selftest.py [-v]

Exit 0 = every case passed. Exit 1 = at least one did not.
"""
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "ops"))

import importlib.util
_spec = importlib.util.spec_from_file_location("cutover_readiness", REPO / "ops" / "cutover-readiness.py")
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)

VERBOSE = "-v" in sys.argv[1:]
CASES = []


def case(name, fn):
    CASES.append((name, fn))


# ---------- declared_count() ----------

def _with_tmp(text):
    d = Path(tempfile.mkdtemp())
    p = d / "rules.md"
    p.write_text(text)
    return p


case("declared_count: header form (shared/joe style)",
     lambda: cr.declared_count(_with_tmp(
         "# Compiled rules\n\n**153 active rule(s), by section.** Recite the total.\n"
         "...\n*Exported: 2026-08-13T00:00:00Z · 153 active rule(s)*\n")) == 153)

case("declared_count: footer-only form (empty file, dell style)",
     lambda: cr.declared_count(_with_tmp(
         "# Compiled rules — Dell (personal)\n\n*No active rules yet.*\n\n"
         "*Exported: 2026-08-13T00:00:00Z · 0 active rule(s)*\n")) == 0)

case("declared_count: missing file returns None",
     lambda: cr.declared_count(Path(tempfile.mkdtemp()) / "does-not-exist.md") is None)

case("declared_count: file with neither pattern returns None",
     lambda: cr.declared_count(_with_tmp("nothing here about rule counts\n")) is None)


# ---------- classify_partner() ----------

case("classify: local, everything agrees -> READY, not a failure",
     lambda: cr.classify_partner("joe", is_local=True, render_ok=True,
                                  live_ok=True, live_agrees=True, identity_agrees=True,
                                  doctrine_ok=True) == ("READY", False))

case("classify: local, render disagrees -> NOT READY regardless of live -> failure",
     lambda: cr.classify_partner("joe", is_local=True, render_ok=False,
                                  live_ok=True, live_agrees=True, identity_agrees=True,
                                  doctrine_ok=True) == ("NOT READY", True))

case("classify: local, live call failed -> NOT READY, failure",
     lambda: cr.classify_partner("joe", is_local=True, render_ok=True,
                                  live_ok=False, live_agrees=False, identity_agrees=False,
                                  doctrine_ok=True) == ("NOT READY", True))

case("classify: local, live counts disagree with store -> NOT READY, failure",
     lambda: cr.classify_partner("joe", is_local=True, render_ok=True,
                                  live_ok=True, live_agrees=False, identity_agrees=True,
                                  doctrine_ok=True) == ("NOT READY", True))

case("classify: local, sponsoring_human_id does not match partner -> NOT READY, failure",
     lambda: cr.classify_partner("joe", is_local=True, render_ok=True,
                                  live_ok=True, live_agrees=True, identity_agrees=False,
                                  doctrine_ok=True) == ("NOT READY", True))

case("classify: local, doctrine unreachable -> NOT READY, failure",
     lambda: cr.classify_partner("joe", is_local=True, render_ok=True,
                                  live_ok=True, live_agrees=True, identity_agrees=True,
                                  doctrine_ok=False) == ("NOT READY", True))

case("classify: non-local, render agrees -> PARTIAL-HERE, NOT a failure",
     lambda: (lambda r: r[0].startswith("PARTIAL-HERE") and r[1] is False)(
         cr.classify_partner("dell", is_local=False, render_ok=True, reason="this machine's identity is 'joe'")))

case("classify: non-local, render disagrees -> NOT READY, IS a failure "
     "(a store/render mismatch is a real fact, not an identity limit)",
     lambda: cr.classify_partner("dell", is_local=False, render_ok=False,
                                  reason="this machine's identity is 'joe'") == ("NOT READY", True))


def main():
    failed = 0
    for name, fn in CASES:
        try:
            ok = bool(fn())
        except Exception as e:
            ok = False
            name = f"{name}  [EXCEPTION: {type(e).__name__}: {e}]"
        if VERBOSE or not ok:
            print(f"  {'OK' if ok else 'FAIL'}  {name}")
        if not ok:
            failed += 1
    print(f"cutover-readiness-selftest: {len(CASES) - failed}/{len(CASES)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
