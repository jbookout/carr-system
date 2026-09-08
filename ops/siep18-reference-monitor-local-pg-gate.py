#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only acceptance for the current SIEP-18 grant and guard binding."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from gate_runtime_role import grant_settable_runtime_roles, rollback_only_connection, set_local_role

REPO = Path(__file__).resolve().parents[1]

EXPECTED_GRANT_COUNT = 307
EXPECTED_GRANT_DIGEST = (
    "sha256:5ac46a8d4226dae12c5a455be0080a472bf4e7f9dd3aa725004ec9c105be74a1"
)

# WR-000048 mutation test fixtures. NARROWED_ROLE_AUTHORITY_SCOPE is the
# portable census scope this repair cascade installs (verbatim from the
# templates: ops/scac-policy-epoch-sql.mjs and renderSIEP13RegistrySql in
# ops/scac-mutation-inventory.mjs, which this session edited). It must appear
# exactly once inside the live ops.scac_mutation_catalog_v18_current()
# definition in migrations/0492_sourced_shape_forward_correction_and_scac_successor.sql --
# if it does not, the migration no longer carries the fix this test exists to
# guard, and that is a louder failure than a silently-skipped mutation test.
# UNNARROWED_ROLE_AUTHORITY_SCOPE is the pre-fix scope, lifted verbatim (never
# retyped) from `git show 5788cec1:migrations/0455_siep12_policy_epoch.sql`,
# which the RESCOPE and handoff documents identify as the exact defect: the
# recursive term constrains only `other.rolname<>'carr_ci'`, so the walk
# crosses out of the carr_ namespace into neon_superuser and the pg_* built-ins.
NARROWED_ROLE_AUTHORITY_SCOPE = (
    "  with recursive connected(oid) as (\n"
    "    select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci' and not rolcanlogin and not rolsuper union\n"
    "    select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end where other.rolname~'^carr_' and other.rolname<>'carr_ci' and not other.rolcanlogin and not other.rolsuper\n"
    "  ), role_rows as ("
)
UNNARROWED_ROLE_AUTHORITY_SCOPE = (
    "  with recursive connected(oid) as (\n"
    "    select oid from pg_roles where rolname~'^carr_' and rolname<>'carr_ci'\n"
    "    union\n"
    "    select other.oid from connected c join pg_auth_members m on m.roleid=c.oid or m.member=c.oid\n"
    "      join pg_roles other on other.oid=case when m.roleid=c.oid then m.member else m.roleid end\n"
    "     where other.rolname<>'carr_ci'\n"
    "  ), role_rows as ("
)


def current_function_sql(source: str, version: int) -> str:
    """Extract the exact, currently-installed SCAC catalog current function
    definition from a migration file's text, start marker through its closing $fn$;."""
    start_marker = f"create or replace function ops.scac_mutation_catalog_v{version}_current()"
    end_marker = "end $fn$;"
    start = source.index(start_marker)
    end = source.index(end_marker, start) + len(end_marker)
    return source[start:end]


def function_body(source: str) -> str:
    """Return the body stored by PostgreSQL for a generated $fn$ function."""
    start_marker = "as $fn$"
    end_marker = "$fn$;"
    start = source.index(start_marker) + len(start_marker)
    end = source.rindex(end_marker)
    return source[start:end].strip()


def fail(message: str) -> int:
    print(f"siep18-reference-monitor-local-pg-gate: FAIL — {message}", file=sys.stderr)
    return 1


def uuid_for(short: str) -> str:
    return f"{short}-0000-4000-8000-000000000018"


def seed_reviewed_rule_projection(cur) -> None:
    """Install the reviewed rule map so the real deferred epoch trigger can bootstrap."""
    raw = (REPO / "ops/config/rule-enforcement-map.json").read_bytes()
    reviewed = json.loads(raw)
    map_digest = hashlib.sha256(raw).hexdigest()
    scope_by_short = {
        short: scope
        for scope, short_ids in reviewed["active_rule_ids"].items()
        for short in short_ids
    }
    joe = cur.execute(
        """insert into public.actor(slug,kind,display_name) values ('joe','human','Joe')
             on conflict(slug) do update set display_name=excluded.display_name
             returning id"""
    ).fetchone()[0]
    document_id = cur.execute(
        """insert into public.doctrine_document(slug,title,content_class,created_by)
             values ('siep18-monitor-fixture','SIEP-18 monitor fixture','reference',%s)
             returning id""",
        (joe,),
    ).fetchone()[0]
    generation = cur.execute(
        "select generation from public.doctrine_meta where id=1"
    ).fetchone()[0]
    cur.execute(
        """insert into public.doctrine_snapshot(document_id,generation,snapshot_json,content_hash)
             values (%s,%s,%s::jsonb,%s)""",
        (
            document_id,
            generation,
            json.dumps({"document": {"slug": "siep18-monitor-fixture"}, "sections": []}),
            hashlib.sha256(b"siep18-monitor-fixture").hexdigest(),
        ),
    )
    cur.execute("alter table public.rule disable trigger user")
    try:
        for short, scope in sorted(scope_by_short.items()):
            cur.execute(
                """insert into public.rule(id,statement,taught_by,status,activated_by,personal_to)
                     values (%s,%s,%s,'active',%s,%s)""",
                (
                    uuid_for(short),
                    f"SIEP-18 reviewed projection fixture {short}",
                    joe,
                    joe,
                    joe if scope == "joe" else None,
                ),
            )
    finally:
        cur.execute("alter table public.rule enable trigger user")
    for pack, contract in sorted(reviewed["rule_packs"].items()):
        cur.execute(
            """insert into ops.rule_pack(pack,title,description,triggers,source)
                 values (%s,%s,%s,%s,%s)""",
            (
                pack,
                contract["title"],
                contract["description"],
                contract["triggers"],
                "ops/config/rule-enforcement-map.json",
            ),
        )
    for short, contract in sorted(reviewed["rule_load_layers"].items()):
        cur.execute(
            """insert into ops.rule_load_layer
                 (rule_id,short_id,load_layer,packs,scope,why,source,map_digest)
                 values (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                uuid_for(short),
                short,
                contract["load_layer"],
                contract.get("packs", []),
                scope_by_short[short],
                contract.get("why"),
                "ops/config/rule-enforcement-map.json",
                map_digest,
            ),
        )
    cur.execute("set constraints all immediate")
    cur.execute("set constraints all deferred")


# ── WR-000068: sourced shape forward correction (migration 0492) ─────────────
#
# The v18 catalog this gate seals exists because 0492 added the private
# correction receipt, the effective-lineage resolver, and the rebased
# consumers. The acceptance below exercises that surface on the same
# rollback-only transaction: exact setter signature and grants, receipt
# privacy and immutability, the classifier boundary on the sourced initial
# not_required path, one durable not_required -> required correction, the
# replay and cross-table refusal matrix, the synthetic corrected -> Shape ->
# ready -> both 0333 outcome guards lifecycle, the lineage readback, a heavy
# legacy receipt's correction, and the unsourced survivors.

WR68_SETTER = "ops.set_sourced_work_request_shape_disposition(text,integer,text,text,text,uuid,uuid)"
WR68_SETTER_IDENTITY = (
    "p_work_request text, p_base_version integer, p_disposition text, p_fixed_surface_ref text, "
    "p_rationale text, p_decided_by_actor_id uuid, p_idempotency_key uuid"
)
WR68_RUNTIME_ROLES = ("carr_reader", "carr_writer", "carr_jobs", "carr_authority")
WR68_PRIVATE_FUNCTIONS = (
    "ops.sourced_work_request_shape_disposition_lineage(uuid)",
    "ops.effective_sourced_work_request_shape_disposition(ops.work_request)",
    "ops.sourced_work_shape_revision_requires_effective_required()",
)
WR68_PROJECTION = "ops.read_sourced_work_request_shape_disposition_lineage(uuid)"
WR68_RECEIPT_TABLES = (
    "ops.sourced_work_request_shape_disposition_receipt",
    "ops.sourced_work_request_shape_disposition_correction_receipt",
)
WR68_MUTABLE_KEYS = {
    "shape_disposition", "shape_fixed_surface_ref", "shape_rationale",
    "shape_decided_by_actor_id", "shape_decided_at", "version", "updated_at",
}
WR68_SETTER_SQL = "select * from ops.set_sourced_work_request_shape_disposition(%s,%s,%s,%s,%s,%s,%s)"
WR68_SHAPE_REVISION_SQL = """insert into ops.work_shape_revision
  (work_request_id, work_request_version, version, trinity, hidden_assumption, repo_searches,
   maintained_repos, archetypes, chosen_key, mind_changing_fact, builder_brief, created_by_actor_id)
  values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""


def wr68_refusal(cur, sql: str, params: tuple, fragment: str, label: str) -> None:
    cur.execute("savepoint wr68_refusal")
    try:
        cur.execute(sql, params)
    except psycopg.Error as exc:
        cur.execute("rollback to savepoint wr68_refusal")
        cur.execute("release savepoint wr68_refusal")
        if fragment.lower() not in str(exc).lower():
            raise RuntimeError(
                f"WR-000068 {label}: expected refusal containing {fragment!r}, got {exc}"
            ) from exc
        return
    cur.execute("rollback to savepoint wr68_refusal")
    cur.execute("release savepoint wr68_refusal")
    raise RuntimeError(f"WR-000068 {label} was accepted")


def wr68_one(cur, sql: str, params: tuple = ()):
    row = cur.execute(sql, params).fetchone()
    if row is None:
        raise RuntimeError(f"WR-000068 fixture row was not returned for {sql.split()[0]}")
    return row


def wr68_row_image(cur, table: str, column: str, value) -> dict:
    return wr68_one(cur, f"select to_jsonb(x) from {table} x where x.{column}=%s", (value,))[0]


def wr68_doctrine_fixture(cur, actor_id, token: str):
    source_doc = wr68_one(cur, """insert into doctrine_document (slug,title,content_class,visibility,created_by)
         values (%s,'WR68 correction source','reference','shared',%s) returning id""",
        (f"wr68-correction-{token}", actor_id))[0]
    source_section = wr68_one(cur, """insert into doctrine_section
         (document_id,section_key,title,ordinal,status,current_version)
         values (%s,'source','WR68 correction source',1,'active',1) returning id""", (source_doc,))[0]
    source_text = "A mistaken not_required receipt is corrected forward, never rewritten."
    source_rev = wr68_one(cur, """insert into doctrine_revision
         (section_id,version,actor_id,body,plain_text,content_hash,commit_message)
         values (%s,1,%s,%s,%s,%s,'WR68 source fixture') returning id""",
        (source_section, actor_id, Jsonb({"text": source_text}), source_text,
         hashlib.sha256(source_text.encode()).hexdigest()))[0]
    cur.execute("update doctrine_section set current_revision_id=%s where id=%s", (source_rev, source_section))
    runbook_doc = cur.execute(
        "select id from doctrine_document where slug='runbook' and visibility='shared'"
    ).fetchone()
    if not runbook_doc:
        runbook_doc = wr68_one(cur, """insert into doctrine_document
             (slug,title,content_class,visibility,created_by)
             values ('runbook','Runbook','reference','shared',%s) returning id""", (actor_id,))
    runbook_key = f"wr68-correction-{token}"
    runbook_section = wr68_one(cur, """insert into doctrine_section
         (document_id,section_key,title,ordinal,status,current_version)
         values (%s,%s,'WR68 correction runbook',998,'active',1) returning id""",
        (runbook_doc[0], runbook_key))[0]
    runbook_text = "Correct the sourced shape disposition forward, then shape, plan, and observe."
    runbook_rev = wr68_one(cur, """insert into doctrine_revision
         (section_id,version,actor_id,body,plain_text,content_hash,commit_message)
         values (%s,1,%s,%s,%s,%s,'WR68 runbook fixture') returning id""",
        (runbook_section, actor_id, Jsonb({"text": runbook_text}), runbook_text,
         hashlib.sha256(runbook_text.encode()).hexdigest()))[0]
    cur.execute("update doctrine_section set current_revision_id=%s where id=%s", (runbook_rev, runbook_section))
    return source_section, source_rev, f"doctrine:wr68-correction-{token}#source", f"doctrine:runbook#{runbook_key}"


def wr68_capture(cur, source_section, source_rev, origin_ref, title: str, desired: str, criteria: list):
    set_local_role(cur, "carr_writer")
    try:
        return wr68_one(cur, """select id,ref,state,version from ops.capture_sourced_work_request(
             %s,%s,%s,%s,%s,%s,%s)""",
            (origin_ref, title, desired, Jsonb(criteria), source_section, source_rev, uuid.uuid4()))
    finally:
        cur.execute("reset role")


def wr68_as_joe(cur, sql: str, params: tuple):
    cur.execute("set session authorization carr_authority_joe")
    try:
        return wr68_one(cur, sql, params)
    finally:
        cur.execute("reset session authorization")


def wr68_triage(cur, ref: str, version: int) -> int:
    triaged = wr68_as_joe(cur, "select * from ops.triage_sourced_work_request(%s,%s,'operational',%s)",
                          (ref, version, uuid.uuid4()))
    if triaged[2:4] != ("triaged", version + 1):
        raise RuntimeError(f"WR-000068 fixture did not reach triaged: {triaged!r}")
    return triaged[3]


def wr68_setter(cur, ref: str, version: int, disposition: str, fixed, rationale: str, actor_id, key):
    set_local_role(cur, "carr_writer")
    try:
        return wr68_one(cur, WR68_SETTER_SQL, (ref, version, disposition, fixed, rationale, actor_id, key))
    finally:
        cur.execute("reset role")


def wr68_setter_refusal(cur, params: tuple, fragment: str, label: str) -> None:
    set_local_role(cur, "carr_writer")
    try:
        wr68_refusal(cur, WR68_SETTER_SQL, params, fragment, label)
    finally:
        cur.execute("reset role")


def wr68_lineage(cur, work_request_id):
    set_local_role(cur, "carr_reader")
    try:
        return wr68_one(cur, "select ops.read_sourced_work_request_shape_disposition_lineage(%s)",
                        (work_request_id,))[0]
    finally:
        cur.execute("reset role")


def wr68_classify(cur, ref: str, version: int):
    set_local_role(cur, "carr_writer")
    try:
        return cur.execute(
            "select tier,reasons from ops.classify_sourced_work_request_build(%s,%s,'','[]'::jsonb,'{}'::jsonb)",
            (ref, version),
        ).fetchall()
    finally:
        cur.execute("reset role")


def wr68_shape_revision(cur, work_request_id, work_request_version: int, actor_id) -> None:
    set_local_role(cur, "carr_writer")
    try:
        cur.execute(WR68_SHAPE_REVISION_SQL, (
            work_request_id, work_request_version, 1,
            Jsonb({"workflow_trigger": "fixture", "output_user": "fixture", "runtime": "fixture"}),
            "The fixture assumes nothing hidden", Jsonb(["search one", "search two"]),
            Jsonb([{"url": f"https://github.com/example/repo-{n}", "maintenance_evidence": "active"} for n in range(5)]),
            Jsonb([{"key": k, "label": k, "core_assumption": f"assumption {k}",
                    "scores": {"trinity_fit": 3, "useful_v1_effort": 3, "extension_effort": 3}} for k in ("a", "b", "c")]),
            "a", "A measured fact would change this choice",
            Jsonb({"chosen_shape": "fixture", "repo_url": "https://github.com/example/repo-0",
                   "trinity": {"workflow_trigger": "fixture", "output_user": "fixture", "runtime": "fixture"},
                   "must_have_integrations": ["one"], "v1_non_goals": ["none"], "text": "fixture brief"}),
            actor_id))
    finally:
        cur.execute("reset role")


def wr68_sourced_shape_forward_correction_acceptance(cur) -> dict:
    token = uuid.uuid4().hex[:12]
    cur.execute("""do $$ begin
      if not exists (select 1 from pg_roles where rolname='carr_authority_joe') then
        create role carr_authority_joe login;
      end if;
    end $$""")
    cur.execute("grant carr_authority to carr_authority_joe")
    grant_settable_runtime_roles(cur, "carr_authority_joe", *WR68_RUNTIME_ROLES)
    joe_id = wr68_one(cur, "select id from actor where slug='joe' and active and kind='human'")[0]

    # A. Exactly one seven-argument setter, carr_writer execute only, no public grant.
    signature = wr68_one(cur, f"""select
        (select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace
          where n.nspname='ops' and p.proname='set_sourced_work_request_shape_disposition'),
        pg_get_function_identity_arguments('{WR68_SETTER}'::regprocedure),
        has_function_privilege('carr_writer','{WR68_SETTER}','execute'),
        has_function_privilege('carr_reader','{WR68_SETTER}','execute'),
        has_function_privilege('carr_jobs','{WR68_SETTER}','execute'),
        has_function_privilege('carr_authority','{WR68_SETTER}','execute'),
        (select coalesce(bool_or(acl.grantee=0),false) from pg_proc p
           cross join lateral aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) acl
          where p.oid='{WR68_SETTER}'::regprocedure),
        (select p.prosecdef from pg_proc p where p.oid='{WR68_SETTER}'::regprocedure)""")
    if signature != (1, WR68_SETTER_IDENTITY, True, False, False, False, False, True):
        raise RuntimeError(f"WR-000068 setter signature/grant posture drifted: {signature!r}")

    # B. Receipt tables and resolvers are private; only the narrow projection is readable.
    for table in WR68_RECEIPT_TABLES:
        for role in WR68_RUNTIME_ROLES:
            for privilege in ("select", "insert", "update", "delete"):
                if wr68_one(cur, "select has_table_privilege(%s,%s,%s)", (role, table, privilege))[0]:
                    raise RuntimeError(f"WR-000068 {role} holds {privilege} on {table}")
    for function in WR68_PRIVATE_FUNCTIONS:
        for role in WR68_RUNTIME_ROLES:
            if wr68_one(cur, "select has_function_privilege(%s,%s,'execute')", (role, function))[0]:
                raise RuntimeError(f"WR-000068 {role} may execute private {function}")
    projection_grants = tuple(
        wr68_one(cur, "select has_function_privilege(%s,%s,'execute')", (role, WR68_PROJECTION))[0]
        for role in WR68_RUNTIME_ROLES
    )
    if projection_grants != (True, True, False, False):
        raise RuntimeError(f"WR-000068 lineage projection grants are not reader/writer only: {projection_grants!r}")
    set_local_role(cur, "carr_reader")
    for table in WR68_RECEIPT_TABLES:
        wr68_refusal(cur, f"select count(*) from {table}", (), "permission denied", f"reader select on {table}")
    wr68_refusal(cur, "select * from ops.sourced_work_request_shape_disposition_lineage(%s)",
                 (uuid.uuid4(),), "permission denied", "reader lineage resolver")
    cur.execute("reset role")
    set_local_role(cur, "carr_writer")
    wr68_refusal(cur, "select * from ops.effective_sourced_work_request_shape_disposition((select x from ops.work_request x limit 1))",
                 (), "permission denied", "writer effective resolver")
    cur.execute("reset role")

    # F. Advisory key lock precedes both receipt lookups; the row lock precedes the correction insert.
    setter_source = wr68_one(cur, f"select prosrc from pg_proc where oid='{WR68_SETTER}'::regprocedure")[0]
    lock_at = setter_source.index("pg_advisory_xact_lock(hashtextextended('program6-sourced-shape-disposition:' || p_idempotency_key, 0))")
    original_lookup = setter_source.index("from ops.sourced_work_request_shape_disposition_receipt r\n   where r.idempotency_key = p_idempotency_key")
    correction_lookup = setter_source.index("from ops.sourced_work_request_shape_disposition_correction_receipt c\n   where c.idempotency_key = p_idempotency_key")
    row_lock = setter_source.index("where x.ref = p_work_request\n   for update")
    correction_insert = setter_source.index("insert into ops.sourced_work_request_shape_disposition_correction_receipt")
    if not (lock_at < original_lookup < correction_lookup < row_lock < correction_insert):
        raise RuntimeError("WR-000068 setter lock ordering drifted")
    if setter_source.count("ops.heavy_build_classification(") != 1 or \
       setter_source.index("ops.heavy_build_classification(") < correction_insert:
        raise RuntimeError("WR-000068 classifier refusal is not confined to the initial disposition branch")

    # C. Standard sourced fixture: initial not_required -> one correction -> Shape -> ready -> 0333.
    source_section, source_rev, origin_ref, runbook_ref = wr68_doctrine_fixture(cur, joe_id, token)
    request_id, ref, _state, captured_version = wr68_capture(
        cur, source_section, source_rev, origin_ref, "Sourced shape correction gate",
        "Record one receipt-backed forward shape correction",
        [{"id": "CORRECTED", "text": "The correction is receipt-backed and linked"}])
    triaged_version = wr68_triage(cur, ref, captured_version)
    if wr68_classify(cur, ref, triaged_version) != [("standard", [])]:
        raise RuntimeError("WR-000068 standard fixture did not classify standard")
    if wr68_classify(cur, ref, triaged_version + 5) != []:
        raise RuntimeError("WR-000068 wrapper returned rows for a stale version")

    key_initial, key_correction = uuid.uuid4(), uuid.uuid4()
    initial = wr68_setter(cur, ref, triaged_version, "not_required", "safe:fixed-surface",
                          "The surface is already fixed.", joe_id, key_initial)
    if initial[1:6] != (ref, "triaged", triaged_version + 1, "not_required", "safe:fixed-surface") or initial[-1] is not False:
        raise RuntimeError(f"WR-000068 initial not_required was not persisted exactly: {initial!r}")
    original_version = triaged_version + 1
    lineage = wr68_lineage(cur, request_id)
    if lineage["status"] != "original" or lineage["correction"] is not None or \
       lineage["backs_current_version"] is not True or \
       lineage["effective"]["receipt_kind"] != "original" or \
       lineage["original"]["result_version"] != original_version:
        raise RuntimeError(f"WR-000068 original lineage readback is not exact: {lineage!r}")
    original_image = wr68_row_image(cur, "ops.sourced_work_request_shape_disposition_receipt", "work_request_id", request_id)
    before_correction = wr68_row_image(cur, "ops.work_request", "id", request_id)

    replay = wr68_setter(cur, ref, triaged_version, "not_required", "safe:fixed-surface",
                         "The surface is already fixed.", joe_id, key_initial)
    if replay[:-1] != initial[:-1] or replay[-1] is not True:
        raise RuntimeError("WR-000068 exact original replay did not return the persisted receipt")
    wr68_setter_refusal(cur, (ref, triaged_version, "not_required", "safe:fixed-surface", "changed", joe_id, key_initial),
                        "different sourced shape disposition", "changed original payload")
    wr68_setter_refusal(cur, (ref, original_version, "required", None, "Cross-table reuse", joe_id, key_initial),
                        "different sourced shape disposition", "correction call reusing the original key")
    wr68_setter_refusal(cur, (ref, triaged_version, "required", None, "Stale base version", joe_id, uuid.uuid4()),
                        "exact current triaged sourced Work Request required", "stale correction")
    wr68_setter_refusal(cur, (ref, original_version, "not_required", "safe:other", "Second not_required", joe_id, uuid.uuid4()),
                        "one required correction before Shape", "second not_required disposition")

    corrected = wr68_setter(cur, ref, original_version, "required", None,
                            "The request is intrinsically heavy and needs a Work Shape.", joe_id, key_correction)
    corrected_version = original_version + 1
    if corrected[1:6] != (ref, "triaged", corrected_version, "required", None) or corrected[-1] is not False:
        raise RuntimeError(f"WR-000068 correction was not persisted exactly: {corrected!r}")
    if wr68_row_image(cur, "ops.sourced_work_request_shape_disposition_receipt", "work_request_id", request_id) != original_image:
        raise RuntimeError("WR-000068 original receipt changed during correction")
    corrections = cur.execute(
        """select original_receipt_id,base_version,result_version,disposition,fixed_surface_ref,decided_by_actor_id
             from ops.sourced_work_request_shape_disposition_correction_receipt where work_request_id=%s""",
        (request_id,)).fetchall()
    if corrections != [(uuid.UUID(original_image["id"]), original_version, corrected_version, "required", None, joe_id)]:
        raise RuntimeError(f"WR-000068 correction receipt is not exactly one linked row: {corrections!r}")
    after_correction = wr68_row_image(cur, "ops.work_request", "id", request_id)
    changed = {key for key in after_correction if after_correction[key] != before_correction.get(key)}
    if not changed <= WR68_MUTABLE_KEYS or after_correction["version"] != corrected_version:
        raise RuntimeError(f"WR-000068 correction mutated more than shape/version/time: {sorted(changed)!r}")
    lineage = wr68_lineage(cur, request_id)
    if lineage["status"] != "corrected" or lineage["backs_current_version"] is not True or \
       lineage["effective"]["receipt_kind"] != "correction" or \
       lineage["effective"]["result_version"] != corrected_version or \
       lineage["correction"]["original_receipt_id"] != lineage["original"]["receipt_id"] or \
       lineage["original"]["disposition"] != "not_required" or lineage["correction"]["disposition"] != "required":
        raise RuntimeError(f"WR-000068 corrected lineage readback is not exact: {lineage!r}")

    replay = wr68_setter(cur, ref, original_version, "required", None,
                         "The request is intrinsically heavy and needs a Work Shape.", joe_id, key_correction)
    if replay[:-1] != corrected[:-1] or replay[-1] is not True:
        raise RuntimeError("WR-000068 exact correction replay did not return the persisted correction")
    wr68_setter_refusal(cur, (ref, original_version, "required", None, "changed", joe_id, key_correction),
                        "different sourced shape correction", "changed correction payload")
    wr68_setter_refusal(cur, (ref, original_version, "not_required", "safe:fixed-surface", "The surface is already fixed.", joe_id, key_correction),
                        "different sourced shape correction", "original-shaped call reusing the correction key")
    wr68_setter_refusal(cur, (ref, triaged_version, "not_required", "safe:fixed-surface", "The surface is already fixed.", joe_id, key_initial),
                        "different sourced shape disposition", "original replay after its correction")
    wr68_setter_refusal(cur, (ref, corrected_version, "required", None, "A second correction", joe_id, uuid.uuid4()),
                        "one required correction before Shape", "second correction")
    wr68_refusal(cur, "update ops.sourced_work_request_shape_disposition_correction_receipt set rationale='tampered' where work_request_id=%s",
                 (request_id,), "append-only", "correction receipt update")
    wr68_refusal(cur, "delete from ops.sourced_work_request_shape_disposition_correction_receipt where work_request_id=%s",
                 (request_id,), "append-only", "correction receipt delete")
    wr68_refusal(cur, "update ops.sourced_work_request_shape_disposition_receipt set rationale='tampered' where work_request_id=%s",
                 (request_id,), "append-only", "original receipt update")
    wr68_refusal(cur, """insert into ops.sourced_work_request_shape_disposition_correction_receipt
                   (work_request_id,idempotency_key,original_receipt_id,base_version,result_version,disposition,rationale,decided_by_actor_id)
                   values (%s,%s,%s,%s,%s,'required','forged second correction',%s)""",
                 (request_id, uuid.uuid4(), original_image["id"], corrected_version, corrected_version + 1, joe_id),
                 "duplicate key", "owner-forged second correction")
    set_local_role(cur, "carr_writer")
    wr68_refusal(cur, """update ops.work_request set shape_disposition='required',shape_fixed_surface_ref=null,shape_rationale='forged',
                     shape_decided_by_actor_id=%s,shape_decided_at=now(),version=version+1,updated_at=now() where id=%s""",
                 (joe_id, request_id), "receipt-backed", "direct sourced shape mutation without receipt")
    wr68_refusal(cur, WR68_SHAPE_REVISION_SQL.replace("values (%s,%s,%s", "values (%s,%s,%s") , (
        request_id, original_version, 1, Jsonb({}), "stale", Jsonb([]), Jsonb([]), Jsonb([]), "a", "x", Jsonb({}), joe_id),
        "exact current receipt-backed required", "stale-version Shape revision")
    cur.execute("reset role")
    wr68_shape_revision(cur, request_id, corrected_version, joe_id)
    replay = wr68_setter(cur, ref, original_version, "required", None,
                         "The request is intrinsically heavy and needs a Work Shape.", joe_id, key_correction)
    if replay[-1] is not True:
        raise RuntimeError("WR-000068 correction replay after Shape was not stable")
    wr68_setter_refusal(cur, (ref, corrected_version, "required", None, "Post-Shape correction", joe_id, uuid.uuid4()),
                        "one required correction before Shape", "correction after Shape")

    set_local_role(cur, "carr_writer")
    plan = wr68_one(cur, """select * from ops.propose_sourced_work_request_plan(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (ref, corrected_version, "Shape, plan, and observe the corrected request", runbook_ref, Jsonb([]),
                     "safe:recovery:stop", "safe:observability:record",
                     Jsonb({"max_steps": 2, "max_duration_minutes": 15}), uuid.uuid4()))
    cur.execute("reset role")
    if plan[5:7] != ("triaged", corrected_version):
        raise RuntimeError(f"WR-000068 proposal over the corrected request failed: {plan!r}")
    ready = wr68_as_joe(cur, "select * from ops.accept_sourced_work_request_plan(%s,%s,%s,%s)",
                        (ref, corrected_version, plan[2], uuid.uuid4()))
    ready_version = corrected_version + 1
    if ready[2:4] != ("ready", ready_version) or ready[9] != "required" or ready[10] is not None:
        raise RuntimeError(f"WR-000068 acceptance did not preserve the corrected required shape: {ready!r}")
    binding = wr68_one(cur, """select sb.disposition,sb.fixed_surface_ref,sb.rationale,sb.decided_by_actor_id,sb.decided_at
          from ops.sourced_work_request_plan_shape_binding_receipt sb
          join ops.sourced_work_request_plan_acceptance_receipt ar on ar.id=sb.plan_acceptance_receipt_id
         where ar.work_request_id=%s""", (request_id,))
    correction_row = wr68_one(cur, """select disposition,fixed_surface_ref,rationale,decided_by_actor_id,decided_at
          from ops.sourced_work_request_shape_disposition_correction_receipt where work_request_id=%s""", (request_id,))
    if binding != correction_row:
        raise RuntimeError(f"WR-000068 shape binding does not equal the correction: {binding!r} vs {correction_row!r}")
    wr68_setter_refusal(cur, (ref, original_version, "required", None, "The request is intrinsically heavy and needs a Work Shape.", joe_id, key_correction),
                        "different sourced shape correction", "correction replay after ready")
    set_local_role(cur, "carr_writer")
    feedback = wr68_one(cur, """select * from ops.propose_sourced_work_request_outcome_feedback(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (ref, ready_version, plan[2], Jsonb([{"id": "CORRECTED", "result": "met"}]),
                         Jsonb(["safe:evidence:wr68-lifecycle"]), "none", "Observed the corrected lifecycle",
                         12, "mcp", False, 0, uuid.uuid4()))
    cur.execute("reset role")
    if feedback[5:7] != ("ready", ready_version):
        raise RuntimeError(f"WR-000068 outcome proposal over the corrected lineage failed: {feedback!r}")
    accepted = wr68_as_joe(cur, "select * from ops.accept_sourced_work_request_outcome_feedback(%s,%s,%s,%s)",
                           (ref, ready_version, feedback[2], uuid.uuid4()))
    if accepted[2:4] != ("ready", ready_version):
        raise RuntimeError(f"WR-000068 outcome acceptance over the corrected lineage failed: {accepted!r}")
    lineage = wr68_lineage(cur, request_id)
    if lineage["status"] != "corrected" or lineage["backs_current_version"] is not False or \
       lineage["effective"]["result_version"] != corrected_version:
        raise RuntimeError(f"WR-000068 ready-state lineage readback is not exact: {lineage!r}")
    cur.execute("savepoint wr68_binding_drift")
    cur.execute("alter table ops.sourced_work_request_plan_shape_binding_receipt disable trigger sourced_work_request_plan_shape_binding_immutable")
    cur.execute("update ops.sourced_work_request_plan_shape_binding_receipt set rationale='drifted' where work_request_id=%s", (request_id,))
    set_local_role(cur, "carr_writer")
    wr68_refusal(cur, """select * from ops.propose_sourced_work_request_outcome_feedback(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                 (ref, ready_version, plan[2], Jsonb([{"id": "CORRECTED", "result": "met"}]),
                  Jsonb(["safe:evidence:wr68-drift"]), "none", "Binding drifted from lineage", 12, "mcp", False, 0, uuid.uuid4()),
                 "only an exact current ready sourced plan", "outcome proposal over a binding that left the lineage")
    cur.execute("reset role")
    cur.execute("rollback to savepoint wr68_binding_drift")
    cur.execute("release savepoint wr68_binding_drift")

    # D. Heavy fixtures: initial not_required refused; a legacy heavy not_required receipt corrects.
    heavy_id, heavy_ref, _s, heavy_captured = wr68_capture(
        cur, source_section, source_rev, origin_ref, "Build a new governed heavy-build execution system",
        "Implement the complete end-to-end platform", [{"id": "HEAVY", "text": "The heavy request is shaped"}])
    heavy_version = wr68_triage(cur, heavy_ref, heavy_captured)
    heavy_classification = wr68_classify(cur, heavy_ref, heavy_version)
    if len(heavy_classification) != 1 or heavy_classification[0][0] != "heavy":
        raise RuntimeError(f"WR-000068 heavy fixture did not classify heavy: {heavy_classification!r}")
    heavy_before = wr68_row_image(cur, "ops.work_request", "id", heavy_id)
    wr68_setter_refusal(cur, (heavy_ref, heavy_version, "not_required", "safe:fixed-surface", "Heavy not_required", joe_id, uuid.uuid4()),
                        "intrinsically heavy", "heavy initial not_required")
    if wr68_row_image(cur, "ops.work_request", "id", heavy_id) != heavy_before or cur.execute(
            "select count(*) from ops.sourced_work_request_shape_disposition_receipt where work_request_id=%s", (heavy_id,)).fetchone()[0]:
        raise RuntimeError("WR-000068 heavy refusal mutated state")
    heavy_required = wr68_setter(cur, heavy_ref, heavy_version, "required", None, "Heavy work needs a Shape.", joe_id, uuid.uuid4())
    if heavy_required[4] != "required":
        raise RuntimeError(f"WR-000068 heavy initial required was refused: {heavy_required!r}")

    legacy_id, legacy_ref, _s, legacy_captured = wr68_capture(
        cur, source_section, source_rev, origin_ref, "Build a new governed heavy-build execution system",
        "Implement the complete end-to-end platform again", [{"id": "LEGACY", "text": "The legacy receipt is corrected"}])
    legacy_version = wr68_triage(cur, legacy_ref, legacy_captured)
    legacy_receipt = wr68_one(cur, """insert into ops.sourced_work_request_shape_disposition_receipt
        (work_request_id,idempotency_key,base_version,result_version,disposition,fixed_surface_ref,rationale,decided_by_actor_id)
        values (%s,%s,%s,%s,'not_required','safe:legacy-surface','Mistaken legacy receipt',%s) returning *""",
        (legacy_id, uuid.uuid4(), legacy_version, legacy_version + 1, joe_id))
    cur.execute("""update ops.work_request set shape_disposition='not_required',shape_fixed_surface_ref='safe:legacy-surface',
                     shape_rationale='Mistaken legacy receipt',shape_decided_by_actor_id=%s,shape_decided_at=%s,
                     version=%s,updated_at=now() where id=%s""",
                (joe_id, legacy_receipt[-1], legacy_version + 1, legacy_id))
    legacy_corrected = wr68_setter(cur, legacy_ref, legacy_version + 1, "required", None,
                                   "WR-000063 shape correction", joe_id, uuid.uuid4())
    if legacy_corrected[3:6] != (legacy_version + 2, "required", None):
        raise RuntimeError(f"WR-000068 heavy legacy correction failed: {legacy_corrected!r}")
    if wr68_lineage(cur, legacy_id)["status"] != "corrected":
        raise RuntimeError("WR-000068 heavy legacy lineage did not read corrected")
    wr68_shape_revision(cur, legacy_id, legacy_version + 2, joe_id)
    set_local_role(cur, "carr_writer")
    legacy_plan = wr68_one(cur, """select * from ops.propose_sourced_work_request_plan(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                           (legacy_ref, legacy_version + 2, "Plan the corrected heavy request", runbook_ref, Jsonb([]),
                            "safe:recovery:stop", "safe:observability:record",
                            Jsonb({"max_steps": 2, "max_duration_minutes": 15}), uuid.uuid4()))
    cur.execute("reset role")
    if legacy_plan[5:7] != ("triaged", legacy_version + 2):
        raise RuntimeError(f"WR-000068 proposal over the corrected heavy request failed: {legacy_plan!r}")

    # E. Unsourced survivors: direct update, no receipts, null lineage, Shape untouched, setter refused.
    unsourced_ref = f"WR-98{int(token[:6], 16) % 1000000:06d}"
    unsourced_id = wr68_one(cur, """insert into ops.work_request (ref,state,title,requester_actor,owner_actor)
         values (%s,'triaged','WR68 unsourced survivor','joe','joe') returning id""",
        (unsourced_ref,))[0]
    set_local_role(cur, "carr_writer")
    unsourced = wr68_one(cur, """update ops.work_request set shape_disposition='not_required',shape_fixed_surface_ref='fixture:surface',
        shape_rationale='fixed surface',shape_decided_by_actor_id=%s,shape_decided_at=now(),updated_at=now(),version=version+1
        where id=%s returning version,shape_disposition""", (joe_id, unsourced_id))
    cur.execute("reset role")
    if unsourced != (2, "not_required"):
        raise RuntimeError(f"WR-000068 unsourced direct disposition regressed: {unsourced!r}")
    set_local_role(cur, "carr_writer")
    cur.execute("""update ops.work_request set shape_disposition='required',shape_fixed_surface_ref=null,
        shape_rationale='open surface',updated_at=now(),version=version+1 where id=%s""", (unsourced_id,))
    cur.execute("reset role")
    wr68_shape_revision(cur, unsourced_id, 3, joe_id)
    receipts = wr68_one(cur, """select (select count(*) from ops.sourced_work_request_shape_disposition_receipt where work_request_id=%s),
        (select count(*) from ops.sourced_work_request_shape_disposition_correction_receipt where work_request_id=%s),
        (select count(*) from ops.effective_sourced_work_request_shape_disposition((select x from ops.work_request x where x.id=%s)))""",
        (unsourced_id, unsourced_id, unsourced_id))
    if receipts != (0, 0, 0) or wr68_lineage(cur, unsourced_id) is not None:
        raise RuntimeError(f"WR-000068 unsourced request touched the sourced lineage: {receipts!r}")
    wr68_setter_refusal(cur, (unsourced_ref, 3, "required", None, "Unsourced admission", joe_id, uuid.uuid4()),
                        "exact current triaged sourced Work Request required", "unsourced admission to the sourced setter")

    return {
        "contract": "wr68-sourced-shape-forward-correction-local-pg.v1",
        "setter_pg_proc_rows": 1,
        "setter_execute": "carr_writer",
        "receipt_tables_private": True,
        "lineage_projection": "carr_reader,carr_writer",
        "standard_lifecycle": "not_required -> correction -> Shape -> ready -> outcome proposed/accepted",
        "heavy_initial_not_required": "refused",
        "heavy_legacy_correction": "corrected -> Shape -> proposed",
        "unsourced_survivor": True,
    }


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("CARR_LOCAL_PG_DSN", "")
    if not dsn:
        return fail("DATABASE_URL or CARR_LOCAL_PG_DSN is required")
    try:
        with rollback_only_connection(dsn) as conn, conn.cursor() as cur:
            registry = cur.execute(
                """select registry_digest,entry_count,source_entry_count,catalog_projection,
                          atomic_database_mediation_operational,direct_database_grant_cutover,
                          production_enforcement_active
                     from ops.scac_mutation_registry_version
                    where registry_version='scac-mutation-registry.v18'"""
            ).fetchone()
            if registry is None or registry[4:] != (False, False, False):
                raise RuntimeError(f"v18 registry is absent or authority-expanding: {registry!r}")
            if registry[3].get("runtime_dml_grants") != {
                "count": EXPECTED_GRANT_COUNT, "digest": EXPECTED_GRANT_DIGEST,
            }:
                raise RuntimeError(f"v18 grant projection is not exact: {registry[3]!r}")

            # A fresh reconstructed database intentionally has no policy epoch
            # until nonempty reviewed doctrine/rule data arrives. Seed that
            # reviewed projection and require the real deferred refresh trigger
            # to build a cryptographically valid current chain; never fabricate an
            # epoch pointer merely to make the monitor look current.
            if cur.execute("select count(*) from ops.scac_policy_epoch").fetchone()[0] != 0:
                raise RuntimeError("empty reconstructed policy was unexpectedly blessed")
            seed_reviewed_rule_projection(cur)
            epoch_chain = cur.execute(
                "select ops.scac_policy_epoch_chain_state()"
            ).fetchone()[0]
            if epoch_chain.get("valid") is not True or \
               epoch_chain.get("reason") != "valid" or \
               epoch_chain.get("registry_version") != "scac-mutation-registry.v18" or \
               epoch_chain.get("registry_digest") != registry[0] or \
               epoch_chain.get("current_source_digest") != epoch_chain.get("live_source_digest"):
                raise RuntimeError(f"real v18 policy epoch chain is not current: {epoch_chain!r}")

            # The bulk fixture seed suppresses public.rule lifecycle triggers so
            # it does not pretend 218 synthetic rows passed the real admission
            # workflow. Prove the epoch observer itself separately, with triggers
            # enabled: mutate one unreceipted fixture rule, force the deferred
            # constraint trigger, and require one new current epoch sourced from
            # public.rule. This prevents rule_pack/rule_load_layer observers from
            # masking a broken scac_epoch_rule trigger.
            epoch_before = cur.execute(
                "select epoch,epoch_digest from ops.scac_policy_epoch order by epoch desc limit 1"
            ).fetchone()
            probe_rule = cur.execute(
                """select id from public.rule
                    where statement like 'SIEP-18 reviewed projection fixture %'
                    order by id limit 1"""
            ).fetchone()
            if epoch_before is None or probe_rule is None:
                raise RuntimeError("trigger-enabled rule epoch probe has no seeded preimage")
            cur.execute(
                "update public.rule set statement=statement||' [epoch trigger probe]' where id=%s",
                (probe_rule[0],),
            )
            cur.execute("set constraints all immediate")
            epoch_after = cur.execute(
                """select epoch,previous_epoch,previous_epoch_digest,source_relation
                     from ops.scac_policy_epoch order by epoch desc limit 1"""
            ).fetchone()
            if epoch_after != (
                epoch_before[0] + 1, epoch_before[0], epoch_before[1], "public.rule"
            ):
                raise RuntimeError(
                    "trigger-enabled public.rule mutation did not append exactly one "
                    f"linked epoch: before={epoch_before!r}, after={epoch_after!r}"
                )
            epoch_chain = cur.execute(
                "select ops.scac_policy_epoch_chain_state()"
            ).fetchone()[0]
            if epoch_chain.get("valid") is not True or \
               epoch_chain.get("reason") != "valid" or \
               epoch_chain.get("registry_version") != "scac-mutation-registry.v18" or \
               epoch_chain.get("registry_digest") != registry[0] or \
               epoch_chain.get("current_source_digest") != epoch_chain.get("live_source_digest"):
                raise RuntimeError(
                    f"trigger-enabled public.rule epoch is not current: {epoch_chain!r}"
                )
            cur.execute("set constraints all deferred")

            grant_snapshot = cur.execute(
                "select ops.scac_runtime_dml_grant_snapshot()"
            ).fetchone()[0]
            if grant_snapshot != {
                "schema_version": "scac-runtime-dml-grants.v1",
                "entry_count": EXPECTED_GRANT_COUNT,
                "grant_digest": EXPECTED_GRANT_DIGEST,
            }:
                raise RuntimeError(f"runtime DML grant snapshot drifted: {grant_snapshot!r}")

            state = cur.execute("select ops.scac_reference_monitor_state()").fetchone()[0]
            if state.get("monitor_state") != "current" or \
               state.get("grant_state") != "current" or \
               state.get("guard_state") != "complete" or \
               state.get("missing_guard_count") != 0 or \
               state.get("unsupported_writable_relation_count") != 0 or \
               state.get("policy_epoch_state") != "current" or \
               state.get("registry_version") != "scac-mutation-registry.v18" or \
               state.get("direct_database_grant_cutover") is not False or \
               state.get("production_enforcement_active") is not False:
                raise RuntimeError(f"reference monitor did not become exactly current: {state!r}")

            lookup = cur.execute(
                "select ops.scac_mutation_registration_v18(%s,'mcp-tool:standing-context')",
                (registry[0],),
            ).fetchone()[0]
            if lookup.get("registered") is not True or \
               lookup.get("registry_version") != "scac-mutation-registry.v18":
                raise RuntimeError(f"v18 exact registry lookup refused: {lookup!r}")
            if cur.execute(
                "select ops.scac_mutation_registry_v17_seal_available()"
            ).fetchone()[0] is not True:
                raise RuntimeError("sealed v17 predecessor is unavailable")

            # WR-000048 role-escalation guard (dispatcher ruling 2 + Joe decision
            # 23df893f, loop 569 -- SUPERSEDED 2026-09-03: the named exception for
            # carr_program5_forward_fix_verifier -> neon_superuser was REMOVED from
            # the guard as part of the WR-000048 repair cascade, so the guard now
            # reads plainly with no exceptions. The portable role-authority census
            # no longer enumerates platform/superuser roles, so the v18 catalog
            # current-check carries a compensating control: ANY carr_ role that is a
            # member of a superuser role or a neon_*/pg_* bundle fails it closed --
            # no exceptions, including carr_program5_forward_fix_verifier itself.
            # Mutation-test both trip paths here: the neon_*/pg_* bundle-name path
            # (an existing pg_* role) and the plain rolsuper path (a role this test
            # creates and marks superuser itself, since no platform role in a local
            # database is guaranteed to carry rolsuper).
            if cur.execute("select ops.scac_mutation_catalog_v18_current()").fetchone()[0] is not True:
                raise RuntimeError("v18 catalog not current before escalation mutation")
            cur.execute("savepoint escalation_mutation")
            cur.execute("create role carr_siep18_escalation_probe")
            cur.execute("grant pg_write_all_data to carr_siep18_escalation_probe")
            if cur.execute("select ops.scac_mutation_catalog_v18_current()").fetchone()[0] is not False:
                raise RuntimeError(
                    "escalation guard did not trip on a carr_ role granted pg_write_all_data"
                )
            cur.execute("rollback to savepoint escalation_mutation")
            cur.execute("release savepoint escalation_mutation")

            # Second, distinct trip path (WR-000048): a carr_ role that is a member
            # of an ACTUAL superuser role -- not a neon_*/pg_* NAMED bundle -- must
            # also fail the v18 current-check closed, with no exception for any
            # carr_ role including the one named in the now-removed carve-out.
            if cur.execute("select ops.scac_mutation_catalog_v18_current()").fetchone()[0] is not True:
                raise RuntimeError("v18 catalog not current before superuser-bundle mutation")
            cur.execute("savepoint superuser_bundle_mutation")
            cur.execute("create role carr_siep18_superuser_bundle_probe")
            cur.execute("create role siep18_gate_synthetic_superuser superuser")
            cur.execute(
                "grant siep18_gate_synthetic_superuser to carr_siep18_superuser_bundle_probe"
            )
            if cur.execute("select ops.scac_mutation_catalog_v18_current()").fetchone()[0] is not False:
                raise RuntimeError(
                    "escalation guard did not trip on a carr_ role granted an actual "
                    "superuser role (rolsuper path, distinct from the neon_/pg_ "
                    "bundle-name path above)"
                )
            cur.execute("rollback to savepoint superuser_bundle_mutation")
            cur.execute("release savepoint superuser_bundle_mutation")

            # WR-000048 census-scope mutation test. ops.scac_policy_epoch_refresh()
            # (migrations/0455) takes a bootstrap escape hatch and returns null
            # without ever reaching the catalog guard when there is no prior epoch
            # AND the rule-delivery projection is empty -- true of every CI fixture
            # that never seeds a rule, which is exactly how this defect (mechanism
            # pinned in RECEIPT-A1) went unnoticed through 14/14 unit tests and a
            # 52-program acceptance suite. seed_reviewed_rule_projection() above
            # seeds a genuinely non-empty rule/doctrine projection specifically so
            # the real (non-bootstrap) path runs; the epoch_chain assertion above
            # already required the real deferred trigger to succeed once. Assert
            # that explicitly here, then prove the assertion is not vacuous: widen
            # the live current-check function's role-authority scope back to the
            # pre-fix (unnarrowed) CTE inside a savepoint, and require the same
            # real (non-bootstrap) snapshot path to now raise.
            snapshot = cur.execute("select ops.scac_policy_epoch_snapshot()").fetchone()[0]
            if not isinstance(snapshot, dict) or snapshot.get("registry_version") != "scac-mutation-registry.v18":
                raise RuntimeError(
                    f"ops.scac_policy_epoch_snapshot() did not succeed on the real, "
                    f"non-bootstrap path with the repaired templates: {snapshot!r}"
                )
            v18_migration_path = REPO / "migrations/0492_sourced_shape_forward_correction_and_scac_successor.sql"
            v18_migration_sql = v18_migration_path.read_text(encoding="utf-8")
            committed_v18_current = current_function_sql(v18_migration_sql, 18)
            committed_v18_body = function_body(committed_v18_current)

            # Prove the canonical migration class installed the committed
            # catalog functions before this gate mutates anything. Historical
            # v2-v10 `*_current` functions are deliberately retained as seal/live
            # wrappers, while the full catalog validators that remain installed
            # under `*_live_at_seal` plus v18 must all carry the narrowed census.
            installed_catalog_functions = cur.execute(
                """select p.proname, pg_get_functiondef(p.oid), p.prosrc
                     from pg_proc p
                     join pg_namespace n on n.oid=p.pronamespace
                    where n.nspname='ops'
                      and p.proname~'^scac_mutation_catalog_v([2-9]|10|11|12|13|14|15|16|17|18)_(current|live_at_seal)$'
                    order by p.proname"""
            ).fetchall()
            installed_names = {row[0] for row in installed_catalog_functions}
            expected_current_names = {
                f"scac_mutation_catalog_v{version}_current" for version in range(2, 19)
            }
            if not expected_current_names.issubset(installed_names):
                raise RuntimeError(
                    "canonical migration chain did not install every v2-v18 current "
                    f"function: missing {sorted(expected_current_names - installed_names)!r}"
                )
            installed_validators = [
                row for row in installed_catalog_functions if "role_rows as (" in row[2]
            ]
            if len(installed_validators) != 15:
                raise RuntimeError(
                    "canonical migration chain did not retain the expected fifteen "
                    f"role-authority validators (v4-v18): {[row[0] for row in installed_validators]!r}"
                )
            for function_name, definition, _body in installed_validators:
                if NARROWED_ROLE_AUTHORITY_SCOPE not in definition or \
                   UNNARROWED_ROLE_AUTHORITY_SCOPE in definition:
                    raise RuntimeError(
                        f"installed {function_name} does not carry only the narrowed "
                        "role-authority scope"
                    )

            installed_v18 = next(
                row for row in installed_catalog_functions
                if row[0] == "scac_mutation_catalog_v18_current"
            )
            installed_v18_definition = installed_v18[1]
            installed_v18_body = installed_v18[2].strip()
            if installed_v18_body != committed_v18_body:
                raise RuntimeError(
                    "installed ops.scac_mutation_catalog_v18_current() body does not "
                    "match the committed 0492 migration definition"
                )
            if NARROWED_ROLE_AUTHORITY_SCOPE not in installed_v18_definition:
                raise RuntimeError(
                    "the installed ops.scac_mutation_catalog_v18_current() definition no "
                    "longer contains the expected narrowed role-authority scope -- "
                    "the mutation test below would be vacuous; the fix or the "
                    "generator moved without this test being updated"
                )
            widened_v18_current = installed_v18_definition.replace(
                NARROWED_ROLE_AUTHORITY_SCOPE, UNNARROWED_ROLE_AUTHORITY_SCOPE
            )
            if widened_v18_current == installed_v18_definition:
                raise RuntimeError("widening the role-authority scope for the mutation test was a no-op")
            cur.execute("savepoint census_scope_mutation")
            cur.execute(widened_v18_current)
            active_widened_definition = cur.execute(
                "select pg_get_functiondef('ops.scac_mutation_catalog_v18_current()'::regprocedure)"
            ).fetchone()[0]
            if UNNARROWED_ROLE_AUTHORITY_SCOPE not in active_widened_definition or \
               NARROWED_ROLE_AUTHORITY_SCOPE in active_widened_definition:
                raise RuntimeError(
                    "the deliberate widened mutation was not installed in the live "
                    "v18 current-check function"
                )
            try:
                cur.execute("select ops.scac_policy_epoch_snapshot()")
            except Exception as exc:  # noqa: BLE001 - the raise IS the assertion
                if "drifted" not in str(exc).lower() and "corrupt" not in str(exc).lower():
                    raise RuntimeError(
                        f"widened role-authority scope raised the wrong error: {exc}"
                    ) from exc
            else:
                raise RuntimeError(
                    "ops.scac_policy_epoch_snapshot() did not raise with the "
                    "pre-fix (unnarrowed) role-authority scope reinstated -- the "
                    "narrowing fix is not what makes this pass"
                )
            cur.execute("rollback to savepoint census_scope_mutation")
            cur.execute("release savepoint census_scope_mutation")
            restored_v18_definition = cur.execute(
                "select pg_get_functiondef('ops.scac_mutation_catalog_v18_current()'::regprocedure)"
            ).fetchone()[0]
            if restored_v18_definition != installed_v18_definition:
                raise RuntimeError(
                    "rollback did not restore the committed v18 current-check definition"
                )
            if cur.execute("select ops.scac_mutation_catalog_v18_current()").fetchone()[0] is not True:
                raise RuntimeError("v18 catalog current-check did not re-arm after the census-scope rollback")

            cur.execute("savepoint grant_drift")
            cur.execute("grant insert on public.lead to carr_reader")
            drifted = cur.execute("select ops.scac_reference_monitor_state()").fetchone()[0]
            if drifted.get("monitor_state") != "unavailable" or \
               drifted.get("grant_state") != "drifted_or_unbound":
                raise RuntimeError(f"unexpected DML grant did not fail closed: {drifted!r}")
            cur.execute("rollback to savepoint grant_drift")
            cur.execute("release savepoint grant_drift")

            cur.execute("savepoint unsupported_view")
            cur.execute("create view ops.siep18_gate_writable_view as select id from public.lead")
            cur.execute("grant update on ops.siep18_gate_writable_view to carr_writer")
            unsupported = cur.execute(
                "select ops.scac_reference_monitor_state()"
            ).fetchone()[0]
            if unsupported.get("monitor_state") != "unavailable" or \
               unsupported.get("guard_state") != "unsupported_writable_relation" or \
               unsupported.get("unsupported_writable_relation_count") != 1:
                raise RuntimeError(
                    f"unsupported writable view did not fail closed: {unsupported!r}"
                )
            cur.execute("rollback to savepoint unsupported_view")
            cur.execute("release savepoint unsupported_view")

            wr68 = wr68_sourced_shape_forward_correction_acceptance(cur)
    except Exception as exc:  # noqa: BLE001 - gate reports the exact refusal
        return fail(str(exc))
    print(
        "siep18-reference-monitor-local-pg-gate passed: exact v18 grant seal, "
        "complete guards, drift refusal, unsupported-view refusal, and "
        f"WR-000068 sourced shape forward correction {json.dumps(wr68, sort_keys=True)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
