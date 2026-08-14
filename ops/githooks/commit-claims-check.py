#!/usr/bin/env python3
"""commit-claims-check.py — the commit message and the diff must agree
(rule 24e10ee8). Called by ops/githooks/commit-msg; not a hook itself.

THE INCIDENT, 2026-08-13. A commit message claimed the gate baseline had been
re-blessed; the diff contained no baseline. The message is the only part of a
commit nothing verifies — CI runs the tree, the baseline hashes the gates, and
the message can say anything at all. So the two claim shapes that have actually
lied here are checked against the INDEX at commit time:

  1. "re-bless(ed)" / "re-stamp(ed) … baseline" requires
     ops/config/gate-baseline.json in the staged diff.
  2. A body line in the house named-path style — `path/to/file.ext — what
     changed` — requires that path in the staged diff. That form is how this
     repo's messages enumerate their own diff (the named-path commit law), so
     a line-leading path about an untouched file is a claim the diff refutes.
     Mid-prose mentions of a path are NOT claims and are left alone.

A message-only amend stages nothing, so with an empty index the check stands
down — there is no diff to disagree with. A partial amend can legitimately
carry claims about files outside the staged delta; that is what the one-command
escape hatch (CARR_ALLOW_CLAIM_MISMATCH=1) is for, and why this stays an
accident-stopper rather than a security control.

Exit 0 to allow, 1 to refuse; 0 with a warning when git itself cannot be read.
"""

import re
import subprocess
import sys

BASELINE = "ops/config/gate-baseline.json"
REBLESS = re.compile(r"\bre-?(?:bless|stamp)\w*\b.{0,60}?\bbaseline\b"
                     r"|\bbaseline\b.{0,60}?\bre-?(?:bless|stamp)\w*\b",
                     re.I | re.S)
# Line-leading `path — text`: at least one slash, a real extension, then the
# em-dash separator the house style uses. An en-dash or hyphen is accepted so a
# typo does not turn a claim into prose.
PATH_LINE = re.compile(r"^([A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)+\.[A-Za-z0-9]+)"
                       r"\s+[—–-]\s", re.M)


def staged_files() -> list[str] | None:
    p = subprocess.run(["git", "diff", "--cached", "--name-only"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return None
    return [line for line in p.stdout.splitlines() if line.strip()]


def message_of(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    # Strip comments and everything below a scissors line, the way git does.
    lines = []
    for line in raw.splitlines():
        if line.startswith("# ------------------------ >8 ------------------------"):
            break
        if line.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def claims_of(message: str) -> list[str]:
    claimed = []
    # The re-bless claim is matched against the SUBJECT only. A body regularly
    # DISCUSSES past re-bless incidents in prose — this very hook's history
    # does — and a mention is not an invocation (the settings-change gate
    # learned that the hard way on 2026-08-14). The subject states what this
    # commit does; that is where the claim lives.
    subject = message.splitlines()[0] if message.splitlines() else ""
    if REBLESS.search(subject):
        claimed.append(BASELINE)
    for match in PATH_LINE.finditer(message):
        claimed.append(match.group(1))
    return claimed


def main() -> int:
    if len(sys.argv) < 2:
        return 0
    try:
        message = message_of(sys.argv[1])
    except OSError as exc:
        print(f"commit-claims-check: could not read the message ({exc}); "
              "letting the commit through UNCHECKED.", file=sys.stderr)
        return 0

    claimed = claims_of(message)
    if not claimed:
        return 0

    staged = staged_files()
    if staged is None:
        print("commit-claims-check: could not read the index; letting the "
              "commit through UNCHECKED.", file=sys.stderr)
        return 0
    if not staged:
        return 0  # message-only amend: no diff to disagree with

    missing = [c for c in claimed if c not in staged]
    if not missing:
        return 0

    sys.stderr.write(
        "\n"
        "  ────────────────────────────────────────────────────────────────\n"
        "  COMMIT REFUSED — the message claims what the diff does not do\n"
        "  ────────────────────────────────────────────────────────────────\n\n"
        "  Your changes are untouched and still staged. Nothing was lost.\n\n")
    for c in missing:
        if c == BASELINE and BASELINE not in message:
            sys.stderr.write(f"    the message says the baseline was re-blessed, "
                             f"but {BASELINE} is not in the staged diff\n")
        else:
            sys.stderr.write(f"    the message names {c}, "
                             "which is not in the staged diff\n")
    sys.stderr.write(
        "\n"
        "  On 2026-08-13 a commit claimed a re-bless its diff did not carry,\n"
        "  and whoever read the message believed the gates were attested.\n"
        "  Fix the message, or stage what it names. A partial amend that\n"
        "  legitimately describes files outside this delta can say so for\n"
        "  one command:\n\n"
        "      CARR_ALLOW_CLAIM_MISMATCH=1 git commit ...\n\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
