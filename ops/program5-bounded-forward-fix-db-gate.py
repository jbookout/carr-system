# ci: db-gate
"""Disposable-Postgres security contract for 0315a's bounded staging proof.

This runs after the migration harness has compiled/applied every pending SQL
file.  It temporarily removes only 0316/0317 *ledger rows* in the disposable
database to model the clean replacement staging prefix.  It is not a migration
rehearsal and never claims that an empty database can replay historical CARR
data migrations.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import uuid

try:
    import psycopg
except ImportError:
    sys.exit("program5-bounded-forward-fix-db-gate: psycopg not installed")


REPO = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("program5_base_gate", REPO / "ops" / "staging-release-readback-gate.py")
if spec is None or spec.loader is None:
    sys.exit("program5-bounded-forward-fix-db-gate: cannot load base fixture")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

PASSES: list[str] = []
FAILURES: list[str] = []
HELD = ("0316_rule_delivery_audit_counts.sql", "0317_atomic_rule_delivery_cutover.sql")


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSES.append(name)
        print(f"  ok    {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


def one(cur):
    row = cur.fetchone()
    if row is None:
        raise AssertionError("database returned no row")
    return row


def refuses(cur, statement: str, params: tuple = ()) -> bool:
    cur.execute("savepoint bounded_refusal")
    try:
        cur.execute(statement, params)
    except psycopg.Error:
        cur.execute("rollback to savepoint bounded_refusal")
        return True
    cur.execute("rollback to savepoint bounded_refusal")
    return False


def jobs(cur) -> None:
    cur.execute("set session authorization carr_jobs")


def verifier(cur) -> None:
    cur.execute("set session authorization carr_program5_forward_fix_verifier")


def owner(cur) -> None:
    cur.execute("reset session authorization")


def ensure_verifier(cur) -> None:
    cur.execute("create role carr_program5_forward_fix_verifier nologin")
    cur.execute("grant carr_program5_forward_fix_verifiers to carr_program5_forward_fix_verifier")


def forward_fixture(cur, suffix: str) -> dict:
    fixture = base.seed_fixture(cur, suffix)
    cur.execute("update ops.release set recovery_strategy='forward_fix' where id=%s", (fixture["current_id"],))
    return fixture


def prepare_attempt(cur, fixture: dict, idem: uuid.UUID) -> dict:
    cur.execute("select ops.prepare_staging_forward_fix_rehearsal(%s::uuid,%s::uuid,%s,%s)",
                (idem, uuid.uuid4(), fixture["current_key"], base.CURRENT_SHA))
    return one(cur)[0]


def full_ledger(cur) -> list[tuple[str, str]]:
    cur.execute("select filename,sha256 from public.schema_migrations order by filename collate \"C\"")
    return list(cur.fetchall())


def remove_held_rows(cur) -> list[tuple[str, str]]:
    rows = full_ledger(cur)
    held = [row for row in rows if row[0] in HELD]
    if [name for name, _digest in held] != list(HELD):
        raise AssertionError("disposable migration ledger lacks exact 0316/0317 suffix")
    cur.execute("delete from public.schema_migrations where filename = any(%s)", (list(HELD),))
    return held


def restore_held_rows(cur, rows: list[tuple[str, str]]) -> None:
    cur.executemany("insert into public.schema_migrations(filename,sha256) values(%s,%s)", rows)


def prepare_contract(cur, idem: uuid.UUID, held: list[tuple[str, str]]) -> dict:
    cur.execute("select ops.prepare_staging_forward_fix_bounded_contract(%s::uuid,%s::text[],%s::text[])",
                (idem, [name for name, _digest in held], [digest for _name, digest in held]))
    return one(cur)[0]


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("program5-bounded-forward-fix-db-gate: DATABASE_URL is not set")
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        base.ensure_authority_roles(cur)
        ensure_verifier(cur)
        cur.execute("select to_regprocedure('ops.prepare_staging_forward_fix_bounded_contract(uuid,text[],text[])')")
        check("0315a compiled the DB-derived three-argument prepare door", one(cur)[0] is not None)
        cur.execute("select to_regprocedure('ops.program5_bounded_staging_forward_fix_rehearsal(uuid)')")
        check("0315a compiled the staging-only bounded predicate", one(cur)[0] is not None)
        cur.execute("""select
          has_function_privilege('carr_jobs','ops.prepare_staging_forward_fix_bounded_contract(uuid,text[],text[])'::regprocedure,'execute'),
          has_function_privilege('carr_reader','ops.prepare_staging_forward_fix_bounded_contract(uuid,text[],text[])'::regprocedure,'execute'),
          has_function_privilege('carr_jobs','ops.program5_bounded_staging_forward_fix_rehearsal(uuid)'::regprocedure,'execute'),
          has_function_privilege('carr_program5_forward_fix_verifier','ops.record_staging_bounded_forward_fix_rehearsal(uuid,uuid,text,integer,text,integer,text,bigint,boolean)'::regprocedure,'execute'),
          has_function_privilege('carr_jobs','ops.record_staging_bounded_forward_fix_rehearsal(uuid,uuid,text,integer,text,integer,text,bigint,boolean)'::regprocedure,'execute')""")
        check("prepare is carr_jobs-only (no inherited PUBLIC execute); bounded predicate is not public; recorder is verifier-only",
              one(cur) == (True, False, False, True, False))

        fixture = forward_fixture(cur, "bounded-main")
        idem = uuid.uuid4()
        jobs(cur)
        prepared = prepare_attempt(cur, fixture, idem)
        owner(cur)
        held = remove_held_rows(cur)
        jobs(cur)
        bound = prepare_contract(cur, idem, held)
        replay = prepare_contract(cur, idem, held)
        check("DB derives and replays one canonical bounded contract",
              isinstance(bound.get("contract_sha256"), str)
              and bound.get("replayed") is False and replay.get("replayed") is True)
        check("carr_jobs cannot forge changed held-back hashes", refuses(
            cur, "select ops.prepare_staging_forward_fix_bounded_contract(%s::uuid,%s::text[],%s::text[])",
            (idem, list(HELD), ["0" * 64, held[1][1]])))
        check("legacy caller-scalar overload is not executable by carr_jobs", refuses(
            cur, "select ops.prepare_staging_forward_fix_bounded_contract(%s::uuid,%s,%s,%s::uuid,%s,%s,%s,%s::text[],%s,%s::text[],%s)",
            (uuid.uuid4(), "sha256:" + "0" * 64, "sha256:" + "0" * 64, uuid.uuid4(),
             "0315a_program5_bounded_forward_fix_rehearsal.sql", 1, "sha256:" + "0" * 64,
             ["0315_program5_forward_fix_rehearsal.sql", "0315a_program5_bounded_forward_fix_rehearsal.sql"],
             "sha256:" + "0" * 64, list(HELD), "sha256:" + "0" * 64)))
        check("carr_jobs cannot directly append a bounded contract", refuses(
            cur, "insert into ops.staging_forward_fix_bounded_contract(rehearsal_attempt_id,contract_sha256,source_artifact_digest,source_schema_highest_migration,source_schema_applied_count,source_schema_ledger_sha256,target_schema_highest_migration,target_schema_applied_count,target_schema_ledger_sha256,selected_migrations,selected_ordinals,selected_migrations_sha256,held_back_migrations,held_back_ordinals,held_back_migrations_sha256) values(%s,%s,%s,%s,1,%s,%s,1,%s,%s,array[1,2],%s,%s,array[3,4],%s)",
            (uuid.uuid4(), "sha256:" + "0" * 64, "sha256:" + "0" * 64, "0317_atomic_rule_delivery_cutover.sql", "sha256:" + "0" * 64,
             "0315a_program5_bounded_forward_fix_rehearsal.sql", "sha256:" + "0" * 64,
             ["0315_program5_forward_fix_rehearsal.sql", "0315a_program5_bounded_forward_fix_rehearsal.sql"], "sha256:" + "0" * 64,
             list(HELD), "sha256:" + "0" * 64)))
        owner(cur)
        cur.execute("create temporary table bounded_contract_shape (like ops.staging_forward_fix_bounded_contract including constraints) on commit drop")
        cur.execute("insert into bounded_contract_shape select * from ops.staging_forward_fix_bounded_contract where id=%s",
                    (bound["bounded_forward_fix_contract_id"],))
        check("durable bounded contract refuses ordinal tampering", refuses(
            cur, "update bounded_contract_shape set selected_ordinals=array[1,2]"))
        jobs(cur)
        check("carr_jobs cannot read verifier-only bounded projection", refuses(
            cur, "select * from ops.read_staging_forward_fix_bounded_contract(%s::uuid)", (idem,)))
        check("carr_jobs cannot call the bounded result recorder", refuses(
            cur, "select ops.record_staging_bounded_forward_fix_rehearsal(%s::uuid,%s::uuid,%s,211,%s,%s,%s,170,false)",
            (idem, uuid.uuid4(), prepared["expected_provider_tag"],
             "0315a_program5_bounded_forward_fix_rehearsal.sql", 1, "sha256:" + "0" * 64)))
        verifier(cur)
        cur.execute("""select expected_provider_tag,contract_sha256,target_schema_highest_migration,
                              target_schema_applied_count,target_schema_ledger_sha256
                         from ops.read_staging_forward_fix_bounded_contract(%s::uuid)""", (idem,))
        projection = one(cur)
        check("scoped verifier reads only the durable bounded declaration",
              projection[:2] == (prepared["expected_provider_tag"], bound["contract_sha256"]))
        check("verifier cannot invoke the carr_jobs contract writer", refuses(
            cur, "select ops.prepare_staging_forward_fix_bounded_contract(%s::uuid,%s::text[],%s::text[])",
            (idem, list(HELD), [digest for _name, digest in held])))
        owner(cur)
        jobs(cur)
        cur.execute("select ops.claim_staging_forward_fix_rehearsal(%s::uuid)", (idem,))
        check("claim occurs only after the immutable bounded contract", one(cur)[0]["mutation_allowed"] is True)
        owner(cur)
        verifier(cur)
        check("original 0315 full-tree recorder refuses a bounded-prefix readback", refuses(
            cur, "select ops.record_staging_forward_fix_rehearsal(%s::uuid,%s::uuid,%s,211,%s,%s,%s,170,false)",
            (idem, uuid.uuid4(), prepared["expected_provider_tag"], projection[2], projection[3], projection[4])))
        cur.execute("select ops.record_staging_bounded_forward_fix_rehearsal(%s::uuid,%s::uuid,%s,211,%s,%s,%s,170,false)",
                    (idem, uuid.uuid4(), prepared["expected_provider_tag"],
                     projection[2], projection[3], projection[4]))
        recorded = one(cur)[0]
        check("verifier records only the exact persisted bounded prefix", recorded.get("replayed") is False)
        owner(cur)
        cur.execute("select ops.program5_exact_recovery_rehearsal(%s::uuid),ops.program5_bounded_staging_forward_fix_rehearsal(%s::uuid)",
                    (fixture["current_id"], fixture["current_id"]))
        production_run, bounded_run = one(cur)
        check("bounded receipt is discoverable but excluded from Production predicate",
              production_run is None and str(bounded_run) == str(recorded["recovery_run_id"]))
        cur.execute("select run_key from ops.run where id=%s", (recorded["recovery_run_id"],))
        check("bounded receipt has its separate staging-only run key",
              one(cur)[0] == "recovery.rehearsal.forward-fix.bounded")
        cur.execute("select pg_get_functiondef('ops.prepare_staging_forward_fix_bounded_contract(uuid,text[],text[])'::regprocedure)")
        check("contract writer serializes idempotent concurrent callers", "pg_advisory_xact_lock" in one(cur)[0])

        # A separate prepared attempt proves the claim-before-contract stop.
        restore_held_rows(cur, held)

        # Frozen 0315 full-tree forward-fix remains a separate, production-
        # eligible shape: NULL bounded id, original run key and original
        # recorder. The new bounded recorder cannot consume it.
        legacy = forward_fixture(cur, "legacy-full-tree")
        legacy_idem = uuid.uuid4()
        jobs(cur)
        legacy_prepared = prepare_attempt(cur, legacy, legacy_idem)
        cur.execute("select ops.claim_staging_forward_fix_rehearsal(%s::uuid)", (legacy_idem,))
        owner(cur)
        verifier(cur)
        cur.execute("select ops.record_staging_forward_fix_rehearsal(%s::uuid,%s::uuid,%s,211,%s,%s,%s,170,false)",
                    (legacy_idem, uuid.uuid4(), legacy_prepared["expected_provider_tag"],
                     base.SCHEMA_HIGHEST_MIGRATION, base.SCHEMA_APPLIED_COUNT, base.SCHEMA_LEDGER_SHA256))
        legacy_recorded = one(cur)[0]
        check("original 0315 full-tree forward-fix remains independent", legacy_recorded.get("replayed") is False)
        check("bounded recorder refuses an original full-tree forward-fix row", refuses(
            cur, "select ops.record_staging_bounded_forward_fix_rehearsal(%s::uuid,%s::uuid,%s,211,%s,%s,%s,170,false)",
            (legacy_idem, uuid.uuid4(), legacy_prepared["expected_provider_tag"],
             base.SCHEMA_HIGHEST_MIGRATION, base.SCHEMA_APPLIED_COUNT, base.SCHEMA_LEDGER_SHA256)))
        owner(cur)
        cur.execute("select b.bounded_forward_fix_contract_id is null, r.run_key, ops.program5_exact_recovery_rehearsal(%s::uuid)=%s::uuid from ops.staging_recovery_rehearsal_bundle b join ops.run r on r.recovery_rehearsal_bundle_id=b.id where b.id=%s::uuid",
                    (legacy["current_id"], legacy_recorded["recovery_run_id"], legacy_recorded["bundle_id"]))
        check("original full-tree forward-fix has NULL bounded id and stays Production-eligible",
              one(cur) == (True, "recovery.rehearsal.forward-fix", True))

        rollback = base.seed_fixture(cur, "rollback-null")
        base.make_typed_bundle(cur, rollback)
        cur.execute("select bounded_forward_fix_contract_id is null,recovery_strategy from ops.staging_recovery_rehearsal_bundle where current_release_id=%s", (rollback["current_id"],))
        check("rollback shape remains NULL-bounded and unchanged", one(cur) == (True, "rollback"))

        later = forward_fixture(cur, "claim-first")
        later_idem = uuid.uuid4()
        jobs(cur)
        prepare_attempt(cur, later, later_idem)
        cur.execute("select ops.claim_staging_forward_fix_rehearsal(%s::uuid)", (later_idem,))
        owner(cur)
        held_again = remove_held_rows(cur)
        jobs(cur)
        check("claim-before-contract is refused", refuses(
            cur, "select ops.prepare_staging_forward_fix_bounded_contract(%s::uuid,%s::text[],%s::text[])",
            (later_idem, list(HELD), [digest for _name, digest in held_again])))
        owner(cur)
        restore_held_rows(cur, held_again)

        # Retaining a later row and a prefix hole each refuse before any claim.
        full = forward_fixture(cur, "later-and-hole")
        full_idem = uuid.uuid4()
        jobs(cur)
        prepare_attempt(cur, full, full_idem)
        check("later 0316/0317 ledger rows refuse the bounded prefix", refuses(
            cur, "select ops.prepare_staging_forward_fix_bounded_contract(%s::uuid,%s::text[],%s::text[])",
            (full_idem, list(HELD), [digest for _name, digest in held_again])))
        owner(cur)
        all_rows = full_ledger(cur)
        hole = next(row for row in all_rows if row[0] not in HELD and row[0] != "0315a_program5_bounded_forward_fix_rehearsal.sql")
        cur.execute("delete from public.schema_migrations where filename in (%s,%s,%s)",
                    (hole[0], HELD[0], HELD[1]))
        jobs(cur)
        check("prefix hole refuses even when later migrations are absent", refuses(
            cur, "select ops.prepare_staging_forward_fix_bounded_contract(%s::uuid,%s::text[],%s::text[])",
            (full_idem, list(HELD), [digest for _name, digest in held_again])))
        owner(cur)
        # Every fixture, role, and temporary ledger deletion is confined to
        # this disposable transaction.  A clean ``with`` block would COMMIT;
        # explicitly rolling back prevents this gate from poisoning the next
        # alphabetically discovered DB gate with its verifier role or hole.
        conn.rollback()

    if FAILURES:
        print(f"program5 bounded forward-fix DB gate: {len(FAILURES)} failure(s)", file=sys.stderr)
        return 1
    print(f"program5 bounded forward-fix DB gate: {len(PASSES)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
