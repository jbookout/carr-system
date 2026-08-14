#!/usr/bin/env python3
"""rule-render-markup-check.py — fail loudly if tool-call markup reached a rule.

THE DEFECT, documented in full by rule c53beeaa: when a tool call is written with
a malformed or unterminated parameter, the closing tag lands INSIDE a field's
value instead of terminating it. That field swallows its own closer plus every
parameter after it as literal text, and each of those parameters is written NULL.
The verb returns ok:true, because the call did parse — into the wrong shape.

c53beeaa already prescribes the check: "Assert no field contains `<parameter`,
`</parameter`, `<invoke` or `</invoke`." Nothing ran it. On 2026-08-13 SIX active
shared rules were found carrying the markup, five of them with the partner's
verbatim quote absorbed into the statement and human_quote written NULL — his
testimony surviving only as literal text inside compiled prose. The oldest dated
from 2026-08-09, so it sat in binding rules for four days while every session
loaded it.

c53beeaa also says this defect SURVIVES KNOWING ABOUT IT: on 2026-08-03 the
session repairing three corrupted rules reproduced the identical corruption on
its very next write, while holding the diagnosis in context. Care does not
prevent it. Only a check does, and a check nobody runs is not a check — so this
runs on the hourly rules refresh, right where the renders are produced.

It reads the RENDERS rather than the store on purpose: the renders are what every
session actually loads, so a clean render is the property that matters, and it
needs no database credential to run.

Exit 1 on any hit, so the refresh logs a FAIL instead of a silent OK.
"""
import os
import re
import sys
from glob import glob

MARKERS = ("<parameter", "</parameter", "<invoke", "</invoke")

# The one legitimate occurrence: rule c53beeaa quotes these strings when telling
# a session to assert their absence. Matched by its own sentence, not by rule id,
# so rewording the rule does not silently disable the check for everything else.
ALLOWED = "Assert no field contains"


def find_vault():
    """Resolve the render root without assuming Joe's Google account.

    This used to hardcode GoogleDrive-joe.bookout.carr.us@gmail.com as its only
    candidate, which made the check a FALSE GREEN anywhere else: no match ->
    find_vault() returns None -> main() prints "SKIP vault not reachable" and
    returns 0 -> bin/refresh-rules.sh, the one sanctioned way to refresh the
    rules, reads that as a pass. Dell's Mac is a different Google account, so
    the check that exists to keep tool-call markup out of the rules would have
    passed there without ever opening a file. Same resolver as
    ops/rule-enforcement-map-check.py: CARR_VAULT wins, then any GoogleDrive-*
    account, then the legacy ~/My Drive path.
    """
    configured = os.environ.get("CARR_VAULT")
    if configured:
        expanded = os.path.abspath(os.path.expanduser(configured))
        return expanded if os.path.isdir(expanded) else None
    home = os.path.expanduser("~")
    hits = sorted(glob(os.path.join(
        home, "Library", "CloudStorage", "GoogleDrive-*", "My Drive", "CARR AI"
    )))
    legacy = os.path.join(home, "My Drive", "CARR AI")
    if os.path.isdir(legacy):
        hits.append(legacy)
    return hits[0] if hits else None


def main() -> int:
    vault = find_vault()
    if not vault:
        print("rule-render-markup: SKIP vault not reachable")
        return 0

    renders = [os.path.join(vault, "DNA", "compiled-rules-shared.md"),
               os.path.join(vault, "00_Context", "compiled-rules-joe.md")]

    hits = []
    for path in renders:
        if not os.path.exists(path):
            print(f"rule-render-markup: SKIP {os.path.basename(path)} missing")
            return 0
        for n, line in enumerate(open(path), 1):
            if ALLOWED in line:
                continue
            if any(m in line for m in MARKERS):
                rid_match = re.search(r"`#([0-9a-f]{8})`", line)
                hits.append((os.path.basename(path), n,
                             rid_match.group(1) if rid_match else "unknown rule"))

    if not hits:
        print("rule-render-markup: OK no tool-call markup in any rule")
        return 0

    print(f"rule-render-markup: FAIL {len(hits)} rule line(s) carry tool-call markup")
    for name, n, rid in hits:
        print(f"  {name}:{n} rule {rid}")
    print("  A field swallowed its own closing tag; the parameters after it were "
          "written NULL. READ the absorbed text before deleting it — it IS the "
          "missing field's content, and amend-rule will fill a NULL human_quote. "
          "Repair procedure: rule c53beeaa.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
