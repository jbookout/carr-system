#!/usr/bin/env python3
# ci: selftest — the paired suite is ops/snapshot-seed-coverage-selftest.py; this
# module is a library for bin/schema-snapshot.sh, not a standalone gate.
"""Refuse a schema snapshot that cannot rebuild the database it was taken from.

THE TRAP, sprung six times before this file existed. A migration seeds rows. The
snapshot's ledger absorbs that migration, so on a rebuild it is already "applied"
and never replays. If the snapshot did not also carry the rows, the rebuilt
database has the table and none of its contents, and the failure surfaces days
later as a db-gate red in CI with nothing pointing at the cause. Each of the six
was then patched one table at a time, by hand, in bin/schema-snapshot.sh.

WHAT THIS CHECKS, and it is deliberately narrow: every table that an ALREADY
APPLIED migration writes rows into AT MIGRATION TIME must be CLASSIFIED in
ops/config/snapshot-seed-coverage.json -- carried, carried_subset, or explicitly
excluded with a reason. A table that is none of those is the next instance, and
the snapshot refuses to be written until someone classifies it.

WHAT "AT MIGRATION TIME" MEANS, and the first version of this file got it wrong.
It is NOT "appears in an INSERT statement in the file". An independent review of
the first version (2026-08-27) failed it on two blind spots that this repository's
own migrations already exercise:

  * DEFINE-THEN-CALL. 0247_system_rule_scope_binding.sql creates
    ops.sync_system_rule_control_bindings(), whose body inserts into
    ops.rule_control_binding, and then CALLS it at line 159. The first version
    stripped every routine body as "runtime code" and saw nothing in that file.
    A function body is runtime code only until the migration invokes it. So
    routine bodies are stripped, then put BACK for any routine the migration
    actually calls -- transitively, because a called routine may call another.

  * DML THAT IS NOT "INSERT INTO". CREATE TABLE ... AS SELECT, MERGE, and
    COPY ... FROM all land rows and were all invisible. SELECT ... INTO is
    recognised only at top level, never inside a DO block or routine body, where
    PL/pgSQL's SELECT ... INTO assigns a variable and names no table at all.

WHAT IT DOES NOT DO, on purpose. It never adds a table to the snapshot and never
decides that a table deserves carrying. bin/schema-snapshot.sh's own rule still
governs that and is quoted here because this check is the thing most likely to
erode it: "a table qualifies because someone read its rows and can say what is in
them, never because a migration seeded it." Detection is not permission to
auto-dump. All this does is refuse to let an unclassified table pass in silence.

BOTH DIRECTIONS ARE CHECKED. A declared-carried table that has fallen out of the
artifact is the trap again, arriving by deletion. A declared-excluded table that
has APPEARED in the artifact is worse: business or runtime data entering a
tracked file, which is the one outcome the snapshot's whole design prevents.

READS THE ARTIFACT, NOT THE DATABASE. The applied-migration ledger and the data
statements both live in the snapshot being written, so this runs offline against
the candidate file: deterministic, testable without production, and true of the
exact bytes about to be committed rather than of a second query whose answer
could differ.
"""

import json
import os
import re
import sys

DOLLAR = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*|)\$")
TABLE = r"(\"?[a-z_][a-z0-9_]*\"?(?:\s*\.\s*\"?[a-z_][a-z0-9_]*\"?)?)"
LEDGER_COPY = re.compile(r"^COPY\s+public\.schema_migrations\s*\(", re.M)
ANY_COPY = re.compile(r"^COPY\s+" + TABLE + r"\s*\(", re.M | re.I)

CREATE_ROUTINE = re.compile(
    r"create\s+(?:or\s+replace\s+)?function\s+([a-z_][a-z0-9_]*(?:\s*\.\s*[a-z_][a-z0-9_]*)?)\s*\(", re.I)

# Statements that NAME a function without invoking it. Left in place they make
# every routine in a migration look called, which is how an early draft of this
# check reported 88 "newly seeded" tables instead of the real 58.
MENTIONS_ROUTINE = re.compile(
    r"(?:create\s+(?:or\s+replace\s+)?function|revoke[^;]*?on\s+function|grant[^;]*?on\s+function"
    r"|alter\s+function|drop\s+function|comment\s+on\s+function"
    r"|security\s+label[^;]*?on\s+function)\s+[a-z_][a-z0-9_.\s]*\([^)]*\)", re.I | re.S)

# Row-landing DML. Every form here has been seen in this repository's migrations.
WRITES_ANYWHERE = (
    re.compile(r"insert\s+into\s+" + TABLE, re.I),
    re.compile(r"merge\s+into\s+" + TABLE, re.I),
    re.compile(r"copy\s+" + TABLE + r"\s*\([^)]*\)\s*from", re.I),
    # TEMP and TEMPORARY are deliberately NOT matched. Thirteen migrations build
    # scratch tables this way for before/after comparison (_org_before, amd2_map,
    # lt_before, ...); they vanish with the session and can never be in a snapshot,
    # so counting them is a pure false alarm — and a check that cries wolf gets
    # switched off, which is the failure mode this whole file exists to avoid.
    re.compile(r"create\s+(?:unlogged\s+)?table\s+(?:if\s+not\s+exists\s+)?"
               + TABLE + r"[^;]{0,4000}?\bas\s+(?:with|select)", re.I | re.S),
)

# SELECT ... INTO IS DELIBERATELY NOT DETECTED, and the reason is worth stating so
# nobody adds it back. In plain SQL it creates a table; in PL/pgSQL the identical
# syntax assigns a variable. Every occurrence across all 264 migrations is the
# variable form — `into n`, `into missing`, `into dup`, `into changed` — and one
# more sits inside a comment in 0078 ("select side into s from participant_role"),
# which a regex cannot tell from code without a real SQL parser. Detecting it
# produced only false alarms. No migration here seeds a persistent table this way;
# CREATE TABLE ... AS covers the case that matters.


def normalise(table):
    """public.foo and foo are the same table; ops.foo is not."""
    table = table.replace('"', "").replace(" ", "").lower()
    return table[len("public."):] if table.startswith("public.") else table


def split_segments(sql):
    """Return (top_level_text, do_bodies, routines) for one migration.

    The discriminator for a dollar-quoted body is the token immediately before
    the opening quote: PostgreSQL introduces a routine body with AS and an
    anonymous block with DO. A DO block runs at migration time unconditionally.
    A routine body runs only if something calls it, which is resolved separately.
    """
    top, do_bodies, routines = [], [], {}
    i, n = 0, len(sql)
    while i < n:
        match = DOLLAR.search(sql, i)
        if not match:
            top.append(sql[i:])
            break
        top.append(sql[i:match.start()])
        tag = match.group(0)
        end = sql.find(tag, match.end())
        if end == -1:                        # unterminated: keep the rest verbatim
            top.append(sql[match.start():])
            break
        body = sql[match.end():end]
        previous = re.search(r"([A-Za-z_]+)\s*$", sql[:match.start()])
        keyword = previous.group(1).lower() if previous else ""
        if keyword == "as":
            headers = list(CREATE_ROUTINE.finditer(sql[:match.start()]))
            if headers:
                routines.setdefault(normalise(headers[-1].group(1)), []).append(body)
            else:                            # AS without a routine header: a literal
                top.append(" " + body + " ")
        elif keyword == "do":
            do_bodies.append(body)
        else:
            top.append(" " + body + " ")     # ordinary dollar-quoted string
        i = end + len(tag)
    return "".join(top), do_bodies, routines


def migration_time_text(sql):
    """Every stretch of SQL that actually executes when this migration runs.

    Top level and DO blocks always execute. A routine body executes only if the
    migration calls it, so routines are pulled in by transitive closure over the
    text that already executes, with definition sites masked out first.
    """
    top, do_bodies, routines = split_segments(sql)
    executing = [top] + do_bodies
    called, changed = set(), True
    while changed:
        changed = False
        blob = MENTIONS_ROUTINE.sub(" ", " ".join(executing))
        for name, bodies in routines.items():
            if name in called:
                continue
            short = re.escape(name.split(".")[-1])
            if re.search(r"\b" + short + r"\s*\(", blob, re.I):
                called.add(name)
                executing.extend(bodies)
                changed = True
    return top, executing


def written_tables(sql):
    """Tables this migration lands rows in at migration time."""
    _top, executing = migration_time_text(sql)
    found = set()
    for segment in executing:
        for pattern in WRITES_ANYWHERE:
            found.update(normalise(hit.group(1)) for hit in pattern.finditer(segment))
    return found


def applied_migrations(artifact):
    """The filenames in the artifact's own applied-migration ledger."""
    match = LEDGER_COPY.search(artifact)
    if not match:
        raise LookupError(
            "the artifact carries no public.schema_migrations ledger; "
            "the snapshot is malformed and nothing can be judged against it")
    applied = set()
    for line in artifact[match.end():].splitlines()[1:]:
        if line.startswith("\\."):
            break
        first = line.split("\t", 1)[0].strip()
        if first:
            applied.add(first)
    return applied


def seeded_tables(repo, applied):
    """table -> sorted migrations that write rows into it at migration time.

    Also returns ledger entries with no file on disk. Those used to be skipped in
    silence, which meant a renamed migration quietly removed its tables from the
    question -- so they are reported rather than dropped.
    """
    seeds, missing = {}, []
    directory = os.path.join(repo, "migrations")
    present = set(os.listdir(directory))
    for name in sorted(applied):
        if name not in present:
            missing.append(name)
            continue
        with open(os.path.join(directory, name), encoding="utf-8", errors="replace") as handle:
            for table in written_tables(handle.read()):
                seeds.setdefault(table, set()).add(name)
    return {t: sorted(m) for t, m in seeds.items()}, sorted(missing)


def data_region(artifact):
    """Everything from the ledger COPY to EOF.

    pg_dump emits every CREATE FUNCTION, every COMMENT and all other DDL before
    the first COPY, so this boundary separates data statements from prose that
    merely quotes one. Without it a COMMENT reading "replaces the blind `insert
    into party ...`" makes the business table `party` look carried.
    """
    match = LEDGER_COPY.search(artifact)
    return artifact[match.start():] if match else ""


def check_region_boundary(artifact):
    """The ledger COPY must be the FIRST COPY, or the boundary above is a lie.

    This held only because bin/schema-snapshot.sh dumps the ledger before the
    vocabulary. Reordering those two would silently blind the excluded-but-present
    direction, so the positional assumption is asserted rather than assumed.
    """
    first = ANY_COPY.search(artifact)
    ledger = LEDGER_COPY.search(artifact)
    if first and ledger and first.start() != ledger.start():
        return (f"DATA REGION BOUNDARY MOVED: the first COPY in the artifact is "
                f"{normalise(first.group(1))}, not public.schema_migrations.\n"
                f"    This check reads data statements from the ledger COPY to EOF. With another\n"
                f"    COPY above it, rows in that block are invisible here and an excluded table\n"
                f"    could enter a tracked file unseen. Restore the ledger-first order in\n"
                f"    bin/schema-snapshot.sh, or teach this check the new boundary.")
    return None


def tables_with_data(artifact):
    """Tables the artifact actually carries rows for."""
    region = data_region(artifact)
    found = set()
    for hit in ANY_COPY.finditer(region):
        found.add(normalise(hit.group(1)))
    for pattern in WRITES_ANYWHERE:
        for hit in re.finditer(r"^\s*" + pattern.pattern, region, pattern.flags | re.M):
            found.add(normalise(hit.group(1)))
    return found


def load_classification(repo):
    path = os.path.join(repo, "ops", "config", "snapshot-seed-coverage.json")
    with open(path, encoding="utf-8") as handle:
        doc = json.load(handle)
    carried = dict(doc.get("carried") or {})
    subset = dict(doc.get("carried_subset") or {})
    excluded = dict(doc.get("excluded") or {})
    forbidden = dict(doc.get("carried_subset_must_not_contain") or {})
    buckets = (("carried", carried), ("carried_subset", subset), ("excluded", excluded))
    for index, (name_a, bucket_a) in enumerate(buckets):
        for name_b, bucket_b in buckets[index + 1:]:
            both = sorted(set(bucket_a) & set(bucket_b))
            if both:
                raise ValueError(f"a table cannot be both {name_a} and {name_b}: " + ", ".join(both))
    stray = sorted(set(forbidden) - set(subset))
    if stray:
        raise ValueError("carried_subset_must_not_contain names a table that is not "
                         "carried_subset: " + ", ".join(stray))
    return carried, subset, excluded, forbidden, path


def check(repo, artifact_text):
    """Return a list of human-readable failures; empty means the snapshot is sound."""
    carried, subset, excluded, forbidden, config_path = load_classification(repo)
    rel_config = os.path.relpath(config_path, repo)
    applied = applied_migrations(artifact_text)
    seeds, missing = seeded_tables(repo, applied)
    present = tables_with_data(artifact_text)
    failures = []

    boundary = check_region_boundary(artifact_text)
    if boundary:
        failures.append(boundary)

    for name in missing:
        failures.append(
            f"LEDGER NAMES A MIGRATION THAT IS NOT ON DISK: {name}\n"
            f"    It is applied, so a rebuild will not replay it, and its file is gone — so\n"
            f"    whatever it seeded cannot be checked. Restore the file or record the rename.")

    for table in sorted(t for t in seeds if t not in carried and t not in subset and t not in excluded):
        failures.append(
            f"UNCLASSIFIED SEEDED TABLE: {table}\n"
            f"    written at migration time by: {', '.join(seeds[table])}\n"
            f"    (already in this snapshot's ledger, so that migration will NOT replay on a\n"
            f"    database rebuilt from this file)\n"
            f"    Decide and record it in {rel_config}: 'carried' if a rebuild needs its rows\n"
            f"    (then add the block to bin/schema-snapshot.sh), or 'excluded' with the reason\n"
            f"    it is business, runtime or usage data. Read the rows before carrying them.")

    for table in sorted(set(carried) | set(subset)):
        if table not in present:
            failures.append(
                f"DECLARED CARRIED BUT ABSENT: {table}\n"
                f"    {rel_config} says this snapshot carries it, and it is not in the artifact.\n"
                f"    Reason on file: {carried.get(table, subset.get(table, '(none recorded)'))}\n"
                f"    Either its block in bin/schema-snapshot.sh stopped emitting, or the source\n"
                f"    database no longer holds the rows. A rebuild would come up short.")

    for table in sorted(excluded):
        if table in present:
            failures.append(
                f"DECLARED EXCLUDED BUT PRESENT: {table}\n"
                f"    {rel_config} excludes this table, and the artifact carries rows for it.\n"
                f"    Reason on file: {excluded[table]}\n"
                f"    A tracked snapshot must never widen into business, runtime or usage data.")

    # A partially-carried table is carried by a scoped render, and the scope lives
    # in a WHERE clause this file cannot read. What it CAN do is assert the shape
    # that must never appear if the scope is right -- so widening or dropping that
    # WHERE is caught here rather than discovered in a tracked file.
    region = data_region(artifact_text)
    for table, rules in sorted(forbidden.items()):
        for label, pattern in sorted((rules or {}).items()):
            try:
                found = re.search(pattern, region)
            except re.error as exc:
                failures.append(f"UNUSABLE SCOPE PATTERN for {table} ({label}): {exc}")
                continue
            if found:
                failures.append(
                    f"CARRIED-SUBSET SCOPE BREACHED: {table} ({label})\n"
                    f"    The artifact contains {found.group(0)!r}, which the scope for this\n"
                    f"    partially-carried table forbids. Its render in bin/schema-snapshot.sh\n"
                    f"    has widened past what {rel_config} says it carries.")

    return failures


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: snapshot-seed-coverage.py <repo-root> <candidate-snapshot>\n")
        return 64
    repo, artifact_path = argv[1], argv[2]
    with open(artifact_path, encoding="utf-8", errors="replace") as handle:
        artifact = handle.read()
    try:
        failures = check(repo, artifact)
    except (LookupError, ValueError, OSError, KeyError) as exc:
        sys.stderr.write(f"schema-snapshot: seed-coverage check could not run: {exc}\n")
        return 1
    if not failures:
        return 0
    sys.stderr.write(
        "schema-snapshot: REFUSING to write a snapshot that cannot rebuild its own database.\n"
        "The applied-migration ledger below would tell a rebuild these migrations are done,\n"
        "so their seed rows never replay. Nothing has been written.\n\n")
    for failure in failures:
        sys.stderr.write("  " + failure.replace("\n", "\n  ") + "\n\n")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
