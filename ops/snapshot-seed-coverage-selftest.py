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
import sys
import tempfile
import contextlib
import io

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

        # ---------------------------------------------------------------- R2
        # A privilege statement may list MANY functions. Masking only the first left
        # the rest looking like calls, and 49 of 153 live detections were routine
        # bodies admitted that way. Modelled on 0153_control_plane_resilience.sql.
        grant_list = ("create function ops.record_obs() returns void language plpgsql as $$\n"
                      "begin\n  insert into ops.provider_observation (k) values (1);\nend $$;\n"
                      "create function ops.put_cache() returns void language plpgsql as $$\n"
                      "begin\n  insert into ops.cognition_result_cache (k) values (1);\nend $$;\n"
                      "grant execute on function\n  ops.record_obs(),\n  ops.put_cache()\nto carr_jobs;\n")
        repo = build_repo(tmp + "/r2a", {"0100_grants.sql": grant_list},
                          {"carried": {}, "excluded": {}})
        case("a comma-continued GRANT ON FUNCTION list is not read as calls",
             module.check(repo, artifact(["0100_grants.sql"])) == [])

        quoted_sig = ("create function ops.record_lease() returns void language plpgsql as $$\n"
                      "begin\n  insert into lease (k) values (1);\nend $$;\n"
                      "do $$\nbegin\n"
                      "  if not has_function_privilege('carr_writer','ops.record_lease()','execute')\n"
                      "  then raise exception 'missing'; end if;\nend $$;\n")
        repo = build_repo(tmp + "/r2b", {"0100_quoted.sql": quoted_sig},
                          {"carried": {}, "excluded": {}})
        case("a signature inside a quoted string is a name, not a call",
             module.check(repo, artifact(["0100_quoted.sql"])) == [])

        for label, body, table in (
            ("INSERT INTO ONLY", "insert into only ops.parent_table (k) values (1);\n", "ops.parent_table"),
            ("COPY with no column list", "copy ops.bare_copy from stdin;\n1\n\\.\n", "ops.bare_copy"),
        ):
            repo = build_repo(tmp + "/r2" + label[:6], {"0100_dml.sql": body},
                              {"carried": {}, "excluded": {}})
            case(f"{label} names the real table",
                 any(table in f for f in module.check(repo, artifact(["0100_dml.sql"]))))

        repo = build_repo(tmp + "/r2cte", {"0100_seed.sql": seeding},
                          {"carried": {"ops.widget": "bounded config"}, "excluded": {}})
        cte = artifact(["0100_seed.sql"],
                       ["-- CARR APPENDED BLOCK\nwith s as (select 1) insert into ops.widget (a) "
                        "select 1 from s on conflict do nothing;\n\n"])
        case("a carried table emitted behind a CTE still reads as present",
             module.check(repo, cte) == [])

        repo = build_repo(tmp + "/r2dead", {"0100_seed.sql": seeding},
                          {"carried": {}, "excluded": {"ops.widget": "runtime",
                                                       "ops.long_gone": "nothing writes this any more"}})
        case("a classification entry nothing seeds any more is reported, not left to rot",
             any("NO LONGER APPLIES" in f and "ops.long_gone" in f
                 for f in module.check(repo, artifact(["0100_seed.sql"]))))

        repo = build_repo(tmp + "/r2bound", {"0100_seed.sql": seeding},
                          {"carried": {}, "excluded": {"ops.widget": "runtime"}})
        above = artifact(["0100_seed.sql"]).replace(
            LEDGER_HEADER, "insert into public.client (id) values (1);\n\n" + LEDGER_HEADER, 1)
        case("an INSERT above the ledger refuses; the boundary is not COPY-only",
             any("DATA REGION BOUNDARY MOVED" in f for f in module.check(repo, above)))

        # ---------------------------------------------------------------- R3
        # normalise() folds public.foo and foo together. Nothing exercised it, so
        # deleting the fold passed all 45 cases while producing 25 failures on the
        # real db/schema.sql — the same untested-surface disease that hid the main()
        # mutants, one layer down.
        qualified = "insert into public.qualified_table (k) values (1);\n"
        repo = build_repo(tmp + "/r3norm", {"0100_q.sql": qualified},
                          {"carried": {}, "excluded": {"qualified_table": "runtime"}})
        case("a migration writing public.foo is satisfied by the bare name foo",
             module.check(repo, artifact(["0100_q.sql"])) == [])
        repo = build_repo(tmp + "/r3norm2", {"0100_q.sql": "insert into bare_table (k) values (1);\n"},
                          {"carried": {"bare_table": "vocabulary"}, "excluded": {}})
        case("an artifact carrying COPY public.foo satisfies a bare-name carried entry",
             module.check(repo, artifact(["0100_q.sql"],
                                         [copy_block("public.bare_table")])) == [])
        repo = build_repo(tmp + "/r3norm3", {"0100_q.sql": "insert into ops.kept (k) values (1);\n"},
                          {"carried": {}, "excluded": {"kept": "runtime"}})
        case("ops.foo is NOT folded to foo — only the public schema is implicit",
             any("ops.kept" in f for f in module.check(repo, artifact(["0100_q.sql"]))))

        # CREATE PROCEDURE bodies were attributed to the last CREATE FUNCTION header,
        # so a procedure that writes could be credited to the wrong routine or lost.
        proc = ("create function ops.helper() returns void language plpgsql as $$\n"
                "begin\n  perform 1;\nend $$;\n"
                "create procedure ops.seed_it() language plpgsql as $$\n"
                "begin\n  insert into ops.proc_table (k) values (1);\nend $$;\n"
                "call ops.seed_it();\n")
        repo = build_repo(tmp + "/r3proc", {"0100_proc.sql": proc},
                          {"carried": {}, "excluded": {}})
        case("a CREATE PROCEDURE body that is CALLed is counted as a seed",
             any("ops.proc_table" in f for f in module.check(repo, artifact(["0100_proc.sql"]))))

        # COPY with no column list, on the ARTIFACT side this time. It was fixed for
        # migrations and left broken for the snapshot, so a carried table emitted that
        # way read as absent.
        repo = build_repo(tmp + "/r3copy", {"0100_seed.sql": seeding},
                          {"carried": {"ops.widget": "bounded config"}, "excluded": {}})
        case("a carried table emitted as COPY with no column list reads as present",
             module.check(repo, artifact(["0100_seed.sql"],
                                         ["COPY ops.widget FROM stdin;\n1\n\\.\n\n\n"])) == [])

        # The boundary must see every row-landing form above the ledger, not INSERT
        # and COPY alone.
        repo = build_repo(tmp + "/r3bound", {"0100_seed.sql": seeding},
                          {"carried": {}, "excluded": {"ops.widget": "runtime"}})
        for label, statement in (
            ("MERGE", "merge into public.client t using s on t.k=s.k when not matched then insert (k) values (s.k);\n"),
            ("CREATE TABLE AS", "create table public.client_copy as select * from public.client;\n"),
        ):
            above = artifact(["0100_seed.sql"]).replace(
                LEDGER_HEADER, statement + "\n" + LEDGER_HEADER, 1)
            case(f"{label} above the ledger refuses; the boundary is not INSERT/COPY only",
                 any("DATA REGION BOUNDARY MOVED" in f for f in module.check(repo, above)))

        # ------------------------------------------------------- SWEEP RESIDUE
        # Found by a mutation sweep over the whole module rather than over the last
        # fix: these two branches could be broken with the entire suite still green.
        repo = build_repo(tmp + "/swept", {"0100_seed.sql": seeding},
                          {"carried": {}, "carried_subset": {"ops.widget": "program rows only"},
                           "excluded": {},
                           "carried_subset_must_not_contain": {
                               "ops.widget": {"broken": "([unclosed"}}})
        case("an unusable scope pattern is reported, never silently skipped",
             any("UNUSABLE SCOPE PATTERN" in f for f in
                 module.check(repo, artifact(["0100_seed.sql"], [insert_block("ops.widget")]))))
        case("the boundary check returns nothing when there is no ledger to anchor to",
             module.check_region_boundary("-- no ledger here\n") is None)

        # ---------------------------------------------------------------- MAIN
        # main() is the ONLY surface bin/schema-snapshot.sh consumes. Testing check()
        # alone let a mutant that returns 0 unconditionally, or swallows the stderr
        # payload, pass all 34 cases while the guard refused nothing.
        repo = build_repo(tmp + "/mainok", {"0100_seed.sql": seeding},
                          {"carried": {}, "excluded": {"ops.widget": "runtime"}})
        path_clean = os.path.join(tmp, "clean.sql")
        open(path_clean, "w").write(artifact(["0100_seed.sql"]))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc_clean = module.main(["prog", repo, path_clean])
        case("main() returns 0 and says nothing when the snapshot is sound",
             rc_clean == 0 and err.getvalue() == "")

        repo_bad = build_repo(tmp + "/mainbad", {"0100_seed.sql": seeding},
                              {"carried": {}, "excluded": {}})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc_bad = module.main(["prog", repo_bad, path_clean])
        text = err.getvalue()
        case("main() returns NON-ZERO on a finding, so the generator actually stops",
             rc_bad != 0)
        case("main() writes the refusal and the table name to stderr",
             "REFUSING" in text and "ops.widget" in text)
        # Wrapped: a broken argc guard makes main() index past the end of argv, and
        # an uncaught traceback here would take the whole suite down instead of
        # reporting one failed case.
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc_args = module.main(["prog", repo])
            case("main() refuses a wrong argument count instead of proceeding", rc_args != 0)
        except Exception:                                    # noqa: BLE001 - any crash is a fail
            case("main() refuses a wrong argument count instead of proceeding", False)

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
        # NOT a restatement of the case above: that one drives a clean artifact, this
        # one drives an artifact where the table is ABSENT, and pins that the failure
        # reported is the carried-but-absent one and never "unclassified".
        absent = module.check(repo, artifact(["0100_seed.sql"]))
        case("a carried_subset table reports as carried-but-absent, never unclassified",
             absent and not any("UNCLASSIFIED" in f for f in absent))
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
