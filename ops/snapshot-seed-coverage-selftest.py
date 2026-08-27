#!/usr/bin/env python3
"""Paired suite for ops/snapshot-seed-coverage.py.

REWRITTEN 2026-08-27 after an independent fresh-context review FAILED the first
version on two blind spots this repository's own migrations already exercised:
a function defined and then CALLED at migration time (its body was stripped as
"runtime code"), and row-landing DML that is not the literal words INSERT INTO.
Every gap that review named has a case below, and the two decisive ones have a
case built from the real counterexample rather than a synthetic stand-in.

PROVEN TO FAIL, NOT MERELY TO PASS. The check this exercises exists because five
snapshot traps were each discovered days late by a db-gate in CI. A sixth-instance
detector that only ever returns clean would be worse than nothing: it would read
as coverage. So every case below drives a real refusal with a real payload, and
the negative controls pin the two ways a false alarm would show up — a migration
that is still PENDING loses nothing on a rebuild, and a table that is classified
is not a finding.

Fully offline: it composes synthetic migrations and synthetic artifacts in a
temporary directory. It never reads production, never reads db/schema.sql, and
never writes inside the repository.
"""

import importlib.util
import json
import os
import pathlib
import shutil
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent


def load_module():
    spec = importlib.util.spec_from_file_location(
        "snapshot_seed_coverage", REPO / "ops" / "snapshot-seed-coverage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LEDGER_HEADER = "COPY public.schema_migrations (filename, sha256, applied_at) FROM stdin;\n"


def artifact(applied, data_blocks=()):
    """A minimal snapshot: DDL that MENTIONS tables, then the data region.

    The DDL half is not padding. A COMMENT quoting `insert into party ...` and a
    function body containing `insert into ops.run ...` are both real shapes from
    db/schema.sql, and both made an early version of this check report business
    and runtime tables as carried. They stay here as the regression.
    """
    ddl = (
        "--\n-- PostgreSQL database dump\n--\n\n"
        "CREATE FUNCTION public.record_run() RETURNS void\n"
        "    LANGUAGE plpgsql\n"
        "    AS $$\nbegin\n  insert into ops.run (kind) values ('x');\nend $$;\n\n"
        "COMMENT ON FUNCTION public.org_party_id(p_name text) IS "
        "'Replaces the blind `insert into party ... values (''org'', $1)` at tools.js.';\n\n"
    )
    ledger = LEDGER_HEADER + "".join(
        f"{name}\tabc123\t2026-08-01 00:00:00+00\n" for name in applied) + "\\.\n\n\n"
    return ddl + ledger + "".join(data_blocks)


def copy_block(table, columns="(a, b)"):
    return f"COPY {table} {columns} FROM stdin;\n1\tvalue\n\\.\n\n\n"


def insert_block(table):
    return f"-- CARR APPENDED BLOCK\ninsert into {table} (a) values ('x') on conflict do nothing;\n\n"


def build_repo(tmp, migrations, classification):
    root = pathlib.Path(tmp)
    (root / "migrations").mkdir(parents=True, exist_ok=True)
    (root / "ops" / "config").mkdir(parents=True, exist_ok=True)
    for name, body in migrations.items():
        (root / "migrations" / name).write_text(body, encoding="utf-8")
    (root / "ops" / "config" / "snapshot-seed-coverage.json").write_text(
        json.dumps(classification), encoding="utf-8")
    return str(root)


FAILURES: list[str] = []


def case(name, ok):
    if ok:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}")
        FAILURES.append(name)


def summarise(failures):
    return " || ".join(f.split("\n")[0] for f in failures)


def main():
    module = load_module()
    print("ops/snapshot-seed-coverage-selftest.py")

    seeding = ("create table ops.widget (k text);\n"
               "insert into ops.widget (k) values ('alpha'),('beta');\n")
    # A migration whose ONLY migration-time insert is a self-test probe inside a DO
    # block that asserts the row does not survive. ops.rule_pack is exactly this
    # shape in 0291, and reading it as a seed would be a false alarm.
    probe_only = ("do $$\nbegin\n"
                  "  insert into ops.probe_only (k) values ('p');\n"
                  "  raise exception 'must not survive';\n"
                  "exception when others then null;\nend $$;\n")
    runtime_only = ("create function ops.f() returns void language plpgsql as $$\n"
                    "begin\n  insert into ops.never_seeded (k) values ('r');\nend $$;\n")

    with tempfile.TemporaryDirectory() as tmp:
        # ---------------------------------------------------------------- 1
        repo = build_repo(tmp + "/a", {"0100_seed.sql": seeding},
                          {"carried": {}, "excluded": {}})
        found = module.check(repo, artifact(["0100_seed.sql"]))
        case("the sixth instance: an unclassified seeded table refuses",
             len(found) == 1 and "UNCLASSIFIED" in found[0] and "ops.widget" in found[0])
        case("the refusal names the migration that seeded it",
             found and "0100_seed.sql" in found[0])

        # ---------------------------------------------------------------- 2
        found = module.check(repo, artifact([]))
        case("negative control: a PENDING seeding migration is not a finding "
             "(it still replays, so a rebuild loses nothing)", found == [])

        # ---------------------------------------------------------------- 3
        repo = build_repo(tmp + "/b", {"0100_seed.sql": seeding},
                          {"carried": {}, "excluded": {"ops.widget": "runtime data"}})
        case("negative control: an explicitly excluded table is not a finding",
             module.check(repo, artifact(["0100_seed.sql"])) == [])

        # ---------------------------------------------------------------- 4
        repo = build_repo(tmp + "/c", {"0100_seed.sql": seeding},
                          {"carried": {"ops.widget": "bounded config"}, "excluded": {}})
        case("a declared-carried table absent from the artifact refuses",
             any("DECLARED CARRIED BUT ABSENT" in f for f in
                 module.check(repo, artifact(["0100_seed.sql"]))))
        case("a declared-carried table present as a COPY block passes",
             module.check(repo, artifact(["0100_seed.sql"],
                                         [copy_block("ops.widget")])) == [])
        case("a declared-carried table present as an appended insert passes",
             module.check(repo, artifact(["0100_seed.sql"],
                                         [insert_block("ops.widget")])) == [])

        # ---------------------------------------------------------------- 5
        repo = build_repo(tmp + "/d", {"0100_seed.sql": seeding},
                          {"carried": {}, "excluded": {"ops.widget": "business data"}})
        case("an excluded table that APPEARS in the artifact refuses "
             "(the snapshot widening into business data)",
             any("DECLARED EXCLUDED BUT PRESENT" in f for f in
                 module.check(repo, artifact(["0100_seed.sql"],
                                             [copy_block("ops.widget")]))))

        # ---------------------------------------------------------------- 6
        repo = build_repo(tmp + "/e",
                          {"0100_probe.sql": probe_only, "0101_runtime.sql": runtime_only},
                          {"carried": {}, "excluded": {}})
        found = module.check(repo, artifact(["0100_probe.sql", "0101_runtime.sql"]))
        case("a runtime insert inside a FUNCTION body is not read as a seed",
             not any("ops.never_seeded" in f for f in found))
        case("an insert inside a DO block IS read as a seed "
             "(a DO block runs at migration time and can seed)",
             any("ops.probe_only" in f for f in found))

        # ---------------------------------------------------------------- 7
        repo = build_repo(tmp + "/f", {"0100_seed.sql": seeding},
                          {"carried": {"ops.widget": "x"}, "excluded": {"ops.widget": "y"}})
        try:
            module.check(repo, artifact(["0100_seed.sql"]))
            case("a table classified both carried and excluded is rejected", False)
        except ValueError:
            case("a table classified both carried and excluded is rejected", True)

        # ---------------------------------------------------------------- 8
        repo = build_repo(tmp + "/g", {"0100_seed.sql": seeding},
                          {"carried": {}, "excluded": {"ops.widget": "x"}})
        try:
            module.check(repo, "-- no ledger here\n")
            case("an artifact with no migration ledger is refused, never passed", False)
        except LookupError:
            case("an artifact with no migration ledger is refused, never passed", True)

        # ---------------------------------------------------------------- 9
        # The DDL-region regression: `party` and `ops.run` are named only in a
        # COMMENT and a function body, and must not read as carried.
        repo = build_repo(tmp + "/h",
                          {"0100_biz.sql": "insert into party (kind) values ('org');\n"
                                           "insert into ops.run (kind) values ('x');\n"},
                          {"carried": {}, "excluded": {"party": "business data",
                                                       "ops.run": "runtime records"}})
        case("a table named only in DDL prose or a function body is not 'carried'",
             module.check(repo, artifact(["0100_biz.sql"])) == [])

        # ---------------------------------------------------------------- G1
        # THE DECISIVE GAP. A migration defines a function whose body inserts, then
        # CALLS it. The body is not runtime code here — it ran. Modelled on
        # migrations/0247_system_rule_scope_binding.sql, which does exactly this and
        # which the first version of the checker saw as empty.
        called = ("create function ops.sync_bindings() returns void language plpgsql as $$\n"
                  "begin\n  insert into ops.rule_control_binding (rule_id) values (1);\nend $$;\n"
                  "revoke all on function ops.sync_bindings() from public;\n"
                  "select ops.sync_bindings();\n")
        repo = build_repo(tmp + "/g1", {"0100_called.sql": called},
                          {"carried": {}, "excluded": {}})
        found = module.check(repo, artifact(["0100_called.sql"]))
        case("a function DEFINED AND CALLED at migration time has its inserts counted",
             any("ops.rule_control_binding" in f for f in found))

        # The same function, never called: its body is runtime code and must not count.
        uncalled = ("create function ops.sync_bindings() returns void language plpgsql as $$\n"
                    "begin\n  insert into ops.rule_control_binding (rule_id) values (1);\nend $$;\n"
                    "revoke all on function ops.sync_bindings() from public;\n")
        repo = build_repo(tmp + "/g1b", {"0100_uncalled.sql": uncalled},
                          {"carried": {}, "excluded": {}})
        case("a function DEFINED BUT NEVER CALLED does not count as a seed",
             module.check(repo, artifact(["0100_uncalled.sql"])) == [])

        # REVOKE/GRANT/COMMENT name a function without invoking it. Reading those as
        # calls made an early draft report 88 seeded tables instead of the real 58.
        case("REVOKE ON FUNCTION is not read as a call",
             not any("ops.rule_control_binding" in f
                     for f in module.check(repo, artifact(["0100_uncalled.sql"]))))

        # Transitively: a called function calling another.
        chained = ("create function ops.inner() returns void language plpgsql as $$\n"
                   "begin\n  insert into ops.deep_table (k) values (1);\nend $$;\n"
                   "create function ops.outer_fn() returns void language plpgsql as $$\n"
                   "begin\n  perform ops.inner();\nend $$;\n"
                   "select ops.outer_fn();\n")
        repo = build_repo(tmp + "/g1c", {"0100_chain.sql": chained},
                          {"carried": {}, "excluded": {}})
        case("the called-function closure is transitive (called calls called)",
             any("ops.deep_table" in f for f in module.check(repo, artifact(["0100_chain.sql"]))))

        # ---------------------------------------------------------------- G2
        for label, body, table in (
            ("MERGE", "merge into ops.merged_table t using src s on t.k=s.k "
                      "when not matched then insert (k) values (s.k);\n", "ops.merged_table"),
            ("COPY ... FROM", "copy ops.copied_table (k, v) from stdin;\n1\tx\n\\.\n", "ops.copied_table"),
            ("CREATE TABLE ... AS SELECT",
             "create table ops.derived_table as select k from ops.source_table;\n", "ops.derived_table"),
        ):
            repo = build_repo(tmp + "/g2" + label[:4], {"0100_dml.sql": body},
                              {"carried": {}, "excluded": {}})
            case(f"{label} lands rows and is counted as a seed",
                 any(table in f for f in module.check(repo, artifact(["0100_dml.sql"]))))

        # TEMP tables vanish with the session and can never be in a snapshot. Thirteen
        # migrations build them for before/after comparison; counting them is a pure
        # false alarm, and a check that cries wolf gets switched off.
        temp = ("create temp table _org_before as select id from party;\n"
                "create temporary table lt_before as select id from lead;\n")
        repo = build_repo(tmp + "/g2temp", {"0100_temp.sql": temp},
                          {"carried": {}, "excluded": {}})
        case("CREATE TEMP TABLE ... AS is NOT counted (scratch, never in a snapshot)",
             module.check(repo, artifact(["0100_temp.sql"])) == [])

        # ---------------------------------------------------------------- G5
        repo = build_repo(tmp + "/g5", {"0100_seed.sql": seeding},
                          {"carried": {}, "excluded": {"ops.widget": "runtime"}})
        moved = artifact(["0100_seed.sql"]).replace(
            LEDGER_HEADER, copy_block("public.actor", "(id, slug)") + LEDGER_HEADER, 1)
        case("a COPY above the ledger refuses, because the data-region boundary moved",
             any("DATA REGION BOUNDARY MOVED" in f for f in module.check(repo, moved)))

        # ---------------------------------------------------------------- G6
        repo = build_repo(tmp + "/g6", {"0100_seed.sql": seeding},
                          {"carried": {}, "excluded": {"ops.widget": "runtime"}})
        case("a ledger entry with no file on disk is reported, not silently skipped",
             any("NOT ON DISK" in f for f in
                 module.check(repo, artifact(["0100_seed.sql", "0999_renamed_away.sql"]))))

        # ---------------------------------------------------------------- G3
        repo = build_repo(tmp + "/g3", {"0100_seed.sql": seeding},
                          {"carried": {}, "carried_subset": {"ops.widget": "program rows only"},
                           "excluded": {},
                           "carried_subset_must_not_contain": {
                               "ops.widget": {"sourced captures": r'"ref":\s*"WR-0\d{5}"'}}})
        clean = artifact(["0100_seed.sql"], [insert_block("ops.widget")])
        case("a carried_subset table within its declared scope passes",
             module.check(repo, clean) == [])
        breached = artifact(["0100_seed.sql"], [
            '-- CARR APPENDED BLOCK\ninsert into ops.widget select * from '
            'jsonb_populate_record(null::ops.widget, \'{"ref": "WR-000017"}\'::jsonb);\n\n'])
        case("a carried_subset render that widens past its scope refuses",
             any("SCOPE BREACHED" in f for f in module.check(repo, breached)))
        repo = build_repo(tmp + "/g3b", {"0100_seed.sql": seeding},
                          {"carried": {"ops.widget": "x"}, "excluded": {},
                           "carried_subset_must_not_contain": {"ops.widget": {"a": "b"}}})
        try:
            module.check(repo, artifact(["0100_seed.sql"], [copy_block("ops.widget")]))
            case("a scope rule naming a non-subset table is rejected", False)
        except ValueError:
            case("a scope rule naming a non-subset table is rejected", True)

        # ---------------------------------------------------------------- G7
        repo = build_repo(tmp + "/g7", {"0100_seed.sql": seeding},
                          {"carried": {"ops.widget": ""}, "excluded": {}})
        try:
            failures = module.check(repo, artifact(["0100_seed.sql"]))
            case("a carried entry with an empty reason refuses cleanly, never tracebacks",
                 any("DECLARED CARRIED BUT ABSENT" in f for f in failures))
        except KeyError:
            case("a carried entry with an empty reason refuses cleanly, never tracebacks", False)

        # --------------------------------------------------------------- 9b
        # carried_subset: a table carried IN PART by a scoped render. It must be
        # present like anything carried, and must NOT be held to the
        # excluded-must-be-absent rule, which would refuse the render itself.
        repo = build_repo(tmp + "/i", {"0100_seed.sql": seeding},
                          {"carried": {}, "carried_subset": {"ops.widget": "program rows only"},
                           "excluded": {}})
        case("a carried_subset table present in the artifact passes",
             module.check(repo, artifact(["0100_seed.sql"],
                                         [insert_block("ops.widget")])) == [])
        case("a carried_subset table ABSENT from the artifact refuses",
             any("DECLARED CARRIED BUT ABSENT" in f for f in
                 module.check(repo, artifact(["0100_seed.sql"]))))
        case("a carried_subset table satisfies classification (not 'unclassified')",
             not any("UNCLASSIFIED" in f for f in
                     module.check(repo, artifact(["0100_seed.sql"]))))
        repo = build_repo(tmp + "/j", {"0100_seed.sql": seeding},
                          {"carried": {"ops.widget": "x"},
                           "carried_subset": {"ops.widget": "y"}, "excluded": {}})
        try:
            module.check(repo, artifact(["0100_seed.sql"], [copy_block("ops.widget")]))
            case("a table in two buckets is rejected whichever pair it is", False)
        except ValueError:
            case("a table in two buckets is rejected whichever pair it is", True)

    # -------------------------------------------------------------------- 10
    # The live classification must actually cover the live tree, or the check
    # would refuse every real snapshot and get switched off.
    config = REPO / "ops" / "config" / "snapshot-seed-coverage.json"
    doc = json.loads(config.read_text(encoding="utf-8"))
    carried, excluded = doc["carried"], doc["excluded"]
    subset = doc.get("carried_subset") or {}
    case("no table appears in two buckets in the live classification",
         not (set(carried) & set(excluded)) and not (set(carried) & set(subset))
         and not (set(subset) & set(excluded)))
    case("every carried_subset entry names the scope it carries",
         all("only" in str(v).lower() for v in subset.values()))
    case("every live classification entry states a reason",
         all(str(v).strip() for v in
             list(carried.values()) + list(subset.values()) + list(excluded.values())))

    if FAILURES:
        print(f"\nFAILED {len(FAILURES)}: {'; '.join(FAILURES)}")
        return 1
    print("\nall cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
