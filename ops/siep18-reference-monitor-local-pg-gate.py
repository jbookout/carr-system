#!/usr/bin/env python3
# ci: db-gate
# doctrine: runbook
"""Rollback-only acceptance for the current SIEP-18 grant and guard binding."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from gate_runtime_role import rollback_only_connection

REPO = Path(__file__).resolve().parents[1]

EXPECTED_GRANT_COUNT = 297
EXPECTED_GRANT_DIGEST = (
    "sha256:0f04a50d8bc65e2dcc765b1981ab1d5091c809570f0a773db3f5c6e2b9d43501"
)


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
                    where registry_version='scac-mutation-registry.v10'"""
            ).fetchone()
            if registry is None or registry[4:] != (False, False, False):
                raise RuntimeError(f"v10 registry is absent or authority-expanding: {registry!r}")
            if registry[3].get("runtime_dml_grants") != {
                "count": EXPECTED_GRANT_COUNT, "digest": EXPECTED_GRANT_DIGEST,
            }:
                raise RuntimeError(f"v10 grant projection is not exact: {registry[3]!r}")

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
               epoch_chain.get("registry_version") != "scac-mutation-registry.v10" or \
               epoch_chain.get("registry_digest") != registry[0] or \
               epoch_chain.get("current_source_digest") != epoch_chain.get("live_source_digest"):
                raise RuntimeError(f"real v10 policy epoch chain is not current: {epoch_chain!r}")

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
               state.get("registry_version") != "scac-mutation-registry.v10" or \
               state.get("direct_database_grant_cutover") is not False or \
               state.get("production_enforcement_active") is not False:
                raise RuntimeError(f"reference monitor did not become exactly current: {state!r}")

            lookup = cur.execute(
                "select ops.scac_mutation_registration_v10(%s,'mcp-tool:standing-context')",
                (registry[0],),
            ).fetchone()[0]
            if lookup.get("registered") is not True or \
               lookup.get("registry_version") != "scac-mutation-registry.v10":
                raise RuntimeError(f"v10 exact registry lookup refused: {lookup!r}")
            if cur.execute(
                "select ops.scac_mutation_registry_v9_seal_available()"
            ).fetchone()[0] is not True:
                raise RuntimeError("sealed v9 predecessor is unavailable")

            # WR-000048 role-escalation guard (dispatcher ruling 2 + Joe decision
            # 23df893f, loop 569 -- SUPERSEDED 2026-09-03: the named exception for
            # carr_program5_forward_fix_verifier -> neon_superuser was REMOVED from
            # the guard as part of the WR-000048 repair cascade, so the guard now
            # reads plainly with no exceptions. The portable role-authority census
            # no longer enumerates platform/superuser roles, so the v10 catalog
            # current-check carries a compensating control: ANY carr_ role that is a
            # member of a superuser role or a neon_*/pg_* bundle fails it closed --
            # no exceptions, including carr_program5_forward_fix_verifier itself.
            # Mutation-test both trip paths here: the neon_*/pg_* bundle-name path
            # (an existing pg_* role) and the plain rolsuper path (a role this test
            # creates and marks superuser itself, since no platform role in a local
            # database is guaranteed to carry rolsuper).
            if cur.execute("select ops.scac_mutation_catalog_v10_current()").fetchone()[0] is not True:
                raise RuntimeError("v10 catalog not current before escalation mutation")
            cur.execute("savepoint escalation_mutation")
            cur.execute("create role carr_siep18_escalation_probe")
            cur.execute("grant pg_write_all_data to carr_siep18_escalation_probe")
            if cur.execute("select ops.scac_mutation_catalog_v10_current()").fetchone()[0] is not False:
                raise RuntimeError(
                    "escalation guard did not trip on a carr_ role granted pg_write_all_data"
                )
            cur.execute("rollback to savepoint escalation_mutation")
            cur.execute("release savepoint escalation_mutation")

            # Second, distinct trip path (WR-000048): a carr_ role that is a member
            # of an ACTUAL superuser role -- not a neon_*/pg_* NAMED bundle -- must
            # also fail the v10 current-check closed, with no exception for any
            # carr_ role including the one named in the now-removed carve-out.
            if cur.execute("select ops.scac_mutation_catalog_v10_current()").fetchone()[0] is not True:
                raise RuntimeError("v10 catalog not current before superuser-bundle mutation")
            cur.execute("savepoint superuser_bundle_mutation")
            cur.execute("create role carr_siep18_superuser_bundle_probe")
            cur.execute("create role siep18_gate_synthetic_superuser superuser")
            cur.execute(
                "grant siep18_gate_synthetic_superuser to carr_siep18_superuser_bundle_probe"
            )
            if cur.execute("select ops.scac_mutation_catalog_v10_current()").fetchone()[0] is not False:
                raise RuntimeError(
                    "escalation guard did not trip on a carr_ role granted an actual "
                    "superuser role (rolsuper path, distinct from the neon_/pg_ "
                    "bundle-name path above)"
                )
            cur.execute("rollback to savepoint superuser_bundle_mutation")
            cur.execute("release savepoint superuser_bundle_mutation")

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
    except Exception as exc:  # noqa: BLE001 - gate reports the exact refusal
        return fail(str(exc))
    print(
        "siep18-reference-monitor-local-pg-gate passed: exact v10 grant seal, "
        "complete guards, drift refusal, and unsupported-view refusal"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
