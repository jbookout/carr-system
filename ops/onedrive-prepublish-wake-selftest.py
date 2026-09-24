#!/usr/bin/env python3
"""ops/onedrive-prepublish-wake-selftest.py — asserts the nightly-exports
fix for the 9/22-9/24 OneDrive File Provider defect is actually wired in,
rather than merely present as files nothing calls.

Covers, cheaply and without a database or a live OneDrive mount:
  * ops/onedrive-prepublish-wake.py runs standalone, single-attempt, and
    exits 0 even when every target is absent (a cold/empty provider must
    never fail this step — see its own docstring).
  * bin/nightly.sh actually calls it, BEFORE the exports step, and wraps the
    exports step itself in `caffeinate -i -s`.
  * bin/step-timeout.zsh bounds the new step with its OWN short external
    wall-clock, strictly shorter than the exports step's — a step meant to
    buy the real wait a head start must never be allowed to eat into it.
  * bin/nightly-exports-retry.sh (the daytime safety net) parses, and uses
    the words-not-a-function-call shape carr_routine_exec requires (see
    bin/step-timeout.zsh's own carr_step_timeout_prefix docstring) rather
    than repeating the env -i / zsh-function mistake that bug fix started
    from.
  * The new launchd plist parses with plistlib and uses StartCalendarInterval,
    not StartInterval (StartInterval jobs have been observed to never fire on
    this Mac).

Run: python3 ops/onedrive-prepublish-wake-selftest.py
"""

import plistlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
checked = 0
failed = 0


def ok(cond, label):
    global checked, failed
    checked += 1
    if cond:
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}")


def main() -> int:
    # Prefer the repo's own .venv (has psycopg/openpyxl, same as every
    # nightly.sh step); fall back to the ambient interpreter. FAILS OPEN with
    # a clear SKIP rather than a false FAIL when neither can import the
    # exporters package at all — the same convention md_renders_retired() and
    # carr_step_timeout_prefix already use elsewhere in this chain.
    venv_python = REPO / ".venv" / "bin" / "python"
    python = str(venv_python) if venv_python.exists() else sys.executable

    # ── the wake script runs standalone and never fails on a cold provider ──
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [python, str(REPO / "ops" / "onedrive-prepublish-wake.py")],
            env={"PATH": "/usr/bin:/bin", "CARR_EXPORT_HOME": tmp},
            cwd=str(REPO), capture_output=True, text=True, timeout=30,
        )
    if proc.returncode != 0 and "ModuleNotFoundError" in proc.stderr:
        print(f"  SKIP  onedrive-prepublish-wake.py needs {python}'s exporters "
              f"dependencies (psycopg/openpyxl), not available here — not a "
              f"finding about the code")
    else:
        ok(proc.returncode == 0, "onedrive-prepublish-wake.py exits 0 against an empty (cold) provider dir")
        ok("absent" in proc.stdout, "it reports each missing target as absent rather than raising")
        ok("probing 6 live export path" in proc.stdout,
           "it probes exactly the six LIVE targets, not the retired .md renders too")

    # ── bin/nightly.sh: ordering and the caffeinate wrap ────────────────────
    nightly = (REPO / "bin" / "nightly.sh").read_text()
    wake_idx = nightly.find('step "onedrive pre-publish wake"')
    exports_idx = nightly.find('step "exports (6 targets -> OneDrive)"')
    ok(wake_idx != -1, "bin/nightly.sh calls the onedrive pre-publish wake step")
    ok(exports_idx != -1, "bin/nightly.sh still calls the exports step")
    ok(wake_idx != -1 and exports_idx != -1 and wake_idx < exports_idx,
       "the wake step runs BEFORE the exports step, not after")
    exports_line = next((l for l in nightly.splitlines()
                          if l.strip().startswith('step "exports (6 targets -> OneDrive)"')), "")
    ok("caffeinate -i -s" in exports_line,
       "the exports step itself runs under caffeinate -i -s")

    # ── bin/step-timeout.zsh: the new step's own short, separate budget ─────
    step_timeout = (REPO / "bin" / "step-timeout.zsh").read_text()
    wake_m = re.search(r'"onedrive pre-publish wake"\s+(\d+)', step_timeout)
    exports_m = re.search(r'"exports"\s+(\d+)', step_timeout)
    ok(wake_m is not None, "STEP_TIMEOUT_OVERRIDE declares a budget for the wake step")
    ok(exports_m is not None, "STEP_TIMEOUT_OVERRIDE still declares a budget for exports")
    if wake_m and exports_m:
        wake_budget, exports_budget = int(wake_m.group(1)), int(exports_m.group(1))
        ok(0 < wake_budget < exports_budget,
           f"the wake step's budget ({wake_budget}s) is short and strictly less than "
           f"exports' ({exports_budget}s) — it can only ever cost a head start, never "
           "eat into the real wait")

    # ── bin/nightly-exports-retry.sh: syntax, and the env -i / function bug ─
    retry_script = REPO / "bin" / "nightly-exports-retry.sh"
    proc = subprocess.run(["zsh", "-n", str(retry_script)], capture_output=True, text=True)
    ok(proc.returncode == 0, f"bin/nightly-exports-retry.sh parses under zsh -n ({proc.stderr.strip()})")
    retry_src = retry_script.read_text()
    ok("carr_routine_exec carr_step_with_timeout" not in retry_src,
       "the retry script does not pass a zsh FUNCTION through env -i (carr_routine_exec "
       "execs a real program; a function call there silently fails to run at all)")
    ok('carr_step_timeout_prefix "$(carr_step_timeout_for exports)"' in retry_src
       and 'carr_routine_exec "${CARR_STEP_TIMEOUT_ARGV[@]}"' in retry_src,
       "it uses carr_step_timeout_prefix's ARGV words instead, the same shape step() uses")
    ok("caffeinate -i -s" in retry_src, "the daytime retry also runs the export under caffeinate")

    # ── the new plist ─────────────────────────────────────────────────────
    plist_path = REPO / "ops" / "launchd" / "com.carr.nightly-exports-daytime-retry.plist"
    with open(plist_path, "rb") as fh:
        plist = plistlib.load(fh)
    ok(plist.get("Label") == "com.carr.nightly-exports-daytime-retry",
       "the new plist declares its own label")
    ok("StartCalendarInterval" in plist and "StartInterval" not in plist,
       "it uses StartCalendarInterval, not StartInterval (observed to never fire on this Mac)")

    installer = REPO / "bin" / "install-nightly-exports-retry.sh"
    ok(installer.exists(), "a narrow installer script exists for the new job")
    proc = subprocess.run(["zsh", "-n", str(installer)], capture_output=True, text=True)
    ok(proc.returncode == 0, f"the installer parses under zsh -n ({proc.stderr.strip()})")

    print(f"\nonedrive-prepublish-wake-selftest: {checked - failed}/{checked} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
