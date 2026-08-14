#!/usr/bin/env python3
"""staged-gate-bless-check.py — a gate change carries its bless, or it does not
commit (rule c0b38d80). Called by ops/githooks/pre-commit; not a hook itself.

TWICE THIS WAS VIOLATED FOR REAL. 2026-08-12: a gate change landed without its
bless and main's baseline was later overwritten by a PR cut before it. 2026-08-14:
a live hotfix to settings-change-gate.py sat unblessed in the shared checkout and
every session that morning started under a GATE INTEGRITY FAILURE banner. Both
times the gate moved without the baseline and every later session had to treat
the enforcement layer as untrusted. The commit is the one moment the two can be
forced to move together, so that is where this stands.

WHAT IT CHECKS, against the INDEX, not the working tree — a partially staged
bless is exactly the ambiguity it exists to refuse:

  1. Any staged hooks/*.py (except *-selftest.py, which gate-integrity
     deliberately leaves unprotected) requires ops/config/gate-baseline.json
     staged in the same commit.
  2. The staged baseline's recorded hash must match the staged file bytes.
     Presence alone would bless the wrong bytes — the 2026-08-13 false
     re-bless — so the hash is verified, not assumed.
  3. The wiring contracts get the same treatment: a contract changes what the
     gates enforce without touching a hook, which is why gate-integrity hashes
     them too.

The contract list is duplicated from hooks/gate-integrity.py CONTRACTS on
purpose: this script must run inside selftest fixture repos that carry no
hooks/ directory, and a drifted entry here fails toward a false refusal, which
the escape hatch answers, never toward silence.

Exit 0 to allow, 1 to refuse (pre-commit relays the message), and 0 with a
warning if git itself cannot be read — an accident-stopper that crashes must
never become the thing that blocks every commit.
"""

import hashlib
import json
import subprocess
import sys

CONTRACTS = ("delegation-gate-hook.json", "codex-hooks.json",
             "rule-enforcement-map.json", "model-floors.json")
BASELINE = "ops/config/gate-baseline.json"


def staged_files() -> list[str]:
    out = subprocess.run(["git", "diff", "--cached", "--name-only"],
                         capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if line.strip()]


def staged_blob(path: str) -> bytes | None:
    """The INDEX copy of a path, or None when the stage deletes it."""
    p = subprocess.run(["git", "show", f":{path}"], capture_output=True)
    return p.stdout if p.returncode == 0 else None


def gate_paths(staged: list[str]) -> list[str]:
    hooks = [p for p in staged
             if p.startswith("hooks/") and p.endswith(".py")
             and p.count("/") == 1 and not p.endswith("-selftest.py")]
    contracts = [p for p in staged
                 if p.startswith("ops/config/")
                 and p.split("/")[-1] in CONTRACTS]
    return hooks + contracts


def baseline_entry(baseline: dict, path: str) -> str | None:
    name = path.split("/")[-1]
    table = "contracts" if name in CONTRACTS else "hashes"
    return (baseline.get(table) or {}).get(name)


def main() -> int:
    try:
        staged = staged_files()
    except Exception as exc:                                  # noqa: BLE001
        print(f"staged-gate-bless-check: could not read the index ({exc}); "
              "letting the commit through UNCHECKED.", file=sys.stderr)
        return 0

    touched = gate_paths(staged)
    if not touched:
        return 0

    problems = []
    if BASELINE not in staged:
        problems = [f"{p} is staged, but {BASELINE} is not" for p in touched]
    else:
        raw = staged_blob(BASELINE)
        try:
            baseline = json.loads(raw or b"")
        except ValueError:
            baseline = None
        if not isinstance(baseline, dict):
            problems.append(f"{BASELINE} is staged but is not readable JSON")
        else:
            for path in touched:
                blob = staged_blob(path)
                want = baseline_entry(baseline, path)
                if blob is None:
                    # A staged deletion: the baseline moved in this commit and
                    # that is what co-change requires; gate-integrity owns
                    # whether the removal itself is coherent.
                    continue
                got = hashlib.sha256(blob).hexdigest()
                if want != got:
                    problems.append(
                        f"{path}: staged bytes hash {got[:12]}…, baseline "
                        f"records {(want or 'NO ENTRY')[:12]}…")

    if not problems:
        return 0

    names = " ".join(sorted({p.split("/")[-1] for p in touched
                             if not p.startswith("ops/config/")}
                            | {p.split("/")[-1] for p in touched
                               if p.startswith("ops/config/")}))
    sys.stderr.write(
        "\n"
        "  ────────────────────────────────────────────────────────────────\n"
        "  COMMIT REFUSED — a gate change must carry its bless (c0b38d80)\n"
        "  ────────────────────────────────────────────────────────────────\n\n"
        "  Your changes are untouched and still staged. Nothing was lost.\n\n")
    for p in problems:
        sys.stderr.write(f"    {p}\n")
    sys.stderr.write(
        "\n"
        "  A gate that moves without its baseline makes every later session\n"
        "  start under a GATE INTEGRITY FAILURE it can do nothing about —\n"
        "  that was 2026-08-12, and again the morning of 2026-08-14.\n\n"
        "  Bless what you changed and stage the result with it:\n\n"
        f"      python3 hooks/gate-integrity.py --bless {names}\n"
        f"      git add {BASELINE}\n\n"
        "  If this commit genuinely must not carry the bless, say so for one\n"
        "  command:\n\n"
        "      CARR_ALLOW_UNBLESSED_GATE=1 git commit ...\n\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
