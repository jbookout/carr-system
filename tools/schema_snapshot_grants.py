#!/usr/bin/env python3
"""Read the generated CARR GRANTS section from ``db/schema.sql`` safely.

The snapshot is the canonical reconstruction declaration for privilege bundles.
Consumers must reuse its emitted GRANT statements rather than maintain another
table/function privilege list that can drift from the rebuild path.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable


SECTION_MARKER = "-- CARR GRANTS (bin/schema-snapshot.sh) — not produced by pg_dump."
SECTION_END = "-- PostgreSQL database dump"

# Keep this grammar in lockstep with the five format() calls in
# bin/schema-snapshot.sh.  The old ``.+`` parser admitted an arbitrary second
# statement so long as the line eventually ended in ``to carr_writer;``.
# Catalog names emitted by that generator are deliberately unquoted lower-case
# identifiers; a future generator expansion must update this executable grammar
# before a new SQL shape can be consumed by a privileged provisioner.
IDENT = r"[a-z_][a-z0-9_$]*"
ROLE = rf"(?P<grantee>{IDENT})"
PRIVILEGES = r"[a-z_]+(?:, [a-z_]+)*"
COLUMNS = rf"{IDENT}(?:, {IDENT})*"
FUNCTION_ARGS = rf"(?:{IDENT} [a-z_][a-z0-9_ ]*(?:\[\])?(?:, {IDENT} [a-z_][a-z0-9_ ]*(?:\[\])?)*)?"
GRANT_PATTERNS = (
    re.compile(
        rf"^grant (?P<privileges>{PRIVILEGES}) on schema (?P<schema>{IDENT}) to {ROLE};$"
    ),
    re.compile(
        rf"^grant (?P<privileges>{PRIVILEGES}) on (?P<relation_kind>table|sequence) "
        rf"(?P<schema>{IDENT})\.(?P<object>{IDENT}) to {ROLE};$"
    ),
    re.compile(
        rf"^grant (?P<privileges>[a-z_]+) \((?P<columns>{COLUMNS})\) on table "
        rf"(?P<schema>{IDENT})\.(?P<object>{IDENT}) to {ROLE};$"
    ),
    re.compile(
        rf"^grant execute on function (?P<schema>{IDENT})\.(?P<object>{IDENT})"
        rf"\((?P<function_args>{FUNCTION_ARGS})\) to {ROLE};$"
    ),
    re.compile(rf"^grant (?P<granted_role>{IDENT}) to {ROLE};$"),
)


class SnapshotGrantError(ValueError):
    """The generated grant section is missing or no longer safely parseable."""


def match_generated_grant(statement: str) -> re.Match[str]:
    """Match exactly one generator-emitted statement and return its grantee."""
    if (
        not statement.endswith(";")
        or statement.count(";") != 1
        or "--" in statement
        or "/*" in statement
        or "*/" in statement
        or "\n" in statement
        or "\r" in statement
    ):
        raise SnapshotGrantError(
            "CARR GRANTS statement is not one comment-free SQL statement: "
            + statement[:120]
        )
    for pattern in GRANT_PATTERNS:
        match = pattern.fullmatch(statement)
        if match is not None:
            return match
    raise SnapshotGrantError(
        "CARR GRANTS contains SQL outside the schema generator grammar: "
        + statement[:120]
    )


def carr_grants_section_lines(schema_text: str) -> list[str]:
    """Return non-comment SQL lines from the one generated CARR GRANTS section.

    The generator emits every GRANT on one line. Refusing any other SQL shape is
    intentional: silently skipping a wrapped or newly generated statement would
    provision a role that only partly matches the repository declaration.
    """
    lines = schema_text.splitlines()
    markers = [i for i, line in enumerate(lines) if line == SECTION_MARKER]
    if len(markers) != 1:
        raise SnapshotGrantError(
            f"expected exactly one CARR GRANTS marker, found {len(markers)}"
        )
    start = markers[0] + 1
    try:
        end = next(i for i in range(start, len(lines)) if lines[i] == SECTION_END)
    except StopIteration as exc:
        raise SnapshotGrantError("CARR GRANTS section has no dump boundary") from exc

    sql_lines: list[str] = []
    for raw in lines[start:end]:
        line = raw.strip()
        if not line or line.startswith("--"):
            continue
        match_generated_grant(line)
        sql_lines.append(line)
    if not sql_lines:
        raise SnapshotGrantError("CARR GRANTS section is empty")
    return sql_lines


def grants_to_role(schema_text: str, role: str) -> list[str]:
    """Return exact canonical statements whose grantee set is only ``role``."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", role):
        raise SnapshotGrantError(f"unsafe role name {role!r}")
    selected: list[str] = []
    for statement in carr_grants_section_lines(schema_text):
        match = match_generated_grant(statement)
        grantee = match.group("grantee")
        if role.lower() == grantee:
            selected.append(statement)
    if not selected:
        raise SnapshotGrantError(f"CARR GRANTS contains no statements for {role}")
    return selected


def load_grants_to_role(schema_path: Path, role: str) -> list[str]:
    return grants_to_role(schema_path.read_text(encoding="utf-8"), role)


def acl_facts(statements: Iterable[str]) -> tuple[tuple[str, str, str, bool], ...]:
    """Expand canonical object GRANTs into atomic, non-grantable ACL facts.

    The snapshot generator emits no ``WITH GRANT OPTION`` form.  Grantability
    therefore belongs in the comparison key and is always false canonically;
    a catalog row with it set is excess authority, not an equivalent GRANT.
    """
    facts: set[tuple[str, str, str, bool]] = set()
    for statement in statements:
        match = match_generated_grant(statement)
        groups = match.groupdict()
        if groups.get("granted_role") is not None:
            continue
        privileges = (groups.get("privileges") or "execute").split(", ")
        schema = groups["schema"]
        obj = groups.get("object")
        columns = groups.get("columns")
        function_args = groups.get("function_args")
        if function_args is not None:
            identity = f"{schema}.{obj}({function_args})"
            kind = "function"
            for privilege in privileges:
                facts.add((kind, identity, privilege, False))
        elif columns is not None:
            for column in columns.split(", "):
                identity = f"{schema}.{obj}({column})"
                for privilege in privileges:
                    facts.add(("column", identity, privilege, False))
        elif obj is not None:
            identity = f"{schema}.{obj}"
            kind = groups["relation_kind"]
            for privilege in privileges:
                facts.add((kind, identity, privilege, False))
        else:
            for privilege in privileges:
                facts.add(("schema", schema, privilege, False))
    return tuple(sorted(facts))


def render_statements(statements: Iterable[str]) -> str:
    """Render an executable script without changing canonical statement bytes."""
    return "\n".join(statements) + "\n"
