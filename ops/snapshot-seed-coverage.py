#!/usr/bin/env python3
# ci: selftest — the paired suite is ops/snapshot-seed-coverage-selftest.py; this
# module is a library for bin/schema-snapshot.sh, not a standalone gate.
"""Refuse a schema snapshot that cannot rebuild the database it was taken from.

THE TRAP, sprung five times before this file existed. A migration seeds rows.
The snapshot's ledger absorbs that migration, so on a rebuild it is already
"applied" and never replays. If the snapshot did not also carry the rows, the
rebuilt database has the table and none of its contents, and the failure shows
up days later as a db-gate red in CI with nothing pointing at the cause. Each of
the five was then patched one table at a time, by hand, in bin/schema-snapshot.sh:
the role preamble, the control catalog, the guidance registry and retrieval
seeds, the rule-delivery policy and activation targets, the governed execution
providers. Five patches, one shape, and nothing that would notice the sixth.

WHAT THIS CHECKS, and it is deliberately narrow: every table that an ALREADY
APPLIED migration inserts into at migration time must be CLASSIFIED in
ops/config/snapshot-seed-coverage.json -- either carried by the snapshot or
explicitly excluded with a reason. A table that is neither is the sixth
instance, and the snapshot refuses to be written until someone classifies it.

WHAT IT DOES NOT DO, on purpose. It never adds a table to the snapshot and it
never decides that a table deserves carrying. bin/schema-snapshot.sh's own rule
still governs that and is quoted here because this check is the thing most
likely to erode it: "a table qualifies because someone read its rows and can say
what is in them, never because a migration seeded it." Detection is not
permission to auto-dump. All this does is refuse to let an unclassified table
pass silently, which is the only part that was missing.

THE TWO DIRECTIONS ARE BOTH CHECKED. A declared-carried table that has fallen
out of the artifact is a regression -- that is the trap again, arriving by
deletion instead of by addition. A declared-excluded table that has APPEARED in
the artifact is worse: it means business or runtime data is entering a tracked
file, which is the one outcome the snapshot's whole design exists to prevent.

READS THE ARTIFACT, NOT THE DATABASE. The applied-migration ledger and the data
statements both live in the snapshot being written, so this runs offline against
the candidate file. That makes it deterministic, testable without production,
and true of the exact bytes about to be committed rather than of a second query
whose answer could differ.
"""

import json
import os
import re
import sys

DOLLAR = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*|)\$")
INSERT = re.compile(
    r"insert\s+into\s+(\"?[a-z_][a-z0-9_]*\"?(?:\s*\.\s*\"?[a-z_][a-z0-9_]*\"?)?)", re.I)
LEDGER_COPY = re.compile(r"^COPY\s+public\.schema_migrations\s*\(", re.M)


def strip_routine_bodies(sql):
    """Drop dollar-quoted routine bodies; keep DO blocks and string literals.

    The discriminator is the token immediately before the opening dollar quote.
    PostgreSQL introduces a routine body with AS and an anonymous block with DO,
    and pg_dump follows the same shape. A DO block runs at migration time and can
    seed; a function body is runtime code, and reading its inserts as seeds is
    what makes a naive grep report ops.run and ops.incident as seeded tables.
    """
    out, i, n = [], 0, len(sql)
    while i < n:
        m = DOLLAR.search(sql, i)
        if not m:
            out.append(sql[i:])
            break
        out.append(sql[i:m.start()])
        tag = m.group(0)
        end = sql.find(tag, m.end())
        if end == -1:                       # unterminated: keep the rest verbatim
            out.append(sql[m.start():])
            break
        prev = re.search(r"([A-Za-z_]+)\s*$", sql[:m.start()])
        keyword = prev.group(1).lower() if prev else ""
        out.append(" " if keyword == "as" else " " + sql[m.end():end] + " ")
        i = end + len(tag)
    return "".join(out)


def normalise(table):
    """public.foo and foo are the same table; ops.foo is not."""
    table = table.replace('"', "").replace(" ", "").lower()
    return table[len("public."):] if table.startswith("public.") else table


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
    """table -> sorted migrations that insert into it at migration time."""
    seeds = {}
    migrations = os.path.join(repo, "migrations")
    for name in sorted(os.listdir(migrations)):
        if not name.endswith(".sql") or name not in applied:
            continue
        with open(os.path.join(migrations, name), encoding="utf-8", errors="replace") as fh:
            sql = strip_routine_bodies(fh.read())
        for hit in INSERT.finditer(sql):
            seeds.setdefault(normalise(hit.group(1)), set()).add(name)
    return {t: sorted(m) for t, m in seeds.items()}


def data_region(artifact):
    """Everything from the ledger COPY to EOF.

    pg_dump emits every CREATE FUNCTION, every COMMENT and all other DDL before
    the first COPY, so this boundary separates data statements from prose that
    merely quotes one. Without it a COMMENT reading "replaces the blind `insert
    into party ...`" makes the business table `party` look carried.
    """
    match = LEDGER_COPY.search(artifact)
    return artifact[match.start():] if match else ""


def tables_with_data(artifact):
    """Tables the artifact actually carries rows for."""
    region = data_region(artifact)
    found = set()
    for hit in re.finditer(r"^COPY\s+(\"?[a-z_][a-z0-9_]*\"?(?:\s*\.\s*\"?[a-z_][a-z0-9_]*\"?)?)\s*\(",
                           region, re.M | re.I):
        found.add(normalise(hit.group(1)))
    for hit in re.finditer(r"^\s*" + INSERT.pattern, region, re.M | re.I):
        found.add(normalise(hit.group(1)))
    return found


def load_classification(repo):
    path = os.path.join(repo, "ops", "config", "snapshot-seed-coverage.json")
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    carried = dict(doc.get("carried") or {})
    subset = dict(doc.get("carried_subset") or {})
    excluded = dict(doc.get("excluded") or {})
    # carried_subset is for a table that is carried IN PART by a scoped render --
    # ops.work_request is the case: its program rows are bounded internal
    # configuration a rebuild needs, and its sourced captures are operational
    # history that must never enter a tracked file. Such a table must be PRESENT,
    # like anything carried, but it is not held to the excluded-must-be-absent
    # rule, which would refuse the very render that makes it correct. The scope
    # lives in the reason, and the render in bin/schema-snapshot.sh is what
    # enforces it -- this list records the decision, it does not police the WHERE.
    buckets = (("carried", carried), ("carried_subset", subset), ("excluded", excluded))
    for i, (name_a, bucket_a) in enumerate(buckets):
        for name_b, bucket_b in buckets[i + 1:]:
            both = sorted(set(bucket_a) & set(bucket_b))
            if both:
                raise ValueError(
                    f"a table cannot be both {name_a} and {name_b}: " + ", ".join(both))
    return carried, subset, excluded, path


def check(repo, artifact_text):
    """Return a list of human-readable failures; empty means the snapshot is sound."""
    carried, subset, excluded, config_path = load_classification(repo)
    rel_config = os.path.relpath(config_path, repo)
    applied = applied_migrations(artifact_text)
    seeds = seeded_tables(repo, applied)
    present = tables_with_data(artifact_text)
    failures = []

    unclassified = sorted(
        t for t in seeds if t not in carried and t not in subset and t not in excluded)
    for table in unclassified:
        failures.append(
            f"UNCLASSIFIED SEEDED TABLE: {table}\n"
            f"    seeded by: {', '.join(seeds[table])} (already in this snapshot's ledger,\n"
            f"    so that migration will NOT replay on a database rebuilt from this file)\n"
            f"    Decide and record it in {rel_config}: 'carried' if a rebuild needs its rows\n"
            f"    (then add the block to bin/schema-snapshot.sh), or 'excluded' with the reason\n"
            f"    it is business, runtime or usage data. Read the rows before carrying them.")

    for table in sorted(set(carried) | set(subset)):
        if table not in present:
            failures.append(
                f"DECLARED CARRIED BUT ABSENT: {table}\n"
                f"    {rel_config} says this snapshot carries it, and it is not in the artifact.\n"
                f"    Reason on file: {carried.get(table) or subset[table]}\n"
                f"    Either its block in bin/schema-snapshot.sh stopped emitting, or the source\n"
                f"    database no longer holds the rows. A rebuild from this file would come up short.")

    for table in sorted(excluded):
        if table in present:
            failures.append(
                f"DECLARED EXCLUDED BUT PRESENT: {table}\n"
                f"    {rel_config} excludes this table, and the artifact carries rows for it.\n"
                f"    Reason on file: {excluded[table]}\n"
                f"    A tracked snapshot must never widen into business, runtime or usage data.")

    return failures


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: snapshot_seed_coverage.py <repo-root> <candidate-snapshot>\n")
        return 64
    repo, artifact_path = argv[1], argv[2]
    with open(artifact_path, encoding="utf-8", errors="replace") as fh:
        artifact = fh.read()
    try:
        failures = check(repo, artifact)
    except (LookupError, ValueError, OSError) as exc:
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
