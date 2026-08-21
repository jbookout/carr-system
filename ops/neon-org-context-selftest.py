#!/usr/bin/env python3
"""Every neonctl call must name its organization, or it waits for a human.

WHAT HAPPENED, 2026-08-21. The Neon login was refreshed at 11:59, and the
account it refreshed into belongs to more than one organization. From that
moment `neonctl connection-string` stopped answering and started ASKING —
"What organization would you like to use?" — on an interactive picker driven
by arrow keys. Nothing is wrong with the credential. There is simply nobody at
the keyboard of an unattended run, so the call sits until its caller's timeout
and then fails: db-tap gave up after 60 seconds, and every path that derives a
DSN through it went with it, the nightly chain and the restore drill included.

THE TELL IS EASY TO MISREAD. The failure surfaces as a subprocess timeout, so
it reads like a network problem or a slow API. It is neither — the network was
fine and the Neon console answered in half a second. A prompt is not latency.

THE RULE THIS ENFORCES is that no unattended path may depend on an interactive
credential (rule 847f9995). An org picker is exactly that: a human decision
demanded at runtime, in a context where no human can answer.

TWO BELTS, because either alone has a hole:

  * `--org-id` at the call site, which cannot be lost by an environment that
    forgot to export something. This is what tools/db-tap.py does, and until
    today it did it for `projects list` and for nothing else.
  * NEON_ORG_ID exported from bin/routine-credential-env.sh, the prelude every
    unattended routine already sources, which covers the shell callers without
    24 separate edits.

Run:  python3 ops/neon-org-context-selftest.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(label)


# ── 1. the shared prelude exports the org ───────────────────────────────────
prelude = (REPO / "bin" / "routine-credential-env.sh")
prelude_src = prelude.read_text() if prelude.exists() else ""
check("bin/routine-credential-env.sh exports NEON_ORG_ID",
      re.search(r"export\s+NEON_ORG_ID=", prelude_src) is not None,
      "the prelude every unattended routine sources is where the shell callers get it")

# ── 2. db-tap names the org on EVERY neonctl invocation ─────────────────────
tap = (REPO / "tools" / "db-tap.py")
tap_src = tap.read_text() if tap.exists() else ""

check("tools/db-tap.py still defines the org id it passes",
      re.search(r'NEON_ORG\s*=\s*"org-', tap_src) is not None)

uncovered = []
for m in re.finditer(r"\[\s*NEONCTL\b[^\]]*\]", tap_src, re.S):
    call = " ".join(m.group(0).split())
    if "--org-id" not in call:
        line = tap_src[: m.start()].count("\n") + 1
        uncovered.append(f"line {line}: {call[:80]}")
check("tools/db-tap.py names the org on every neonctl invocation",
      not uncovered, "uncovered -> " + "; ".join(uncovered))

# ── 3. the anti-drift case: a NEW call site must not slip through ───────────
# Same reasoning as the codex-exec hook-trust check: several call sites doing
# the same job, each with its own chance to regress quietly.
TRIPLE_DQ = chr(34) * 3 + "..*?" + chr(34) * 3
TRIPLE_SQ = chr(39) * 3 + "..*?" + chr(39) * 3

SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "_to_delete", "out"}
new_sites = []
for path in REPO.rglob("*"):
    if path.suffix not in (".py", ".sh") or not path.is_file():
        continue
    if any(part in SKIP_DIRS or part.startswith((".worktrees", ".codex-worktrees", ".tmp-"))
           for part in path.parts):
        continue
    rel = path.relative_to(REPO)
    if rel.name == "neon-org-context-selftest.py":
        continue
    try:
        src = path.read_text(errors="ignore")
    except OSError:
        continue
    # PROSE IS NOT A CALL SITE. bin/outage-drill.py's docstring discusses
    # neonctl's 60-second timeout at length — it documented this exact failure
    # class before it happened — and never invokes the binary. An earlier draft
    # of this check flagged it, which would have taught the next reader that
    # the gate cries wolf. Crude but sufficient: drop triple-quoted blocks and
    # whole-line comments, then match only what is left.
    code = re.sub(TRIPLE_DQ, "", src, flags=re.S)
    code = re.sub(TRIPLE_SQ, "", code, flags=re.S)
    code = "\n".join(l for l in code.splitlines() if not l.lstrip().startswith("#"))
    if not re.search(r"(NEONCTL|neonctl)\S*\s+(connection-string|projects\s+list|branches\s+list)", code):
        continue
    if "--org-id" in code or "NEON_ORG_ID" in code:
        continue
    new_sites.append(str(rel))
check("no file invokes an org-scoped neonctl subcommand without naming the org",
      not new_sites, f"uncovered: {new_sites}")

print(f"\n{'all checks passed' if not failures else f'FAILED {len(failures)} check(s): ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
