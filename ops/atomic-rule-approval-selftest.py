#!/usr/bin/env python3
"""Static contract: approval is impossible without immediate enforcement."""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MIGRATION = REPO / "migrations" / "0185_atomic_rule_approval.sql"


def main() -> int:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    failures: list[str] = []

    def check(name: str, condition: bool) -> None:
        print(f"  {'ok  ' if condition else 'FAIL'} {name}")
        if not condition:
            failures.append(name)

    check("one authority function owns approval",
          "function ops.approve_rule(" in sql and "ops.authority_actor_slug()" in sql)
    check("Joe is the sole required system-rule authority",
          "if v_actor_slug <> 'joe' then" in sql
          and "cannot replace Joe approval".lower() in sql)
    check("approval means active and enforced in one transaction",
          "'policy_status','active'" in sql
          and "'enforcement_status',v_status" in sql
          and "update rule" in sql)
    check("missing controls refuse approval instead of creating a waiting approval",
          "rule approval refused: exact enforcement is not installed" in sql
          and "rule_enforcement_backlog" not in sql
          and "finalize_rule_approval" not in sql)
    check("active transition requires immutable enforced approval receipt",
          "rule_approval_receipt_append_only" in sql
          and "immutable enforced approval receipt is missing" in sql
          and "active requires installed enforcement" in sql)
    check("callers name registered controls instead of implementation prose",
          "enforcement_control_catalog" in sql
          and "and c.control_key=any(v_requested)" in sql
          and "p_implementation_ref" not in sql)
    check("a real control cannot be claimed for an unrelated rule or statement",
          "rule_control_binding" in sql
          and "b.rule_id=p_rule_id" in sql
          and "b.statement_hash=encode(digest(v_rule.statement,'sha256'),'hex')" in sql)
    check("cost control is registered with implementation and tests",
          "platform_metering_pre_dispatch" in sql
          and "ops/platform-metering-gate-selftest.py" in sql)
    check("Joe's existing cost rule is deployment-bound without re-teaching it",
          "ae44e0c0-e773-456c-a85b-2dc4cf4dd49e" in sql
          and "4a0e59ce-728a-49b5-a055-116156e9470e" in sql
          and "function ops.sync_system_rule_control_bindings()" in sql
          and "select ops.sync_system_rule_control_bindings()" in sql
          and "r.status='proposed'" in sql)
    check("routine roles cannot approve rules",
          "from public,carr_reader,carr_writer,carr_jobs" in sql
          and "grant execute on function ops.approve_rule" in sql
          and "to carr_authority" in sql)
    check("approval replay is input-bound",
          "is distinct from p_rule_id" in sql
          and "is distinct from v_requested" in sql
          and "idempotency key was reused with different input" in sql)
    check("advisory prose cannot be approved as an unbreakable rule",
          "advisory guidance is not an unbreakable rule" in sql
          and "standing_context_runtime" in sql
          and "mislabeled as unbreakable enforcement" in sql)
    check("migration invariants run before commit", sql.rfind("do $$") < sql.rfind("commit;"))

    print(f"\natomic-rule-approval-selftest: {13-len(failures)}/13 passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
