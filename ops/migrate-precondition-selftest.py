#!/usr/bin/env python3
"""The data-dependent-migration table is a precondition, never a skip-on-failure.

WHAT IT EXISTS FOR (defect 87da6fe5, 2026-08-22). Migration 0248 binds the
conduct-stop control to the autonomy rule, with an insert requiring that rule to
exist as `proposed` and a proof block that raises when the insert matched
nothing. That row exists in Production and nowhere else, so 0248 applied cleanly
to Production and then failed on the isolated staging project — which stopped at
206 applied while Production reached 209.

The cost was the whole release chain. The typed staging readback compares
staging's schema against a candidate's exact declared migration set, so it could
never match; the recovery rehearsal could not complete; and Production approval
refuses without a rehearsal bundle. The repository also stopped being able to
reconstruct a non-production environment, which is Program 1's rebuild clause.

WHAT THIS SUITE PINS, because the table is one edit away from being a door that
lets any red migration through:

  1. The probe runs BEFORE the file, not after it fails. A migration that errors
     for any other reason must still stop the run exactly as it did.
  2. A present precondition means the file APPLIES. The table must not turn into
     "never run 0248".
  3. An absent precondition records the file with its real sha256, so the ledger
     stays honest and validate_applied_ledger keeps working.
  4. Every entry names a file that exists, a probe that is a single read, and a
     stated reason. An entry for a file nobody ships is a stale exemption.

Run: .venv/bin/python ops/migrate-precondition-selftest.py
"""

from __future__ import annotations

import pathlib
import re
import sys
import hashlib

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import migrate  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{'' if ok else '  — ' + detail}")


class FakeCursor:
    """Answers exactly the calls the apply loop makes, and records them."""

    def __init__(self, probe_returns_row: bool, raise_on_apply: Exception | None = None):
        self._probe_row = probe_returns_row
        self._raise = raise_on_apply
        self.executed: list[str] = []
        self._last_was_probe = False

    def execute(self, sql, params=None):
        text = " ".join(str(sql).split())
        self.executed.append(text)
        self._last_was_probe = text.lower().startswith("select 1 from")
        if self._raise is not None and text.startswith("--") is False \
           and "insert into schema_migrations" not in text \
           and not self._last_was_probe and "set local" not in text.lower():
            raise self._raise

    def fetchone(self):
        if self._last_was_probe:
            return (1,) if self._probe_row else None
        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_table_shape() -> None:
    table = migrate.DATA_DEPENDENT_MIGRATIONS
    check("the table is not empty and not a wildcard", bool(table) and isinstance(table, dict))
    for name, value in table.items():
        path = REPO / "migrations" / name
        check(f"{name} names a migration that exists", path.is_file(),
              "an entry for a file nobody ships is a stale exemption")
        ok_shape = isinstance(value, tuple) and len(value) == 2 and all(
            isinstance(v, str) and v.strip() for v in value)
        check(f"{name} carries a probe and a stated reason", ok_shape)
        if not ok_shape:
            continue
        probe, reason = value
        check(f"{name}'s probe is a single read",
              probe.lower().lstrip().startswith("select") and ";" not in probe.strip().rstrip(";"),
              "a probe that can write, or chains statements, is not a precondition")
        check(f"{name}'s reason explains why the file is inert without its row",
              len(reason) > 40)


def test_probe_is_checked_before_the_file_runs() -> None:
    """The ordering IS the safety property: a precondition, not a rescue."""
    cur = FakeCursor(probe_returns_row=False)
    name = next(iter(migrate.DATA_DEPENDENT_MIGRATIONS))
    probe, _ = migrate.DATA_DEPENDENT_MIGRATIONS[name]
    cur.execute(probe)
    check("an absent precondition is detectable before applying", cur.fetchone() is None)

    cur2 = FakeCursor(probe_returns_row=True)
    cur2.execute(probe)
    check("a present precondition is detectable before applying", cur2.fetchone() is not None)


def test_source_enforces_the_contract() -> None:
    """Read the apply loop itself: the guard must key on the probe, not on an
    exception handler. A version that caught the migration's error and carried on
    would pass every behavioural test above while being the door this must not be."""
    src = (REPO / "tools" / "migrate.py").read_text(encoding="utf-8")
    loop = src[src.index("for name, sql, digest in pending:"):]
    check("the guard consults DATA_DEPENDENT_MIGRATIONS inside the apply loop",
          "DATA_DEPENDENT_MIGRATIONS.get(name)" in loop)
    check("the discharge path runs the probe and tests for no row",
          re.search(r"cur\.execute\(probe\)\s*\n\s*if cur\.fetchone\(\) is None:", loop) is not None)
    # SCOPED TO THE DISCHARGE BLOCK. Checking the whole loop passes trivially,
    # because the ordinary apply path records (name, digest) too — a first
    # version of this assertion did exactly that and survived a mutation that
    # wrote a literal placeholder into the ledger on the discharge path.
    discharge = loop[loop.index("if cur.fetchone() is None:"):loop.index("continue")]
    check("the discharge records the real sha256, not a placeholder",
          "insert into schema_migrations (filename, sha256) values (%s, %s)" in discharge
          and "(name, digest)" in discharge,
          "a discharged row with a fake sha makes validate_applied_ledger reject the tree later")
    check("nothing swallows a migration exception into a discharge",
          "except Exception" not in loop and "except psycopg.Error" not in loop,
          "a broad handler here would turn this table into skip-on-failure")
    check("the two documented timeout handlers still fail the run",
          "LockNotAvailable" in loop and "QueryCanceled" in loop and loop.count("fail(") >= 2)
    check("0339 and later migrations reject their own transaction control",
          migrate.OUTER_TRANSACTION_MIGRATION == "0339_"
          and all(migrate.contains_transaction_control(statement) for statement in (
              "begin;", "begin transaction;", " COMMIT;", "commit work;",
              "start transaction;", "rollback;", "rollback work;", "end;",
              "select 1; commit; select 2;", "commit; select 1;",
              "begin; select 1;", "rollback transaction;", "/*x*/ commit;",
              "commit and chain;", "commit work and no chain;",
              "rollback and chain;", "abort;", "abort work;",
              "prepare transaction 'x';", "commit prepared 'x';"))
          and not any(migrate.contains_transaction_control(statement) for statement in (
              "-- begin;\nselect 1;", "select 'commit;';", 'select "rollback";',
              "do $$ begin perform 1; end $$;", "/* commit; */ select 1;")),
          "an internal commit would expose schema before its ledger-bound epoch")


def test_historical_transaction_artifact_is_exact() -> None:
    artifacts = migrate.HISTORICAL_TRANSACTION_CONTROL_ARTIFACTS
    expected_names = {
        "0344_demote_evidence_activation_bookkeeping.sql",
        "0345_governance_queue_projection.sql",
        "0348_pr_only_main_ruleset_control.sql",
        "0349_versioned_rule_amendment.sql",
        "0351_legacy_rule_lifecycle_admission.sql",
    }
    check("the historical transaction-control grandfather is five exact files",
          set(artifacts) == expected_names,
          "the exception must never widen to another migration")
    exact = True
    drift_refused = True
    for name in expected_names:
        sql = (REPO / "migrations" / name).read_text(encoding="utf-8")
        digest = hashlib.sha256(sql.encode()).hexdigest()
        exact = exact and artifacts.get(name) == digest \
            and migrate.contains_transaction_control(sql)
        changed_digest = hashlib.sha256((sql + "\n-- drift probe").encode()).hexdigest()
        drift_refused = drift_refused and artifacts.get(name) != changed_digest
    check("every grandfather digest equals its immutable applied artifact", exact,
          "an edited or transaction-free file must not match a historical exception")
    check("one-byte drift cannot reuse any historical grandfather", drift_refused)


def test_reviewed_controller_transaction_artifact_is_exact() -> None:
    artifacts = migrate.REVIEWED_TRANSACTION_CONTROL_ARTIFACTS
    expected_names = {
        "0363_rule_delivery_activation_digest_repin.sql",
        "0382_standing_guidance_reader_boundary.sql",
        "0383_control_plane_not_configured_state.sql",
        "0387_control_plane_record_queue_priority_tiers.sql",
    }
    check("the reviewed controller transaction allowlist is four exact artifacts",
          set(artifacts) == expected_names
          and not set(artifacts) & set(migrate.HISTORICAL_TRANSACTION_CONTROL_ARTIFACTS))
    exact = True
    drift_refused = True
    for name in expected_names:
        sql = (REPO / "migrations" / name).read_text(encoding="utf-8")
        digest = hashlib.sha256(sql.encode()).hexdigest()
        changed_digest = hashlib.sha256((sql + "\n-- drift probe").encode()).hexdigest()
        exact = exact and artifacts.get(name) == digest \
            and migrate.contains_transaction_control(sql)
        drift_refused = drift_refused and artifacts.get(name) != changed_digest
    check("each controller digest equals its reviewed source artifact", exact)
    check("one-byte drift cannot reuse any controller transaction review", drift_refused)


def main() -> int:
    print("migrate-precondition-selftest")
    test_table_shape()
    test_probe_is_checked_before_the_file_runs()
    test_source_enforces_the_contract()
    test_historical_transaction_artifact_is_exact()
    test_reviewed_controller_transaction_artifact_is_exact()
    print()
    print(f"migrate-precondition-selftest: {len(PASS)}/{len(PASS) + len(FAIL)} passed")
    if FAIL:
        print("FAILURES: " + ", ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
