#!/usr/bin/env python3
"""Hermetic checks that a LIVE export cannot resolve its vault root to the repo.

WHY THIS EXISTS. bin/routine-credential-env.sh runs every nightly step under
`env -i` and passes CARR_VAULT="${CARR_VAULT:-}", which is an EMPTY STRING when
the variable is unset — and it is unset under launchd. exporters/common.py read
that root with os.environ.get(name, default), which returns the empty string
rather than the default, so VAULT became Path("") and every path a LIVE export
called absolute resolved against the working directory instead: the repo.

Measured cost before the fix (2026-08-19 nightly run): two nights of exports
printed "ok — N rows -> DNA/... (LIVE)" while writing 126 files into untracked
~/carr-system/DNA and ~/carr-system/00_Context. The vault sat frozen at
2026-08-17 02:08 CDT, five generated files read STALE, 36 renders reported as
tampered (the recorded hash described the repo copy), and two export targets
failed to build against source files that exist perfectly well in the vault.

The three cases below are the whole contract: unset falls back, EMPTY falls back
the same way, and a real value is still honoured.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = REPO / ".venv" / "bin" / "python"
PROBE = "from exporters.common import VAULT; print(VAULT)"


def check(label: str, ok: bool) -> None:
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        raise AssertionError(label)


def vault_root(carr_vault: str | None) -> str:
    """Resolve exporters.common.VAULT in a clean child, as the chain does."""
    env = {"HOME": os.environ.get("HOME", ""), "PATH": os.environ.get("PATH", "")}
    if carr_vault is not None:
        env["CARR_VAULT"] = carr_vault
    out = subprocess.run([str(PY), "-c", PROBE], cwd=REPO, env=env,
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def main() -> int:
    print("export vault root")
    default = vault_root(None)
    check("unset CARR_VAULT resolves to an absolute root", default.startswith("/"))
    check("unset CARR_VAULT resolves outside the repo", not default.startswith(str(REPO)))

    empty = vault_root("")
    check("EMPTY CARR_VAULT falls back to the same root as unset", empty == default)
    check("EMPTY CARR_VAULT never resolves to the repo or a relative path",
          empty.startswith("/") and not empty.startswith(str(REPO)))

    with tempfile.TemporaryDirectory() as tmp:
        check("a real CARR_VAULT is still honoured", vault_root(tmp) == tmp)

    helper = (REPO / "bin" / "routine-credential-env.sh").read_text(encoding="utf-8")
    check("the clean child never passes CARR_VAULT as an empty string",
          'CARR_VAULT="${CARR_VAULT:-}"' not in helper)
    check("the clean child still passes a real CARR_VAULT through",
          'vault_env=(CARR_VAULT="$CARR_VAULT")' in helper)

    # The helper is the OTHER half of the same contract: a routine child must
    # receive a real vault root or no key at all, never an empty one.
    probe = 'source "%s"; carr_routine_exec /usr/bin/env' % (REPO / "bin" / "routine-credential-env.sh")
    for label, carr_vault in (("unset", None), ("empty", "")):
        env = {"HOME": os.environ.get("HOME", ""), "PATH": os.environ.get("PATH", "")}
        if carr_vault is not None:
            env["CARR_VAULT"] = carr_vault
        out = subprocess.run(["/bin/zsh", "-c", probe], cwd=REPO, env=env,
                             capture_output=True, text=True)
        passed = [ln for ln in out.stdout.splitlines() if ln.startswith("CARR_VAULT=")]
        check(f"a {label} CARR_VAULT reaches the child as no key rather than an empty one",
              passed == [])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
