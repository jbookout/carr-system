#!/usr/bin/env python3
"""test-schema-snapshot-grants.py — db/schema.sql must carry the app roles'
PRIVILEGES, not just the roles.

WHY THIS EXISTS. The roles trap (test-schema-snapshot-roles.py) had a second
half. The snapshot is pg_dump --no-acl, so every GRANT from an already-applied
migration is absent from it — and ops/ci.sh's migration class builds its
throwaway database from the snapshot. The roles exist there (the preamble
creates them) but hold NOTHING: has_table_privilege() answers false for every
table and every role, and a green migration class proves nothing about grants.

Found 2026-08-14 while building the update-lead verb (PR #75): verifying that
carr_writer may insert into lead required replaying migrations 0001-0004 by
hand against an empty database, because the environment CI actually builds
could not answer the question.

The fix is a generated CARR GRANTS section in the snapshot: the app roles'
ACLs, read from production's catalogs by bin/schema-snapshot.sh and emitted as
plain GRANT statements after the structure they attach to. This test pins the
shapes that section must carry — each anchor below is a grant idiom the
migrations actually use, chosen because a naive emitter would drop it:

  * plain table grants        (0004: writer on public tables — the PR #75 case)
  * column-scoped grants      (0021 selects; 0117's update list, where the
                               ABSENCE of resolved_at is the interlock)
  * function execute grants   (0094, 0106)
  * schema usage              (0004 public, 0115 ops)
  * role membership bundles   (0006: exporter is reader-plus-a-little)

And the guard that matters as much as any anchor: the section must never widen
beyond the app roles. A grantee outside the closed set means production ACLs
for some other principal were swept into a tracked file.
"""
import os
import re
import sys

from schema_snapshot_grants import (
    SECTION_MARKER,
    SnapshotGrantError,
    carr_grants_section_lines,
    grants_to_role,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT = os.path.join(REPO, "db", "schema.sql")
GENERATOR = os.path.join(REPO, "bin", "schema-snapshot.sh")

# The app roles the migrations grant to. neondb_owner may appear as a membership
# grantee only (0005/0006 bundle it the roles), never as an ACL grantee: its own
# ACLs are Neon's business, not this repo's.
#
# carr_authority joined on 2026-08-19, with the snapshot's role preamble and for
# the same reason. 0161 creates it, and once a refresh carried the ledger past
# 0161 that migration stopped creating anything, so the snapshot has to carry
# both the role and its 24 grants. This list and the preamble in
# bin/schema-snapshot.sh are the same set and have to move together — leaving it
# out here reports the role's own grants as strays.
#
# carr_device_evidence joined the same day, by way of 0163 — the fourth role to
# age out of the snapshot. This list and the preamble in bin/schema-snapshot.sh
# are the same set written twice, so they have to move together: a role in one
# and not the other makes its own grants report as strays.
#
# carr_calendar_prebrief_jobs, carr_calendar_prebrief_attestors, and
# carr_calendar_prebrief_email_resolver are known here while 0227 remains pending. The
# generator deliberately excludes it from its active catalog query and role
# preamble so the current snapshot cannot leak a 0227 artifact.
# Move it into APP_ROLES and the generator together on the snapshot refresh
# that records 0227 as applied.
PENDING_ROLE_BUNDLES = ["carr_calendar_prebrief_jobs", "carr_calendar_prebrief_canary_jobs",
                        "carr_calendar_prebrief_attestors", "carr_calendar_prebrief_email_resolver"]
APP_ROLES = ["carr_reader", "carr_writer", "carr_jobs", "carr_exporter",
             "carr_authority", "carr_device_evidence"]
MEMBERSHIP_ONLY = ["neondb_owner"]

failures: list[str] = []
checked = 0


def check(name, cond, detail=""):
    global checked
    checked += 1
    if cond:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


def main():
    if not os.path.exists(SNAPSHOT):
        print(f"FAIL: {SNAPSHOT} not present")
        return 1
    sql = open(SNAPSHOT).read()
    lines = sql.split("\n")

    executable_lines = [line for line in lines
                        if line.strip() and not line.lstrip().startswith("--")]
    for role in PENDING_ROLE_BUNDLES:
        check(f"pending role {role} is absent from executable snapshot SQL",
              not any(role in line for line in executable_lines),
              "pending migration roles must not leak into the applied-only baseline")

    check("the snapshot carries a CARR GRANTS section",
          "CARR GRANTS" in sql)

    # Statement lines only — the section's own comment explains itself in
    # prose that mentions grants, and the roles test already learned that
    # matching inside comments fails a correct file.
    grant_lines = [(i, ln.strip()) for i, ln in enumerate(lines)
                   if re.match(r"\s*grant\s", ln, re.I)
                   and not ln.lstrip().startswith("--")]

    canonical_section = carr_grants_section_lines(sql)
    raw_writer_grants = [ln for ln in canonical_section
                         if ln.endswith(" to carr_writer;")]
    check("the shared staging provisioner extractor equals the generated writer ACLs",
          grants_to_role(sql, "carr_writer") == raw_writer_grants
          and len(raw_writer_grants) >= 170)

    destructive = sql.replace(
        SECTION_MARKER,
        SECTION_MARKER
        + "\ngrant select on table public.actor to carr_writer; "
          "drop table public.actor; -- to carr_writer;",
        1,
    )
    try:
        grants_to_role(destructive, "carr_writer")
    except SnapshotGrantError:
        destructive_refused = True
    else:
        destructive_refused = False
    check("multi-statement/comment SQL disguised as a writer GRANT is refused",
          destructive_refused)

    for role in APP_ROLES:
        check(f"at least one grant names {role}",
              any(re.search(rf"\bto {role}\b", ln) for _, ln in grant_lines))

    # The PR #75 question, answerable at last: may the writer insert into
    # lead? The emitter aggregates privileges per (table, grantee), so insert
    # may share its statement with select and update.
    check("carr_writer holds insert on public.lead (the PR #75 case)",
          any(re.match(r"grant [a-z, ]*\binsert\b[a-z, ]* on table "
                       r"public\.lead to carr_writer;", ln)
              for _, ln in grant_lines))

    # 0117's column-scoped update. The columns INSIDE the parens are the
    # grant; resolved_at staying OUTSIDE them is the interlock the migration
    # exists for. An emitter that flattens column grants to whole-table
    # grants would pass a naive "update is granted" check and silently hand
    # the collector the human-conclusion columns.
    col_update = next((ln for _, ln in grant_lines
                       if re.match(r"grant update \([^)]*\bstate\b[^)]*\) "
                                   r"on table ops\.incident to carr_jobs;", ln)),
                      None)
    check("0117's column-scoped update on ops.incident survives",
          col_update is not None)
    check("resolved_at stays OUTSIDE the update column list — the interlock",
          col_update is not None and "resolved_at" not in col_update)

    check("0021's column-scoped selects for carr_jobs survive",
          any(re.match(r"grant select \([^)]*\) on table public\.client "
                       r"to carr_jobs;", ln)
              for _, ln in grant_lines))

    check("function execute grants survive (0106's state_as_of)",
          any(re.match(r"grant execute on function public\.state_as_of\(.*\) "
                       r"to carr_reader;", ln)
              for _, ln in grant_lines))

    for schema, role in [("public", "carr_writer"), ("ops", "carr_jobs")]:
        check(f"schema usage on {schema} for {role}",
              any(re.match(rf"grant [a-z, ]*\busage\b[a-z, ]* on schema "
                           rf"{schema} to {role};", ln)
                  for _, ln in grant_lines))

    check("0006's membership bundle survives (exporter is reader-plus)",
          any(ln == "grant carr_reader to carr_exporter;"
              for _, ln in grant_lines))

    generator = open(GENERATOR).read()
    membership_query = re.search(
        r"select distinct format\('grant %s to %s;', gr\.rolname, mem\.rolname\)"
        r".*?from pg_auth_members m.*?order by 1;",
        generator,
        re.S,
    )
    check("membership renderer de-duplicates only identical rendered lines",
          membership_query is not None)

    # THE WIDENING GUARD. Every grantee in the file must be an app role —
    # or neondb_owner, on membership lines only. Anything else means some
    # other principal's production ACLs were swept into a tracked file.
    allowed = set(APP_ROLES)
    membership = re.compile(
        rf"grant ({'|'.join(APP_ROLES)}) to ({'|'.join(APP_ROLES + MEMBERSHIP_ONLY)});")
    strays = []
    for _, ln in grant_lines:
        if membership.fullmatch(ln):
            continue
        m = re.search(r"\bto ([a-z0-9_, ]+);", ln)
        grantees = [g.strip() for g in m.group(1).split(",")] if m else ["<unparsed>"]
        strays.extend(g for g in grantees if g not in allowed)
    check("no grantee outside the app-role set",
          not strays, f"strays: {sorted(set(strays))[:5]}")

    # Ordering: a grant that runs before its object exists aborts the load,
    # so the whole section must follow the structure. Compared by line
    # number over statement lines, the roles test's hard-won idiom.
    last_create = max((i for i, ln in enumerate(lines)
                       if re.match(r"\s*CREATE (TABLE|.*VIEW|SEQUENCE|FUNCTION)\b", ln)),
                      default=None)
    first_grant = grant_lines[0][0] if grant_lines else None
    check("every grant follows the structure it attaches to",
          first_grant is not None and last_create is not None
          and first_grant > last_create,
          f"first grant at line {first_grant}, last create at line {last_create}")

    print(f"\npassed {checked - len(failures)} · failed {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
