#!/usr/bin/env python3
"""
gate-integrity-self-heal-selftest.py — acceptance test for the self-heal
branch in hooks/gate-integrity.py, written the same day as the code (rule
e65efc68).

THE INCIDENT IT COMES FROM, 2026-08-18. Dell's Mac was migrated clean on
2026-08-11 (A15/A17 closed, adapter wiring exact). Between then and today
ops/config/hooks.json grew new gates. Nobody ever re-ran the installer on his
machine, so his live settings.json quietly fell behind the repo it is
rendered from — not tampering, just an install that never re-ran. Two Joe
sessions independently diagnosed the identical failure shape and both had to
hand Joe a one-line command to relay to Dell by hand. He asked, twice, for the
system to stop needing a human in the loop for this.

WHAT THE SELF-HEAL MUST DO, one assertion per line of that:

  1. FIRE only on the ENVIRONMENT class (CLAUDE ADAPTER WIRING drift) — never
     on a CONTENT problem (a hash mismatch, a missing/unblessed gate). Content
     drift is tampering-adjacent and must always still stop and reach a human.

  2. RUN the same idempotent installer migrate-dell.sh already trusts
     (`config-as-code.py install --apply`), then RE-CHECK wiring before
     declaring victory — a heal that didn't verify itself is a guess.

  3. NEVER BE SILENT. A successful heal still prints what it did, in both the
     success banner and (if it only partially fixed things) the failure
     banner, because a self-modifying gate that says nothing is the exact
     shape of the 2026-08-08 incident this whole file exists to prevent.

This test monkeypatches gate-integrity's own functions rather than exercising
a real machine: launchd/keychain side effects from a live config-as-code.py
run are out of scope for a unit test and genuinely dangerous to fake.
"""
import importlib.util
import io
import os
import subprocess
import sys
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
GI_PATH = os.path.join(REPO, "hooks", "gate-integrity.py")

spec = importlib.util.spec_from_file_location("gate_integrity_under_test", GI_PATH)
assert spec is not None and spec.loader is not None, f"could not load {GI_PATH}"
gi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gi)


def _run_main_with(monkeypatches, argv=None):
    saved = {}
    for name, value in monkeypatches.items():
        saved[name] = getattr(gi, name)
        setattr(gi, name, value)
    saved_argv = sys.argv
    sys.argv = ["gate-integrity.py"] + (argv or [])
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = gi.main()
    finally:
        for name, value in saved.items():
            setattr(gi, name, value)
        sys.argv = saved_argv
    return rc, buf.getvalue()


def test_healed_when_wiring_only():
    calls = []

    def fake_settings_matches_repo(_state={"n": 0}):
        _state["n"] += 1
        if _state["n"] == 1:
            return (["missing hook X"], None)
        return ([], None)  # re-check after heal: clean

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="WROTE OK", stderr="")

    patches = {
        "json": _FakeJson(base_hashes=gi.current(), base_contracts=gi.current_contracts()),
        "claude_configuration_state": lambda *a, **k: "configured",
        "codex_configuration_state": lambda *a, **k: "absent",
        "settings_matches_repo": fake_settings_matches_repo,
        "rule_enforcement_map_matches_inventory": lambda *a, **k: (True, None),
        "project_settings_path": lambda *a, **k: "/does/not/exist",
        "subprocess": _FakeSubprocess(fake_run),
    }
    rc, out = _run_main_with(patches)
    assert rc == 0, f"expected exit 0, got {rc}\n{out}"
    assert calls, "self-heal never ran config-as-code.py"
    assert "install" in calls[0] and "--apply" in calls[0], calls[0]
    assert "SELF-HEAL" in out, out
    assert "GATE INTEGRITY FAILURE" not in out, out
    print("PASS  wiring-only drift self-heals and re-verifies")


def test_no_heal_on_content_problem():
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_settings_matches_repo():
        return (["missing hook X"], None)

    patches = {
        "json": _FakeJson(base_hashes={"gate-integrity.py": "not-the-real-hash"},
                           base_contracts=gi.current_contracts()),
        "claude_configuration_state": lambda *a, **k: "configured",
        "codex_configuration_state": lambda *a, **k: "absent",
        "settings_matches_repo": fake_settings_matches_repo,
        "rule_enforcement_map_matches_inventory": lambda *a, **k: (True, None),
        "project_settings_path": lambda *a, **k: "/does/not/exist",
        "subprocess": _FakeSubprocess(fake_run),
    }
    rc, out = _run_main_with(patches)
    assert not calls, f"self-heal ran despite a CONTENT problem: {calls}"
    assert "GATE INTEGRITY FAILURE" in out, out
    assert "CHANGED: hooks/gate-integrity.py" in out, out
    print("PASS  content-class drift (tampering-adjacent) never triggers self-heal")


class _FakeSubprocess:
    """Stands in for the `subprocess` module reference inside gate-integrity,
    routing .run to a test double while leaving everything else untouched."""
    def __init__(self, run_fn):
        self.run = run_fn
        self.CompletedProcess = subprocess.CompletedProcess
        self.SubprocessError = subprocess.SubprocessError
        self.DEVNULL = subprocess.DEVNULL


class _FakeJson:
    """Stands in for the `json` module reference, serving a synthetic baseline
    on the one `json.load(open(BASELINE))` call path and passing everything
    else (including json.dumps used elsewhere) straight through."""
    def __init__(self, base_hashes, base_contracts):
        import json as _real_json
        self._real = _real_json
        self._baseline = {"hashes": base_hashes, "contracts": base_contracts}

    def load(self, fh):
        return self._baseline

    def dumps(self, *a, **k):
        return self._real.dumps(*a, **k)


def main():
    # BASELINE must exist as a path for os.path.exists(BASELINE) to pass;
    # point it at this file itself (content irrelevant — json.load is faked).
    gi.BASELINE = __file__
    test_healed_when_wiring_only()
    test_no_heal_on_content_problem()
    return 0


if __name__ == "__main__":
    sys.exit(main())
