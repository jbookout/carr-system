#!/usr/bin/env python3
"""boot-budget-check-selftest.py — the PAIRED suite for ops/boot-budget-check.py
(WR-000019 slice S11, boot diet).

Builds its own synthetic fixture tree on purpose (the established split, see
ops/ci.sh's inventory-checks comment): this measures the CHECK's behaviour,
never the real repository's actual numbers -- ops/boot-budget-check.py itself,
run directly against THIS repo by ops/ci.sh's inventory loop, is what proves
the real inventory is in budget.

THE ONE THING THIS FILE MUST PROVE, per WR-000019 slice S11's own acceptance
criterion: a synthetic overage actually fails the check, and the failure
message instructs consolidation rather than a budget raise.
"""
import json
import os
import sys
import tempfile
import shutil

REPO = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(REPO)
sys.path.insert(0, REPO)

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "boot_budget_check", os.path.join(REPO, "ops", "boot-budget-check.py"))
boot_budget_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(boot_budget_check)

FAILURES = []


def check(name, condition):
    if condition:
        print(f"  ok  {name}")
    else:
        print(f"  FAIL  {name}")
        FAILURES.append(name)


MCP_JS_TEMPLATE = """// synthetic mcp.js fixture
const RULE_DELIVERY_RAIL = `%(rail)s`;
function handler() {
  switch (rpc.method) {
    case "initialize":
      return reply({
        instructions:
          "%(instr)s" + RULE_DELIVERY_RAIL,
      });
  }
}
"""


def make_tree(tmp, *, claude_md_bytes, instr_chars, rail_chars,
              current_recitation_bytes, pack_index_bytes,
              total_budget_tokens, sub_budgets_tokens, bytes_per_token=3.5):
    claude_md = os.path.join(tmp, "CLAUDE.md")
    with open(claude_md, "w") as fh:
        fh.write("x" * claude_md_bytes)

    mcp_js = os.path.join(tmp, "mcp.js")
    with open(mcp_js, "w") as fh:
        fh.write(MCP_JS_TEMPLATE % {
            "rail": "r" * rail_chars,
            "instr": "i" * instr_chars,
        })

    fixture = os.path.join(tmp, "core-fixture.json")
    with open(fixture, "w") as fh:
        json.dump({
            "current_full_recitation_bytes": current_recitation_bytes,
            "pack_index_bytes": pack_index_bytes,
        }, fh)

    budget = os.path.join(tmp, "budget.json")
    with open(budget, "w") as fh:
        json.dump({
            "bytes_per_token": bytes_per_token,
            "total_budget_tokens": total_budget_tokens,
            "sub_budgets_tokens": sub_budgets_tokens,
        }, fh)

    return claude_md, mcp_js, fixture, budget


def test_passes_under_budget():
    tmp = tempfile.mkdtemp(prefix="boot-budget-pass-")
    try:
        claude_md, mcp_js, fixture, budget = make_tree(
            tmp, claude_md_bytes=350, instr_chars=100, rail_chars=50,
            current_recitation_bytes=1000, pack_index_bytes=200,
            total_budget_tokens=1000,
            sub_budgets_tokens={"claude_md": 500, "connector_instructions": 500,
                                 "core_payload": 500})
        b, surface_tokens, total, _ = boot_budget_check.measure(
            claude_md_path=claude_md, mcp_js_path=mcp_js,
            core_fixture_path=fixture, budget_path=budget)
        over = boot_budget_check.evaluate(b, surface_tokens, total)
        check("a modest synthetic tree passes with no overages", over == [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_synthetic_overage_fails_and_names_consolidation():
    """THE ACCEPTANCE CRITERION: a synthetic overage must fail the check, and
    the failure output must instruct consolidation, never a budget raise."""
    tmp = tempfile.mkdtemp(prefix="boot-budget-overage-")
    try:
        # CLAUDE.md alone is set to blow both its own sub-budget and the total.
        claude_md, mcp_js, fixture, budget = make_tree(
            tmp, claude_md_bytes=50_000, instr_chars=100, rail_chars=50,
            current_recitation_bytes=1000, pack_index_bytes=200,
            total_budget_tokens=1000,
            sub_budgets_tokens={"claude_md": 500, "connector_instructions": 500,
                                 "core_payload": 500})
        b, surface_tokens, total, _ = boot_budget_check.measure(
            claude_md_path=claude_md, mcp_js_path=mcp_js,
            core_fixture_path=fixture, budget_path=budget)
        over = boot_budget_check.evaluate(b, surface_tokens, total)
        check("a synthetic 50KB CLAUDE.md is reported over budget",
              any(name == "claude_md" for name, _, _ in over))
        check("the total is also reported over budget",
              any(name == "TOTAL" for name, _, _ in over))

        # main() is repo-relative by design (matching every other *-check.py
        # in this repo), so the CLI path itself is exercised separately in
        # test_main_exit_code_reflects_overage() by monkeypatching its module
        # path constants. Here, confirm main() prints the exact consolidation
        # message on overage.
        check("the consolidation advice text is present and names the triage + amendment path",
              "consolidation" in boot_budget_check.CONSOLIDATION_ADVICE.lower()
              and "rule-triage" in boot_budget_check.CONSOLIDATION_ADVICE
              and "amend-rule" in boot_budget_check.CONSOLIDATION_ADVICE)
        check("the consolidation advice never tells anyone to raise the budget",
              "raise the budget" not in boot_budget_check.CONSOLIDATION_ADVICE.lower()
              or "not a signal to raise" in boot_budget_check.CONSOLIDATION_ADVICE.lower())
        check("the advice explicitly reserves a raise for Joe alone",
              "joe" in boot_budget_check.CONSOLIDATION_ADVICE.lower())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_main_exit_code_reflects_overage():
    """Exercises the real main() entrypoint end to end (not just measure/evaluate),
    by pointing it at a repo root built to overage via monkeypatched module paths --
    this is the same technique the check's own module uses to stay import-safe."""
    tmp = tempfile.mkdtemp(prefix="boot-budget-main-")
    try:
        claude_md, mcp_js, fixture, budget = make_tree(
            tmp, claude_md_bytes=50_000, instr_chars=100, rail_chars=50,
            current_recitation_bytes=1000, pack_index_bytes=200,
            total_budget_tokens=1000,
            sub_budgets_tokens={"claude_md": 500, "connector_instructions": 500,
                                 "core_payload": 500})
        orig = (boot_budget_check.CLAUDE_MD_PATH, boot_budget_check.MCP_JS_PATH,
                boot_budget_check.CORE_FIXTURE_PATH, boot_budget_check.BUDGET_PATH)
        boot_budget_check.CLAUDE_MD_PATH = claude_md
        boot_budget_check.MCP_JS_PATH = mcp_js
        boot_budget_check.CORE_FIXTURE_PATH = fixture
        boot_budget_check.BUDGET_PATH = budget
        try:
            rc = boot_budget_check.main([])
        finally:
            (boot_budget_check.CLAUDE_MD_PATH, boot_budget_check.MCP_JS_PATH,
             boot_budget_check.CORE_FIXTURE_PATH, boot_budget_check.BUDGET_PATH) = orig
        check("main() exits nonzero on a synthetic overage", rc == 1)

        # And the inverse: a small tree under budget exits 0.
        claude_md2, mcp_js2, fixture2, budget2 = make_tree(
            tmp, claude_md_bytes=350, instr_chars=100, rail_chars=50,
            current_recitation_bytes=1000, pack_index_bytes=200,
            total_budget_tokens=1000,
            sub_budgets_tokens={"claude_md": 500, "connector_instructions": 500,
                                 "core_payload": 500})
        boot_budget_check.CLAUDE_MD_PATH = claude_md2
        boot_budget_check.MCP_JS_PATH = mcp_js2
        boot_budget_check.CORE_FIXTURE_PATH = fixture2
        boot_budget_check.BUDGET_PATH = budget2
        try:
            rc2 = boot_budget_check.main([])
        finally:
            (boot_budget_check.CLAUDE_MD_PATH, boot_budget_check.MCP_JS_PATH,
             boot_budget_check.CORE_FIXTURE_PATH, boot_budget_check.BUDGET_PATH) = orig
        check("main() exits zero when every surface is within budget", rc2 == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_extractor_is_honest_about_the_real_instructions_block():
    """Not synthetic: proves the connector_instructions extractor actually
    finds mcp-server/src/mcp.js's real `initialize` block and returns a
    plausible, nonzero size -- a silently-empty extraction (mcp.js renamed
    the handler, or restructured the string literals) must be caught here,
    not discovered the day the budget mysteriously reads as ~0 tokens."""
    real_mcp_js = os.path.join(REPO, "mcp-server", "src", "mcp.js")
    n = boot_budget_check.connector_instructions_bytes(real_mcp_js)
    check("the real connector instructions block extracts to a plausible size",
          500 < n < 20_000)


if __name__ == "__main__":
    test_passes_under_budget()
    test_synthetic_overage_fails_and_names_consolidation()
    test_main_exit_code_reflects_overage()
    test_extractor_is_honest_about_the_real_instructions_block()
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S): {FAILURES}")
        sys.exit(1)
    print("\nOK: boot-budget-check selftest passed")
    sys.exit(0)
