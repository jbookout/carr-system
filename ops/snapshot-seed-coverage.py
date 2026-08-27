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

CREATE_ROUTINE = re.compile(
    r"create\s+(?:or\s+replace\s+)?(?:function|procedure)\s+"
    r"([a-z_][a-z0-9_]*(?:\s*\.\s*[a-z_][a-z0-9_]*)?)\s*\(", re.I)

# Statements that NAME a function without invoking it, masked to the END OF THE
# STATEMENT rather than to the end of one signature. That distinction is the whole
# fix: a privilege statement may list many functions --
#
#     grant execute on function
#       ops.select_provider_routes(text[]),
#       ops.record_provider_observation(text,text,integer,text,integer,text),
#       ops.put_cognition_cache(text,text,integer,integer,jsonb,text[],integer)
#     to carr_jobs;
#
# -- and masking only the first left the rest looking like calls, so their bodies
# were pulled in as migration-time writes. An independent review measured the
# damage: 49 of 153 detections were routine bodies admitted this way, none of
# which the migration ever runs. Masking to the semicolon covers a list of any
# length.
MENTIONS_ROUTINE = re.compile(
    r"(?:create\s+(?:or\s+replace\s+)?(?:function|procedure)|revoke\b[^;]*?\bon\s+(?:function|procedure)"
    r"|grant\b[^;]*?\bon\s+(?:function|procedure)|alter\s+(?:function|procedure)"
    r"|drop\s+(?:function|procedure)"
    r"|comment\s+on\s+(?:function|procedure)"
    r"|security\s+label\b[^;]*?\bon\s+(?:function|procedure))[^;]*;", re.I | re.S)


# Row-landing DML. Every form here has been seen in this repository's migrations.
WRITES_ANYWHERE = (
    re.compile(r"insert\s+into\s+(?:only\s+)?" + TABLE, re.I),
    re.compile(r"merge\s+into\s+" + TABLE, re.I),
    re.compile(r"copy\s+" + TABLE + r"\s*(?:\([^)]*\)\s*)?from\b", re.I),
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


def _scanned(body):
    """A nested body, run through the same scan and flattened.

    A body's own routines and DO blocks are folded in with it: anything a called
    routine's body reaches is reached when that routine runs.
    """
    inner_top, inner_dos, inner_routines = scan_sql(body)
    parts = [inner_top, *inner_dos]
    for bodies in inner_routines.values():
        parts.extend(bodies)
    return " ".join(parts)


def scan_sql(sql):
    """One left-to-right pass over a migration: strip comments, blank string
    literals, and split out dollar-quoted bodies.

    WHY A SCAN AND NOT MORE REGULAR EXPRESSIONS. The previous version masked
    string literals with a pattern applied to text that still contained SQL
    comments, so an apostrophe in ordinary English prose paired with the opening
    quote of a real string and masked everything between them -- including the
    CALL that makes a routine body count as a seed. One apostrophe in one comment
    disarmed the check for that whole migration, and 102 of 264 applied
    migrations already have odd apostrophe parity. Found by the fourth
    independent review.

    You cannot find comments without knowing where strings are, and you cannot
    find strings without knowing where comments are. Alternating regular
    expressions will always lose that race; a single pass that handles both, plus
    dollar quoting, cannot. Literals are blanked rather than kept because a
    table name is an identifier -- no row-landing statement hides inside a
    string, and an `insert into` quoted in prose is not one either.

    Bodies are scanned too, not stored raw. A DO block or a routine body has its
    own comments and its own string literals, and leaving them unscanned put the
    apostrophe bug straight back one level down -- caught by the suite case that
    pins a signature inside has_function_privilege() as a name rather than a call.

    Returns (top_level_text, do_bodies, routines) with routines keyed by name.
    """
    top, do_bodies, routines = [], [], {}
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "-" and sql.startswith("--", i):                 # line comment
            end = sql.find("\n", i)
            i = n if end == -1 else end
            top.append(" ")
        elif ch == "/" and sql.startswith("/*", i):               # block comment, nestable
            depth, i = 1, i + 2
            while i < n and depth:
                if sql.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif sql.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            top.append(" ")
        elif ch == "'":                                           # string literal
            # E'...' takes BACKSLASH escapes; a plain literal does not (server
            # default standard_conforming_strings). Knowing only the '' form let
            # E'the reviewer\\'s registry' end the literal at the escaped quote, so
            # the rest of the migration was read inside-out and a plainly top-level
            # INSERT went unreported. That is the apostrophe class of R4 one escape
            # form over, and it swallowed real DML rather than only a call.
            escaped = bool(re.search(r"(?:^|[^A-Za-z0-9_])[Ee]$", "".join(top)))
            i += 1
            while i < n:
                if escaped and sql[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                if sql[i] == "'":
                    if sql.startswith("''", i):
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            top.append(" ")
        elif ch == "$":
            match = DOLLAR.match(sql, i)
            if not match:
                top.append(ch)
                i += 1
                continue
            tag = match.group(0)
            end = sql.find(tag, match.end())
            if end == -1:                                         # unterminated: keep verbatim
                top.append(sql[i:])
                break
            body = sql[match.end():end]
            head = "".join(top)
            previous = re.search(r"([A-Za-z_]+)\s*$", head)
            keyword = previous.group(1).lower() if previous else ""
            # `do language plpgsql $$ ... $$` is the same statement as `do $$ ... $$`.
            # Matching only the bare word left the body blanked as a string literal
            # and the block invisible.
            if keyword != "do" and re.search(r"(?:^|;|\s)do\s+language\s+[a-z_]+\s*$", head, re.I):
                keyword = "do"
            if keyword == "as":
                headers = list(CREATE_ROUTINE.finditer("".join(top)))
                if headers:
                    routines.setdefault(normalise(headers[-1].group(1)), []).append(_scanned(body))
                else:
                    top.append(" " + _scanned(body) + " ")
            elif keyword == "do":
                do_bodies.append(_scanned(body))
            else:
                top.append(" ")                                   # dollar-quoted string literal
            i = end + len(tag)
        else:
            top.append(ch)
            i += 1
    return "".join(top), do_bodies, routines


def migration_time_text(sql):
    """Every stretch of SQL that actually executes when this migration runs.

    Top level and DO blocks always execute. A routine body executes only if the
    migration calls it, so routines are pulled in by transitive closure over the
    text that already executes, with definition sites masked out first.
    """
    top, do_bodies, routines = scan_sql(sql)
    executing = [top] + do_bodies
    called, changed = set(), True
    while changed:
        changed = False
        blob = MENTIONS_ROUTINE.sub(" ", " ".join(executing))
        for name, bodies in routines.items():
            if name in called:
                continue
            short = re.escape(name.split(".")[-1])
            if re.search(r"\b" + short + r"\s*\(", blob, re.I):  # PERFORM/SELECT/CALL all match
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
    ledger = LEDGER_COPY.search(artifact)
    if not ledger:
        return None
    # Not COPY alone: the two data blocks bin/schema-snapshot.sh appends by hand
    # (the SIEP manifest, doctrine_meta) are INSERTs, so an INSERT above the ledger
    # would sit outside the data region and go unread just as a COPY would.
    # Only statements OUTSIDE routine bodies count. The DDL above the ledger is
    # full of CREATE FUNCTION bodies whose own INSERTs start a line, and reading
    # those as data made this check refuse every real snapshot.
    prefix = artifact[:ledger.start()]
    prefix_top, _do_bodies, _routines = scan_sql(prefix)
    # NOT anchored to the start of a line. Data above the ledger is data wherever
    # it sits on the line, and the anchor bought nothing: with it and without it the
    # real artifact is equally clean, so all it did was give a mutation somewhere to
    # hide. Literals and comments are already gone from prefix_top, so a table merely
    # NAMED in prose cannot reach here.
    candidates = []
    for pattern in WRITES_ANYWHERE:
        candidates.append(re.search(pattern.pattern, prefix_top, pattern.flags | re.M))
    first = min((c for c in candidates if c), key=lambda c: c.start(), default=None)
    if first:
        return (f"DATA REGION BOUNDARY MOVED: the first data statement in the artifact is "
                f"{normalise(first.group(1))}, above public.schema_migrations.\n"
                f"    This check reads data statements from the ledger COPY to EOF. With data\n"
                f"    above it, rows in that block are invisible here and an excluded table\n"
                f"    could enter a tracked file unseen. Restore the ledger-first order in\n"
                f"    bin/schema-snapshot.sh, or teach this check the new boundary.")
    return None


def tables_with_data(artifact):
    """Tables the artifact actually carries rows for."""
    region = data_region(artifact)
    found = set()
    # WRITES_ANYWHERE carries the COPY form, so there is no separate COPY scan. An
    # earlier version kept one, and a mutation sweep proved it dead twice over: first
    # here, then again in check_region_boundary once that grew its own loop over the
    # same patterns. A second scanner that cannot change an answer is not redundancy,
    # it is a place for a mutation to hide.
    for pattern in WRITES_ANYWHERE:
        for hit in re.finditer(r"^\s*(?:with\s+[^;]{0,4000}?\s)?" + pattern.pattern,
                               region, pattern.flags | re.M):
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

    classified = set(carried) | set(subset) | set(excluded)
    for table in sorted(classified - set(seeds)):
        failures.append(
            f"CLASSIFICATION ENTRY NO LONGER APPLIES: {table}\n"
            f"    {rel_config} classifies this table, and no applied migration writes rows\n"
            f"    into it at migration time any more. That is how a false alarm becomes\n"
            f"    permanent: an entry nobody revisits, for a question nobody is asking.\n"
            f"    Remove it, or say why detection changed.")

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
