#!/usr/bin/env python3
"""A test fake may not pin the column list of the tool_call replay lookup.

WHY THIS EXISTS. Every verb that writes goes through one replay lookup against
public.tool_call, and test fakes recognise it by matching the start of the SQL.
Matching on `select request_hash, response from tool_call` pins the whole column
list: the literal only matches while the select selects exactly those two
columns, so the next branch to add a column silently stops matching every fake
that pinned it.

WHAT THAT COSTS, measured on 2026-08-21 rather than imagined. A branch widened
that select to carry identity — actor_id, organization_tenant_id and
application_session_id — and 26 test files had the pin:

    10  throw `unhandled fake query` and fail outright. The test then fails on
        the wrong error entirely, reporting an unhandled query while saying
        nothing about the behaviour it exists to check.
     1  work-shape asserts on the SQL source text with indexOf and compares
        positions; indexOf returns -1 and the lock-ordering assertion fails.
     9  return a STORED prior call. These are the quiet ones. After the widening
        they fall through to an empty result, so a replay check comparing actor,
        tenant or session reaches a verdict against a row that has forgotten who
        made it, rather than throwing where somebody would notice.

Neither side is wrong on its own, which is why nothing catches it until a merge:
the widening branch passes, main passes, and the failure exists only once they
meet.

WHY A CHECK AND NOT A SWEEP. All 26 were fixed. Two new files carried the same
pin within hours, written by sessions that had no way to know it was a trap —
the pattern gets copied from whichever neighbouring fake is open at the time. A
cleanup removes today's instances; only a check that refuses the pattern stops
tomorrow's.

THE RULE. A matcher literal must stop at the columns that do not move. The head
`select request_hash, response` is stable, so a fake may match exactly that and
nothing further. Anything continuing past it — more columns, or ` from
tool_call` — is pinned and is refused here.

This reads the repository's own test sources. It needs no database, no network
and no credential, so it runs anywhere the checkout does.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parents[1]
TEST_DIR = REPO / "mcp-server" / "test"

# The stable head, and the only form a fake may match on.
STABLE_HEAD = "select request_hash, response"

# A pin is the stable head followed by anything other than the end of the
# literal — another column, or the FROM clause. The closing quote is what tells
# an honest prefix from a pinned one, so the quote character is part of the
# pattern rather than something stripped beforehand.
PINNED = re.compile(
    r"""(?P<quote>["'`])select\s+request_hash,\s*response(?P<tail>\s*,|\s+from)"""
)

checks = 0
failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    global checks
    checks += 1
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        failures.append(label)
        for line in detail.splitlines():
            print("       " + line)
    return ok


def offenders() -> list[tuple[Path, int, str]]:
    """Every pinned matcher in the test tree, with its line and the text."""
    found: list[tuple[Path, int, str]] = []
    for path in sorted(TEST_DIR.rglob("*")):
        if not path.is_file() or path.suffix not in (".mjs", ".js"):
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if PINNED.search(line):
                found.append((path, lineno, line.strip()))
    return found


def main() -> int:
    if not TEST_DIR.is_dir():
        print(f"replay-fake-column-pin: {TEST_DIR} is missing", file=sys.stderr)
        return 69

    # THE PATTERN ITSELF IS PROVEN FIRST, on strings built here rather than on
    # whatever the tree happens to hold. A checker that silently matches nothing
    # passes every scan it runs, which is the failure mode this file exists to
    # prevent in others.
    pinned_forms = [
        ('sql.startsWith("select request_hash, response from tool_call")',
         "the two-column form that broke 26 files"),
        ('body.indexOf("select request_hash, response from tool_call")',
         "the same pin reached through indexOf on the source text"),
        ('sql.startsWith("select request_hash, response, actor_id from tool_call")',
         "pinning a WIDER column list is the same defect, one column later"),
        ("sql.startsWith(`select request_hash, response from tool_call`)",
         "a template literal pins exactly as hard as a quoted string"),
    ]
    for text, why in pinned_forms:
        check(f"refused: {why}", bool(PINNED.search(text)),
              f"this checker did not flag a pinned matcher:\n  {text}")

    allowed_forms = [
        'sql.startsWith("select request_hash, response")',
        'body.indexOf("select request_hash, response")',
        "sql.startsWith(`select request_hash, response`)",
    ]
    for text in allowed_forms:
        check("allowed: a matcher stopping at the stable head",
              not PINNED.search(text),
              f"this checker flagged an honest prefix:\n  {text}")

    # The real query in src is allowed to name its columns in full — it IS the
    # column list. Only the fakes that recognise it are constrained.
    check("the checker looks only at the test tree",
          "src" not in TEST_DIR.parts[-1:],
          "TEST_DIR must point at mcp-server/test")

    found = offenders()
    detail = "\n".join(
        f"{p.relative_to(REPO)}:{n}\n    {t}" for p, n, t in found)
    check(
        "no test fake pins the replay lookup's column list",
        not found,
        (f"{len(found)} pinned matcher(s). Match on "
         f'"{STABLE_HEAD}" and stop there — it fits the current query and every\n'
         "widening of it. A fake that stores a tool_call should also keep\n"
         "actor_id, organization_tenant_id and application_session_id from the\n"
         "insert's own parameter order, so a replay check is judged against a\n"
         "row that carries an identity.\n\n" + detail) if found else "")

    passed = checks - len(failures)
    print(f"passed {passed} · failed {len(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
