#!/usr/bin/env python3
"""boot-budget-check.py — the permanent ceiling on what every session loads
before it does any work (WR-000019 slice S11, boot diet).

WHY. The standing-context boot payload used to recite ~205 rule gists on
every call, CLAUDE.md carries its own weight, and the MCP connector's
`initialize` response repeats a ~200-word instruction block once per
registration. Nothing capped the sum, so it only ever grew. This is that cap,
enforced the same way ops/enforcement-coverage-check.py and its neighbours
are: repository content only, no database, no network, no machine state --
so it runs the same under `env -i` on a bare checkout as it does on Joe's
Mac, and it is one of the "map checks" ops/ci.sh's inventory loop runs
directly against THIS repo (see ops/boot-budget-check-selftest.py for the one
that builds a synthetic fixture tree instead, per the established split).

THREE SURFACES, matching ops/config/boot-budget.v1.json's sub_budgets_tokens:
  * claude_md               -- CLAUDE.md's own byte length.
  * connector_instructions  -- the `initialize` instructions string literal
                                 in mcp-server/src/mcp.js (plus RULE_DELIVERY_RAIL),
                                 measured ONCE. It is delivered a second time per
                                 duplicate MCP registration (see WR-000019 slice
                                 S11's report on the connector dedup finding) --
                                 that duplication is a CLIENT-config fact this
                                 repository-content check cannot see, so it is
                                 reported separately, never folded into this number.
  * core_payload            -- what standing-context loads TODAY: the full
                                 gist recitation plus the pack/trigger index,
                                 read from a committed SNAPSHOT
                                 (ops/config/boot-budget-core-fixture.v1.json)
                                 because this check has no database to call
                                 standing-context with. NOT the same number as
                                 standing-context's own core_preview -- that is
                                 the LARGER, separate measurement of what an
                                 enforced (post-S13-flip) boot would cost, and
                                 folding it in here would fail this check before
                                 the flip has even happened. Refresh the fixture,
                                 and revisit this budget's sub-budget, once S13
                                 actually flips delivery -- that is Joe's call.

THE FAILURE MESSAGE NEVER SUGGESTS RAISING THE BUDGET. An overage means
something is due for consolidation -- merge or retire a rule through the S7
triage (ops/config/rule-triage.v1.json) and the S10 amendment path
(amend-rule / retire-rule), trim CLAUDE.md, or fix a known duplication. A
budget raise is Joe's decision alone; this check will not word its way
around that by suggesting one.

Usage:
    ./.venv/bin/python ops/boot-budget-check.py
"""
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLAUDE_MD_PATH = os.path.join(REPO, "CLAUDE.md")
MCP_JS_PATH = os.path.join(REPO, "mcp-server", "src", "mcp.js")
BUDGET_PATH = os.path.join(REPO, "ops", "config", "boot-budget.v1.json")
CORE_FIXTURE_PATH = os.path.join(REPO, "ops", "config", "boot-budget-core-fixture.v1.json")

CONSOLIDATION_ADVICE = (
    "This is not a signal to raise the budget. The fix is consolidation: merge or\n"
    "retire redundant/stale rules through the S7 triage (ops/config/rule-triage.v1.json)\n"
    "and the S10 amendment path (amend-rule / retire-rule), trim CLAUDE.md, or close a\n"
    "known duplication (see the WR-000019 slice S11 connector-dedup finding). Raising\n"
    "any number in ops/config/boot-budget.v1.json is Joe's decision alone, never a\n"
    "session's -- do not edit that file to make this check pass."
)


def claude_md_bytes(path):
    with open(path, "rb") as fh:
        return len(fh.read())


def connector_instructions_bytes(path):
    """Byte length of the `initialize` instructions block mcp.js serves --
    the string-literal concatenation plus the separate RULE_DELIVERY_RAIL
    template literal it appends. Measures ONE registration; see the module
    docstring on why a second registration's duplicate cost is not folded in
    here."""
    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read()
    rail = ""
    rail_match = re.search(r"const RULE_DELIVERY_RAIL = `(.*?)`;", src, re.S)
    if rail_match:
        rail = rail_match.group(1)
    if "instructions:" not in src:
        raise ValueError(f"{path}: no `instructions:` block found -- has the "
                          "initialize handler moved or been renamed?")
    start = src.index("instructions:")
    end_marker = "});"
    if end_marker not in src[start:]:
        raise ValueError(f"{path}: `instructions:` block never closes with `{end_marker}`")
    end = src.index(end_marker, start)
    chunk = src[start:end]
    literals = re.findall(r'"((?:[^"\\]|\\.)*)"', chunk)
    joined = "".join(s.replace('\\"', '"') for s in literals)
    if not joined.strip():
        raise ValueError(f"{path}: extracted an empty instructions block -- "
                          "the string-literal shape probably changed")
    return len((joined + rail).encode("utf-8"))


def core_payload_bytes(fixture_path):
    with open(fixture_path) as fh:
        fixture = json.load(fh)
    return fixture["current_full_recitation_bytes"] + fixture["pack_index_bytes"]


def load_budget(path):
    with open(path) as fh:
        return json.load(fh)


def measure(claude_md_path=None, mcp_js_path=None,
            core_fixture_path=None, budget_path=None):
    """Returns (budget_dict, surface_tokens_dict, total_tokens).

    Defaults resolve the module-level path constants AT CALL TIME (never
    baked into the signature) so a test can monkeypatch CLAUDE_MD_PATH et al.
    on this module and have main() actually pick it up -- a default bound at
    def-time would silently keep pointing at whatever the constant was when
    the module loaded, which is exactly the kind of untestable check this
    file exists to not be."""
    claude_md_path = claude_md_path or CLAUDE_MD_PATH
    mcp_js_path = mcp_js_path or MCP_JS_PATH
    core_fixture_path = core_fixture_path or CORE_FIXTURE_PATH
    budget_path = budget_path or BUDGET_PATH
    budget = load_budget(budget_path)
    bpt = float(budget.get("bytes_per_token", 3.5))
    surface_bytes = {
        "claude_md": claude_md_bytes(claude_md_path),
        "connector_instructions": connector_instructions_bytes(mcp_js_path),
        "core_payload": core_payload_bytes(core_fixture_path),
    }
    surface_tokens = {name: b / bpt for name, b in surface_bytes.items()}
    total_tokens = sum(surface_tokens.values())
    return budget, surface_tokens, total_tokens, surface_bytes


def evaluate(budget, surface_tokens, total_tokens):
    """Returns a list of (surface_or_'TOTAL', measured, cap) tuples that are
    over budget. Empty list means pass."""
    over = []
    sub_budgets = budget.get("sub_budgets_tokens", {})
    for name, measured in surface_tokens.items():
        cap = sub_budgets.get(name)
        if cap is not None and measured > cap:
            over.append((name, measured, cap))
    total_cap = budget.get("total_budget_tokens")
    if total_cap is not None and total_tokens > total_cap:
        over.append(("TOTAL", total_tokens, total_cap))
    return over


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    budget, surface_tokens, total_tokens, surface_bytes = measure()

    print("BOOT BUDGET (WR-000019 slice S11)")
    for name in ("claude_md", "connector_instructions", "core_payload"):
        cap = budget.get("sub_budgets_tokens", {}).get(name)
        cap_s = f"(budget {cap})" if cap is not None else "(no sub-budget set)"
        print(f"  {name:24s} {surface_bytes[name]:7d} bytes  "
              f"~{surface_tokens[name]:8.1f} tokens  {cap_s}")
    total_cap = budget.get("total_budget_tokens")
    print(f"  {'TOTAL':24s} {'':7s}         ~{total_tokens:8.1f} tokens  "
          f"(budget {total_cap})")
    print("  NOTE: connector_instructions is ONE registration's cost. This "
          "session's live tool list may carry it twice if the CARR connector "
          "is registered under two MCP prefixes -- see the WR-000019 slice "
          "S11 dedup finding; this check cannot see a client-side duplicate "
          "registration and does not estimate one.")

    over = evaluate(budget, surface_tokens, total_tokens)
    if over:
        print("\nBOOT BUDGET EXCEEDED:")
        for name, measured, cap in over:
            print(f"  {name}: ~{measured:.1f} tokens > budget {cap}")
        print()
        print(CONSOLIDATION_ADVICE)
        return 1

    print("\nOK: within budget.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
