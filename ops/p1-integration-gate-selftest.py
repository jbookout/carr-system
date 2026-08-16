#!/usr/bin/env python3
"""p1-integration-gate-selftest.py — fixtures for ops/p1-integration-gate.py.

WHAT THIS CAN AND CANNOT TEST, said plainly. The gate's real assertions need a
Neon branch, and creating one costs money and time, so CI cannot run them. What
CI CAN hold is the part that must never break: the refusal to touch production,
and the parsing the guards depend on. Those are the lines where a mistake is
unrecoverable rather than merely wrong.

THE ONE THAT MATTERS is guard 0. Every other guard runs AFTER a branch exists;
guard 0 is the one that decides whether anything gets created at all. If staging
ever resolved to the production project id — a renamed project, a copied config,
a typo in the pinned ids — the gate would branch production, load a schema into
it and then DELETE the branch on teardown. Guard 0 is the only thing standing
between that and a very bad night, so it gets a fixture that drives the real
module with the ids deliberately collided.

Run: .venv/bin/python ops/p1-integration-gate-selftest.py
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GATE = REPO / "ops" / "p1-integration-gate.py"

PASSED: int = 0
FAILED: list[str] = []


def check(name, condition, detail=""):
    global PASSED
    if condition:
        PASSED += 1
        print(f"  ok    {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def load_gate():
    spec = importlib.util.spec_from_file_location("p1_integration_gate", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    if not GATE.exists():
        print(f"FAIL: gate not found at {GATE}")
        return 1

    gate = load_gate()

    # ── host parsing: guard 2 is only as good as this ───────────────────────
    check("host_of pulls the endpoint out of a Neon DSN",
          gate.host_of("postgresql://u:p@ep-damp-star-123.us-east-2.aws.neon.tech/db?sslmode=require")
          == "ep-damp-star-123.us-east-2.aws.neon.tech")
    check("host_of ignores the database and the query string",
          gate.host_of("postgres://a:b@host.example/neondb?opts=1") == "host.example")
    check("host_of returns empty rather than guessing on a malformed DSN",
          gate.host_of("not-a-dsn") == "")

    # ── GUARD 0: the refusal that prevents branching production ─────────────
    # Drives the REAL module in a subprocess with the two project ids collided,
    # so the assertion is on the shipped code path rather than on a copy of it.
    shim = f'''
import importlib.util, sys
spec = importlib.util.spec_from_file_location("db_tap", r"{REPO}/tools/db-tap.py")
db_tap = importlib.util.module_from_spec(spec); spec.loader.exec_module(db_tap)
prod_id = db_tap.PROJECTS["production"]["id"]
db_tap.PROJECTS["staging"] = dict(db_tap.PROJECTS["staging"])
db_tap.PROJECTS["staging"]["id"] = prod_id          # the catastrophe case
sys.modules["db_tap"] = db_tap
gspec = importlib.util.spec_from_file_location("g", r"{GATE}")
g = importlib.util.module_from_spec(gspec)
gspec.loader.exec_module(g)
g.db_tap = db_tap
sys.argv = ["p1-integration-gate"]
sys.exit(g.main())
'''
    proc = subprocess.run([sys.executable, "-c", shim], capture_output=True,
                          text=True, timeout=120,
                          env={**os.environ, "NEON_API_KEY": "selftest-not-a-real-key"})
    combined = (proc.stdout or "") + (proc.stderr or "")
    check("guard 0 REFUSES when staging resolves to the production project id",
          proc.returncode != 0 and "PRODUCTION" in combined,
          f"rc={proc.returncode} out={combined.strip()[:180]}")
    check("the refusal says it is creating and deleting nothing",
          "Refusing to branch" in combined,
          combined.strip()[:180])

    # ── the not-configured contract ─────────────────────────────────────────
    # bin/nightly.sh and bin/run-scheduled.sh both read 78 as "not configured
    # here" rather than as a failure. A gate that returned 1 without a
    # credential would turn every un-provisioned machine into a red chain.
    check("EX_CONFIG 78 is the documented no-credential exit",
          "78" in GATE.read_text() and "EX_CONFIG" in GATE.read_text())

    print(f"\np1-integration-gate-selftest: {PASSED}/{PASSED + len(FAILED)} passed")
    if FAILED:
        print("FAILURES: " + ", ".join(FAILED))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
